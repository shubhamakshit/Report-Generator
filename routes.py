
import os
import uuid
import base64
import io
import zipfile
from datetime import datetime
from flask import (
    Blueprint,
    Response,
    render_template,
    request,
    jsonify,
    send_file,
    redirect,
    url_for,
    current_app,
)
from werkzeug.utils import secure_filename
import fitz
import cv2
import numpy as np

from database import get_folder_tree, get_all_descendant_folder_ids
from processing import (
    resize_image_if_needed,
    call_nim_ocr_api,
    extract_question_number_from_ocr_result,
    crop_image_perspective,
    create_pdf_from_full_images,
)
from utils import get_db_connection, create_a4_pdf_from_images
from strings import *
from redact import redact_pictures_in_image
from resize import expand_pdf_for_notes

main_bp = Blueprint('main', __name__)

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_NIM_AVAILABLE = bool(NVIDIA_API_KEY)

@main_bp.route('/process_final_pdf/<int:pdf_id>')
def process_final_pdf(pdf_id):
    conn = get_db_connection()
    pdf_info = conn.execute('SELECT filename FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
    
    if not pdf_info:
        conn.close()
        return redirect(url_for('main.index_v2')) 

    original_filename = pdf_info['filename']
    pdf_path = os.path.join(current_app.config['OUTPUT_FOLDER'], original_filename)

    if not os.path.exists(pdf_path):
        conn.close()
        return redirect(url_for('main.index_v2'))

    session_id = str(uuid.uuid4())
    
    conn.execute('INSERT INTO sessions (id, original_filename) VALUES (?, ?)', (session_id, original_filename))
    
    doc = fitz.open(pdf_path)
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=900)
        page_filename = f"{session_id}_page_{i}.png"
        page_path = os.path.join(current_app.config['UPLOAD_FOLDER'], page_filename)
        pix.save(page_path)
        
        conn.execute(
            'INSERT INTO images (session_id, image_index, filename, original_name, image_type) VALUES (?, ?, ?, ?, ?)',
            (session_id, i, page_filename, f"Page {i+1}", 'original')
        )
    
    conn.commit()
    conn.close()
    doc.close()
    
    return redirect(url_for('main.crop_interface_v2', session_id=session_id, image_index=0))

@main_bp.route(ROUTE_INDEX_V2)
def index_v2():
    conn = get_db_connection()
    pdfs = conn.execute('SELECT id, filename, subject, tags, notes, persist FROM generated_pdfs ORDER BY created_at DESC').fetchall()
    conn.close()
    return render_template('indexv2.html', pdfs=[dict(row) for row in pdfs])

@main_bp.route(ROUTE_IMAGES)
def image_upload():
    return render_template('image_upload.html')

@main_bp.route(ROUTE_UPLOAD_PDF, methods=[METHOD_POST])
def upload_pdf():
    session_id = str(uuid.uuid4())
    if 'pdf' not in request.files:
        return jsonify({'error': 'No PDF file part'}), 400
    file = request.files['pdf']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file and file.filename.lower().endswith('.pdf'):
        conn = get_db_connection()
        conn.execute('INSERT INTO sessions (id, original_filename) VALUES (?, ?)', (session_id, secure_filename(file.filename)))
        
        pdf_filename = f"{session_id}_{secure_filename(file.filename)}"
        pdf_path = os.path.join(current_app.config['UPLOAD_FOLDER'], pdf_filename)
        file.save(pdf_path)

        doc = fitz.open(pdf_path)
        page_files = []
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=900)
            page_filename = f"{session_id}_page_{i}.png"
            page_path = os.path.join(current_app.config['UPLOAD_FOLDER'], page_filename)
            pix.save(page_path)
            
            conn.execute(
                'INSERT INTO images (session_id, image_index, filename, original_name, image_type) VALUES (?, ?, ?, ?, ?)',
                (session_id, i, page_filename, f"Page {i+1}", 'original')
            )
            page_files.append({'filename': page_filename, 'original_name': f"Page {i+1}", 'index': i})
        
        conn.commit()
        conn.close()
        doc.close()
        return jsonify({'session_id': session_id, 'files': page_files})
    else:
        return jsonify({'error': 'Invalid file type, please upload a PDF'}), 400

@main_bp.route(ROUTE_UPLOAD_IMAGES, methods=[METHOD_POST])
def upload_images():
    session_id = str(uuid.uuid4())
    
    if 'images' not in request.files:
        return jsonify({'error': 'No image files part'}), 400
    
    files = request.files.getlist('images')
    
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No selected files'}), 400

    valid_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp'}
    for file in files:
        if not file or not any(file.filename.lower().endswith(ext) for ext in valid_extensions):
            return jsonify({'error': 'Invalid file type. Please upload only image files (PNG, JPG, JPEG, GIF, BMP)'}), 400

    conn = get_db_connection()
    original_filename = f"{len(files)} images" if len(files) > 1 else secure_filename(files[0].filename) if files else "images"
    conn.execute('INSERT INTO sessions (id, original_filename) VALUES (?, ?)', (session_id, original_filename))
    
    uploaded_files = []
    for i, file in enumerate(files):
        if file and file.filename != '':
            filename = f"{session_id}_{secure_filename(file.filename)}"
            file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            conn.execute(
                'INSERT INTO images (session_id, image_index, filename, original_name, image_type) VALUES (?, ?, ?, ?, ?)',
                (session_id, i, filename, secure_filename(file.filename), 'original')
            )
            uploaded_files.append({'filename': filename, 'original_name': secure_filename(file.filename), 'index': i})
    
    conn.commit()
    conn.close()
    
    return jsonify({'session_id': session_id, 'files': uploaded_files})

@main_bp.route('/cropv2/<session_id>/<int:image_index>')
def crop_interface_v2(session_id, image_index):
    conn = get_db_connection()
    
    image_info = conn.execute(
        "SELECT * FROM images WHERE session_id = ? AND image_index = ? AND image_type = 'original'",
        (session_id, image_index)
    ).fetchone()
    
    if not image_info:
        conn.close()
        return "Original page/image not found for this session and index.", 404

    total_pages_result = conn.execute(
        "SELECT COUNT(*) FROM images WHERE session_id = ? AND image_type = 'original'",
        (session_id,)
    ).fetchone()
    total_pages = total_pages_result[0] if total_pages_result else 0
    
    conn.close()
    
    return render_template(
        'cropv2.html', 
        session_id=session_id, 
        image_index=image_index, 
        image_info=dict(image_info),
        total_pages=total_pages
    )

