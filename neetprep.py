from flask import Blueprint, render_template, request, jsonify, current_app, url_for
from utils import get_db_connection
import requests
import time
import os
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
import math

from gemini_classifier import classify_questions_with_gemini

neetprep_bp = Blueprint('neetprep_bp', __name__)

# ... (Constants and GraphQL queries remain the same) ...
ENDPOINT_URL = "https://www.neetprep.com/graphql"
USER_ID = "VXNlcjozNTY5Mzcw="

HEADERS = {
    'accept': '*/*',
    'content-type': 'application/json',
    'origin': 'https://www.neetprep.com',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
}

# --- Queries ---
query_template_step1 = 'query GetAttempts {{ testAttempts( limit: {limit}, offset: {offset}, where: {{ userId: "{userId}" }} ) {{ id completed }} }}'
query_template_step2 = 'query GetIncorrectIds {{ incorrectQuestions( testAttemptId: "{attemptId}", first: 200 ) {{ id }} }}'
query_template_step3 = '''
query GetQuestionDetails {{
  question(id: "{questionId}") {{
    id
    question
    options
    correctOptionIndex
    level
    topics(first: 1) {{
      edges {{
        node {{
          name
          subjects(first: 1) {{
            edges {{
              node {{ name }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
'''

def fetch_question_details(q_id):
    """Worker function to fetch details for a single question."""
    result = run_hardcoded_query(query_template_step3, questionId=q_id)
    if result and 'data' in result and 'question' in result['data'] and result['data']['question']:
        return result['data']['question']
    return None

@neetprep_bp.route('/neetprep')
def index():
    """Renders the main NeetPrep UI with topics and counts."""
    conn = get_db_connection()
    
    # Get NeetPrep question counts per topic
    neetprep_topics = conn.execute('SELECT topic, COUNT(*) as count FROM neetprep_questions GROUP BY topic').fetchall()
    neetprep_topic_counts = {row['topic']: row['count'] for row in neetprep_topics}

    # Get classified question counts per chapter
    classified_chapters = conn.execute('SELECT chapter, COUNT(*) as count FROM questions WHERE subject IS NOT NULL AND chapter IS NOT NULL GROUP BY chapter').fetchall()
    classified_chapter_counts = {row['chapter']: row['count'] for row in classified_chapters}

    # Get unclassified count
    unclassified_count = conn.execute("SELECT COUNT(*) as count FROM neetprep_questions WHERE topic = 'Unclassified'").fetchone()['count']

    # Combine the topics
    all_topics = set(neetprep_topic_counts.keys()) | set(classified_chapter_counts.keys())
    
    combined_topics = []
    for topic in sorted(list(all_topics)):
        combined_topics.append({
            'topic': topic,
            'neetprep_count': neetprep_topic_counts.get(topic, 0),
            'my_questions_count': classified_chapter_counts.get(topic, 0)
        })

    conn.close()
    return render_template('neetprep.html', topics=combined_topics, unclassified_count=unclassified_count)

