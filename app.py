import os
from flask import Flask
from flask_cors import CORS

from database import setup_database

def create_app():
    app = Flask(__name__)
    CORS(app)

    # Configuration
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 * 4096
    app.config['UPLOAD_FOLDER'] = 'uploads'
    app.config['PROCESSED_FOLDER'] = 'processed'
    app.config['OUTPUT_FOLDER'] = 'output'

    for folder in [app.config['UPLOAD_FOLDER'], app.config['PROCESSED_FOLDER'], app.config['OUTPUT_FOLDER']]:
        os.makedirs(folder, exist_ok=True)

    with app.app_context():
        setup_database()

    # Register Blueprints
    from routes import main_bp
    from json_processor import json_bp
    from neetprep import neetprep_bp
    from classifier_routes import classifier_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(json_bp)
    app.register_blueprint(neetprep_bp)
    app.register_blueprint(classifier_bp)

    return app

app = create_app()