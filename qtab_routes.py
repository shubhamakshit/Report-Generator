from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app, send_from_directory
from flask_login import login_required, current_user
from database import get_db_connection, get_qtab_folder_tree
from werkzeug.utils import secure_filename
import json
import os
import base64
from datetime import datetime, timezone
from google import genai
from google.genai import types
import logging

qtab_bp = Blueprint('qtab', __name__)

logger = logging.getLogger(__name__)

def extract_json_from_response(response_text):
    """Extract JSON from Gemini response, handling code blocks."""
    try:
        if "```json" in response_text:
            json_text = response_text.split("```json")[1].split("```")[0]
        else:
            json_text = response_text
        return json.loads(json_text)
    except Exception as e:
        logger.error(f"JSON extraction error: {str(e)}")
        return {
            "status": "error",
            "message": "Failed to parse response JSON",
            "error": str(e)
        }

def process_image_for_questions(image_path, username):
    """Process an image to extract question-answer pairs using Gemini."""
    try:
        current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Read image file
        with open(image_path, 'rb') as f:
            image_bytes = f.read()
        
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        # Initialize GenAI client
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {
                "status": "error",
                "message": "GEMINI_API_KEY not configured"
            }
            
        client = genai.Client(api_key=api_key)
        model = "gemini-2.0-flash-lite"

        prompt = f"""Current Date and Time (UTC): {current_time}
Current User's Login: {username}

You are given an image file. Your task is to:

    Extract question numbers and their corresponding answers, forming pairs.

    Group them under appropriate sections, such as "Question Paper 1", "Section A", "Part B", etc., if such headers are present in the image.

    If no sections are present, just list the question–answer pairs normally.

    If the image is faulty, unclear, or does not contain extractable question-answer data, return a clear error in JSON.

    Do not output anything except a valid JSON object.

Output Format:

If sections are detected (try your level best to do so):

{{
  "status": "success",
  "data": {{
    "Section 1": [
      {{"question_number": "1", "answer": "B"}},
      {{"question_number": "2", "answer": "C"}}
    ],
    "Section 2": [
      {{"question_number": "1", "answer": "A"}},
      {{"question_number": "2", "answer": "D"}}
    ]
  }}
}}

If no sections are detected:

{{
  "status": "success",
  "data": [
    {{"question_number": "1", "answer": "B"}},
    {{"question_number": "2", "answer": "C"}}
  ]
}}

If the image is faulty or data cannot be extracted:

{{
  "status": "error",
  "message": "Image is unreadable or does not contain question-answer data."
}}

Ensure the output is strictly in JSON format with no additional explanations or text."""

        result = client.models.generate_content(
            model=model,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=prompt),
                        types.Part.from_bytes(
                            data=base64.b64decode(image_base64),
                            mime_type="image/jpeg"
                        ),
                    ],
                )
            ],
        )

        parsed_result = extract_json_from_response(result.text)
        parsed_result.update({
            "metadata": {
                "processed_at": current_time,
                "processed_by": username
            }
        })
        return parsed_result

    except Exception as e:
        logger.error(f"Image processing error: {str(e)}")
        return {
            "status": "error",
            "message": "Failed to process image.",
            "error": str(e),
            "metadata": {
                "processed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "processed_by": username
            }
        }


