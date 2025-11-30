
import os
import uuid
import base64
import io
import zipfile
import threading
import copy
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, jsonify, current_app, url_for, send_from_directory, send_file, redirect
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
import shlex
import fitz
from urllib.parse import urlparse
import requests
import cv2
import numpy as np

from database import get_folder_tree, get_all_descendant_folder_ids
from processing import (
    resize_image_if_needed,
    call_nim_ocr_api,
    extract_question_number_from_ocr_result,
    crop_image_perspective,
    create_pdf_from_full_images,
    remove_color_from_image
)

from strings import *
from utils import get_db_connection, create_a4_pdf_from_images
from redact import redact_pictures_in_image
from resize import expand_pdf_for_notes

# Global dictionary to store async processing status
# Key: session_id, Value: {'status': 'processing'|'completed'|'error', 'progress': int, 'total': int, 'message': str}
upload_progress = {}

main_bp = Blueprint('main', __name__)

@main_bp.route('/upload_progress/<session_id>')
@login_required
def get_upload_progress(session_id):
    status = upload_progress.get(session_id)
    if not status:
        # Check if session exists in DB (maybe it finished and server restarted, or we missed it)
        conn = get_db_connection()
        exists = conn.execute('SELECT id FROM sessions WHERE id = ?', (session_id,)).fetchone()
        conn.close()
        if exists:
            return jsonify({'status': 'completed', 'progress': 100})
        return jsonify({'error': 'Session not found or processing not started'}), 404
    return jsonify(status)

def process_pdf_background(session_id, user_id, original_filename, pdf_content, app_config):
    """Background task to process PDF splitting."""
    upload_progress[session_id] = {'status': 'processing', 'progress': 0, 'message': 'Starting...'}
    
    try:
        # We need to manually create a connection since we are in a thread
        # And we can't use current_app context directly if not carefully managed, 
        # but we passed app_config to reconstruct paths.
        # Database connection needs to be fresh.
        
        conn = get_db_connection() # This creates a new connection
        
        pdf_filename = f"{session_id}_{original_filename}"
        pdf_path = os.path.join(app_config['UPLOAD_FOLDER'], pdf_filename)
        
        with open(pdf_path, 'wb') as f:
            f.write(pdf_content)
            
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        upload_progress[session_id]['total'] = total_pages
        
        # Fetch user DPI - we need to query it since current_user proxy might not work in thread
        user_row = conn.execute("SELECT dpi FROM users WHERE id = ?", (user_id,)).fetchone()
        dpi = user_row['dpi'] if user_row else 150
        
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            page_filename = f"{session_id}_page_{i}.png"
            page_path = os.path.join(app_config['UPLOAD_FOLDER'], page_filename)
            pix.save(page_path)
            
            conn.execute(
                'INSERT INTO images (session_id, image_index, filename, original_name, image_type) VALUES (?, ?, ?, ?, ?)',
                (session_id, i, page_filename, f"Page {i+1}", 'original')
            )
            
            # Update progress
            progress = int(((i + 1) / total_pages) * 100)
            upload_progress[session_id].update({'progress': progress, 'message': f'Processed page {i+1}/{total_pages}'})
            
        conn.commit()
        conn.close()
        doc.close()
        
        upload_progress[session_id] = {'status': 'completed', 'progress': 100, 'message': 'Done'}
        
    except Exception as e:
        print(f"Async processing error: {e}")
        upload_progress[session_id] = {'status': 'error', 'message': str(e)}
        if 'conn' in locals(): conn.close()

# ... existing imports ...

@main_bp.route('/process_color_rm_batch', methods=['POST'])
@login_required
def process_color_rm_batch():
    data = request.json
    session_id = data.get('session_id')
    target_colors = data.get('colors', [])
    threshold = data.get('threshold', 0.8)
    bg_mode = data.get('bg_mode', 'black')
    region_box = data.get('region', None)

    conn = get_db_connection()
    
    # Check ownership
    session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
    if not session_owner or session_owner['user_id'] != current_user.id:
        conn.close()
        return jsonify({'error': 'Unauthorized'}), 403

    # Get all original images
    original_images = conn.execute(
        "SELECT * FROM images WHERE session_id = ? AND image_type = 'original' ORDER BY image_index",
        (session_id,)
    ).fetchall()

    processed_count = 0
    
    try:
        for img in original_images:
            original_path = os.path.join(current_app.config['UPLOAD_FOLDER'], img['filename'])
            
            if not os.path.exists(original_path):
                continue
                
            # Process
            processed_img_cv = remove_color_from_image(original_path, target_colors, threshold, bg_mode, region_box)
            
            # Save
            processed_filename = f"color_rm_{session_id}_{img['image_index']}_{datetime.now().strftime('%H%M%S')}.png"
            processed_path = os.path.join(current_app.config['PROCESSED_FOLDER'], processed_filename)
            cv2.imwrite(processed_path, processed_img_cv)
            
            # Update DB (upsert logic roughly)
            # Check if exists first
            existing = conn.execute(
                "SELECT id FROM images WHERE session_id = ? AND image_index = ? AND image_type = 'color_rm'",
                (session_id, img['image_index'])
            ).fetchone()
            
            if existing:
                conn.execute(
                    "UPDATE images SET processed_filename = ?, filename = ? WHERE id = ?",
                    (processed_filename, img['filename'], existing['id'])
                )
            else:
                conn.execute(
                    'INSERT INTO images (session_id, image_index, filename, original_name, processed_filename, image_type) VALUES (?, ?, ?, ?, ?, ?)',
                    (session_id, img['image_index'], img['filename'], img['original_name'], processed_filename, 'color_rm')
                )
            processed_count += 1
            
        conn.commit()
        return jsonify({'success': True, 'count': processed_count})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@main_bp.route('/generate_color_rm_pdf/<session_id>')
@login_required
def generate_color_rm_pdf(session_id):
    conn = get_db_connection()
    
    # Check ownership
    session_data = conn.execute('SELECT user_id, original_filename FROM sessions WHERE id = ?', (session_id,)).fetchone()
    if not session_data or session_data['user_id'] != current_user.id:
        conn.close()
        return jsonify({'error': 'Unauthorized'}), 403
        
    # Range filtering
    start_page = request.args.get('start', type=int)
    end_page = request.args.get('end', type=int)
    
    query = "SELECT * FROM images WHERE session_id = ? AND image_type = 'original'"
    params = [session_id]
    
    if start_page:
        query += " AND image_index >= ?"
        params.append(start_page - 1) # 0-based index
    if end_page:
        query += " AND image_index <= ?"
        params.append(end_page - 1)
        
    query += " ORDER BY image_index"
        
    images = conn.execute(query, params).fetchall()
    
    pdf_image_paths = []
    
    for img in images:
        # Check for processed version
        processed = conn.execute(
            "SELECT processed_filename FROM images WHERE session_id = ? AND image_index = ? AND image_type = 'color_rm'",
            (session_id, img['image_index'])
        ).fetchone()
        
        if processed and processed['processed_filename']:
            path = os.path.join(current_app.config['PROCESSED_FOLDER'], processed['processed_filename'])
        else:
            # Fallback to original
            path = os.path.join(current_app.config['UPLOAD_FOLDER'], img['filename'])
            
        if os.path.exists(path):
            pdf_image_paths.append(path)
            
    if not pdf_image_paths:
        conn.close()
        return "No images found to generate PDF", 404
        
    # Generate PDF
    range_suffix = ""
    if start_page or end_page:
        range_suffix = f"_pg{start_page or 1}-{end_page or 'end'}"
        
    pdf_filename = f"Color_Removed_{session_data['original_filename']}{range_suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    if not pdf_filename.lower().endswith('.pdf'): pdf_filename += ".pdf"
    
    output_path = os.path.join(current_app.config['OUTPUT_FOLDER'], pdf_filename)
    
    # Use user's preferred color_rm_dpi for PDF resolution
    pdf_resolution = current_user.color_rm_dpi if hasattr(current_user, 'color_rm_dpi') else 200.0

    success = create_pdf_from_full_images(pdf_image_paths, output_path, resolution=pdf_resolution)
    
    if success:
        conn.execute(
            'INSERT INTO generated_pdfs (session_id, filename, subject, user_id) VALUES (?, ?, ?, ?)',
            (session_id, pdf_filename, "Color Removal Export", current_user.id)
        )
        conn.commit()
        conn.close()
        return redirect(url_for('main.pdf_manager')) # Redirect to manager or download directly
    else:
        conn.close()
        return "Failed to generate PDF", 500

