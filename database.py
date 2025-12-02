
import os
import sqlite3
from datetime import datetime, timedelta
from flask import current_app
from utils import get_db_connection

def setup_database():
    """Initializes the database and creates/updates tables as needed."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Create sessions table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        original_filename TEXT,
        persist INTEGER DEFAULT 0,
        name TEXT,
        user_id INTEGER,
        session_type TEXT DEFAULT 'standard'
    );
    """)

    # Create images table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS images (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        image_index INTEGER NOT NULL,
        filename TEXT NOT NULL,
        original_name TEXT NOT NULL,
        processed_filename TEXT,
        image_type TEXT DEFAULT 'original',
        FOREIGN KEY (session_id) REFERENCES sessions (id)
    );
    """)

    # Create questions table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        image_id INTEGER NOT NULL, 
        question_number TEXT,
        subject TEXT,
        status TEXT,
        marked_solution TEXT,
        actual_solution TEXT,
        time_taken TEXT,
        tags TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions (id),
        FOREIGN KEY (image_id) REFERENCES images (id)
    );
    """)

    # Create folders table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS folders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        parent_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (parent_id) REFERENCES folders (id) ON DELETE CASCADE
    );
    """)

    # Create generated_pdfs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS generated_pdfs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        subject TEXT NOT NULL,
        tags TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        source_filename TEXT,
        folder_id INTEGER,
        persist INTEGER DEFAULT 0,
        FOREIGN KEY (session_id) REFERENCES sessions (id),
        FOREIGN KEY (folder_id) REFERENCES folders (id) ON DELETE SET NULL
    );
    """)

    # Create neetprep_questions table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS neetprep_questions (
        id TEXT PRIMARY KEY,
        question_text TEXT,
        options TEXT,
        correct_answer_index INTEGER,
        level TEXT,
        topic TEXT,
        subject TEXT,
        last_fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Create neetprep_processed_attempts table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS neetprep_processed_attempts (
        attempt_id TEXT PRIMARY KEY,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Create subjective_folders table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subjective_folders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        parent_id INTEGER,
        user_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (parent_id) REFERENCES subjective_folders (id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users (id)
    );
    """)

    # Create subjective_questions table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subjective_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        question_topic TEXT NOT NULL,
        question_html TEXT NOT NULL,
        question_number_within_topic TEXT,
        folder_id INTEGER,
        topic_order INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (folder_id) REFERENCES subjective_folders (id) ON DELETE SET NULL
    );
    """)

    # --- Migrations ---
    try:
        cursor.execute("SELECT topic_order FROM subjective_questions LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE subjective_questions ADD COLUMN topic_order INTEGER DEFAULT 0")

    try:
        cursor.execute("SELECT folder_id FROM subjective_questions LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE subjective_questions ADD COLUMN folder_id INTEGER REFERENCES subjective_folders(id) ON DELETE SET NULL")

    try:
        cursor.execute("SELECT tags FROM questions LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE questions ADD COLUMN tags TEXT")

    try:
        cursor.execute("SELECT tags FROM questions LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE questions ADD COLUMN tags TEXT")

    try:
        cursor.execute("SELECT image_type FROM images LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE images ADD COLUMN image_type TEXT DEFAULT 'original'")

    try:
        cursor.execute("SELECT original_filename FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE sessions ADD COLUMN original_filename TEXT")

    try:
        cursor.execute("SELECT persist FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE sessions ADD COLUMN persist INTEGER DEFAULT 0")

    try:
        cursor.execute("SELECT name FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE sessions ADD COLUMN name TEXT")

    try:
        cursor.execute("SELECT persist FROM generated_pdfs LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE generated_pdfs ADD COLUMN persist INTEGER DEFAULT 0")

    try:
        cursor.execute("SELECT folder_id FROM generated_pdfs LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE generated_pdfs ADD COLUMN folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL")

    try:
        cursor.execute("SELECT question_text FROM questions LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE questions ADD COLUMN question_text TEXT")

    try:
        cursor.execute("SELECT chapter FROM questions LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE questions ADD COLUMN chapter TEXT")

    # --- Multi-user Migrations ---
    try:
        cursor.execute("SELECT user_id FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE sessions ADD COLUMN user_id INTEGER REFERENCES users(id)")

    try:
        cursor.execute("SELECT user_id FROM generated_pdfs LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE generated_pdfs ADD COLUMN user_id INTEGER REFERENCES users(id)")

    try:
        cursor.execute("SELECT user_id FROM folders LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE folders ADD COLUMN user_id INTEGER REFERENCES users(id)")

    try:
        cursor.execute("SELECT neetprep_enabled FROM users LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE users ADD COLUMN neetprep_enabled INTEGER DEFAULT 1")
    
    try:
        cursor.execute("SELECT dpi FROM users LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE users ADD COLUMN dpi INTEGER DEFAULT 100")

    try:
        cursor.execute("SELECT color_rm_dpi FROM users LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE users ADD COLUMN color_rm_dpi INTEGER DEFAULT 200")

    try:
        cursor.execute("SELECT session_type FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE sessions ADD COLUMN session_type TEXT DEFAULT 'standard'")

    conn.commit()
    conn.close()


