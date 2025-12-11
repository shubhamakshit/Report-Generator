from flask import Blueprint, render_template, request, jsonify, current_app, redirect, url_for
from utils import get_db_connection
from PIL import Image, ImageDraw
import os
from utils import get_or_download_font
import json
import imgkit
from bs4 import BeautifulSoup
import re
import uuid
import requests
import base64
import html
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from json_processor_v3 import JSONProcessorV3

json_bp = Blueprint('json_bp', __name__)

# --- SCHEMAS ---
SCHEMA_V2_1 = {
    "version": "2.1",
}

SCHEMA_V2 = {
    # To be defined by the user
}

SCHEMAS = {
    "2.1": SCHEMA_V2_1,
    "2": SCHEMA_V2,
}


# --- JSON PROCESSOR CLASS ---
class JSONProcessor:
    def __init__(self, json_data):
        self.data = json_data
        self.version = self._detect_version()

    def _detect_version(self):
        if self.data and "version" in self.data:
            return str(self.data["version"])
        if self.data and "data" in self.data and "root" in self.data["data"]:
            return "original"
        if self.data and "root" in self.data:
             return "original"
        return None

    def process(self, statuses=None):
        if self.version == "2.1":
            return self._process_v2_1()
        elif self.version == "2":
            return self._process_v2()
        elif self.version == "original":
            return self._process_original(statuses=statuses)
        else:
            raise ValueError(f"Unsupported or unknown JSON version: {self.version}")

    def _process_v2_1(self):
        def safe_int(value):
            try:
                return int(value)
            except (ValueError, TypeError):
                return None

        processed_questions = []
        statuses_to_include = self.data.get("config", {}).get("statuses_to_include", ["wrong", "unattempted"])
        for q in self.data.get("questions", []):
            status = q.get("status")
            if status in statuses_to_include:
                options = q.get("options", [])
                user_answer = "N/A"
                if q.get('source') == 'classified':
                    user_answer = q.get('user_answer_index')
                else:
                    user_answer_index = safe_int(q.get("user_answer_index"))
                    if user_answer_index is not None and user_answer_index < len(options):
                        user_answer = options[user_answer_index]

                correct_answer = "N/A"
                if q.get('source') == 'classified':
                    correct_answer = q.get('correct_answer_index')
                else:
                    correct_answer_index = safe_int(q.get("correct_answer_index"))
                    if correct_answer_index is not None and correct_answer_index < len(options):
                        correct_answer = options[correct_answer_index]

                processed_questions.append({
                    "question": q.get("question_text"),
                    "yourAnswer": user_answer,
                    "correctAnswer": correct_answer,
                    "status": status,
                    "custom_fields": q.get("custom_fields", {})
                })
        return {
            "test_name": self.data.get("test_name", "Unnamed Test"),
            "questions": processed_questions,
            "font_size": self.data.get("config", {}).get("font_size", 24),
            "metadata": self.data.get("metadata", {}),
            "config": self.data.get("config", {})
        }

    def _process_v2(self):
        raise NotImplementedError("Processing for JSON schema v2 is not yet implemented. Please provide the schema.")

    def _process_original(self, statuses=None):
        data_root = self.data
        if 'data' in self.data and 'root' in self.data['data']:
            data_root = self.data['data']

        questions_data = data_root.get("root", {}).get("_testAttempt4d9rq8", {}).get("test", {}).get("_questions4dxVsH", {}).get("edges", [])
        user_answers = data_root.get("root", {}).get("_testAttempt4d9rq8", {}).get("userAnswers", {})
        
        selected_statuses = statuses if statuses is not None else self.data.get('statuses', ['wrong', 'unattempted'])

        processed_questions = []
        for edge in questions_data:
            node = edge.get("node", {})
            question_id_encoded = node.get("id", "")
            try:
                question_id = base64.b64decode(question_id_encoded).decode('utf-8').split(':')[1]
            except (IndexError, ValueError, TypeError):
                continue

            question_text = node.get("question", "")
            question_text = fix_font_family_in_html(question_text)
            
            options = node.get("options", [])
            correct_option_index = node.get("correctOptionIndex")
            user_answer_index_str = user_answers.get(question_id)
            user_answer_index = int(user_answer_index_str) if user_answer_index_str is not None else None

            status = "unattempted"
            if user_answer_index is not None:
                status = "correct" if user_answer_index == correct_option_index else "wrong"

            if status in selected_statuses:
                user_answer = "N/A"
                if user_answer_index is not None and user_answer_index < len(options):
                    user_answer = options[user_answer_index]
                
                correct_answer = "N/A"
                if correct_option_index is not None and correct_option_index < len(options):
                    correct_answer = options[correct_option_index]

                processed_questions.append({
                    "question": question_text,
                    "yourAnswer": user_answer,
                    "correctAnswer": correct_answer,
                    "status": status
                })
        
        test_name = self.data.get('test_name')
        if not test_name:
            try:
                test_name = data_root['root']['_testAttempt4d9rq8']['test']['name']
            except KeyError:
                test_name = 'Uploaded Test'

        return {
            "test_name": test_name,
            "questions": processed_questions,
            "font_size": self.data.get('font_size', 24)
        }

