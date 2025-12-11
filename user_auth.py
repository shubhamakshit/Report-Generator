import sqlite3
from flask_login import LoginManager, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from utils import get_db_connection

class User(UserMixin):
    """User model for Flask-Login."""
    def __init__(self, id, username, email, password_hash, neetprep_enabled, dpi, color_rm_dpi, v2_default=0, magnifier_enabled=1, google_token=None, classifier_model='gemini'):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.neetprep_enabled = neetprep_enabled
        self.dpi = dpi
        self.color_rm_dpi = color_rm_dpi
        self.v2_default = v2_default
        self.magnifier_enabled = magnifier_enabled
        self.google_token = google_token
        self.classifier_model = classifier_model

    @staticmethod
    def get(user_id):
        conn = get_db_connection()
        user_row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        if user_row:
            user_data = dict(user_row)
            return User(
                user_data['id'], 
                user_data['username'], 
                user_data['email'], 
                user_data['password_hash'], 
                user_data['neetprep_enabled'], 
                user_data['dpi'], 
                user_data.get('color_rm_dpi', 200),
                user_data.get('v2_default', 0),
                user_data.get('magnifier_enabled', 1),
                user_data.get('google_token'),
                user_data.get('classifier_model', 'gemini')
            )
        return None

    @staticmethod
    def get_by_username(username):
        conn = get_db_connection()
        user_row = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        if user_row:
            user_data = dict(user_row)
            return User(
                user_data['id'], 
                user_data['username'], 
                user_data['email'], 
                user_data['password_hash'], 
                user_data['neetprep_enabled'], 
                user_data['dpi'], 
                user_data.get('color_rm_dpi', 200),
                user_data.get('v2_default', 0),
                user_data.get('magnifier_enabled', 1),
                user_data.get('google_token'),
                user_data.get('classifier_model', 'gemini')
            )
        return None
    
    @staticmethod
    def create(username, email, password):
        password_hash = generate_password_hash(password)
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)',
                (username, email, password_hash)
            )
            conn.commit()
            new_id = cursor.lastrowid
            conn.close()
            return User.get(new_id)
        except sqlite3.IntegrityError:
            conn.close()
            return None # Username or email already exists

def setup_login_manager(app):
    """Initializes and configures the Flask-Login manager."""
    login_manager = LoginManager()
    login_manager.init_app(app)
    # This is the route Flask-Login will redirect to if a user tries to access
    # a page that requires authentication without being logged in.
    login_manager.login_view = 'auth.login'

    @login_manager.user_loader
    def load_user(user_id):
        return User.get(user_id)
