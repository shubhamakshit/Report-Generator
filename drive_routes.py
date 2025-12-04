
import os
import shutil
import gdown
from flask import Blueprint, render_template, request, jsonify, current_app, send_from_directory, url_for
from flask_login import login_required, current_user
from database import get_db_connection
from datetime import datetime
import threading

drive_bp = Blueprint('drive', __name__)

DRIVE_SYNC_FOLDER = 'drive_sync'

def get_sync_folder_path(source_name=None):
    base = os.path.join(current_app.config['OUTPUT_FOLDER'], DRIVE_SYNC_FOLDER)
    if not os.path.exists(base):
        os.makedirs(base)
    if source_name:
        path = os.path.join(base, source_name)
        if not os.path.exists(path):
            os.makedirs(path)
        return path
    return base

@drive_bp.route('/drive_manager')
@login_required
def drive_manager():
    conn = get_db_connection()
    sources = conn.execute('SELECT * FROM drive_sources WHERE user_id = ? ORDER BY created_at DESC', (current_user.id,)).fetchall()
    conn.close()
    return render_template('drive_manager.html', sources=[dict(s) for s in sources])

@drive_bp.route('/drive/add', methods=['POST'])
@login_required
def add_source():
    name = request.form.get('name')
    url = request.form.get('url')
    
    if not name or not url:
        return jsonify({'error': 'Name and URL required'}), 400
        
    conn = get_db_connection()
    try:
        # Create local folder
        local_path = name.strip().replace(' ', '_')
        
        conn.execute('INSERT INTO drive_sources (name, url, local_path, user_id) VALUES (?, ?, ?, ?)',
                     (name, url, local_path, current_user.id))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@drive_bp.route('/drive/delete/<int:id>', methods=['POST'])
