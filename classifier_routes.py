from flask import Blueprint, jsonify, current_app, render_template, request
from flask_login import login_required, current_user
from utils import get_db_connection
import os
import time
import json
from processing import resize_image_if_needed, call_nim_ocr_api
from gemini_classifier import classify_questions_with_gemini
from nova_classifier import classify_questions_with_nova

classifier_bp = Blueprint('classifier_bp', __name__)

@classifier_bp.route('/classified/edit')
@login_required
def edit_classified_questions():
    """Renders the page for editing classified questions."""
    conn = get_db_connection()

    AVAILABLE_SUBJECTS = ["Biology", "Chemistry", "Physics", "Mathematics"]
    
    # Security: Fetch questions belonging to the current user
    questions_from_db = conn.execute("""
        SELECT q.id, q.question_text, q.chapter, q.subject, q.tags 
        FROM questions q
        JOIN sessions s ON q.session_id = s.id
        WHERE s.user_id = ? AND q.subject IS NOT NULL AND q.chapter IS NOT NULL 
        ORDER BY q.id
    """, (current_user.id,)).fetchall()
    
    questions = []
    for q in questions_from_db:
        q_dict = dict(q)
        plain_text = q_dict['question_text'] # It's already plain text from OCR
        q_dict['question_text_plain'] = (plain_text[:100] + '...') if len(plain_text) > 100 else plain_text
        questions.append(q_dict)

    # Suggestions should also be user-specific
    chapters = conn.execute('SELECT DISTINCT q.chapter FROM questions q JOIN sessions s ON q.session_id = s.id WHERE s.user_id = ? AND q.chapter IS NOT NULL ORDER BY q.chapter', (current_user.id,)).fetchall()
    tags_query = conn.execute('SELECT DISTINCT q.tags FROM questions q JOIN sessions s ON q.session_id = s.id WHERE s.user_id = ? AND q.tags IS NOT NULL AND q.tags != \'\'', (current_user.id,)).fetchall()
    all_tags = set()
    for row in tags_query:
        tags = [tag.strip() for tag in row['tags'].split(',')]
        all_tags.update(tags)

    conn.close()
    return render_template('classified_edit.html', 
                           questions=questions, 
                           chapters=[c['chapter'] for c in chapters], 
                           all_tags=sorted(list(all_tags)),
                           available_subjects=AVAILABLE_SUBJECTS)

