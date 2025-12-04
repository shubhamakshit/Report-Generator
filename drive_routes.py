import os
import shutil
import gdown
from flask import Blueprint, render_template, request, jsonify, current_app, send_from_directory, url_for, redirect, session
from flask_login import login_required, current_user
from database import get_db_connection
from datetime import datetime
import threading
import re
import json

# Allow OAuth over HTTP for local testing
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

from gdrive_service import get_drive_service, create_flow, list_drive_files, download_file_to_stream, get_file_metadata

drive_bp = Blueprint('drive', __name__)

DRIVE_SYNC_FOLDER = 'drive_sync'

def extract_drive_id(url):
    # Extracts Drive ID (File or Folder) - simplified regex for ~25+ chars
    match = re.search(r'[-\w]{25,}', url)
    return match.group(0) if match else None

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
    
    # Check Drive API Status
    drive_connected = bool(current_user.google_token)
    
    return render_template('drive_manager.html', sources=[dict(s) for s in sources], drive_connected=drive_connected)

@drive_bp.route('/drive/connect')
@login_required
def connect_drive():
    try:
        redirect_uri = 'http://localhost'
        flow = create_flow(redirect_uri)
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true')
        session['oauth_state'] = state
        return render_template('drive_connect_manual.html', auth_url=authorization_url)
    except FileNotFoundError:
        return "client_secret.json not found. Please upload it to the app root via Settings.", 404
    except Exception as e:
        return f"Error creating flow: {e}", 500

@drive_bp.route('/drive/manual_callback', methods=['POST'])
@login_required
def manual_callback():
    state = session.get('oauth_state')
    full_url = request.form.get('full_url')
    if not full_url: return "URL is required", 400
    try:
        redirect_uri = 'http://localhost'
        flow = create_flow(redirect_uri)
        flow.fetch_token(authorization_response=full_url)
        credentials = flow.credentials
        token_json = credentials.to_json()
        conn = get_db_connection()
        conn.execute('UPDATE users SET google_token = ? WHERE id = ?', (token_json, current_user.id))
        conn.commit()
        conn.close()
        current_user.google_token = token_json
        return redirect(url_for('drive.drive_manager'))
    except Exception as e:
        return f"Auth failed: {e}<br><br>Make sure you copied the full URL correctly.", 500

@drive_bp.route('/oauth2callback')
def oauth2callback():
    state = session.get('oauth_state')
    if not state: return "Invalid state", 400
    try:
        redirect_uri = url_for('drive.oauth2callback', _external=True)
        flow = create_flow(redirect_uri)
        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials
        token_json = credentials.to_json()
        conn = get_db_connection()
        conn.execute('UPDATE users SET google_token = ? WHERE id = ?', (token_json, current_user.id))
        conn.commit()
        conn.close()
        current_user.google_token = token_json
        return redirect(url_for('drive.drive_manager'))
    except Exception as e:
        return f"Auth failed: {e}", 500