@login_required
def delete_source(id):
    conn = get_db_connection()
    source = conn.execute('SELECT * FROM drive_sources WHERE id = ?', (id,)).fetchone()
    
    if not source or source['user_id'] != current_user.id:
        conn.close()
        return jsonify({'error': 'Unauthorized'}), 403
        
    # Delete from DB
    conn.execute('DELETE FROM drive_sources WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    
    # Delete files
    try:
        path = get_sync_folder_path(source['local_path'])
        if os.path.exists(path):
            shutil.rmtree(path)
    except Exception as e:
        print(f"Error deleting folder: {e}")
        
    return jsonify({'success': True})

def sync_task(source_id, user_id, app_config):
    # Re-create app context manually if needed or just use DB
    # We passed app_config to reconstruct paths
    
    # Connect to DB
    from utils import get_db_connection
    import sqlite3
    
    conn = sqlite3.connect('database.db') # Manual connect for thread safety
    conn.row_factory = sqlite3.Row
    
    try:
        source = conn.execute('SELECT * FROM drive_sources WHERE id = ?', (source_id,)).fetchone()
        if not source: return
        
        output_base = os.path.join(app_config['OUTPUT_FOLDER'], DRIVE_SYNC_FOLDER, source['local_path'])
        if not os.path.exists(output_base):
            os.makedirs(output_base)
            
        print(f"Syncing Drive: {source['name']} to {output_base}")
        
        # Use gdown to download folder
        # gdown.download_folder(url, output=...)
        # Note: gdown might fail if not a folder link or permissions issue
        # We catch exceptions
        
        try:
            # Check if it's a folder or file
            if 'drive.google.com/drive/folders' in source['url']:
                gdown.download_folder(url=source['url'], output=output_base, quiet=False, use_cookies=False)
            else:
                # Try single file download? Or assume folder?
                # gdown handles file links with download(), not download_folder
                # But "Syncing Public Drive Folders" implies folders.
                # Let's try download_folder first.
                gdown.download_folder(url=source['url'], output=output_base, quiet=False, use_cookies=False)
                
            # Update last_synced
            conn.execute('UPDATE drive_sources SET last_synced = CURRENT_TIMESTAMP WHERE id = ?', (source_id,))
            conn.commit()
            print("Sync complete.")
            
        except Exception as e:
            print(f"GDown Error: {e}")
            
    except Exception as e:
        print(f"Sync Task Error: {e}")
    finally:
        conn.close()

@drive_bp.route('/drive/sync/<int:id>', methods=['POST'])
@login_required
def sync_source(id):
    conn = get_db_connection()
    source = conn.execute('SELECT * FROM drive_sources WHERE id = ?', (id,)).fetchone()
    conn.close()
    
    if not source or source['user_id'] != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
        
    thread = threading.Thread(target=sync_task, args=(id, current_user.id, current_app.config.copy()))
    thread.start()
    
    return jsonify({'success': True, 'message': 'Sync started in background'})

@drive_bp.route('/drive/browse/<int:source_id>')
@drive_bp.route('/drive/browse/<int:source_id>/<path:subpath>')
@login_required
def browse_drive(source_id, subpath=''):
    conn = get_db_connection()
    source = conn.execute('SELECT * FROM drive_sources WHERE id = ?', (source_id,)).fetchone()
    conn.close()
    
    if not source or source['user_id'] != current_user.id:
        return "Unauthorized", 403
        
    base_path = get_sync_folder_path(source['local_path'])
    current_path = os.path.join(base_path, subpath)
    
    if not os.path.exists(current_path):
        return "Path not found", 404
        
    # List files
    items = []
    try:
        for entry in os.scandir(current_path):
            is_dir = entry.is_dir()
            # Determine type
            file_type = 'file'
            if is_dir: file_type = 'folder'
            elif entry.name.lower().endswith('.pdf'): file_type = 'pdf'
            elif entry.name.lower().endswith(('.png', '.jpg', '.jpeg')): file_type = 'image'
            
            items.append({
                'name': entry.name,
                'type': file_type,
                'path': os.path.join(subpath, entry.name).strip('/')
            })
    except Exception as e:
        return f"Error listing files: {e}", 500
        
    # Sort: Folders first, then files
    items.sort(key=lambda x: (x['type'] != 'folder', x['name'].lower()))
    
    breadcrumbs = []
    if subpath:
        parts = subpath.split('/')
        built = ''
        for part in parts:
            built = os.path.join(built, part).strip('/')
            breadcrumbs.append({'name': part, 'path': built})
            
    return render_template('drive_browser.html', source=source, items=items, breadcrumbs=breadcrumbs, current_subpath=subpath)

@drive_bp.route('/drive/file/<int:source_id>/<path:filepath>')
@login_required
def view_drive_file(source_id, filepath):
    conn = get_db_connection()
    source = conn.execute('SELECT * FROM drive_sources WHERE id = ?', (source_id,)).fetchone()
    conn.close()
    
    if not source or source['user_id'] != current_user.id:
        return "Unauthorized", 403
        
    base_path = get_sync_folder_path(source['local_path'])
    full_path = os.path.join(base_path, filepath)
    
    if not os.path.exists(full_path):
        return "File not found", 404
        
    # If PDF, serve via viewer
    if filepath.lower().endswith('.pdf'):
        # We can reuse the view_pdf_v2 route logic by passing a special URL?
        # Or just render the template with a direct link to this route's raw file handler.
        # Let's make a raw file handler.
        file_url = url_for('drive.serve_drive_file', source_id=source_id, filepath=filepath)
        return render_template('pdfjs_viewer.html', pdf_url=file_url, pdf_title=os.path.basename(filepath))
        
    # If Image, serve raw
    return send_from_directory(os.path.dirname(full_path), os.path.basename(full_path))

@drive_bp.route('/drive/raw/<int:source_id>/<path:filepath>')
@login_required
def serve_drive_file(source_id, filepath):
    conn = get_db_connection()
    source = conn.execute('SELECT * FROM drive_sources WHERE id = ?', (source_id,)).fetchone()
    conn.close()
    
    if not source or source['user_id'] != current_user.id:
        return "Unauthorized", 403
        
    base_path = get_sync_folder_path(source['local_path'])
    return send_from_directory(base_path, filepath)
