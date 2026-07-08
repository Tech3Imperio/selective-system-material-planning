"""Projects: CRUD, list with filters (FR-P1/P2), archive (FR-G3), coverage dashboard (W10)."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from .db import get_db
from .services import coverage_rows, get_project_or_404, window_summary

bp = Blueprint('projects', __name__, url_prefix='/projects')

STATUSES = ('Draft', 'Active', 'Closed')


@bp.route('/')
def list_projects():
    db = get_db()
    status = request.args.get('status', '')
    q = request.args.get('q', '').strip()
    show_archived = request.args.get('show_archived') == '1'
    sql = 'SELECT * FROM projects WHERE 1=1'
    params = []
    if not show_archived:
        sql += ' AND archived = 0'
    if status in STATUSES:
        sql += ' AND status = ?'
        params.append(status)
    if q:
        sql += ' AND (name LIKE ? OR client_name LIKE ?)'
        params.extend([f'%{q}%', f'%{q}%'])
    sql += ' ORDER BY created_at DESC'
    projects = db.execute(sql, params).fetchall()
    return render_template(
        'projects/list.html',
        projects=projects, statuses=STATUSES,
        status=status, q=q, show_archived=show_archived,
    )


def _read_form():
    crew_size = request.form.get('crew_size', '').strip()
    return {
        'name': request.form.get('name', '').strip(),
        'client_name': request.form.get('client_name', '').strip(),
        'site_address': request.form.get('site_address', '').strip(),
        'status': request.form.get('status', 'Draft'),
        'start_date': request.form.get('start_date', '').strip() or None,
        'expected_end_date': request.form.get('expected_end_date', '').strip() or None,
        'crew_size': int(crew_size) if crew_size.isdigit() else None,
        'notes': request.form.get('notes', '').strip(),
    }


def _validate(f):
    errors = []
    if not f['name']:
        errors.append('Project name is required.')
    if not f['client_name']:
        errors.append('Client name is required.')
    if f['status'] not in STATUSES:
        errors.append('Invalid status.')
    return errors


@bp.route('/new', methods=('GET', 'POST'))
def new():
    if request.method == 'POST':
        f = _read_form()
        errors = _validate(f)
        if errors:
            for e in errors:
                flash(e, 'error')
        else:
            db = get_db()
            cur = db.execute(
                'INSERT INTO projects (name, client_name, site_address, status, start_date,'
                ' expected_end_date, crew_size, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (f['name'], f['client_name'], f['site_address'], f['status'],
                 f['start_date'], f['expected_end_date'], f['crew_size'], f['notes']),
            )
            db.commit()
            flash('Project created.', 'success')
            return redirect(url_for('projects.overview', project_id=cur.lastrowid))
    return render_template('projects/form.html', project=None, statuses=STATUSES)


@bp.route('/<int:project_id>')
def overview(project_id):
    db = get_db()
    project = get_project_or_404(project_id)
    coverage = coverage_rows(db, project_id)
    summary = window_summary(db, project_id)
    counts = {
        'requirements': db.execute(
            'SELECT COUNT(*) FROM requirement_lines WHERE project_id = ?', (project_id,)
        ).fetchone()[0],
        'approved': db.execute(
            "SELECT COUNT(*) FROM requirement_lines WHERE project_id = ? AND status = 'Approved'",
            (project_id,),
        ).fetchone()[0],
        'pos': db.execute(
            "SELECT COUNT(*) FROM purchase_orders WHERE project_id = ? AND status != 'Cancelled'",
            (project_id,),
        ).fetchone()[0],
    }
    return render_template(
        'projects/overview.html',
        project=project, coverage=coverage, summary=summary, counts=counts,
    )


@bp.route('/<int:project_id>/edit', methods=('GET', 'POST'))
def edit(project_id):
    project = get_project_or_404(project_id)
    if request.method == 'POST':
        f = _read_form()
        errors = _validate(f)
        if errors:
            for e in errors:
                flash(e, 'error')
        else:
            db = get_db()
            db.execute(
                'UPDATE projects SET name = ?, client_name = ?, site_address = ?, status = ?,'
                ' start_date = ?, expected_end_date = ?, crew_size = ?, notes = ?,'
                ' updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (f['name'], f['client_name'], f['site_address'], f['status'],
                 f['start_date'], f['expected_end_date'], f['crew_size'], f['notes'], project_id),
            )
            db.commit()
            flash('Project updated.', 'success')
            return redirect(url_for('projects.overview', project_id=project_id))
    return render_template('projects/form.html', project=project, statuses=STATUSES)


@bp.route('/<int:project_id>/archive', methods=('POST',))
def archive(project_id):
    get_project_or_404(project_id)
    db = get_db()
    db.execute(
        'UPDATE projects SET archived = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
        (project_id,),
    )
    db.commit()
    flash('Project archived. It is hidden from the project list but nothing was deleted.', 'success')
    return redirect(url_for('projects.list_projects'))


@bp.route('/<int:project_id>/unarchive', methods=('POST',))
def unarchive(project_id):
    get_project_or_404(project_id)
    db = get_db()
    db.execute(
        'UPDATE projects SET archived = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
        (project_id,),
    )
    db.commit()
    flash('Project restored.', 'success')
    return redirect(url_for('projects.overview', project_id=project_id))
