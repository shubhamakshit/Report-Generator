from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from utils import get_db_connection
import os

settings_bp = Blueprint('settings', __name__)

@settings_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        # --- Handle Client Secret Upload ---
        if 'client_secret' in request.files:
            file = request.files['client_secret']
            if file and file.filename:
                if file.filename.endswith('.json'):
                    try:
                        save_path = os.path.join(current_app.root_path, 'client_secret.json')
                        file.save(save_path)
                        flash('Client Secret uploaded successfully!', 'success')
                    except Exception as e:
                        flash(f'Error saving file: {e}', 'danger')
                else:
                    flash('Invalid file type. Please upload a JSON file.', 'danger')

        # --- Handle NeetPrep Toggle ---
        neetprep_enabled = 1 if request.form.get('neetprep_enabled') else 0
        
        # --- Handle V2 Default Toggle ---
        v2_default = 1 if request.form.get('v2_default') else 0
        
        # --- Handle Magnifier Toggle ---
        magnifier_enabled = 1 if request.form.get('magnifier_enabled') else 0
        
        # --- Handle DPI Setting ---
        dpi_input = request.form.get('dpi')
        if not dpi_input:
            dpi = 300
        else:
            try:
                dpi = int(dpi_input)
                if not (72 <= dpi <= 900):
                    flash('Invalid DPI value. Please enter a number between 72 and 900.', 'danger')
                    return redirect(url_for('settings.settings'))
            except (ValueError, TypeError):
                flash('Invalid DPI value. Please enter a valid number.', 'danger')
                return redirect(url_for('settings.settings'))

        # --- Handle Color RM DPI Setting ---
        color_rm_dpi_input = request.form.get('color_rm_dpi')
        if not color_rm_dpi_input:
            color_rm_dpi = 200
        else:
            try:
                color_rm_dpi = int(color_rm_dpi_input)
                if not (72 <= color_rm_dpi <= 600):
                    flash('Invalid Color Removal DPI value. Please enter a number between 72 and 600.', 'danger')
                    return redirect(url_for('settings.settings'))
            except (ValueError, TypeError):
                flash('Invalid Color Removal DPI value. Please enter a valid number.', 'danger')
                return redirect(url_for('settings.settings'))

        # --- Update Database ---
        conn = get_db_connection()
        conn.execute('UPDATE users SET neetprep_enabled = ?, v2_default = ?, magnifier_enabled = ?, dpi = ?, color_rm_dpi = ? WHERE id = ?', (neetprep_enabled, v2_default, magnifier_enabled, dpi, color_rm_dpi, current_user.id))
        conn.commit()
        conn.close()
        
        # --- Update current_user object for the session ---
        current_user.neetprep_enabled = neetprep_enabled
        current_user.v2_default = v2_default
        current_user.magnifier_enabled = magnifier_enabled
        current_user.dpi = dpi
        current_user.color_rm_dpi = color_rm_dpi
        
        flash('Settings saved successfully!', 'success')
        return redirect(url_for('settings.settings'))

    client_secret_exists = os.path.exists(os.path.join(current_app.root_path, 'client_secret.json'))
    drive_redirect_uri = url_for('drive.oauth2callback', _external=True)
    return render_template('settings.html', client_secret_exists=client_secret_exists, drive_redirect_uri=drive_redirect_uri)
