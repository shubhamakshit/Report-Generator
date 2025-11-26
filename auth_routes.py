from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required
from user_auth import User
from werkzeug.security import check_password_hash
from urllib.parse import urlparse

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False

        user = User.get_by_username(username)

        if not user or not check_password_hash(user.password_hash, password):
            flash('Please check your login details and try again.')
            return redirect(url_for('auth.login'))

        login_user(user, remember=remember)
        
        next_page = request.form.get('next')
        # Security: Only redirect to local paths
        if next_page and urlparse(next_page).netloc == '':
            return redirect(next_page)
        
        return redirect(url_for('dashboard.dashboard'))

    return render_template('login.html')

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')

        # Check if user already exists
        if User.get_by_username(username):
            flash('Username already exists.')
            return redirect(url_for('auth.register'))

        # Create new user
        user = User.create(username, email, password)
        if user:
            login_user(user)
            return redirect(url_for('dashboard.dashboard'))
        else:
            flash('An error occurred during registration.')
            return redirect(url_for('auth.register'))

    return render_template('register.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.index'))
