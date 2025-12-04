import sqlite3
import json
import time

DATABASE = 'database.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def migrate_subjective_questions():
    print("Starting migration of subjective questions...")
    conn = get_db_connection()
    
    try:
        # Fetch all subjective questions
        questions = conn.execute('SELECT * FROM subjective_questions').fetchall()
        
        updated_count = 0
        
        for q in questions:
            q_id = q['id']
            q_html = q['question_html']
            q_json = q['question_json']
            
            # Check if json is empty or None
            if not q_json or q_json.strip() == '':
                print(f"Migrating Question ID: {q_id}")
                
                # Create EditorJS block structure
                editor_js_data = {
                    "time": int(time.time() * 1000),
                    "blocks": [
                        {
                            "type": "paragraph",
                            "data": {
                                "text": q_html
                            }
                        }
                    ],
                    "version": "2.22.2" # Using a standard version
                }
                
                json_string = json.dumps(editor_js_data)
                
                # Update the record
                conn.execute(
                    'UPDATE subjective_questions SET question_json = ? WHERE id = ?',
                    (json_string, q_id)
                )
                updated_count += 1
        
        conn.commit()
        print(f"Migration completed. Updated {updated_count} questions.")
        
    except Exception as e:
        conn.rollback()
        print(f"Error during migration: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate_subjective_questions()