@main_bp.route('/tmp/<path:filename>')
@login_required  # Should still protect temp files if they are user-specific
def serve_tmp_file(filename):
    # In a real multi-user scenario, you'd check if this temp file belongs to the user
    return send_from_directory(current_app.config['TEMP_FOLDER'], filename)

@main_bp.route('/processed/<path:filename>')
@login_required
def serve_processed_file(filename):
    # This is a critical security change. Before serving a processed file,
    # we must check if it belongs to the current user.
    conn = get_db_connection()
    image_owner = conn.execute(
        "SELECT s.user_id FROM images i JOIN sessions s ON i.session_id = s.id WHERE i.processed_filename = ?",
        (filename,)
    ).fetchone()
    conn.close()

    if image_owner and image_owner['user_id'] == current_user.id:
        return send_from_directory(current_app.config['PROCESSED_FOLDER'], filename)
    else:
        return "Unauthorized", 403


NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_NIM_AVAILABLE = bool(NVIDIA_API_KEY)

@main_bp.route('/process_final_pdf/<int:pdf_id>')
@login_required
def process_final_pdf(pdf_id):
    conn = get_db_connection()
    # Security: Check if the PDF belongs to the current user
    pdf_info = conn.execute('SELECT filename FROM generated_pdfs WHERE id = ? AND user_id = ?', (pdf_id, current_user.id)).fetchone()
    
    if not pdf_info:
        conn.close()
        flash("PDF not found or you don't have permission to access it.", "warning")
        return redirect(url_for('main.index_v2')) 

    original_filename = pdf_info['filename']
    pdf_path = os.path.join(current_app.config['OUTPUT_FOLDER'], original_filename)

    if not os.path.exists(pdf_path):
        conn.close()
        flash("PDF file is missing from disk.", "danger")
        return redirect(url_for('main.index_v2'))

    session_id = str(uuid.uuid4())
    
    # Associate new session with the current user
    conn.execute('INSERT INTO sessions (id, original_filename, user_id) VALUES (?, ?, ?)', (session_id, original_filename, current_user.id))
    
    doc = fitz.open(pdf_path)
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=current_user.dpi)
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
@login_required
def index_v2():
    conn = get_db_connection()
    pdfs = conn.execute('SELECT id, filename, subject, tags, notes, persist FROM generated_pdfs WHERE user_id = ? ORDER BY created_at DESC', (current_user.id,)).fetchall()
    conn.close()
    return render_template('indexv2.html', pdfs=[dict(row) for row in pdfs])

def _parse_curl_command(command):
    current_app.logger.info(f"Parsing cURL command: {command}")
    try:
        parts = shlex.split(command)
    except ValueError as e:
        current_app.logger.error(f"shlex splitting failed for command: '{command}'. Error: {e}")
        # Fallback to simple split for commands that might not be perfectly quoted
        parts = command.split()
        
    current_app.logger.info(f"Command parts: {parts}")
    url, output_filename = None, None
    
    # First, try to find the output filename if -o is present
    try:
        if '-o' in parts:
            o_index = parts.index('-o')
            if o_index + 1 < len(parts):
                output_filename = parts[o_index + 1] # shlex handles quotes
    except ValueError:
        pass # -o not found, handled below

    # Then, find the URL (always starts with http)
    for part in parts:
        if part.startswith('http'):
            url = part
            break
    
    # If URL found but no output filename was specified with -o, derive from URL
    if url and not output_filename:
        output_filename = os.path.basename(urlparse(url).path)
        if not output_filename: # Fallback if path is empty (e.g., http://example.com)
            output_filename = "downloaded_pdf.pdf"
        if not output_filename.lower().endswith('.pdf'):
            output_filename += '.pdf' # Ensure it has a .pdf extension

    current_app.logger.info(f"Parsed URL: {url}, Filename: {output_filename}")
    return url, output_filename

@main_bp.route('/v2/upload', methods=['POST'])
@login_required
def v2_upload():
    session_id = str(uuid.uuid4())
    pdf_content, original_filename = None, None

    try:
        # Case 1: Direct file upload
        if 'pdf' in request.files and request.files['pdf'].filename:
            file = request.files['pdf']
            if file and file.filename.lower().endswith('.pdf'):
                original_filename = secure_filename(file.filename)
                pdf_content = file.read()
            else:
                return jsonify({'error': 'Invalid file type, please upload a PDF'}), 400

        # Case 2: URL upload
        elif 'pdf_url' in request.form and request.form['pdf_url']:
            pdf_url = request.form['pdf_url']
            response = requests.get(pdf_url, allow_redirects=True)
            response.raise_for_status()
            original_filename = os.path.basename(urlparse(pdf_url).path)
            if not original_filename.lower().endswith('.pdf'):
                original_filename += '.pdf'
            pdf_content = response.content

        # Case 3: cURL command upload
        elif 'curl_command' in request.form and request.form['curl_command']:
            # For simplicity, we handle one cURL command at a time for the analysis workflow
            command = request.form['curl_command'].strip().split('\n')[0]
            url, filename = _parse_curl_command(command)
            if not url or not filename:
                return jsonify({'error': f"Could not parse cURL command: {command}"}), 400
            
            response = requests.get(url, allow_redirects=True)
            response.raise_for_status()
            original_filename = filename
            pdf_content = response.content

        else:
            return jsonify({'error': 'No PDF file, URL, or cURL command provided'}), 400

        if not pdf_content or not original_filename:
            return jsonify({'error': 'Failed to retrieve PDF content or filename'}), 500

        session_type = request.form.get('type', 'standard')
        conn = get_db_connection()
        conn.execute('INSERT INTO sessions (id, original_filename, name, user_id, session_type) VALUES (?, ?, ?, ?, ?)', (session_id, original_filename, original_filename, current_user.id, session_type))
        conn.commit() # Commit session creation first
        conn.close()

        # Check for async request
        is_async = request.args.get('async') == 'true'
        
        if is_async:
            # Start background thread
            # We pass app config copy to be safe
            app_config = current_app.config.copy()
            thread = threading.Thread(target=process_pdf_background, args=(session_id, current_user.id, original_filename, pdf_content, app_config))
            thread.start()
            
            return jsonify({'session_id': session_id, 'status': 'processing'})

        # --- Sync processing logic ---
        # Re-open connection for sync processing
        conn = get_db_connection()
        
        pdf_filename = f"{session_id}_{original_filename}"
        pdf_path = os.path.join(current_app.config['UPLOAD_FOLDER'], pdf_filename)
        with open(pdf_path, 'wb') as f:
            f.write(pdf_content)

        doc = fitz.open(pdf_path)
        page_files = []
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=current_user.dpi)
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

    except requests.RequestException as e:
        return jsonify({'error': f"Failed to download PDF from URL: {e}"}), 500
    except Exception as e:
        # Ensure connection is closed on error
        if 'conn' in locals() and conn:
            conn.rollback()
            conn.close()
        current_app.logger.error(f"An error occurred during v2 upload: {e}")
        return jsonify({'error': "An internal error occurred while processing the PDF."}), 500


@main_bp.route(ROUTE_IMAGES)
@login_required
def image_upload():
    return render_template('image_upload.html')

@main_bp.route(ROUTE_UPLOAD_IMAGES, methods=[METHOD_POST])
@login_required
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

    session_type = request.form.get('type', 'standard')
    conn = get_db_connection()
    original_filename = f"{len(files)} images" if len(files) > 1 else secure_filename(files[0].filename) if files else "images"
    conn.execute('INSERT INTO sessions (id, original_filename, name, user_id, session_type) VALUES (?, ?, ?, ?, ?)', (session_id, original_filename, original_filename, current_user.id, session_type))
    
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

