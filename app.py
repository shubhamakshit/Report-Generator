import os
import math
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import requests

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['PROCESSED_FOLDER'] = 'processed'
app.config['OUTPUT_FOLDER'] = 'output'

for folder in [app.config['UPLOAD_FOLDER'], app.config['PROCESSED_FOLDER'], app.config['OUTPUT_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

sessions = {}

def get_or_download_font(font_path="arial.ttf", font_size=50):
    if not os.path.exists(font_path):
        try:
            response = requests.get("https://github.com/kavin808/arial.ttf/raw/refs/heads/master/arial.ttf", timeout=30)
            response.raise_for_status()
            with open(font_path, 'wb') as f: f.write(response.content)
        except Exception:
            return ImageFont.load_default()
    try:
        return ImageFont.truetype(font_path, size=font_size)
    except IOError:
        return ImageFont.load_default()

def apply_contrast_enhancement(image_path, brightness=0, contrast=1.0, gamma=1.0):
    img = Image.open(image_path)
    if brightness != 0:
        img = ImageEnhance.Brightness(img).enhance(1.0 + brightness / 100.0)
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if gamma != 1.0:
        img_array = np.array(img)
        img_array = np.power(img_array / 255.0, gamma) * 255.0
        img_array = np.clip(img_array, 0, 255).astype(np.uint8)
        img = Image.fromarray(img_array)
    return img

def crop_image_perspective(image_path, points):
    if len(points) < 4:
        return cv2.imread(image_path)

    img = cv2.imread(image_path)
    height, width = img.shape[:2]

    def clamp(val):
        return max(0.0, min(1.0, val))

    src_points = np.array([
        [clamp(p.get('x', 0.0)) * width, clamp(p.get('y', 0.0)) * height] for p in points[:4]
    ], dtype=np.float32)

    (tl, tr, br, bl) = src_points
    width_top = np.linalg.norm(tr - tl)
    width_bottom = np.linalg.norm(br - bl)
    max_width = int(max(width_top, width_bottom))
    height_right = np.linalg.norm(tr - br)
    height_left = np.linalg.norm(tl - bl)
    max_height = int(max(height_right, height_left))
    
    if max_width == 0 or max_height == 0:
        return img

    dst_points = np.array([
        [0, 0], [max_width - 1, 0], [max_width - 1, max_height - 1], [0, max_height - 1]
    ], dtype=np.float32)

    matrix = cv2.getPerspectiveTransform(src_points, dst_points)
    warped = cv2.warpPerspective(img, matrix, (max_width, max_height))
    return warped


def create_a4_pdf_from_images(image_info, base_folder, output_filename, images_per_page):
    if not image_info: return False
    A4_WIDTH_PX, A4_HEIGHT_PX = 4960, 7016
    font_large = get_or_download_font(font_size=60)
    font_small = get_or_download_font(font_size=45)
    pages = []
    info_chunks = [image_info[i:i + images_per_page] for i in range(0, len(image_info), images_per_page)]
    for chunk in info_chunks:
        total_width = total_height = valid_images = 0
        for info in chunk:
            img_path = os.path.join(base_folder, info['filename'])
            try:
                with Image.open(img_path) as img:
                    total_width += img.width
                    total_height += img.height
                    valid_images += 1
            except: pass
        
        avg_aspect = (total_width / valid_images) / (total_height / valid_images) if valid_images > 0 else 1
        use_landscape = avg_aspect > 1.2
        page_width, page_height = (A4_HEIGHT_PX, A4_WIDTH_PX) if use_landscape else (A4_WIDTH_PX, A4_HEIGHT_PX)
        
        MARGIN_PX, PADDING_PX, INFO_HEIGHT = 200, 80, 300
        page_canvas = Image.new('RGB', (page_width, page_height), 'white')
        draw_canvas = ImageDraw.Draw(page_canvas)
        num_images_on_page = len(chunk)
        cols = int(math.ceil(math.sqrt(num_images_on_page)))
        rows = int(math.ceil(num_images_on_page / cols))
        if use_landscape and cols < rows: cols, rows = rows, cols
        
        total_padding_x = (cols - 1) * PADDING_PX
        total_padding_y = (rows - 1) * PADDING_PX
        cell_width = (page_width - 2 * MARGIN_PX - total_padding_x) // cols
        cell_height = (page_height - 2 * MARGIN_PX - total_padding_y) // rows
        available_img_height = cell_height - INFO_HEIGHT

        for i, info in enumerate(chunk):
            col, row = i % cols, i // cols
            cell_x = MARGIN_PX + col * (cell_width + PADDING_PX)
            cell_y = MARGIN_PX + row * (cell_height + PADDING_PX)
            img_path = os.path.join(base_folder, info['filename'])
            try:
                with Image.open(img_path).convert("RGB") as img:
                    img_aspect = img.width / img.height
                    cell_aspect = cell_width / available_img_height
                    if img_aspect > cell_aspect:
                        new_width = cell_width - 20
                        new_height = int(new_width / img_aspect)
                    else:
                        new_height = available_img_height - 20
                        new_width = int(new_height * img_aspect)
                    
                    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    paste_x = cell_x + (cell_width - img.width) // 2
                    paste_y = cell_y + INFO_HEIGHT + (available_img_height - img.height) // 2
                    page_canvas.paste(img, (paste_x, paste_y))
                    
                    q_num_text = f"Question {info['question_number']}"
                    draw_canvas.text((cell_x + 20, cell_y + 20), q_num_text, fill="black", font=font_large)
                    info_y_offset = 90
                    excluded_keys = {'filename', 'question_number'}
                    for key, value in info.items():
                        if key not in excluded_keys:
                            display_key = key.replace('_', ' ').title()
                            info_text = f"{display_key}: {str(value)[:30]}"
                            draw_canvas.text((cell_x + 20, cell_y + info_y_offset), info_text, fill="darkgray", font=font_small)
                            info_y_offset += 50
            except Exception as e:
                print(f"PDF creation error for {info['filename']}: {e}")
        pages.append(page_canvas)
    
    if pages:
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
        pages[0].save(output_path, "PDF", resolution=600.0, save_all=True, append_images=pages[1:])
        return True
    return False

@app.route('/')
def index(): return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_files():
    session_id = str(uuid.uuid4())
    sessions[session_id] = {'images': [], 'questions': []}
    files = request.files.getlist('images')
    if not files or files[0].filename == '': return jsonify({'error': 'No files selected'})
    
    uploaded_files = []
    for i, file in enumerate(files):
        if file:
            filename = f"{session_id}_{i}_{secure_filename(file.filename)}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            uploaded_files.append({'filename': filename, 'original_name': file.filename, 'index': i})
    
    sessions[session_id]['images'] = uploaded_files
    return jsonify({'session_id': session_id, 'files': uploaded_files})

@app.route('/crop/<session_id>/<int:image_index>')
def crop_interface(session_id, image_index):
    if session_id not in sessions or image_index >= len(sessions[session_id]['images']):
        return "Session or image not found", 404
    return render_template('crop.html', session_id=session_id, image_index=image_index, image_info=sessions[session_id]['images'][image_index])

@app.route('/process_crop', methods=['POST'])
def process_crop():
    data = request.json
    session_id, image_index, points, adjustments = data['session_id'], data['image_index'], data['points'], data.get('adjustments', {})
    if session_id not in sessions: return jsonify({'error': 'Session not found'})
    
    image_info = sessions[session_id]['images'][image_index]
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], image_info['filename'])
    
    try:
        # Step 1: Perform the crop if points are provided.
        if points and len(points) >= 4:
            image_to_process = crop_image_perspective(input_path, points)
        else:
            image_to_process = cv2.imread(input_path)

        # Step 2: Save the result of Step 1 (cropped or original) to a temporary file.
        temp_path = os.path.join(app.config['PROCESSED_FOLDER'], f"temp_{image_info['filename']}")
        cv2.imwrite(temp_path, image_to_process)
        
        # ### START OF THE FIX ###
        # Step 3: Apply enhancements to the TEMPORARY file, not the original input.
        enhanced_img = apply_contrast_enhancement(temp_path, **adjustments)
        # ### END OF THE FIX ###
        
        # Step 4: Save the final, enhanced image to its permanent location.
        processed_filename = f"processed_{image_info['filename']}"
        processed_path = os.path.join(app.config['PROCESSED_FOLDER'], processed_filename)
        enhanced_img.save(processed_path)
        
        # Update session data
        sessions[session_id]['images'][image_index]['processed_filename'] = processed_filename
        return jsonify({'success': True, 'processed_filename': processed_filename})

    except Exception as e:
        print(f"Processing error: {e}")
        return jsonify({'error': f'Processing failed: {str(e)}'})


