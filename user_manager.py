
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g
from database import get_db_connection

user_manager_bp = Blueprint('user_manager', __name__)

@user_manager_bp.route('/users')
def users_list():
    conn = get_db_connection()
    users = conn.execute('SELECT id, username FROM users').fetchall()
    conn.close()
    return render_template('users.html', users=users)

@user_manager_bp.route('/users/create', methods=['POST'])
def create_user():
    username = request.form.get('username')
    if not username:
        flash('Username cannot be empty!', 'danger')
        return redirect(url_for('user_manager.users_list'))

    conn = get_db_connection()
    try:
        conn.execute('INSERT INTO users (username) VALUES (?)', (username,))
        conn.commit()
        flash(f'User {username} created successfully!', 'success')
    except Exception as e:
        flash(f'Error creating user: {e}', 'danger')
    finally:
        conn.close()
    return redirect(url_for('user_manager.users_list'))

@user_manager_bp.route('/switch_user/<username>')
def switch_user(username):
    conn = get_db_connection()
    user = conn.execute('SELECT id, username FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()

    if user:
        session['user_id'] = user['id']
        session['username'] = user['username']
        flash(f'Switched to user {username}', 'success')
    else:
        flash(f'User {username} not found', 'danger')
    
    # Redirect to the dashboard of the switched user
    return redirect(url_for('dashboard.dashboard', username=username))
