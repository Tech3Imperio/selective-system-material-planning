"""FR-G4: automated daily backup of the SQLite database, plus a manual CLI command.

A daemon thread checks every hour whether today's backup exists and creates it if
not, using the SQLite online-backup API (safe while the app is running). The last
KEEP_DAYS backups are retained. Restore procedure is documented in README.md.
"""
import os
import sqlite3
import threading
import time
from datetime import date

import click

KEEP_DAYS = 14
CHECK_INTERVAL_SECONDS = 3600


def backup_path_for_today(backup_dir):
    return os.path.join(backup_dir, f'material-planning-{date.today().isoformat()}.sqlite3')


def perform_backup(db_path, backup_dir):
    os.makedirs(backup_dir, exist_ok=True)
    dest_path = backup_path_for_today(backup_dir)
    src = sqlite3.connect(db_path)
    dest = sqlite3.connect(dest_path)
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()
    prune_old_backups(backup_dir)
    return dest_path


def prune_old_backups(backup_dir):
    backups = sorted(
        f for f in os.listdir(backup_dir)
        if f.startswith('material-planning-') and f.endswith('.sqlite3')
    )
    for old in backups[:-KEEP_DAYS]:
        os.remove(os.path.join(backup_dir, old))


def start_backup_thread(app):
    db_path = app.config['DATABASE']
    backup_dir = app.config['BACKUP_DIR']

    def loop():
        while True:
            try:
                if not os.path.exists(backup_path_for_today(backup_dir)) and os.path.exists(db_path):
                    perform_backup(db_path, backup_dir)
            except Exception as exc:  # keep the thread alive; a failed run retries next hour
                app.logger.warning('Daily backup failed: %s', exc)
            time.sleep(CHECK_INTERVAL_SECONDS)

    threading.Thread(target=loop, daemon=True, name='daily-backup').start()


def register_cli(app):
    @app.cli.command('backup')
    def backup_command():
        """Back up the database now."""
        path = perform_backup(app.config['DATABASE'], app.config['BACKUP_DIR'])
        click.echo(f'Backup written to {path}')
