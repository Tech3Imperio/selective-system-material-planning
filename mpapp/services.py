"""Shared business logic: coverage, stock balances, PO numbering and lifecycle.

Guard-rails from the PRD live here so every route enforces them identically:
- FR-O2: Tostem-supplied materials are never allowed on a purchase order.
- FR-S5: stock issues may not take on-hand below zero.
- R2:    To-order gap = max(0, Required - Ordered).
"""
import math

from flask import abort

from .db import get_db

# Float-sum tolerance for quantity comparisons (SQLite REAL aggregation).
EPS = 1e-9

# PO statuses that count as "Ordered" in coverage (Draft and Cancelled do not).
ORDERED_PO_STATUSES = ('Issued', 'Partially Received', 'Received', 'Closed')

UOM_CHOICES = ['sqm', 'm', 'mm', 'nos', 'pairs', 'tubes', 'rolls', 'box', 'kg', 'litre']
CATEGORY_CHOICES = ['Window Consumable', 'Site PPE', 'Tools', 'Other']

# --- Formula-material calculation (Phase-1.5 extension) ----------------------
#
# Silicone / Screws / Masking Tape quantities are derived from each window's
# product Width & Height (from the drawing sheet), applied per window and summed.
# The reference points supplied by the business are encoded as the constants
# below so the numbers reproduce those anchors exactly.

# Silicone: cartridges scale with perimeter × gap. Anchor: a 2000×2000 window
# (perimeter 8000mm) with a 5mm gap uses ~3.5 cartridges (280 ml each).
SILICONE_DEFAULT_GAP_MM = 5.0
SILICONE_REF_CARTRIDGES = 3.5
SILICONE_REF_PERIMETER = 8000.0   # 2 × (2000 + 2000)
SILICONE_REF_GAP = 5.0
SILICONE_K = SILICONE_REF_CARTRIDGES / (SILICONE_REF_PERIMETER * SILICONE_REF_GAP)

# Screws: ~1 screw per 150mm of perimeter. Anchor: 3000×3000 window
# (perimeter 12000mm) needs ~80 screws (12000 / 150 = 80).
SCREWS_DEFAULT_SPACING_MM = 150.0

# Masking tape: a standard 20-metre roll; tape used per window = 2 × perimeter.
MASKING_TAPE_ROLL_MM = 20000.0
MASKING_TAPE_PASSES = 2


def perimeter_mm(width_mm, height_mm):
    return 2.0 * (float(width_mm) + float(height_mm))


def silicone_cartridges(width_mm, height_mm, gap_mm=SILICONE_DEFAULT_GAP_MM):
    """Cartridges for one window (raw, unrounded)."""
    return perimeter_mm(width_mm, height_mm) * float(gap_mm) * SILICONE_K


def screws_count(width_mm, height_mm, spacing_mm=SCREWS_DEFAULT_SPACING_MM):
    """Screws for one window (raw, unrounded)."""
    return perimeter_mm(width_mm, height_mm) / float(spacing_mm)


def masking_tape_rolls(width_mm, height_mm):
    """Masking-tape rolls for one window (raw, unrounded)."""
    return (MASKING_TAPE_PASSES * perimeter_mm(width_mm, height_mm)) / MASKING_TAPE_ROLL_MM


def compute_formula_material(method, products, params=None):
    """Aggregate a formula material over a list of window products.

    products: iterable of rows/dicts with product_width, product_height, window_qty.
    Returns dict with raw total, rounded-up total, uom, and a human summary of the
    parameters used. Returns None for an unknown method.
    """
    params = params or {}
    raw = 0.0
    if method == 'silicone':
        gap = float(params.get('gap_mm', SILICONE_DEFAULT_GAP_MM))
        for p in products:
            raw += silicone_cartridges(p['product_width'], p['product_height'], gap) * p['window_qty']
        return {
            'method': 'silicone', 'raw': raw, 'qty': math.ceil(raw - EPS),
            'uom': 'tubes', 'params': {'gap_mm': gap},
            'summary': f'{gap:g}mm gap · perimeter × gap · ref 2000×2000 = 3.5 cartridges',
        }
    if method == 'screws':
        spacing = float(params.get('spacing_mm', SCREWS_DEFAULT_SPACING_MM))
        for p in products:
            raw += screws_count(p['product_width'], p['product_height'], spacing) * p['window_qty']
        return {
            'method': 'screws', 'raw': raw, 'qty': math.ceil(raw - EPS),
            'uom': 'nos', 'params': {'spacing_mm': spacing},
            'summary': f'{spacing:g}mm spacing · 1 screw per {spacing:g}mm of perimeter',
        }
    if method == 'masking_tape':
        for p in products:
            raw += masking_tape_rolls(p['product_width'], p['product_height']) * p['window_qty']
        return {
            'method': 'masking_tape', 'raw': raw, 'qty': math.ceil(raw - EPS),
            'uom': 'rolls', 'params': {},
            'summary': f'{MASKING_TAPE_PASSES} × perimeter ÷ {MASKING_TAPE_ROLL_MM/1000:g}m roll',
        }
    return None


