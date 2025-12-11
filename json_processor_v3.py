import json
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from jsonschema import validate, ValidationError
import uuid
from flask import current_app, url_for
from werkzeug.utils import secure_filename
import sqlite3 # Import sqlite3
import sys

# Ensure current directory is in Python path for local imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database import get_db_connection
from utils import create_a4_pdf_from_images


# JSON v3.0 Schema for validation
JSON_V3_SCHEMA = {
    "type": "object",
    "properties": {
        "version": {"type": "string", "const": "3.0"},
        "source": {"type": "string"},
        "test_name": {"type": "string"},
        "test_id": {"type": "string"},
        "test_mapping_id": {"type": "string"},
        "metadata": {"type": "object"},
        "config": {
            "type": "object",
            "properties": {
                "statuses_to_include": {"type": "array", "items": {"type": "string"}},
                "layout": {
                    "type": "object",
                    "properties": {
                        "images_per_page": {"type": "integer"},
                        "orientation": {"type": "string"}
                    },
                    "required": ["images_per_page", "orientation"]
                }
            },
            "required": ["statuses_to_include", "layout"]
        },
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_number": {"type": "string"},
                    "image_url": {"type": "string", "format": "uri"},
                    "status": {"type": "string"},
                    "marked_solution": {"type": "string"},
                    "correct_solution": {"type": "string"},
                    "subject": {"type": "string"},
                    "chapter": {"type": "string"},
                    "topic": {"type": "string"},
                    "time_taken": {"type": "integer"}
                },
                "required": ["question_number", "image_url", "status", "marked_solution", "correct_solution", "subject", "time_taken"]
            }
        },
        "view": {"type": "boolean"}
    },
    "required": ["version", "source", "test_name", "test_id", "test_mapping_id", "config", "questions", "view"]
}

