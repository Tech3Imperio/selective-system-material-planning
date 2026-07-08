"""Per-project stock ledger (W8/W9, FR-S2..S5): direct receipts, issues, adjustments,
filterable ledger, and CSV export (FR-T4). On-hand is always derived from the ledger.
"""
import csv
import io
from datetime import date

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from .db import get_db
from .services import EPS, get_project_or_404, parse_number, parse_positive_number, stock_on_hand

bp = Blueprint('stock', __name__, url_prefix='/projects/<int:project_id>/stock')


def _active_materials(db):
    return db.execute(
        'SELECT * FROM materials WHERE active = 1 ORDER BY name COLLATE NOCASE'
    ).fetchall()


def _ledger_query(project_id):
    material_id = request.args.get('material_id', '')
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    sql = """
        SELECT st.*, m.name AS material_name, m.uom,
               pl.po_id AS po_id, po.po_number AS po_number
        FROM stock_transactions st
        JOIN materials m ON m.id = st.material_id
        LEFT JOIN po_lines pl ON pl.id = st.po_line_id
        LEFT JOIN purchase_orders po ON po.id = pl.po_id
        WHERE st.project_id = ?
    """
    params = [project_id]
    if material_id.isdigit():
        sql += ' AND st.material_id = ?'
        params.append(int(material_id))
    if date_from:
        sql += ' AND st.date >= ?'
        params.append(date_from)
    if date_to:
        sql += ' AND st.date <= ?'
        params.append(date_to)
    sql += ' ORDER BY st.date DESC, st.id DESC'
    return sql, params, {'material_id': material_id, 'date_from': date_from, 'date_to': date_to}


@bp.route('/')
def ledger(project_id):
    db = get_db()
    project = get_project_or_404(project_id)
    sql, params, filters = _ledger_query(project_id)
    transactions = db.execute(sql, params).fetchall()
    balances = db.execute(
        """
        SELECT m.id, m.name, m.uom, m.supply_source, SUM(st.qty) AS on_hand
        FROM stock_transactions st JOIN materials m ON m.id = st.material_id
        WHERE st.project_id = ?
        GROUP BY m.id ORDER BY m.name COLLATE NOCASE
        """,
        (project_id,),
    ).fetchall()
    return render_template(
        'stock/ledger.html',
        project=project, transactions=transactions, balances=balances,
        materials=_active_materials(db), filters=filters, today=date.today().isoformat(),
    )


@bp.route('/receipt', methods=('POST',))
def direct_receipt(project_id):
    """FR-S2: direct receipt without a PO (e.g., Tostem gaskets arriving with the windows)."""
    db = get_db()
    get_project_or_404(project_id)
    material_raw = request.form.get('material_id', '').strip()
    qty = parse_positive_number(request.form.get('qty'))
    txn_date = request.form.get('date', '').strip() or date.today().isoformat()
    note = request.form.get('note', '').strip()
    material = None
    if material_raw.isdigit():
        material = db.execute(
            'SELECT * FROM materials WHERE id = ?', (int(material_raw),)
        ).fetchone()
    if material is None:
        flash('Pick a material.', 'error')
    elif qty is None:
        flash('Received quantity must be a positive number.', 'error')
    else:
        db.execute(
            'INSERT INTO stock_transactions (project_id, material_id, type, qty, date, reason_notes)'
            " VALUES (?, ?, 'Receipt', ?, ?, ?)",
            (project_id, material['id'], qty, txn_date, note),
        )
        db.commit()
        flash('Direct receipt recorded.', 'success')
    return redirect(url_for('stock.ledger', project_id=project_id))


@bp.route('/issue', methods=('POST',))
def issue(project_id):
    db = get_db()
    get_project_or_404(project_id)
    material_raw = request.form.get('material_id', '').strip()
    qty = parse_positive_number(request.form.get('qty'))
    txn_date = request.form.get('date', '').strip() or date.today().isoformat()
    note = request.form.get('note', '').strip()
    material = None
    if material_raw.isdigit():
        material = db.execute(
            'SELECT * FROM materials WHERE id = ?', (int(material_raw),)
        ).fetchone()
    if material is None:
        flash('Pick a material.', 'error')
    elif qty is None:
        flash('Issue quantity must be a positive number.', 'error')
    else:
        on_hand = stock_on_hand(db, project_id, material['id'])
        if qty > on_hand + EPS:
            # FR-S5: block issues that would take on-hand below zero.
            flash(
                f"Cannot issue {qty:g} {material['uom']} of {material['name']}: "
                f"only {on_hand:g} {material['uom']} on hand. Issues beyond on-hand are blocked.",
                'error',
            )
        else:
            db.execute(
                'INSERT INTO stock_transactions (project_id, material_id, type, qty, date,'
                " reason_notes) VALUES (?, ?, 'Issue', ?, ?, ?)",
                (project_id, material['id'], -qty, txn_date, note),
            )
            db.commit()
            flash('Issue recorded.', 'success')
    return redirect(url_for('stock.ledger', project_id=project_id))


@bp.route('/adjust', methods=('POST',))
def adjust(project_id):
    db = get_db()
    get_project_or_404(project_id)
    material_raw = request.form.get('material_id', '').strip()
    qty = parse_number(request.form.get('qty'))
    txn_date = request.form.get('date', '').strip() or date.today().isoformat()
    reason = request.form.get('reason', '').strip()
    material = None
    if material_raw.isdigit():
        material = db.execute(
            'SELECT * FROM materials WHERE id = ?', (int(material_raw),)
        ).fetchone()
    if material is None:
        flash('Pick a material.', 'error')
    elif qty is None or qty == 0:
        flash('Adjustment quantity must be a non-zero number (use - for reductions).', 'error')
    elif not reason:
        # FR-S3: adjustments require a reason.
        flash('A reason is mandatory for stock adjustments.', 'error')
    else:
        on_hand = stock_on_hand(db, project_id, material['id'])
        if on_hand + qty < -EPS:
            flash(
                f"This adjustment would take {material['name']} on-hand below zero "
                f"(current on-hand {on_hand:g}).",
                'error',
            )
        else:
            db.execute(
                'INSERT INTO stock_transactions (project_id, material_id, type, qty, date,'
                " reason_notes) VALUES (?, ?, 'Adjustment', ?, ?, ?)",
                (project_id, material['id'], qty, txn_date, reason),
            )
            db.commit()
            flash('Adjustment recorded.', 'success')
    return redirect(url_for('stock.ledger', project_id=project_id))


@bp.route('/export.csv')
def export_csv(project_id):
    """FR-T4: stock ledger export, honouring the active filters."""
    db = get_db()
    project = get_project_or_404(project_id)
    sql, params, _ = _ledger_query(project_id)
    transactions = db.execute(sql, params).fetchall()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Date', 'Material', 'UoM', 'Type', 'Qty', 'PO Number', 'Notes', 'Recorded At'])
    for t in transactions:
        writer.writerow([
            t['date'], t['material_name'], t['uom'], t['type'], t['qty'],
            t['po_number'] or '', t['reason_notes'] or '', t['created_at'],
        ])
    filename = f"stock-ledger-project-{project['id']}.csv"
    return Response(
        out.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )
