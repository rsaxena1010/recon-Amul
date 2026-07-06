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

## Git

- Branch: `claude/redash-data-consolidation-5mp9wu`
- Commit and push work to that branch. No PR unless explicitly requested.

## Change log

- 2026-07-06 — Initial notebook + this instructions file (two-query → one consolidated
  Parquet, previews, consolidated sample, quality report).
- 2026-07-06 — `OUT_DIR` now auto-selects a writable dir (`~/amul_recon`, else
  `./amul_recon`); `/home/Documents` was read-only in the runtime and raised
  `PermissionError`. Override `OUT_DIR` in the CONFIG cell for a custom path.
