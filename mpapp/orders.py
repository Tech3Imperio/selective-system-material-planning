"""Purchase orders: creation from approved requirement lines grouped by vendor (W6, FR-O1),
the Tostem guard-rail (FR-O2), auto-numbering (FR-O3), lifecycle (FR-O4), receiving with
partial and confirmed over-receipts (W8, FR-S1), and the printable PO (FR-O5/O6).
"""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .db import get_db
from .services import (
    EPS, get_project_or_404, next_po_number, parse_number, parse_positive_number,
    po_has_receipts, po_line_received_qty, refresh_po_status, tostem_po_error,
)

bp = Blueprint('orders', __name__)

EDITABLE_STATUSES = ('Draft',)
RECEIVABLE_STATUSES = ('Issued', 'Partially Received', 'Received')


def _get_po_or_404(db, po_id):
    po = db.execute(
        """
        SELECT po.*, v.name AS vendor_name, p.name AS project_name, p.id AS pid
        FROM purchase_orders po
        JOIN vendors v ON v.id = po.vendor_id
        JOIN projects p ON p.id = po.project_id
        WHERE po.id = ?
        """,
        (po_id,),
    ).fetchone()
    if po is None:
        abort(404)
    return po


def _po_lines(db, po_id):
    lines = db.execute(
        """
        SELECT pl.*, m.name AS material_name, m.supply_source,
               rl.basis_note AS requirement_basis
        FROM po_lines pl
        JOIN materials m ON m.id = pl.material_id
        LEFT JOIN requirement_lines rl ON rl.id = pl.requirement_line_id
        WHERE pl.po_id = ? ORDER BY pl.id
        """,
        (po_id,),
    ).fetchall()
    out = []
    for line in lines:
        d = dict(line)
        d['received_qty'] = po_line_received_qty(db, line['id'])
        d['amount'] = (line['rate'] * line['qty']) if line['rate'] is not None else None
        out.append(d)
    return out


def _active_vendors(db):
    return db.execute('SELECT * FROM vendors WHERE active = 1 ORDER BY name COLLATE NOCASE').fetchall()


def _load_selected_requirements(db, project_id, line_ids):
    """Fetch and validate selected requirement lines for PO creation.

    Enforces: lines belong to the project, are Approved (FR-O1), and are not
    Tostem-supplied (FR-O2). Returns (lines, error_message).
    """
    ids = []
    for raw in line_ids:
        if str(raw).isdigit():
            ids.append(int(raw))
    if not ids:
        return None, 'Select at least one approved requirement line.'
    placeholders = ','.join('?' * len(ids))
    lines = db.execute(
        f"""
        SELECT rl.*, m.name AS material_name, m.supply_source, m.default_vendor_id
        FROM requirement_lines rl JOIN materials m ON m.id = rl.material_id
        WHERE rl.id IN ({placeholders}) AND rl.project_id = ?
        """,
        (*ids, project_id),
    ).fetchall()
    if len(lines) != len(set(ids)):
        return None, 'Some selected lines were not found on this project.'
    tostem = [l['material_name'] for l in lines if l['supply_source'] == 'Tostem-supplied']
    if tostem:
        return None, tostem_po_error(tostem)
    not_approved = [l['material_name'] for l in lines if l['status'] != 'Approved']
    if not_approved:
        return None, (
            'Only Approved requirement lines can go on a purchase order. '
            'Draft lines selected: ' + ', '.join(sorted(set(not_approved))) + '.'
        )
    return lines, None


@bp.route('/projects/<int:project_id>/purchase-orders/from-requirements', methods=('POST',))
def from_requirements(project_id):
    """Step 1 of W6: group the selected lines by default vendor for review."""
    db = get_db()
    get_project_or_404(project_id)
    lines, error = _load_selected_requirements(db, project_id, request.form.getlist('line_ids'))
    if error:
        flash(error, 'error')
        return redirect(url_for('requirements.list_requirements', project_id=project_id))
    project = get_project_or_404(project_id)
    groups = {}
    for line in lines:
        groups.setdefault(line['default_vendor_id'], []).append(line)
    group_list = [
        {'key': f'g{i}', 'default_vendor_id': vendor_id, 'lines': grp}
        for i, (vendor_id, grp) in enumerate(groups.items())
    ]
    return render_template(
        'orders/create_from_reqs.html',
        project=project, groups=group_list, vendors=_active_vendors(db),
    )


