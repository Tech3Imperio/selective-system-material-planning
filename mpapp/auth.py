"""Single-user login with session timeout (FR-G1)."""
import time

from flask import (
    Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

bp = Blueprint('auth', __name__)

EXEMPT_ENDPOINTS = {'auth.login', 'static'}


@bp.before_app_request
def enforce_login_and_timeout():
    if request.endpoint is None or request.endpoint in EXEMPT_ENDPOINTS:
        return None
    user_id = session.get('user_id')
    if user_id is None:
        return redirect(url_for('auth.login', next=request.path))
    timeout_seconds = current_app.config['SESSION_TIMEOUT_MINUTES'] * 60
    last_active = session.get('last_active', 0)
    now = time.time()
    if now - last_active > timeout_seconds:
        session.clear()
        flash('Your session expired. Please log in again.', 'error')
        return redirect(url_for('auth.login', next=request.path))
    session['last_active'] = now
    g.user = get_db().execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if g.user is None:
        session.clear()
        return redirect(url_for('auth.login'))
    return None


@bp.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        user = get_db().execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        if user is None or not check_password_hash(user['password_hash'], password):
            flash('Invalid email or password.', 'error')
        else:
            session.clear()
            session['user_id'] = user['id']
            session['last_active'] = time.time()
            next_url = request.args.get('next') or url_for('projects.list_projects')
            if not next_url.startswith('/'):
                next_url = url_for('projects.list_projects')
            return redirect(next_url)
    return render_template('auth/login.html')


@bp.route('/logout', methods=('POST',))
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@bp.route('/settings', methods=('GET', 'POST'))
def settings():
    from .services import get_app_settings

    db = get_db()
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        if not check_password_hash(g.user['password_hash'], current):
            flash('Current password is incorrect.', 'error')
        elif len(new) < 8:
            flash('New password must be at least 8 characters.', 'error')
        elif new != confirm:
            flash('New password and confirmation do not match.', 'error')
        else:
            db.execute(
                'UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (generate_password_hash(new), g.user['id']),
            )
            db.commit()
            flash('Password updated.', 'success')
            return redirect(url_for('auth.settings'))
    return render_template('auth/settings.html', settings=get_app_settings(db))


@bp.route('/settings/company', methods=('POST',))
def settings_company():
    """Company profile / PO branding: shown on the printed Purchase Order header."""
    from .services import SETTING_DEFAULTS, save_app_settings

    db = get_db()
    save_app_settings(db, {key: request.form.get(key, '') for key in SETTING_DEFAULTS})
    db.commit()
    flash('Company profile saved. It appears on printed purchase orders.', 'success')
    return redirect(url_for('auth.settings'))
