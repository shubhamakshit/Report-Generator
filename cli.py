import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime

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
OUTPUT_FOLDER = os.path.join(SCRIPT_DIR, 'output')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
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


def is_google_drive_url(url):
    """Check if the given URL is a Google Drive URL."""
    return "drive.google.com" in url


def download_file_from_google_drive(url, destination_folder):
    """Download a file from a Google Drive URL."""
    file_id = url.split('/')[-2]
    download_url = f'https://drive.google.com/uc?export=download&id={file_id}'
    response = requests.get(download_url, stream=True)
    content_disposition = response.headers.get('content-disposition')
    if content_disposition:
        filenames = re.findall('filename="(.+)"', content_disposition)
        if filenames:
            filename = filenames[0]
        else:
            filename = f"{str(uuid.uuid4())}.pdf"
    else:
        filename = f"{str(uuid.uuid4())}.pdf"
    destination_path = os.path.join(destination_folder, filename)
    with open(destination_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)
    return destination_path, filename


# --- CLI Command Definition ---
@click.command()
@click.argument('pdf_path', type=click.STRING)
@click.option('--simple-progress', is_flag=True, help='Print simple percentage progress to stdout.')
@click.option('--final', is_flag=True, help='Mark the PDF as a final version and add to generated_pdfs table.')
@click.option('--subject', type=click.STRING, help='Subject for the final PDF.')
@click.option('--tags', type=click.STRING, help='Tags for the final PDF (comma-separated).')
@click.option('--notes', type=click.STRING, help='Notes for the final PDF.')
def upload(pdf_path, simple_progress, final, subject, tags, notes):
    """
    A CLI tool to upload a large PDF directly to the application's database.
    PDF_PATH: The full path to the PDF file you wish to upload or a Google Drive URL.
    """
    # Suppress all click.echo output if simple_progress is enabled
    if simple_progress:
        click.echo = lambda message, **kwargs: None

    if final:
        if not subject:
            click.secho("Error: --subject is required when using --final.", fg="red", err=True)
            raise click.Abort()
        
        if not os.path.exists(pdf_path):
            click.secho(f"Error: File not found at {pdf_path}", fg="red", err=True)
            raise click.Abort()

        session_id = str(uuid.uuid4())
        original_filename = secure_filename(os.path.basename(pdf_path))
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO sessions (id, original_filename) VALUES (?, ?)',
                       (session_id, original_filename))
        
        output_filename = f"{session_id}_{original_filename}"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        import shutil
        shutil.copy(pdf_path, output_path)

        cursor.execute(
            'INSERT INTO generated_pdfs (session_id, filename, subject, tags, notes, source_filename) VALUES (?, ?, ?, ?, ?, ?)',
            (session_id, output_filename, subject, tags, notes, original_filename)
        )
        conn.commit()
        conn.close()
        click.secho(f"Successfully added final PDF '{original_filename}' to the database.", fg="green")
        return

    temp_pdf_path = None
    if is_google_drive_url(pdf_path):
        click.echo(f"Processing Google Drive URL: {click.style(pdf_path, bold=True)}")
        try:
            temp_pdf_path, original_filename = download_file_from_google_drive(pdf_path, UPLOAD_FOLDER)
            pdf_path = temp_pdf_path
        except Exception as e:
            click.secho(f"Error downloading from Google Drive: {e}", fg="red", err=True)
            raise click.Abort()
    elif not os.path.exists(pdf_path):
        click.secho(f"Error: File not found at {pdf_path}", fg="red", err=True)
        raise click.Abort()
    else:
        original_filename = secure_filename(os.path.basename(pdf_path))

    click.echo(f"Processing PDF: {click.style(pdf_path, bold=True)}")
    session_id = str(uuid.uuid4())

    try:
        doc = fitz.open(pdf_path)
        num_pages = len(doc)
        if num_pages == 0:
            click.secho("Warning: This PDF has 0 pages. Nothing to process.", fg="yellow")
            return

        click.echo(f"PDF contains {num_pages} pages to process.")
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO sessions (id, original_filename) VALUES (?, ?)',
                       (session_id, original_filename))
        click.echo(f"Created session: {click.style(session_id, fg='cyan')}")

        images_to_insert = []

        if simple_progress:
            # Simple loop for progress reporting
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=150)
                page_filename = f"{session_id}_page_{i}.png"
                page_path = os.path.join(UPLOAD_FOLDER, page_filename)
                pix.save(page_path)
                images_to_insert.append(
                    (session_id, i, page_filename, f"Page {i + 1}", 'original')
                )
                # Print percentage to stdout and flush
                percentage = int(((i + 1) / num_pages) * 100)
                sys.stdout.write(f"{percentage}\n")
                sys.stdout.flush()
        else:
            # Rich progress bar for interactive terminal
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

    except Exception as e:
        # For simple progress, we must output errors to stderr
        click.secho(f"\nAn unexpected error occurred: {e}", fg="red", err=True)
        if 'conn' in locals():
            conn.rollback()
        raise click.Abort()
    finally:
        if 'doc' in locals() and doc:
            doc.close()
        if 'conn' in locals() and conn:
            conn.close()
        if temp_pdf_path and os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)

    click.secho(f"\n✅ All done! Upload complete for '{original_filename}'.", fg="green", bold=True)


if __name__ == '__main__':
    upload()