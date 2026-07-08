"""Material requirement lines (W5, FR-R1..FR-R4) plus the formula-calculated
materials (silicone / screws / masking tape) added in the drawing-sheet extension.

Manual lines keep the Phase-1 guard-rail: required_qty is taken verbatim from user
input. Formula lines are derived from the drawing sheet's window products via
services.compute_formula_material and clearly record the parameters used.
Packers stay manual (with a reference note); gaskets are untouched here.
"""
import json

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .db import get_db
from .services import (
    FORMULA_MATERIALS, SCREWS_DEFAULT_SPACING_MM, SILICONE_DEFAULT_GAP_MM, SILICONE_K,
    MASKING_TAPE_PASSES, MASKING_TAPE_ROLL_MM,
    compute_formula_material, get_project_or_404, parse_positive_number,
)

bp = Blueprint('requirements', __name__, url_prefix='/projects/<int:project_id>/requirements')

ITEM_TYPE = 'Material'


def _active_materials(db):
    return db.execute(
        "SELECT * FROM materials WHERE active = 1 AND item_type = 'Material'"
        ' ORDER BY name COLLATE NOCASE'
    ).fetchall()


def _window_products(db, project_id):
    return db.execute(
        'SELECT * FROM drawing_products WHERE project_id = ? ORDER BY page_no, id',
        (project_id,),
    ).fetchall()


def _current_params(db, project_id):
    """Read the parameters last used for the silicone/screws auto lines, if any."""
    params = {'gap_mm': SILICONE_DEFAULT_GAP_MM, 'spacing_mm': SCREWS_DEFAULT_SPACING_MM}
    rows = db.execute(
        "SELECT calc_method, calc_params FROM requirement_lines"
        " WHERE project_id = ? AND calc_method != 'manual'",
        (project_id,),
    ).fetchall()
    for r in rows:
        if not r['calc_params']:
            continue
        try:
            data = json.loads(r['calc_params'])
        except (ValueError, TypeError):
            continue
        if r['calc_method'] == 'silicone' and 'gap_mm' in data:
            params['gap_mm'] = data['gap_mm']
        if r['calc_method'] == 'screws' and 'spacing_mm' in data:
            params['spacing_mm'] = data['spacing_mm']
    return params


def _formula_context(db, project_id):
    products = _window_products(db, project_id)
    prod_dicts = [
        {'product_width': p['product_width'], 'product_height': p['product_height'],
         'window_qty': p['window_qty']}
        for p in products
    ]
    params = _current_params(db, project_id)
    calcs = {}
    for method in FORMULA_MATERIALS:
        calcs[method] = compute_formula_material(method, prod_dicts, params)
    # existing auto lines keyed by method, to show saved/approved state
    auto = {
        r['calc_method']: r
        for r in db.execute(
            "SELECT rl.*, m.name AS material_name FROM requirement_lines rl"
            " JOIN materials m ON m.id = rl.material_id"
            " WHERE rl.project_id = ? AND rl.calc_method != 'manual'",
            (project_id,),
        ).fetchall()
    }
    return {
        'products': products,
        'total_windows': sum(p['window_qty'] for p in products),
        'products_json': [
            {'ref': p['ref_code'], 'pw': p['product_width'], 'ph': p['product_height'],
             'qty': p['window_qty']}
            for p in products
        ],
        'params': params,
        'calcs': calcs,
        'auto_lines': auto,
        'silicone_k': SILICONE_K,
        'tape_passes': MASKING_TAPE_PASSES,
        'tape_roll_mm': MASKING_TAPE_ROLL_MM,
    }


def _get_line_or_404(db, project_id, line_id):
    line = db.execute(
        'SELECT * FROM requirement_lines WHERE id = ? AND project_id = ?', (line_id, project_id)
    ).fetchone()
    if line is None:
        abort(404)
    return line


