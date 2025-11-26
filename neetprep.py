from flask import Blueprint, render_template, request, jsonify, current_app, url_for
from flask_login import login_required, current_user
from utils import get_db_connection
import requests
import time
import os
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
import math
import imgkit

from gemini_classifier import classify_questions_with_gemini
from json_processor import _process_json_and_generate_pdf
from json_processor import _process_json_and_generate_pdf

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
@login_required
def index():
    """Renders the main NeetPrep UI with topics and counts."""
    conn = get_db_connection()
    selected_subject = request.args.get('subject', 'All')
    AVAILABLE_SUBJECTS = ["All", "Biology", "Chemistry", "Physics", "Mathematics"]
    
    neetprep_topic_counts = {}
    unclassified_count = 0
    if current_user.neetprep_enabled:
        # Get NeetPrep question counts per topic, filtered by subject
        if selected_subject != 'All':
            neetprep_topics_query = 'SELECT topic, COUNT(*) as count FROM neetprep_questions WHERE subject = ? GROUP BY topic'
            neetprep_topics = conn.execute(neetprep_topics_query, (selected_subject,)).fetchall()
        else:
            neetprep_topics_query = 'SELECT topic, COUNT(*) as count FROM neetprep_questions GROUP BY topic'
            neetprep_topics = conn.execute(neetprep_topics_query).fetchall()
        neetprep_topic_counts = {row['topic']: row['count'] for row in neetprep_topics}
        unclassified_count = conn.execute("SELECT COUNT(*) as count FROM neetprep_questions WHERE topic = 'Unclassified'").fetchone()['count']


    # Get classified question counts per chapter for the current user, filtered by subject
    query_params = [current_user.id]
    base_query = """
        SELECT q.chapter, COUNT(*) as count 
        FROM questions q
        JOIN sessions s ON q.session_id = s.id
        WHERE s.user_id = ? AND q.subject IS NOT NULL AND q.chapter IS NOT NULL 
    """
    if selected_subject != 'All':
        base_query += " AND q.subject = ? "
        query_params.append(selected_subject)
    
    base_query += " GROUP BY q.chapter"
    
    classified_chapters = conn.execute(base_query, query_params).fetchall()
    classified_chapter_counts = {row['chapter']: row['count'] for row in classified_chapters}

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
    return render_template('neetprep.html', 
                           topics=combined_topics, 
                           unclassified_count=unclassified_count,
                           available_subjects=AVAILABLE_SUBJECTS,
                           selected_subject=selected_subject,
                           neetprep_enabled=current_user.neetprep_enabled)

@neetprep_bp.route('/neetprep/sync', methods=['POST'])
@login_required
def sync_neetprep_data():
    data = request.json
    force_sync = data.get('force', False)
    print(f"NeetPrep sync started by user {current_user.id}. Force sync: {force_sync}")

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
                    
                    questions_to_insert.append((q_data.get('id'), q_data.get('question'), json.dumps(q_data.get('options', [])), q_data.get('correctOptionIndex'), q_data.get('level', 'N/A'), topic_name, "Unclassified"))
                
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
@login_required
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


from rich.table import Table
from rich.console import Console

