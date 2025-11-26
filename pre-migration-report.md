# Pre-Migration Report: Single-User to Multi-User Architecture

This document outlines the necessary changes to migrate the DocuPDF application from a single-user to a multi-user architecture. The migration is designed to be completed in phases, ensuring that existing data is preserved and correctly associated with the primary user.

---

## Phase 1: User Authentication Foundation

This phase introduces the core concepts of users and authentication.

### 1.1 New `users` Table

A new table will be created to store user credentials.

```sql
-- file: database.py (addition)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 1.2 New File: `user_manager.py`

A new file will handle user session management, password hashing, and provide the user model required by Flask-Login.

```python
# file: user_manager.py (new file)
from flask_login import LoginManager, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from utils import get_db_connection

class User(UserMixin):
    def __init__(self, id, username, email, password_hash):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash

    @staticmethod
    def get(user_id):
        conn = get_db_connection()
        user_row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        if user_row:
            return User(user_row['id'], user_row['username'], user_row['email'], user_row['password_hash'])
        return None

    @staticmethod
    def get_by_username(username):
        conn = get_db_connection()
        user_row = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        if user_row:
            return User(user_row['id'], user_row['username'], user_row['email'], user_row['password_hash'])
        return None

def setup_login_manager(app):
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'user_auth.login' # New blueprint for user auth

    @login_manager.user_loader
    def load_user(user_id):
        return User.get(user_id)

# (Additional functions for creating users, etc. will be added here)
```

### 1.3 Application Setup (`app.py`)

The main `app.py` will be updated to initialize the `LoginManager` and register the new authentication blueprint.

```python
# file: app.py (changes)
# Current
def create_app():
    app = Flask(__name__)
    # ...
    # Register Blueprints
    from routes import main_bp
    # ...
    app.register_blueprint(main_bp)
    return app

# After
from flask_login import LoginManager

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.urandom(24) # Important for session security
    # ...
    
    # Setup Login Manager
    from user_manager import setup_login_manager
    setup_login_manager(app)

    # Register Blueprints
    from routes import main_bp
    from user_auth_routes import auth_bp # New blueprint for login/register
    # ...
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    return app
```

### 1.4 New Templates: `login.html` and `register.html`

New HTML templates will be created for the user login and registration forms. These will be standard forms with fields for username, password, and email.

---

## Phase 2: Database and Data Segregation

This phase links all application data to specific users.

### 2.1 Database Schema Changes

The following tables will be altered to include a `user_id` foreign key.

```sql
-- file: database.py (migrations)

-- Add user_id to sessions
ALTER TABLE sessions ADD COLUMN user_id INTEGER REFERENCES users(id);

-- Add user_id to generated_pdfs
ALTER TABLE generated_pdfs ADD COLUMN user_id INTEGER REFERENCES users(id);

-- Add user_id to folders
ALTER TABLE folders ADD COLUMN user_id INTEGER REFERENCES users(id);
```

### 2.2 Backend Logic Changes (Code-by-Code)

All routes and functions that interact with user-specific data must be updated. This will be done by using the `current_user` object provided by Flask-Login after a user logs in. All routes will also be protected with the `@login_required` decorator.

#### **`routes.py`**

##### `v2_upload()`

**Current:**
```python
@main_bp.route('/v2/upload', methods=['POST'])
def v2_upload():
    session_id = str(uuid.uuid4())
    # ...
    conn = get_db_connection()
    conn.execute('INSERT INTO sessions (id, original_filename, name) VALUES (?, ?, ?)', (session_id, original_filename, original_filename))
    # ...
```

**After:**
```python
from flask_login import login_required, current_user

@main_bp.route('/v2/upload', methods=['POST'])
@login_required
def v2_upload():
    session_id = str(uuid.uuid4())
    # ...
    conn = get_db_connection()
    conn.execute('INSERT INTO sessions (id, original_filename, name, user_id) VALUES (?, ?, ?, ?)', 
                 (session_id, original_filename, original_filename, current_user.id))
    # ...
```

##### `question_entry_v2(session_id)`

**Current:**
```python
@main_bp.route('/question_entry_v2/<session_id>')
def question_entry_v2(session_id):
    conn = get_db_connection()
    session_data = conn.execute(
        'SELECT original_filename, subject, tags, notes FROM sessions WHERE id = ?', (session_id,)
    ).fetchone()
    #...
```

**After:**
```python
from flask_login import login_required, current_user

@main_bp.route('/question_entry_v2/<session_id>')
@login_required
def question_entry_v2(session_id):
    conn = get_db_connection()
    # Add user_id check to prevent unauthorized access
    session_data = conn.execute(
        'SELECT original_filename, subject, tags, notes FROM sessions WHERE id = ? AND user_id = ?', 
        (session_id, current_user.id)
    ).fetchone()
    if not session_data:
        return "Unauthorized", 403
    #...