@qtab_bp.route('/qtab')
@qtab_bp.route('/qtab/<path:folder_path>')
@login_required
def qtab_list(folder_path=''):
    """Display the question table interface with folder navigation."""
    conn = get_db_connection()
    
    # Folder Navigation Logic (same as subjective_list)
    folder_id = None
    breadcrumbs = []
    
    if folder_path:
        parts = folder_path.split('/')
        parent_id = None
        for i, part in enumerate(parts):
            res = conn.execute(
                "SELECT id FROM qtab_folders WHERE name = ? AND user_id = ? AND (parent_id = ? OR (? IS NULL AND parent_id IS NULL))", 
                (part, current_user.id, parent_id, parent_id)
            ).fetchone()
            if not res:
                conn.close()
                flash('Folder not found.', 'danger')
                return redirect(url_for('qtab.qtab_list'))
            parent_id = res['id']
            breadcrumbs.append({'name': part, 'path': '/'.join(parts[:i+1])})
        folder_id = parent_id

    # Fetch Subfolders
    if folder_id:
        subfolders = conn.execute(
            'SELECT * FROM qtab_folders WHERE parent_id = ? AND user_id = ? ORDER BY name', 
            (folder_id, current_user.id)
        ).fetchall()
    else:
        subfolders = conn.execute(
            'SELECT * FROM qtab_folders WHERE parent_id IS NULL AND user_id = ? ORDER BY name', 
            (current_user.id,)
        ).fetchall()

    # Fetch images in this folder from the qtab_images table
    if folder_id:
        images = conn.execute(
            'SELECT * FROM qtab_images WHERE folder_id = ? AND user_id = ? ORDER BY created_at DESC',
            (folder_id, current_user.id)
        ).fetchall()
    else:
        images = conn.execute(
            'SELECT * FROM qtab_images WHERE folder_id IS NULL AND user_id = ? ORDER BY created_at DESC',
            (current_user.id,)
        ).fetchall()

    conn.close()
    
    # Convert to dicts for template compatibility
    subfolders = [dict(row) for row in subfolders]
    images = [dict(row) for row in images]
    
    folder_tree = get_qtab_folder_tree(current_user.id)

    return render_template(
        'qtab_list.html', 
        images=images,
        subfolders=subfolders, 
        breadcrumbs=breadcrumbs, 
        current_folder_id=folder_id,
        folder_tree=folder_tree
    )


@qtab_bp.route('/qtab/upload', methods=['POST'])
@login_required
def qtab_upload():
    """Upload and process images for question extraction."""
    if 'image' not in request.files:
        return jsonify({"status": "error", "message": "No image file provided."}), 400

    image = request.files['image']
    if image.filename == '':
        return jsonify({"status": "error", "message": "No selected file."}), 400

    folder_id = request.form.get('folder_id')
    if folder_id == 'null' or folder_id == '':
        folder_id = None

    try:
        # Save the uploaded image
        filename = secure_filename(image.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        saved_filename = f"qtab_{current_user.id}_{timestamp}_{filename}"
        
        # Create qtab folder if it doesn't exist
        qtab_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'qtab')
        os.makedirs(qtab_folder, exist_ok=True)
        
        file_path = os.path.join(qtab_folder, saved_filename)
        image.save(file_path)

        # Process the image with Gemini
        result = process_image_for_questions(file_path, current_user.username)
        
        # Store in database
        conn = get_db_connection()
        conn.execute(
            '''INSERT INTO qtab_images 
               (user_id, folder_id, filename, original_name, result_json, status) 
               VALUES (?, ?, ?, ?, ?, ?)''',
            (current_user.id, folder_id, saved_filename, filename, 
             json.dumps(result), result.get('status', 'error'))
        )
        conn.commit()
        image_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        conn.close()
        
        result['image_id'] = image_id
        return jsonify(result)

    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return jsonify({
            "status": "error",
            "message": "An error occurred.",
            "error": str(e),
            "metadata": {
                "processed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "processed_by": current_user.username
            }
        }), 500


@qtab_bp.route('/qtab/image/<int:image_id>')
@login_required
def qtab_get_image(image_id):
    """Get details of a specific qtab image."""
    conn = get_db_connection()
    image = conn.execute(
        'SELECT * FROM qtab_images WHERE id = ? AND user_id = ?',
        (image_id, current_user.id)
    ).fetchone()
    conn.close()
    
    if not image:
        return jsonify({'error': 'Image not found'}), 404
    
    return jsonify(dict(image))


@qtab_bp.route('/qtab/image/<int:image_id>/delete', methods=['DELETE'])
@login_required
def qtab_delete_image(image_id):
    """Delete a qtab image."""
    conn = get_db_connection()
    
    # Check ownership
    image = conn.execute(
        'SELECT filename FROM qtab_images WHERE id = ? AND user_id = ?',
        (image_id, current_user.id)
    ).fetchone()
    
    if not image:
        conn.close()
        return jsonify({'error': 'Image not found or unauthorized'}), 404
    
    # Delete file
    qtab_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'qtab')
    file_path = os.path.join(qtab_folder, image['filename'])
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except OSError as e:
        logger.error(f"Error deleting file: {e}")
    
    # Delete from database
    conn.execute('DELETE FROM qtab_images WHERE id = ?', (image_id,))
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})


