import os
import math
import uuid
import base64
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_file, redirect
from werkzeug.utils import secure_filename
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import sqlite3
import fitz
import requests
import json
import io
import zipfile

# Import route strings and constants
from strings import *

# --- NVIDIA NIM Configuration ---
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NIM_API_URL = "https://ai.api.nvidia.com/v1/cv/nvidia/nemoretriever-ocr-v1"
NIM_HEADERS = {
    "Authorization": f"Bearer {NVIDIA_API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
MODEL_MAX_WIDTH = 500
MODEL_MAX_HEIGHT = 500

# Check if NVIDIA API key is set
NVIDIA_NIM_AVAILABLE = bool(NVIDIA_API_KEY)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 * 4096
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['PROCESSED_FOLDER'] = 'processed'
app.config['OUTPUT_FOLDER'] = 'output'
DATABASE = 'database.db'

for folder in [app.config['UPLOAD_FOLDER'], app.config['PROCESSED_FOLDER'], app.config['OUTPUT_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

# --- Database Helper Functions ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def setup_database():
    """Initializes the database and creates/updates tables as needed."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Create sessions table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        original_filename TEXT
    );
    """)

    # Create images table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        image_index INTEGER NOT NULL,
        filename TEXT NOT NULL,
        original_name TEXT NOT NULL,
        processed_filename TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions (id)
    );
    """)

    # Add image_type column to images table if it doesn't exist (for migration)
    try:
        cursor.execute("SELECT image_type FROM images LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating database: Adding 'image_type' column to 'images' table.")
        cursor.execute("ALTER TABLE images ADD COLUMN image_type TEXT DEFAULT 'original'")

    # Add original_filename column to sessions table if it doesn't exist (for migration)
    try:
        cursor.execute("SELECT original_filename FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating database: Adding 'original_filename' column to 'sessions' table.")
        cursor.execute("ALTER TABLE sessions ADD COLUMN original_filename TEXT")

    # Add persist column to sessions table if it doesn't exist (for migration)
    try:
        cursor.execute("SELECT persist FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating database: Adding 'persist' column to 'sessions' table.")
        cursor.execute("ALTER TABLE sessions ADD COLUMN persist INTEGER DEFAULT 0")

    # Create questions table (dropping if exists to ensure schema is correct)
    cursor.execute("DROP TABLE IF EXISTS questions")
    cursor.execute("""
    CREATE TABLE questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        image_id INTEGER NOT NULL, 
        question_number TEXT,
        subject TEXT,
        status TEXT,
        marked_solution TEXT,
        actual_solution TEXT,
        time_taken TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions (id),
        FOREIGN KEY (image_id) REFERENCES images (id)
    );
    """)

    # Create folders table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS folders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        parent_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (parent_id) REFERENCES folders (id) ON DELETE CASCADE
    );
    """)

    # Create generated_pdfs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS generated_pdfs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        subject TEXT NOT NULL,
        tags TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        source_filename TEXT,
        folder_id INTEGER,
        FOREIGN KEY (session_id) REFERENCES sessions (id),
        FOREIGN KEY (folder_id) REFERENCES folders (id) ON DELETE SET NULL
    );
    """)

    # --- Migrations for generated_pdfs table ---
    
    # Add persist column
    try:
        cursor.execute("SELECT persist FROM generated_pdfs LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating database: Adding 'persist' column to 'generated_pdfs' table.")
        cursor.execute("ALTER TABLE generated_pdfs ADD COLUMN persist INTEGER DEFAULT 0")

    # Add folder_id column if it doesn't exist
    try:
        cursor.execute("SELECT folder_id FROM generated_pdfs LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating database: Adding 'folder_id' column to 'generated_pdfs' table.")
        cursor.execute("ALTER TABLE generated_pdfs ADD COLUMN folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL")

    # Migrate from group_name to folders
    try:
        cursor.execute("SELECT group_name FROM generated_pdfs LIMIT 1")
        
        # If group_name exists, migrate data
        print("Migrating data from 'group_name' to 'folders' table.")
        
        # Get all unique group names
        groups = cursor.execute("SELECT DISTINCT group_name FROM generated_pdfs WHERE group_name IS NOT NULL AND group_name != ''").fetchall()
        
        folder_map = {} # Maps group_name to folder_id

        for group in groups:
            group_name = group['group_name']
            parts = group_name.split('/')
            parent_id = None
            current_path = ""
            
            for part in parts:
                current_path = f"{current_path}/{part}" if current_path else part
                
                if current_path not in folder_map:
                    # Check if folder already exists
                    res = cursor.execute("SELECT id FROM folders WHERE name = ? AND parent_id IS ?", (part, parent_id)).fetchone()
                    if res:
                        folder_id = res['id']
                    else:
                        # Insert folder and get its ID
                        cursor.execute("INSERT INTO folders (name, parent_id) VALUES (?, ?)", (part, parent_id))
                        folder_id = cursor.lastrowid
                    
                    folder_map[current_path] = folder_id
                    parent_id = folder_id
                else:
                    parent_id = folder_map[current_path]

            # Update PDFs with the final folder_id for the group_name
            cursor.execute("UPDATE generated_pdfs SET folder_id = ? WHERE group_name = ?", (parent_id, group_name))

        # Once migration is done, we can drop the group_name column
        # Note: Dropping columns in SQLite is tricky. A common way is to recreate the table.
        # For simplicity here, we'll just leave the old column. A more robust migration script would handle this better.
        # A 'manual' step might be to run: ALTER TABLE generated_pdfs DROP COLUMN group_name;
        # But this is not supported in all SQLite versions.
        
    except sqlite3.OperationalError:
        # group_name column doesn't exist, so no migration needed.
        pass

    conn.commit()
    conn.close()

def cleanup_old_data():
    """Removes sessions, files, and PDFs older than 1 day, unless persisted."""
    print("Running cleanup of old data...")
    conn = get_db_connection()
    cutoff = datetime.now() - timedelta(days=1)
    
    # Find old, non-persisted sessions
    old_sessions = conn.execute('SELECT id FROM sessions WHERE created_at < ? AND persist = 0', (cutoff,)).fetchall()
    
    for session in old_sessions:
        session_id = session['id']
        print(f"Deleting old session: {session_id}")
        
        # Find and delete associated files
        images_to_delete = conn.execute('SELECT filename, processed_filename FROM images WHERE session_id = ?', (session_id,)).fetchall()
        for img in images_to_delete:
            if img['filename']:
                try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], img['filename']))
                except OSError: pass
            if img['processed_filename']:
                try: os.remove(os.path.join(app.config['PROCESSED_FOLDER'], img['processed_filename']))
                except OSError: pass

        # Delete records from database
        conn.execute('DELETE FROM questions WHERE session_id = ?', (session_id,))
        conn.execute('DELETE FROM images WHERE session_id = ?', (session_id,))
        conn.execute('DELETE FROM sessions WHERE id = ?', (session_id,))

    # Cleanup old, non-persisted generated PDFs
    old_pdfs = conn.execute('SELECT id, filename FROM generated_pdfs WHERE created_at < ? AND persist = 0', (cutoff,)).fetchall()
    for pdf in old_pdfs:
        pdf_id, pdf_filename = pdf['id'], pdf['filename']
        print(f"Deleting old generated PDF: {pdf_filename}")
        try:
            os.remove(os.path.join(app.config['OUTPUT_FOLDER'], pdf_filename))
        except OSError:
            pass
        conn.execute('DELETE FROM generated_pdfs WHERE id = ?', (pdf_id,))

    # Cleanup old PDF files in the output folder
    for filename in os.listdir(app.config['OUTPUT_FOLDER']):
        file_path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
        file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
        if file_mtime < cutoff:
            print(f"Deleting old PDF: {filename}")
            try: os.remove(file_path)
            except OSError: pass
            
    conn.commit()
    conn.close()
    print("Cleanup finished.")

# --- NVIDIA NIM Helper Functions ---

def resize_image_if_needed(image_path: str) -> bytes:
    """Resizes an image to a maximum of 500x500 pixels and returns bytes."""
    with Image.open(image_path) as image:
        # Always resize to maximum 500x500 to ensure small file size
        MAX_SIZE = 500
        width, height = image.size
        
        # Calculate new dimensions maintaining aspect ratio
        if width > height:
            new_width = min(width, MAX_SIZE)
            new_height = int(height * (new_width / width))
        else:
            new_height = min(height, MAX_SIZE)
            new_width = int(width * (new_height / height))
            
        # Ensure both dimensions are within limits
        if new_width > MAX_SIZE:
            new_width = MAX_SIZE
            new_height = int(height * (new_width / width))
        if new_height > MAX_SIZE:
            new_height = MAX_SIZE
            new_width = int(width * (new_height / height))
        
        # Resize the image
        resized_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Convert to bytes with compression to reduce size
        img_byte_arr = io.BytesIO()
        resized_image.save(img_byte_arr, format='JPEG', quality=85, optimize=True)
        image_bytes = img_byte_arr.getvalue()
        
        # Double-check size and reduce quality further if needed
        base64_size = len(base64.b64encode(image_bytes).decode('utf-8'))
        if base64_size > 180000:
            # Reduce quality to meet size constraint
            quality = max(50, int(85 * (180000 / base64_size)))
            img_byte_arr = io.BytesIO()
            resized_image.save(img_byte_arr, format='JPEG', quality=quality, optimize=True)
            image_bytes = img_byte_arr.getvalue()
            
        return image_bytes

def call_nim_ocr_api(image_bytes: bytes):
    """Calls the NVIDIA NIM API to perform OCR on an image."""
    try:
        if not NVIDIA_API_KEY:
            raise Exception("NVIDIA_API_KEY environment variable not set.")
            
        base64_encoded_data = base64.b64encode(image_bytes)
        base64_string = base64_encoded_data.decode('utf-8')
        
        # Check base64 encoded size (the actual limit for the API)
        if len(base64_string) > 180000:
            raise Exception("Image too large. To upload larger images, use the assets API.")
        
        image_url = f"data:image/png;base64,{base64_string}"
        
        payload = {
            "input": [
                {
                    "type": "image_url",
                    "url": image_url
                }
            ]
        }
        
        response = requests.post(NIM_API_URL, headers=NIM_HEADERS, json=payload, timeout=300)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        if e.response is not None:
            try:
                error_detail = e.response.json().get("error", e.response.text)
            except json.JSONDecodeError:
                error_detail = e.response.text
        raise Exception(f"NIM API Error: {error_detail}")

def extract_question_number_from_ocr_result(ocr_result: dict) -> str:
    """Extracts the question number from the OCR result."""
    try:
        # Handle the new response format from the OCR API
        # The OCR API returns a structured response with text detections
        if "data" in ocr_result and len(ocr_result["data"]) > 0:
            text_detections = ocr_result["data"][0].get("text_detections", [])
            # Extract all text from detections and join them
            content = " ".join([detection["text_prediction"]["text"] for detection in text_detections])
        else:
            content = str(ocr_result)
            
        # More robust extraction - look for a number at the beginning of the text
        # This pattern matches:
        # 1. Numbers at the start of the text (possibly with whitespace)
        # 2. Numbers after "Q" or "Question" keywords
        # 3. Numbers in formats like "1.", "1)", "Q1", "Question 1"
        import re
        
        # Pattern 1: Number at the very beginning
        match = re.search(r'^\s*(\d+)', content)
        if match:
            return match.group(1)
            
        # Pattern 2: Number after "Q" or "Question"
        match = re.search(r'(?:^|\s)(?:[Qq][\.:]?\s*|QUESTION\s+)(\d+)', content, re.IGNORECASE)
        if match:
            return match.group(1)
            
        # Pattern 3: Number with punctuation (e.g., "1.", "1)")
        match = re.search(r'^\s*(\d+)[\.\)]', content)
        if match:
            return match.group(1)
            
        return ""
    except (KeyError, IndexError, TypeError):
        return ""

# --- Font and Image Processing (No changes needed) ---
def get_or_download_font(font_path="arial.ttf", font_size=50):
    if not os.path.exists(font_path):
        try:
            import requests
            response = requests.get("https://github.com/kavin808/arial.ttf/raw/refs/heads/master/arial.ttf", timeout=30)
            response.raise_for_status()
            with open(font_path, 'wb') as f: f.write(response.content)
        except Exception: return ImageFont.load_default()
    try: return ImageFont.truetype(font_path, size=font_size)
    except IOError: return ImageFont.load_default()

def crop_image_perspective(image_path, points):
    if len(points) < 4: return cv2.imread(image_path)
    img = cv2.imread(image_path)
    if img is None: raise ValueError("Could not read the image file.")
    height, width = img.shape[:2]
    def clamp(val): return max(0.0, min(1.0, val))
    src_points = np.array([[clamp(p.get('x', 0.0)) * width, clamp(p.get('y', 0.0)) * height] for p in points[:4]], dtype=np.float32)
    (tl, tr, br, bl) = src_points
    width_top, width_bottom = np.linalg.norm(tr - tl), np.linalg.norm(br - bl)
    max_width = int(max(width_top, width_bottom))
    height_right, height_left = np.linalg.norm(tr - br), np.linalg.norm(tl - bl)
    max_height = int(max(height_right, height_left))
    if max_width == 0 or max_height == 0: return img
    dst_points = np.array([[0, 0], [max_width - 1, 0], [max_width - 1, max_height - 1], [0, max_height - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(src_points, dst_points)
    return cv2.warpPerspective(img, matrix, (max_width, max_height))

def create_a4_pdf_from_images(image_info, base_folder, output_filename, images_per_page, orientation='portrait', grid_rows=None, grid_cols=None):
    if not image_info: return False
    A4_WIDTH_PX, A4_HEIGHT_PX = 4960, 7016
    font_large, font_small = get_or_download_font(font_size=60), get_or_download_font(font_size=45)
    pages, info_chunks = [], [image_info[i:i + images_per_page] for i in range(0, len(image_info), images_per_page)]
    for chunk in info_chunks:
        page_width, page_height = (A4_HEIGHT_PX, A4_WIDTH_PX) if orientation == 'landscape' else (A4_WIDTH_PX, A4_HEIGHT_PX)
        page = Image.new('RGB', (page_width, page_height), 'white')
        draw = ImageDraw.Draw(page)
        
        if grid_rows and grid_cols:
            rows, cols = grid_rows, grid_cols
        else:
            cols, rows = int(math.ceil(math.sqrt(len(chunk)))), int(math.ceil(len(chunk) / int(math.ceil(math.sqrt(len(chunk))))))
        
        cell_width, cell_height = (page_width - 400) // cols, (page_height - 400) // rows
        for i, info in enumerate(chunk):
            col, row = i % cols, i // cols
            cell_x, cell_y = 200 + col * cell_width, 200 + row * cell_height
            try:
                img = None
                if info.get('image_data'):
                    # Handle base64 encoded image data
                    header, encoded = info['image_data'].split(",", 1)
                    image_data = base64.b64decode(encoded)
                    img = Image.open(io.BytesIO(image_data)).convert("RGB")
                elif info.get('processed_filename') or info.get('filename'):
                    # Handle image from file path
                    img_path = os.path.join(base_folder, info.get('processed_filename') or info.get('filename'))
                    img = Image.open(img_path).convert("RGB")

                if img:
                    target_w, target_h = cell_width - 40, cell_height - 170
                    
                    # Calculate new dimensions while maintaining aspect ratio
                    img_ratio = img.width / img.height
                    target_ratio = target_w / target_h
                    
                    if img_ratio > target_ratio:
                        # Image is wider than target area, scale by width
                        new_w = target_w
                        new_h = int(new_w / img_ratio)
                    else:
                        # Image is taller than target area, scale by height
                        new_h = target_h
                        new_w = int(new_h * img_ratio)

                    print(f"Original image size: {img.width}x{img.height}, Resized to: {new_w}x{new_h}")
                    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    
                    page.paste(img, (cell_x + 20, cell_y + 150))

                draw.text((cell_x + 20, cell_y + 20), f"Q: {info['question_number']}", fill="black", font=font_large)
                info_text = f"Status: {info['status']} | Marked: {info['marked_solution']} | Correct: {info['actual_solution']}"
                draw.text((cell_x + 20, cell_y + 90), info_text, fill="darkgray", font=font_small)
            except Exception as e:
                print(f"Error processing image for PDF: {e}")
        pages.append(page)
    if pages:
        pages[0].save(os.path.join(app.config['OUTPUT_FOLDER'], output_filename), "PDF", resolution=900.0, save_all=True, append_images=pages[1:])
        return True
    return False

# --- Flask Routes (Modified for Database) ---


@app.route(ROUTE_INDEX_V2)
def index_v2():
    """Renders the new PDF upload page."""
    return render_template('indexv2.html')

@app.route(ROUTE_IMAGES)
def image_upload():
    """Renders the multiple image upload page."""
    return render_template('image_upload.html')

@app.route(ROUTE_UPLOAD_PDF, methods=[METHOD_POST])
def upload_pdf():
    """Handles PDF upload, splits it into images, and creates a session."""
    session_id = str(uuid.uuid4())
    if 'pdf' not in request.files:
        return jsonify({'error': 'No PDF file part'}), 400
    file = request.files['pdf']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file and file.filename.lower().endswith('.pdf'):
        conn = get_db_connection()
        # Store session with original filename
        conn.execute('INSERT INTO sessions (id, original_filename) VALUES (?, ?)', (session_id, secure_filename(file.filename)))
        
        pdf_filename = f"{session_id}_{secure_filename(file.filename)}"
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename)
        file.save(pdf_path)

        doc = fitz.open(pdf_path)
        page_files = []
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=900)
            page_filename = f"{session_id}_page_{i}.png"
            page_path = os.path.join(app.config['UPLOAD_FOLDER'], page_filename)
            pix.save(page_path)
            
            # Save page as an "image" in the database
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


@app.route(ROUTE_UPLOAD_IMAGES, methods=[METHOD_POST])
def upload_images():
    """Handles multiple image upload and creates a session."""
    session_id = str(uuid.uuid4())
    
    if 'images' not in request.files:
        return jsonify({'error': 'No image files part'}), 400
    
    files = request.files.getlist('images')
    
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No selected files'}), 400

    # Check if all files are valid images
    valid_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp'}
    for file in files:
        if not file or not any(file.filename.lower().endswith(ext) for ext in valid_extensions):
            return jsonify({'error': 'Invalid file type. Please upload only image files (PNG, JPG, JPEG, GIF, BMP)'}), 400

    conn = get_db_connection()
    # For multiple images, we'll use a generic name or the first image name
    original_filename = f"{len(files)} images" if len(files) > 1 else secure_filename(files[0].filename) if files else "images"
    conn.execute('INSERT INTO sessions (id, original_filename) VALUES (?, ?)', (session_id, original_filename))
    
    uploaded_files = []
    for i, file in enumerate(files):
        if file and file.filename != '':
            filename = f"{session_id}_{secure_filename(file.filename)}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            # Save image as an "image" in the database with image_type 'original'
            conn.execute(
                'INSERT INTO images (session_id, image_index, filename, original_name, image_type) VALUES (?, ?, ?, ?, ?)',
                (session_id, i, filename, secure_filename(file.filename), 'original')
            )
            uploaded_files.append({'filename': filename, 'original_name': secure_filename(file.filename), 'index': i})
    
    conn.commit()
    conn.close()
    
    return jsonify({'session_id': session_id, 'files': uploaded_files})

@app.route('/cropv2/<session_id>/<int:image_index>')
def crop_interface_v2(session_id, image_index):
    """Renders the new multi-box cropping interface for a PDF page or image."""
    conn = get_db_connection()
    
    # Get the specific page/image to crop
    image_info = conn.execute(
        "SELECT * FROM images WHERE session_id = ? AND image_index = ? AND image_type = 'original'",
        (session_id, image_index)
    ).fetchone()
    
    if not image_info:
        conn.close()
        return "Original page/image not found for this session and index.", 404

    # Get the total count of original pages/images for navigation
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

@app.route(ROUTE_PROCESS_CROP_V2, methods=[METHOD_POST])
def process_crop_v2():
    """Processes multiple crop boxes from a single page and saves them as new 'cropped' images."""
    data = request.json
    session_id, page_index, boxes, image_data_url = data['session_id'], data['image_index'], data['boxes'], data.get('imageData')

    conn = get_db_connection()
    # Find the original page to get its filename
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
        temp_path = os.path.join(app.config['PROCESSED_FOLDER'], temp_filename)
        with open(temp_path, "wb") as f: f.write(image_data)

        # Delete existing cropped images for this specific page
        # We need to find all cropped images that were created from this original page
        existing_cropped = conn.execute(
            "SELECT id, processed_filename FROM images WHERE session_id = ? AND filename = ? AND image_type = 'cropped'",
            (session_id, page_info['filename'])
        ).fetchall()
        
        # Delete the actual image files
        for cropped_img in existing_cropped:
            try:
                if cropped_img['processed_filename']:
                    os.remove(os.path.join(app.config['PROCESSED_FOLDER'], cropped_img['processed_filename']))
            except OSError:
                pass  # File might not exist, that's okay
        
        # Delete the database records for these cropped images
        conn.execute(
            "DELETE FROM images WHERE session_id = ? AND filename = ? AND image_type = 'cropped'",
            (session_id, page_info['filename'])
        )
        
        # Delete any associated questions for these cropped images
        for cropped_img in existing_cropped:
            conn.execute(
                "DELETE FROM questions WHERE session_id = ? AND image_id = ?",
                (session_id, cropped_img['id'])
            )

        # Determine the starting index for the new cropped images
        max_index_result = conn.execute('SELECT MAX(image_index) FROM images WHERE session_id = ?', (session_id,)).fetchone()
        next_index = (max_index_result[0] if max_index_result[0] is not None else -1) + 1
        
        images_to_insert = []
        for i, box in enumerate(boxes):
            points = [
                {'x': box['x'], 'y': box['y']},
                {'x': box['x'] + box['w'], 'y': box['y']},
                {'x': box['x'] + box['w'], 'y': box['y'] + box['h']},
                {'x': box['x'], 'y': box['y'] + box['h']}
            ]
            
            cropped_img = crop_image_perspective(temp_path, points)
            
            crop_filename = f"processed_{session_id}_page{page_index}_crop{i}.jpg"
            crop_path = os.path.join(app.config['PROCESSED_FOLDER'], crop_filename)
            cv2.imwrite(crop_path, cropped_img)

            images_to_insert.append((
                session_id,
                next_index + i,
                page_info['filename'], # Keep original filename for reference
                f"Page {page_index + 1} - Q{i + 1}",
                crop_filename,
                'cropped' # The new image type
            ))
        
        # Use a single transaction to insert all new cropped images
        if images_to_insert:
            conn.executemany(
                'INSERT INTO images (session_id, image_index, filename, original_name, processed_filename, image_type) VALUES (?, ?, ?, ?, ?, ?)',
                images_to_insert
            )
        
        conn.commit()
        conn.close()
        os.remove(temp_path)
        
        return jsonify({'success': True, 'processed_count': len(boxes)})

    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"V2 Processing error: {e}")
        return jsonify({'error': f'Processing failed: {str(e)}'}), 500


# ADD THIS NEW ROUTE TO YOUR EXISTING bak.app.py FILE

@app.route('/question_entry_v2/<session_id>')
def question_entry_v2(session_id):
    """Renders the question entry page for the V2 (PDF) workflow."""
    conn = get_db_connection()
    # This query is the key: it only selects images that were created by the cropping process.
    images = conn.execute(
        "SELECT * FROM images WHERE session_id = ? AND image_type = 'cropped' ORDER BY id", 
        (session_id,)
    ).fetchall()
    conn.close()
    
    if not images:
        return "No questions were created from the PDF. Please go back and draw crop boxes.", 404
        
    return render_template('question_entry_v2.html', 
                          session_id=session_id, 
                          images=[dict(img) for img in images],
                          nvidia_nim_available=NVIDIA_NIM_AVAILABLE)


@app.route(ROUTE_DASHBOARD)
def dashboard():
    """Renders the dashboard for managing database state."""
    conn = get_db_connection()
    
    # Get all sessions with their creation dates and original filenames
    sessions = conn.execute("""
        SELECT s.id, s.created_at, s.original_filename, s.persist,
               COUNT(CASE WHEN i.image_type = 'original' THEN 1 END) as page_count,
               COUNT(CASE WHEN i.image_type = 'cropped' THEN 1 END) as question_count
        FROM sessions s
        LEFT JOIN images i ON s.id = i.session_id
        GROUP BY s.id, s.created_at, s.original_filename, s.persist
        ORDER BY s.created_at DESC
    """).fetchall()
    
    # Process sessions for display
    processed_sessions = []
    for session in sessions:
        session_dict = dict(session)
        # Use the original filename stored in the database, or fallback to "Unknown"
        session_dict['pdf_name'] = session_dict['original_filename'] or 'Unknown'
        processed_sessions.append(session_dict)
    
    conn.close()
    
    return render_template('dashboard.html', sessions=processed_sessions)


@app.route('/delete_session/<session_id>', methods=[METHOD_DELETE])
def delete_session(session_id):
    """Deletes a session and all associated files and records."""
    try:
        conn = get_db_connection()
        
        # Find and delete associated files
        images_to_delete = conn.execute('SELECT filename, processed_filename FROM images WHERE session_id = ?', (session_id,)).fetchall()
        for img in images_to_delete:
            if img['filename']:
                try: os.remove(os.path.join(app.config['UPLOAD_FOLDER'], img['filename']))
                except OSError: pass
            if img['processed_filename']:
                try: os.remove(os.path.join(app.config['PROCESSED_FOLDER'], img['processed_filename']))
                except OSError: pass

        # Delete records from database
        conn.execute('DELETE FROM questions WHERE session_id = ?', (session_id,))
        conn.execute('DELETE FROM images WHERE session_id = ?', (session_id,))
        conn.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/toggle_persist/<session_id>', methods=[METHOD_POST])
def toggle_persist(session_id):
    """Toggles the persistence status of a session."""
    try:
        conn = get_db_connection()
        
        # Get current persist status
        current_status = conn.execute('SELECT persist FROM sessions WHERE id = ?', (session_id,)).fetchone()
        
        if not current_status:
            conn.close()
            return jsonify({'error': 'Session not found'}), 404
            
        # Toggle the status (0 becomes 1, 1 becomes 0)
        new_status = 1 - current_status['persist']
        
        conn.execute('UPDATE sessions SET persist = ? WHERE id = ?', (new_status, session_id))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'status': 'persisted' if new_status == 1 else 'not_persisted'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/delete_question/<image_id>', methods=[METHOD_DELETE])
def delete_question(image_id):
    """Deletes a single question (cropped image) and its associated files."""
    try:
        conn = get_db_connection()
        
        # Get the image info
        image_info = conn.execute(
            'SELECT session_id, filename, processed_filename FROM images WHERE id = ?', 
            (image_id,)
        ).fetchone()
        
        if not image_info:
            conn.close()
            return jsonify({'error': 'Question not found'}), 404
            
        # Delete associated files
        #if image_info['filename']:
        #    try: 
        #        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], image_info['filename']))
        #    except OSError: 
        #        pass
        #if image_info['processed_filename']:
        #    try: 
        #        os.remove(os.path.join(app.config['PROCESSED_FOLDER'], image_info['processed_filename']))
        #    except OSError: 
        #        pass

        # Delete records from database
        conn.execute('DELETE FROM questions WHERE image_id = ?', (image_id,))
        conn.execute('DELETE FROM images WHERE id = ?', (image_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route(ROUTE_SAVE_QUESTIONS, methods=[METHOD_POST])
def save_questions():
    data = request.json
    session_id, questions = data['session_id'], data['questions']
    
    conn = get_db_connection()
    # Delete old questions for this session to prevent duplicates
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

@app.route(ROUTE_EXTRACT_QUESTION_NUMBER, methods=[METHOD_POST])
def extract_question_number():
    """Extracts question number from an image using NVIDIA NIM OCR."""
    if not NVIDIA_NIM_AVAILABLE:
        return jsonify({'error': 'NVIDIA NIM feature is not available. Please set the NVIDIA_API_KEY environment variable.'}), 400
    
    data = request.json
    image_id = data.get('image_id')
    
    if not image_id:
        return jsonify({'error': 'Missing image_id parameter'}), 400
    
    try:
        # Get image info from database
        conn = get_db_connection()
        image_info = conn.execute(
            'SELECT processed_filename FROM images WHERE id = ?', 
            (image_id,)
        ).fetchone()
        conn.close()
        
        if not image_info or not image_info['processed_filename']:
            return jsonify({'error': 'Image not found or not processed'}), 404
            
        # Construct image path
        image_path = os.path.join(app.config['PROCESSED_FOLDER'], image_info['processed_filename'])
        if not os.path.exists(image_path):
            return jsonify({'error': 'Image file not found on disk'}), 404
            
        # Resize image if needed and convert to bytes
        image_bytes = resize_image_if_needed(image_path)
        
        # Call NVIDIA NIM API for OCR
        ocr_result = call_nim_ocr_api(image_bytes)
        
        # Extract question number from OCR result
        question_number = extract_question_number_from_ocr_result(ocr_result)
        
        return jsonify({
            'success': True, 
            'question_number': question_number,
            'image_id': image_id
        })
        
    except Exception as e:
        return jsonify({'error': f'Failed to extract question number: {str(e)}'}), 500


@app.route(ROUTE_EXTRACT_ALL_QUESTION_NUMBERS, methods=[METHOD_POST])
def extract_all_question_numbers():
    """Extracts question numbers from all images in a session using NVIDIA NIM OCR."""
    if not NVIDIA_NIM_AVAILABLE:
        return jsonify({'error': 'NVIDIA NIM feature is not available. Please set the NVIDIA_API_KEY environment variable.'}), 400
    
    data = request.json
    session_id = data.get('session_id')
    
    if not session_id:
        return jsonify({'error': 'Missing session_id parameter'}), 400
    
    try:
        # Get all cropped images from the session
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
        
        # Process each image (with a limit to prevent overwhelming the API)
        MAX_CONCURRENT_REQUESTS = 5
        processed_count = 0
        
        for image in images:
            # Check if we've reached the limit
            if processed_count >= MAX_CONCURRENT_REQUESTS:
                # Add a small delay before processing more
                import time
                time.sleep(1)
                processed_count = 0
            
            try:
                image_id = image['id']
                processed_filename = image['processed_filename']
                
                if not processed_filename:
                    errors.append({'image_id': image_id, 'error': 'Image not processed'})
                    continue
                
                # Construct image path
                image_path = os.path.join(app.config['PROCESSED_FOLDER'], processed_filename)
                if not os.path.exists(image_path):
                    errors.append({'image_id': image_id, 'error': 'Image file not found on disk'})
                    continue
                
                # Resize image if needed and convert to bytes
                image_bytes = resize_image_if_needed(image_path)
                
                # Call NVIDIA NIM API for OCR
                ocr_result = call_nim_ocr_api(image_bytes)
                
                # Extract question number from OCR result
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

@app.route(ROUTE_GENERATE_PDF, methods=[METHOD_POST])
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

    # Add miscellaneous questions from the request
    miscellaneous_questions = data.get('miscellaneous_questions', [])
    all_questions.extend(miscellaneous_questions)

    filter_type = data.get('filter_type', 'all')
    filtered_questions = [
        q for q in all_questions if filter_type == 'all' or q['status'] == filter_type
    ]

    if not filtered_questions: return jsonify({'error': 'No questions match the filter criteria'}), 400
    
    pdf_filename = f"{secure_filename(data.get('pdf_name', 'analysis'))}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    images_per_page = int(data.get('images_per_page', 4))
    orientation = data.get('orientation', 'portrait') # default to portrait
    grid_rows = data.get('grid_rows')
    grid_cols = data.get('grid_cols')

    if create_a4_pdf_from_images(filtered_questions, app.config['PROCESSED_FOLDER'], pdf_filename, images_per_page, orientation, grid_rows, grid_cols):
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

@app.route('/download/<filename>')
def download_file(filename):
    return send_file(os.path.join(app.config['OUTPUT_FOLDER'], filename), as_attachment=True)

@app.route('/view_pdf/<filename>')
def view_pdf(filename):
    return send_file(os.path.join(app.config['OUTPUT_FOLDER'], filename), as_attachment=False)

@app.route('/image/<folder>/<filename>')
def serve_image(folder, filename):
    folder_path = app.config.get(f'{folder.upper()}_FOLDER')
    if not folder_path or not os.path.exists(os.path.join(folder_path, filename)):
        return "Not found", 404
    return send_file(os.path.join(folder_path, filename))

@app.route(ROUTE_INDEX)
def index():
    """Renders the main page with options for PDF or image upload."""
    return render_template('main.html')

def get_folder_tree():
    conn = get_db_connection()
    folders = conn.execute('SELECT id, name, parent_id FROM folders ORDER BY name').fetchall()
    conn.close()
    
    folder_map = {f['id']: dict(f) for f in folders}
    tree = []
    
    for folder_id, folder in folder_map.items():
        if folder['parent_id']:
            parent = folder_map.get(folder['parent_id'])
            if parent:
                if 'children' not in parent:
                    parent['children'] = []
                parent['children'].append(folder)
        else:
            tree.append(folder)
            
    return tree

@app.route('/pdf_manager')
@app.route('/pdf_manager/browse/<path:folder_path>')
def pdf_manager(folder_path=''):
    """Renders the PDF manager with a hierarchical folder structure."""
    conn = get_db_connection()
    view_mode = request.args.get('view', 'default')

    if view_mode == 'all':
        pdfs = conn.execute('SELECT * FROM generated_pdfs ORDER BY created_at DESC').fetchall()
        subfolders = []
        breadcrumbs = []
        current_path = ''
        all_view = True
        folder_id = None
    else:
        all_view = False
        folder_id = None
        breadcrumbs = []
        if folder_path:
            parts = folder_path.split('/')
            parent_id = None
            for i, part in enumerate(parts):
                res = conn.execute("SELECT id FROM folders WHERE name = ? AND (parent_id = ? OR (? IS NULL AND parent_id IS NULL))", (part, parent_id, parent_id)).fetchone()
                if not res:
                    # Folder not found, maybe redirect to home of pdf_manager
                    return redirect('/pdf_manager')
                parent_id = res['id']
                breadcrumbs.append({'name': part, 'path': '/'.join(parts[:i+1])})
            folder_id = parent_id

        # Get PDFs in the current folder
        if folder_id:
            pdfs = conn.execute('SELECT * FROM generated_pdfs WHERE folder_id = ? ORDER BY created_at DESC', (folder_id,)).fetchall()
        else:
            pdfs = conn.execute('SELECT * FROM generated_pdfs WHERE folder_id IS NULL ORDER BY created_at DESC').fetchall()
        
        # Get subfolders of the current folder
        if folder_id:
            subfolders = conn.execute('SELECT * FROM folders WHERE parent_id = ? ORDER BY name', (folder_id,)).fetchall()
        else:
            subfolders = conn.execute('SELECT * FROM folders WHERE parent_id IS NULL ORDER BY name').fetchall()

    folder_tree = get_folder_tree()
    conn.close()
    
    return render_template('pdf_manager.html', 
                           pdfs=[dict(row) for row in pdfs],
                           subfolders=[dict(row) for row in subfolders],
                           current_folder_id=folder_id,
                           breadcrumbs=breadcrumbs,
                           all_view=all_view,
                           folder_tree=folder_tree)


@app.route('/get_pdf_details/<int:pdf_id>')
def get_pdf_details(pdf_id):
    """Fetches details for a specific PDF."""
    conn = get_db_connection()
    pdf = conn.execute('SELECT * FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
    conn.close()
    if pdf:
        return jsonify(dict(pdf))
    return jsonify({'error': 'PDF not found'}), 404


@app.route('/update_pdf_details/<int:pdf_id>', methods=[METHOD_POST])
def update_pdf_details(pdf_id):
    """Updates the details of a specific PDF."""
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

@app.route('/rename_item', methods=[METHOD_POST])
def rename_item():
    data = request.json
    item_type = data.get('item_type')
    item_id = data.get('item_id')
    new_name = data.get('new_name')

    if not all([item_type, item_id, new_name]):
        return jsonify({'error': 'Missing parameters'}), 400

    conn = get_db_connection()
    if item_type == 'folder':
        conn.execute('UPDATE folders SET name = ? WHERE id = ?', (new_name, item_id))
    elif item_type == 'pdf':
        conn.execute('UPDATE generated_pdfs SET subject = ? WHERE id = ?', (new_name, item_id))
    else:
        conn.close()
        return jsonify({'error': 'Invalid item type'}), 400

    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/delete_folder/<int:folder_id>', methods=[METHOD_DELETE])
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
        try:
            os.remove(os.path.join(app.config['OUTPUT_FOLDER'], pdf['filename']))
        except OSError:
            pass
    
    conn.execute(f'DELETE FROM generated_pdfs WHERE folder_id IN ({placeholders})', folder_ids_to_delete)
    conn.execute(f'DELETE FROM folders WHERE id IN ({placeholders})', folder_ids_to_delete)
    
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/delete_generated_pdf/<int:pdf_id>', methods=[METHOD_DELETE])
def delete_generated_pdf(pdf_id):
    """Deletes a generated PDF and its record."""
    try:
        conn = get_db_connection()
        pdf_info = conn.execute('SELECT filename FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
        if pdf_info:
            try:
                os.remove(os.path.join(app.config['OUTPUT_FOLDER'], pdf_info['filename']))
            except OSError:
                pass
            conn.execute('DELETE FROM generated_pdfs WHERE id = ?', (pdf_id,))
            conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/toggle_persist_generated_pdf/<int:pdf_id>', methods=[METHOD_POST])
def toggle_persist_generated_pdf(pdf_id):
    """Toggles the persistence status of a generated PDF."""
    try:
        conn = get_db_connection()
        current_status = conn.execute('SELECT persist FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
        if not current_status:
            conn.close()
            return jsonify({'error': 'PDF not found'}), 404
        new_status = 1 - current_status['persist']
        conn.execute('UPDATE generated_pdfs SET persist = ? WHERE id = ?', (new_status, pdf_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'status': 'persisted' if new_status == 1 else 'not_persisted'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bulk_delete_pdfs', methods=[METHOD_POST])
def bulk_delete_pdfs():
    """Deletes multiple PDFs at once."""
    data = request.json
    pdf_ids = data.get('ids', [])
    if not pdf_ids:
        return jsonify({'error': 'No PDF IDs provided'}), 400
    try:
        conn = get_db_connection()
        for pdf_id in pdf_ids:
            pdf_info = conn.execute('SELECT filename FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
            if pdf_info:
                try:
                    os.remove(os.path.join(app.config['OUTPUT_FOLDER'], pdf_info['filename']))
                except OSError:
                    pass
                conn.execute('DELETE FROM generated_pdfs WHERE id = ?', (pdf_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/bulk_toggle_persist', methods=[METHOD_POST])
def bulk_toggle_persist():
    """Toggles the persistence status for multiple PDFs."""
    data = request.json
    pdf_ids = data.get('ids', [])
    if not pdf_ids:
        return jsonify({'error': 'No PDF IDs provided'}), 400
    try:
        conn = get_db_connection()
        for pdf_id in pdf_ids:
            current_status = conn.execute('SELECT persist FROM generated_pdfs WHERE id = ?', (pdf_id,)).fetchone()
            if current_status:
                new_status = 1 - current_status['persist']
                conn.execute('UPDATE generated_pdfs SET persist = ? WHERE id = ?', (new_status, pdf_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/create_folder', methods=[METHOD_POST])
def create_folder():
    """Creates a new folder in the database."""
    data = request.json
    name = data.get('new_folder_name')
    parent_id = data.get('parent_id')
    
    # Basic validation
    if not name:
        return jsonify({'error': 'Folder name is required'}), 400

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

@app.route('/bulk_move_pdfs', methods=[METHOD_POST])
def bulk_move_pdfs():
    """Moves multiple PDFs to a specified folder path."""
    data = request.json
    pdf_ids = data.get('ids', [])
    target_folder_id = data.get('target_folder_id')

    if not pdf_ids:
        return jsonify({'error': 'No PDF IDs provided'}), 400

    try:
        conn = get_db_connection()
        placeholders = ', '.join('?' * len(pdf_ids))
        conn.execute(
            f'UPDATE generated_pdfs SET folder_id = ? WHERE id IN ({placeholders})',
            (target_folder_id, *pdf_ids)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/upload_final_pdf')
def upload_final_pdf():
    return render_template('upload_final_pdf.html')

@app.route('/handle_final_pdf_upload', methods=[METHOD_POST])
def handle_final_pdf_upload():
    if 'pdf' not in request.files:
        return 'No PDF file part', 400
    file = request.files['pdf']
    if file.filename == '':
        return 'No selected file', 400

    subject = request.form.get('subject')
    if not subject:
        return 'Subject is required', 400

    if file and file.filename.lower().endswith('.pdf'):
        session_id = str(uuid.uuid4())
        original_filename = secure_filename(file.filename)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO sessions (id, original_filename) VALUES (?, ?)',
                       (session_id, original_filename))
        
        output_filename = f"{session_id}_{original_filename}"
        output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
        file.save(output_path)

        tags = request.form.get('tags')
        notes = request.form.get('notes')

        cursor.execute(
            'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename) VALUES (?, ?, ?, ?, ?, ?)',
            (session_id, output_filename, subject, tags, notes, original_filename)
        )
        conn.commit()
        conn.close()
        return redirect('/pdf_manager')
    else:
        return 'Invalid file type', 400

if __name__ == '__main__':
    setup_database()
    cleanup_old_data()
    app.run(debug=False, port=1302, host='0.0.0.0')
