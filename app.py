import os
from flask import Flask
from flask_cors import CORS
from datetime import datetime, date

from database import setup_database

def humanize_datetime(dt_str):
    """Converts a datetime string to a human-friendly format."""
    if not dt_str:
        return ""
    try:
        # Split the string at the decimal point to handle microseconds
        dt = datetime.fromisoformat(dt_str.split('.')[0])
        today = date.today()
        if dt.date() == today:
            return "Today"
        elif dt.date() == date.fromordinal(today.toordinal() - 1):
            return "Yesterday"
        else:
            return dt.strftime('%b %d, %Y')
    except (ValueError, TypeError):
        return dt_str # Return original string if parsing fails

def create_app():
    app = Flask(__name__)
    CORS(app)

    # Register custom Jinja2 filter
    app.jinja_env.filters['humanize'] = humanize_datetime
    app.jinja_env.filters['chr'] = chr

    # Configuration
    app.config['SECRET_KEY'] = os.urandom(24)
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 * 4096
    app.config['UPLOAD_FOLDER'] = 'uploads'
    app.config['PROCESSED_FOLDER'] = 'processed'
    app.config['OUTPUT_FOLDER'] = 'output'
    app.config['TEMP_FOLDER'] = 'tmp'

    # Ensure instance folders exist
    for folder in [app.config['UPLOAD_FOLDER'], app.config['PROCESSED_FOLDER'], app.config['OUTPUT_FOLDER'], app.config['TEMP_FOLDER']]:
        os.makedirs(folder, exist_ok=True)

    with app.app_context():
        setup_database()

    # Setup Login Manager
    from user_auth import setup_login_manager
    setup_login_manager(app)

    # Register Blueprints
    from routes import main_bp
    from json_processor import json_bp
    from neetprep import neetprep_bp
    from classifier_routes import classifier_bp
    from dashboard import dashboard_bp
    from image_routes import image_bp
    from auth_routes import auth_bp
    from settings_routes import settings_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(json_bp)
    app.register_blueprint(neetprep_bp)
    app.register_blueprint(classifier_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(image_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(settings_bp)

    return app

app = create_app()