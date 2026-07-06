# Amul Reconciliation — Project Instructions

> Living spec for this project. Add new instructions here so they don't have to be
> repeated. Last updated: 2026-07-06.

## Goal

Pull two invoice extracts from **Redash / Trino** and consolidate them into **one single
Parquet file** that can handle large data volumes, then run reconciliation analysis on that
file.

- **Source 1 — HPTech**: Zomato Hyperpure (HP WMS GRN extract)
- **Source 2 — GPOS**: Blinkit (document-digitisation variance extract)

Both feed **one consolidated Parquet file** (with a matching optional CSV).

## Deliverables

1. **`amul_recon_consolidation.ipynb`** — the notebook. Run top-to-bottom after editing dates.
2. **`PROJECT_INSTRUCTIONS.md`** — this file (accumulates all standing instructions).

## Notebook requirements (as given)

- **Date range at the top as variables** — replaced into the queries wherever needed.
  - HPTech uses `GRN_FROM` / `GRN_TO`  → placeholders `{{GRN from}}` / `{{GRN to}}`
  - GPOS uses `START_DATE` / `END_DATE` → placeholders `{{start_date}}` / `{{end_date}}`
- **Handle huge data**: chunked SQL reads streamed straight to Parquet; data is never fully
  held in memory. Tune with `CHUNKSIZE`.
- **One consolidated Parquet file** for both HPTech and GPOS transactions (a `source` column
  tags each row's origin).
- **Show both queries** (rendered SQL is printed) **and a data sample of each**.
- **Show a sample of the consolidated file** — a few rows from both HPTech and GPOS.
- **Metadata / quality checks**: record counts (total + per source), per-column null & blank
  counts, numeric zero counts, date-range coverage, on-disk size, fully-empty-column warning.
- Analysis is done afterwards on the consolidated file.

## Connection & configuration

- Connection is via **pencilbox** to the Trino warehouse:
  ```python
  import pencilbox as pb
  connection = pb.get_connection("[Warehouse] Trino")
  ```
- Queries are the Redash SQL, with `{{...}}` placeholders substituted by the `render()` helper
  (whitespace-tolerant; errors if any placeholder is left unsubstituted).
- The GPOS query's `%%dummy%%` LIKE pattern is kept **double-escaped** for the pandas DBAPI
  paramstyle — do not collapse it to a single `%`.

> **TODO — user to provide:** any additional connection/config details (credentials handling,
> alternate warehouse names, Redash API vs direct Trino, etc.). Fill in here when supplied.

## Config knobs (top of notebook)

| Variable | Meaning |
|----------|---------|
| `GRN_FROM`, `GRN_TO` | HPTech GRN-completed window |
| `START_DATE`, `END_DATE` | GPOS updated_at / grn_date window |
| `OUT_DIR`, `OUT_FILE`, `CSV_FILE` | Output locations |
| `CHUNKSIZE` | Rows per fetch (lower if memory-tight) |
| `WRITE_CSV`, `CSV_MAX_ROWS` | Toggle / cap CSV export (CSV is skipped above the cap) |

## Consolidated schema notes

The two queries return **different** columns, reconciled into one pinned target schema
(`TARGET_SCHEMA` / `COLUMN_ORDER` in the notebook):

- GPOS-only: `vr_id`, `approval_timestamp`
- HPTech-only: `grn_number`
- Type reconciliation: HPTech emits `asn_quantity` / a few fields as empty strings → coerced
  to numeric null; identifier columns kept as strings; measures as `float64`; dates/timestamps
  parsed.
- HPTech's query repeats `grn_quantity`; duplicate columns are de-duplicated (first kept).

If either query's `SELECT` columns change, update `COLUMN_ORDER` and the
`STRING_COLS` / `NUM_COLS` / `DATE_COLS` / `TS_COLS` lists accordingly.

## Dashboard (`build_dashboard.py`)

Self-contained offline HTML dashboard generated from the consolidated parquet
(inline SVG, no external JS/CSS — respects the no-external-content policy). Notebook
section 12 calls `build(OUT_FILE, DASH_FILE)`. Sections:

- **PO value vs Invoice / GRN / DN / Net payable** (value flow).
- **Payment status by invoice**: Paid–clean, Paid–qty variance, Due–clean (release),
  Due–open concern.
- **Quantity discrepancies**: lines where `invoice_qty ≠ grn_qty + dn_qty`.
- **Payments due**: clean (release) vs held (open concern).
- **By city / by vendor** summaries.

### Reconciliation definitions (agreed)
- PO value = `po_quantity × po_landing_price`
- Invoice value = `invoice_quantity × invoice_landing_price`
- GRN value = `net_amount` (`grn_quantity × grn_landing_price`)
- DN value = `dn_quantity × invoice_landing_price`
- Net payable = Invoice value − DN value
- Quantity variance = `invoice_quantity − (grn_quantity + dn_quantity)`; non-zero = discrepancy
- **Open concern** = an unresolved quantity variance (GRN+DN ≠ invoice). A DN alone is a
  *resolution* of short supply, NOT an open concern.
- **Paid** = a payment amount / UTR record exists for the invoice.

### Known caveats baked into the dashboard banner
- `total_payment_value` is **batch-level** (constant per invoice, repeats across line
  items, ~8× net). Do not sum across rows or treat as an invoice settlement balance.
- GPOS rows currently absent — dashboard reflects HPTech only until the GPOS query returns
  data. It auto-adapts (shows both sources) once GPOS lands.

## Git

- Branch: `claude/redash-data-consolidation-5mp9wu`
- Commit and push work to that branch. No PR unless explicitly requested.

## Change log

- 2026-07-06 — Initial notebook + this instructions file (two-query → one consolidated
  Parquet, previews, consolidated sample, quality report).
- 2026-07-06 — `OUT_DIR` now auto-selects a writable dir (`~/amul_recon`, else
  `./amul_recon`); `/home/Documents` was read-only in the runtime and raised
  `PermissionError`. Override `OUT_DIR` in the CONFIG cell for a custom path.
- 2026-07-06 — Added `build_dashboard.py` + notebook section 12: offline HTML
  reconciliation dashboard (PO vs payments/DN, GRN+DN≠invoice discrepancies,
  payments due vs held). Analysis of the June-2025 HPTech extract: PO ₹143.2 Cr,
  Invoice ₹104.2 Cr, GRN ₹105.4 Cr, DN ₹0.66 Cr; 83 discrepant lines (all
  excess-received); 10 invoices with no payment recorded.
