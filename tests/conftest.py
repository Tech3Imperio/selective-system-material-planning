import pytest

from mpapp import create_app
from mpapp.db import get_db

ADMIN_EMAIL = 'owner@example.com'
ADMIN_PASSWORD = 'password123'


@pytest.fixture
def app(tmp_path):
    return create_app({
        'TESTING': True,
        'DATABASE': str(tmp_path / 'test.sqlite3'),
        'BACKUP_DIR': str(tmp_path / 'backups'),
        'SECRET_KEY': 'test-secret',
        'ADMIN_EMAIL': ADMIN_EMAIL,
        'ADMIN_PASSWORD': ADMIN_PASSWORD,
        'SESSION_TIMEOUT_MINUTES': 120,
    })


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth(client):
    client.post('/login', data={'email': ADMIN_EMAIL, 'password': ADMIN_PASSWORD})
    return client


def query(app, sql, params=()):
    with app.app_context():
        return get_db().execute(sql, params).fetchall()


def query_one(app, sql, params=()):
    with app.app_context():
        return get_db().execute(sql, params).fetchone()


def execute(app, sql, params=()):
    with app.app_context():
        db = get_db()
        cur = db.execute(sql, params)
        db.commit()
        return cur.lastrowid


@pytest.fixture
def project(app, auth):
    auth.post('/projects/new', data={
        'name': 'Skyline Towers — Tower B', 'client_name': 'Skyline Devco',
        'status': 'Active', 'site_address': '', 'start_date': '', 'expected_end_date': '',
        'crew_size': '6', 'notes': '',
    })
    return query_one(app, 'SELECT * FROM projects ORDER BY id DESC LIMIT 1')


@pytest.fixture
def vendor(app, auth):
    auth.post('/vendors/new', data={
        'name': 'Alpha Traders', 'contact_person': 'Ravi', 'phone': '99999',
        'email': 'alpha@example.com', 'address': 'Mumbai',
    })
    return query_one(app, 'SELECT * FROM vendors ORDER BY id DESC LIMIT 1')


def material_id(app, name):
    return query_one(app, 'SELECT id FROM materials WHERE name = ?', (name,))['id']


def add_requirement(auth, project_id, mat_id, qty, note='', approve=False, app=None):
    auth.post(f'/projects/{project_id}/requirements/add', data={
        'material_id': str(mat_id), 'required_qty': str(qty), 'basis_note': note,
    })
    line = query_one(app, 'SELECT * FROM requirement_lines ORDER BY id DESC LIMIT 1')
    if approve:
        auth.post(f'/projects/{project_id}/requirements/{line["id"]}/approve')
        line = query_one(app, 'SELECT * FROM requirement_lines WHERE id = ?', (line['id'],))
    return line


def add_tool(auth, project_id, mat_id, qty, note='', approve=False, app=None):
    auth.post(f'/projects/{project_id}/tools/add', data={
        'material_id': str(mat_id), 'required_qty': str(qty), 'basis_note': note,
    })
    line = query_one(app, 'SELECT * FROM requirement_lines ORDER BY id DESC LIMIT 1')
    if approve:
        auth.post(f'/projects/{project_id}/tools/{line["id"]}/approve')
        line = query_one(app, 'SELECT * FROM requirement_lines WHERE id = ?', (line['id'],))
    return line


def create_po(auth, app, project_id, vendor_id, req_line_ids):
    """Run the two-step W6 flow and return the created PO row."""
    auth.post(f'/projects/{project_id}/purchase-orders/from-requirements',
              data={'line_ids': [str(i) for i in req_line_ids]})
    auth.post(f'/projects/{project_id}/purchase-orders/create', data={
        'group_keys': 'g0',
        'vendor_g0': str(vendor_id),
        'lines_g0': ','.join(str(i) for i in req_line_ids),
    })
    return query_one(app, 'SELECT * FROM purchase_orders ORDER BY id DESC LIMIT 1')