@main_bp.route('/api/session_images/<session_id>')
@login_required
def get_session_images(session_id):
    conn = get_db_connection()
    
    # Security check
    session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
    if not session_owner or session_owner['user_id'] != current_user.id:
        conn.close()
        return jsonify({'error': 'Unauthorized'}), 403

    # Get all original images
    originals = conn.execute(
        "SELECT image_index, filename FROM images WHERE session_id = ? AND image_type = 'original' ORDER BY image_index",
        (session_id,)
    ).fetchall()
    
    # Get processed status
    processed = conn.execute(
        "SELECT image_index, processed_filename FROM images WHERE session_id = ? AND image_type = 'color_rm'",
        (session_id,)
    ).fetchall()
    
    processed_map = {row['image_index']: row['processed_filename'] for row in processed}
    
    images_list = []
    for img in originals:
        idx = img['image_index']
        p_filename = processed_map.get(idx)
        
        images_list.append({
            'index': idx,
            'page_number': idx + 1,
            'original_url': url_for('main.serve_image', folder='uploads', filename=img['filename']),
            'processed_url': url_for('main.serve_processed_file', filename=p_filename) if p_filename else None,
            'is_processed': bool(p_filename)
        })
        
    conn.close()
    return jsonify({'images': images_list})

@main_bp.route('/cropv2/<session_id>/<int:image_index>')
@login_required
def crop_interface_v2(session_id, image_index):
    conn = get_db_connection()
    
    # Security: Check ownership of the session
    session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
    if not session_owner or session_owner['user_id'] != current_user.id:
        conn.close()
        return "Unauthorized", 403

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
        user_id=current_user.id,  # Pass user ID to template
        image_index=image_index,
        image_info=dict(image_info),
        total_pages=total_pages
    )

@main_bp.route(ROUTE_PROCESS_CROP_V2, methods=[METHOD_POST])
@login_required
def process_crop_v2():
    data = request.json
    session_id, page_index, boxes_data, image_data_url = data['session_id'], data['image_index'], data['boxes'], data.get('imageData')

    conn = get_db_connection()

    # Security: Check ownership of the session
    session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
    if not session_owner or session_owner['user_id'] != current_user.id:
        conn.close()
        return jsonify({'error': 'Unauthorized'}), 403

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

        # Identify boxes on the current page that are acting as sources for other boxes
        # This prevents them from being saved as standalone questions if they are merged
        local_source_ids = set()
        for box in boxes_data:
            if box.get('remote_stitch_source'):
                src = box['remote_stitch_source']
                if src.get('page_index') == page_index:
                    # The source box is on this page. Add its ID to the ignore list.
                    # Note: src['box'] might handle ID as string or int, ensure consistency if needed
                    local_source_ids.add(src['box']['id'])

        primary_boxes = [box for box in boxes_data if not box.get('stitch_to')]
        processed_boxes = []

        for i, primary_box in enumerate(primary_boxes):
            # Skip if this box is being consumed by another box on the same page
            if primary_box['id'] in local_source_ids:
                continue

            # --- CROSS-PAGE STITCHING LOGIC ---
            if primary_box.get('remote_stitch_source'):
                source_info = primary_box['remote_stitch_source']
                source_page_index = source_info['page_index']
                source_box = source_info['box']
                
                # Fetch source page filename
                source_page_db = conn.execute(
                    "SELECT filename FROM images WHERE session_id = ? AND image_index = ? AND image_type = 'original'",
                    (session_id, source_page_index)
                ).fetchone()
                
                if source_page_db:
                    source_filename = source_page_db['filename']
                    source_path = os.path.join(current_app.config['UPLOAD_FOLDER'], source_filename)
                    
                    if os.path.exists(source_path):
                        # Crop Source (Parent)
                        src_points = [
                            {'x': source_box['x'], 'y': source_box['y']},
                            {'x': source_box['x'] + source_box['w'], 'y': source_box['y']},
                            {'x': source_box['x'] + source_box['w'], 'y': source_box['y'] + source_box['h']},
                            {'x': source_box['x'], 'y': source_box['y'] + source_box['h']}
                        ]
                        # We use the original source file for the parent crop
                        parent_crop = crop_image_perspective(source_path, src_points)
                        
                        # Crop Current (Child)
                        child_points = [
                            {'x': primary_box['x'], 'y': primary_box['y']},
                            {'x': primary_box['x'] + primary_box['w'], 'y': primary_box['y']},
                            {'x': primary_box['x'] + primary_box['w'], 'y': primary_box['y'] + primary_box['h']},
                            {'x': primary_box['x'], 'y': primary_box['y'] + primary_box['h']}
                        ]
                        child_crop = crop_image_perspective(temp_path, child_points)
                        
                        # Stitch (Parent Top, Child Bottom)
                        h1, w1 = parent_crop.shape[:2]
                        h2, w2 = child_crop.shape[:2]
                        max_width = max(w1, w2)
                        
                        stitched_image = np.full((h1 + h2, max_width, 3), 255, dtype=np.uint8)
                        
                        x_offset1 = (max_width - w1) // 2
                        stitched_image[0:h1, x_offset1:x_offset1 + w1] = parent_crop
                        
                        x_offset2 = (max_width - w2) // 2
                        stitched_image[h1:h1 + h2, x_offset2:x_offset2 + w2] = child_crop
                    else:
                        # Fallback if source file missing
                        current_app.logger.error(f"Source file missing for stitch: {source_path}")
                        # Just crop the child
                        points = [
                            {'x': primary_box['x'], 'y': primary_box['y']},
                            {'x': primary_box['x'] + primary_box['w'], 'y': primary_box['y']},
                            {'x': primary_box['x'] + primary_box['w'], 'y': primary_box['y'] + primary_box['h']},
                            {'x': primary_box['x'], 'y': primary_box['y'] + primary_box['h']}
                        ]
                        stitched_image = crop_image_perspective(temp_path, points)
                else:
                     # Fallback if db lookup fails
                    current_app.logger.error(f"Source page DB record missing: session {session_id} index {source_page_index}")
                    points = [
                        {'x': primary_box['x'], 'y': primary_box['y']},
                        {'x': primary_box['x'] + primary_box['w'], 'y': primary_box['y']},
                        {'x': primary_box['x'] + primary_box['w'], 'y': primary_box['y'] + primary_box['h']},
                        {'x': primary_box['x'], 'y': primary_box['y'] + primary_box['h']}
                    ]
                    stitched_image = crop_image_perspective(temp_path, points)

            # --- STANDARD LOCAL STITCHING OR SINGLE BOX LOGIC ---
            else:
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

@main_bp.route('/color_rm')
@login_required
def color_rm_entry():
    return render_template('color_rm_upload.html')

@main_bp.route('/color_rm_interface/<session_id>/<int:image_index>')
@login_required
def color_rm_interface(session_id, image_index):
    conn = get_db_connection()
    
    # Security: Check ownership of the session
    session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
    if not session_owner or session_owner['user_id'] != current_user.id:
        conn.close()
        return "Unauthorized", 403

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
        'color_rm.html',
        session_id=session_id,
        user_id=current_user.id,  # Pass user ID to template
        image_index=image_index,
        image_info=dict(image_info),
        total_pages=total_pages
    )

