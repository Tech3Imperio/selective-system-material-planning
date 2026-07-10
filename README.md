# Selective Systems — Material Planning (Phase 1)

Standalone web application for planning installation materials for Tostem
pre-engineered aluminium window projects, per the Phase 1 PRD: window schedules,
material catalog, manually entered requirement lines, vendor purchase orders,
per-project stock ledger, and reports.

**Phase 1 deliberately contains no automatic quantity calculation (FR-R4).**
Every material quantity is typed in by the user. Window lines capture height,
width, and configuration type so the Phase 2 formula engine can run on existing
data without migration.

## Running the app

```bash
pip install -r requirements.txt
python run.py
# open http://127.0.0.1:5000
```

Default login (change via Settings after first login, or set env vars before
first run):

- Email: `tech3@imperiorailing.com` (override with `MP_ADMIN_EMAIL`)
- Password: `changeme123` (override with `MP_ADMIN_PASSWORD`)

Other environment variables:

- `MP_SESSION_TIMEOUT_MINUTES` — idle session timeout (default 120).

The SQLite database, secret key, and backups live in the Flask `instance/`
directory, created on first run. The nine starter materials (Glass, Silicone,
Packers, Screws, Masking Tape, Gasket, Safety Shoes, Helmets, Jackets) and the
3T3P configuration type are seeded automatically on first run.

## What's implemented (PRD §7–§10)

- **Projects** (FR-P1/P2): CRUD, status Draft/Active/Closed, list with search +
  status filter, archive instead of delete (FR-G3).
- **Window schedule** (FR-W1..W5): manual entry with mandatory config/width/
  height/qty, duplicate-line shortcut, inline creation of new configuration
  types, CSV/XLSX import with a row-level validation screen (one-click "create
  this config" for unknown codes; invalid rows rejected; the source file is
  stored for audit and downloadable), and a per-project summary.
- **Material catalog** (FR-C1..C3): extensible catalog with category, UoM,
  supply source (Vendor-procured / Tostem-supplied), optional default vendor;
  seeded on first run; deactivated materials are hidden from new-entry forms
  but preserved in history.
- **Requirements** (FR-R1..R4): manual quantity + basis note, Draft/Approved
  with approval locking, multiple lines per material aggregated in coverage,
  and the Phase 1 guard-rail — no quantity is ever prefilled or computed from
  window data.
- **Purchase orders** (FR-O1..O6): created from selected approved lines,
  grouped by default vendor (changeable) with traceability back to the
  requirement line; Tostem-supplied materials are rejected from POs with a
  message pointing at the Tostem Requisition (FR-O2); auto-numbering
  `SS-PO-YYYY-NNN`; lifecycle Draft → Issued → Partially Received → Received →
  Closed with Cancelled only before any receipt; printable PO with totals only
  when rates are entered.
- **Stock** (FR-S1..S5): receipts against issued PO lines (partial allowed,
  over-receipt needs explicit confirmation), direct receipts without a PO for
  Tostem arrivals, issues blocked beyond on-hand, adjustments require a reason,
  on-hand always derived from the ledger, filterable ledger + CSV export.
- **Reports** (§10): R1 requirement report, R2 coverage/shortfall
  (`To-order gap = max(0, Required − Ordered)`; Ordered counts issued POs),
  R3 printable PO, R4 Tostem Requisition (Tostem lines + window summary),
  R5 stock ledger export, plus a cross-project PO register with status/vendor
  filters (FR-T5). Printables use the browser's Print → Save as PDF.
- **General**: single-user login with session timeout (FR-G1), timestamps on
  all records (FR-G2), soft delete for projects/materials/POs (FR-G3), and an
  automated daily database backup (FR-G4).

## Backups & restore (FR-G4 / N5)

A background job writes a daily snapshot to `instance/backups/`
(`material-planning-YYYY-MM-DD.sqlite3`, last 14 kept), using SQLite's online
backup API. Manual backup: `flask --app run backup`.

**Restore procedure:** stop the app, copy the chosen backup file over
`instance/material_planning.sqlite3`, restart the app.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers the PRD acceptance criteria: import flow with invalid rows
(AC1), UI-extensible configs/materials (AC2), approval locking (AC3), the
FR-O2 Tostem guard-rail (AC4), the Tostem requisition (AC5), partial receipts
reconciling with coverage (AC6), no auto-computed quantities (AC7), and stock
guard-rails (AC8).

## Drawing-sheet extension (Materials/Tools split, Glass Sheet, formulas)

