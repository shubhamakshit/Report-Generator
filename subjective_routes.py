from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from database import get_db_connection, get_subjective_folder_tree, get_all_descendant_folder_ids
from gemini_subjective import generate_subjective_questions
from werkzeug.utils import secure_filename
import json
import os
import re # Import the regular expression module

subjective_bp = Blueprint('subjective', __name__)

# Helper function for natural sorting
def natural_sort_key(s):
    if s is None:
        return (0, "") # Treat None as 0 and empty string for comparison
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', str(s))]

@subjective_bp.route('/subjective_generator', methods=['GET'])
@login_required
def generator():
    return render_template('subjective_generator.html')

@subjective_bp.route('/generate_subjective', methods=['POST'])
@login_required
def generate():
    if 'image' not in request.files:
        flash('No image file provided.', 'danger')
        return redirect(url_for('subjective.generator'))
    
    file = request.files['image']
    if file.filename == '':
        flash('No selected file.', 'danger')
        return redirect(url_for('subjective.generator'))
        
    if file:
        filename = secure_filename(file.filename)
        temp_path = os.path.join(current_app.config['TEMP_FOLDER'], filename)
        file.save(temp_path)
        
        result = generate_subjective_questions(temp_path)
        
        # Cleanup
        try:
            os.remove(temp_path)
        except OSError:
            pass
            
        if result and result.get('success'):
            grouped_questions = {}
            for q in result.get('data', []):
                topic = q.get('question_topic', 'Uncategorized')
                if topic not in grouped_questions:
                    grouped_questions[topic] = []
                grouped_questions[topic].append(q)
            
            # Sort questions within each topic group by question_number_within_topic
            for topic_name in grouped_questions:
                grouped_questions[topic_name] = sorted(
                    grouped_questions[topic_name],
                    key=lambda q: int(q['question_number_within_topic']) if q['question_number_within_topic'].isdigit() else q['question_number_within_topic']
                )
                
            page_title_topic = "Extracted Questions"
            if result.get('data') and len(result.get('data')) > 0:
                # Use the topic of the first question as a general page title, or keep 'Extracted Questions'
                page_title_topic = result.get('data')[0].get('question_topic', "Extracted Questions")

            return render_template('subjective_results.html', grouped_questions=grouped_questions, topic=page_title_topic)
        else:
            flash('Failed to generate questions from image. Please try again.', 'danger')
            return redirect(url_for('subjective.generator'))
    
    return redirect(url_for('subjective.generator'))

