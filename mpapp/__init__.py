"""Selective Systems — Material Planning (Phase 1) application factory."""
import os
import secrets

from flask import Flask, redirect, url_for

from . import db as dbmod
from .filters import register_filters


def _load_secret_key(instance_path):
    """Persist a generated secret key so sessions survive restarts."""
    path = os.path.join(instance_path, 'secret_key')
    if os.path.exists(path):
        with open(path) as f:
            key = f.read().strip()
        if key:
            return key
    key = secrets.token_hex(32)
    with open(path, 'w') as f:
        f.write(key)
    os.chmod(path, 0o600)
    return key


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.from_mapping(
        DATABASE=os.path.join(app.instance_path, 'material_planning.sqlite3'),
        BACKUP_DIR=os.path.join(app.instance_path, 'backups'),
        SESSION_TIMEOUT_MINUTES=int(os.environ.get('MP_SESSION_TIMEOUT_MINUTES', '120')),
        ADMIN_EMAIL=os.environ.get('MP_ADMIN_EMAIL', 'tech3@imperiorailing.com'),
        ADMIN_PASSWORD=os.environ.get('MP_ADMIN_PASSWORD', 'changeme123'),
        COMPANY_NAME='Selective Systems',
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    app.secret_key = app.config.get('SECRET_KEY') or _load_secret_key(app.instance_path)

    dbmod.init_app(app)
    with app.app_context():
        dbmod.init_db()

    from . import (
        auth, catalog, glass, orders, projects, reports, requirements, stock, tools, windows,
    )
    app.register_blueprint(auth.bp)
    app.register_blueprint(projects.bp)
    app.register_blueprint(windows.bp)
    app.register_blueprint(catalog.bp)
    app.register_blueprint(requirements.bp)
    app.register_blueprint(tools.bp)
    app.register_blueprint(glass.bp)
    app.register_blueprint(orders.bp)
    app.register_blueprint(stock.bp)
    app.register_blueprint(reports.bp)

    register_filters(app)

    from .backup import register_cli, start_backup_thread
    register_cli(app)
    if not app.config.get('TESTING'):
        start_backup_thread(app)

    @app.route('/')
    def index():
        return redirect(url_for('projects.list_projects'))

    @app.context_processor
    def inject_company():
        return {'company_name': app.config['COMPANY_NAME']}

    return app
