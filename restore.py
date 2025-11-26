
import sqlite3
import json
import os
import shutil
import zipfile
from database import setup_database

def restore_from_backup(zip_filename='backup.zip', tmp_dir='tmp_restore'):
    """
    Restores the application state from a zip backup.

    :param zip_filename: Name of the backup zip file.
    :param tmp_dir: Temporary directory to extract the backup.
    """
    if not os.path.exists(zip_filename):
        print(f"Backup file not found: {zip_filename}")
        return

    # Confirmation prompt
    confirm = input("This will wipe all existing data. Are you sure you want to continue? (y/n): ")
    if confirm.lower() != 'y':
        print("Restore operation cancelled.")
        return

    # 1. Clean existing data
    print("Cleaning existing data...")
    for dir_to_clean in ['instance', 'output', 'processed', 'uploads']:
        if os.path.exists(dir_to_clean):
            shutil.rmtree(dir_to_clean)
        os.makedirs(dir_to_clean)
    
    # 2. Recreate database schema
    print("Setting up new database schema...")
    setup_database()

    # 3. Extract the backup
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir)

    print(f"Extracting {zip_filename} to {tmp_dir}...")
    with zipfile.ZipFile(zip_filename, 'r') as zipf:
        zipf.extractall(tmp_dir)

    # 4. Restore database from JSON files
    try:
        conn = sqlite3.connect('instance/database.db')
        cursor = conn.cursor()

        json_files = [f for f in os.listdir(tmp_dir) if f.endswith('.json')] #
        for json_file in json_files:
            table_name = os.path.splitext(json_file)[0]
            file_path = os.path.join(tmp_dir, json_file)
            
            print(f"Restoring table: {table_name}")
            with open(file_path, 'r') as f:
                data = json.load(f)
            
            if not data:
                continue

            columns = data[0].keys()
            placeholders = ', '.join(['?' for _ in columns])
            query = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})"
            
            for row in data:
                values = [row.get(col) for col in columns]
                cursor.execute(query, values)
        
        conn.commit()
        print("Database restore complete.")

    except sqlite3.Error as e:
        print(f"Database error during restore: {e}")
    finally:
        if conn:
            conn.close()

    # 5. Restore file directories
    print("Restoring file directories...")
    for dir_name in ['output', 'processed', 'uploads']:
        source_dir = os.path.join(tmp_dir, dir_name)
        dest_dir = dir_name
        if os.path.exists(source_dir):
            # Copy contents, not the directory itself
            for item in os.listdir(source_dir):
                s = os.path.join(source_dir, item)
                d = os.path.join(dest_dir, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)
            print(f"Restored directory: {dir_name}")

    # 6. Clean up temporary directory
    shutil.rmtree(tmp_dir)
    print(f"Cleaned up temporary directory: {tmp_dir}")

    print("\nRestore complete!")

if __name__ == '__main__':
    restore_from_backup()