@subjective_bp.route('/save_subjective', methods=['POST'])
@login_required
def save():
    if request.is_json:
        data = request.get_json()
        questions = data.get('questions', [])
        target_folder_id = data.get('folder_id') # Optional: save directly to a folder
    else:
        questions = []
        target_folder_id = None

    if not questions:
         return jsonify({'success': False, 'message': 'No questions to save.'}), 400

    conn = get_db_connection()
    try:
        for q in questions:
            conn.execute('''
                INSERT INTO subjective_questions (user_id, question_topic, question_html, question_number_within_topic, folder_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (current_user.id, q['question_topic'], q['question_html'], q['question_number_within_topic'], target_folder_id))
        conn.commit()
        flash(f'{len(questions)} questions saved successfully!', 'success')
        return jsonify({'success': True, 'redirect_url': url_for('subjective.list_questions')})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        conn.close()

@subjective_bp.route('/subjective_list')
@subjective_bp.route('/subjective_list/<path:folder_path>')
@login_required
def list_questions(folder_path=''):
    conn = get_db_connection()
    
    # Folder Navigation Logic
    folder_id = None
    breadcrumbs = []
    
    if folder_path:
        parts = folder_path.split('/')
        parent_id = None
        for i, part in enumerate(parts):
            res = conn.execute("SELECT id FROM subjective_folders WHERE name = ? AND user_id = ? AND (parent_id = ? OR (? IS NULL AND parent_id IS NULL))", (part, current_user.id, parent_id, parent_id)).fetchone()
            if not res:
                conn.close()
                flash('Folder not found.', 'danger')
                return redirect(url_for('subjective.list_questions'))
            parent_id = res['id']
            breadcrumbs.append({'name': part, 'path': '/'.join(parts[:i+1])})
        folder_id = parent_id

    # Fetch Subfolders
    if folder_id:
        subfolders = conn.execute('SELECT * FROM subjective_folders WHERE parent_id = ? AND user_id = ? ORDER BY name', (folder_id, current_user.id)).fetchall()
        questions_rows = conn.execute('SELECT * FROM subjective_questions WHERE folder_id = ? AND user_id = ? ORDER BY created_at DESC', (folder_id, current_user.id)).fetchall()
    else:
        subfolders = conn.execute('SELECT * FROM subjective_folders WHERE parent_id IS NULL AND user_id = ? ORDER BY name', (current_user.id,)).fetchall()
        questions_rows = conn.execute('SELECT * FROM subjective_questions WHERE folder_id IS NULL AND user_id = ? ORDER BY created_at DESC', (current_user.id,)).fetchall()

    conn.close()
    
    # Convert to dicts to ensure template compatibility
    subfolders = [dict(row) for row in subfolders]
    questions_rows = [dict(row) for row in questions_rows]
    
    # Group questions by topic and find representative topic_order
    temp_grouped = {}
    topic_orders = {}

    for q in questions_rows:
        topic = q['question_topic']
        if topic not in temp_grouped:
            temp_grouped[topic] = []
            # Default order 0 if None
            topic_orders[topic] = q.get('topic_order') or 0
        temp_grouped[topic].append(q)
            
    # Sort topics based on topic_order
    sorted_topics = sorted(topic_orders.keys(), key=lambda t: topic_orders[t])
    
    grouped_questions = {}
    for topic in sorted_topics:
        # Sort questions within topic
        questions = sorted(
            temp_grouped[topic],
            key=lambda q: natural_sort_key(q.get('question_number_within_topic'))
        )
        grouped_questions[topic] = questions
    
    folder_tree = get_subjective_folder_tree(current_user.id)

    return render_template(
        'subjective_list.html', 
        grouped_questions=grouped_questions, 
        subfolders=subfolders, 
        breadcrumbs=breadcrumbs, 
        current_folder_id=folder_id,
        folder_tree=folder_tree
    )

@subjective_bp.route('/subjective/question/add', methods=['POST'])
@login_required
def add_subjective_question():
    data = request.json
    topic = data.get('topic')
    html = data.get('html')
    number = data.get('number')
    folder_id = data.get('folder_id')

    if not topic or not html:
        return jsonify({'error': 'Topic and Question are required'}), 400

    conn = get_db_connection()
    try:
        conn.execute(
            'INSERT INTO subjective_questions (user_id, question_topic, question_html, question_number_within_topic, folder_id) VALUES (?, ?, ?, ?, ?)',
            (current_user.id, topic, html, number, folder_id)
        )
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@subjective_bp.route('/subjective/question/update/<int:question_id>', methods=['POST'])
@login_required
def update_subjective_question(question_id):
    data = request.json
    topic = data.get('topic')
    html = data.get('html')
    number = data.get('number')

    if not topic or not html:
        return jsonify({'error': 'Topic and Question are required'}), 400

    conn = get_db_connection()
    try:
        # Check ownership
        owner = conn.execute('SELECT user_id FROM subjective_questions WHERE id = ?', (question_id,)).fetchone()
        if not owner or owner['user_id'] != current_user.id:
            conn.close()
            return jsonify({'error': 'Unauthorized'}), 403

        conn.execute(
            'UPDATE subjective_questions SET question_topic = ?, question_html = ?, question_number_within_topic = ? WHERE id = ?',
            (topic, html, number, question_id)
        )
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@subjective_bp.route('/subjective/question/delete/<int:question_id>', methods=['DELETE'])
@login_required
def delete_subjective_question(question_id):
    conn = get_db_connection()
    try:
        # Check ownership
        owner = conn.execute('SELECT user_id FROM subjective_questions WHERE id = ?', (question_id,)).fetchone()
        if not owner or owner['user_id'] != current_user.id:
            conn.close()
            return jsonify({'error': 'Unauthorized'}), 403

        conn.execute('DELETE FROM subjective_questions WHERE id = ?', (question_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@subjective_bp.route('/subjective/topic/rename', methods=['POST'])
@login_required
def rename_subjective_topic():
    data = request.json
    old_topic = data.get('old_topic')
    new_topic = data.get('new_topic')
    folder_id = data.get('folder_id')

    if not old_topic or not new_topic:
        return jsonify({'error': 'Topic names required'}), 400

    conn = get_db_connection()
    try:
        # Scope update to folder or root
        if folder_id:
             conn.execute(
                'UPDATE subjective_questions SET question_topic = ? WHERE question_topic = ? AND folder_id = ? AND user_id = ?',
                (new_topic, old_topic, folder_id, current_user.id)
            )
        else:
             conn.execute(
                'UPDATE subjective_questions SET question_topic = ? WHERE question_topic = ? AND folder_id IS NULL AND user_id = ?',
                (new_topic, old_topic, current_user.id)
            )
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@subjective_bp.route('/subjective/topic/delete', methods=['POST'])
@login_required
def delete_subjective_topic():
    data = request.json
    topic = data.get('topic')
    folder_id = data.get('folder_id')

    if not topic:
        return jsonify({'error': 'Topic name required'}), 400

    conn = get_db_connection()
    try:
        if folder_id:
             conn.execute(
                'DELETE FROM subjective_questions WHERE question_topic = ? AND folder_id = ? AND user_id = ?',
                (topic, folder_id, current_user.id)
            )
        else:
             conn.execute(
                'DELETE FROM subjective_questions WHERE question_topic = ? AND folder_id IS NULL AND user_id = ?',
                (topic, current_user.id)
            )
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@subjective_bp.route('/subjective/topic/reorder', methods=['POST'])
@login_required
def reorder_subjective_topics():
    data = request.json
    topic_order = data.get('topic_order', [])
    folder_id = data.get('folder_id')

    if not topic_order:
        return jsonify({'error': 'Topic order list required'}), 400

    conn = get_db_connection()
    try:
        for index, topic in enumerate(topic_order):
            # We use a negative index or just the index. 
            # To show "first", we want lower numbers.
            if folder_id:
                conn.execute(
                    'UPDATE subjective_questions SET topic_order = ? WHERE question_topic = ? AND folder_id = ? AND user_id = ?',
                    (index, topic, folder_id, current_user.id)
                )
            else:
                conn.execute(
                    'UPDATE subjective_questions SET topic_order = ? WHERE question_topic = ? AND folder_id IS NULL AND user_id = ?',
                    (index, topic, current_user.id)
                )
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@subjective_bp.route('/subjective/create_folder', methods=['POST'])
@login_required
def create_folder():
    data = request.json
    name = data.get('name')
    parent_id = data.get('parent_id')
    
    if not name:
        return jsonify({'error': 'Folder name is required'}), 400
        
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO subjective_folders (name, parent_id, user_id) VALUES (?, ?, ?)', (name, parent_id, current_user.id))
        new_id = cursor.lastrowid
        conn.commit()
        return jsonify({'success': True, 'id': new_id, 'name': name, 'parent_id': parent_id})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@subjective_bp.route('/subjective/move_items', methods=['POST'])
@login_required
def move_items():
    data = request.json
    question_ids = data.get('question_ids', [])
    folder_ids = data.get('folder_ids', [])
    target_folder_id = data.get('target_folder_id')
    
    if not question_ids and not folder_ids:
        return jsonify({'error': 'No items selected'}), 400
        
    conn = get_db_connection()
    try:
        # Verify target folder ownership if not root
        if target_folder_id:
            owner = conn.execute('SELECT user_id FROM subjective_folders WHERE id = ?', (target_folder_id,)).fetchone()
            if not owner or owner['user_id'] != current_user.id:
                conn.close()
                return jsonify({'error': 'Unauthorized target folder'}), 403

        # Move Questions
        if question_ids:
            placeholders = ', '.join('?' * len(question_ids))
            conn.execute(f'UPDATE subjective_questions SET folder_id = ? WHERE id IN ({placeholders}) AND user_id = ?', (target_folder_id, *question_ids, current_user.id))

        # Move Folders
        if folder_ids:
            # Prevent moving a folder into itself
            if target_folder_id and int(target_folder_id) in [int(fid) for fid in folder_ids]:
                 conn.close()
                 return jsonify({'error': 'Cannot move a folder into itself.'}), 400
            
            # Ideally, we should also check for circular dependencies (moving parent into child), 
            # but for simplicity we'll just do the basic check above and basic ownership check.
            placeholders = ', '.join('?' * len(folder_ids))
            conn.execute(f'UPDATE subjective_folders SET parent_id = ? WHERE id IN ({placeholders}) AND user_id = ?', (target_folder_id, *folder_ids, current_user.id))

        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()
