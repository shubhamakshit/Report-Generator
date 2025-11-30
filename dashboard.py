
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from database import get_db_connection
import os
from flask import current_app

dashboard_bp = Blueprint('dashboard', __name__)

def get_session_size(session_id, user_id):
    """Calculate the total size of files associated with a session."""
    import os
    from flask import current_app

    # Import logging
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        rich_available = True
    except ImportError:
        # Rich not available, just use basic logging
        console = None
        rich_available = False

    current_app.logger.info(f"Calculating size for session_id: {session_id}")

    total_size = 0
    breakdown = []

    conn = get_db_connection()

    # Get all images associated with the session
    images = conn.execute("""
        SELECT filename, processed_filename, image_type
        FROM images
        WHERE session_id = ?
    """, (session_id,)).fetchall()

    # Add sizes of original and processed images
    for image in images:
        # Add original file size (in upload folder)
        if image['filename']:
            file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], image['filename'])
            if os.path.exists(file_path):
                size = os.path.getsize(file_path)
                total_size += size
                current_app.logger.info(f"  Original image {image['filename']}: {size} bytes")
                breakdown.append(("Original Image", image['filename'], size))
            else:
                current_app.logger.info(f"  Original image file not found: {file_path}")

        # Add processed/cropped image size (in processed folder)
        if image['processed_filename']:
            file_path = os.path.join(current_app.config['PROCESSED_FOLDER'], image['processed_filename'])
            if os.path.exists(file_path):
                size = os.path.getsize(file_path)
                total_size += size
                current_app.logger.info(f"  Processed image {image['processed_filename']}: {size} bytes")
                breakdown.append(("Processed Image", image['processed_filename'], size))
            else:
                current_app.logger.info(f"  Processed image file not found: {file_path}")

    # Add size of original PDF file if it exists
    session_info = conn.execute("SELECT original_filename FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if session_info and session_info['original_filename']:
        # Try to find the original PDF in the upload folder with the session ID prefix
        pdf_filename = f"{session_id}_{session_info['original_filename']}"
        pdf_path = os.path.join(current_app.config['UPLOAD_FOLDER'], pdf_filename)
        if os.path.exists(pdf_path):
            size = os.path.getsize(pdf_path)
            total_size += size
            current_app.logger.info(f"  Original PDF {pdf_filename}: {size} bytes")
            breakdown.append(("Original PDF", pdf_filename, size))
        else:
            current_app.logger.info(f"  Original PDF file not found: {pdf_path}")

    # Add size of any generated PDFs for this session
    generated_pdfs = conn.execute("""
        SELECT filename
        FROM generated_pdfs
        WHERE session_id = ?
    """, (session_id,)).fetchall()

    for pdf in generated_pdfs:
        if pdf['filename']:
            pdf_path = os.path.join(current_app.config['OUTPUT_FOLDER'], pdf['filename'])
            if os.path.exists(pdf_path):
                size = os.path.getsize(pdf_path)
                total_size += size
                current_app.logger.info(f"  Generated PDF {pdf['filename']}: {size} bytes")
                breakdown.append(("Generated PDF", pdf['filename'], size))
            else:
                current_app.logger.info(f"  Generated PDF file not found: {pdf_path}")

    current_app.logger.info(f"Total size for session {session_id}: {total_size} bytes")

    # Create a rich table to show breakdown if rich is available
    if rich_available and console:
        table = Table(title=f"Session {session_id} Size Breakdown")
        table.add_column("File Type", style="cyan")
        table.add_column("Filename", style="magenta")
        table.add_column("Size (bytes)", style="green")

        for file_type, filename, size in breakdown:
            table.add_row(file_type, filename, str(size))

        if breakdown:
            console.print(table)
        else:
            console.print(f"[yellow]No files found for session {session_id}[/yellow]")

    conn.close()
    return total_size


def format_file_size(size_bytes):
    """Convert bytes to human readable format."""
    if size_bytes == 0:
        return "0 B"

    size_names = ["B", "KB", "MB", "GB"]
    import math
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_names[i]}"