def cleanup_old_data():
    """Removes sessions, files, and PDFs older than 1 day, unless persisted."""
    print("Running cleanup of old data...")
    conn = get_db_connection()
    cutoff = datetime.now() - timedelta(days=1)
    
    old_sessions = conn.execute('SELECT id FROM sessions WHERE created_at < ? AND persist = 0', (cutoff,)).fetchall()
    
    for session in old_sessions:
        session_id = session['id']
        print(f"Deleting old session: {session_id}")
        
        images_to_delete = conn.execute('SELECT filename, processed_filename FROM images WHERE session_id = ?', (session_id,)).fetchall()
        for img in images_to_delete:
            if img['filename']:
                try: os.remove(os.path.join(current_app.config['UPLOAD_FOLDER'], img['filename']))
                except OSError: pass
            if img['processed_filename']:
                try: os.remove(os.path.join(current_app.config['PROCESSED_FOLDER'], img['processed_filename']))
                except OSError: pass

        conn.execute('DELETE FROM questions WHERE session_id = ?', (session_id,))
        conn.execute('DELETE FROM images WHERE session_id = ?', (session_id,))
        conn.execute('DELETE FROM sessions WHERE id = ?', (session_id,))

    old_pdfs = conn.execute('SELECT id, filename FROM generated_pdfs WHERE created_at < ? AND persist = 0', (cutoff,)).fetchall()
    for pdf in old_pdfs:
        pdf_id, pdf_filename = pdf['id'], pdf['filename']
        print(f"Deleting old generated PDF: {pdf_filename}")
        try:
            os.remove(os.path.join(current_app.config['OUTPUT_FOLDER'], pdf_filename))
        except OSError:
            pass
        conn.execute('DELETE FROM generated_pdfs WHERE id = ?', (pdf_id,))

    db_filenames = {row['filename'] for row in conn.execute('SELECT filename FROM generated_pdfs').fetchall()}
    for filename in os.listdir(current_app.config['OUTPUT_FOLDER']):
        if filename not in db_filenames:
            file_path = os.path.join(current_app.config['OUTPUT_FOLDER'], filename)
            file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
            if file_mtime < cutoff:
                print(f"Deleting old, orphaned PDF: {filename}")
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            
    conn.commit()
    conn.close()
    print("Cleanup finished.")

def get_folder_tree(user_id=None):
    conn = get_db_connection()
    if user_id:
        folders = conn.execute('SELECT id, name, parent_id FROM folders WHERE user_id = ? ORDER BY name', (user_id,)).fetchall()
    else:
        # Fallback for old behavior or admin views
        folders = conn.execute('SELECT id, name, parent_id FROM folders ORDER BY name').fetchall()
    conn.close()
    
    folder_map = {f['id']: dict(f) for f in folders}
    tree = []
    
    for folder_id, folder in folder_map.items():
        if folder['parent_id']:
            parent = folder_map.get(folder['parent_id'])
            if parent:
                if 'children' not in parent:
                    parent['children'] = []
                parent['children'].append(folder)
        else:
            tree.append(folder)
            
    return tree

def get_subjective_folder_tree(user_id):
    conn = get_db_connection()
    folders = conn.execute('SELECT id, name, parent_id FROM subjective_folders WHERE user_id = ? ORDER BY name', (user_id,)).fetchall()
    conn.close()
    
    folder_map = {f['id']: dict(f) for f in folders}
    tree = []
    
    for folder_id, folder in folder_map.items():
        if folder['parent_id']:
            parent = folder_map.get(folder['parent_id'])
            if parent:
                if 'children' not in parent:
                    parent['children'] = []
                parent['children'].append(folder)
        else:
            tree.append(folder)
            
    return tree

def get_all_descendant_folder_ids(conn, folder_id, user_id=None):
    """Recursively gets all descendant folder IDs for a given folder, scoped to a user."""
    if user_id:
        children = conn.execute('SELECT id FROM folders WHERE parent_id = ? AND user_id = ?', (folder_id, user_id)).fetchall()
    else:
        children = conn.execute('SELECT id FROM folders WHERE parent_id = ?', (folder_id,)).fetchall()
        
    folder_ids = [f['id'] for f in children]
    for child_id in folder_ids:
        folder_ids.extend(get_all_descendant_folder_ids(conn, child_id, user_id))
    return folder_ids
