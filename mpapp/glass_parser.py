"""Parser for Tostem "Drawing Sheet" PDFs.

Extracts, per project:
  * glass_lines    — one per glass piece: ref code, glass W/H, final quantity.
  * products       — one per window: ref code, product W/H, window count
                     (feeds the silicone / screws / masking-tape formulas).

Two page layouts occur and are detected independently per page:

  FORMAT A — a single "Order Qty (sets)" applies to the whole page. Each glass
             sub-row carries a "Qty/1set". Final glass qty = pageOrderQty × Qty/1set.
             The window product is the page-level Product Width/Height × pageOrderQty
             (the per-row Width/Height are sub-panel sizes, not the window).

  FORMAT B — each data row is its own window with its own "Order Qty (sets)" and a
             glass "Qty" that is already the final total (no multiplication). The
             window product is the row's Product Width/Height × row Order Qty.

Rows with no Glass Width/Height (screen/mesh panels) have no glass requirement and
are skipped for glass; in Format B such a row can still be a window product.

Column positions come from pdfplumber's ruling-line table extraction, which keeps
empty cells aligned — the columns are derived from each page's own header rows, so
the parser adapts if the template shifts rather than relying on fixed indices.
"""


def _clean(cell):
    return ''.join(str(cell).split()) if cell not in (None, '') else ''


def _num(v):
    s = _clean(v)
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return None


def _find_subheader(table):
    """Row index of the sub-header (the row that carries 'GLS' and 'GW')."""
    for idx, row in enumerate(table):
        cells = [_clean(c) for c in row]
        if 'GLS' in cells and 'GW' in cells:
            return idx
    return None


def parse_table(table, page_no=None):
    """Parse one extracted table. Returns dict(glass=[...], products=[...]) or None."""
    sub_idx = _find_subheader(table)
    if sub_idx is None or sub_idx == 0:
        return None
    subrow = [_clean(c) for c in table[sub_idx]]

    def label_idx(label):
        return subrow.index(label) if label in subrow else None

    width_i = label_idx('Width')      # product width (sub-row column)
    height_i = label_idx('Height')    # product height (sub-row column)
    sspec_i = label_idx('S.Spec')

    gw_cols = [i for i, c in enumerate(subrow) if c == 'GW']
    gh_cols = [i for i, c in enumerate(subrow) if c == 'GH']
    qty_cols = [i for i, c in enumerate(subrow) if c in ('Qty', 'Qty/1set')]
    glass_qty_cols = [i for i in qty_cols if sspec_i is None or i > sspec_i]
    if not glass_qty_cols:
        return None
    is_format_a = subrow[glass_qty_cols[0]] == 'Qty/1set'

    # Build (GW, GH, Qty) column triplets for GLASS #1..#3.
    triplets = []
    for gw in gw_cols:
        gh = next((i for i in gh_cols if i > gw), None)
        qc = next((i for i in glass_qty_cols if i > (gh if gh is not None else gw)), None)
        if gh is not None and qc is not None:
            triplets.append((gw, gh, qc))

    # Per-row order/qty column: the header cell (row above sub-header) that reads
    # 'Qty/1set' (Format A) or 'Order Qty (sets)' (Format B).
    header_top = [_clean(c) for c in table[sub_idx - 1]]
    order_col = None
    for i, c in enumerate(header_top):
        if c in ('Qty/1set', 'OrderQty(sets)'):
            order_col = i
            break

    # Page-level product (Format A): read the value row under the very top header.
    page_order_qty = None
    page_prod_w = page_prod_h = None
    if is_format_a:
        top = [_clean(c) for c in table[0]]
        if 'OrderQty(sets)' in top and len(table) > 1:
            valrow = table[1]
            page_order_qty = _num(valrow[top.index('OrderQty(sets)')])
            if 'ProductWidth' in top:
                page_prod_w = _num(valrow[top.index('ProductWidth')])
            if 'ProductHeight' in top:
                page_prod_h = _num(valrow[top.index('ProductHeight')])

    glass = []
    products = []
    page_product_ref = None
    for row in table[sub_idx + 1:]:
        no = _num(row[0]) if row and len(row) > 0 else None
        if no is None:
            continue  # footer / caption / non-data row
        ref = str(row[1]).strip() if len(row) > 1 and row[1] else ''
        if not ref:
            continue
        if page_product_ref is None:
            page_product_ref = ref
        prod_w = _num(row[width_i]) if width_i is not None and width_i < len(row) else None
        prod_h = _num(row[height_i]) if height_i is not None and height_i < len(row) else None
        row_qty = _num(row[order_col]) if order_col is not None and order_col < len(row) else None

        for gw_i, gh_i, q_i in triplets:
            gw = _num(row[gw_i]) if gw_i < len(row) else None
            gh = _num(row[gh_i]) if gh_i < len(row) else None
            gq = _num(row[q_i]) if q_i < len(row) else None
            if gw is None or gh is None:
                continue  # screen/mesh panel -> no glass
            final = (page_order_qty or 0) * (gq or 0) if is_format_a else (gq or 0)
            glass.append({
                'ref': ref, 'gw': gw, 'gh': gh, 'qty': int(final),
                'fmt': 'A' if is_format_a else 'B', 'page': page_no,
            })

        # Format B: each data row with product dims + count is its own window.
        if not is_format_a and prod_w and prod_h and row_qty:
            products.append({
                'ref': ref, 'pw': prod_w, 'ph': prod_h, 'qty': int(row_qty),
                'fmt': 'B', 'page': page_no,
            })

    # Format A: one window product for the whole page.
    if is_format_a and page_prod_w and page_prod_h and page_order_qty:
        products.append({
            'ref': page_product_ref or '', 'pw': page_prod_w, 'ph': page_prod_h,
            'qty': int(page_order_qty), 'fmt': 'A', 'page': page_no,
        })

    return {'glass': glass, 'products': products, 'fmt': 'A' if is_format_a else 'B'}


def parse_pdf_bytes(content):
    """Parse a drawing-sheet PDF (bytes). Returns a result dict.

    Raises RuntimeError if pdfplumber is unavailable, ValueError if no glass
    tables were found (wrong document type).
    """
    try:
        import io
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            'PDF parsing requires the pdfplumber package (pip install pdfplumber).'
        ) from exc

    glass, products = [], []
    fmt_a_pages = fmt_b_pages = 0
    try:
        pdf_ctx = pdfplumber.open(io.BytesIO(content))
    except Exception as exc:
        raise ValueError(
            'This file could not be read as a PDF. Please upload the Tostem drawing '
            'sheet as a valid PDF.'
        ) from exc
    with pdf_ctx as pdf:
        for pnum, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables():
                res = parse_table(table, page_no=pnum)
                if not res:
                    continue
                glass.extend(res['glass'])
                products.extend(res['products'])
                if res['fmt'] == 'A':
                    fmt_a_pages += 1
                else:
                    fmt_b_pages += 1

    if not glass and not products:
        raise ValueError(
            'No Tostem drawing-sheet tables were found in this PDF. '
            'Please upload the drawing sheet exported from Tostem.'
        )
    return {
        'glass': glass,
        'products': products,
        'glass_total_qty': sum(g['qty'] for g in glass),
        'pages_format_a': fmt_a_pages,
        'pages_format_b': fmt_b_pages,
    }
