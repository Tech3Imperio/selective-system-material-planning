"""Jinja filters: DD-MM-YYYY dates, trimmed quantities, rupee amounts (NFR N3)."""
from datetime import datetime


def fmt_date(value):
    if not value:
        return ''
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').strftime('%d-%m-%Y')
    except ValueError:
        return str(value)


def fmt_qty(value):
    if value is None or value == '':
        return ''
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    return ('%.3f' % f).rstrip('0').rstrip('.')


def fmt_money(value):
    if value is None or value == '':
        return ''
    return '₹ {:,.2f}'.format(float(value))


def register_filters(app):
    app.jinja_env.filters['ddmmyyyy'] = fmt_date
    app.jinja_env.filters['qty'] = fmt_qty
    app.jinja_env.filters['money'] = fmt_money
