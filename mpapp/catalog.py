"""Material catalog (FR-C1..C3), vendors, and window configuration types (§7.2/§7.4/§7.5)."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from .db import get_db
from .services import CATEGORY_CHOICES, UOM_CHOICES

bp = Blueprint('catalog', __name__)


# --- Materials -------------------------------------------------------------

@bp.route('/materials')
def materials():
    db = get_db()
    show_inactive = request.args.get('show_inactive') == '1'
    sql = """
        SELECT m.*, v.name AS vendor_name FROM materials m
        LEFT JOIN vendors v ON v.id = m.default_vendor_id
    """
    if not show_inactive:
        sql += ' WHERE m.active = 1'
    sql += ' ORDER BY m.name COLLATE NOCASE'
    rows = db.execute(sql).fetchall()
    return render_template('catalog/materials.html', materials=rows, show_inactive=show_inactive)


def _material_form():
    default_vendor = request.form.get('default_vendor_id', '').strip()
    item_type = request.form.get('item_type', 'Material').strip()
    return {
        'name': request.form.get('name', '').strip(),
        'category': request.form.get('category', '').strip(),
        'uom': request.form.get('uom', '').strip(),
        'supply_source': request.form.get('supply_source', '').strip(),
        'item_type': item_type if item_type in ('Material', 'Tool') else 'Material',
        'default_vendor_id': int(default_vendor) if default_vendor.isdigit() else None,
        'notes': request.form.get('notes', '').strip(),
    }


def _material_errors(f):
    errors = []
    if not f['name']:
        errors.append('Material name is required.')
    if not f['category']:
        errors.append('Category is required.')
    if not f['uom']:
        errors.append('Unit of measure is required.')
    if f['supply_source'] not in ('Vendor-procured', 'Tostem-supplied'):
        errors.append('Supply source must be Vendor-procured or Tostem-supplied.')
    return errors


def _vendor_options(db):
    return db.execute('SELECT * FROM vendors WHERE active = 1 ORDER BY name COLLATE NOCASE').fetchall()


@bp.route('/materials/new', methods=('GET', 'POST'))
def material_new():
    db = get_db()
    if request.method == 'POST':
        f = _material_form()
        errors = _material_errors(f)
        if errors:
            for e in errors:
                flash(e, 'error')
        else:
            db.execute(
                'INSERT INTO materials (name, category, uom, supply_source, item_type,'
                ' default_vendor_id, notes) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (f['name'], f['category'], f['uom'], f['supply_source'], f['item_type'],
                 f['default_vendor_id'], f['notes']),
            )
            db.commit()
            flash(f"{f['item_type']} added.", 'success')
            return redirect(url_for('catalog.materials'))
    return render_template(
        'catalog/material_form.html', material=None,
        vendors=_vendor_options(db), uoms=UOM_CHOICES, categories=CATEGORY_CHOICES,
    )


@bp.route('/materials/<int:material_id>/edit', methods=('GET', 'POST'))
def material_edit(material_id):
    db = get_db()
    material = db.execute('SELECT * FROM materials WHERE id = ?', (material_id,)).fetchone()
    if material is None:
        flash('Material not found.', 'error')
        return redirect(url_for('catalog.materials'))
    if request.method == 'POST':
        f = _material_form()
        errors = _material_errors(f)
        if errors:
            for e in errors:
                flash(e, 'error')
        else:
            db.execute(
                'UPDATE materials SET name = ?, category = ?, uom = ?, supply_source = ?,'
                ' item_type = ?, default_vendor_id = ?, notes = ?, updated_at = CURRENT_TIMESTAMP'
                ' WHERE id = ?',
                (f['name'], f['category'], f['uom'], f['supply_source'], f['item_type'],
                 f['default_vendor_id'], f['notes'], material_id),
            )
            db.commit()
            flash('Catalog item updated.', 'success')
            return redirect(url_for('catalog.materials'))
    return render_template(
        'catalog/material_form.html', material=material,
        vendors=_vendor_options(db), uoms=UOM_CHOICES, categories=CATEGORY_CHOICES,
    )


@bp.route('/materials/<int:material_id>/toggle-active', methods=('POST',))
def material_toggle(material_id):
    db = get_db()
    material = db.execute('SELECT * FROM materials WHERE id = ?', (material_id,)).fetchone()
    if material is None:
        flash('Material not found.', 'error')
    else:
        new_state = 0 if material['active'] else 1
        db.execute(
            'UPDATE materials SET active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (new_state, material_id),
        )
        db.commit()
        # FR-C3: deactivation hides from new-entry forms; history keeps the record.
        flash(
            f"Material {'reactivated' if new_state else 'deactivated'}. "
            'Historical records are unaffected.',
            'success',
        )
    return redirect(url_for('catalog.materials', show_inactive=1))


# --- Vendors ----------------------------------------------------------------

@bp.route('/vendors')
def vendors():
    db = get_db()
    show_inactive = request.args.get('show_inactive') == '1'
    sql = 'SELECT * FROM vendors'
    if not show_inactive:
        sql += ' WHERE active = 1'
    sql += ' ORDER BY name COLLATE NOCASE'
    return render_template(
        'catalog/vendors.html', vendors=db.execute(sql).fetchall(), show_inactive=show_inactive,
    )


def _vendor_form():
    return {k: request.form.get(k, '').strip()
            for k in ('name', 'contact_person', 'phone', 'email', 'address')}


@bp.route('/vendors/new', methods=('GET', 'POST'))
def vendor_new():
    if request.method == 'POST':
        f = _vendor_form()
        if not f['name']:
            flash('Vendor name is required.', 'error')
        else:
            db = get_db()
            db.execute(
                'INSERT INTO vendors (name, contact_person, phone, email, address)'
                ' VALUES (?, ?, ?, ?, ?)',
                (f['name'], f['contact_person'], f['phone'], f['email'], f['address']),
            )
            db.commit()
            flash('Vendor added.', 'success')
            return redirect(url_for('catalog.vendors'))
    return render_template('catalog/vendor_form.html', vendor=None)


@bp.route('/vendors/<int:vendor_id>/edit', methods=('GET', 'POST'))
def vendor_edit(vendor_id):
    db = get_db()
    vendor = db.execute('SELECT * FROM vendors WHERE id = ?', (vendor_id,)).fetchone()
    if vendor is None:
        flash('Vendor not found.', 'error')
        return redirect(url_for('catalog.vendors'))
    if request.method == 'POST':
        f = _vendor_form()
        if not f['name']:
            flash('Vendor name is required.', 'error')
        else:
            db.execute(
                'UPDATE vendors SET name = ?, contact_person = ?, phone = ?, email = ?, address = ?,'
                ' updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (f['name'], f['contact_person'], f['phone'], f['email'], f['address'], vendor_id),
            )
            db.commit()
            flash('Vendor updated.', 'success')
            return redirect(url_for('catalog.vendors'))
    return render_template('catalog/vendor_form.html', vendor=vendor)


@bp.route('/vendors/<int:vendor_id>/toggle-active', methods=('POST',))
def vendor_toggle(vendor_id):
    db = get_db()
    vendor = db.execute('SELECT * FROM vendors WHERE id = ?', (vendor_id,)).fetchone()
    if vendor is None:
        flash('Vendor not found.', 'error')
    else:
        db.execute(
            'UPDATE vendors SET active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (0 if vendor['active'] else 1, vendor_id),
        )
        db.commit()
        flash('Vendor updated.', 'success')
    return redirect(url_for('catalog.vendors', show_inactive=1))


# --- Window configuration types (§7.2, extensible) ---------------------------

@bp.route('/configs')
def configs():
    db = get_db()
    rows = db.execute(
        'SELECT * FROM window_config_types ORDER BY active DESC, code COLLATE NOCASE'
    ).fetchall()
    return render_template('catalog/configs.html', configs=rows)


def _config_form():
    tracks = request.form.get('tracks', '').strip()
    panels = request.form.get('panels', '').strip()
    return {
        'code': request.form.get('code', '').strip(),
        'name': request.form.get('name', '').strip(),
        'tracks': int(tracks) if tracks.isdigit() else None,
        'panels': int(panels) if panels.isdigit() else None,
        'description': request.form.get('description', '').strip(),
    }


@bp.route('/configs/new', methods=('GET', 'POST'))
def config_new():
    if request.method == 'POST':
        f = _config_form()
        db = get_db()
        if not f['code'] or not f['name']:
            flash('Code and name are required.', 'error')
        elif db.execute(
            'SELECT 1 FROM window_config_types WHERE code = ?', (f['code'],)
        ).fetchone():
            flash(f"Config code '{f['code']}' already exists.", 'error')
        else:
            db.execute(
                'INSERT INTO window_config_types (code, name, tracks, panels, description)'
                ' VALUES (?, ?, ?, ?, ?)',
                (f['code'], f['name'], f['tracks'], f['panels'], f['description']),
            )
            db.commit()
            flash('Configuration type added.', 'success')
            return redirect(url_for('catalog.configs'))
    return render_template('catalog/config_form.html', config=None)


@bp.route('/configs/<int:config_id>/edit', methods=('GET', 'POST'))
def config_edit(config_id):
    db = get_db()
    config = db.execute('SELECT * FROM window_config_types WHERE id = ?', (config_id,)).fetchone()
    if config is None:
        flash('Configuration type not found.', 'error')
        return redirect(url_for('catalog.configs'))
    if request.method == 'POST':
        f = _config_form()
        clash = db.execute(
            'SELECT 1 FROM window_config_types WHERE code = ? AND id != ?', (f['code'], config_id)
        ).fetchone()
        if not f['code'] or not f['name']:
            flash('Code and name are required.', 'error')
        elif clash:
            flash(f"Config code '{f['code']}' already exists.", 'error')
        else:
            db.execute(
                'UPDATE window_config_types SET code = ?, name = ?, tracks = ?, panels = ?,'
                ' description = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (f['code'], f['name'], f['tracks'], f['panels'], f['description'], config_id),
            )
            db.commit()
            flash('Configuration type updated.', 'success')
            return redirect(url_for('catalog.configs'))
    return render_template('catalog/config_form.html', config=config)


@bp.route('/configs/<int:config_id>/toggle-active', methods=('POST',))
def config_toggle(config_id):
    db = get_db()
    config = db.execute('SELECT * FROM window_config_types WHERE id = ?', (config_id,)).fetchone()
    if config is None:
        flash('Configuration type not found.', 'error')
    else:
        db.execute(
            'UPDATE window_config_types SET active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (0 if config['active'] else 1, config_id),
        )
        db.commit()
        flash('Configuration type updated.', 'success')
    return redirect(url_for('catalog.configs'))


@bp.route('/configs/quick-create', methods=('POST',))
def config_quick_create():
    """One-click config creation used by the import validation screen (W3)."""
    db = get_db()
    code = request.form.get('code', '').strip()
    name = request.form.get('name', '').strip() or code
    next_url = request.form.get('next', '')
    if not next_url.startswith('/'):
        next_url = url_for('catalog.configs')
    if not code:
        flash('Config code is required.', 'error')
    elif db.execute('SELECT 1 FROM window_config_types WHERE code = ?', (code,)).fetchone():
        flash(f"Config code '{code}' already exists.", 'error')
    else:
        db.execute(
            'INSERT INTO window_config_types (code, name) VALUES (?, ?)', (code, name)
        )
        db.commit()
        flash(f"Configuration type '{code}' created.", 'success')
    return redirect(next_url)
