from flask import Blueprint, jsonify, current_app, render_template, request
from utils import get_db_connection
import os
from processing import resize_image_if_needed, call_nim_ocr_api
from gemini_classifier import classify_questions_with_gemini

classifier_bp = Blueprint('classifier_bp', __name__)

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_NIM_AVAILABLE = bool(NVIDIA_API_KEY)

@classifier_bp.route('/classified/edit')
def edit_classified_questions():
    """Renders the page for editing classified questions."""
    conn = get_db_connection()
    questions_from_db = conn.execute('SELECT id, question_text, chapter, subject FROM questions WHERE subject IS NOT NULL AND chapter IS NOT NULL ORDER BY id').fetchall()
    
    questions = []
    for q in questions_from_db:
        q_dict = dict(q)
        plain_text = q_dict['question_text'] # It's already plain text from OCR
        q_dict['question_text_plain'] = (plain_text[:100] + '...') if len(plain_text) > 100 else plain_text
        questions.append(q_dict)

    chapters = conn.execute('SELECT DISTINCT chapter FROM questions WHERE chapter IS NOT NULL ORDER BY chapter').fetchall()
    conn.close()
    return render_template('classified_edit.html', questions=questions, chapters=[c['chapter'] for c in chapters])

@classifier_bp.route('/classified/update_question/<int:question_id>', methods=['POST'])
def update_classified_question(question_id):
    """Handles updating a question's metadata."""
    data = request.json
    new_chapter = data.get('chapter')
    new_subject = data.get('subject')

    if not new_chapter or not new_subject:
        return jsonify({'error': 'Chapter and Subject cannot be empty.'}), 400

    try:
        conn = get_db_connection()
        conn.execute(
            'UPDATE questions SET chapter = ?, subject = ? WHERE id = ?',
            (new_chapter, new_subject, question_id)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        current_app.logger.error(f"Error updating question {question_id}: {repr(e)}")
@classifier_bp.route('/classified/delete_question/<int:question_id>', methods=['DELETE'])
def delete_classified_question(question_id):
    """Handles deleting a classified question."""
    try:
        conn = get_db_connection()
        # First, get the image_id from the questions table
        image_id_res = conn.execute('SELECT image_id FROM questions WHERE id = ?', (question_id,)).fetchone()
        if not image_id_res:
            conn.close()
            return jsonify({'error': 'Question not found'}), 404
        
        image_id = image_id_res['image_id']

        # Get the image filename to delete from the filesystem
        image_info = conn.execute('SELECT processed_filename FROM images WHERE id = ?', (image_id,)).fetchone()
        if image_info and image_info['processed_filename']:
            try:
                os.remove(os.path.join(current_app.config['PROCESSED_FOLDER'], image_info['processed_filename']))
            except OSError as e:
                current_app.logger.error(f"Error deleting image file: {e}")

        # Delete from questions and images tables
        conn.execute('DELETE FROM questions WHERE id = ?', (question_id,))
        conn.execute('DELETE FROM images WHERE id = ?', (image_id,))
        
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        current_app.logger.error(f"Error deleting question {question_id}: {repr(e)}")
        return jsonify({'error': str(e)}), 500


@classifier_bp.route('/extract_and_classify_all/<session_id>', methods=['POST'])
def extract_and_classify_all(session_id):
    if not NVIDIA_NIM_AVAILABLE:
        return jsonify({'error': 'NVIDIA NIM feature is not available. Please set the NVIDIA_API_KEY environment variable.'}), 400

    try:
        conn = get_db_connection()
        images = conn.execute(
            "SELECT id, processed_filename FROM images WHERE session_id = ? AND image_type = 'cropped' ORDER BY id", 
            (session_id,)
        ).fetchall()
        
        if not images:
            conn.close()
            return jsonify({'error': 'No cropped images found in session'}), 404

        current_app.logger.info(f"Found {len(images)} images to process.")

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

        current_app.logger.info(f"Extracted text for {len(question_texts)} questions. Now classifying with Gemini.")
        classification_result = classify_questions_with_gemini(question_texts)
        current_app.logger.info(f"Gemini classification result: {classification_result}")


        if not classification_result or not classification_result.get('data'):
            conn.close()
            return jsonify({'error': 'Gemini API did not return valid data.'}), 500

        update_count = 0
        for item in classification_result.get('data', []):
            item_index = item.get('index')
            if item_index is not None and 1 <= item_index <= len(image_ids):
                matched_id = image_ids[item_index - 1]
                new_chapter = item.get('chapter_title')
                if new_chapter and new_chapter != 'Non-Biology':
                    conn.execute('UPDATE questions SET subject = ?, chapter = ? WHERE image_id = ?', ("Biology", new_chapter, matched_id))
                    current_app.logger.info(f"Updated subject to 'Biology' and chapter to '{new_chapter}' for image_id: {matched_id}")
                    update_count += 1

        conn.commit()
        current_app.logger.info(f"Updated {update_count} questions in the database.")

        # Add this for debugging
        if image_ids:
            test_image_id = image_ids[0]
            res = conn.execute("SELECT * FROM questions WHERE image_id = ?", (test_image_id,)).fetchone()
            current_app.logger.info(f"DEBUG: Question data for image_id {test_image_id}: {dict(res) if res else 'Not Found'}")

        conn.close()

        return jsonify({'success': True, 'message': f'Successfully extracted and classified {len(image_ids)} questions.'})

    except Exception as e:
        current_app.logger.error(f'Failed to extract and classify questions: {str(e)}', exc_info=True)
        return jsonify({'error': f'Failed to extract and classify questions: {str(e)}'}), 500
