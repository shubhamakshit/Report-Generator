from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from flask_socketio import emit, join_room
from app import socketio
from werkzeug.utils import secure_filename
from database import get_db_connection
import os
import uuid

camera_bp = Blueprint('camera', __name__)

@camera_bp.route('/camera_web')
@login_required
def camera_web():
    return render_template('camera_web.html')

@camera_bp.route('/camera_mobile')
@login_required
def camera_mobile():
    # camera_id can be used to select specific camera if needed, defaulting to back camera
    return render_template('camera_mobile.html')

# --- WebRTC Signaling ---

@socketio.on('join')
def handle_join(data):
    room = data.get('room', 'stream_room')
    join_room(room)
    print(f"Client joined room: {room}")
    emit('user_joined', {'message': 'A user has joined'}, room=room)

@socketio.on('offer')
def handle_offer(data):
    room = data.get('room', 'stream_room')
    print("Received offer")
    emit('offer', data['offer'], room=room, include_self=False)

@socketio.on('answer')
def handle_answer(data):
    room = data.get('room', 'stream_room')
    print("Received answer")
    emit('answer', data['answer'], room=room, include_self=False)

@socketio.on('candidate')
def handle_candidate(data):
    room = data.get('room', 'stream_room')
    print("Received candidate")
    emit('candidate', data['candidate'], room=room, include_self=False)

@socketio.on('remote_capture')
def handle_remote_capture(data):
    room = data.get('room', 'stream_room')
    print("Received remote capture request")
    emit('trigger_capture', {}, room=room, include_self=False)

@camera_bp.route('/camera/upload_captured_image', methods=['POST'])
@login_required
def upload_captured_image():
    if 'image' not in request.files:
        return jsonify({'error': 'No image file provided'}), 400
    
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file:
        session_id = str(uuid.uuid4())
        original_filename = secure_filename(file.filename) or f"captured_image_{session_id}.png"
        
        # Save to UPLOAD_FOLDER or TEMP_FOLDER
        # For captured images, TEMP_FOLDER is suitable, then processed further
        save_path = os.path.join(os.getcwd(), 'tmp', original_filename) # Using tmp folder relative to CWD
        file.save(save_path)

        conn = get_db_connection()
        try:
            conn.execute(
                'INSERT INTO sessions (id, original_filename, name, user_id, session_type) VALUES (?, ?, ?, ?, ?)',
                (session_id, original_filename, original_filename, current_user.id, 'image_capture')
            )
            # Insert the image into the images table
            conn.execute(
                'INSERT INTO images (session_id, image_index, filename, original_name, image_type) VALUES (?, ?, ?, ?, ?)',
                (session_id, 0, original_filename, original_filename, 'original')
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            return jsonify({'error': f'Database error: {str(e)}'}), 500
        finally:
            conn.close()
            
        return jsonify({'success': True, 'session_id': session_id, 'filename': original_filename})
    
    return jsonify({'error': 'Image capture failed'}), 500
