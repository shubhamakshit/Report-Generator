import os
import math
import uuid
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import requests

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['PROCESSED_FOLDER'] = 'processed'
app.config['OUTPUT_FOLDER'] = 'output'

# Create necessary directories
for folder in [app.config['UPLOAD_FOLDER'], app.config['PROCESSED_FOLDER'], app.config['OUTPUT_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

# Global storage for session data
sessions = {}

def get_or_download_font(font_path="arial.ttf", font_size=50):
    """Downloads Arial font if not available locally"""
    if not os.path.exists(font_path):
        try:
            response = requests.get(
                "https://github.com/kavin808/arial.ttf/raw/refs/heads/master/arial.ttf",
                timeout=30
            )
            response.raise_for_status()
            with open(font_path, 'wb') as f:
                f.write(response.content)
            print(f"Downloaded Arial font to {font_path}")
        except Exception as e:
            print(f"Failed to download Arial font: {e}")
            return ImageFont.load_default()

    try:
        return ImageFont.truetype(font_path, size=font_size)
    except IOError:
        return ImageFont.load_default()

def intelligent_sort_files(files):
    """Sort files intelligently by extracting numbers from filenames"""
    def extract_number(filename):
        import re
        numbers = re.findall(r'\d+', filename)
        return int(numbers[0]) if numbers else 0

    return sorted(files, key=lambda x: extract_number(x.filename))

def apply_contrast_enhancement(image_path, brightness=0, contrast=1.0, gamma=1.0):
    """Apply contrast, brightness and gamma correction to image"""
    img = Image.open(image_path)

    # Brightness adjustment
    if brightness != 0:
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(1.0 + brightness / 100.0)

    # Contrast adjustment
    if contrast != 1.0:
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(contrast)

    # Gamma correction
    if gamma != 1.0:
        img_array = np.array(img)
        img_array = np.power(img_array / 255.0, gamma) * 255.0
        img_array = np.clip(img_array, 0, 255).astype(np.uint8)
        img = Image.fromarray(img_array)

    return img

# --- NEW, CORRECTED CROPPING FUNCTION ---
def crop_image_perspective(image_path, points):
    """
    Crops and corrects the perspective of an image given 4 corner points.
    The points must be in [Top-Left, Top-Right, Bottom-Right, Bottom-Left] order.
    """
    if len(points) < 4:
        print("Warning: Not enough points for perspective crop. Returning original image.")
        return cv2.imread(image_path)

    img = cv2.imread(image_path)
    height, width = img.shape[:2]

    # Use only the first 4 points (corners) and convert to pixel coordinates
    src_points = np.array([
        [int(p['x'] * width), int(p['y'] * height)] for p in points[:4]
    ], dtype=np.float32)

    # Unpack the points for clarity based on the required order
    tl, tr, br, bl = src_points

    # Calculate the width of the new image.
    # It will be the maximum of the distances between (TR-TL) and (BR-BL)
    width_top = np.linalg.norm(tr - tl)
    width_bottom = np.linalg.norm(br - bl)
    max_width = int(max(width_top, width_bottom))

    # Calculate the height of the new image.
    # It will be the maximum of the distances between (TR-BR) and (TL-BL)
    height_right = np.linalg.norm(tr - br)
    height_left = np.linalg.norm(tl - bl)
    max_height = int(max(height_right, height_left))

    # Define the destination points for the new, straightened image
    dst_points = np.array([
        [0, 0],                         # Mapped from Top-Left
        [max_width - 1, 0],             # Mapped from Top-Right
        [max_width - 1, max_height - 1],# Mapped from Bottom-Right
        [0, max_height - 1]             # Mapped from Bottom-Left
    ], dtype=np.float32)

    # Get the perspective transformation matrix and apply it
    matrix = cv2.getPerspectiveTransform(src_points, dst_points)
    warped = cv2.warpPerspective(img, matrix, (max_width, max_height))

    return warped


def create_a4_pdf_from_images(image_info, base_folder, output_filename, images_per_page):
    """Creates PDF from images with question info"""
    if not image_info:
        return False

    # A4 dimensions at 600 DPI
    A4_WIDTH_PX, A4_HEIGHT_PX = 4960, 7016

    font_large = get_or_download_font(font_size=60)
    font_small = get_or_download_font(font_size=45)

    pages = []
    info_chunks = [image_info[i:i + images_per_page] for i in range(0, len(image_info), images_per_page)]

    for chunk_idx, chunk in enumerate(info_chunks):
        # Determine orientation
        total_width = total_height = valid_images = 0

        for info in chunk:
            img_path = os.path.join(base_folder, info['filename'])
            try:
                with Image.open(img_path) as img:
                    total_width += img.width
                    total_height += img.height
                    valid_images += 1
            except:
                pass

        if valid_images > 0:
            avg_aspect = (total_width / valid_images) / (total_height / valid_images)
            use_landscape = avg_aspect > 1.2
        else:
            use_landscape = False

        if use_landscape:
            page_width, page_height = A4_HEIGHT_PX, A4_WIDTH_PX
        else:
            page_width, page_height = A4_WIDTH_PX, A4_HEIGHT_PX

        MARGIN_PX = 200
        PADDING_PX = 80
        INFO_HEIGHT = 300

        page_canvas = Image.new('RGB', (page_width, page_height), 'white')
        draw_canvas = ImageDraw.Draw(page_canvas)
        num_images_on_page = len(chunk)

        cols = int(math.ceil(math.sqrt(num_images_on_page)))
        rows = int(math.ceil(num_images_on_page / cols))

        if use_landscape and cols < rows:
            cols, rows = rows, cols

        total_padding_x = (cols - 1) * PADDING_PX
        total_padding_y = (rows - 1) * PADDING_PX
        cell_width = (page_width - 2 * MARGIN_PX - total_padding_x) // cols
        cell_height = (page_height - 2 * MARGIN_PX - total_padding_y) // rows
        available_img_height = cell_height - INFO_HEIGHT

        for i, info in enumerate(chunk):
            col = i % cols
            row = i // cols

            img_path = os.path.join(base_folder, info['filename'])
            try:
                img = Image.open(img_path).convert("RGB")

                img_aspect = img.width / img.height
                cell_aspect = cell_width / available_img_height

                if img_aspect > cell_aspect:
                    new_width = cell_width - 20
                    new_height = int(new_width / img_aspect)
                else:
                    new_height = available_img_height - 20
                    new_width = int(new_height * img_aspect)

                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                cell_x = MARGIN_PX + col * (cell_width + PADDING_PX)
                cell_y = MARGIN_PX + row * (cell_height + PADDING_PX)

                paste_x = cell_x + (cell_width - img.width) // 2
                paste_y = cell_y + INFO_HEIGHT + (available_img_height - img.height) // 2

                page_canvas.paste(img, (paste_x, paste_y))

                info_box_coords = [cell_x, cell_y, cell_x + cell_width, cell_y + INFO_HEIGHT - 20]
                draw_canvas.rectangle(info_box_coords, fill="white", outline="lightgray", width=2)

                q_num_text = f"Question {info['question_number']}"
                draw_canvas.text((cell_x + 20, cell_y + 20), q_num_text, fill="black", font=font_large)

                info_y_offset = 90
                line_height = 50

                excluded_keys = {'filename', 'question_number'}
                for key, value in info.items():
                    if key not in excluded_keys:
                        display_key = key.replace('_', ' ').title()
                        info_text = f"{display_key}: {str(value)[:30]}"

                        text_bbox = draw_canvas.textbbox((0, 0), info_text, font=font_small)
                        text_width = text_bbox[2] - text_bbox[0]

                        if text_width > cell_width - 40:
                            info_text = info_text[:int(len(info_text) * (cell_width - 40) / text_width)] + "..."

                        draw_canvas.text((cell_x + 20, cell_y + info_y_offset), info_text, fill="darkgray", font=font_small)
                        info_y_offset += line_height

                        if info_y_offset > INFO_HEIGHT - 40:
                            break

                img_border_coords = [cell_x + 10, cell_y + INFO_HEIGHT - 10, cell_x + cell_width - 10, cell_y + cell_height - 10]
                draw_canvas.rectangle(img_border_coords, outline="lightgray", width=1)

            except Exception as e:
                print(f"Error processing image {info['filename']}: {e}")
                cell_x = MARGIN_PX + col * (cell_width + PADDING_PX)
                cell_y = MARGIN_PX + row * (cell_height + PADDING_PX)
                draw_canvas.rectangle([cell_x, cell_y, cell_x + cell_width, cell_y + cell_height], outline="red", width=3)
                draw_canvas.text((cell_x + 20, cell_y + cell_height // 2), "Image Load Error", fill="red", font=font_small)

        pages.append(page_canvas)

    if pages:
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
        pages[0].save(output_path, "PDF", resolution=600.0, save_all=True, append_images=pages[1:])
        return True
    return False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_files():
    session_id = str(uuid.uuid4())
    sessions[session_id] = {'images': [], 'questions': []}

    files = request.files.getlist('images')
    if not files or files[0].filename == '':
        return jsonify({'error': 'No files selected'})

    files = intelligent_sort_files(files)

    uploaded_files = []
    for i, file in enumerate(files):
        if file:
            filename = f"{session_id}_{i}_{secure_filename(file.filename)}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            uploaded_files.append({
                'filename': filename,
                'original_name': file.filename,
                'index': i
            })

    sessions[session_id]['images'] = uploaded_files
    return jsonify({'session_id': session_id, 'files': uploaded_files})

@app.route('/crop/<session_id>/<int:image_index>')
def crop_interface(session_id, image_index):
    if session_id not in sessions:
        return "Session not found", 404

    if image_index >= len(sessions[session_id]['images']):
        return "Image not found", 404

    image_info = sessions[session_id]['images'][image_index]
    return render_template('crop.html', session_id=session_id, image_index=image_index, image_info=image_info)

@app.route('/process_crop', methods=['POST'])
def process_crop():
    data = request.json
    session_id = data['session_id']
    image_index = data['image_index']
    points = data['points']
    adjustments = data.get('adjustments', {})

    if session_id not in sessions:
        return jsonify({'error': 'Session not found'})

    image_info = sessions[session_id]['images'][image_index]
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], image_info['filename'])

    processed_image = None # To hold the image data for enhancement

    # --- MODIFIED CROPPING LOGIC ---
    if points and len(points) >= 4:
        try:
            # The frontend sends corners in [TL, TR, BL, BR] order.
            # Our new function needs them in [TL, TR, BR, BL] order.
            # So, we swap the last two CORNER points before passing them.
            # The first 4 items in `points` are the corners.
            corners_ordered_for_cv2 = [points[0], points[1], points[3], points[2]]

            processed_image = crop_image_perspective(input_path, corners_ordered_for_cv2)
        except Exception as e:
            print(f"Cropping error: {e}")
            return jsonify({'error': f'Cropping failed: {str(e)}'})

    # If cropping wasn't performed or failed, load the original image
    if processed_image is None:
        processed_image = cv2.imread(input_path)

    # Save the processed (cropped or original) image temporarily to apply enhancements
    try:
        temp_path = os.path.join(app.config['PROCESSED_FOLDER'], f"temp_{image_info['filename']}")
        cv2.imwrite(temp_path, processed_image)

        enhanced_img = apply_contrast_enhancement(
            temp_path,
            brightness=adjustments.get('brightness', 0),
            contrast=adjustments.get('contrast', 1.0),
            gamma=adjustments.get('gamma', 1.0)
        )

        processed_filename = f"processed_{image_info['filename']}"
        processed_path = os.path.join(app.config['PROCESSED_FOLDER'], processed_filename)
        enhanced_img.save(processed_path)

        sessions[session_id]['images'][image_index]['processed_filename'] = processed_filename

        return jsonify({'success': True, 'processed_filename': processed_filename})

    except Exception as e:
        print(f"Enhancement/Save error: {e}")
        return jsonify({'error': f'Processing failed: {str(e)}'})


