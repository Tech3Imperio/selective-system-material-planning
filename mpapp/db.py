"""Database layer: connection handling, schema, and first-run seed data."""
import sqlite3

from flask import current_app, g
from werkzeug.security import generate_password_hash


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_app(app):
    app.teardown_appcontext(close_db)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    client_name TEXT NOT NULL,
    site_address TEXT,
    status TEXT NOT NULL DEFAULT 'Draft' CHECK (status IN ('Draft', 'Active', 'Closed')),
    start_date TEXT,
    expected_end_date TEXT,
    crew_size INTEGER,
    notes TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS window_config_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    tracks INTEGER,
    panels INTEGER,
    description TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS window_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    opening_ref TEXT NOT NULL,
    location TEXT,
    config_type_id INTEGER NOT NULL REFERENCES window_config_types(id),
    width_mm REAL NOT NULL CHECK (width_mm > 0),
    height_mm REAL NOT NULL CHECK (height_mm > 0),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    tostem_order_ref TEXT,
    source TEXT NOT NULL DEFAULT 'Manual' CHECK (source IN ('Manual', 'Imported')),
    remarks TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_window_lines_project ON window_lines(project_id);

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    contact_person TEXT,
    phone TEXT,
    email TEXT,
    address TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    uom TEXT NOT NULL,
    supply_source TEXT NOT NULL CHECK (supply_source IN ('Vendor-procured', 'Tostem-supplied')),
    default_vendor_id INTEGER REFERENCES vendors(id),
    notes TEXT,
    -- item_type splits the catalog into consumable Materials vs site Tools (PPE etc.).
    -- Both reuse the same requirement-line / PO / stock machinery.
    item_type TEXT NOT NULL DEFAULT 'Material' CHECK (item_type IN ('Material', 'Tool')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS requirement_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    material_id INTEGER NOT NULL REFERENCES materials(id),
    required_qty REAL NOT NULL CHECK (required_qty > 0),
    uom TEXT NOT NULL,
    basis_note TEXT,
    status TEXT NOT NULL DEFAULT 'Draft' CHECK (status IN ('Draft', 'Approved')),
    -- calc_method: 'manual' (typed by user, the Phase-1 default) or a formula key
    -- ('silicone' | 'screws' | 'masking_tape'). calc_params holds the JSON inputs
    -- used (e.g. {"gap_mm": 5}) so the requirement can be recomputed and audited.
    calc_method TEXT NOT NULL DEFAULT 'manual',
    calc_params TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_requirement_lines_project ON requirement_lines(project_id);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    order_date TEXT NOT NULL,
    expected_delivery TEXT,
    status TEXT NOT NULL DEFAULT 'Draft'
        CHECK (status IN ('Draft', 'Issued', 'Partially Received', 'Received', 'Closed', 'Cancelled')),
    terms_notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_project ON purchase_orders(project_id);

CREATE TABLE IF NOT EXISTS po_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id INTEGER NOT NULL REFERENCES purchase_orders(id),
    material_id INTEGER NOT NULL REFERENCES materials(id),
    qty REAL NOT NULL CHECK (qty > 0),
    uom TEXT NOT NULL,
    rate REAL,
    requirement_line_id INTEGER REFERENCES requirement_lines(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_po_lines_po ON po_lines(po_id);

CREATE TABLE IF NOT EXISTS stock_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    material_id INTEGER NOT NULL REFERENCES materials(id),
    type TEXT NOT NULL CHECK (type IN ('Receipt', 'Issue', 'Adjustment')),
    qty REAL NOT NULL,
    po_line_id INTEGER REFERENCES po_lines(id),
    date TEXT NOT NULL,
    reason_notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_stock_txn_project ON stock_transactions(project_id);

CREATE TABLE IF NOT EXISTS import_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    filename TEXT NOT NULL,
    content BLOB NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Imported', 'Discarded')),
    rows_imported INTEGER NOT NULL DEFAULT 0,
    rows_rejected INTEGER NOT NULL DEFAULT 0,
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- A parsed Tostem drawing sheet PDF. The source file is retained for audit.
CREATE TABLE IF NOT EXISTS drawing_sheets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    filename TEXT NOT NULL,
    content BLOB NOT NULL,
    glass_count INTEGER NOT NULL DEFAULT 0,
    glass_total_qty INTEGER NOT NULL DEFAULT 0,
    product_count INTEGER NOT NULL DEFAULT 0,
    pages_format_a INTEGER NOT NULL DEFAULT 0,
    pages_format_b INTEGER NOT NULL DEFAULT 0,
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- One glass piece required by the project. Dimensions (GW/GH) and Qty come from
-- the drawing sheet and are FIXED. thickness/glass_type/glass_color are manual,
-- editable per line (not derived from the PDF).
CREATE TABLE IF NOT EXISTS glass_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    drawing_sheet_id INTEGER NOT NULL REFERENCES drawing_sheets(id),
    ref_code TEXT NOT NULL,
    glass_width REAL NOT NULL,
    glass_height REAL NOT NULL,
    qty INTEGER NOT NULL,
    product_width REAL,
    product_height REAL,
    source_format TEXT,
    page_no INTEGER,
    thickness TEXT,
    glass_type TEXT,
    glass_color TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_glass_lines_project ON glass_lines(project_id);

-- One window/product extracted from the drawing sheet, with its product
-- dimensions and window count. These feed the silicone/screws/masking-tape
-- formulas (per the brief: use the drawing sheet's Product Width/Height).
CREATE TABLE IF NOT EXISTS drawing_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    drawing_sheet_id INTEGER NOT NULL REFERENCES drawing_sheets(id),
    ref_code TEXT NOT NULL,
    product_width REAL NOT NULL,
    product_height REAL NOT NULL,
    window_qty INTEGER NOT NULL,
    source_format TEXT,
    page_no INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_drawing_products_project ON drawing_products(project_id);
"""

# Seed catalog per PRD §7.4 / FR-C2: name, category, uom, supply_source, item_type.
# PPE (shoes/helmets/jackets) are Tools; everything else is a Material.
SEED_MATERIALS = [
    ('Glass', 'Window Consumable', 'sqm', 'Vendor-procured', 'Material'),
    ('Silicone', 'Window Consumable', 'tubes', 'Vendor-procured', 'Material'),
    ('Packers', 'Window Consumable', 'nos', 'Vendor-procured', 'Material'),
    ('Screws', 'Window Consumable', 'box', 'Vendor-procured', 'Material'),
    ('Masking Tape', 'Window Consumable', 'rolls', 'Vendor-procured', 'Material'),
    ('Gasket', 'Window Consumable', 'm', 'Tostem-supplied', 'Material'),
    ('Safety Shoes', 'Site PPE', 'pairs', 'Vendor-procured', 'Tool'),
    ('Helmets', 'Site PPE', 'nos', 'Vendor-procured', 'Tool'),
    ('Jackets', 'Site PPE', 'nos', 'Vendor-procured', 'Tool'),
]


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    migrate_db(db)
    seed_db(db)
    db.commit()


def _columns(db, table):
    return {row[1] for row in db.execute(f'PRAGMA table_info({table})').fetchall()}


def migrate_db(db):
    """Add columns introduced after first release to pre-existing databases.

    CREATE TABLE IF NOT EXISTS never alters an existing table, so new columns
    are added here idempotently. New tables are handled by the schema script.
    """
    mat_cols = _columns(db, 'materials')
    if 'item_type' not in mat_cols:
        db.execute(
            "ALTER TABLE materials ADD COLUMN item_type TEXT NOT NULL DEFAULT 'Material'"
        )
        # Reclassify the seeded PPE items as Tools on upgrade.
        db.execute(
            "UPDATE materials SET item_type = 'Tool' "
            "WHERE name IN ('Safety Shoes', 'Helmets', 'Jackets')"
        )
    req_cols = _columns(db, 'requirement_lines')
    if 'calc_method' not in req_cols:
        db.execute(
            "ALTER TABLE requirement_lines ADD COLUMN calc_method TEXT NOT NULL DEFAULT 'manual'"
        )
    if 'calc_params' not in req_cols:
        db.execute('ALTER TABLE requirement_lines ADD COLUMN calc_params TEXT')


def seed_db(db):
    if db.execute('SELECT COUNT(*) FROM materials').fetchone()[0] == 0:
        db.executemany(
            'INSERT INTO materials (name, category, uom, supply_source, item_type)'
            ' VALUES (?, ?, ?, ?, ?)',
            SEED_MATERIALS,
        )
    if db.execute('SELECT COUNT(*) FROM window_config_types').fetchone()[0] == 0:
        db.execute(
            'INSERT INTO window_config_types (code, name, tracks, panels) VALUES (?, ?, ?, ?)',
            ('3T3P', '3 Track 3 Panel Sliding', 3, 3),
        )
    if db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
        db.execute(
            'INSERT INTO users (email, password_hash) VALUES (?, ?)',
            (
                current_app.config['ADMIN_EMAIL'],
                generate_password_hash(current_app.config['ADMIN_PASSWORD']),
            ),
        )