@drive_bp.route('/drive/add', methods=['POST'])
@login_required
def add_source():
    name = request.form.get('name')
    url = request.form.get('url')
    if not name or not url: return jsonify({'error': 'Name and URL required'}), 400
    conn = get_db_connection()
    try:
        source_type = 'file'
        if '/folders/' in url or 'drive/folders' in url: source_type = 'folder'
        local_path = name.strip().replace(' ', '_')
        conn.execute('INSERT INTO drive_sources (name, url, local_path, user_id, source_type) VALUES (?, ?, ?, ?, ?)',
                     (name, url, local_path, current_user.id, source_type))
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
    conn.execute('DELETE FROM drive_sources WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    try:
        path = get_sync_folder_path(source['local_path'])
        if os.path.exists(path): shutil.rmtree(path)
    except Exception as e:
        print(f"Error deleting folder: {e}")
    return jsonify({'success': True})

def sync_task(source_id, user_id, app_config):
    import sqlite3
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    try:
        source = conn.execute('SELECT * FROM drive_sources WHERE id = ?', (source_id,)).fetchone()
        if not source: return
        output_base = os.path.join(app_config['OUTPUT_FOLDER'], DRIVE_SYNC_FOLDER, source['local_path'])
        if not os.path.exists(output_base): os.makedirs(output_base)
        print(f"Syncing Drive: {source['name']} to {output_base}")
        try:
            gdown.download_folder(url=source['url'], output=output_base, quiet=False, use_cookies=False)
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
    if not source or source['user_id'] != current_user.id: return jsonify({'error': 'Unauthorized'}), 403
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
    
    if not source or source['user_id'] != current_user.id: return "Unauthorized", 403

    # === API Upgrade Logic ===
    if current_user.google_token and not subpath:
        drive_id = extract_drive_id(source['url'])
        if drive_id:
            # Pass source name as title
            return redirect(url_for('drive.browse_drive_api', folder_id=drive_id, title=source['name']))
    # =========================
        
    base_path = get_sync_folder_path(source['local_path'])
    current_path = os.path.join(base_path, subpath)
    
    if not os.path.exists(current_path):
        if source['source_type'] == 'file': pass
        else: return "Path not found (Not synced yet). Click Sync Now in Manager.", 404
        
    items = []
    if os.path.exists(current_path):
        try:
            for entry in os.scandir(current_path):
                is_dir = entry.is_dir()
                file_type = 'file'
                if is_dir: file_type = 'folder'
                elif entry.name.lower().endswith('.pdf'): file_type = 'pdf'
                elif entry.name.lower().endswith(('.png', '.jpg', '.jpeg')): file_type = 'image'
                
                items.append({
                    'name': entry.name,
                    'type': file_type,
                    'path': os.path.join(subpath, entry.name).strip('/')
                })
        except Exception as e: return f"Error listing files: {e}", 500
        
    items.sort(key=lambda x: (x['type'] != 'folder', x['name'].lower()))
    
    if not items and source['source_type'] == 'file':
        items.append({'name': 'Tap to Download & View', 'type': 'pdf', 'path': 'document.pdf'})
    
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
    if not source or source['user_id'] != current_user.id: return "Unauthorized", 403
    base_path = get_sync_folder_path(source['local_path'])
    full_path = os.path.join(base_path, filepath)
    
    if not os.path.exists(full_path) and source['source_type'] == 'file':
        try:
            if not os.path.exists(base_path): os.makedirs(base_path)
            gdown.download(url=source['url'], output=full_path, quiet=False, fuzzy=True)
        except Exception as e: return f"Error downloading file: {e}", 500
    
    if not os.path.exists(full_path): return "File not found.", 404
    if full_path.lower().endswith('.pdf'):
        file_url = url_for('drive.serve_drive_file', source_id=source_id, filepath=os.path.basename(full_path))
        return render_template('pdfjs_viewer.html', pdf_url=file_url, pdf_title=os.path.basename(full_path))
    return send_from_directory(os.path.dirname(full_path), os.path.basename(full_path))

@drive_bp.route('/drive/raw/<int:source_id>/<path:filepath>')
@login_required
def serve_drive_file(source_id, filepath):
    conn = get_db_connection()
    source = conn.execute('SELECT * FROM drive_sources WHERE id = ?', (source_id,)).fetchone()
    conn.close()
    if not source or source['user_id'] != current_user.id: return "Unauthorized", 403
    base_path = get_sync_folder_path(source['local_path'])
    return send_from_directory(base_path, filepath)

@drive_bp.route('/drive/api/list')
@drive_bp.route('/drive/api/list/<folder_id>')
@login_required
def api_list_files(folder_id='root'):
    service = get_drive_service(current_user)
    if not service: return jsonify({'error': 'Not connected'}), 401
    files, next_token = list_drive_files(service, folder_id)
    file_list = []
    for f in files:
        is_folder = f['mimeType'] == 'application/vnd.google-apps.folder'
        icon = 'folder-fill text-warning' if is_folder else 'file-earmark-text text-secondary'
        if f['mimeType'] == 'application/pdf': icon = 'file-earmark-pdf-fill text-danger'
        elif 'image' in f['mimeType']: icon = 'file-earmark-image-fill text-info'
        file_list.append({
            'id': f['id'],
            'name': f['name'],
            'type': 'folder' if is_folder else 'file',
            'mimeType': f['mimeType'],
            'icon': icon,
            'size': f.get('size')
        })
    return jsonify({'files': file_list, 'next_token': next_token})

@drive_bp.route('/drive/api/browse/<folder_id>')
@login_required
def browse_drive_api(folder_id):
    service = get_drive_service(current_user)
    if not service: return redirect(url_for('drive.drive_manager'))
    title = request.args.get('title', 'My Drive')
    files, next_token = list_drive_files(service, folder_id)
    items = []
    for f in files:
        is_folder = f['mimeType'] == 'application/vnd.google-apps.folder'
        f_type = 'folder' if is_folder else ('pdf' if f['mimeType'] == 'application/pdf' else 'file')
        if 'image' in f['mimeType']: f_type = 'image'
        items.append({
            'name': f['name'],
            'type': f_type,
            'path': f['id'],
            'is_api': True
        })
    return render_template('drive_browser.html', source={'id': 'api', 'name': title}, items=items, breadcrumbs=[], is_api=True)

@drive_bp.route('/drive/api/open/<file_id>')
@login_required
def api_open_file(file_id):
    service = get_drive_service(current_user)
    if not service: return "Not connected", 401
    try:
        meta = get_file_metadata(service, file_id)
        if not meta: return "File not found", 404
        filename = meta['name']
        cache_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'drive_cache')
        if not os.path.exists(cache_dir): os.makedirs(cache_dir)
        from werkzeug.utils import secure_filename
        safe_name = secure_filename(filename)
        file_path = os.path.join(cache_dir, safe_name)
        if not os.path.exists(file_path):
            with open(file_path, 'wb') as f:
                download_file_to_stream(service, file_id, f)
        if safe_name.lower().endswith('.pdf'):
            file_url = url_for('drive.serve_cache_file', filename=safe_name)
            return render_template('pdfjs_viewer.html', pdf_url=file_url, pdf_title=filename)
        if safe_name.lower().endswith(('.png', '.jpg', '.jpeg')):
             return send_from_directory(cache_dir, safe_name)
        return "File downloaded but type not supported for viewing.", 200
    except Exception as e: return f"Error opening file: {e}", 500

@drive_bp.route('/drive/cache/<filename>')
@login_required
def serve_cache_file(filename):
    cache_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'drive_cache')
    return send_from_directory(cache_dir, filename)