@main_bp.route('/process_color_rm', methods=['POST'])
@login_required
def process_color_rm():
    data = request.json
    session_id = data.get('session_id')
    image_index = data.get('image_index')
    image_data_url = data.get('imageData')

    conn = get_db_connection()
    
    # Security: Check ownership of the session
    session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
    if not session_owner or session_owner['user_id'] != current_user.id:
        conn.close()
        return jsonify({'error': 'Unauthorized'}), 403
    
    page_info = conn.execute(
        "SELECT filename, original_name FROM images WHERE session_id = ? AND image_index = ? AND image_type = 'original'", 
        (session_id, image_index)
    ).fetchone()

    if not page_info:
        conn.close()
        return jsonify({'error': 'Original page not found'}), 404

    try:
        header, encoded = image_data_url.split(",", 1)
        image_data = base64.b64decode(encoded)
        
        processed_filename = f"color_rm_{session_id}_{image_index}_{datetime.now().strftime('%H%M%S')}.png"
        processed_path = os.path.join(current_app.config['PROCESSED_FOLDER'], processed_filename)
        
        with open(processed_path, "wb") as f:
            f.write(image_data)
            
        # Insert into DB so serve_processed_file allows access
        conn.execute(
            'INSERT INTO images (session_id, image_index, filename, original_name, processed_filename, image_type) VALUES (?, ?, ?, ?, ?, ?)',
            (session_id, image_index, page_info['filename'], page_info['original_name'], processed_filename, 'color_rm')
        )
        conn.commit()
        
        return jsonify({
            'success': True, 
            'filename': processed_filename, 
            'url': url_for('main.serve_processed_file', filename=processed_filename)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@main_bp.route('/question_entry_v2/<session_id>')
@login_required
def question_entry_v2(session_id):
    conn = get_db_connection()

    # Fetch session metadata, ensuring it belongs to the current user
    session_data = conn.execute(
        'SELECT original_filename, subject, tags, notes FROM sessions WHERE id = ? AND user_id = ?', (session_id, current_user.id)
    ).fetchone()

    if not session_data:
        conn.close()
        flash("Session not found or you don't have permission to access it.", "warning")
        return redirect(url_for('dashboard.dashboard'))

    # Fetch images and associated questions
    images = conn.execute(
        """SELECT i.id, i.processed_filename, q.question_number, q.status, q.marked_solution, q.actual_solution
           FROM images i
           LEFT JOIN questions q ON i.id = q.image_id
           WHERE i.session_id = ? AND i.image_type = 'cropped'
           ORDER BY i.id""",
        (session_id,)
    ).fetchall()

    # Count classified questions (those with both subject and chapter)
    classification_count = conn.execute(
        """SELECT COUNT(*) as count
           FROM images i
           LEFT JOIN questions q ON i.id = q.image_id
           WHERE i.session_id = ? AND i.image_type = 'cropped'
           AND q.subject IS NOT NULL AND q.chapter IS NOT NULL""",
        (session_id,)
    ).fetchone()['count']

    classified_count = classification_count

    conn.close()

    if not images:
        return "No questions were created from the PDF. Please go back and draw crop boxes.", 404

    return render_template('question_entry_v2.html',
                          session_id=session_id,
                          images=[dict(img) for img in images],
                          session_data=dict(session_data) if session_data else {},
                          classified_count=classified_count,
                          total_questions=len(images),
                          nvidia_nim_available=NVIDIA_NIM_AVAILABLE)

@main_bp.route('/old/dashboard')
@login_required
def old_dashboard():
    # Redirect to the main dashboard to avoid duplicate code
    return redirect(url_for('dashboard.dashboard'))

@main_bp.route('/delete_session/<session_id>', methods=[METHOD_DELETE])
@login_required
def delete_session(session_id):
    try:
        conn = get_db_connection()
        # Security: Check ownership of the session
        session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
        if not session_owner or session_owner['user_id'] != current_user.id:
            conn.close()
            return jsonify({'error': 'Unauthorized'}), 403

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
@login_required
def toggle_persist(session_id):
    try:
        conn = get_db_connection()
        # Security: Check ownership of the session
        session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
        if not session_owner or session_owner['user_id'] != current_user.id:
            conn.close()
            return jsonify({'error': 'Unauthorized'}), 403

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

@main_bp.route('/rename_session/<session_id>', methods=['POST'])
@login_required
def rename_session(session_id):
    data = request.json
    new_name = data.get('new_name')

    if not new_name:
        return jsonify({'error': 'New name is required'}), 400

    try:
        conn = get_db_connection()
        # Security: Check ownership of the session
        session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
        if not session_owner or session_owner['user_id'] != current_user.id:
            conn.close()
            return jsonify({'error': 'Unauthorized'}), 403

        conn.execute('UPDATE sessions SET name = ? WHERE id = ?', (new_name, session_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main_bp.route('/delete_question/<image_id>', methods=[METHOD_DELETE])
@login_required
def delete_question(image_id):
    try:
        conn = get_db_connection()
        # Security: Check ownership of the image via the session
        image_owner = conn.execute("""
            SELECT s.user_id FROM images i
            JOIN sessions s ON i.session_id = s.id
            WHERE i.id = ?
        """, (image_id,)).fetchone()

        if not image_owner or image_owner['user_id'] != current_user.id:
            conn.close()
            return jsonify({'error': 'Unauthorized'}), 403

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

from rich.table import Table
from rich.console import Console

@main_bp.route(ROUTE_SAVE_QUESTIONS, methods=[METHOD_POST])
@login_required
def save_questions():
    data = request.json
    session_id = data['session_id']
    questions = data['questions']
    pdf_subject = data.get('pdf_subject', '')
    pdf_tags = data.get('pdf_tags', '')
    pdf_notes = data.get('pdf_notes', '')

    conn = get_db_connection()
    # Security: Check ownership of the session
    session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
    if not session_owner or session_owner['user_id'] != current_user.id:
        conn.close()
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Update session metadata
    conn.execute(
        'UPDATE sessions SET subject = ?, tags = ?, notes = ? WHERE id = ?',
        (pdf_subject, pdf_tags, pdf_notes, session_id)
    )

    # Delete and re-insert questions
    conn.execute('DELETE FROM questions WHERE session_id = ?', (session_id,))
    
    questions_to_insert = []
    for q in questions:
        questions_to_insert.append((
            session_id, 
            q['image_id'], 
            q['question_number'], 
            "", # subject column in questions table - can be removed later
            q['status'], 
            q.get('marked_solution', ""), 
            q.get('actual_solution', ""), 
            q.get('time_taken', ""),
            pdf_tags # Save tags with each question too
        ))
    
    conn.executemany(
        'INSERT INTO questions (session_id, image_id, question_number, subject, status, marked_solution, actual_solution, time_taken, tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        questions_to_insert
    )
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Questions saved successfully.'})

@main_bp.route(ROUTE_EXTRACT_QUESTION_NUMBER, methods=[METHOD_POST])
@login_required
def extract_question_number():
    if not NVIDIA_NIM_AVAILABLE:
        return jsonify({'error': 'NVIDIA NIM feature is not available. Please set the NVIDIA_API_KEY environment variable.'}), 400
    
    data = request.json
    image_id = data.get('image_id')
    
    if not image_id:
        return jsonify({'error': 'Missing image_id parameter'}), 400
    
    try:
        conn = get_db_connection()
        # Security: Check ownership of the image via the session
        image_owner = conn.execute("""
            SELECT s.user_id FROM images i
            JOIN sessions s ON i.session_id = s.id
            WHERE i.id = ?
        """, (image_id,)).fetchone()

        if not image_owner or image_owner['user_id'] != current_user.id:
            conn.close()
            return jsonify({'error': 'Unauthorized'}), 403

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
@login_required
def extract_all_question_numbers():
    if not NVIDIA_NIM_AVAILABLE:
        return jsonify({'error': 'NVIDIA NIM feature is not available.'}), 400
    
    data = request.json
    session_id = data.get('session_id')
    
    if not session_id:
        return jsonify({'error': 'Missing session_id parameter'}), 400
    
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
@login_required
def get_all_subjects_and_tags():
    conn = get_db_connection()
    subjects = [row['subject'] for row in conn.execute('SELECT DISTINCT subject FROM generated_pdfs WHERE subject IS NOT NULL AND user_id = ?', (current_user.id,)).fetchall()]
    tags_query = conn.execute('SELECT DISTINCT tags FROM generated_pdfs WHERE tags IS NOT NULL AND tags != \'\' AND user_id = ?', (current_user.id,)).fetchall()
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
@login_required
def get_metadata_suggestions():
    conn = get_db_connection()
    subjects = [row['subject'] for row in conn.execute('SELECT DISTINCT subject FROM generated_pdfs WHERE subject IS NOT NULL AND user_id = ?', (current_user.id,)).fetchall()]
    tags_query = conn.execute('SELECT DISTINCT tags FROM generated_pdfs WHERE tags IS NOT NULL AND tags != \'\' AND user_id = ?', (current_user.id,)).fetchall()
    all_tags = set()
    for row in tags_query:
        tags = [tag.strip() for tag in row['tags'].split(',')]
        all_tags.update(tags)
    conn.close()
    return jsonify({
        'subjects': sorted(subjects),
        'tags': sorted(list(all_tags))
    })

@main_bp.route('/generate_preview', methods=[METHOD_POST])
@login_required
def generate_preview():
    data = request.json
    session_id = data['session_id']

    conn = get_db_connection()
    # Security: Check ownership of the session
    session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
    if not session_owner or session_owner['user_id'] != current_user.id:
        conn.close()
        return jsonify({'error': 'Unauthorized'}), 403

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
@login_required
def generate_pdf():
    data = request.json
    session_id = data['session_id']
    
    conn = get_db_connection()
    # Security: Check ownership of the session
    session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
    if not session_owner or session_owner['user_id'] != current_user.id:
        conn.close()
        return jsonify({'error': 'Unauthorized'}), 403

    query = """
        SELECT q.*, i.filename, i.processed_filename FROM questions q 
        JOIN images i ON q.image_id = i.id
        WHERE q.session_id = ? ORDER BY i.id
    """
    all_questions = [dict(row) for row in conn.execute(query, (session_id,)).fetchall()]
    
    miscellaneous_questions = data.get('miscellaneous_questions', [])
    all_questions.extend(miscellaneous_questions)

    filter_type = data.get('filter_type', 'all')
    filtered_questions = [
        q for q in all_questions if filter_type == 'all' or q['status'] == filter_type
    ]

    if not filtered_questions:
        conn.close()
        return jsonify({'error': 'No questions match the filter criteria'}), 400
    
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
        session_info = conn.execute('SELECT original_filename FROM sessions WHERE id = ?', (session_id,)).fetchone()
        source_filename = session_info['original_filename'] if session_info else 'Unknown'
        
        conn.execute(
            'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename, user_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (session_id, pdf_filename, data.get('subject'), data.get('tags'), data.get('notes'), source_filename, current_user.id)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'pdf_filename': pdf_filename})
    else:
        conn.close()
        return jsonify({'error': 'PDF generation failed'}), 500

@main_bp.route('/download/<filename>')
def download_file(filename):
    return send_file(os.path.join(current_app.config['OUTPUT_FOLDER'], filename), as_attachment=True)

@main_bp.route('/view_pdf/<filename>')
def view_pdf(filename):
    return send_file(os.path.join(current_app.config['OUTPUT_FOLDER'], filename), as_attachment=False)

@main_bp.route('/viewpdflegacy/<int:pdf_id>')
def view_pdf_legacy(pdf_id):
    conn = get_db_connection()
    pdf_info = conn.execute('SELECT filename, subject FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
    conn.close()

    if not pdf_info:
        return "PDF not found", 404

    pdf_filename = pdf_info['filename']
    pdf_subject = pdf_info['subject']
    pdf_path = os.path.join(current_app.config['OUTPUT_FOLDER'], pdf_filename)

    if not os.path.exists(pdf_path):
        return "PDF file not found on disk", 404

    image_paths = []
    try:
        doc = fitz.open(pdf_path)
        for i in range(0, doc.page_count, 2):
            # Get first page
            page1 = doc.load_page(i)
            pix1 = page1.get_pixmap(dpi=current_user.dpi)

            # Check for second page
            if i + 1 < doc.page_count:
                page2 = doc.load_page(i + 1)
                pix2 = page2.get_pixmap(dpi=current_user.dpi)

                # Convert pixmaps to numpy arrays for easier manipulation
                img1 = np.frombuffer(pix1.samples, dtype=np.uint8).reshape(pix1.h, pix1.w, pix1.n)
                img2 = np.frombuffer(pix2.samples, dtype=np.uint8).reshape(pix2.h, pix2.w, pix2.n)

                # Ensure both images have 3 channels (RGB) for consistent stacking
                if img1.shape[2] == 4: # RGBA
                    img1 = cv2.cvtColor(img1, cv2.COLOR_RGBA2RGB)
                if img2.shape[2] == 4: # RGBA
                    img2 = cv2.cvtColor(img2, cv2.COLOR_RGBA2RGB)

                # Pad images to have the same height if necessary
                max_h = max(img1.shape[0], img2.shape[0])
                if img1.shape[0] < max_h:
                    img1 = np.pad(img1, ((0, max_h - img1.shape[0]), (0, 0), (0, 0)), mode='constant', constant_values=255)
                if img2.shape[0] < max_h:
                    img2 = np.pad(img2, ((0, max_h - img2.shape[0]), (0, 0), (0, 0)), mode='constant', constant_values=255)

                # Combine images horizontally
                combined_img = np.hstack((img1, img2))

                # Convert back to pixmap for saving
                combined_pix = fitz.Pixmap(fitz.csRGB, combined_img.shape[1], combined_img.shape[0], combined_img.tobytes())
            else:
                # Only one page, use pix1 directly
                combined_pix = pix1

            temp_image_filename = f"legacy_view_{uuid.uuid4()}_page_{i}_{i+1}.png"
            temp_image_path = os.path.join(current_app.config['PROCESSED_FOLDER'], temp_image_filename)
            combined_pix.save(temp_image_path)
            image_paths.append(url_for('main.serve_image', folder='processed', filename=temp_image_filename))
        doc.close()
    except Exception as e:
        print(f"Error converting PDF to images: {e}")
        return f"Error processing PDF: {str(e)}", 500

    return render_template('simple_viewer.html', image_urls=image_paths, pdf_title=pdf_subject or pdf_filename)

@main_bp.route('/image/<folder>/<filename>')
def serve_image(folder, filename):
    # Map common URL folder names to config keys
    folder_map = {
        'uploads': 'UPLOAD_FOLDER',
        'processed': 'PROCESSED_FOLDER',
        'output': 'OUTPUT_FOLDER'
    }
    
    config_key = folder_map.get(folder)
    if not config_key:
        # Fallback to direct uppercase match (legacy behavior)
        config_key = f'{folder.upper()}_FOLDER'
        
    folder_path = current_app.config.get(config_key)
    
    if not folder_path or not os.path.exists(os.path.join(folder_path, filename)):
        return "Not found", 404
    return send_file(os.path.join(folder_path, filename))

@main_bp.route(ROUTE_INDEX)
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.dashboard'))
    return redirect(url_for('auth.login'))

@main_bp.route('/pdf_manager')
@main_bp.route('/pdf_manager/browse/<path:folder_path>')
@login_required
def pdf_manager(folder_path=''):
    conn = get_db_connection()
    view_mode = request.args.get('view', 'default')
    search_query = request.args.get('search', '')
    is_recursive = request.args.get('recursive') == 'true'

    query_params = [current_user.id]
    base_query = 'SELECT * FROM generated_pdfs WHERE user_id = ?'
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
                res = conn.execute("SELECT id FROM folders WHERE name = ? AND user_id = ? AND (parent_id = ? OR (? IS NULL AND parent_id IS NULL))", (part, current_user.id, parent_id, parent_id)).fetchone()
                if not res:
                    return redirect(url_for('main.pdf_manager'))
                parent_id = res['id']
                breadcrumbs.append({'name': part, 'path': '/'.join(parts[:i+1])})
            folder_id = parent_id

        if is_recursive and search_query:
            if folder_id:
                # Note: get_all_descendant_folder_ids needs to be made user-aware if folders can be nested deeply
                # For now, we assume it gets all children regardless of user, but the main query is user-filtered.
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
            subfolders = conn.execute('SELECT * FROM folders WHERE parent_id = ? AND user_id = ? ORDER BY name', (folder_id, current_user.id)).fetchall()
        else:
            subfolders = conn.execute('SELECT * FROM folders WHERE parent_id IS NULL AND user_id = ? ORDER BY name', (current_user.id,)).fetchall()

    if where_clauses:
        base_query += ' AND ' + ' AND '.join(where_clauses)
    
    base_query += ' ORDER BY created_at DESC'
    
    pdfs = conn.execute(base_query, query_params).fetchall()
    
    pdfs_list = [dict(row) for row in pdfs]
    subfolders_list = [dict(row) for row in subfolders]

    for pdf in pdfs_list:
        if isinstance(pdf['created_at'], str):
            try:
                pdf['created_at'] = datetime.strptime(pdf['created_at'], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass 

    for folder in subfolders_list:
        if isinstance(folder['created_at'], str):
            try:
                folder['created_at'] = datetime.strptime(folder['created_at'], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass

    # get_folder_tree also needs to be user-aware
    folder_tree = get_folder_tree(user_id=current_user.id)
    conn.close()
    
    return render_template('pdf_manager.html', 
                           pdfs=pdfs_list,
                           subfolders=subfolders_list,
                           current_folder_id=folder_id,
                           breadcrumbs=breadcrumbs,
                           all_view=all_view,
                           folder_tree=folder_tree,
                           search_query=search_query,
                           recursive=is_recursive)

@main_bp.route('/get_pdf_details/<int:pdf_id>')
@login_required
def get_pdf_details(pdf_id):
    conn = get_db_connection()
    pdf = conn.execute('SELECT * FROM generated_pdfs WHERE id = ? AND user_id = ?', (pdf_id, current_user.id)).fetchone()
    conn.close()
    if pdf:
        return jsonify(dict(pdf))
    return jsonify({'error': 'PDF not found'}), 404

@main_bp.route('/update_pdf_details/<int:pdf_id>', methods=[METHOD_POST])
@login_required
def update_pdf_details(pdf_id):
    data = request.json
    try:
        conn = get_db_connection()
        pdf_owner = conn.execute('SELECT user_id FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
        if not pdf_owner or pdf_owner['user_id'] != current_user.id:
            conn.close()
            return jsonify({'error': 'Unauthorized'}), 403

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
@login_required
def rename_item():
    data = request.json
    item_type, item_id, new_name = data.get('item_type'), data.get('item_id'), data.get('new_name')

    if not all([item_type, item_id, new_name]):
        return jsonify({'error': 'Missing parameters'}), 400

    conn = get_db_connection()
    if item_type == 'folder':
        folder_owner = conn.execute('SELECT user_id FROM folders WHERE id = ?', (item_id,)).fetchone()
        if not folder_owner or folder_owner['user_id'] != current_user.id:
            conn.close(); return jsonify({'error': 'Unauthorized'}), 403
        conn.execute('UPDATE folders SET name = ? WHERE id = ?', (new_name, item_id))
    elif item_type == 'pdf':
        pdf_owner = conn.execute('SELECT user_id, filename FROM generated_pdfs WHERE id = ?', (item_id,)).fetchone()
        if not pdf_owner or pdf_owner['user_id'] != current_user.id:
            conn.close(); return jsonify({'error': 'Unauthorized'}), 403

        old_filename = pdf_owner['filename']
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
@login_required
def delete_folder(folder_id):
    conn = get_db_connection()
    folder_owner = conn.execute('SELECT user_id FROM folders WHERE id = ?', (folder_id,)).fetchone()
    if not folder_owner or folder_owner['user_id'] != current_user.id:
        conn.close(); return jsonify({'error': 'Unauthorized'}), 403
    
    folder_ids_to_delete = [folder_id] + get_all_descendant_folder_ids(conn, folder_id, current_user.id)
    placeholders = ', '.join('?' * len(folder_ids_to_delete))
    
    pdfs_to_delete = conn.execute(f'SELECT id, filename FROM generated_pdfs WHERE folder_id IN ({placeholders}) AND user_id = ?', (*folder_ids_to_delete, current_user.id)).fetchall()
    
    for pdf in pdfs_to_delete:
        try: os.remove(os.path.join(current_app.config['OUTPUT_FOLDER'], pdf['filename']))
        except OSError: pass
    
    if pdfs_to_delete:
        conn.execute(f'DELETE FROM generated_pdfs WHERE id IN ({','.join(map(str, [p['id'] for p in pdfs_to_delete]))})')
    
    conn.execute(f'DELETE FROM folders WHERE id IN ({placeholders})', folder_ids_to_delete)
    
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@main_bp.route('/delete_generated_pdf/<int:pdf_id>', methods=[METHOD_DELETE])
@login_required
def delete_generated_pdf(pdf_id):
    try:
        conn = get_db_connection()
        pdf_info = conn.execute('SELECT filename, user_id FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
        if pdf_info and pdf_info['user_id'] == current_user.id:
            try: os.remove(os.path.join(current_app.config['OUTPUT_FOLDER'], pdf_info['filename']))
            except OSError: pass
            conn.execute('DELETE FROM generated_pdfs WHERE id = ?', (pdf_id,))
            conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main_bp.route('/toggle_persist_generated_pdf/<int:pdf_id>', methods=[METHOD_POST])
@login_required
def toggle_persist_generated_pdf(pdf_id):
    try:
        conn = get_db_connection()
        pdf_info = conn.execute('SELECT persist, session_id, user_id FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
        
        if not pdf_info or pdf_info['user_id'] != current_user.id:
            conn.close(); return jsonify({'error': 'Unauthorized'}), 403

        new_status = 1 - pdf_info['persist']
        session_id = pdf_info['session_id']

        conn.execute('UPDATE generated_pdfs SET persist = ? WHERE id = ?', (new_status, pdf_id))
        if session_id:
            session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
            if session_owner and session_owner['user_id'] == current_user.id:
                conn.execute('UPDATE sessions SET persist = ? WHERE id = ?', (new_status, session_id))

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'status': 'persisted' if new_status == 1 else 'not_persisted'})
    except Exception as e:
        print(f"Error in toggle_persist_generated_pdf: {e}")
        conn.rollback(); conn.close(); return jsonify({'error': str(e)}), 500

@main_bp.route('/bulk_delete_pdfs', methods=[METHOD_POST])
@login_required
def bulk_delete_pdfs():
    data = request.json
    pdf_ids = data.get('ids', [])
    if not pdf_ids: return jsonify({'error': 'No PDF IDs provided'}), 400
    try:
        conn = get_db_connection()
        placeholders = ','.join('?' for _ in pdf_ids)
        owned_pdfs = conn.execute(f'SELECT id, filename FROM generated_pdfs WHERE id IN ({placeholders}) AND user_id = ?', (*pdf_ids, current_user.id)).fetchall()
        
        owned_pdf_ids = [pdf['id'] for pdf in owned_pdfs]
        if not owned_pdf_ids:
            conn.close()
            return jsonify({'success': True, 'message': 'No owned PDFs to delete.'})

        for pdf in owned_pdfs:
            try: os.remove(os.path.join(current_app.config['OUTPUT_FOLDER'], pdf['filename']))
            except OSError: pass
        
        delete_placeholders = ','.join('?' for _ in owned_pdf_ids)
        conn.execute(f'DELETE FROM generated_pdfs WHERE id IN ({delete_placeholders})', owned_pdf_ids)
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main_bp.route('/bulk_toggle_persist', methods=[METHOD_POST])
@login_required
def bulk_toggle_persist():
    data = request.json
    pdf_ids = data.get('ids', [])
    if not pdf_ids: return jsonify({'error': 'No PDF IDs provided'}), 400
    try:
        conn = get_db_connection()
        placeholders = ','.join('?' for _ in pdf_ids)
        owned_pdfs = conn.execute(f'SELECT id, persist, session_id FROM generated_pdfs WHERE id IN ({placeholders}) AND user_id = ?', (*pdf_ids, current_user.id)).fetchall()

        for pdf in owned_pdfs:
            new_status = 1 - pdf['persist']
            session_id = pdf['session_id']
            conn.execute('UPDATE generated_pdfs SET persist = ? WHERE id = ?', (new_status, pdf['id']))
            if session_id:
                conn.execute('UPDATE sessions SET persist = ? WHERE id = ? AND user_id = ?', (new_status, session_id, current_user.id))

        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error in bulk_toggle_persist: {e}")
        conn.rollback(); conn.close(); return jsonify({'error': str(e)}), 500

@main_bp.route('/bulk_download_pdfs', methods=[METHOD_POST])
@login_required
def bulk_download_pdfs():
    data = request.json
    pdf_ids = data.get('ids', [])
    if not pdf_ids: return jsonify({'error': 'No PDF IDs provided'}), 400
    
    memory_file = io.BytesIO()
    
    try:
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            conn = get_db_connection()
            placeholders = ','.join('?' for _ in pdf_ids)
            owned_pdfs = conn.execute(f'SELECT filename FROM generated_pdfs WHERE id IN ({placeholders}) AND user_id = ?', (*pdf_ids, current_user.id)).fetchall()
            for pdf_info in owned_pdfs:
                pdf_path = os.path.join(current_app.config['OUTPUT_FOLDER'], pdf_info['filename'])
                if os.path.exists(pdf_path): zf.write(pdf_path, os.path.basename(pdf_path))
            conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    memory_file.seek(0)
    
    return send_file(memory_file, mimetype='application/zip', as_attachment=True, download_name='pdfs.zip')

@main_bp.route('/create_folder', methods=[METHOD_POST])
@login_required
def create_folder():
    data = request.json
    name, parent_id = data.get('new_folder_name'), data.get('parent_id')
    if not name: return jsonify({'error': 'Folder name is required'}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO folders (name, parent_id, user_id) VALUES (?, ?, ?)", (name, parent_id, current_user.id))
        new_folder_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': new_folder_id, 'name': name, 'parent_id': parent_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main_bp.route('/bulk_move_pdfs', methods=[METHOD_POST])
@login_required
def bulk_move_pdfs():
    data = request.json
    pdf_ids, target_folder_id = data.get('ids', []), data.get('target_folder_id')
    if not pdf_ids: return jsonify({'error': 'No PDF IDs provided'}), 400

    try:
        conn = get_db_connection()
        if target_folder_id:
            folder_owner = conn.execute('SELECT user_id FROM folders WHERE id = ?', (target_folder_id,)).fetchone()
            if not folder_owner or folder_owner['user_id'] != current_user.id:
                conn.close(); return jsonify({'error': 'Unauthorized target folder'}), 403
        
        placeholders = ', '.join('?' * len(pdf_ids))
        conn.execute(f'UPDATE generated_pdfs SET folder_id = ? WHERE id IN ({placeholders}) AND user_id = ?', (target_folder_id, *pdf_ids, current_user.id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main_bp.route('/merge_pdfs', methods=[METHOD_POST])
@login_required
def merge_pdfs():
    data = request.json
    pdf_ids = data.get('pdf_ids', [])
    if len(pdf_ids) < 2: return jsonify({'error': 'Please select at least two PDFs to merge.'}), 400

    try:
        conn = get_db_connection()
        safe_pdf_ids = [int(pid) for pid in pdf_ids]
        placeholders = ', '.join('?' * len(safe_pdf_ids))
        query = f"SELECT filename FROM generated_pdfs WHERE id IN ({placeholders}) AND user_id = ?"
        pdfs_to_merge = conn.execute(query, (*safe_pdf_ids, current_user.id)).fetchall()

        if len(pdfs_to_merge) != len(safe_pdf_ids):
             conn.close(); return jsonify({'error': 'One or more selected PDFs not found or are unauthorized.'}), 404

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
        conn.execute('INSERT INTO sessions (id, original_filename, user_id) VALUES (?, ?, ?)', (session_id, f"Merged from {len(source_filenames)} files", current_user.id))

        subject = "Merged Document"
        notes = f"This document was created by merging the following files:\n" + "\n".join(source_filenames)
        
        conn.execute(
            'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename, user_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (session_id, new_filename, subject, 'merged', notes, ", ".join(source_filenames), current_user.id)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'new_filename': new_filename})

    except Exception as e:
        if 'conn' in locals() and conn: conn.close()
        print(f"Error merging PDFs: {e}")
        return jsonify({'error': str(e)}), 500

@main_bp.route('/upload_final_pdf')
@login_required
def upload_final_pdf():
    return render_template('upload_final_pdf.html')


@main_bp.route('/handle_final_pdf_upload', methods=[METHOD_POST])
@login_required
def handle_final_pdf_upload():
    subject = request.form.get('subject')
    if not subject: return 'Subject is required', 400

    tags, notes = request.form.get('tags'), request.form.get('notes')
    conn = get_db_connection()
    
    def process_and_save_pdf(file_content, original_filename):
        session_id = str(uuid.uuid4())
        # Associate session with user
        conn.execute('INSERT INTO sessions (id, original_filename, user_id) VALUES (?, ?, ?)', 
                     (session_id, original_filename, current_user.id))

        secure_name = secure_filename(original_filename)
        output_filename = f"{session_id}_{secure_name}"
        output_path = os.path.join(current_app.config['OUTPUT_FOLDER'], output_filename)

        with open(output_path, 'wb') as f:
            f.write(file_content)

        # Associate generated PDF with user
        conn.execute(
            'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename, user_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (session_id, output_filename, subject, tags, notes, original_filename, current_user.id)
        )
        conn.commit()

    try:
        if 'pdf' in request.files and request.files['pdf'].filename:
            file = request.files['pdf']
            if file and file.filename.lower().endswith('.pdf'):
                process_and_save_pdf(file.read(), file.filename)
            else:
                return 'Invalid file type', 400

        elif 'pdf_url' in request.form and request.form['pdf_url']:
            pdf_url = request.form['pdf_url']
            response = requests.get(pdf_url, allow_redirects=True)
            response.raise_for_status()
            original_filename = os.path.basename(urlparse(pdf_url).path)
            if not original_filename.lower().endswith('.pdf'):
                original_filename += '.pdf'
            process_and_save_pdf(response.content, original_filename)

        elif 'curl_command' in request.form and request.form['curl_command']:
            curl_input = request.form['curl_command'].strip().replace('\n', ',')
            curl_commands = [cmd.strip() for cmd in curl_input.split(',') if cmd.strip()]
            
            for command in curl_commands:
                url, filename = _parse_curl_command(command)
                if not url or not filename:
                    current_app.logger.warning(f"Could not parse cURL command: {command}")
                    continue
                
                response = requests.get(url, allow_redirects=True)
                response.raise_for_status()
                process_and_save_pdf(response.content, filename)
        
        else:
            return 'No PDF file, URL, or cURL command provided', 400

    except requests.RequestException as e:
        current_app.logger.error(f"Failed to download PDF from URL: {e}")
        return f"Failed to download PDF: {e}", 500
    except Exception as e:
        current_app.logger.error(f"An error occurred during PDF upload: {e}")
        return "An internal error occurred.", 500
    finally:
        conn.close()

    return redirect(url_for('main.pdf_manager'))

@main_bp.route('/resize/', defaults={'folder_path': ''}, methods=['GET', 'POST'])
@main_bp.route('/resize/browse/<path:folder_path>', methods=['GET', 'POST'])
@login_required
def resize_pdf_route(folder_path):
    if request.method == 'POST':
        input_pdf_name, output_pdf_name = request.form.get('input_pdf'), request.form.get('output_pdf')
        bg_color_hex, pattern = request.form.get('bg_color', '#FFFFFF'), request.form.get('pattern')
        pattern_color_hex = request.form.get('pattern_color', '#CCCCCC')
        mode, stitch_direction = request.form.get('mode', 'notes_only'), request.form.get('stitch_direction', 'horizontal')
        add_space = 'add_space' in request.form

        if not input_pdf_name or not output_pdf_name: return "Missing input or output PDF name", 400
        
        conn = get_db_connection()
        pdf_owner = conn.execute('SELECT user_id FROM generated_pdfs WHERE filename = ?', (input_pdf_name,)).fetchone()
        if not pdf_owner or pdf_owner['user_id'] != current_user.id:
            conn.close()
            return "Unauthorized", 403

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

            session_id = str(uuid.uuid4())
            conn.execute('INSERT INTO sessions (id, original_filename, user_id) VALUES (?, ?, ?)', (session_id, f"Resized from {input_pdf_name}", current_user.id))

            subject = f"Resized - {os.path.basename(input_pdf_name)}"
            notes = f"Resized with options: mode={mode}, stitch_direction={stitch_direction}, add_space={add_space}, bg_color={bg_color_hex}, pattern={pattern}"
            
            conn.execute(
                'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename, user_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (session_id, output_pdf_name, subject, 'resized', notes, input_pdf_name, current_user.id)
            )
            conn.commit()
            conn.close()

            return redirect(url_for('main.pdf_manager'))
        except Exception as e:
            conn.close()
            return f"Error during resizing or database update: {e}", 500

    else:  # GET request
        conn = get_db_connection()
        search_query, is_recursive = request.args.get('search', ''), request.args.get('recursive') == 'true'
        query_params, where_clauses = [current_user.id], ['user_id = ?']

        if search_query:
            where_clauses.append('(filename LIKE ? OR subject LIKE ? OR tags LIKE ?)')
            search_term = f'%{search_query}%'
            query_params.extend([search_term, search_term, search_term])

        folder_id, subfolders, breadcrumbs = None, [], []

        if folder_path:
            parts = folder_path.split('/')
            parent_id = None
            for i, part in enumerate(parts):
                res = conn.execute("SELECT id FROM folders WHERE name = ? AND user_id = ? AND (parent_id = ? OR (? IS NULL AND parent_id IS NULL))", (part, current_user.id, parent_id, parent_id)).fetchone()
                if not res: return redirect(url_for('main.resize_pdf_route'))
                parent_id = res['id']
                breadcrumbs.append({'name': part, 'path': '/'.join(parts[:i+1])})
            folder_id = parent_id

        if is_recursive and search_query:
            if folder_id:
                descendant_ids = get_all_descendant_folder_ids(conn, folder_id, current_user.id)
                all_folder_ids = [folder_id] + descendant_ids
                if all_folder_ids:
                    placeholders = ', '.join('?' * len(all_folder_ids))
                    where_clauses.append(f'folder_id IN ({placeholders})')
                    query_params.extend(all_folder_ids)
        else:
            if folder_id: where_clauses.append('folder_id = ?'); query_params.append(folder_id)
            else: where_clauses.append('folder_id IS NULL')

        if folder_id:
            subfolders = conn.execute('SELECT * FROM folders WHERE parent_id = ? AND user_id = ? ORDER BY name', (folder_id, current_user.id)).fetchall()
        else:
            subfolders = conn.execute('SELECT * FROM folders WHERE parent_id IS NULL AND user_id = ? ORDER BY name', (current_user.id,)).fetchall()

        base_query = 'SELECT * FROM generated_pdfs WHERE ' + ' AND '.join(where_clauses)
        base_query += ' ORDER BY created_at DESC'
        
        pdfs = conn.execute(base_query, query_params).fetchall()
        folder_tree = get_folder_tree(user_id=current_user.id)
        conn.close()

        return render_template('resize.html', pdfs=[dict(row) for row in pdfs], subfolders=[dict(row) for row in subfolders],
                               current_folder_id=folder_id, breadcrumbs=breadcrumbs, folder_tree=folder_tree,
                               search_query=search_query, recursive=is_recursive)

@main_bp.route('/print_pdfs', methods=['POST'])
@login_required
def print_pdfs():
    pdf_ids = request.form.getlist('pdf_ids')
    current_app.logger.info(f"User {current_user.id} printing PDFs with IDs: {pdf_ids}")
    if not pdf_ids:
        return jsonify({'error': 'No PDFs selected'}), 400

    conn = get_db_connection()
    placeholders = ','.join('?' for _ in pdf_ids)
    query = f"SELECT filename, subject FROM generated_pdfs WHERE id IN ({placeholders}) AND user_id = ?"
    pdfs_info = conn.execute(query, (*pdf_ids, current_user.id)).fetchall()
    conn.close()

    current_app.logger.info(f"Found {len(pdfs_info)} owned PDFs to print.")
    if not pdfs_info:
        return jsonify({'error': 'No valid PDFs found for the given IDs'}), 404

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
                current_app.logger.error(f"ERROR processing PDF '{pdf_info['filename']}': {e}")
        else:
            current_app.logger.warning(f"PDF file not found at '{pdf_path}'")

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    temp_filename = f'printed_documents_{timestamp}.pdf'
    temp_filepath = os.path.join(current_app.config['TEMP_FOLDER'], temp_filename)
    
    os.makedirs(current_app.config['TEMP_FOLDER'], exist_ok=True)
    merged_pdf.save(temp_filepath)
    merged_pdf.close()

    return jsonify({'success': True, 'url': url_for('main.view_generated_pdf', filename=temp_filename)})

@main_bp.route('/view_generated_pdf/<filename>')
def view_generated_pdf(filename):
    """Serves a generated PDF from the temporary folder."""
    safe_filename = secure_filename(filename)
    filepath = os.path.join(current_app.config['TEMP_FOLDER'], safe_filename)
    if not os.path.exists(filepath):
        return "File not found.", 404
    return send_file(filepath, mimetype='application/pdf', as_attachment=False)

@main_bp.route('/redact_status/<session_id>')
@login_required
def redact_status(session_id):
    conn = get_db_connection()
    session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
    conn.close()
    if not session_owner or session_owner['user_id'] != current_user.id:
        return "Unauthorized", 403
    return render_template('redact_status.html', session_id=session_id)

@main_bp.route('/redaction_stream/<session_id>')
@login_required
def redaction_stream(session_id):
    def generate():
        conn = get_db_connection()
        session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
        if not session_owner or session_owner['user_id'] != current_user.id:
            conn.close()
            yield f"data: {json.dumps({'error': 'Unauthorized'})}\n\n"
            return

        if not NVIDIA_NIM_AVAILABLE:
            yield f"data: {json.dumps({'error': 'NVIDIA API Key is not configured.'})}\n\n"; return

        images = conn.execute("SELECT id, filename FROM images WHERE session_id = ? AND image_type = 'original' ORDER BY image_index", (session_id,)).fetchall()
        
        if not images: 
            conn.close()
            yield f"data: {json.dumps({'error': 'No images found for this session.'})}\n\n"
            return

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
                'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename, user_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (session_id, final_pdf_filename, subject, 'redacted', notes, ", ".join(source_filenames_for_notes), current_user.id)
            )
            conn.commit()

            download_url = url_for('main.download_file', filename=final_pdf_filename)
            yield f"data: {json.dumps({'complete': True, 'download_url': download_url})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            conn.close()

    return Response(generate(), mimetype='text/event-stream')

@main_bp.route('/chart')
@login_required
def chart():
    conn = get_db_connection()

    total_sessions = conn.execute('SELECT COUNT(*) FROM sessions WHERE user_id = ?', (current_user.id,)).fetchone()[0]
    total_pdfs = conn.execute('SELECT COUNT(*) FROM generated_pdfs WHERE user_id = ?', (current_user.id,)).fetchone()[0]
    
    total_questions = conn.execute("""
        SELECT COUNT(q.id) FROM questions q
        JOIN sessions s ON q.session_id = s.id
        WHERE s.user_id = ?
    """, (current_user.id,)).fetchone()[0]

    total_classified_questions = conn.execute("""
        SELECT COUNT(q.id) FROM questions q
        JOIN sessions s ON q.session_id = s.id
        WHERE s.user_id = ? AND q.subject IS NOT NULL AND q.chapter IS NOT NULL
    """, (current_user.id,)).fetchone()[0]

    conn.close()

    return render_template('chart.html', 
                           total_sessions=total_sessions,
                           total_pdfs=total_pdfs,
                           total_questions=total_questions,
                           total_classified_questions=total_classified_questions)