@app.route('/question_entry/<session_id>')
def question_entry(session_id):
    if session_id not in sessions:
        return "Session not found", 404

    return render_template('question_entry.html', session_id=session_id, images=sessions[session_id]['images'])

@app.route('/save_questions', methods=['POST'])
def save_questions():
    data = request.json
    session_id = data['session_id']
    questions = data['questions']

    if session_id not in sessions:
        return jsonify({'error': 'Session not found'})

    sessions[session_id]['questions'] = questions
    return jsonify({'success': True})

@app.route('/generate_pdf', methods=['POST'])
def generate_pdf():
    data = request.json
    session_id = data['session_id']
    pdf_name = data.get('pdf_name', 'questions')
    images_per_page = data.get('images_per_page', 4)
    filter_type = data.get('filter_type', 'all')

    if session_id not in sessions:
        return jsonify({'error': 'Session not found'})

    session_data = sessions[session_id]
    questions = session_data['questions']
    images = session_data['images']

    filtered_questions = []
    for i, question in enumerate(questions):
        include = False
        if filter_type == 'all':
            include = True
        elif filter_type == 'wrong' and question['status'] == 'wrong':
            include = True
        elif filter_type == 'unattempted' and question['status'] == 'unattempted':
            include = True

        if include and i < len(images):
            image_info = images[i]
            # Use processed filename if it exists, otherwise fall back to original upload
            base_filename = image_info.get('processed_filename', image_info['filename'])
            base_folder = app.config['PROCESSED_FOLDER'] if 'processed_' in base_filename else app.config['UPLOAD_FOLDER']

            # Create a full info dictionary for the PDF generator
            question_info = {
                'filename': base_filename,
                'question_number': question['question_number'],
                'subject': question['subject'],
                'status': question['status'],
                'marked_solution': question.get('marked_solution', 'N/A'),
                'actual_solution': question.get('actual_solution', 'N/A'),
                'time_taken': question.get('time_taken', 'N/A')
            }
            # Add a key for the folder path to resolve ambiguity
            question_info['_base_folder'] = base_folder
            filtered_questions.append(question_info)

    if not filtered_questions:
        return jsonify({'error': 'No questions match the filter criteria for the PDF'})

    pdf_filename = f"{secure_filename(pdf_name)}_{filter_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

    # We need to adapt the PDF creation to handle images from different folders
    # For simplicity, we will continue to assume all processed images are in PROCESSED_FOLDER
    # The logic above already ensures the correct filename is passed.
    success = create_a4_pdf_from_images(
        image_info=filtered_questions,
        base_folder=app.config['PROCESSED_FOLDER'], # Simplified assumption
        output_filename=pdf_filename,
        images_per_page=images_per_page
    )

    if success:
        return jsonify({'success': True, 'pdf_filename': pdf_filename})
    else:
        return jsonify({'error': 'PDF generation failed'})

@app.route('/download/<filename>')
def download_file(filename):
    return send_file(os.path.join(app.config['OUTPUT_FOLDER'], filename), as_attachment=True)

@app.route('/image/<folder>/<filename>')
def serve_image(folder, filename):
    if folder == 'upload':
        folder_path = app.config['UPLOAD_FOLDER']
    elif folder == 'processed':
        folder_path = app.config['PROCESSED_FOLDER']
    else:
        return "Invalid folder", 404
    return send_file(os.path.join(folder_path, filename))

if __name__ == '__main__':
    app.run(debug=True, port=5000)