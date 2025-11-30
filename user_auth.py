import sqlite3
from flask_login import LoginManager, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from utils import get_db_connection

class User(UserMixin):
    """User model for Flask-Login."""
    def __init__(self, id, username, email, password_hash, neetprep_enabled, dpi, color_rm_dpi):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.neetprep_enabled = neetprep_enabled
        self.dpi = dpi
        self.color_rm_dpi = color_rm_dpi

    @staticmethod
    def get(user_id):
        conn = get_db_connection()
        user_row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        if user_row:
            return User(user_row['id'], user_row['username'], user_row['email'], user_row['password_hash'], user_row['neetprep_enabled'], user_row['dpi'], dict(user_row).get('color_rm_dpi', 200))
        return None

    @staticmethod
    def get_by_username(username):
        conn = get_db_connection()
        user_row = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        if user_row:
            return User(user_row['id'], user_row['username'], user_row['email'], user_row['password_hash'], user_row['neetprep_enabled'], user_row['dpi'], dict(user_row).get('color_rm_dpi', 200))
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