def html_to_image_worker(item, session_id, font_size, processed_folder, original_filename, index):
    """Worker function to convert a single HTML question to an image."""
    question_html = item.get('question')
    if not question_html:
        question_html = "<p>Question text not provided.</p>"

    soup = BeautifulSoup(question_html, 'html.parser')
    for img in soup.find_all('img'):
        img_src = img.get('src')
        if img_src:
            if img_src.startswith('http'):
                try:
                    response = requests.get(img_src)
                    if response.status_code == 200:
                        img_b64 = base64.b64encode(response.content).decode('utf-8')
                        img['src'] = f"data:image/png;base64,{img_b64}"
                except Exception as e:
                    current_app.logger.error(f"Could not embed image {img_src}: {e}")
            elif os.path.exists(img_src):
                with open(img_src, 'rb') as f:
                    img_b64 = base64.b64encode(f.read()).decode('utf-8')
                    img['src'] = f"data:image/jpeg;base64,{img_b64}"

    question_html = str(soup)

    style = f"<style>body {{ font-size: {font_size}px; }}</style>"
    question_html = style + question_html

    processed_filename = f"processed_{session_id}_page0_crop{index}.jpg"
    image_path = os.path.join(processed_folder, processed_filename)
    
    try:
        imgkit.from_string(question_html, image_path)
    except Exception:
        image_font = get_or_download_font(font_size=font_size)
        soup = BeautifulSoup(question_html, 'html.parser')
        question_text = soup.get_text()
        image = Image.new('RGB', (800, 600), 'white')
        draw = ImageDraw.Draw(image)
        final_y = draw_multiline_text(draw, question_text, (20, 20), image_font, 760, 'black')
        image = image.crop((0, 0, 800, final_y + 20))
        image.save(image_path, 'JPEG')

    return {
        'processed_filename': processed_filename,
        'original_filename': original_filename,
        'item': item,
        'index': index
    }

from flask_login import login_required, current_user

