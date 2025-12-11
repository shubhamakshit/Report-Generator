import sqlite3
import os
import shutil
from datetime import datetime

DATABASE_PATH = 'database.db'
MIGRATIONS_DIR = 'migrations'
BACKUP_DIR = 'backups'

def backup_database():
    """Creates a timestamped backup of the database."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_filename = f"database_backup_{timestamp}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_filename)
    
    try:
        shutil.copy2(DATABASE_PATH, backup_path)
        print(f"Successfully created database backup at: {backup_path}")
        return backup_path
    except FileNotFoundError:
        print(f"Warning: Database file not found at {DATABASE_PATH}. Cannot create backup.")
        return None

def apply_migration(migration_file):
    """Applies a single SQL migration file to the database."""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        with open(migration_file, 'r') as f:
            sql_script = f.read()
        
        # Split script into individual statements
        statements = [s.strip() for s in sql_script.split(';') if s.strip()]
        
        print(f"Applying migration: {migration_file}...")
        for statement in statements:
            try:
                cursor.execute(statement)
                print(f"  Executed: {statement[:80]}...")
            except sqlite3.OperationalError as e:
                # This is a common error if the column already exists. We can treat it as a warning.
                if "duplicate column name" in str(e):
                    print(f"  Warning: {e}. Skipping statement.")
                else:
                    raise  # Re-raise other operational errors

        conn.commit()
        conn.close()
        print(f"Successfully applied migration: {migration_file}")
    except sqlite3.Error as e:
        print(f"Error applying migration {migration_file}: {e}")
        return False
    return True

def verify_migration():
    """Verifies that the new columns exist in the tables."""
    print("\nVerifying migration...")
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()

        # Verify 'questions' table
        cursor.execute("PRAGMA table_info(questions);")
        questions_columns = [row[1] for row in cursor.fetchall()]
        expected_q_cols = ['topic', 'time_taken', 'difficulty', 'source', 'test_id', 'test_mapping_id']
        missing_q_cols = [col for col in expected_q_cols if col not in questions_columns]
        if not missing_q_cols:
            print("✅ 'questions' table verification successful.")
        else:
            print(f"❌ 'questions' table verification failed. Missing columns: {missing_q_cols}")

        # Verify 'sessions' table
        cursor.execute("PRAGMA table_info(sessions);")
        sessions_columns = [row[1] for row in cursor.fetchall()]
        expected_s_cols = ['test_id', 'test_mapping_id', 'source', 'metadata']
        missing_s_cols = [col for col in expected_s_cols if col not in sessions_columns]
        if not missing_s_cols:
            print("✅ 'sessions' table verification successful.")
        else:
            print(f"❌ 'sessions' table verification failed. Missing columns: {missing_s_cols}")
            
        conn.close()
        
        return not missing_q_cols and not missing_s_cols

    except sqlite3.Error as e:
        print(f"Error during verification: {e}")
        return False

def main():
    """Main function to run the migration process."""
    print("--- Starting Database Migration ---")
    
    backup_path = backup_database()
    if not backup_path and os.path.exists(DATABASE_PATH):
        print("Aborting migration due to backup failure.")
        return

    migration_file = os.path.join(MIGRATIONS_DIR, 'add_v3_fields.sql')
    if not os.path.exists(migration_file):
        print(f"Error: Migration file not found at {migration_file}")
        return
        
    if apply_migration(migration_file):
        verify_migration()
    else:
        print("\nMigration failed. Please check the errors above.")
        print("You may need to restore from the backup if the database is in an inconsistent state.")

    print("--- Migration Process Finished ---")

if __name__ == "__main__":
    main()