@bp.route('/projects/<int:project_id>/purchase-orders/create', methods=('POST',))
def create(project_id):
    """Step 2 of W6: create one draft PO per chosen vendor (same vendor groups merge)."""
    db = get_db()
    get_project_or_404(project_id)
    group_keys = request.form.getlist('group_keys')
    by_vendor = {}
    all_line_ids = []
    for key in group_keys:
        vendor_raw = request.form.get(f'vendor_{key}', '').strip()
        line_ids = [x for x in request.form.get(f'lines_{key}', '').split(',') if x.strip()]
        if not vendor_raw.isdigit():
            flash('Choose a vendor for every group before creating POs.', 'error')
            return redirect(url_for('requirements.list_requirements', project_id=project_id))
        vendor = db.execute(
            'SELECT * FROM vendors WHERE id = ? AND active = 1', (int(vendor_raw),)
        ).fetchone()
        if vendor is None:
            flash('Unknown vendor selected.', 'error')
            return redirect(url_for('requirements.list_requirements', project_id=project_id))
        by_vendor.setdefault(vendor['id'], []).extend(line_ids)
        all_line_ids.extend(line_ids)

    lines, error = _load_selected_requirements(db, project_id, all_line_ids)
    if error:
        flash(error, 'error')
        return redirect(url_for('requirements.list_requirements', project_id=project_id))
    lines_by_id = {l['id']: l for l in lines}

    today = date.today()
    created = []
    for vendor_id, line_ids in by_vendor.items():
        po_number = next_po_number(db, today.year)
        cur = db.execute(
            'INSERT INTO purchase_orders (po_number, project_id, vendor_id, order_date)'
            ' VALUES (?, ?, ?, ?)',
            (po_number, project_id, vendor_id, today.isoformat()),
        )
        po_id = cur.lastrowid
        for raw_id in line_ids:
            rl = lines_by_id[int(raw_id)]
            db.execute(
                'INSERT INTO po_lines (po_id, material_id, qty, uom, requirement_line_id)'
                ' VALUES (?, ?, ?, ?, ?)',
                (po_id, rl['material_id'], rl['required_qty'], rl['uom'], rl['id']),
            )
        created.append((po_id, po_number))
    db.commit()

    flash('Created draft PO(s): ' + ', '.join(number for _, number in created)
          + '. Review quantities, then Issue.', 'success')
    if len(created) == 1:
        return redirect(url_for('orders.detail', po_id=created[0][0]))
    return redirect(url_for('orders.register', project_id=project_id))


# --- Register (FR-T5) --------------------------------------------------------

@bp.route('/purchase-orders')
def register():
    db = get_db()
    status = request.args.get('status', '')
    vendor_id = request.args.get('vendor_id', '')
    project_id = request.args.get('project_id', '')
    sql = """
        SELECT po.*, v.name AS vendor_name, p.name AS project_name,
          (SELECT COUNT(*) FROM po_lines pl WHERE pl.po_id = po.id) AS line_count,
          (SELECT SUM(pl.qty * pl.rate) FROM po_lines pl
            WHERE pl.po_id = po.id AND pl.rate IS NOT NULL) AS total_amount
        FROM purchase_orders po
        JOIN vendors v ON v.id = po.vendor_id
        JOIN projects p ON p.id = po.project_id
        WHERE 1=1
    """
    params = []
    if status:
        sql += ' AND po.status = ?'
        params.append(status)
    if vendor_id.isdigit():
        sql += ' AND po.vendor_id = ?'
        params.append(int(vendor_id))
    if project_id.isdigit():
        sql += ' AND po.project_id = ?'
        params.append(int(project_id))
    sql += ' ORDER BY po.po_number DESC'
    pos = db.execute(sql, params).fetchall()
    vendors = db.execute('SELECT * FROM vendors ORDER BY name COLLATE NOCASE').fetchall()
    projects = db.execute('SELECT id, name FROM projects ORDER BY name COLLATE NOCASE').fetchall()
    return render_template(
        'orders/register.html',
        pos=pos, vendors=vendors, projects=projects,
        status=status, vendor_id=vendor_id, project_id=project_id,
    )


# --- PO detail / draft editing ------------------------------------------------

@bp.route('/purchase-orders/<int:po_id>')
def detail(po_id):
    db = get_db()
    po = _get_po_or_404(db, po_id)
    lines = _po_lines(db, po_id)
    materials = db.execute(
        'SELECT * FROM materials WHERE active = 1 ORDER BY name COLLATE NOCASE'
    ).fetchall()
    has_rates = any(l['rate'] is not None for l in lines)
    total = sum(l['amount'] for l in lines if l['amount'] is not None) if has_rates else None
    return render_template(
        'orders/detail.html',
        po=po, lines=lines, vendors=_active_vendors(db), materials=materials,
        has_rates=has_rates, total=total, has_receipts=po_has_receipts(db, po_id),
    )


def _require_draft(po):
    if po['status'] not in EDITABLE_STATUSES:
        flash('Only Draft POs can be edited.', 'error')
        return False
    return True