```
*(Note: This pattern of adding `@login_required` and `AND user_id = ?` to queries will be repeated for almost every route in `routes.py`, `dashboard.py`, `json_processor.py`, etc. The examples above illustrate the core change.)*

#### **`dashboard.py`**

##### `dashboard()`

**Current:**
```python
@dashboard_bp.route('/dashboard')
def dashboard():
    conn = get_db_connection()
    sessions_rows = conn.execute("""
        SELECT s.id, ...
        FROM sessions s
        ...
    """).fetchall()
    #...
```

**After:**
```python
from flask_login import login_required, current_user

@dashboard_bp.route('/dashboard')
@login_required
def dashboard():
    conn = get_db_connection()
    sessions_rows = conn.execute("""
        SELECT s.id, ...
        FROM sessions s
        LEFT JOIN images i ON s.id = i.session_id
        WHERE s.user_id = ?
        GROUP BY s.id, ...
        ORDER BY s.created_at DESC
    """, (current_user.id,)).fetchall()
    #...
```

---

## Phase 3: Security and UI

This phase focuses on the user-facing elements and securing file access.

### 3.1 UI Navigation (`_nav_links.html`)

The navigation links will be updated to show context-aware links for login, registration, and logout.

**Current:**
```html
<!-- file: templates/_nav_links.html -->
<div class="navbar-nav ms-auto">
    <a class="nav-link" href="/dashboard">...</a>
    <a class="nav-link" href="{{ url_for('neetprep_bp.index') }}">...</a>
    ...
</div>
```

**After:**
```html
<!-- file: templates/_nav_links.html -->
<div class="navbar-nav ms-auto">
    {% if current_user.is_authenticated %}
        <li class="nav-item">
            <span class="navbar-text">Welcome, {{ current_user.username }}</span>
        </li>
        <a class="nav-link" href="/dashboard">...</a>
        <a class="nav-link" href="{{ url_for('neetprep_bp.index') }}">...</a>
        ...
        <a class="nav-link" href="{{ url_for('user_auth.logout') }}">
            <i class="bi bi-box-arrow-right me-1"></i> Logout
        </a>
    {% else %}
        <a class="nav-link" href="{{ url_for('user_auth.login') }}">
            <i class="bi bi-box-arrow-in-right me-1"></i> Login
        </a>
        <a class="nav-link" href="{{ url_for('user_auth.register') }}">
            <i class="bi bi-person-plus-fill me-1"></i> Register
        </a>
    {% endif %}
</div>
```

### 3.2 Secure File Access (`routes.py`)

Routes that serve files directly must check for ownership before sending the file.

##### `download_file(filename)`

**Current:**
```python
# file: routes.py
@main_bp.route('/download/<filename>')
def download_file(filename):
    return send_file(os.path.join(current_app.config['OUTPUT_FOLDER'], filename), as_attachment=True)
```

**After:**
```python
# file: routes.py
from flask_login import login_required, current_user

@main_bp.route('/download/<filename>')
@login_required
def download_file(filename):
    conn = get_db_connection()
    # Check if the requested file belongs to the current user
    pdf_owner = conn.execute(
        'SELECT user_id FROM generated_pdfs WHERE filename = ?', (filename,)
    ).fetchone()
    conn.close()

    if pdf_owner and pdf_owner['user_id'] == current_user.id:
        return send_file(os.path.join(current_app.config['OUTPUT_FOLDER'], filename), as_attachment=True)
    else:
        return "Unauthorized", 403
```

---

## Data Migration Script (Conceptual)

A one-time script will be created to migrate the existing data.

```python
# file: migrate_to_multiuser.py (conceptual)
import sqlite3
from werkzeug.security import generate_password_hash

def migrate():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    # 1. Create a default user (credentials should be provided securely)
    default_username = 'admin' # Or your preferred username
    default_password = 'your_secure_password'
    password_hash = generate_password_hash(default_password)
    
    try:
        cursor.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
            (default_username, 'admin@local.host', password_hash)
        )
        user_id = cursor.lastrowid
        print(f"Created default user '{default_username}' with ID {user_id}")
    except sqlite3.IntegrityError:
        print("Default user already exists.")
        user_id = cursor.execute("SELECT id FROM users WHERE username = ?", (default_username,)).fetchone()[0]

    # 2. Add user_id columns (This should be done via ALTER TABLE statements first)
    # ...

    # 3. Assign all existing data to the default user
    tables_to_update = ['sessions', 'generated_pdfs', 'folders']
    for table in tables_to_update:
        try:
            cursor.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (user_id,))
            print(f"Assigned {cursor.rowcount} records in '{table}' to user {user_id}")
        except sqlite3.OperationalError as e:
            print(f"Could not update table {table}. Maybe user_id column doesn't exist? Error: {e}")

    conn.commit()
    conn.close()
    print("Data migration complete.")

if __name__ == '__main__':
    migrate()
```
