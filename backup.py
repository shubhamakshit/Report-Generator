import sqlite3
import json
import os
import shutil
import zipfile

def backup_database_and_files(db_path='instance/database.db', backup_dir='backup', zip_filename='backup.zip'):
    """
    Exports all tables from the SQLite database to JSON files, backs up associated files,
    and creates a zip archive of the backup.

    :param db_path: Path to the SQLite database file.
    :param backup_dir: Directory to save the backup.
    :param zip_filename: Name of the output zip file.
    """
    if os.path.exists(backup_dir):
        shutil.rmtree(backup_dir)
    os.makedirs(backup_dir)

    # 1. Backup the database to JSON files
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]

        for table_name in tables:
            print(f"Backing up table: {table_name}")
            cursor.execute(f"SELECT * FROM {table_name}")
            rows = cursor.fetchall()
            data = [dict(row) for row in rows]
            
            backup_file_path = os.path.join(backup_dir, f"{table_name}.json")
            with open(backup_file_path, 'w') as f:
                json.dump(data, f, indent=4)
            
            print(f"Successfully backed up {table_name} to {backup_file_path}")

    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return
    finally:
        if conn:
            conn.close()

    # 2. Backup associated files
    file_dirs_to_backup = ['output', 'processed', 'uploads']
    for dir_name in file_dirs_to_backup:
        source_dir = dir_name
        dest_dir = os.path.join(backup_dir, dir_name)
        
        if os.path.exists(source_dir):
            print(f"Backing up directory: {source_dir}")
            shutil.copytree(source_dir, dest_dir)
            print(f"Successfully backed up {source_dir} to {dest_dir}")
        else:
            print(f"Directory not found, skipping: {source_dir}")

    # 3. Create a zip archive of the backup directory
    print(f"\nCreating zip archive: {zip_filename}")
    with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(backup_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, backup_dir)
                zipf.write(file_path, arcname)
    
    print(f"Successfully created {zip_filename}")

    # 4. Clean up the backup directory
    shutil.rmtree(backup_dir)
    print(f"Cleaned up backup directory: {backup_dir}")

    print("\nBackup complete!")

if __name__ == '__main__':
    backup_database_and_files()
