from flask import Blueprint, send_from_directory, current_app

image_bp = Blueprint('image_bp', __name__)

@image_bp.route('/processed/<path:filename>')
def serve_processed_image(filename):
    current_app.logger.info(f"Serving processed image: {filename}")
    return send_from_directory(current_app.config['PROCESSED_FOLDER'], filename)

@image_bp.route('/tmp/<path:filename>')
def serve_tmp_image(filename):
    current_app.logger.info(f"Serving temporary image: {filename}")
    return send_from_directory(current_app.config['TEMP_FOLDER'], filename)

# Proxy routes for /neetprep/processed and /neetprep/tmp
@image_bp.route('/neetprep/processed/<path:filename>')
def serve_neetprep_processed_image(filename):
    current_app.logger.info(f"Serving /neetprep/processed image: {filename}")
    return send_from_directory(current_app.config['PROCESSED_FOLDER'], filename)

@image_bp.route('/neetprep/tmp/<path:filename>')
def serve_neetprep_tmp_image(filename):
    current_app.logger.info(f"Serving /neetprep/tmp image: {filename}")
    return send_from_directory(current_app.config['TEMP_FOLDER'], filename)
