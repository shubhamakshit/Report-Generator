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

# ... (Existing helper functions: get_sync_folder_path) ...

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
        # Use localhost for manual copy-paste flow
        # This requires the Google Cloud Client ID to be type "Desktop App" 
        # OR "Web Application" with "http://localhost" registered.
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
    
    if not full_url:
        return "URL is required", 400
        
    try:
        # Recreate flow with the same redirect_uri used in connect_drive
        redirect_uri = 'http://localhost'
        flow = create_flow(redirect_uri)
        
        # We might need to handle http vs https mismatch in the pasted URL
        # OAUTHLIB_INSECURE_TRANSPORT is already set globally
        
        flow.fetch_token(authorization_response=full_url)
        
        credentials = flow.credentials
        token_json = credentials.to_json()
        
        # Save to DB
        conn = get_db_connection()
        conn.execute('UPDATE users SET google_token = ? WHERE id = ?', (token_json, current_user.id))
        conn.commit()
        conn.close()
        
        # Update current user object in session
        current_user.google_token = token_json
        
        return redirect(url_for('drive.drive_manager'))
    except Exception as e:
        return f"Auth failed: {e}<br><br>Make sure you copied the full URL correctly.", 500

@drive_bp.route('/oauth2callback')
def oauth2callback():
    # Legacy/Direct callback handler
    state = session.get('oauth_state')
    if not state:
        return "Invalid state", 400
        
    try:
        redirect_uri = url_for('drive.oauth2callback', _external=True)
        flow = create_flow(redirect_uri)
        flow.fetch_token(authorization_response=request.url)
        
        credentials = flow.credentials
        token_json = credentials.to_json()
        
        # Save to DB
        conn = get_db_connection()
        conn.execute('UPDATE users SET google_token = ? WHERE id = ?', (token_json, current_user.id))
        conn.commit()
        conn.close()
        
        # Update current user object in session if needed (Flask-Login does this on next request usually)
        current_user.google_token = token_json
        
        return redirect(url_for('drive.drive_manager'))
    except Exception as e:
        return f"Auth failed: {e}", 500

@drive_bp.route('/drive/api/list')
@drive_bp.route('/drive/api/list/<folder_id>')
@login_required
def api_list_files(folder_id='root'):
    service = get_drive_service(current_user)
    if not service:
        return jsonify({'error': 'Not connected'}), 401
        
    files, next_token = list_drive_files(service, folder_id)
    
    # Format for UI
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
    
    files, next_token = list_drive_files(service, folder_id)
    
    # Adapt to template
    items = []
    for f in files:
        is_folder = f['mimeType'] == 'application/vnd.google-apps.folder'
        f_type = 'folder' if is_folder else ('pdf' if f['mimeType'] == 'application/pdf' else 'file')
        if 'image' in f['mimeType']: f_type = 'image'
        
        items.append({
            'name': f['name'],
            'type': f_type,
            'path': f['id'], # For API, path is ID
            'is_api': True
        })
        
    return render_template('drive_browser.html', source={'id': 'api', 'name': 'My Drive'}, items=items, breadcrumbs=[], is_api=True)

@drive_bp.route('/drive/api/open/<file_id>')
@login_required
def api_open_file(file_id):
    service = get_drive_service(current_user)
    if not service: return "Not connected", 401
    
    try:
        # Get metadata
        meta = get_file_metadata(service, file_id)
        if not meta: return "File not found", 404
        
        filename = meta['name']
        
        # Download to tmp
        cache_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'drive_cache')
        if not os.path.exists(cache_dir): os.makedirs(cache_dir)
        
        from werkzeug.utils import secure_filename
        safe_name = secure_filename(filename)
        
        file_path = os.path.join(cache_dir, safe_name)
        
        # Stream download directly to file if not exists
        if not os.path.exists(file_path):
            from gdrive_service import download_file_to_stream
            with open(file_path, 'wb') as f:
                download_file_to_stream(service, file_id, f)
            
        # Redirect to viewer
        if safe_name.lower().endswith('.pdf'):
            # Use raw route to serve from cache? No, create a specific route for cache serving or use existing
            # We can use send_from_directory logic.
            # Let's create a temporary route or use the image serving logic if it supports arbitrary paths?
            # Better: Create a route /drive/cache/<filename>
            file_url = url_for('drive.serve_cache_file', filename=safe_name)
            return render_template('pdfjs_viewer.html', pdf_url=file_url, pdf_title=filename)
        
        # If image
        if safe_name.lower().endswith(('.png', '.jpg', '.jpeg')):
             return send_from_directory(cache_dir, safe_name)
             
        return "File downloaded but type not supported for viewing.", 200
        
    except Exception as e:
        return f"Error opening file: {e}", 500

@drive_bp.route('/drive/cache/<filename>')
@login_required
def serve_cache_file(filename):
    cache_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'drive_cache')
    return send_from_directory(cache_dir, filename)

# ... (Keep existing add_source, delete_source, sync_task, sync_source, browse_drive, view_drive_file routes) ...
# I need to be careful not to delete them if I overwrite.
# I will append or merge carefully.
# Since I'm using `write_file`, I should include the OLD content too if I want to keep it.
# The prompt implies "add features".
# `browse_drive` is for the OLD public sync feature.
# The new API features are separate (`/drive/api/...`).
# I will Read the existing file first to preserve it, then append/modify.