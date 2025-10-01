import sqlite3

def get_full_path(conn, folder_id, path_cache):
    """Recursively gets the full path string for a given folder_id."""
    if folder_id is None:
        return ""
    if folder_id in path_cache:
        return path_cache[folder_id]

    cursor = conn.cursor()
    cursor.execute("SELECT name, parent_id FROM folders WHERE id = ?", (folder_id,))
    folder = cursor.fetchone()
    
    if not folder:
        return ""

    parent_path = get_full_path(conn, folder['parent_id'], path_cache)
    full_path = f"{parent_path}/{folder['name']}" if parent_path else folder['name']
    
    path_cache[folder_id] = full_path
    return full_path

def migrate():
    """Migrates the database from the new folder system back to the old group_name system."""
    conn = None
    try:
        conn = sqlite3.connect('database.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        print("Starting reverse migration...")

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='folders';")
        if cursor.fetchone() is None:
            print("'folders' table not found. Database seems to be in the old format already. No migration needed.")
            return

        try:
            cursor.execute("SELECT group_name FROM generated_pdfs LIMIT 1")
        except sqlite3.OperationalError:
            print("Adding temporary 'group_name' column to generated_pdfs.")
            cursor.execute("ALTER TABLE generated_pdfs ADD COLUMN group_name TEXT")

        cursor.execute("SELECT id, folder_id FROM generated_pdfs")
        pdfs = cursor.fetchall()
        path_cache = {}

        print(f"Found {len(pdfs)} PDFs to process.")
        for i, pdf in enumerate(pdfs):
            if pdf['folder_id']:
                full_path = get_full_path(conn, pdf['folder_id'], path_cache)
                cursor.execute("UPDATE generated_pdfs SET group_name = ? WHERE id = ?", (full_path, pdf['id']))
            else:
                cursor.execute("UPDATE generated_pdfs SET group_name = NULL WHERE id = ?", (pdf['id'],))
            
            if (i + 1) % 50 == 0:
                print(f"Processed {i + 1}/{len(pdfs)} PDFs...")
        
        conn.commit() # Commit all the group_name updates first.
        print("All PDFs have been updated with the old group_name format.")

        print("Cleaning up new schema...")
        cursor.execute("DROP TABLE folders")
        
        cursor.execute("PRAGMA foreign_keys=off;")
        
        cursor.execute("""
        CREATE TABLE generated_pdfs_new(
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, filename TEXT, subject TEXT, 
            tags TEXT, notes TEXT, created_at TIMESTAMP, source_filename TEXT, 
            persist INTEGER, group_name TEXT
        );
        """)
        cursor.execute("INSERT INTO generated_pdfs_new(id, session_id, filename, subject, tags, notes, created_at, source_filename, persist, group_name) SELECT id, session_id, filename, subject, tags, notes, created_at, source_filename, persist, group_name FROM generated_pdfs;")
        cursor.execute("DROP TABLE generated_pdfs;")
        cursor.execute("ALTER TABLE generated_pdfs_new RENAME TO generated_pdfs;")
        
        conn.commit() # Commit the schema changes
        cursor.execute("PRAGMA foreign_keys=on;")

        print("Cleanup complete.")
        print('\nMigration successful! The database is now compatible with the old app.py.')

    except Exception as e:
        print(f"An error occurred during migration: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    migrate()