@main_bp.route(ROUTE_PROCESS_CROP_V2, methods=[METHOD_POST])
def process_crop_v2():
    data = request.json
    session_id, page_index, boxes_data, image_data_url = data['session_id'], data['image_index'], data['boxes'], data.get('imageData')

    conn = get_db_connection()
    page_info = conn.execute(
        "SELECT filename FROM images WHERE session_id = ? AND image_index = ? AND image_type = 'original'", 
        (session_id, page_index)
    ).fetchone()

    if not page_info:
        conn.close()
        return jsonify({'error': 'Original page not found in session'}), 404
    
    try:
        header, encoded = image_data_url.split(",", 1)
        image_data = base64.b64decode(encoded)
        
        temp_filename = f"temp_filtered_{page_info['filename']}"
        temp_path = os.path.join(current_app.config['PROCESSED_FOLDER'], temp_filename)
        with open(temp_path, "wb") as f: f.write(image_data)

        existing_cropped = conn.execute(
            "SELECT id, processed_filename FROM images WHERE session_id = ? AND filename = ? AND image_type = 'cropped'",
            (session_id, page_info['filename'])
        ).fetchall()
        
        for cropped_img in existing_cropped:
            if cropped_img['processed_filename']:
                try: os.remove(os.path.join(current_app.config['PROCESSED_FOLDER'], cropped_img['processed_filename']))
                except OSError: pass
            conn.execute("DELETE FROM questions WHERE session_id = ? AND image_id = ?", (session_id, cropped_img['id']))
        
        conn.execute(
            "DELETE FROM images WHERE session_id = ? AND filename = ? AND image_type = 'cropped'",
            (session_id, page_info['filename'])
        )

        primary_boxes = [box for box in boxes_data if not box.get('stitch_to')]
        processed_boxes = []

        for i, primary_box in enumerate(primary_boxes):
            children = [box for box in boxes_data if box.get('stitch_to') == primary_box['id']]
            
            points = [
                {'x': primary_box['x'], 'y': primary_box['y']},
                {'x': primary_box['x'] + primary_box['w'], 'y': primary_box['y']},
                {'x': primary_box['x'] + primary_box['w'], 'y': primary_box['y'] + primary_box['h']},
                {'x': primary_box['x'], 'y': primary_box['y'] + primary_box['h']}
            ]
            primary_crop = crop_image_perspective(temp_path, points)

            stitched_image = primary_crop

            if children:
                child = children[0]
                child_points = [
                    {'x': child['x'], 'y': child['y']},
                    {'x': child['x'] + child['w'], 'y': child['y']},
                    {'x': child['x'] + child['w'], 'y': child['y'] + child['h']},
                    {'x': child['x'], 'y': child['y'] + child['h']}
                ]
                child_crop = crop_image_perspective(temp_path, child_points)

                h1, w1 = primary_crop.shape[:2]
                h2, w2 = child_crop.shape[:2]
                max_width = max(w1, w2)

                stitched_image = np.full((h1 + h2, max_width, 3), 255, dtype=np.uint8)

                x_offset1 = (max_width - w1) // 2
                stitched_image[0:h1, x_offset1:x_offset1 + w1] = primary_crop

                x_offset2 = (max_width - w2) // 2
                stitched_image[h1:h1 + h2, x_offset2:x_offset2 + w2] = child_crop

            crop_filename = f"processed_{session_id}_page{page_index}_crop{i}.jpg"
            crop_path = os.path.join(current_app.config['PROCESSED_FOLDER'], crop_filename)
            cv2.imwrite(crop_path, stitched_image)

            processed_boxes.append({
                'original_filename': page_info['filename'],
                'original_name': f"Page {page_index + 1} - Q{i + 1}",
                'processed_filename': crop_filename
            })

        max_index_result = conn.execute('SELECT MAX(image_index) FROM images WHERE session_id = ?', (session_id,)).fetchone()
        next_index = (max_index_result[0] if max_index_result[0] is not None else -1) + 1
        
        images_to_insert = []
        for i, p_box in enumerate(processed_boxes):
            images_to_insert.append((
                session_id,
                next_index + i,
                p_box['original_filename'],
                p_box['original_name'],
                p_box['processed_filename'],
                'cropped'
            ))
        
        if images_to_insert:
            conn.executemany(
                'INSERT INTO images (session_id, image_index, filename, original_name, processed_filename, image_type) VALUES (?, ?, ?, ?, ?, ?)',
                images_to_insert
            )
        
        conn.commit()
        conn.close()
        os.remove(temp_path)
        
        return jsonify({'success': True, 'processed_count': len(processed_boxes)})

    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"V2 Processing error: {e}")
        return jsonify({'error': f'Processing failed: {str(e)}'}), 500

@main_bp.route('/question_entry_v2/<session_id>')
def question_entry_v2(session_id):
    test_name = request.args.get('test_name')
    conn = get_db_connection()
    images = conn.execute(
        """SELECT i.id, i.processed_filename, q.question_number, q.status, q.marked_solution, q.actual_solution 
           FROM images i 
           LEFT JOIN questions q ON i.id = q.image_id 
           WHERE i.session_id = ? AND i.image_type = 'cropped' 
           ORDER BY i.id""", 
        (session_id,)
    ).fetchall()
    conn.close()
    
    if not images:
        return "No questions were created from the PDF. Please go back and draw crop boxes.", 404
        
    return render_template('question_entry_v2.html', 
                          session_id=session_id, 
                          images=[dict(img) for img in images],
                          nvidia_nim_available=NVIDIA_NIM_AVAILABLE,
                          test_name=test_name)

@main_bp.route(ROUTE_DASHBOARD)
def dashboard():
    conn = get_db_connection()
    sessions = conn.execute("""
        SELECT s.id, s.created_at, s.original_filename, s.persist,
               COUNT(CASE WHEN i.image_type = 'original' THEN 1 END) as page_count,
               COUNT(CASE WHEN i.image_type = 'cropped' THEN 1 END) as question_count
        FROM sessions s
        LEFT JOIN images i ON s.id = i.session_id
        GROUP BY s.id, s.created_at, s.original_filename, s.persist
        ORDER BY s.created_at DESC
    """).fetchall()
    
    processed_sessions = []
    for session in sessions:
        session_dict = dict(session)
        session_dict['pdf_name'] = session_dict['original_filename'] or 'Unknown'
        processed_sessions.append(session_dict)
    
    conn.close()
    
    return render_template('dashboard.html', sessions=processed_sessions)