class JSONProcessorV3:
    def __init__(self, data=None):
        self.data = data

    def validate(self):
        """Validates the JSON data against the v3.0 schema."""
        try:
            validate(instance=self.data, schema=JSON_V3_SCHEMA)
            return True
        except ValidationError as e:
            raise ValueError(f"Schema validation failed: {e.message}")

    def download_image_from_url(self, url, save_path, timeout=30):
        """Downloads an image from a URL and saves it to a path."""
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            with open(save_path, 'wb') as f:
                f.write(response.content)
            return save_path
        except requests.exceptions.RequestException as e:
            print(f"Error downloading image from {url}: {e}") # Keep print for tests
            if current_app:
                current_app.logger.error(f"Error downloading image from {url}: {e}")
            return None

    def download_images_parallel(self, questions, output_dir, session_id, max_workers=10):
        """Downloads all images in parallel and returns a map of question number to local path."""
        image_paths = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_question = {
                executor.submit(
                    self.download_image_from_url,
                    q['image_url'],
                    os.path.join(output_dir, f"{session_id}_q_{q['question_number']}.png")
                ): q for q in questions if q.get('image_url')
            }
            
            for future in as_completed(future_to_question):
                question = future_to_question[future]
                url = question['image_url']
                try:
                    path = future.result()
                    if path:
                        image_paths[question['question_number']] = path
                        current_app.logger.info(f"Successfully downloaded image from {url}")
                    else:
                        current_app.logger.error(f"Failed to download image from {url}")
                except Exception as e:
                    current_app.logger.error(f"Error processing image for question {question.get('question_number')} from {url}: {e}")
        return image_paths

    def process(self, user_id=1): # Default user_id for now, replace with actual user
        """Main processing logic for the v3.0 payload, including DB insertion and PDF generation."""
        if not self.data:
            raise ValueError("No data provided to process.")

        current_app.logger.info("Starting processing of JSON v3.0 payload.")
        current_app.logger.info(f"Test Name: {self.data.get('test_name')}")
        current_app.logger.info(f"Test ID: {self.data.get('test_id')}")
        current_app.logger.info(f"Metadata: {self.data.get('metadata')}")

        if not self.validate():
            raise ValueError("Schema validation failed.")
        
        conn = get_db_connection()
        try:
            test_name = self.data['test_name']
            test_id = self.data['test_id']
            test_mapping_id = self.data['test_mapping_id']
            questions_payload = self.data['questions']
            view_mode = self.data.get('view', False)
            metadata = json.dumps(self.data.get('metadata', {})) # Store metadata as JSON string
            
            config = self.data.get('config', {})
            layout = config.get('layout', {})
            images_per_page = layout.get('images_per_page', 4)
            orientation = layout.get('orientation', 'portrait')
            
            session_id = str(uuid.uuid4())
            original_filename = f"{test_name}.json" # Name of the JSON file that was uploaded

            conn.execute(
                'INSERT INTO sessions (id, original_filename, user_id, test_id, test_mapping_id, source, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (session_id, original_filename, user_id, test_id, test_mapping_id, self.data.get('source', 'manual'), metadata)
            )
            
            processed_folder = current_app.config.get('PROCESSED_FOLDER', 'processed')
            os.makedirs(processed_folder, exist_ok=True)
            
            current_app.logger.info(f"Downloading images for test {test_id} to {processed_folder}")
            image_path_map = self.download_images_parallel(questions_payload, processed_folder, session_id)
            
            image_records = []
            question_records = []

            for i, q_data in enumerate(questions_payload):
                question_number = q_data['question_number']
                
                # Check if image was downloaded
                processed_filename = None
                local_image_path = image_path_map.get(question_number)
                if local_image_path:
                    processed_filename = os.path.basename(local_image_path)
                
                # Insert into images table
                image_insert_result = conn.execute(
                    'INSERT INTO images (session_id, image_index, filename, original_name, processed_filename, image_type) VALUES (?, ?, ?, ?, ?, ?)',
                    (session_id, i + 1, q_data.get('image_url', ''), f"Question {question_number}", processed_filename, 'cropped' if processed_filename else 'original_url_only')
                )
                image_id = image_insert_result.lastrowid
                
                # Insert into questions table
                question_records.append((
                    session_id, image_id, question_number, q_data['status'],
                    q_data['marked_solution'], q_data['correct_solution'],
                    q_data.get('subject'), q_data.get('chapter'), q_data.get('topic'), q_data.get('time_taken')
                ))
            
            conn.executemany(
                'INSERT INTO questions (session_id, image_id, question_number, status, marked_solution, actual_solution, subject, chapter, topic, time_taken) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                question_records
            )
            
            conn.commit()

            response_data = {
                "status": "success",
                "message": "JSON v3.0 processed successfully."
            }

            if view_mode:
                query = "SELECT q.*, i.processed_filename FROM questions q JOIN images i ON q.image_id = i.id WHERE q.session_id = ? ORDER BY i.id"
                all_questions = [dict(row) for row in conn.execute(query, (session_id,)).fetchall()]
                
                if not all_questions:
                    conn.rollback()
                    raise ValueError('No questions found for PDF generation.')

                pdf_output_folder = current_app.config.get('OUTPUT_FOLDER', 'output')
                os.makedirs(pdf_output_folder, exist_ok=True)
                
                pdf_filename = f"{secure_filename(test_name)}_{session_id[:8]}.pdf"
                
                create_a4_pdf_from_images(
                    image_info=all_questions, base_folder=processed_folder, output_filename=pdf_filename,
                    images_per_page=images_per_page, output_folder=pdf_output_folder,
                    orientation=orientation
                )
                
                conn.execute(
                    'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename, user_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (session_id, pdf_filename, test_name, test_mapping_id, 'Generated automatically via JSON v3.0 upload.', original_filename, user_id)
                )
                conn.commit()
                response_data['view_url'] = url_for('main.view_pdf', filename=pdf_filename, _external=True)
                response_data['message'] = "PDF auto-generated and saved."
            else:
                response_data['edit_url'] = url_for('main.question_entry_v2', session_id=session_id, test_name=test_name, _external=True)
                response_data['message'] = "Session created for manual review."
            
            return response_data

        except ValueError as e:
            if conn:
                conn.rollback()
            current_app.logger.error(f"JSON v3.0 processing error: {e}")
            raise # Re-raise to be caught by the endpoint
        except sqlite3.Error as e:
            if conn:
                conn.rollback()
            current_app.logger.error(f"Database error during JSON v3.0 processing: {e}")
            raise ValueError(f"Database error: {e}")
        except Exception as e:
            if conn:
                conn.rollback()
            current_app.logger.error(f"Unhandled error during JSON v3.0 processing: {e}")
            raise ValueError(f"An unexpected error occurred: {e}")
        finally:
            if conn:
                conn.close()