@bp.route('/purchase-orders/<int:po_id>/update', methods=('POST',))
def update(po_id):
    db = get_db()
    po = _get_po_or_404(db, po_id)
    if _require_draft(po):
        vendor_raw = request.form.get('vendor_id', '').strip()
        order_date = request.form.get('order_date', '').strip() or po['order_date']
        expected = request.form.get('expected_delivery', '').strip() or None
        terms = request.form.get('terms_notes', '').strip()
        if not vendor_raw.isdigit() or db.execute(
            'SELECT 1 FROM vendors WHERE id = ?', (int(vendor_raw),)
        ).fetchone() is None:
            flash('Choose a valid vendor.', 'error')
        else:
            db.execute(
                'UPDATE purchase_orders SET vendor_id = ?, order_date = ?, expected_delivery = ?,'
                ' terms_notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (int(vendor_raw), order_date, expected, terms, po_id),
            )
            db.commit()
            flash('Purchase order updated.', 'success')
    return redirect(url_for('orders.detail', po_id=po_id))


@bp.route('/purchase-orders/<int:po_id>/lines/add', methods=('POST',))
def add_line(po_id):
    db = get_db()
    po = _get_po_or_404(db, po_id)
    if _require_draft(po):
        material_raw = request.form.get('material_id', '').strip()
        qty = parse_positive_number(request.form.get('qty'))
        rate = parse_number(request.form.get('rate')) if request.form.get('rate', '').strip() else None
        material = None
        if material_raw.isdigit():
            material = db.execute(
                'SELECT * FROM materials WHERE id = ?', (int(material_raw),)
            ).fetchone()
        if material is None:
            flash('Pick a material.', 'error')
        elif material['supply_source'] == 'Tostem-supplied':
            # FR-O2: hard guard-rail, regardless of how the request was formed.
            flash(tostem_po_error([material['name']]), 'error')
        elif qty is None:
            flash('Quantity must be a positive number.', 'error')
        elif rate is not None and rate < 0:
            flash('Rate cannot be negative.', 'error')
        else:
            db.execute(
                'INSERT INTO po_lines (po_id, material_id, qty, uom, rate) VALUES (?, ?, ?, ?, ?)',
                (po_id, material['id'], qty, material['uom'], rate),
            )
            db.commit()
            flash('Line added.', 'success')
    return redirect(url_for('orders.detail', po_id=po_id))


@bp.route('/purchase-orders/<int:po_id>/lines/<int:line_id>/update', methods=('POST',))
def update_line(po_id, line_id):
    db = get_db()
    po = _get_po_or_404(db, po_id)
    line = db.execute(
        'SELECT * FROM po_lines WHERE id = ? AND po_id = ?', (line_id, po_id)
    ).fetchone()
    if line is None:
        abort(404)
    if _require_draft(po):
        qty = parse_positive_number(request.form.get('qty'))
        rate_raw = request.form.get('rate', '').strip()
        rate = parse_number(rate_raw) if rate_raw else None
        if qty is None:
            flash('Quantity must be a positive number.', 'error')
        elif rate_raw and (rate is None or rate < 0):
            flash('Rate must be a non-negative number.', 'error')
        else:
            db.execute(
                'UPDATE po_lines SET qty = ?, rate = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (qty, rate, line_id),
            )
            db.commit()
            flash('Line updated.', 'success')
    return redirect(url_for('orders.detail', po_id=po_id))


@bp.route('/purchase-orders/<int:po_id>/lines/<int:line_id>/delete', methods=('POST',))
def delete_line(po_id, line_id):
    db = get_db()
    po = _get_po_or_404(db, po_id)
    if _require_draft(po):
        db.execute('DELETE FROM po_lines WHERE id = ? AND po_id = ?', (line_id, po_id))
        db.commit()
        flash('Line removed.', 'success')
    return redirect(url_for('orders.detail', po_id=po_id))


# --- Lifecycle (FR-O4) ---------------------------------------------------------

@bp.route('/purchase-orders/<int:po_id>/issue', methods=('POST',))
def issue(po_id):
    db = get_db()
    po = _get_po_or_404(db, po_id)
    line_count = db.execute('SELECT COUNT(*) FROM po_lines WHERE po_id = ?', (po_id,)).fetchone()[0]
    if po['status'] != 'Draft':
        flash('Only Draft POs can be issued.', 'error')
    elif line_count == 0:
        flash('Add at least one line before issuing.', 'error')
    else:
        db.execute(
            "UPDATE purchase_orders SET status = 'Issued', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (po_id,),
        )
        db.commit()
        flash(f"PO {po['po_number']} issued. It is now locked; record receipts against it.", 'success')
    return redirect(url_for('orders.detail', po_id=po_id))