@neetprep_bp.route('/neetprep/sync', methods=['POST'])
def sync_neetprep_data():
    data = request.json
    force_sync = data.get('force', False)
    print(f"NeetPrep sync started. Force sync: {force_sync}")

    try:
        conn = get_db_connection()
        
        if force_sync:
            print("Force sync enabled. Clearing processed attempts and questions tables.")
            conn.execute('DELETE FROM neetprep_processed_attempts')
            conn.execute('DELETE FROM neetprep_questions')
            conn.commit()

        processed_attempts_rows = conn.execute('SELECT attempt_id FROM neetprep_processed_attempts').fetchall()
        processed_attempt_ids = {row['attempt_id'] for row in processed_attempts_rows}
        
        all_attempt_ids = []
        offset = 0
        limit = 100
        print("Fetching test attempts from NeetPrep API...")
        while True:
            result = run_hardcoded_query(query_template_step1, limit=limit, offset=offset, userId=USER_ID)
            if not result or 'data' not in result or not result['data'].get('testAttempts'):
                break
            attempts = result['data']['testAttempts']
            if not attempts: break
            all_attempt_ids.extend([a['id'] for a in attempts if a.get('completed')])
            offset += limit
            time.sleep(0.2)

        new_attempts = [aid for aid in all_attempt_ids if aid not in processed_attempt_ids]
        print(f"Found {len(new_attempts)} new attempts to process.")
        if not new_attempts:
            conn.close()
            return jsonify({'status': 'No new test attempts to sync. Everything is up-to-date.'}), 200

        incorrect_question_ids = set()
        print("Fetching incorrect question IDs for new attempts...")
        for attempt_id in new_attempts:
            result = run_hardcoded_query(query_template_step2, attemptId=attempt_id)
            if result and 'data' in result and result['data'].get('incorrectQuestions'):
                for q in result['data']['incorrectQuestions']:
                    incorrect_question_ids.add(q['id'])
            time.sleep(0.2)

        existing_question_ids_rows = conn.execute('SELECT id FROM neetprep_questions').fetchall()
        existing_question_ids = {row['id'] for row in existing_question_ids_rows}
        new_question_ids = list(incorrect_question_ids - existing_question_ids)
        print(f"Found {len(new_question_ids)} new unique incorrect questions to fetch details for.")

        if not new_question_ids:
            for attempt_id in new_attempts:
                conn.execute('INSERT INTO neetprep_processed_attempts (attempt_id) VALUES (?)', (attempt_id,))
            conn.commit()
            conn.close()
            return jsonify({'status': 'Sync complete. No new questions found, but attempts log updated.'}), 200

        questions_to_insert = []
        total_new = len(new_question_ids)
        completed = 0
        print(f"Fetching details for {total_new} questions...")
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_qid = {executor.submit(fetch_question_details, qid): qid for qid in new_question_ids}
            for future in as_completed(future_to_qid):
                q_data = future.result()
                if q_data:
                    topic_name = "Unclassified"
                    try:
                        topic_name = q_data['topics']['edges'][0]['node']['name']
                    except (IndexError, TypeError, KeyError): pass
                    
                    questions_to_insert.append((q_data.get('id'), q_data.get('question'), json.dumps(q_data.get('options', [])), q_data.get('correctOptionIndex'), q_data.get('level', 'N/A'), topic_name, "Biology"))
                
                completed += 1
                percentage = int((completed / total_new) * 100)
                sys.stdout.write(f'\rSync Progress: {completed}/{total_new} ({percentage}%)')
                sys.stdout.flush()
        
        print("\nAll questions fetched.")

        if questions_to_insert:
            conn.executemany("INSERT INTO neetprep_questions (id, question_text, options, correct_answer_index, level, topic, subject) VALUES (?, ?, ?, ?, ?, ?, ?)", questions_to_insert)
        
        for attempt_id in new_attempts:
            conn.execute('INSERT INTO neetprep_processed_attempts (attempt_id) VALUES (?)', (attempt_id,))

        conn.commit()
        conn.close()

        return jsonify({'status': f'Sync complete. Added {len(questions_to_insert)} new questions.'}), 200

    except Exception as e:
        current_app.logger.error(f"Error during NeetPrep sync: {repr(e)}")
        if 'conn' in locals() and conn:
            conn.close()
        return jsonify({'error': f"A critical error occurred during sync: {repr(e)}"}), 500

