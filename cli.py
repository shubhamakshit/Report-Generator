import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta

import click
import fitz  # PyMuPDF
import requests
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from werkzeug.utils import secure_filename

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
DATABASE = os.path.join(SCRIPT_DIR, 'database.db')
UPLOAD_FOLDER = os.path.join(SCRIPT_DIR, 'uploads')
PROCESSED_FOLDER = os.path.join(SCRIPT_DIR, 'processed')
OUTPUT_FOLDER = os.path.join(SCRIPT_DIR, 'output')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


# --- Database Helper Function ---
def get_db_connection():
    """Establishes a connection to the SQLite database."""
    try:
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        click.secho(f"Database connection error: {e}", fg="red")
        raise click.Abort()


# --- Core Logic Functions (mirrored from app.py) ---
def setup_database_cli():
    """Initializes the database and creates/updates tables as needed."""
    conn = get_db_connection()
    cursor = conn.cursor()
    click.echo("Creating/updating tables...")

    cursor.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, original_filename TEXT, persist INTEGER DEFAULT 0);")
    cursor.execute("CREATE TABLE IF NOT EXISTS images (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, image_index INTEGER NOT NULL, filename TEXT NOT NULL, original_name TEXT NOT NULL, processed_filename TEXT, image_type TEXT DEFAULT 'original', FOREIGN KEY (session_id) REFERENCES sessions (id));")
    cursor.execute("CREATE TABLE IF NOT EXISTS questions (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, image_id INTEGER NOT NULL, question_number TEXT, subject TEXT, status TEXT, marked_solution TEXT, actual_solution TEXT, time_taken TEXT, FOREIGN KEY (session_id) REFERENCES sessions (id), FOREIGN KEY (image_id) REFERENCES images (id));")
    cursor.execute("CREATE TABLE IF NOT EXISTS folders (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, parent_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (parent_id) REFERENCES folders (id) ON DELETE CASCADE);")
    cursor.execute("CREATE TABLE IF NOT EXISTS generated_pdfs (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, filename TEXT NOT NULL, subject TEXT NOT NULL, tags TEXT, notes TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, source_filename TEXT, folder_id INTEGER, persist INTEGER DEFAULT 0, FOREIGN KEY (session_id) REFERENCES sessions (id), FOREIGN KEY (folder_id) REFERENCES folders (id) ON DELETE SET NULL);")
    cursor.execute("CREATE TABLE IF NOT EXISTS neetprep_questions (id TEXT PRIMARY KEY, question_text TEXT, options TEXT, correct_answer_index INTEGER, level TEXT, topic TEXT, subject TEXT, last_fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")
    cursor.execute("CREATE TABLE IF NOT EXISTS neetprep_processed_attempts (attempt_id TEXT PRIMARY KEY, processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);")

    click.echo("Tables created successfully.")
    conn.commit()
    conn.close()

def cleanup_old_data_cli():
    """Removes sessions, files, and PDFs older than 1 day, unless persisted."""
    conn = get_db_connection()
    cutoff = datetime.now() - timedelta(days=1)
    click.echo(f"Starting cleanup for items older than {cutoff.strftime('%Y-%m-%d %H:%M:%S')}:")

    old_sessions = conn.execute('SELECT id FROM sessions WHERE created_at < ? AND persist = 0', (cutoff,)).fetchall()
    click.echo(f"Found {len(old_sessions)} old, non-persisted sessions to delete.")
    for session in old_sessions:
        session_id = session['id']
        images_to_delete = conn.execute('SELECT filename, processed_filename FROM images WHERE session_id = ?', (session_id,)).fetchall()
        for img in images_to_delete:
            if img['filename'] and os.path.exists(os.path.join(UPLOAD_FOLDER, img['filename'])): os.remove(os.path.join(UPLOAD_FOLDER, img['filename']))
            if img['processed_filename'] and os.path.exists(os.path.join(PROCESSED_FOLDER, img['processed_filename'])): os.remove(os.path.join(PROCESSED_FOLDER, img['processed_filename']))
        conn.execute('DELETE FROM questions WHERE session_id = ?', (session_id,))
        conn.execute('DELETE FROM images WHERE session_id = ?', (session_id,))
        conn.execute('DELETE FROM sessions WHERE id = ?', (session_id,))

    old_pdfs = conn.execute('SELECT id, filename FROM generated_pdfs WHERE created_at < ? AND persist = 0', (cutoff,)).fetchall()
    click.echo(f"Found {len(old_pdfs)} old, non-persisted generated PDFs to delete.")
    for pdf in old_pdfs:
        if os.path.exists(os.path.join(OUTPUT_FOLDER, pdf['filename'])): os.remove(os.path.join(OUTPUT_FOLDER, pdf['filename']))
        conn.execute('DELETE FROM generated_pdfs WHERE id = ?', (pdf['id'],))

    conn.commit()
    conn.close()