Built on top of the Phase 1 base, from real-world business clarifications:

- **Materials vs Tools** — the catalog now has an `item_type` (Material / Tool).
  Safety Shoes, Helmets and Jackets are Tools; Masking Tape stays a Material.
  Each project has separate **Materials** and **Tools** tabs, both reusing the
  same requirement-line / approval / PO / stock machinery.
- **Glass Sheet** — upload a Tostem drawing-sheet PDF; the parser auto-detects
  each page's layout independently:
  - *Format A*: one page-level `Order Qty (sets)`; final glass qty = pageQty × `Qty/1set`.
  - *Format B*: per-row `Order Qty (sets)`; the glass `Qty` column is already final.
  Rows with no glass width/height (screen/mesh panels) are skipped. Output is a
  Glass Sheet (Reference Code, Glass W, Glass H, final Qty) plus per-line manual
  **thickness / type / colour** fields (not derived from the PDF; bulk-apply +
  per-row override). Validated against the supplied sample: **112 lines, 916
  pieces, exact match.**
- **Formula auto-calc** (from each window's Product Width/Height in the drawing
  sheet, aggregated per project; parameters shown and recalculated live):
  - *Silicone* = perimeter × gap × k (editable **gap**, default 5 mm; anchored so
    a 2000×2000 window at 5 mm = 3.5 cartridges).
  - *Screws* = perimeter ÷ spacing (editable **spacing**, default 150 mm; a
    3000×3000 window = 80 screws).
  - *Masking Tape* = 2 × perimeter ÷ 20 m roll.
  These save as requirement lines with the parameters recorded in the basis note.
  Approved formula lines are locked (un-approve to recompute).
- **Packers** stay manual, with a reference note (`~60% 2mm, 30% 3mm, 10% 5mm`) —
  informational only, never used in a calculation.
- **Gaskets** are unchanged (still Tostem-supplied, manual, no calculation).

PDF parsing uses `pdfplumber` (added to requirements). Existing databases are
migrated automatically on startup (new columns/tables added; PPE reclassified as
Tools) — no manual migration step.

## Purchase-order workflow extension

- **Purchase Qty + Notes per requirement line** (Materials and Tools tabs):
  the owner's deliberate order quantity, independent of Required Qty (buffers
  for distant sites, trimming for nearby ones). Blank = order the required
  amount. **Purchase Qty is what goes on the PO**; the note is carried onto the
  PO line and the printed document. Editable on Approved lines too — approval
  locks the requirement, not the later purchase decision.
- **Branded PO template**: company name, tagline, address, GST, phone, email,
  Terms & Conditions and authorised-signatory name are configurable under
  **Settings → Company profile** (stored in the `app_settings` table). The logo
  is a placeholder at `mpapp/static/logo-placeholder.svg` — replace that file
  with the real logo. Each PO has a manually typed **delivery address** and
  **expected delivery date**; the **PO date is stamped automatically at issue**;
  numbering stays `SS-PO-YYYY-NNN` (sequential, resets each year).
- **Send via WhatsApp**: on the PO detail/print pages, a click-to-chat link
  (`wa.me/<vendor phone>?text=…`) opens WhatsApp with a formatted text version
  of the PO pre-filled (vendor, number, date, line items, delivery address,
  expected delivery) — the user reviews and hits send manually. No API
  integration. Requires the vendor's phone with country code.
- **Glass Sheet**: totals row (pieces + sqm) at the bottom of the table, and an
  **Export Excel (.xlsx)** download of the processed sheet including the
  manually entered Thickness / Glass Type / Colour per row — separate from the
  original-PDF download, which is unchanged.
- Delivery tracking is explicitly out of scope.

## UI

The interface follows the Claude Design handoff (`design_handoff_material_planning`):
a dark project-scoped sidebar shell, IBM Plex Sans, and a slate/blue design system
(cards, KPI tiles, status badges, compact tables, printable letterhead sheets for
the Tostem Requisition / PO / reports). It is implemented as hand-rolled CSS in
`mpapp/static/style.css` (token values mirror the Tailwind defaults named in the
handoff) plus shared Jinja macros in `mpapp/templates/_ui.html` — no build step,
no frontend dependencies. IBM Plex Sans is loaded from Google Fonts with a
`system-ui` fallback, so the app still works fully offline.

## Out of scope (Phase 1)

No formula engine (the gasket rule 6×H + 2×W is Phase 2), no multi-user roles,
no pricing analytics/GST, no wastage optimization, no notifications, no
multi-warehouse stock or inter-project transfers.