def _process_json_and_generate_pdf(raw_data, user_id):
    """
    Helper function to process JSON data, generate images, and create a PDF.
    This is called by both the /json_upload route and directly from other modules.
    """
    from utils import get_or_download_font, create_a4_pdf_from_images
    
    conn = get_db_connection()
    try:
        if not raw_data:
            return {'error': 'No JSON payload received.'}, 400

        processor = JSONProcessor(raw_data)
        processed_data = processor.process()

        test_name = processed_data.get("test_name")
        processed_questions = processed_data.get("questions")
        font_size = processed_data.get("font_size")
        metadata = processed_data.get("metadata", {})
        tags = metadata.get("tags", "programmatic")
        layout = processed_data.get("config", {}).get("layout", {})
        
        images_per_page = int(layout.get('images_per_page', 4))
        orientation = layout.get('orientation', 'portrait')
        grid_rows = int(layout.get('grid_rows')) if layout.get('grid_rows') else None
        grid_cols = int(layout.get('grid_cols')) if layout.get('grid_cols') else None
        practice_mode = layout.get('practice_mode', 'none')
        
        session_id = str(uuid.uuid4())
        conn.execute('INSERT INTO sessions (id, original_filename, user_id) VALUES (?, ?, ?)', (session_id, f"{test_name}.json", user_id))
        
        original_filename = f"{session_id}_dummy_original.png"
        conn.execute(
            'INSERT INTO images (session_id, image_index, filename, original_name, image_type) VALUES (?, ?, ?, ?, ?)',
            (session_id, 0, original_filename, 'JSON Upload', 'original')
        )

        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(
                lambda p: html_to_image_worker(*p),
                [(item, session_id, font_size, current_app.config['PROCESSED_FOLDER'], original_filename, i) for i, item in enumerate(processed_questions)]
            ))

        for i, item in enumerate(processed_questions):
            processed_filename = f"processed_{session_id}_page0_crop{i}.jpg"
            image_insert_result = conn.execute(
                'INSERT INTO images (session_id, image_index, filename, original_name, processed_filename, image_type) VALUES (?, ?, ?, ?, ?, ?)',
                (session_id, i + 1, original_filename, f"Question {i+1}", processed_filename, 'cropped')
            )
            image_id = image_insert_result.lastrowid
            conn.execute(
                'INSERT INTO questions (session_id, image_id, question_number, status, marked_solution, actual_solution) VALUES (?, ?, ?, ?, ?, ?)',
                (session_id, image_id, str(i + 1), item.get('status'), item.get('yourAnswer'), item.get('correctAnswer'))
            )
        
        conn.commit()

        if raw_data.get('view') is True:
            query = "SELECT q.*, i.processed_filename FROM questions q JOIN images i ON q.image_id = i.id WHERE q.session_id = ? ORDER BY i.id"
            all_questions = [dict(row) for row in conn.execute(query, (session_id,)).fetchall()]
            if not all_questions:
                return {'error': 'No questions were processed to generate a PDF.'}, 400

            from datetime import datetime
            from werkzeug.utils import secure_filename
            pdf_filename = f"{secure_filename(test_name)}_{session_id[:8]}.pdf"
            
            create_a4_pdf_from_images(
                image_info=all_questions, base_folder=current_app.config['PROCESSED_FOLDER'], output_filename=pdf_filename,
                images_per_page=images_per_page, output_folder=current_app.config['OUTPUT_FOLDER'],
                orientation=orientation, grid_rows=grid_rows, grid_cols=grid_cols, practice_mode=practice_mode
            )
            conn.execute(
                'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename, user_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (session_id, pdf_filename, test_name, tags, 'Generated automatically via JSON upload.', f"{test_name}.json", user_id)
            )
            conn.commit()
            return {'success': True, 'view_url': url_for('main.view_pdf', filename=pdf_filename, _external=True)}, 200
        else:
            return {'success': True, 'edit_url': url_for('main.question_entry_v2', session_id=session_id, test_name=test_name, _external=True)}, 200

    except Exception as e:
        if conn:
            conn.rollback()
        current_app.logger.error(f"Error in _process_json_and_generate_pdf: {repr(e)}")
        return {'error': str(e)}, 500
    finally:
        if conn:
            conn.close()

@json_bp.route('/json_upload', methods=['GET', 'POST'])
@login_required
def json_upload():
    if request.method == 'POST':
        result, status_code = _process_json_and_generate_pdf(request.json, current_user.id)
        return jsonify(result), status_code
    return render_template('json_upload.html')


def draw_multiline_text(draw, text, position, font, max_width, fill):
    x, y = position
    lines = text.split('\n')
    wrapped_lines = []
    for line in lines:
        if font.getlength(line) <= max_width:
            wrapped_lines.append(line)
        else:
            current_line = ''
            for word in line.split(' '):
                if font.getlength(current_line + word + ' ') <= max_width:
                    current_line += word + ' '
                else:
                    wrapped_lines.append(current_line)
                    current_line = word + ' '
            wrapped_lines.append(current_line)

    line_height = font.getbbox('A')[3] - font.getbbox('A')[1] if hasattr(font, 'getbbox') else font.getsize('A')[1]
    for line in wrapped_lines:
        draw.text((x, y), line, fill=fill, font=font)
        y += line_height + 5
    return y

def fix_font_family_in_html(html_string):
    if not html_string:
        return html_string
    
    html_string = html.unescape(html_string)
    pattern = r'font-family:\s*"([^"]+(?:,\s*"[^"]+"\s*)*)"'
    
    def replace_font_family(match):
        font_value = match.group(1)
        font_value = font_value.replace('"', "'")
        return f"font-family:'{font_value}'"
    
    html_string = re.sub(pattern, replace_font_family, html_string)
    html_string = re.sub(r'"', "'", html_string)
    
    return html_string


@json_bp.route('/process_json', methods=['POST'])
def process_json():
    request_data = request.json
    data_to_process = request_data.get('data', request_data)
    selected_statuses = request_data.get('statuses', ['wrong', 'unattempted'])
    
    try:
        processor = JSONProcessor(data_to_process)
        processed_data = processor.process(statuses=selected_statuses)
        return jsonify({'success': True, 'questions': processed_data.get('questions')})
    except Exception as e:
        current_app.logger.error(f"Error in process_json: {repr(e)}")
        return jsonify({'success': False, 'error': str(e)})