@neetprep_bp.route('/neetprep/generate', methods=['POST'])
@login_required
def generate_neetprep_pdf():
    if request.is_json:
        data = request.json
    else:
        data = request.form
    
    pdf_type = data.get('type')
    topics_str = data.get('topics')
    topics = json.loads(topics_str) if topics_str and topics_str != '[]' else []

    conn = get_db_connection()
    all_questions = []
    
    # Only fetch NeetPrep questions if the feature is enabled for the user
    if current_user.neetprep_enabled:
        if pdf_type == 'quiz' and topics:
            placeholders = ', '.join('?' for _ in topics)
            neetprep_questions_from_db = conn.execute(f"SELECT * FROM neetprep_questions WHERE topic IN ({placeholders})", topics).fetchall()
            for q in neetprep_questions_from_db:
                try:
                    html_content = f"""<html><head><meta charset="utf-8"></head><body>{q['question_text']}</body></html>"""
                    img_path = os.path.join(current_app.config['TEMP_FOLDER'], f"neetprep_{q['id']}.jpg")
                    imgkit.from_string(html_content, img_path, options={'width': 800})
                    all_questions.append({
                        'image_path': img_path,
                        'details': {'id': q['id'], 'options': json.loads(q['options']), 'correct_answer_index': q['correct_answer_index'], 'user_answer_index': None, 'source': 'neetprep', 'topic': q['topic'], 'subject': q['subject']}
                    })
                except Exception as e:
                    current_app.logger.error(f"Failed to convert NeetPrep question {q['id']} to image: {e}")
        
        elif pdf_type == 'all':
            neetprep_questions_from_db = conn.execute("SELECT * FROM neetprep_questions").fetchall()
            for q in neetprep_questions_from_db:
                all_questions.append({"id": q['id'], "question_text": q['question_text'], "options": json.loads(q['options']), "correct_answer_index": q['correct_answer_index'], "user_answer_index": None, "status": "wrong", "source": "neetprep", "custom_fields": {"difficulty": q['level'], "topic": q['topic'], "subject": q['subject']}})
        
        elif pdf_type == 'selected' and topics:
            placeholders = ', '.join('?' for _ in topics)
            neetprep_questions_from_db = conn.execute(f"SELECT * FROM neetprep_questions WHERE topic IN ({placeholders})", topics).fetchall()
            for q in neetprep_questions_from_db:
                all_questions.append({"id": q['id'], "question_text": q['question_text'], "options": json.loads(q['options']), "correct_answer_index": q['correct_answer_index'], "user_answer_index": None, "status": "wrong", "source": "neetprep", "custom_fields": {"difficulty": q['level'], "topic": q['topic'], "subject": q['subject']}})

    # Always fetch the user's own classified questions if topics are selected or if it's a quiz
    if topics or pdf_type == 'quiz':
        # If no topics are selected for a quiz/selection, this should not run or fetch all
        if not topics:
             # For a quiz, topics are mandatory. For 'selected', topics are mandatory.
            if pdf_type in ['quiz', 'selected']:
                conn.close()
                return jsonify({'error': 'No topics selected.'}), 400
        else:
            placeholders = ', '.join('?' for _ in topics)
            classified_questions_from_db = conn.execute(f"""
                SELECT q.* FROM questions q JOIN sessions s ON q.session_id = s.id
                WHERE q.chapter IN ({placeholders}) AND s.user_id = ?
            """, (*topics, current_user.id)).fetchall()
            for q in classified_questions_from_db:
                image_info = conn.execute("SELECT processed_filename FROM images WHERE id = ?", (q['image_id'],)).fetchone()
                if image_info and image_info['processed_filename']:
                    if pdf_type == 'quiz':
                        all_questions.append({'image_path': os.path.join(current_app.config['PROCESSED_FOLDER'], image_info['processed_filename']),'details': {'id': q['id'], 'options': [], 'correct_answer_index': q['actual_solution'], 'user_answer_index': q['marked_solution'], 'source': 'classified', 'topic': q['chapter'], 'subject': q['subject']}})
                    else:
                        all_questions.append({"id": q['id'], "question_text": f"<img src=\"{os.path.join(current_app.config['PROCESSED_FOLDER'], image_info['processed_filename'])}\" />", "options": [], "correct_answer_index": q['actual_solution'], "user_answer_index": q['marked_solution'], "status": q['status'], "source": "classified", "custom_fields": {"subject": q['subject'], "chapter": q['chapter'], "question_number": q['question_number']}})
    
    # For 'all' type, also include user's classified questions
    if pdf_type == 'all':
        classified_questions_from_db = conn.execute("""
            SELECT q.* FROM questions q JOIN sessions s ON q.session_id = s.id
            WHERE s.user_id = ? AND q.subject IS NOT NULL AND q.chapter IS NOT NULL
        """, (current_user.id,)).fetchall()
        for q in classified_questions_from_db:
            image_info = conn.execute("SELECT processed_filename FROM images WHERE id = ?", (q['image_id'],)).fetchone()
            if image_info and image_info['processed_filename']:
                all_questions.append({"id": q['id'], "question_text": f"<img src=\"{os.path.join(current_app.config['PROCESSED_FOLDER'], image_info['processed_filename'])}\" />", "options": [], "correct_answer_index": q['actual_solution'], "user_answer_index": q['marked_solution'], "status": q['status'], "source": "classified", "custom_fields": {"subject": q['subject'], "chapter": q['chapter'], "question_number": q['question_number']}})

    conn.close()

    if not all_questions:
        return jsonify({'error': 'No questions found for the selected criteria.'}), 404

    if pdf_type == 'quiz':
        return render_template('quiz_v2.html', questions=all_questions)

    test_name = "All Incorrect Questions"
    if pdf_type == 'selected':
        test_name = f"Incorrect Questions - {', '.join(topics)}"

    final_json_output = {
        "version": "2.1", "test_name": test_name,
        "config": { "font_size": 22, "auto_generate_pdf": False, "layout": data.get('layout', {}) },
        "metadata": { "source_book": "NeetPrep & Classified", "student_id": USER_ID, "tags": ", ".join(topics) },
        "questions": all_questions, "view": True
    }

    try:
        result, status_code = _process_json_and_generate_pdf(final_json_output, current_user.id)
        if status_code != 200:
            return jsonify(result), status_code
        
        if result.get('success'):
            return jsonify({'success': True, 'pdf_url': result.get('view_url')})
        else:
            return jsonify({'error': result.get('error', 'Failed to generate PDF via internal call.')}), 500
    except Exception as e:
        current_app.logger.error(f"Failed to call _process_json_and_generate_pdf: {repr(e)}")
        return jsonify({'error': str(e)}), 500

@neetprep_bp.route('/neetprep/edit')
@login_required
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
@login_required
def update_neetprep_question(question_id):
    """Handles updating a question's metadata."""
    # This route modifies global neetprep data. In a real multi-user app,
    # this should be restricted to admin users. For now, @login_required is a basic protection.
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