@classifier_bp.route('/classified/update_question/<int:question_id>', methods=['POST'])
@login_required
def update_classified_question(question_id):
    """Handles updating a question's metadata."""
    data = request.json
    new_chapter = data.get('chapter')
    new_subject = data.get('subject')

    if not new_chapter or not new_subject:
        return jsonify({'error': 'Chapter and Subject cannot be empty.'}), 400

    try:
        conn = get_db_connection()
        # Security: Check ownership before update
        question_owner = conn.execute("SELECT s.user_id FROM questions q JOIN sessions s ON q.session_id = s.id WHERE q.id = ?", (question_id,)).fetchone()
        if not question_owner or question_owner['user_id'] != current_user.id:
            conn.close()
            return jsonify({'error': 'Unauthorized'}), 403

        conn.execute(
            'UPDATE questions SET chapter = ?, subject = ? WHERE id = ?',
            (new_chapter, new_subject, question_id)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        current_app.logger.error(f"Error updating question {question_id}: {repr(e)}")
        return jsonify({'error': str(e)}), 500

@classifier_bp.route('/classified/delete_question/<int:question_id>', methods=['DELETE'])
@login_required
def delete_classified_question(question_id):
    """Handles deleting a classified question."""
    try:
        conn = get_db_connection()
        # Security: Check ownership before delete
        question_owner = conn.execute("SELECT s.user_id FROM questions q JOIN sessions s ON q.session_id = s.id WHERE q.id = ?", (question_id,)).fetchone()
        if not question_owner or question_owner['user_id'] != current_user.id:
            conn.close()
            return jsonify({'error': 'Unauthorized'}), 403

        # Update the question to remove classification
        conn.execute('UPDATE questions SET subject = NULL, chapter = NULL WHERE id = ?', (question_id,))
        
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        current_app.logger.error(f"Error deleting question {question_id}: {repr(e)}")
        return jsonify({'error': str(e)}), 500

@classifier_bp.route('/classified/delete_many', methods=['POST'])
@login_required
def delete_many_classified_questions():
    """Handles bulk deleting classified questions."""
    data = request.json
    question_ids = data.get('ids', [])

    if not question_ids:
        return jsonify({'error': 'No question IDs provided.'}), 400

    try:
        conn = get_db_connection()
        # Security: Filter IDs to only those owned by the user
        placeholders = ','.join('?' for _ in question_ids)
        owned_q_ids_rows = conn.execute(f"""
            SELECT q.id FROM questions q
            JOIN sessions s ON q.session_id = s.id
            WHERE q.id IN ({placeholders}) AND s.user_id = ?
        """, (*question_ids, current_user.id)).fetchall()
        
        owned_q_ids = [row['id'] for row in owned_q_ids_rows]

        if not owned_q_ids:
            conn.close()
            return jsonify({'success': True, 'message': 'No owned questions to delete.'})

        update_placeholders = ','.join('?' for _ in owned_q_ids)
        conn.execute(f'UPDATE questions SET subject = NULL, chapter = NULL WHERE id IN ({update_placeholders})', owned_q_ids)
        
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        current_app.logger.error(f"Error deleting questions: {repr(e)}")
        return jsonify({'error': str(e)}), 500

from rich.table import Table
from rich.console import Console

@classifier_bp.route('/extract_and_classify_all/<session_id>', methods=['POST'])
@login_required
def extract_and_classify_all(session_id):
    try:
        conn = get_db_connection()
        # Security: Check ownership of the session
        session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
        if not session_owner or session_owner['user_id'] != current_user.id:
            conn.close()
            return jsonify({'error': 'Unauthorized'}), 403

        images = conn.execute(
            "SELECT id, processed_filename FROM images WHERE session_id = ? AND image_type = 'cropped' ORDER BY id", 
            (session_id,)
        ).fetchall()
        
        if not images:
            conn.close()
            return jsonify({'error': 'No cropped images found in session'}), 404

        current_app.logger.info(f"Found {len(images)} images to process for user {current_user.id}.")

        question_texts = []
        image_ids = []
        for image in images:
            image_id = image['id']
            processed_filename = image['processed_filename']
            
            if not processed_filename:
                continue
            
            image_path = os.path.join(current_app.config['PROCESSED_FOLDER'], processed_filename)
            if not os.path.exists(image_path):
                continue
            
            image_bytes = resize_image_if_needed(image_path)
            ocr_result = call_nim_ocr_api(image_bytes)
            
            current_app.logger.info(f"NVIDIA OCR Result for image {image_id}: {ocr_result}")

            if not ocr_result.get('data') or not ocr_result['data'][0].get('text_detections'):
                current_app.logger.error(f"NVIDIA OCR result for image {image_id} does not contain 'text_detections' key. Full response: {ocr_result}")
                continue

            text = " ".join(item['text_prediction']['text'] for item in ocr_result['data'][0]['text_detections'])
            
            conn.execute('UPDATE questions SET question_text = ? WHERE image_id = ?', (text, image_id))
            current_app.logger.info(f"Updated question_text for image_id: {image_id}")
            question_texts.append(text)
            image_ids.append(image_id)

        conn.commit()

        # --- Batch Processing and Classification ---
        batch_size = 7 # Default batch size
        total_questions = len(question_texts)
        num_batches = (total_questions + batch_size - 1) // batch_size
        total_update_count = 0

        for i in range(num_batches):
            start_index = i * batch_size
            end_index = start_index + batch_size
            
            batch_texts = question_texts[start_index:end_index]
            batch_image_ids = image_ids[start_index:end_index]

            if not batch_texts:
                continue

            current_app.logger.info(f"Processing Batch {i+1}/{num_batches}...")

            # Choose classifier based on user preference
            classifier_model = getattr(current_user, 'classifier_model', 'gemini')
            
            if classifier_model == 'nova':
                current_app.logger.info(f"Using Nova classifier for user {current_user.id}")
                classification_result = classify_questions_with_nova(batch_texts, start_index=start_index)
                model_name = "Nova"
            else:
                current_app.logger.info(f"Using Gemini classifier for user {current_user.id}")
                classification_result = classify_questions_with_gemini(batch_texts, start_index=start_index)
                model_name = "Gemini"
            
            # Log the result to the terminal
            current_app.logger.info(f"--- Classification Result ({model_name}) for Batch {i+1} ---")
            current_app.logger.info(json.dumps(classification_result, indent=2))
            current_app.logger.info("---------------------------------------------")

            if not classification_result or not classification_result.get('data'):
                current_app.logger.error(f'{model_name} classifier did not return valid data for batch {i+1}.')
                continue # Move to the next batch

            # --- Immediate DB Update for the Batch ---
            batch_update_count = 0
            for item in classification_result.get('data', []):
                item_index_global = item.get('index') # This is the global index (e.g., 1 to 14)
                if item_index_global is not None:
                    # Find the corresponding local index in our full list
                    try:
                        # The item_index_global is 1-based, our list is 0-based
                        local_list_index = item_index_global - 1
                        # Find the image_id for that question
                        matched_id = image_ids[local_list_index]
                    except IndexError:
                        current_app.logger.error(f"Classifier returned an out-of-bounds index: {item_index_global}")
                        continue

                    new_subject = item.get('subject')
                    new_chapter = item.get('chapter_title')
                    
                    if new_subject and new_subject != 'Unclassified' and new_chapter and new_chapter != 'Unclassified':
                        conn.execute('UPDATE questions SET subject = ?, chapter = ? WHERE image_id = ?', (new_subject, new_chapter, matched_id))
                        batch_update_count += 1
                    elif new_subject and new_subject != 'Unclassified':
                        conn.execute('UPDATE questions SET subject = ?, chapter = ? WHERE image_id = ?', (new_subject, 'Unclassified', matched_id))
                        batch_update_count += 1

            conn.commit()
            total_update_count += batch_update_count
            current_app.logger.info(f"Batch {i+1} processed. Updated {batch_update_count} questions in the database.")

            if i < num_batches - 1:
                current_app.logger.info("Waiting 5 seconds before next batch...")
                time.sleep(5)

        conn.close()

        return jsonify({'success': True, 'message': f'Successfully extracted and classified {total_questions} questions. Updated {total_update_count} entries in the database.'})

    except Exception as e:
        current_app.logger.error(f'Failed to extract and classify questions: {str(e)}', exc_info=True)
        return jsonify({'error': f'Failed to extract and classify questions: {str(e)}'}), 500