@app.route('/question_entry/<session_id>')
def question_entry(session_id):
    if session_id not in sessions: return "Session not found", 404
    return render_template('question_entry.html', session_id=session_id, images=sessions[session_id]['images'])

@app.route('/save_questions', methods=['POST'])
def save_questions():
    data = request.json
    session_id, questions = data['session_id'], data['questions']
    if session_id not in sessions: return jsonify({'error': 'Session not found'})
    sessions[session_id]['questions'] = questions
    return jsonify({'success': True})

@app.route('/generate_pdf', methods=['POST'])
def generate_pdf():
    data = request.json
    session_id = data['session_id']
    if session_id not in sessions: return jsonify({'error': 'Session not found'})
    
    questions = sessions[session_id]['questions']
    images = sessions[session_id]['images']
    filter_type = data.get('filter_type', 'all')
    
    filtered_questions = []
    for i, question in enumerate(questions):
        if (filter_type == 'all' or (filter_type == 'wrong' and question['status'] == 'wrong') or 
           (filter_type == 'unattempted' and question['status'] == 'unattempted')):
            if i < len(images):
                image_info = images[i]
                filename = image_info.get('processed_filename', image_info['filename'])
                question_data = {**question, 'filename': filename}
                filtered_questions.append(question_data)
    
    if not filtered_questions: return jsonify({'error': 'No questions match the filter criteria'})
    
    pdf_filename = f"{secure_filename(data.get('pdf_name', 'questions'))}_{filter_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    success = create_a4_pdf_from_images(
        image_info=filtered_questions,
        base_folder=app.config['PROCESSED_FOLDER'],
        output_filename=pdf_filename,
        images_per_page=data.get('images_per_page', 4)
    )
    if success: return jsonify({'success': True, 'pdf_filename': pdf_filename})
    else: return jsonify({'error': 'PDF generation failed'})

@app.route('/download/<filename>')
def download_file(filename):
    return send_file(os.path.join(app.config['OUTPUT_FOLDER'], filename), as_attachment=True)

@app.route('/image/<folder>/<filename>')
def serve_image(folder, filename):
    folder_path = app.config.get(f'{folder.upper()}_FOLDER')
    if not folder_path: return "Invalid folder", 404
    return send_file(os.path.join(folder_path, filename))

if __name__ == '__main__':
    app.run(debug=True, port=1302)