@json_bp.route('/save_processed_json', methods=['POST'])
@login_required
def save_processed_json():
    from app import get_db_connection
    questions_data = request.form.get('questions_data')
    test_name = request.form.get('test_name')
    font_size = int(request.form.get('font_size', 24))
    
    try:
        questions = json.loads(questions_data)
    except json.JSONDecodeError as e:
        try:
            fixed_data = questions_data.replace('"', "'")
            fixed_data = re.sub(r'font-family:"([^"]+)"', lambda m: f"font-family:'{m.group(1).replace('"', "'")}'", fixed_data)
            questions = json.loads(fixed_data)
        except Exception as inner_e:
            current_app.logger.error(f"Initial JSONDecodeError: {e}")
            current_app.logger.error(f"Could not fix JSON data. Error: {inner_e}")
            current_app.logger.error(f"Problematic JSON data (raw): {repr(questions_data)}")
            return jsonify({'error': 'Invalid JSON data received.'}), 400
    
    session_id = str(uuid.uuid4())
    conn = get_db_connection()
    
    try:
        conn.execute('INSERT INTO sessions (id, original_filename, user_id) VALUES (?, ?, ?)', (session_id, 'JSON Upload', current_user.id))
        
        original_filename = f"{session_id}_dummy_original.png"
        conn.execute(
            'INSERT INTO images (session_id, image_index, filename, original_name, image_type) VALUES (?, ?, ?, ?, ?)',
            (session_id, 0, original_filename, 'JSON Upload', 'original')
        )

        for i, item in enumerate(questions):
            question_html = item.get('question')
            your_answer = item.get('yourAnswer')
            correct_answer = item.get('correctAnswer')

            if not question_html:
                question_html = "<p>Question text was not provided.</p>"

            soup = BeautifulSoup(question_html, 'html.parser')
            for img in soup.find_all('img'):
                img_src = img.get('src')
                if img_src and img_src.startswith('http'):
                    try:
                        response = requests.get(img_src)
                        if response.status_code == 200:
                            img_b64 = base64.b64encode(response.content).decode('utf-8')
                            img['src'] = f"data:image/png;base64,{img_b64}"
                    except Exception as e:
                        current_app.logger.error(f"Could not embed image {img_src}: {e}")
            
            question_html = str(soup)

            style = f"<style>body {{ font-size: {font_size}px; }}</style>"
            question_html = style + question_html

            processed_filename = f"processed_{session_id}_page0_crop{i}.jpg"
            image_path = os.path.join(current_app.config['PROCESSED_FOLDER'], processed_filename)
            
            try:
                imgkit.from_string(question_html, image_path)
            except Exception as e:
                image_font = get_or_download_font(font_size=font_size)
                soup = BeautifulSoup(question_html, 'html.parser')
                question_text = soup.get_text()
                image = Image.new('RGB', (800, 600), 'white')
                draw = ImageDraw.Draw(image)
                final_y = draw_multiline_text(draw, question_text, (20, 20), image_font, 760, 'black')
                image = image.crop((0, 0, 800, final_y + 20))
                image.save(image_path, 'JPEG')

            image_insert_result = conn.execute(
                'INSERT INTO images (session_id, image_index, filename, original_name, processed_filename, image_type) VALUES (?, ?, ?, ?, ?, ?)',
                (session_id, i + 1, original_filename, f"Question {i+1}", processed_filename, 'cropped')
            )
            image_id = image_insert_result.lastrowid

            status = item.get('status')
            conn.execute(
                'INSERT INTO questions (session_id, image_id, question_number, status, marked_solution, actual_solution) VALUES (?, ?, ?, ?, ?, ?)',
                (session_id, image_id, str(i + 1), status, your_answer, correct_answer)
            )

        conn.commit()
        return redirect(url_for('main.question_entry_v2', session_id=session_id, test_name=test_name))
    except Exception as e:
        conn.rollback()
        current_app.logger.error(f"Error in save_processed_json: {repr(e)}")
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@json_bp.route('/json_upload_v3', methods=['POST'])
def json_upload_v3():
    if not request.json:
        return jsonify({'error': 'No JSON payload received.'}), 400

    processor_v3 = JSONProcessorV3(request.json)
    try:
        # Pass a user_id, for now a default. In a real app, this might come from an API key.
        result = processor_v3.process(user_id=45)
        return jsonify(result), 200
    except ValueError as e:
        current_app.logger.error(f"JSON v3.0 processing error: {e}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        current_app.logger.error(f"Unhandled error during JSON v3.0 processing: {e}")
        return jsonify({'error': 'An internal server error occurred.'}), 500