@bp.route('/')
def list_requirements(project_id):
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
        WHERE rl.project_id = ? AND m.item_type = 'Material'
        ORDER BY m.name COLLATE NOCASE, rl.id
        """,
        (project_id,),
    ).fetchall()
    packers = db.execute(
        "SELECT id FROM materials WHERE name = 'Packers' AND item_type = 'Material' LIMIT 1"
    ).fetchone()
    return render_template(
        'requirements/list.html',
        project=project, lines=lines, materials=_active_materials(db),
        formula=_formula_context(db, project_id),
        packers_id=packers['id'] if packers else None,
    )


@bp.route('/formulas/save', methods=('POST',))
def save_formulas(project_id):
    """Compute silicone/screws/masking-tape from the drawing-sheet window products
    and upsert them as requirement lines. Approved auto lines are left locked."""
    db = get_db()
    get_project_or_404(project_id)
    products = _window_products(db, project_id)
    if not products:
        flash('Upload a drawing sheet first — there are no window products to calculate from.',
              'error')
        return redirect(url_for('requirements.list_requirements', project_id=project_id))
    prod_dicts = [
        {'product_width': p['product_width'], 'product_height': p['product_height'],
         'window_qty': p['window_qty']}
        for p in products
    ]
    gap = parse_positive_number(request.form.get('gap_mm')) or SILICONE_DEFAULT_GAP_MM
    spacing = parse_positive_number(request.form.get('spacing_mm')) or SCREWS_DEFAULT_SPACING_MM
    params = {'gap_mm': gap, 'spacing_mm': spacing}
    chosen = request.form.getlist('methods')
    if not chosen:
        flash('Select at least one material to calculate.', 'error')
        return redirect(url_for('requirements.list_requirements', project_id=project_id))

    saved, skipped = [], []
    for method in chosen:
        if method not in FORMULA_MATERIALS:
            continue
        result = compute_formula_material(method, prod_dicts, params)
        if result is None or result['qty'] <= 0:
            continue
        material = db.execute(
            "SELECT * FROM materials WHERE name = ? AND item_type = 'Material'",
            (FORMULA_MATERIALS[method],),
        ).fetchone()
        if material is None:
            continue
        existing = db.execute(
            'SELECT * FROM requirement_lines WHERE project_id = ? AND calc_method = ?',
            (project_id, method),
        ).fetchone()
        note = (
            f"Auto-calculated from {len(products)} window product(s) in the drawing sheet — "
            f"{result['summary']}"
        )
        cparams = json.dumps(result['params'])
        if existing is not None:
            if existing['status'] == 'Approved':
                skipped.append(FORMULA_MATERIALS[method])
                continue
            db.execute(
                'UPDATE requirement_lines SET material_id = ?, required_qty = ?, uom = ?,'
                ' basis_note = ?, calc_params = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (material['id'], result['qty'], result['uom'], note, cparams, existing['id']),
            )
        else:
            db.execute(
                'INSERT INTO requirement_lines (project_id, material_id, required_qty, uom,'
                ' basis_note, calc_method, calc_params) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (project_id, material['id'], result['qty'], result['uom'], note, method, cparams),
            )
        saved.append(f"{FORMULA_MATERIALS[method]} = {result['qty']} {result['uom']}")
    db.commit()
    if saved:
        flash('Saved formula materials: ' + '; '.join(saved) + '.', 'success')
    if skipped:
        flash('Left unchanged (Approved/locked — un-approve to recompute): '
              + ', '.join(skipped) + '.', 'error')
    return redirect(url_for('requirements.list_requirements', project_id=project_id))


@bp.route('/add', methods=('POST',))
def add(project_id):
    db = get_db()
    get_project_or_404(project_id)
    material_id = request.form.get('material_id', '').strip()
    qty = parse_positive_number(request.form.get('required_qty'))
    basis_note = request.form.get('basis_note', '').strip()
    material = None
    if material_id.isdigit():
        material = db.execute(
            "SELECT * FROM materials WHERE id = ? AND active = 1 AND item_type = 'Material'",
            (int(material_id),),
        ).fetchone()
    if material is None:
        flash('Pick a material from the catalog.', 'error')
    elif qty is None:
        flash('Required quantity must be a positive number (typed in, per Phase 1 rules).', 'error')
    else:
        db.execute(
            'INSERT INTO requirement_lines (project_id, material_id, required_qty, uom, basis_note)'
            ' VALUES (?, ?, ?, ?, ?)',
            (project_id, material['id'], qty, material['uom'], basis_note),
        )
        db.commit()
        flash('Requirement line added (Draft).', 'success')
    return redirect(url_for('requirements.list_requirements', project_id=project_id))


@bp.route('/<int:line_id>/edit', methods=('GET', 'POST'))
def edit(project_id, line_id):
    db = get_db()
    project = get_project_or_404(project_id)
    line = _get_line_or_404(db, project_id, line_id)
    if line['calc_method'] != 'manual':
        flash('This is a formula-calculated line. Adjust it from the '
              '“Formula-calculated materials” panel (recompute), not by hand.', 'error')
        return redirect(url_for('requirements.list_requirements', project_id=project_id))
    if line['status'] == 'Approved':
        flash('This line is Approved and locked. Un-approve it first to edit (FR-R2).', 'error')
        return redirect(url_for('requirements.list_requirements', project_id=project_id))
    if request.method == 'POST':
        material_id = request.form.get('material_id', '').strip()
        qty = parse_positive_number(request.form.get('required_qty'))
        basis_note = request.form.get('basis_note', '').strip()
        material = None
        if material_id.isdigit():
            material = db.execute(
                "SELECT * FROM materials WHERE id = ? AND item_type = 'Material'",
                (int(material_id),),
            ).fetchone()
        if material is None:
            flash('Pick a material from the catalog.', 'error')
        elif qty is None:
            flash('Required quantity must be a positive number.', 'error')
        else:
            db.execute(
                'UPDATE requirement_lines SET material_id = ?, required_qty = ?, uom = ?,'
                ' basis_note = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (material['id'], qty, material['uom'], basis_note, line_id),
            )
            db.commit()
            flash('Requirement line updated.', 'success')
            return redirect(url_for('requirements.list_requirements', project_id=project_id))
    return render_template(
        'requirements/form.html', project=project, line=line, materials=_active_materials(db),
    )


@bp.route('/<int:line_id>/delete', methods=('POST',))
def delete(project_id, line_id):
    db = get_db()
    line = _get_line_or_404(db, project_id, line_id)
    if line['status'] == 'Approved':
        flash('Approved lines are locked. Un-approve first (FR-R2).', 'error')
        return redirect(url_for('requirements.list_requirements', project_id=project_id))
    linked = db.execute(
        'SELECT COUNT(*) FROM po_lines WHERE requirement_line_id = ?', (line_id,)
    ).fetchone()[0]
    if linked:
        flash('This line is referenced by purchase order line(s) and cannot be deleted.', 'error')
    else:
        db.execute('DELETE FROM requirement_lines WHERE id = ?', (line_id,))
        db.commit()
        flash('Requirement line deleted.', 'success')
    return redirect(url_for('requirements.list_requirements', project_id=project_id))


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
        flash('Requirement line approved and locked.', 'success')
    return redirect(url_for('requirements.list_requirements', project_id=project_id))


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
        flash('Requirement line un-approved — it can be edited again.', 'success')
    return redirect(url_for('requirements.list_requirements', project_id=project_id))