def _get_local_pdf_path(path_or_url):
    """
    Takes a path or URL. If it's a URL, downloads it to the UPLOAD_FOLDER.
    Returns (local_path, original_filename, is_temp_file)
    """
    is_url = path_or_url.lower().startswith(('http://', 'https://'))
    if is_url:
        click.echo(f"Downloading from URL: {path_or_url}")
        try:
            if "drive.google.com" in path_or_url:
                file_id = path_or_url.split('/')[-2]
                download_url = f'https://drive.google.com/uc?export=download&id={file_id}'
                response = requests.get(download_url, stream=True)
                content_disposition = response.headers.get('content-disposition')
                if content_disposition:
                    filenames = re.findall('filename="(.+)"', content_disposition)
                    original_name = secure_filename(filenames[0]) if filenames else f"{str(uuid.uuid4())}.pdf"
                else:
                    original_name = f"{str(uuid.uuid4())}.pdf"
            elif path_or_url.lower().endswith('.pdf'):
                response = requests.get(path_or_url, stream=True)
                response.raise_for_status()
                original_name = secure_filename(path_or_url.split('/')[-1]) or f"{str(uuid.uuid4())}.pdf"
            else:
                raise ValueError("URL is not a recognized Google Drive or direct .pdf link.")

            local_path = os.path.join(UPLOAD_FOLDER, f"temp_{original_name}")
            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            return local_path, original_name, True
        except Exception as e:
            click.secho(f"Error downloading file: {e}", fg="red", err=True)
            return None, None, False
    else:
        if not os.path.exists(path_or_url):
            click.secho(f"Error: File not found at {path_or_url}", fg="red", err=True)
            return None, None, False
        return path_or_url, secure_filename(os.path.basename(path_or_url)), False

# --- CLI Group ---
@click.group()
def cli():
    """A CLI for managing the Report Generator application."""
    pass

# --- CLI Commands ---
@cli.command()
def db_init():
    """Initializes or updates the database schema."""
    click.secho("Initializing database schema...", fg="yellow")
    setup_database_cli()
    click.secho("Database schema is up to date.", fg="green")

@cli.command()
def db_cleanup():
    """Cleans up old, non-persisted data."""
    click.secho("Starting cleanup of old data...", fg="yellow")
    cleanup_old_data_cli()
    click.secho("Cleanup finished.", fg="green")