@neetprep_bp.route('/neetprep/classify', methods=['POST'])
def classify_unclassified_questions():
    """Classifies all questions marked as 'Unclassified' in batches."""
    print("Starting classification of 'Unclassified' questions.")
    conn = get_db_connection()
    unclassified_questions = conn.execute("SELECT id, question_text FROM neetprep_questions WHERE topic = 'Unclassified'").fetchall()
    total_to_classify = len(unclassified_questions)
    
    if total_to_classify == 0:
        conn.close()
        return jsonify({'status': 'No unclassified questions to process.'})

    batch_size = 10
    num_batches = math.ceil(total_to_classify / batch_size)
    total_classified_count = 0

    print(f"Found {total_to_classify} questions. Processing in {num_batches} batches of {batch_size}.")

    for i in range(num_batches):
        batch_start_time = time.time()
        start_index = i * batch_size
        end_index = start_index + batch_size
        batch = unclassified_questions[start_index:end_index]
        
        question_texts = [q['question_text'] for q in batch]
        question_ids = [q['id'] for q in batch]

        print(f"\nProcessing Batch {i+1}/{num_batches}...")

        try:
            classification_result = classify_questions_with_gemini(question_texts)
            
            if not classification_result or not classification_result.get('data'):
                print(f"Batch {i+1} failed: Gemini API did not return valid data.")
                continue

            update_count_in_batch = 0
            for item in classification_result.get('data', []):
                item_index = item.get('index')
                if item_index is not None and 1 <= item_index <= len(question_ids):
                    # The item['index'] is 1-based, so we convert to 0-based
                    matched_id = question_ids[item_index - 1]
                    new_topic = item.get('chapter_title')
                    if new_topic:
                        conn.execute('UPDATE neetprep_questions SET topic = ? WHERE id = ?', (new_topic, matched_id))
                        update_count_in_batch += 1

            conn.commit()
            total_classified_count += update_count_in_batch
            print(f"Batch {i+1} complete. Classified {update_count_in_batch} questions.")

            # Wait before the next batch
            if i < num_batches - 1:
                print("Waiting 6 seconds before next batch...")
                time.sleep(6)

        except Exception as e:
            print(f"\nAn error occurred during batch {i+1}: {repr(e)}")
            continue
    
    conn.close()
    print(f"\nClassification finished. In total, {total_classified_count} questions were updated.")
    return jsonify({'status': f'Classification complete. Updated {total_classified_count} of {total_to_classify} questions.'})


@neetprep_bp.route('/neetprep/generate', methods=['POST'])
def generate_neetprep_pdf():
    data = request.json
    pdf_type = data.get('type')
    
    conn = get_db_connection()
    
    all_questions = []
    
    if pdf_type == 'all':
        # Get all neetprep questions
        neetprep_questions_from_db = conn.execute("SELECT * FROM neetprep_questions").fetchall()
        for q in neetprep_questions_from_db:
            try:
                options_list = json.loads(q['options'])
            except (json.JSONDecodeError, TypeError):
                options_list = []
            
            formatted_question = {
                "id": q['id'],
                "question_text": q['question_text'],
                "options": options_list,
                "correct_answer_index": q['correct_answer_index'],
                "user_answer_index": None,
                "status": "wrong",
                "custom_fields": {
                    "difficulty": q['level'],
                    "topic": q['topic'],
                    "subject": q['subject']
                }
            }
            all_questions.append(formatted_question)

        # Get all classified questions
        classified_questions_from_db = conn.execute("SELECT * FROM questions WHERE subject IS NOT NULL AND chapter IS NOT NULL").fetchall()
        for q in classified_questions_from_db:
            image_info = conn.execute("SELECT processed_filename FROM images WHERE id = ?", (q['image_id'],)).fetchone()
            if not image_info or not image_info['processed_filename']:
                continue

            image_path = os.path.join(current_app.config['PROCESSED_FOLDER'], image_info['processed_filename'])

            formatted_question = {
                "id": q['id'],
                "question_text": f'<img src="{image_path}" />',
                "options": [],
                "correct_answer_index": None,
                "user_answer_index": None,
                "status": q['status'],
                "custom_fields": {
                    "subject": q['subject'],
                    "chapter": q['chapter']
                }
            }
            all_questions.append(formatted_question)

    elif pdf_type == 'selected':
        topics = data.get('topics')
        if not topics:
            conn.close()
            return jsonify({'error': 'No topics selected.'}), 400
        
        placeholders = ', '.join('?' for _ in topics)
        
        # Get neetprep questions for selected topics
        neetprep_questions_from_db = conn.execute(f"SELECT * FROM neetprep_questions WHERE topic IN ({placeholders})", topics).fetchall()
        for q in neetprep_questions_from_db:
            try:
                options_list = json.loads(q['options'])
            except (json.JSONDecodeError, TypeError):
                options_list = []
            
            formatted_question = {
                "id": q['id'],
                "question_text": q['question_text'],
                "options": options_list,
                "correct_answer_index": q['correct_answer_index'],
                "user_answer_index": None,
                "status": "wrong",
                "custom_fields": {
                    "difficulty": q['level'],
                    "topic": q['topic'],
                    "subject": q['subject']
                }
            }
            all_questions.append(formatted_question)

        # Get classified questions for selected topics
        classified_questions_from_db = conn.execute(f"SELECT * FROM questions WHERE chapter IN ({placeholders})", topics).fetchall()
        for q in classified_questions_from_db:
            image_info = conn.execute("SELECT processed_filename FROM images WHERE id = ?", (q['image_id'],)).fetchone()
            if not image_info or not image_info['processed_filename']:
                continue

            image_path = os.path.join(current_app.config['PROCESSED_FOLDER'], image_info['processed_filename'])

            formatted_question = {
                "id": q['id'],
                "question_text": f'<img src="{image_path}" />',
                "options": [],
                "correct_answer_index": None,
                "user_answer_index": None,
                "status": q['status'],
                "custom_fields": {
                    "subject": q['subject'],
                    "chapter": q['chapter']
                }
            }
            all_questions.append(formatted_question)

    conn.close()

    if not all_questions:
        return jsonify({'error': 'No questions found for the selected criteria.'}), 404

    test_name = "All Incorrect Questions"
    if pdf_type == 'selected':
        test_name = f"Incorrect Questions - {', '.join(data.get('topics', []))}"

    final_json_output = {
        "version": "2.1",
        "test_name": test_name,
        "config": {
            "font_size": 22,
            "statuses_to_include": ["wrong", "unattempted"],
            "auto_generate_pdf": False,
            "layout": data.get('layout', {})
        },
        "metadata": {
            "source_book": "NeetPrep",
            "student_id": USER_ID,
            "tags": ", ".join(data.get('topics', []))
        },
        "questions": all_questions,
        "view": True
    }

    try:
        json_upload_url = url_for('json_bp.json_upload', _external=True)

        response = requests.post(json_upload_url, json=final_json_output)
        response.raise_for_status()
        
        result = response.json()
        
        if result.get('success'):
            return jsonify({'success': True, 'pdf_url': result.get('view_url')})
        else:
            return jsonify({'error': result.get('error', 'Failed to generate PDF via json_upload.')}), 500

    except Exception as e:
        current_app.logger.error(f"Failed to call /json_upload for NeetPrep PDF generation: {repr(e)}")
        return jsonify({'error': str(e)}), 500

