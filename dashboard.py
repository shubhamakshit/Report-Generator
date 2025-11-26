
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from database import get_db_connection
import os
from flask import current_app

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    sessions_rows = conn.execute("""
        SELECT s.id, s.created_at, s.original_filename, s.persist, s.name,
               COUNT(CASE WHEN i.image_type = 'original' THEN 1 END) as page_count,
               COUNT(CASE WHEN i.image_type = 'cropped' THEN 1 END) as question_count
        FROM sessions s
        LEFT JOIN images i ON s.id = i.session_id
        WHERE s.user_id = ?
        GROUP BY s.id, s.created_at, s.original_filename, s.persist, s.name
        ORDER BY s.created_at DESC
    """, (current_user.id,)).fetchall()
    sessions = [dict(row) for row in sessions_rows]
    conn.close()
    return render_template('dashboard.html', sessions=sessions)

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