@main_bp.route('/delete_session/<session_id>', methods=[METHOD_DELETE])
def delete_session(session_id):
    try:
        conn = get_db_connection()
        images_to_delete = conn.execute('SELECT filename, processed_filename FROM images WHERE session_id = ?', (session_id,)).fetchall()
        for img in images_to_delete:
            if img['filename']:
                try: os.remove(os.path.join(current_app.config['UPLOAD_FOLDER'], img['filename']))
                except OSError: pass
            if img['processed_filename']:
                try: os.remove(os.path.join(current_app.config['PROCESSED_FOLDER'], img['processed_filename']))
                except OSError: pass

        conn.execute('DELETE FROM questions WHERE session_id = ?', (session_id,))
        conn.execute('DELETE FROM images WHERE session_id = ?', (session_id,))
        conn.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main_bp.route('/toggle_persist/<session_id>', methods=[METHOD_POST])
def toggle_persist(session_id):
    try:
        conn = get_db_connection()
        current_status_res = conn.execute('SELECT persist FROM sessions WHERE id = ?', (session_id,)).fetchone()
        
        if not current_status_res:
            conn.close()
            return jsonify({'error': 'Session not found'}), 404
            
        new_status = 1 - current_status_res['persist']
        conn.execute('UPDATE sessions SET persist = ? WHERE id = ?', (new_status, session_id))
        
        pdf_res = conn.execute('SELECT id FROM generated_pdfs WHERE session_id = ?', (session_id,)).fetchone()
        if pdf_res:
            conn.execute('UPDATE generated_pdfs SET persist = ? WHERE id = ?', (new_status, pdf_res['id']))

        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'status': 'persisted' if new_status == 1 else 'not_persisted'})
    except Exception as e:
        print(f"Error in toggle_persist: {e}")
        conn.rollback()
        conn.close()
        return jsonify({'error': str(e)}), 500

