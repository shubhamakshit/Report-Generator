from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from utils import get_db_connection

settings_bp = Blueprint('settings', __name__)

@settings_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        # --- Handle NeetPrep Toggle ---
        neetprep_enabled = 1 if request.form.get('neetprep_enabled') else 0
        
        # --- Handle DPI Setting ---
        try:
            dpi = int(request.form.get('dpi', 100))
            if not (72 <= dpi <= 900):
                flash('Invalid DPI value. Please enter a number between 72 and 900.', 'danger')
                return redirect(url_for('settings.settings'))
        except (ValueError, TypeError):
            flash('Invalid DPI value. Please enter a valid number.', 'danger')
            return redirect(url_for('settings.settings'))

        # --- Update Database ---
        conn = get_db_connection()
        conn.execute('UPDATE users SET neetprep_enabled = ?, dpi = ? WHERE id = ?', (neetprep_enabled, dpi, current_user.id))
        conn.commit()
        conn.close()
        
        # --- Update current_user object for the session ---
        current_user.neetprep_enabled = neetprep_enabled
        current_user.dpi = dpi
        
        flash('Settings saved successfully!', 'success')
        return redirect(url_for('settings.settings'))

    return render_template('settings.html')
