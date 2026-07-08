# Quick Start — Updated Notebook

## Run in This Order

### 1️⃣ CONFIG Cell (always first)
Edit dates, then run:
```python
GRN_FROM   = '2025-04-01'
GRN_TO     = '2025-06-30'
START_DATE = '2025-04-01'
END_DATE   = '2025-06-30'
```

### 2️⃣ Imports Cell
Just run it (no changes needed)

### 3️⃣ Schema Cell
Just run it (no changes needed)

### 4️⃣ HP_SQL Cell
Just run it to see rendered query

### 5️⃣ GPOS_SQL Cell
Just run it to see rendered query

### 6️⃣ **⭐ DATA VALIDATION CELL (Section 5a)** ← **RUN THIS BEFORE PREVIEW**
This is the key new cell. Takes 3-5 minutes and tells you if data exists:

```
✅ All checks passed → proceed to preview
❌ Check failed (0 rows) → adjust dates/filters, don't run full extract
```

### 7️⃣ Preview Cell
Now safe to run. Shows first 5 rows from each source.

### 8️⃣ Main Extract Cell (Section 7)
Runs the full extract with progress per window.

### 9️⃣ CSV Cell (optional)
Skipped automatically if >2M rows

### 🔟 Quality Report
Shows row counts by source, column quality, date coverage

### 1️⃣1️⃣ Load & Analyze
Load parquet and run your analysis

---

## If GPOS Query Fails

**Memory error?**
```python
# Already set to 1 day, but you can reduce CHUNKSIZE:
CHUNKSIZE = 50_000  # was 100_000
```

**0 rows returned?**
- Run data validation cell first (Section 5a)
- If validation checks pass but full query returns 0, likely a vendor_id or date filter issue
- Check the GPOS query vendor list and date filters

**Takes too long?**
- 1-day windows are small; expect 2-3 min per window
- For 91 days: ~2.5-4.5 hours total
- This is OK — batching prevents memory overflow

---

## Expected Runtime

| Phase | Duration | Notes |
|-------|----------|-------|
| Validation (Section 5a) | 3-5 min | Do this first! Fail fast if no data. |
| Preview (Section 6) | 5-10 min | HPTech ~35s, GPOS ~5-10 min on 1-day window |
| Full Extract (Section 7) | 2-4 hours | 91 GPOS windows @ ~2-3 min each + 13 HPTech windows @ ~15-20 min each |
| CSV Export (optional) | 10-20 min | Skipped if >2M rows |
| Quality Report | 5-10 min | Streams file to count nulls/blanks |

**Total: 2.5-5 hours** depending on data volume

---

## Config for 10x Data (10 months instead of 3)

Just change dates in CONFIG cell:
```python
GRN_FROM   = '2025-01-01'  # 10 months
GRN_TO     = '2025-10-31'
START_DATE = '2025-01-01'
END_DATE   = '2025-10-31'

# Keep these the same:
BATCH_DAYS_HPTECH = 14
BATCH_DAYS_GPOS   = 1     # Stays at 1 day per window
```

**Expected:**
- GPOS: ~300 windows instead of 91 → 7-12 hours instead of 2.5-4.5 hours
- Everything else scales linearly
- Memory per window stays the same (same protection)

---

## Key Improvements in This Version

| Issue | Before | After |
|-------|--------|-------|
| GPOS memory overflow | Fails on large windows | ✅ Uses 1-day windows safely |
| Long wait with 0 rows | 10-20 min wait, then fail | ✅ Validation: 3-5 min, fast feedback |
| Vague error messages | "Query exceeded limit" | ✅ "Try reducing BATCH_DAYS_GPOS" |
| Date filter syntax error | ❌ Invalid SQL | ✅ Fixed to proper Trino syntax |
| No progress tracking | Long black screen | ✅ Per-window output with row counts |

---

## If Something Goes Wrong

1. **Data validation failed** → your date range has no matching data, adjust dates
2. **Preview times out** → Trino cluster may be busy, try again later
3. **Memory error during extract** → reduce `BATCH_DAYS_GPOS` or `CHUNKSIZE`
4. **0 rows from GPOS** → vendor_id list may be outdated or date range wrong
5. **Extract interrupted** → re-run from start (no resume; parquet will be recreated)

See `NOTEBOOK_UPDATES.md` for detailed troubleshooting.