@main_bp.route('/delete_question/<image_id>', methods=[METHOD_DELETE])
def delete_question(image_id):
    try:
        conn = get_db_connection()
        image_info = conn.execute(
            'SELECT session_id, filename, processed_filename FROM images WHERE id = ?', 
            (image_id,)
        ).fetchone()
        
        if not image_info:
            conn.close()
            return jsonify({'error': 'Question not found'}), 404

        conn.execute('DELETE FROM questions WHERE image_id = ?', (image_id,))
        conn.execute('DELETE FROM images WHERE id = ?', (image_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main_bp.route(ROUTE_SAVE_QUESTIONS, methods=[METHOD_POST])
def save_questions():
    data = request.json
    session_id, questions = data['session_id'], data['questions']
    
    conn = get_db_connection()
    conn.execute('DELETE FROM questions WHERE session_id = ?', (session_id,))
    
    questions_to_insert = []
    for q in questions:
        questions_to_insert.append((
            session_id, 
            q['image_id'], 
            q['question_number'], 
            "", 
            q['status'], 
            q['marked_solution'], 
            q['actual_solution'], 
            q.get('time_taken', "")
        ))

    if questions_to_insert:
        conn.executemany(
            """INSERT INTO questions (session_id, image_id, question_number, subject, status, marked_solution, actual_solution, time_taken)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            questions_to_insert
        )
    
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@main_bp.route(ROUTE_EXTRACT_QUESTION_NUMBER, methods=[METHOD_POST])
def extract_question_number():
    if not NVIDIA_NIM_AVAILABLE:
        return jsonify({'error': 'NVIDIA NIM feature is not available. Please set the NVIDIA_API_KEY environment variable.'}), 400
    
    data = request.json
    image_id = data.get('image_id')
    
    if not image_id:
        return jsonify({'error': 'Missing image_id parameter'}), 400
    
    try:
        conn = get_db_connection()
        image_info = conn.execute(
            'SELECT processed_filename FROM images WHERE id = ?', 
            (image_id,)
        ).fetchone()
        conn.close()
        
        if not image_info or not image_info['processed_filename']:
            return jsonify({'error': 'Image not found or not processed'}), 404
            
        image_path = os.path.join(current_app.config['PROCESSED_FOLDER'], image_info['processed_filename'])
        if not os.path.exists(image_path):
            return jsonify({'error': 'Image file not found on disk'}), 404
            
        image_bytes = resize_image_if_needed(image_path)
        ocr_result = call_nim_ocr_api(image_bytes)
        question_number = extract_question_number_from_ocr_result(ocr_result)
        
        return jsonify({
            'success': True, 
            'question_number': question_number,
            'image_id': image_id
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to extract question number: {str(e)}'}), 500

@main_bp.route(ROUTE_EXTRACT_ALL_QUESTION_NUMBERS, methods=[METHOD_POST])
def extract_all_question_numbers():
    if not NVIDIA_NIM_AVAILABLE:
        return jsonify({'error': 'NVIDIA NIM feature is not available.'}), 400
    
    data = request.json
    session_id = data.get('session_id')
    
    if not session_id:
        return jsonify({'error': 'Missing session_id parameter'}), 400
    
    try:
        conn = get_db_connection()
        images = conn.execute(
            "SELECT id, processed_filename FROM images WHERE session_id = ? AND image_type = 'cropped' ORDER BY id", 
            (session_id,)
        ).fetchall()
        conn.close()
        
        if not images:
            return jsonify({'error': 'No cropped images found in session'}), 404
        
        results = []
        errors = []
        
        MAX_CONCURRENT_REQUESTS = 5
        processed_count = 0
        
        for image in images:
            if processed_count >= MAX_CONCURRENT_REQUESTS:
                import time
                time.sleep(1)
                processed_count = 0
            
            try:
                image_id = image['id']
                processed_filename = image['processed_filename']
                
                if not processed_filename:
                    errors.append({'image_id': image_id, 'error': 'Image not processed'})
                    continue
                
                image_path = os.path.join(current_app.config['PROCESSED_FOLDER'], processed_filename)
                if not os.path.exists(image_path):
                    errors.append({'image_id': image_id, 'error': 'Image file not found on disk'})
                    continue
                
                image_bytes = resize_image_if_needed(image_path)
                ocr_result = call_nim_ocr_api(image_bytes)
                question_number = extract_question_number_from_ocr_result(ocr_result)
                
                results.append({
                    'image_id': image_id,
                    'question_number': question_number
                })
                
                processed_count += 1
                
            except Exception as e:
                errors.append({'image_id': image['id'], 'error': str(e)})
        
        return jsonify({
            'success': True,
            'results': results,
            'errors': errors
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to extract question numbers: {str(e)}'}), 500

@main_bp.route('/get_all_subjects_and_tags')
def get_all_subjects_and_tags():
    conn = get_db_connection()
    subjects = [row['subject'] for row in conn.execute('SELECT DISTINCT subject FROM generated_pdfs WHERE subject IS NOT NULL').fetchall()]
    tags_query = conn.execute('SELECT DISTINCT tags FROM generated_pdfs WHERE tags IS NOT NULL AND tags != \'\'').fetchall()
    all_tags = set()
    for row in tags_query:
        tags = [tag.strip() for tag in row['tags'].split(',')]
        all_tags.update(tags)
    conn.close()
    return jsonify({
        'subjects': sorted(subjects),
        'tags': sorted(list(all_tags))
    })

@main_bp.route('/get_metadata_suggestions')
def get_metadata_suggestions():
    conn = get_db_connection()
    subjects = [row['subject'] for row in conn.execute('SELECT DISTINCT subject FROM generated_pdfs WHERE subject IS NOT NULL').fetchall()]
    tags_query = conn.execute('SELECT DISTINCT tags FROM generated_pdfs WHERE tags IS NOT NULL AND tags != \'\'').fetchall()
    all_tags = set()
    for row in tags_query:
        tags = [tag.strip() for tag in row['tags'].split(',')]
        all_tags.update(tags)
    conn.close()
    return jsonify({
        'subjects': sorted(subjects),
        'tags': sorted(list(all_tags))
    })

@main_bp.route('/generate_preview', methods=['POST'])
def generate_preview():
    data = request.json
    session_id = data['session_id']

    conn = get_db_connection()
    query = """
        SELECT q.*, i.filename, i.processed_filename FROM questions q 
        JOIN images i ON q.image_id = i.id
        WHERE q.session_id = ? ORDER BY i.id
    """
    all_questions = [dict(row) for row in conn.execute(query, (session_id,)).fetchall()]
    conn.close()

    miscellaneous_questions = data.get('miscellaneous_questions', [])
    all_questions.extend(miscellaneous_questions)

    filter_type = data.get('filter_type', 'all')
    filtered_questions = [
        q for q in all_questions if filter_type == 'all' or q['status'] == filter_type
    ]

    if not filtered_questions:
        return jsonify({'error': 'No questions match the filter criteria'}), 400

    # For preview, we only need the first page
    images_per_page = int(data.get('images_per_page', 4))
    preview_questions = filtered_questions[:images_per_page]

    practice_mode = data.get('practice_mode', 'none')
    practice_mode_settings = {
        'portrait_2': {'images_per_page': 2, 'orientation': 'portrait', 'grid_rows': 2, 'grid_cols': 1},
        'portrait_3': {'images_per_page': 3, 'orientation': 'portrait', 'grid_rows': 3, 'grid_cols': 1},
        'landscape_2': {'images_per_page': 2, 'orientation': 'landscape', 'grid_rows': 2, 'grid_cols': 1},
        'portrait_2_spacious': {'images_per_page': 2, 'orientation': 'portrait', 'grid_rows': 2, 'grid_cols': 1}
    }

    if practice_mode in practice_mode_settings:
        settings = practice_mode_settings[practice_mode]
        images_per_page = settings['images_per_page']
        orientation = settings['orientation']
        grid_rows = settings['grid_rows']
        grid_cols = settings['grid_cols']
    else:
        images_per_page = int(data.get('images_per_page', 4))
        orientation = data.get('orientation', 'portrait')
        grid_rows = int(data.get('grid_rows')) if data.get('grid_rows') else None
        grid_cols = int(data.get('grid_cols')) if data.get('grid_cols') else None

    pdf_bytes = create_a4_pdf_from_images(
        preview_questions, 
        current_app.config['PROCESSED_FOLDER'], 
        output_filename=None, 
        images_per_page=images_per_page, 
        output_folder=None, 
        orientation=orientation, 
        grid_rows=grid_rows, 
        grid_cols=grid_cols, 
        practice_mode=practice_mode,
        return_bytes=True
    )

    if pdf_bytes:
        # Convert PDF bytes to image for preview
        try:
            pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
            first_page = pdf_document.load_page(0)
            pix = first_page.get_pixmap(dpi=150) # Lower DPI for faster preview
            img_bytes = pix.tobytes("png") # Use tobytes() instead of save()
            
            img_base64 = base64.b64encode(img_bytes).decode('utf-8')
            
            return jsonify({'success': True, 'preview_image': f'data:image/png;base64,{img_base64}'})

        except Exception as e:
            return jsonify({'error': f'Failed to generate preview image: {str(e)}'}), 500
        finally:
            if 'pdf_document' in locals() and pdf_document:
                pdf_document.close()
    else:
        return jsonify({'error': 'PDF generation for preview failed'}), 500

@main_bp.route(ROUTE_GENERATE_PDF, methods=[METHOD_POST])
def generate_pdf():
    data = request.json
    session_id = data['session_id']
    
    conn = get_db_connection()
    query = """
        SELECT q.*, i.filename, i.processed_filename FROM questions q 
        JOIN images i ON q.image_id = i.id
        WHERE q.session_id = ? ORDER BY i.id
    """
    all_questions = [dict(row) for row in conn.execute(query, (session_id,)).fetchall()]
    conn.close()

    miscellaneous_questions = data.get('miscellaneous_questions', [])
    all_questions.extend(miscellaneous_questions)

    filter_type = data.get('filter_type', 'all')
    filtered_questions = [
        q for q in all_questions if filter_type == 'all' or q['status'] == filter_type
    ]

    if not filtered_questions: return jsonify({'error': 'No questions match the filter criteria'}), 400
    
    pdf_filename = f"{secure_filename(data.get('pdf_name', 'analysis'))}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    
    practice_mode = data.get('practice_mode', 'none')
    practice_mode_settings = {
        'portrait_2': {'images_per_page': 2, 'orientation': 'portrait', 'grid_rows': 2, 'grid_cols': 1},
        'portrait_3': {'images_per_page': 3, 'orientation': 'portrait', 'grid_rows': 3, 'grid_cols': 1},
        'landscape_2': {'images_per_page': 2, 'orientation': 'landscape', 'grid_rows': 2, 'grid_cols': 1},
        'portrait_2_spacious': {'images_per_page': 2, 'orientation': 'portrait', 'grid_rows': 2, 'grid_cols': 1}
    }

    if practice_mode in practice_mode_settings:
        settings = practice_mode_settings[practice_mode]
        images_per_page = settings['images_per_page']
        orientation = settings['orientation']
        grid_rows = settings['grid_rows']
        grid_cols = settings['grid_cols']
    else:
        images_per_page = int(data.get('images_per_page', 4))
        orientation = data.get('orientation', 'portrait')
        grid_rows = int(data.get('grid_rows')) if data.get('grid_rows') else None
        grid_cols = int(data.get('grid_cols')) if data.get('grid_cols') else None

    if create_a4_pdf_from_images(filtered_questions, current_app.config['PROCESSED_FOLDER'], pdf_filename, images_per_page, current_app.config['OUTPUT_FOLDER'], orientation, grid_rows, grid_cols, practice_mode):
        conn = get_db_connection()
        session_info = conn.execute('SELECT original_filename FROM sessions WHERE id = ?', (session_id,)).fetchone()
        source_filename = session_info['original_filename'] if session_info else 'Unknown'
        
        conn.execute(
            'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename) VALUES (?, ?, ?, ?, ?, ?)',
            (session_id, pdf_filename, data.get('subject'), data.get('tags'), data.get('notes'), source_filename)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'pdf_filename': pdf_filename})
    else:
        return jsonify({'error': 'PDF generation failed'}), 500

@main_bp.route('/download/<filename>')
def download_file(filename):
    return send_file(os.path.join(current_app.config['OUTPUT_FOLDER'], filename), as_attachment=True)

@main_bp.route('/view_pdf/<filename>')
def view_pdf(filename):
    return send_file(os.path.join(current_app.config['OUTPUT_FOLDER'], filename), as_attachment=False)

@main_bp.route('/image/<folder>/<filename>')
def serve_image(folder, filename):
    folder_path = current_app.config.get(f'{folder.upper()}_FOLDER')
    if not folder_path or not os.path.exists(os.path.join(folder_path, filename)):
        return "Not found", 404
    return send_file(os.path.join(folder_path, filename))

@main_bp.route(ROUTE_INDEX)
def index():
    return render_template('main.html')

@main_bp.route('/pdf_manager')
@main_bp.route('/pdf_manager/browse/<path:folder_path>')
def pdf_manager(folder_path=''):
    conn = get_db_connection()
    view_mode = request.args.get('view', 'default')
    search_query = request.args.get('search', '')
    is_recursive = request.args.get('recursive') == 'true'

    query_params = []
    base_query = 'SELECT * FROM generated_pdfs'
    where_clauses = []

    if search_query:
        where_clauses.append('(subject LIKE ? OR tags LIKE ? OR notes LIKE ?)')
        search_term = f'%{search_query}%'
        query_params.extend([search_term, search_term, search_term])

    all_view = view_mode == 'all'
    folder_id = None
    subfolders = []
    breadcrumbs = []

    if not all_view:
        if folder_path:
            parts = folder_path.split('/')
            parent_id = None
            for i, part in enumerate(parts):
                res = conn.execute("SELECT id FROM folders WHERE name = ? AND (parent_id = ? OR (? IS NULL AND parent_id IS NULL))", (part, parent_id, parent_id)).fetchone()
                if not res:
                    return redirect(url_for('main.pdf_manager'))
                parent_id = res['id']
                breadcrumbs.append({'name': part, 'path': '/'.join(parts[:i+1])})
            folder_id = parent_id

        if is_recursive and search_query:
            if folder_id:
                descendant_ids = get_all_descendant_folder_ids(conn, folder_id)
                all_folder_ids = [folder_id] + descendant_ids
                if all_folder_ids:
                    placeholders = ', '.join('?' * len(all_folder_ids))
                    where_clauses.append(f'folder_id IN ({placeholders})')
                    query_params.extend(all_folder_ids)
        else:
            if folder_id:
                where_clauses.append('folder_id = ?')
                query_params.append(folder_id)
            else:
                where_clauses.append('folder_id IS NULL')

        if folder_id:
            subfolders = conn.execute('SELECT * FROM folders WHERE parent_id = ? ORDER BY name', (folder_id,)).fetchall()
        else:
            subfolders = conn.execute('SELECT * FROM folders WHERE parent_id IS NULL ORDER BY name').fetchall()

    if where_clauses:
        base_query += ' WHERE ' + ' AND '.join(where_clauses)
    
    base_query += ' ORDER BY created_at DESC'
    
    pdfs = conn.execute(base_query, query_params).fetchall()

    folder_tree = get_folder_tree()
    conn.close()
    
    return render_template('pdf_manager.html', 
                           pdfs=[dict(row) for row in pdfs],
                           subfolders=[dict(row) for row in subfolders],
                           current_folder_id=folder_id,
                           breadcrumbs=breadcrumbs,
                           all_view=all_view,
                           folder_tree=folder_tree,
                           search_query=search_query,
                           recursive=is_recursive)

@main_bp.route('/get_pdf_details/<int:pdf_id>')
def get_pdf_details(pdf_id):
    conn = get_db_connection()
    pdf = conn.execute('SELECT * FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
    conn.close()
    if pdf:
        return jsonify(dict(pdf))
    return jsonify({'error': 'PDF not found'}), 404

@main_bp.route('/update_pdf_details/<int:pdf_id>', methods=[METHOD_POST])
def update_pdf_details(pdf_id):
    data = request.json
    try:
        conn = get_db_connection()
        conn.execute(
            'UPDATE generated_pdfs SET subject = ?, tags = ?, notes = ? WHERE id = ?',
            (data.get('subject'), data.get('tags'), data.get('notes'), pdf_id)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main_bp.route('/rename_item', methods=[METHOD_POST])
def rename_item():
    data = request.json
    item_type, item_id, new_name = data.get('item_type'), data.get('item_id'), data.get('new_name')

    if not all([item_type, item_id, new_name]):
        return jsonify({'error': 'Missing parameters'}), 400

    conn = get_db_connection()
    if item_type == 'folder':
        conn.execute('UPDATE folders SET name = ? WHERE id = ?', (new_name, item_id))
    elif item_type == 'pdf':
        pdf_info = conn.execute('SELECT filename FROM generated_pdfs WHERE id = ?', (item_id,)).fetchone()
        if not pdf_info: conn.close(); return jsonify({'error': 'PDF not found'}), 404

        old_filename = pdf_info['filename']
        if not new_name.lower().endswith('.pdf'): new_name += '.pdf'
        new_filename = secure_filename(new_name)

        old_filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], old_filename)
        new_filepath = os.path.join(current_app.config['OUTPUT_FOLDER'], new_filename)

        if os.path.exists(new_filepath): conn.close(); return jsonify({'error': 'A file with this name already exists'}), 400

        try:
            os.rename(old_filepath, new_filepath)
            conn.execute('UPDATE generated_pdfs SET filename = ? WHERE id = ?', (new_filename, item_id))
        except OSError as e:
            conn.close(); return jsonify({'error': f'Failed to rename file on disk: {e}'}), 500
    else:
        conn.close(); return jsonify({'error': 'Invalid item type'}), 400

    conn.commit()
    conn.close()
    return jsonify({'success': True})

@main_bp.route('/delete_folder/<int:folder_id>', methods=[METHOD_DELETE])
def delete_folder(folder_id):
    conn = get_db_connection()
    
    def get_all_child_folders(f_id):
        children = conn.execute('SELECT id FROM folders WHERE parent_id = ?', (f_id,)).fetchall()
        folder_ids = [f['id'] for f in children]
        for child_id in folder_ids:
            folder_ids.extend(get_all_child_folders(child_id))
        return folder_ids

    folder_ids_to_delete = [folder_id] + get_all_child_folders(folder_id)
    placeholders = ', '.join('?' * len(folder_ids_to_delete))
    
    pdfs_to_delete = conn.execute(f'SELECT id, filename FROM generated_pdfs WHERE folder_id IN ({placeholders})', folder_ids_to_delete).fetchall()
    
    for pdf in pdfs_to_delete:
        try: os.remove(os.path.join(current_app.config['OUTPUT_FOLDER'], pdf['filename']))
        except OSError: pass
    
    conn.execute(f'DELETE FROM generated_pdfs WHERE folder_id IN ({placeholders})', folder_ids_to_delete)
    conn.execute(f'DELETE FROM folders WHERE id IN ({placeholders})', folder_ids_to_delete)
    
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@main_bp.route('/delete_generated_pdf/<int:pdf_id>', methods=[METHOD_DELETE])
def delete_generated_pdf(pdf_id):
    try:
        conn = get_db_connection()
        pdf_info = conn.execute('SELECT filename FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
        if pdf_info:
            try: os.remove(os.path.join(current_app.config['OUTPUT_FOLDER'], pdf_info['filename']))
            except OSError: pass
            conn.execute('DELETE FROM generated_pdfs WHERE id = ?', (pdf_id,))
            conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main_bp.route('/toggle_persist_generated_pdf/<int:pdf_id>', methods=[METHOD_POST])
def toggle_persist_generated_pdf(pdf_id):
    try:
        conn = get_db_connection()
        current_status_res = conn.execute('SELECT persist, session_id FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
        
        if not current_status_res: conn.close(); return jsonify({'error': 'PDF not found'}), 404

        new_status = 1 - current_status_res['persist']
        session_id = current_status_res['session_id']

        conn.execute('UPDATE generated_pdfs SET persist = ? WHERE id = ?', (new_status, pdf_id))
        if session_id: conn.execute('UPDATE sessions SET persist = ? WHERE id = ?', (new_status, session_id))

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'status': 'persisted' if new_status == 1 else 'not_persisted'})
    except Exception as e:
        print(f"Error in toggle_persist_generated_pdf: {e}")
        conn.rollback(); conn.close(); return jsonify({'error': str(e)}), 500

@main_bp.route('/bulk_delete_pdfs', methods=[METHOD_POST])
def bulk_delete_pdfs():
    data = request.json
    pdf_ids = data.get('ids', [])
    if not pdf_ids: return jsonify({'error': 'No PDF IDs provided'}), 400
    try:
        conn = get_db_connection()
        for pdf_id in pdf_ids:
            pdf_info = conn.execute('SELECT filename FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
            if pdf_info:
                try: os.remove(os.path.join(current_app.config['OUTPUT_FOLDER'], pdf_info['filename']))
                except OSError: pass
                conn.execute('DELETE FROM generated_pdfs WHERE id = ?', (pdf_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main_bp.route('/bulk_toggle_persist', methods=[METHOD_POST])
def bulk_toggle_persist():
    data = request.json
    pdf_ids = data.get('ids', [])
    if not pdf_ids: return jsonify({'error': 'No PDF IDs provided'}), 400
    try:
        conn = get_db_connection()
        for pdf_id in pdf_ids:
            current_status_res = conn.execute('SELECT persist, session_id FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
            if current_status_res:
                new_status = 1 - current_status_res['persist']
                session_id = current_status_res['session_id']
                
                conn.execute('UPDATE generated_pdfs SET persist = ? WHERE id = ?', (new_status, pdf_id))
                if session_id: conn.execute('UPDATE sessions SET persist = ? WHERE id = ?', (new_status, session_id))

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error in bulk_toggle_persist: {e}")
        conn.rollback(); conn.close(); return jsonify({'error': str(e)}), 500

@main_bp.route('/bulk_download_pdfs', methods=['POST'])
def bulk_download_pdfs():
    data = request.json
    pdf_ids = data.get('ids', [])
    if not pdf_ids: return jsonify({'error': 'No PDF IDs provided'}), 400
    
    memory_file = io.BytesIO()
    
    try:
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            conn = get_db_connection()
            for pdf_id in pdf_ids:
                pdf_info = conn.execute('SELECT filename FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
                if pdf_info:
                    pdf_path = os.path.join(current_app.config['OUTPUT_FOLDER'], pdf_info['filename'])
                    if os.path.exists(pdf_path): zf.write(pdf_path, os.path.basename(pdf_path))
            conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    memory_file.seek(0)
    
    return send_file(memory_file, mimetype='application/zip', as_attachment=True, download_name='pdfs.zip')

@main_bp.route('/create_folder', methods=[METHOD_POST])
def create_folder():
    data = request.json
    name, parent_id = data.get('new_folder_name'), data.get('parent_id')
    if not name: return jsonify({'error': 'Folder name is required'}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO folders (name, parent_id) VALUES (?, ?)", (name, parent_id))
        new_folder_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': new_folder_id, 'name': name, 'parent_id': parent_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main_bp.route('/bulk_move_pdfs', methods=[METHOD_POST])
def bulk_move_pdfs():
    data = request.json
    pdf_ids, target_folder_id = data.get('ids', []), data.get('target_folder_id')
    if not pdf_ids: return jsonify({'error': 'No PDF IDs provided'}), 400

    try:
        conn = get_db_connection()
        placeholders = ', '.join('?' * len(pdf_ids))
        conn.execute(f'UPDATE generated_pdfs SET folder_id = ? WHERE id IN ({placeholders})', (target_folder_id, *pdf_ids))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main_bp.route('/merge_pdfs', methods=['POST'])
def merge_pdfs():
    data = request.json
    pdf_ids = data.get('pdf_ids', [])
    if len(pdf_ids) < 2: return jsonify({'error': 'Please select at least two PDFs to merge.'}), 400

    try:
        conn = get_db_connection()
        safe_pdf_ids = [int(pid) for pid in pdf_ids]
        placeholders = ', '.join('?' * len(safe_pdf_ids))
        query = f"SELECT filename FROM generated_pdfs WHERE id IN ({placeholders}) ORDER BY created_at"
        pdfs_to_merge = conn.execute(query, safe_pdf_ids).fetchall()

        if len(pdfs_to_merge) != len(safe_pdf_ids): conn.close(); return jsonify({'error': 'One or more selected PDFs not found.'}), 404

        merged_doc = fitz.open()
        source_filenames = []
        for pdf_row in pdfs_to_merge:
            filename = pdf_row['filename']
            source_filenames.append(filename)
            pdf_path = os.path.join(current_app.config['OUTPUT_FOLDER'], filename)
            if os.path.exists(pdf_path):
                doc_to_merge = fitz.open(pdf_path)
                merged_doc.insert_pdf(doc_to_merge)
                doc_to_merge.close()
        
        new_filename = f"merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        merged_doc.save(os.path.join(current_app.config['OUTPUT_FOLDER'], new_filename))
        merged_doc.close()

        session_id = str(uuid.uuid4())
        conn.execute('INSERT INTO sessions (id, original_filename) VALUES (?, ?)', (session_id, f"Merged from {len(source_filenames)} files"))

        subject = "Merged Document"
        notes = f"This document was created by merging the following files:\n" + "\n".join(source_filenames)
        
        conn.execute(
            'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename) VALUES (?, ?, ?, ?, ?, ?)',
            (session_id, new_filename, subject, 'merged', notes, ", ".join(source_filenames))
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'new_filename': new_filename})

    except Exception as e:
        if 'conn' in locals() and conn: conn.close()
        print(f"Error merging PDFs: {e}")
        return jsonify({'error': str(e)}), 500

@main_bp.route('/upload_final_pdf')
def upload_final_pdf():
    return render_template('upload_final_pdf.html')

@main_bp.route('/handle_final_pdf_upload', methods=[METHOD_POST])
def handle_final_pdf_upload():
    if 'pdf' not in request.files: return 'No PDF file part', 400
    file = request.files['pdf']
    if file.filename == '': return 'No selected file', 400

    subject = request.form.get('subject')
    if not subject: return 'Subject is required', 400

    if file and file.filename.lower().endswith('.pdf'):
        session_id = str(uuid.uuid4())
        original_filename = secure_filename(file.filename)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO sessions (id, original_filename) VALUES (?, ?)', (session_id, original_filename))
        
        output_filename = f"{session_id}_{original_filename}"
        output_path = os.path.join(current_app.config['OUTPUT_FOLDER'], output_filename)
        file.save(output_path)

        tags, notes = request.form.get('tags'), request.form.get('notes')

        cursor.execute(
            'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename) VALUES (?, ?, ?, ?, ?, ?)',
            (session_id, output_filename, subject, tags, notes, original_filename)
        )
        conn.commit()
        conn.close()
        return redirect(url_for('main.pdf_manager'))
    else:
        return 'Invalid file type', 400

@main_bp.route('/resize/', defaults={'folder_path': ''}, methods=['GET', 'POST'])
@main_bp.route('/resize/browse/<path:folder_path>', methods=['GET', 'POST'])
def resize_pdf_route(folder_path):
    if request.method == 'POST':
        input_pdf_name, output_pdf_name = request.form.get('input_pdf'), request.form.get('output_pdf')
        bg_color_hex, pattern = request.form.get('bg_color', '#FFFFFF'), request.form.get('pattern')
        pattern_color_hex = request.form.get('pattern_color', '#CCCCCC')
        mode, stitch_direction = request.form.get('mode', 'notes_only'), request.form.get('stitch_direction', 'horizontal')
        add_space = 'add_space' in request.form

        if not input_pdf_name or not output_pdf_name: return "Missing input or output PDF name", 400

        def hex_to_rgb(h):
            h = h.lstrip('#')
            return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))

        input_pdf_path = os.path.join(current_app.config['OUTPUT_FOLDER'], input_pdf_name)
        output_pdf_path = os.path.join(current_app.config['OUTPUT_FOLDER'], output_pdf_name)
        bg_color, pattern_color = hex_to_rgb(bg_color_hex), hex_to_rgb(pattern_color_hex)

        try:
            expand_pdf_for_notes(
                input_pdf_path, output_pdf_path, bg_color=bg_color, mode=mode, 
                stitch_direction=stitch_direction, add_space=add_space, 
                pattern=pattern, pattern_color=pattern_color
            )

            conn = get_db_connection()
            session_id = str(uuid.uuid4())
            conn.execute('INSERT INTO sessions (id, original_filename) VALUES (?, ?)', (session_id, f"Resized from {input_pdf_name}"))

            subject = f"Resized - {os.path.basename(input_pdf_name)}"
            notes = f"Resized with options: mode={mode}, stitch_direction={stitch_direction}, add_space={add_space}, bg_color={bg_color_hex}, pattern={pattern}"
            
            conn.execute(
                'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename) VALUES (?, ?, ?, ?, ?, ?)',
                (session_id, output_pdf_name, subject, 'resized', notes, input_pdf_name)
            )
            conn.commit()
            conn.close()

            return redirect(url_for('main.pdf_manager'))
        except Exception as e:
            return f"Error during resizing or database update: {e}", 500

    else:  # GET request
        conn = get_db_connection()
        search_query, is_recursive = request.args.get('search', ''), request.args.get('recursive') == 'true'
        query_params, where_clauses = [], []

        if search_query:
            where_clauses.append('(filename LIKE ? OR subject LIKE ? OR tags LIKE ?)')
            search_term = f'%{search_query}%'
            query_params.extend([search_term, search_term, search_term])

        folder_id, subfolders, breadcrumbs = None, [], []

        if folder_path:
            parts = folder_path.split('/')
            parent_id = None
            for i, part in enumerate(parts):
                res = conn.execute("SELECT id FROM folders WHERE name = ? AND (parent_id = ? OR (? IS NULL AND parent_id IS NULL))", (part, parent_id, parent_id)).fetchone()
                if not res: return redirect(url_for('main.resize_pdf_route'))
                parent_id = res['id']
                breadcrumbs.append({'name': part, 'path': '/'.join(parts[:i+1])})
            folder_id = parent_id

        if is_recursive and search_query:
            if folder_id:
                descendant_ids = get_all_descendant_folder_ids(conn, folder_id)
                all_folder_ids = [folder_id] + descendant_ids
                if all_folder_ids:
                    placeholders = ', '.join('?' * len(all_folder_ids))
                    where_clauses.append(f'folder_id IN ({placeholders})')
                    query_params.extend(all_folder_ids)
        else:
            if folder_id: where_clauses.append('folder_id = ?'); query_params.append(folder_id)
            else: where_clauses.append('folder_id IS NULL')

        if folder_id: subfolders = conn.execute('SELECT * FROM folders WHERE parent_id = ? ORDER BY name', (folder_id,)).fetchall()
        else: subfolders = conn.execute('SELECT * FROM folders WHERE parent_id IS NULL ORDER BY name').fetchall()

        if where_clauses: base_query = 'SELECT * FROM generated_pdfs WHERE ' + ' AND '.join(where_clauses)
        else: base_query = 'SELECT * FROM generated_pdfs'
        
        base_query += ' ORDER BY created_at DESC'
        
        pdfs = conn.execute(base_query, query_params).fetchall()
        folder_tree = get_folder_tree()
        conn.close()

        return render_template('resize.html', pdfs=[dict(row) for row in pdfs], subfolders=[dict(row) for row in subfolders],
                               current_folder_id=folder_id, breadcrumbs=breadcrumbs, folder_tree=folder_tree,
                               search_query=search_query, recursive=is_recursive)

@main_bp.route('/print_pdfs', methods=['POST'])
def print_pdfs():
    pdf_ids = request.form.getlist('pdf_ids')
    if not pdf_ids: return "No PDFs selected", 400

    conn = get_db_connection()
    query = f"SELECT filename, subject FROM generated_pdfs WHERE id IN ({','.join('?' for _ in pdf_ids)})"
    pdfs_info = conn.execute(query, pdf_ids).fetchall()
    conn.close()

    if not pdfs_info: return "No valid PDFs found for the given IDs", 404

    merged_pdf = fitz.open()
    font_path = "arial.ttf"

    for i, pdf_info in enumerate(pdfs_info):
        pdf_path = os.path.join(current_app.config['OUTPUT_FOLDER'], pdf_info['filename'])
        if os.path.exists(pdf_path):
            try:
                doc = fitz.open(pdf_path)
                if len(doc) > 0:
                    first_page = doc[0]
                    rect = first_page.rect
                    text = f"Subject: {pdf_info['subject']}\nFilename: {pdf_info['filename']}"
                    text_rect = fitz.Rect(rect.width * 0.02, 0, rect.width * 0.98, rect.height * 0.05)
                    first_page.insert_textbox(text_rect, text, fontsize=5, fontname="Arial", fontfile=font_path, color=(0, 0, 0), overlay=True, align=fitz.TEXT_ALIGN_CENTER)
                merged_pdf.insert_pdf(doc)
                doc.close()
            except Exception as e:
                print(f"ERROR processing PDF '{pdf_info['filename']}': {e}")
        else:
            print(f"WARNING: PDF file not found at '{pdf_path}'")

    output_stream = io.BytesIO()
    merged_pdf.save(output_stream)
    merged_pdf.close()
    output_stream.seek(0)

    return send_file(output_stream, mimetype='application/pdf', as_attachment=False, download_name='printed_documents.pdf')

@main_bp.route('/redact_status/<session_id>')
def redact_status(session_id):
    return render_template('redact_status.html', session_id=session_id)

@main_bp.route('/redaction_stream/<session_id>')
def redaction_stream(session_id):
    def generate():
        if not NVIDIA_NIM_AVAILABLE:
            yield f"data: {json.dumps({'error': 'NVIDIA API Key is not configured.'})}\n\n"; return

        conn = get_db_connection()
        images = conn.execute("SELECT id, filename FROM images WHERE session_id = ? AND image_type = 'original' ORDER BY image_index", (session_id,)).fetchall()
        
        if not images: conn.close(); yield f"data: {json.dumps({'error': 'No images found for this session.'})}\n\n"; return

        redacted_image_paths, source_filenames_for_notes = [], []
        total_images = len(images)

        try:
            for i, image_row in enumerate(images):
                progress = int(((i + 1) / total_images) * 100)
                yield f"data: {json.dumps({'progress': progress, 'message': f'Redacting page {i + 1} of {total_images}...'})}\n\n"
                
                original_filename = image_row['filename']
                source_filenames_for_notes.append(original_filename)
                original_path = os.path.join(current_app.config['UPLOAD_FOLDER'], original_filename)

                if not os.path.exists(original_path): continue

                redacted_image = redact_pictures_in_image(original_path, NVIDIA_API_KEY)
                
                processed_filename = f"redacted_{original_filename}"
                processed_path = os.path.join(current_app.config['PROCESSED_FOLDER'], processed_filename)
                redacted_image.save(processed_path, 'PNG')
                redacted_image_paths.append(processed_path)

                conn.execute("UPDATE images SET processed_filename = ? WHERE id = ?", (processed_filename, image_row['id']))
                conn.commit()

            yield f"data: {json.dumps({'progress': 100, 'message': 'Assembling final PDF...'})}\n\n"
            final_pdf_filename = f"redacted_document_{session_id}.pdf"
            final_pdf_path = os.path.join(current_app.config['OUTPUT_FOLDER'], final_pdf_filename)

            if not create_pdf_from_full_images(redacted_image_paths, final_pdf_path): raise Exception("Failed to create the final PDF.")

            session_info = conn.execute('SELECT original_filename FROM sessions WHERE id = ?', (session_id,)).fetchone()
            subject = f"Redacted - {session_info['original_filename'] if session_info else 'Document'}"
            notes = f"This document was automatically redacted."
            
            conn.execute(
                'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename) VALUES (?, ?, ?, ?, ?, ?)',
                (session_id, final_pdf_filename, subject, 'redacted', notes, ", ".join(source_filenames_for_notes))
            )
            conn.commit()

            download_url = url_for('main.download_file', filename=final_pdf_filename)
            yield f"data: {json.dumps({'complete': True, 'download_url': download_url})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            conn.close()

    return Response(generate(), mimetype='text/event-stream')
