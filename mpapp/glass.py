"""Glass Sheet: upload a Tostem drawing sheet PDF, parse glass requirements and
window products, and maintain the per-line glass spec (thickness/type/colour).

Glass dimensions and quantities are FIXED once parsed (a physical spec, not a
formula). Thickness / type / colour are manual, editable per line, and never
derived from the PDF.
"""
from flask import (
    Blueprint, abort, flash, redirect, render_template, request, send_file, url_for,
)

from .db import get_db
from .glass_parser import parse_pdf_bytes
from .services import get_project_or_404

bp = Blueprint('glass', __name__, url_prefix='/projects/<int:project_id>/glass')


@bp.route('/')
def sheet(project_id):
    db = get_db()
    project = get_project_or_404(project_id)
    sheets = db.execute(
        'SELECT * FROM drawing_sheets WHERE project_id = ? ORDER BY uploaded_at DESC, id DESC',
        (project_id,),
    ).fetchall()
    lines = db.execute(
        """
        SELECT gl.*, ds.filename AS sheet_filename
        FROM glass_lines gl JOIN drawing_sheets ds ON ds.id = gl.drawing_sheet_id
        WHERE gl.project_id = ?
        ORDER BY gl.page_no, gl.id
        """,
        (project_id,),
    ).fetchall()
    products = db.execute(
        'SELECT * FROM drawing_products WHERE project_id = ? ORDER BY page_no, id',
        (project_id,),
    ).fetchall()
    total_qty = sum(l['qty'] for l in lines)
    total_windows = sum(p['window_qty'] for p in products)
    return render_template(
        'glass/sheet.html',
        project=project, sheets=sheets, lines=lines, products=products,
        total_qty=total_qty, total_windows=total_windows,
    )


@bp.route('/upload', methods=('POST',))
def upload(project_id):
    db = get_db()
    get_project_or_404(project_id)
    file = request.files.get('file')
    if file is None or not file.filename:
        flash('Choose a Tostem drawing-sheet PDF to upload.', 'error')
        return redirect(url_for('glass.sheet', project_id=project_id))
    if not file.filename.lower().endswith('.pdf'):
        flash('The drawing sheet must be a PDF file.', 'error')
        return redirect(url_for('glass.sheet', project_id=project_id))

    content = file.read()
    try:
        result = parse_pdf_bytes(content)
    except (ValueError, RuntimeError) as exc:
        flash(str(exc), 'error')
        return redirect(url_for('glass.sheet', project_id=project_id))

    cur = db.execute(
        'INSERT INTO drawing_sheets (project_id, filename, content, glass_count,'
        ' glass_total_qty, product_count, pages_format_a, pages_format_b)'
        ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (project_id, file.filename, content, len(result['glass']),
         result['glass_total_qty'], len(result['products']),
         result['pages_format_a'], result['pages_format_b']),
    )
    sheet_id = cur.lastrowid
    for g in result['glass']:
        db.execute(
            'INSERT INTO glass_lines (project_id, drawing_sheet_id, ref_code, glass_width,'
            ' glass_height, qty, source_format, page_no) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (project_id, sheet_id, g['ref'], g['gw'], g['gh'], g['qty'], g['fmt'], g['page']),
        )
    for p in result['products']:
        db.execute(
            'INSERT INTO drawing_products (project_id, drawing_sheet_id, ref_code,'
            ' product_width, product_height, window_qty, source_format, page_no)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (project_id, sheet_id, p['ref'], p['pw'], p['ph'], p['qty'], p['fmt'], p['page']),
        )
    db.commit()
    flash(
        f"Parsed {len(result['glass'])} glass line(s) totalling {result['glass_total_qty']} "
        f"piece(s) across {result['pages_format_a']} Format-A and {result['pages_format_b']} "
        f"Format-B page(s); {len(result['products'])} window product(s) captured for "
        "silicone/screws/masking-tape calculation.",
        'success',
    )
    return redirect(url_for('glass.sheet', project_id=project_id))


def _get_line_or_404(db, project_id, line_id):
    line = db.execute(
        'SELECT * FROM glass_lines WHERE id = ? AND project_id = ?', (line_id, project_id)
    ).fetchone()
    if line is None:
        abort(404)
    return line


@bp.route('/line/<int:line_id>/spec', methods=('POST',))
def update_spec(project_id, line_id):
    """Edit the manual glass spec (thickness/type/colour) for one line."""
    db = get_db()
    get_project_or_404(project_id)
    _get_line_or_404(db, project_id, line_id)
    db.execute(
        'UPDATE glass_lines SET thickness = ?, glass_type = ?, glass_color = ?,'
        ' updated_at = CURRENT_TIMESTAMP WHERE id = ?',
        (request.form.get('thickness', '').strip() or None,
         request.form.get('glass_type', '').strip() or None,
         request.form.get('glass_color', '').strip() or None,
         line_id),
    )
    db.commit()
    flash('Glass spec updated.', 'success')
    return redirect(url_for('glass.sheet', project_id=project_id))


@bp.route('/bulk-spec', methods=('POST',))
def bulk_spec(project_id):
    """Apply a thickness/type/colour to every glass line at once (whole order is
    usually one spec; individual rows can still be overridden afterwards)."""
    db = get_db()
    get_project_or_404(project_id)
    thickness = request.form.get('thickness', '').strip()
    glass_type = request.form.get('glass_type', '').strip()
    glass_color = request.form.get('glass_color', '').strip()
    sets, params = [], []
    if thickness:
        sets.append('thickness = ?'); params.append(thickness)
    if glass_type:
        sets.append('glass_type = ?'); params.append(glass_type)
    if glass_color:
        sets.append('glass_color = ?'); params.append(glass_color)
    if not sets:
        flash('Enter at least one of thickness / type / colour to apply.', 'error')
    else:
        params.append(project_id)
        db.execute(
            f"UPDATE glass_lines SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP"
            ' WHERE project_id = ?',
            params,
        )
        db.commit()
        flash('Applied spec to all glass lines. Override individual rows as needed.', 'success')
    return redirect(url_for('glass.sheet', project_id=project_id))


@bp.route('/sheet/<int:sheet_id>/delete', methods=('POST',))
def delete_sheet(project_id, sheet_id):
    db = get_db()
    get_project_or_404(project_id)
    sheet_row = db.execute(
        'SELECT * FROM drawing_sheets WHERE id = ? AND project_id = ?', (sheet_id, project_id)
    ).fetchone()
    if sheet_row is None:
        abort(404)
    db.execute('DELETE FROM glass_lines WHERE drawing_sheet_id = ?', (sheet_id,))
    db.execute('DELETE FROM drawing_products WHERE drawing_sheet_id = ?', (sheet_id,))
    db.execute('DELETE FROM drawing_sheets WHERE id = ?', (sheet_id,))
    db.commit()
    flash('Drawing sheet and its parsed glass lines/products removed.', 'success')
    return redirect(url_for('glass.sheet', project_id=project_id))


@bp.route('/sheet/<int:sheet_id>/download')
def download_sheet(project_id, sheet_id):
    db = get_db()
    get_project_or_404(project_id)
    sheet_row = db.execute(
        'SELECT * FROM drawing_sheets WHERE id = ? AND project_id = ?', (sheet_id, project_id)
    ).fetchone()
    if sheet_row is None:
        abort(404)
    import io
    return send_file(
        io.BytesIO(sheet_row['content']), as_attachment=True, download_name=sheet_row['filename'],
        mimetype='application/pdf',
    )