@neetprep_bp.route('/neetprep/edit')
def edit_neetprep_questions():
    """Renders the page for editing NeetPrep questions."""
    conn = get_db_connection()
    topics = conn.execute('SELECT DISTINCT topic FROM neetprep_questions ORDER BY topic').fetchall()
    questions = conn.execute('SELECT id, question_text, topic, subject FROM neetprep_questions ORDER BY id').fetchall()
    
    questions_plain = []
    for q in questions:
        q_dict = dict(q)
        soup = BeautifulSoup(q_dict['question_text'], 'html.parser')
        plain_text = soup.get_text(strip=True)
        q_dict['question_text_plain'] = (plain_text[:100] + '...') if len(plain_text) > 100 else plain_text
        questions_plain.append(q_dict)

    conn.close()
    return render_template('neetprep_edit.html', questions=questions_plain, topics=[t['topic'] for t in topics])

@neetprep_bp.route('/neetprep/update_question/<question_id>', methods=['POST'])
def update_neetprep_question(question_id):
    """Handles updating a question's metadata."""
    data = request.json
    new_topic = data.get('topic')
    new_subject = data.get('subject')

    if not new_topic or not new_subject:
        return jsonify({'error': 'Topic and Subject cannot be empty.'}), 400

    try:
        conn = get_db_connection()
        conn.execute(
            'UPDATE neetprep_questions SET topic = ?, subject = ? WHERE id = ?',
            (new_topic, new_subject, question_id)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        current_app.logger.error(f"Error updating question {question_id}: {repr(e)}")
        return jsonify({'error': str(e)}), 500

def run_hardcoded_query(query_template, **kwargs):
    """Helper function to run a GraphQL query."""
    final_query = query_template.format(**kwargs)
    payload = {'query': final_query, 'variables': {}}
    try:
        response = requests.post(ENDPOINT_URL, headers=HEADERS, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"NeetPrep API Request Error: {repr(e)}")
        return None
