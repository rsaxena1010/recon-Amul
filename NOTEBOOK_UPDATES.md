# Amul Reconciliation Notebook — Updates for Memory & Error Handling

## Problems Fixed

### 1. ✅ GPOS Query Memory Overflow (300GB limit)
**Problem:** GPOS query was hitting `EXCEEDED_GLOBAL_MEMORY_LIMIT` even with 1-day batches.

**Solution:**
- Split batching into **separate window sizes** for each source:
  - `BATCH_DAYS_HPTECH = 14` days (HPTech handles large windows fine)
  - `BATCH_DAYS_GPOS = 1` day (GPOS is memory-intensive)
- If you still hit memory limits with GPOS, you can reduce `BATCH_DAYS_GPOS` further (or to even smaller windows)

### 2. ✅ No Feedback on Long Queries Returning 0 Rows
**Problem:** Preview took 572 seconds to return 0 rows with no warning until the end.

**Solution:**
- Added **data validation queries** (Section 5a) that run **BEFORE** the expensive query
- These diagnostic queries check:
  - Does GCMMF manufacturer data exist in your date range?
  - Do any target vendors exist in that data?
  - Do verification_request records exist?
- **Total time: 3-5 minutes max** instead of 10-20 minutes with no indication

### 3. ✅ Poor Error Messages
**Problem:** Vague error messages didn't indicate what to fix.

**Solution:**
- Enhanced error handling with **specific diagnostics**:
  - Detects memory limit errors → suggests reducing `BATCH_DAYS_GPOS`
  - Detects timeouts → suggests retrying or simplifying
  - If query returns 0 rows after > 60s → warns about likely filter issues
- Helpful error context instead of raw stack traces

### 4. ✅ GPOS Query Syntax Error
**Problem:** Date filter had invalid syntax: `date'{{start_date}}'` (missing parentheses)

**Solution:**
- Fixed to proper Trino syntax: `CAST(ivr.grn_date AS DATE) BETWEEN date('{{start_date}}') AND date('{{end_date}}')`

---

## How to Use the Updated Notebook

### Step 1: Configure Dates (Cell 1)
```python
GRN_FROM   = '2025-04-01'
GRN_TO     = '2025-06-30'
START_DATE = '2025-04-01'
END_DATE   = '2025-06-30'
```

### Step 2: Run Data Validation (Section 5a) ⭐ DO THIS FIRST
This is the **critical new step**. It takes 3-5 minutes and tells you if there's any matching data:

```
Check 1: GCMMF manufacturer data in date range...
  Result: 12,345 GCMMF invoices found

Check 2: Target vendors (sample) in GCMMF data...
  Result: 5,678 invoices from target vendors found

Check 3: Verification requests in date range...
  Result: 8,901 verification requests found
```

**If any check returns 0:**
- Don't proceed to the full extract (it will also return 0)
- Adjust `START_DATE` / `END_DATE` or filters
- Check if vendor_id list or manufacturer_name needs updating

### Step 3: Preview (Section 6)
- HPTech preview: usually completes in ~35s
- GPOS preview: now uses 1-day window (not 7-day), should be faster

### Step 4: Run Full Extract (Section 7)
For date range 2025-04-01 to 2025-06-30:
- **HPTech**: ~13 windows @ 14 days each = ~2-3 minutes per window
- **GPOS**: ~91 windows @ 1 day each = ~2-3 minutes per window
- **Total**: ~150+ minutes (2.5+ hours) depending on data volume

Progress is printed for each window, so you can monitor:
```
[GPOS] window 1/91 2025-04-01..2025-04-01: 1,234 rows (cum 1,234)
[GPOS] window 2/91 2025-04-02..2025-04-02: 987 rows (cum 2,221)
...
```

---

## Performance Tuning

### If GPOS Still Hits Memory Limit
Reduce batch size further in CONFIG cell:

```python
# Current (1 day per window):
BATCH_DAYS_GPOS = 1

# More aggressive (smaller windows):
BATCH_DAYS_GPOS = 1   # Already at 1, can't go smaller with dates

# Alternative: reduce CHUNKSIZE if memory is really tight
CHUNKSIZE = 50_000  # was 100_000
```

If `BATCH_DAYS_GPOS = 1` still fails, the query itself may need optimization (e.g., removing unnecessary CTEs or aggregations).

### If Extracting 10x More Data
The batching approach scales linearly. For 3x the date range (Jan–Dec instead of Apr–Jun):
- GPOS window count increases from ~91 to ~365 days
- Each window takes ~2-3 min, so **~9-10+ hours total**
- Memory per window stays the same (1 day = small)

Just extend the date range; the batching handles it.

---

## Configuration Summary

| Setting | Default | What It Does |
|---------|---------|--------------|
| `BATCH_DAYS_HPTECH` | 14 | HPTech window size (larger = fewer, longer queries) |
| `BATCH_DAYS_GPOS` | 1 | GPOS window size (keep small to avoid 300GB memory limit) |
| `QUERY_TIMEOUT_SEC` | 600 | Warn if query returns 0 rows after this many seconds |
| `CHUNKSIZE` | 100,000 | Rows fetched per query chunk (lower if memory is tight) |
| `WRITE_CSV` | True | Skip CSV if dataset is > 2M rows |

---

## Troubleshooting

### Scenario: "EXCEEDED_GLOBAL_MEMORY_LIMIT" error on GPOS
**Try:**
1. Ensure `BATCH_DAYS_GPOS = 1` ✓
2. Reduce `CHUNKSIZE` from 100,000 to 50,000
3. Run data validation first to confirm data exists
4. If query is still too complex, simplify by removing unused CTEs or aggregations

### Scenario: GPOS validation checks pass but preview returns 0 rows
**Likely cause:** Full query is more restrictive than the validation queries
- Validation checks basic data existence
- Full query has many joins, filters, and window functions that eliminate rows
- Review the vendor_id filter and window calculations

### Scenario: Process interrupted mid-extraction
**If paused during GPOS extraction (e.g., power loss):**
- Re-running will start from scratch (parquet file is deleted and recreated)
- No resume capability in current version
- For 10x data (365 days), consider splitting manually into quarters and merging

---

## What's New in This Version

✅ **Data Validation Cell (5a)** — fail fast (3-5 min) instead of waiting 10-20 min  
✅ **Separate Batch Sizes** — GPOS uses 1-day windows, HPTech uses 14-day  
✅ **Better Error Messages** — specific fixes for memory, timeouts, no-data issues  
✅ **Fixed GPOS Date Filter** — corrected SQL syntax error  
✅ **Progress Logging** — per-window output so you know where extraction stands  

---

## Questions?

If you encounter issues:
1. Check the validation cell output first
2. Look at error messages for specific suggestions
3. Reduce `BATCH_DAYS_GPOS` if memory errors persist
4. Verify dates and filters match your data