FORMULA_MATERIALS = {
    'silicone': 'Silicone',
    'screws': 'Screws',
    'masking_tape': 'Masking Tape',
}


def tostem_po_error(material_names):
    names = ', '.join(sorted(set(material_names)))
    return (
        f"{names}: Tostem-supplied materials cannot be placed on a vendor purchase order. "
        "Report their quantities to Tostem via the Tostem Requisition report "
        "(Project → Reports → Tostem Requisition)."
    )


def get_project_or_404(project_id):
    row = get_db().execute('SELECT * FROM projects WHERE id = ?', (project_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def touch(db, table, row_id):
    db.execute(f'UPDATE {table} SET updated_at = CURRENT_TIMESTAMP WHERE id = ?', (row_id,))


# --- Stock ---------------------------------------------------------------

def stock_on_hand(db, project_id, material_id):
    """On hand = sum of signed ledger quantities. Never stored (PRD §7.8)."""
    row = db.execute(
        'SELECT COALESCE(SUM(qty), 0) FROM stock_transactions WHERE project_id = ? AND material_id = ?',
        (project_id, material_id),
    ).fetchone()
    return row[0]


# --- Coverage (report R2 / workflow W10) ---------------------------------

def coverage_rows(db, project_id):
    """One row per material touched by this project, with the R2 columns."""
    rows = db.execute(
        """
        SELECT m.id AS material_id, m.name, m.uom, m.supply_source,
          COALESCE((SELECT SUM(rl.required_qty) FROM requirement_lines rl
                    WHERE rl.project_id = :p AND rl.material_id = m.id), 0) AS required,
          COALESCE((SELECT SUM(pl.qty) FROM po_lines pl
                    JOIN purchase_orders po ON po.id = pl.po_id
                    WHERE po.project_id = :p AND pl.material_id = m.id
                      AND po.status IN ('Issued', 'Partially Received', 'Received', 'Closed')), 0) AS ordered,
          COALESCE((SELECT SUM(st.qty) FROM stock_transactions st
                    WHERE st.project_id = :p AND st.material_id = m.id AND st.type = 'Receipt'), 0) AS received,
          COALESCE((SELECT -SUM(st.qty) FROM stock_transactions st
                    WHERE st.project_id = :p AND st.material_id = m.id AND st.type = 'Issue'), 0) AS issued,
          COALESCE((SELECT SUM(st.qty) FROM stock_transactions st
                    WHERE st.project_id = :p AND st.material_id = m.id), 0) AS on_hand
        FROM materials m
        WHERE m.id IN (
            SELECT material_id FROM requirement_lines WHERE project_id = :p
            UNION
            SELECT pl.material_id FROM po_lines pl
              JOIN purchase_orders po ON po.id = pl.po_id
              WHERE po.project_id = :p AND po.status != 'Cancelled'
            UNION
            SELECT material_id FROM stock_transactions WHERE project_id = :p
        )
        ORDER BY m.name COLLATE NOCASE
        """,
        {'p': project_id},
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['to_order_gap'] = max(0, d['required'] - d['ordered'])
        result.append(d)
    return result


# --- Window schedule summary (FR-W5) --------------------------------------

def window_summary(db, project_id):
    totals = db.execute(
        'SELECT COUNT(*) AS line_count, COALESCE(SUM(quantity), 0) AS window_count '
        'FROM window_lines WHERE project_id = ?',
        (project_id,),
    ).fetchone()
    per_config = db.execute(
        """
        SELECT c.code, c.name, COUNT(*) AS line_count, SUM(w.quantity) AS window_count
        FROM window_lines w JOIN window_config_types c ON c.id = w.config_type_id
        WHERE w.project_id = ?
        GROUP BY c.id ORDER BY c.code COLLATE NOCASE
        """,
        (project_id,),
    ).fetchall()
    return {
        'line_count': totals['line_count'],
        'window_count': totals['window_count'],
        'per_config': per_config,
    }


# --- Purchase orders ------------------------------------------------------

def next_po_number(db, year):
    """FR-O3: SS-PO-YYYY-NNN with a per-year sequence."""
    prefix = f'SS-PO-{year}-'
    row = db.execute(
        'SELECT MAX(CAST(SUBSTR(po_number, ?) AS INTEGER)) FROM purchase_orders WHERE po_number LIKE ?',
        (len(prefix) + 1, prefix + '%'),
    ).fetchone()
    seq = (row[0] or 0) + 1
    return f'{prefix}{seq:03d}'


def po_line_received_qty(db, po_line_id):
    row = db.execute(
        "SELECT COALESCE(SUM(qty), 0) FROM stock_transactions WHERE po_line_id = ? AND type = 'Receipt'",
        (po_line_id,),
    ).fetchone()
    return row[0]


def po_has_receipts(db, po_id):
    row = db.execute(
        """
        SELECT COUNT(*) FROM stock_transactions st
        JOIN po_lines pl ON pl.id = st.po_line_id
        WHERE pl.po_id = ? AND st.type = 'Receipt'
        """,
        (po_id,),
    ).fetchone()
    return row[0] > 0


def refresh_po_status(db, po_id):
    """FR-O4 / W8: after a receipt, move Issued <-> Partially Received <-> Received."""
    po = db.execute('SELECT * FROM purchase_orders WHERE id = ?', (po_id,)).fetchone()
    if po is None or po['status'] not in ('Issued', 'Partially Received', 'Received'):
        return
    lines = db.execute('SELECT * FROM po_lines WHERE po_id = ?', (po_id,)).fetchall()
    total_received = 0.0
    fully_received = bool(lines)
    for line in lines:
        received = po_line_received_qty(db, line['id'])
        total_received += received
        if received < line['qty'] - EPS:
            fully_received = False
    if fully_received:
        new_status = 'Received'
    elif total_received > EPS:
        new_status = 'Partially Received'
    else:
        new_status = 'Issued'
    if new_status != po['status']:
        db.execute(
            'UPDATE purchase_orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
            (new_status, po_id),
        )


def parse_positive_number(value):
    """Parse a user-typed positive quantity; returns None when invalid."""
    try:
        n = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return n


def parse_number(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


# --- App settings (company profile / PO branding) -----------------------------

SETTING_DEFAULTS = {
    'company_name': 'Selective Systems',
    'company_tagline': 'Authorised Tostem Partner · Aluminium Windows',
    'company_address': '',
    'company_gst': '',
    'company_phone': '',
    'company_email': '',
    'po_terms': '',
    'po_signatory': '',
}


def get_app_settings(db):
    """Company profile / PO branding settings, with defaults for missing keys."""
    stored = {
        row['key']: row['value']
        for row in db.execute('SELECT key, value FROM app_settings').fetchall()
    }
    return {key: stored.get(key, default) for key, default in SETTING_DEFAULTS.items()}


def save_app_settings(db, values):
    for key in SETTING_DEFAULTS:
        if key in values:
            db.execute(
                'INSERT INTO app_settings (key, value) VALUES (?, ?)'
                ' ON CONFLICT(key) DO UPDATE SET value = excluded.value,'
                ' updated_at = CURRENT_TIMESTAMP',
                (key, str(values[key]).strip()),
            )


# --- WhatsApp click-to-chat (pre-filled message; user sends manually) ---------

def whatsapp_phone_digits(phone):
    """Digits-only phone for wa.me. Returns None when unusable (too short)."""
    digits = ''.join(ch for ch in str(phone or '') if ch.isdigit())
    return digits if len(digits) >= 8 else None


def build_po_whatsapp_message(settings, po, vendor, lines):
    """Plain-text PO summary for the wa.me pre-filled message."""
    from .filters import fmt_date, fmt_qty

    out = [
        f"*Purchase Order {po['po_number']}*",
        settings.get('company_name', 'Selective Systems'),
        f"Date: {fmt_date(po['order_date'])}",
        f"Vendor: {vendor['name']}",
        '',
        'Items:',
    ]
    for i, line in enumerate(lines, start=1):
        item = f"{i}. {line['material_name']} — {fmt_qty(line['qty'])} {line['uom']}"
        if line['notes']:
            item += f" ({line['notes']})"
        out.append(item)
    out.append('')
    if po['delivery_address']:
        out.append(f"Delivery address: {po['delivery_address']}")
    if po['expected_delivery']:
        out.append(f"Expected delivery: {fmt_date(po['expected_delivery'])}")
    out.append('')
    out.append('Please confirm availability and delivery date. Thank you.')
    return '\n'.join(out)


def build_po_whatsapp_link(settings, po, vendor, lines):
    """wa.me click-to-chat URL, or None when the vendor has no usable phone."""
    from urllib.parse import quote

    digits = whatsapp_phone_digits(vendor['phone'])
    if not digits:
        return None
    message = build_po_whatsapp_message(settings, po, vendor, lines)
    return f'https://wa.me/{digits}?text={quote(message)}'