@dashboard_bp.route('/dashboard')
@login_required
def dashboard():
    # Check if size parameter is passed
    show_size = request.args.get('size', type=int)

    conn = get_db_connection()
    sessions_rows = conn.execute("""
        SELECT s.id, s.created_at, s.original_filename, s.persist, s.name, s.session_type,
               COUNT(CASE WHEN i.image_type = 'original' THEN 1 END) as page_count,
               COUNT(CASE WHEN i.image_type = 'cropped' THEN 1 END) as question_count
        FROM sessions s
        LEFT JOIN images i ON s.id = i.session_id
        WHERE s.user_id = ?
        GROUP BY s.id, s.created_at, s.original_filename, s.persist, s.name, s.session_type
        ORDER BY s.created_at DESC
    """, (current_user.id,)).fetchall()

    sessions = []
    for session in sessions_rows:
        session_dict = dict(session)

        # Calculate total size for this session only if requested
        if show_size:
            session_size = get_session_size(session_dict['id'], current_user.id)
            session_dict['total_size'] = session_size
            session_dict['total_size_formatted'] = format_file_size(session_size)

        sessions.append(session_dict)

    conn.close()

    return render_template('dashboard.html', sessions=sessions, show_size=bool(show_size))

@dashboard_bp.route('/sessions/batch_delete', methods=['POST'])
@login_required
def batch_delete_sessions():
    data = request.json
    session_ids = data.get('ids', [])

    if not session_ids:
        return jsonify({'error': 'No session IDs provided'}), 400

    try:
        conn = get_db_connection()
        for session_id in session_ids:
            # Security Check: Ensure the session belongs to the current user
            session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
            if not session_owner or session_owner['user_id'] != current_user.id:
                # Silently skip or log an error, but don't delete
                current_app.logger.warning(f"User {current_user.id} attempted to delete unauthorized session {session_id}.")
                continue

            # Delete associated files
            images_to_delete = conn.execute('SELECT filename, processed_filename FROM images WHERE session_id = ?', (session_id,)).fetchall()
            for img in images_to_delete:
                if img['filename']:
                    try:
                        os.remove(os.path.join(current_app.config['UPLOAD_FOLDER'], img['filename']))
                    except OSError:
                        pass
                if img['processed_filename']:
                    try:
                        os.remove(os.path.join(current_app.config['PROCESSED_FOLDER'], img['processed_filename']))
                    except OSError:
                        pass

            # Delete from database
            conn.execute('DELETE FROM questions WHERE session_id = ?', (session_id,))
            conn.execute('DELETE FROM images WHERE session_id = ?', (session_id,))
            conn.execute('DELETE FROM sessions WHERE id = ?', (session_id,))

        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@dashboard_bp.route('/sessions/reduce_space/<session_id>', methods=['POST'])
@login_required
def reduce_space(session_id):
    """Truncate original page images to reduce disk space."""
    try:
        conn = get_db_connection()

        # Security Check: Ensure the session belongs to the current user
        session_owner = conn.execute('SELECT user_id FROM sessions WHERE id = ?', (session_id,)).fetchone()
        if not session_owner or session_owner['user_id'] != current_user.id:
            current_app.logger.warning(f"User {current_user.id} attempted to reduce space for unauthorized session {session_id}.")
            return jsonify({'error': 'Unauthorized access to session'}), 403

        # Get all original images associated with the session
        images = conn.execute("""
            SELECT filename
            FROM images
            WHERE session_id = ? AND image_type = 'original'
        """, (session_id,)).fetchall()

        # Truncate original images to reduce space
        truncated_count = 0
        for image in images:
            if image['filename']:
                file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], image['filename'])
                if os.path.exists(file_path):
                    try:
                        # Truncate the file to 0 bytes
                        with open(file_path, 'w') as f:
                            f.truncate(0)
                        truncated_count += 1
                    except OSError as e:
                        current_app.logger.error(f"Error truncating file {file_path}: {str(e)}")

        conn.close()

        return jsonify({
            'success': True,
            'truncated_count': truncated_count,
            'message': f'Successfully reduced space by truncating {truncated_count} original page images'
        })
    except Exception as e:
        current_app.logger.error(f"Error in reduce space: {str(e)}")
        return jsonify({'error': str(e)}), 500
