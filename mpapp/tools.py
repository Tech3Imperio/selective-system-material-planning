"""Tool requirement lines (Site PPE and other Tools).

Tools reuse the same requirement_lines table, approval/lock behaviour and PO/stock
machinery as Materials — they are simply the catalog items with item_type='Tool'
(Safety Shoes, Helmets, Jackets, plus any user-added tool). Kept in a separate
blueprint/tab so tool planning is handled independently of consumable materials.
All quantities are typed in manually (no formula calculation applies to tools).
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .db import get_db
from .services import get_project_or_404, parse_positive_number

bp = Blueprint('tools', __name__, url_prefix='/projects/<int:project_id>/tools')


def _active_tools(db):
    return db.execute(
        "SELECT * FROM materials WHERE active = 1 AND item_type = 'Tool'"
        ' ORDER BY name COLLATE NOCASE'
    ).fetchall()


def _get_line_or_404(db, project_id, line_id):
    line = db.execute(
        'SELECT * FROM requirement_lines WHERE id = ? AND project_id = ?', (line_id, project_id)
    ).fetchone()
    if line is None:
        abort(404)
    return line


@bp.route('/')
def list_tools(project_id):
    db = get_db()
    project = get_project_or_404(project_id)
    lines = db.execute(
        """
        SELECT rl.*, m.name AS material_name, m.supply_source,
          COALESCE((SELECT SUM(pl.qty) FROM po_lines pl
                    JOIN purchase_orders po ON po.id = pl.po_id
                    WHERE pl.requirement_line_id = rl.id AND po.status != 'Cancelled'), 0)
            AS on_po_qty
        FROM requirement_lines rl JOIN materials m ON m.id = rl.material_id
        WHERE rl.project_id = ? AND m.item_type = 'Tool'
        ORDER BY m.name COLLATE NOCASE, rl.id
        """,
        (project_id,),
    ).fetchall()
    return render_template(
        'tools/list.html', project=project, lines=lines, tools=_active_tools(db),
    )


@bp.route('/add', methods=('POST',))
def add(project_id):
    db = get_db()
    get_project_or_404(project_id)
    material_id = request.form.get('material_id', '').strip()
    qty = parse_positive_number(request.form.get('required_qty'))
    basis_note = request.form.get('basis_note', '').strip()
    tool = None
    if material_id.isdigit():
        tool = db.execute(
            "SELECT * FROM materials WHERE id = ? AND active = 1 AND item_type = 'Tool'",
            (int(material_id),),
        ).fetchone()
    if tool is None:
        flash('Pick a tool from the catalog.', 'error')
    elif qty is None:
        flash('Required quantity must be a positive number.', 'error')
    else:
        db.execute(
            'INSERT INTO requirement_lines (project_id, material_id, required_qty, uom, basis_note)'
            ' VALUES (?, ?, ?, ?, ?)',
            (project_id, tool['id'], qty, tool['uom'], basis_note),
        )
        db.commit()
        flash('Tool line added (Draft).', 'success')
    return redirect(url_for('tools.list_tools', project_id=project_id))


@bp.route('/<int:line_id>/edit', methods=('GET', 'POST'))
def edit(project_id, line_id):
    db = get_db()
    project = get_project_or_404(project_id)
    line = _get_line_or_404(db, project_id, line_id)
    if line['status'] == 'Approved':
        flash('This line is Approved and locked. Un-approve it first to edit.', 'error')
        return redirect(url_for('tools.list_tools', project_id=project_id))
    if request.method == 'POST':
        material_id = request.form.get('material_id', '').strip()
        qty = parse_positive_number(request.form.get('required_qty'))
        basis_note = request.form.get('basis_note', '').strip()
        tool = None
        if material_id.isdigit():
            tool = db.execute(
                "SELECT * FROM materials WHERE id = ? AND item_type = 'Tool'", (int(material_id),)
            ).fetchone()
        if tool is None:
            flash('Pick a tool from the catalog.', 'error')
        elif qty is None:
            flash('Required quantity must be a positive number.', 'error')
        else:
            db.execute(
                'UPDATE requirement_lines SET material_id = ?, required_qty = ?, uom = ?,'
                ' basis_note = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (tool['id'], qty, tool['uom'], basis_note, line_id),
            )
            db.commit()
            flash('Tool line updated.', 'success')
            return redirect(url_for('tools.list_tools', project_id=project_id))
    return render_template('tools/form.html', project=project, line=line, tools=_active_tools(db))


@bp.route('/<int:line_id>/delete', methods=('POST',))
def delete(project_id, line_id):
    db = get_db()
    line = _get_line_or_404(db, project_id, line_id)
    if line['status'] == 'Approved':
        flash('Approved lines are locked. Un-approve first.', 'error')
        return redirect(url_for('tools.list_tools', project_id=project_id))
    linked = db.execute(
        'SELECT COUNT(*) FROM po_lines WHERE requirement_line_id = ?', (line_id,)
    ).fetchone()[0]
    if linked:
        flash('This line is referenced by purchase order line(s) and cannot be deleted.', 'error')
    else:
        db.execute('DELETE FROM requirement_lines WHERE id = ?', (line_id,))
        db.commit()
        flash('Tool line deleted.', 'success')
    return redirect(url_for('tools.list_tools', project_id=project_id))


@bp.route('/<int:line_id>/purchase', methods=('POST',))
def update_purchase(project_id, line_id):
    """Purchase Qty + Notes for a tool line — same semantics as Materials:
    independent of the required qty, editable on Approved lines too."""
    db = get_db()
    get_project_or_404(project_id)
    _get_line_or_404(db, project_id, line_id)
    raw_qty = request.form.get('purchase_qty', '').strip()
    notes = request.form.get('purchase_notes', '').strip()
    if raw_qty:
        qty = parse_positive_number(raw_qty)
        if qty is None:
            flash('Purchase quantity must be a positive number (or blank to order the required qty).',
                  'error')
            return redirect(url_for('tools.list_tools', project_id=project_id))
    else:
        qty = None
    db.execute(
        'UPDATE requirement_lines SET purchase_qty = ?, purchase_notes = ?,'
        ' updated_at = CURRENT_TIMESTAMP WHERE id = ?',
        (qty, notes or None, line_id),
    )
    db.commit()
    flash('Purchase quantity saved.', 'success')
    return redirect(url_for('tools.list_tools', project_id=project_id))


@bp.route('/<int:line_id>/approve', methods=('POST',))
def approve(project_id, line_id):
    db = get_db()
    line = _get_line_or_404(db, project_id, line_id)
    if line['status'] != 'Approved':
        db.execute(
            "UPDATE requirement_lines SET status = 'Approved', updated_at = CURRENT_TIMESTAMP"
            ' WHERE id = ?',
            (line_id,),
        )
        db.commit()
        flash('Tool line approved and locked.', 'success')
    return redirect(url_for('tools.list_tools', project_id=project_id))


@bp.route('/<int:line_id>/unapprove', methods=('POST',))
def unapprove(project_id, line_id):
    db = get_db()
    line = _get_line_or_404(db, project_id, line_id)
    if line['status'] == 'Approved':
        db.execute(
            "UPDATE requirement_lines SET status = 'Draft', updated_at = CURRENT_TIMESTAMP"
            ' WHERE id = ?',
            (line_id,),
        )
        db.commit()
        flash('Tool line un-approved — it can be edited again.', 'success')
    return redirect(url_for('tools.list_tools', project_id=project_id))