@bp.route('/purchase-orders/<int:po_id>/cancel', methods=('POST',))
def cancel(po_id):
    db = get_db()
    po = _get_po_or_404(db, po_id)
    if po_has_receipts(db, po_id):
        # FR-O4: Cancelled allowed only before any receipt.
        flash('This PO already has receipts and can no longer be cancelled.', 'error')
    elif po['status'] not in ('Draft', 'Issued'):
        flash('Only Draft or Issued POs can be cancelled.', 'error')
    else:
        db.execute(
            "UPDATE purchase_orders SET status = 'Cancelled', updated_at = CURRENT_TIMESTAMP"
            ' WHERE id = ?',
            (po_id,),
        )
        db.commit()
        flash(f"PO {po['po_number']} cancelled.", 'success')
    return redirect(url_for('orders.detail', po_id=po_id))


@bp.route('/purchase-orders/<int:po_id>/close', methods=('POST',))
def close(po_id):
    db = get_db()
    po = _get_po_or_404(db, po_id)
    if po['status'] not in ('Received', 'Partially Received'):
        flash('Only Received (or Partially Received) POs can be closed.', 'error')
    else:
        db.execute(
            "UPDATE purchase_orders SET status = 'Closed', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (po_id,),
        )
        db.commit()
        flash(f"PO {po['po_number']} closed.", 'success')
    return redirect(url_for('orders.detail', po_id=po_id))


# --- Receiving (W8, FR-S1) ------------------------------------------------------

@bp.route('/purchase-orders/<int:po_id>/receive', methods=('GET', 'POST'))
def receive(po_id):
    db = get_db()
    po = _get_po_or_404(db, po_id)
    if po['status'] not in RECEIVABLE_STATUSES:
        flash('Receipts can only be recorded against an issued PO.', 'error')
        return redirect(url_for('orders.detail', po_id=po_id))
    lines = _po_lines(db, po_id)

    if request.method == 'POST':
        receipt_date = request.form.get('date', '').strip() or date.today().isoformat()
        note = request.form.get('note', '').strip()
        confirm_over = request.form.get('confirm_over') == '1'
        entries = []
        errors = []
        over_lines = []
        for line in lines:
            raw = request.form.get(f"qty_{line['id']}", '').strip()
            if not raw:
                continue
            qty = parse_number(raw)
            if qty is None or qty <= 0:
                errors.append(
                    f"{line['material_name']}: received quantity must be a positive number."
                )
                continue
            if line['received_qty'] + qty > line['qty'] + EPS:
                over_lines.append(
                    f"{line['material_name']} (ordered {line['qty']:g}, "
                    f"already received {line['received_qty']:g}, entering {qty:g})"
                )
            entries.append((line, qty))

        if errors:
            for e in errors:
                flash(e, 'error')
        elif not entries:
            flash('Enter a received quantity for at least one line.', 'error')
        elif over_lines and not confirm_over:
            # FR-S1: over-receipt only after an explicit warning confirmation.
            flash(
                'Over-receipt warning — the following lines exceed the ordered quantity: '
                + '; '.join(over_lines)
                + '. Tick the confirmation box below to accept the over-receipt.',
                'error',
            )
            return render_template(
                'orders/receive.html', po=po, lines=lines,
                entered={f"qty_{l['id']}": request.form.get(f"qty_{l['id']}", '') for l in lines},
                receipt_date=receipt_date, note=note, needs_over_confirm=True,
            )
        else:
            for line, qty in entries:
                db.execute(
                    'INSERT INTO stock_transactions (project_id, material_id, type, qty,'
                    " po_line_id, date, reason_notes) VALUES (?, ?, 'Receipt', ?, ?, ?, ?)",
                    (po['project_id'], line['material_id'], qty, line['id'], receipt_date, note),
                )
            refresh_po_status(db, po_id)
            db.commit()
            flash('Receipt recorded and posted to the stock ledger.', 'success')
            return redirect(url_for('orders.detail', po_id=po_id))

    return render_template(
        'orders/receive.html', po=po, lines=lines, entered={},
        receipt_date=date.today().isoformat(), note='', needs_over_confirm=False,
    )


# --- Printable PO (FR-O5 / FR-O6, report R3) -------------------------------------

@bp.route('/purchase-orders/<int:po_id>/print')
def print_po(po_id):
    db = get_db()
    po = _get_po_or_404(db, po_id)
    vendor = db.execute('SELECT * FROM vendors WHERE id = ?', (po['vendor_id'],)).fetchone()
    lines = _po_lines(db, po_id)
    has_rates = any(l['rate'] is not None for l in lines)
    total = sum(l['amount'] for l in lines if l['amount'] is not None) if has_rates else None
    return render_template(
        'orders/print.html', po=po, vendor=vendor, lines=lines,
        has_rates=has_rates, total=total,
    )
