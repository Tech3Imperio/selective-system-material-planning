"""Printable reports (§10): R1 Material Requirement Report, R2 Coverage/Shortfall,
R4 Tostem Requisition. (R3 printable PO lives in orders.py, R5 export in stock.py.)
"""
from datetime import date

from flask import Blueprint, render_template

from .db import get_db
from .services import coverage_rows, get_project_or_404, window_summary

bp = Blueprint('reports', __name__, url_prefix='/projects/<int:project_id>/reports')


@bp.route('/')
def index(project_id):
    project = get_project_or_404(project_id)
    return render_template('reports/index.html', project=project)


@bp.route('/requirements')
def requirement_report(project_id):
    """R1 / FR-T1: all requirement lines with status and basis notes."""
    db = get_db()
    project = get_project_or_404(project_id)
    lines = db.execute(
        """
        SELECT rl.*, m.name AS material_name, m.category, m.supply_source
        FROM requirement_lines rl JOIN materials m ON m.id = rl.material_id
        WHERE rl.project_id = ?
        ORDER BY m.name COLLATE NOCASE, rl.id
        """,
        (project_id,),
    ).fetchall()
    return render_template(
        'reports/requirement_report.html',
        project=project, lines=lines, today=date.today().isoformat(),
    )


@bp.route('/coverage')
def coverage_report(project_id):
    """R2 / FR-T2: Required / Ordered / Received / Issued / On hand / To-order gap."""
    db = get_db()
    project = get_project_or_404(project_id)
    return render_template(
        'reports/coverage.html',
        project=project, coverage=coverage_rows(db, project_id), today=date.today().isoformat(),
    )


@bp.route('/tostem')
def tostem_requisition(project_id):
    """R4 / FR-T3 / W7: Tostem-supplied lines + window schedule summary."""
    db = get_db()
    project = get_project_or_404(project_id)
    lines = db.execute(
        """
        SELECT rl.*, m.name AS material_name
        FROM requirement_lines rl JOIN materials m ON m.id = rl.material_id
        WHERE rl.project_id = ? AND m.supply_source = 'Tostem-supplied'
        ORDER BY m.name COLLATE NOCASE, rl.id
        """,
        (project_id,),
    ).fetchall()
    totals = db.execute(
        """
        SELECT m.name AS material_name, rl.uom, SUM(rl.required_qty) AS total_qty
        FROM requirement_lines rl JOIN materials m ON m.id = rl.material_id
        WHERE rl.project_id = ? AND m.supply_source = 'Tostem-supplied'
        GROUP BY m.id ORDER BY m.name COLLATE NOCASE
        """,
        (project_id,),
    ).fetchall()
    window_lines = db.execute(
        """
        SELECT w.*, c.code AS config_code
        FROM window_lines w JOIN window_config_types c ON c.id = w.config_type_id
        WHERE w.project_id = ? ORDER BY w.opening_ref COLLATE NOCASE, w.id
        """,
        (project_id,),
    ).fetchall()
    return render_template(
        'reports/tostem.html',
        project=project, lines=lines, totals=totals,
        summary=window_summary(db, project_id), window_lines=window_lines,
        today=date.today().isoformat(),
    )