@cli.command('upload')
@click.argument('pdf_paths', type=click.STRING)
@click.option('--simple-progress', is_flag=True, help='Print simple percentage progress to stdout.')
@click.option('--final', is_flag=True, help='Mark the PDF as a final version and add to generated_pdfs table.')
@click.option('--subject', type=click.STRING, help='Subject for the final PDF.')
@click.option('--tags', type=click.STRING, help='Tags for the final PDF (comma-separated).')
@click.option('--notes', type=click.STRING, help='Notes for the final PDF.')
@click.option('--log', is_flag=True, help='Log all output to cli.log.')
def upload(pdf_paths, simple_progress, final, subject, tags, notes, log):
    """
    A CLI tool to upload a large PDF directly to the application's database.
    PDF_PATHS: A comma-separated list of full paths to the PDF files you wish to upload or Google Drive URLs.
    """
    if log:
        try:
            log_f = open('cli.log', 'a')
            sys.stdout = log_f
            sys.stderr = log_f
        except Exception as e:
            click.secho(f"Error opening log file: {e}", fg="red", err=True)
            raise click.Abort()

    click.echo(f"--- Log entry: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    click.echo(f"Arguments: pdf_paths={pdf_paths}, simple_progress={simple_progress}, final={final}, subject={subject}, tags={tags}, notes={notes}, log={log}")
    click.echo("---" * 20)

    files_to_process = [p.strip() for p in pdf_paths.split(',')]

    for pdf_path_or_url in files_to_process:
        click.secho(f"--- Processing: {click.style(pdf_path_or_url, bold=True)} ---", fg="yellow")
        
        local_pdf_path, original_filename, is_temp = _get_local_pdf_path(pdf_path_or_url)

        if not local_pdf_path:
            continue

        try:
            if final:
                if not subject:
                    click.secho("Error: --subject is required when using --final.", fg="red", err=True)
                    raise click.Abort()

                session_id = str(uuid.uuid4())
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('INSERT INTO sessions (id, original_filename) VALUES (?, ?)',
                               (session_id, original_filename))
                
                output_filename = original_filename
                output_path = os.path.join(OUTPUT_FOLDER, output_filename)

                if os.path.exists(output_path):
                    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                    output_filename = f"{timestamp}_{original_filename}"
                    output_path = os.path.join(OUTPUT_FOLDER, output_filename)
                    click.secho(f"Warning: File '{original_filename}' already exists. Saving as '{output_filename}'.", fg="yellow")

                import shutil
                shutil.copy(local_pdf_path, output_path)

                cursor.execute(
                    'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename) VALUES (?, ?, ?, ?, ?, ?)',
                    (session_id, output_filename, subject, tags, notes, original_filename)
                )
                conn.commit()
                conn.close()
                click.secho(f"Successfully added final PDF '{original_filename}' to the database.", fg="green")

            else: # Standard page-extraction mode
                click.echo(f"Processing PDF: {click.style(original_filename, bold=True)}")
                session_id = str(uuid.uuid4())
                doc = fitz.open(local_pdf_path)
                num_pages = len(doc)
                if num_pages == 0:
                    click.secho("Warning: This PDF has 0 pages. Nothing to process.", fg="yellow")
                    continue

                click.echo(f"PDF contains {num_pages} pages to process.")
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('INSERT INTO sessions (id, original_filename) VALUES (?, ?)',
                               (session_id, original_filename))
                click.echo(f"Created session: {click.style(session_id, fg='cyan')}")

                images_to_insert = []

                if simple_progress:
                    for i, page in enumerate(doc):
                        pix = page.get_pixmap(dpi=150)
                        page_filename = f"{session_id}_page_{i}.png"
                        page_path = os.path.join(UPLOAD_FOLDER, page_filename)
                        pix.save(page_path)
                        images_to_insert.append(
                            (session_id, i, page_filename, f"Page {i + 1}", 'original')
                        )
                        percentage = int(((i + 1) / num_pages) * 100)
                        sys.stdout.write(f"{percentage}\n")
                        sys.stdout.flush()
                else:
                    progress = Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(bar_width=None),
                        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                        TextColumn("• Page {task.completed}/{task.total}"),
                        TextColumn("• Elapsed:"), TimeElapsedColumn(),
                        TextColumn("• Remaining:"), TimeRemainingColumn(),
                    )
                    with progress:
                        task = progress.add_task("[green]Extracting pages...", total=num_pages)
                        for i, page in enumerate(doc):
                            pix = page.get_pixmap(dpi=150)
                            page_filename = f"{session_id}_page_{i}.png"
                            page_path = os.path.join(UPLOAD_FOLDER, page_filename)
                            pix.save(page_path)
                            images_to_insert.append(
                                (session_id, i, page_filename, f"Page {i + 1}", 'original')
                            )
                            progress.update(task, advance=1)

                click.echo("\nInserting image records into the database...")
                cursor.executemany(
                    'INSERT INTO images (session_id, image_index, filename, original_name, image_type) VALUES (?, ?, ?, ?, ?)',
                    images_to_insert
                )
                conn.commit()
                click.secho(f"Successfully committed {len(images_to_insert)} records to the database.", fg="green")
                doc.close()

        except Exception as e:
            click.secho(f"An unexpected error occurred while processing {original_filename}: {e}", fg="red", err=True)
        
        finally:
            if is_temp and os.path.exists(local_pdf_path):
                os.remove(local_pdf_path)

        click.secho(f"\n✅ All done! Upload complete for '{original_filename}'.", fg="green", bold=True)

if __name__ == '__main__':
    cli()