@qtab_bp.route('/qtab/create_folder', methods=['POST'])
@login_required
def qtab_create_folder():
    """Create a new folder in qtab_folders table."""
    data = request.json
    folder_name = data.get('name', '').strip()
    parent_id = data.get('parent_id')
    
    if not folder_name:
        return jsonify({'error': 'Folder name is required'}), 400
    
    if parent_id == 'null' or parent_id == '':
        parent_id = None
    
    conn = get_db_connection()
    try:
        # Check if folder with same name exists at this level
        if parent_id:
            existing = conn.execute(
                'SELECT id FROM qtab_folders WHERE name = ? AND parent_id = ? AND user_id = ?',
                (folder_name, parent_id, current_user.id)
            ).fetchone()
        else:
            existing = conn.execute(
                'SELECT id FROM qtab_folders WHERE name = ? AND parent_id IS NULL AND user_id = ?',
                (folder_name, current_user.id)
            ).fetchone()
        
        if existing:
            conn.close()
            return jsonify({'error': 'Folder with this name already exists'}), 400
        
        conn.execute(
            'INSERT INTO qtab_folders (name, parent_id, user_id) VALUES (?, ?, ?)',
            (folder_name, parent_id, current_user.id)
        )
        conn.commit()
        folder_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        conn.close()
        
        return jsonify({'success': True, 'folder_id': folder_id})
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'error': str(e)}), 500


@qtab_bp.route('/qtab/move_images', methods=['POST'])
@login_required
def qtab_move_images():
    """Move images to a different folder."""
    data = request.json
    image_ids = data.get('image_ids', [])
    target_folder_id = data.get('target_folder_id')
    
    if not image_ids:
        return jsonify({'error': 'No images selected'}), 400
    
    if target_folder_id == 'null' or target_folder_id == '':
        target_folder_id = None
    
    conn = get_db_connection()
    try:
        # Verify target folder ownership if not root
        if target_folder_id:
            owner = conn.execute(
                'SELECT user_id FROM qtab_folders WHERE id = ?',
                (target_folder_id,)
            ).fetchone()
            if not owner or owner['user_id'] != current_user.id:
                conn.close()
                return jsonify({'error': 'Unauthorized target folder'}), 403

        # Move images
        placeholders = ', '.join('?' * len(image_ids))
        conn.execute(
            f'UPDATE qtab_images SET folder_id = ? WHERE id IN ({placeholders}) AND user_id = ?',
            (target_folder_id, *image_ids, current_user.id)
        )

        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


@qtab_bp.route('/qtab/serve/<filename>')
@login_required
def serve_qtab_image(filename):
    """Serve qtab images with security check."""
    conn = get_db_connection()
    
    # Verify ownership
    image = conn.execute(
        'SELECT user_id FROM qtab_images WHERE filename = ?',
        (filename,)
    ).fetchone()
    conn.close()
    
    if not image or image['user_id'] != current_user.id:
        return "Unauthorized", 403
    
    qtab_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'qtab')
    return send_from_directory(qtab_folder, filename)


@qtab_bp.route('/qtab/exam/<int:image_id>')
@login_required
def qtab_exam_mode(image_id):
    """Exam mode - one question at a time with keyboard navigation."""
    conn = get_db_connection()
    
    # Get the image and verify ownership
    image = conn.execute(
        'SELECT * FROM qtab_images WHERE id = ? AND user_id = ?',
        (image_id, current_user.id)
    ).fetchone()
    
    if not image:
        conn.close()
        flash('Image not found or unauthorized', 'danger')
        return redirect(url_for('qtab.qtab_list'))
    
    # Parse the result JSON to get all questions
    result_json = json.loads(image['result_json']) if image['result_json'] else {}
    
    # Get all images in the same folder for navigation
    if image['folder_id']:
        all_images = conn.execute(
            'SELECT id, original_name, status FROM qtab_images WHERE folder_id = ? AND user_id = ? ORDER BY created_at',
            (image['folder_id'], current_user.id)
        ).fetchall()
    else:
        all_images = conn.execute(
            'SELECT id, original_name, status FROM qtab_images WHERE folder_id IS NULL AND user_id = ? ORDER BY created_at',
            (current_user.id,)
        ).fetchall()
    
    conn.close()
    
    return render_template(
        'qtab_exam.html',
        image=dict(image),
        result_json=result_json,
        all_images=[dict(img) for img in all_images],
        current_index=next((i for i, img in enumerate(all_images) if img['id'] == image_id), 0)
    )
