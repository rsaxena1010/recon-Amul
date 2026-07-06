#!/usr/bin/env python3
"""
Amul Reconciliation web app.

Upload a consolidated extract (parquet or csv, in the same format the notebook
produces), and get the reconciliation dashboard. The last uploaded file is kept
so the analysis persists across refreshes (until the pod restarts). Change the
"as of" date to drive the credit-day / overdue calculation.
"""
import os
import traceback
import pandas as pd
from flask import Flask, request, redirect, send_file, Response

import dashboard_core as core

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024  # 300 MB uploads

DATA_DIR = os.environ.get("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)
ALLOWED = {".parquet", ".pq", ".csv", ".txt"}


def _current_file():
    if not os.path.isdir(DATA_DIR):
        return None
    files = [os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR)
             if os.path.splitext(f)[1].lower() in ALLOWED and not f.startswith(".")]
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def _upload_bar(current_name, as_of_str):
    cur = (f'<span style="color:var(--muted)">Loaded: <b style="color:var(--ink)">'
           f'{core.esc(current_name)}</b></span>' if current_name
           else '<span style="color:var(--muted)">No file loaded yet.</span>')
    dl = ('<a class="btn ghost" href="/download">Download source</a>' if current_name else '')
    return f"""
<div class="uploadbar">
  <form method="post" action="/upload" enctype="multipart/form-data">
    <input type="file" name="file" accept=".parquet,.pq,.csv,.txt" required>
    <input type="date" name="as_of" value="{as_of_str}" title="As-of date for overdue calc">
    <button class="btn" type="submit">Upload &amp; analyse</button>
  </form>
  <form method="get" action="/">
    <input type="hidden" name="_" value="1">
    <button class="btn ghost" type="submit">Refresh</button>
  </form>
  {dl}
  {cur}
</div>"""


def _as_of(default=None):
    raw = request.values.get("as_of", "").strip()
    if raw:
        try:
            return pd.Timestamp(raw).normalize()
        except Exception:
            pass
    return default or pd.Timestamp.today().normalize()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    path = _current_file()
    as_of = _as_of()
    as_of_str = as_of.strftime("%Y-%m-%d")
    if not path:
        bar = _upload_bar(None, as_of_str)
        return Response(_landing(bar), mimetype="text/html")
    try:
        df = core.read_any(path)
        bar = _upload_bar(os.path.basename(path), as_of_str)
        html = core.render_page(df, as_of, upload_html=bar,
                                data_note=f"file: {os.path.basename(path)}")
        return Response(html, mimetype="text/html")
    except Exception:
        return Response(_error(traceback.format_exc()), mimetype="text/html", status=500)


@app.post("/upload")
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return redirect("/")
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED:
        return Response(_error(f"Unsupported file type '{ext}'. "
                               f"Upload parquet or csv."), status=400, mimetype="text/html")
    # keep a single current file per extension family; clear old ones
    for old in (_current_file(),):
        if old and os.path.exists(old):
            try:
                os.remove(old)
            except OSError:
                pass
    dest = os.path.join(DATA_DIR, "current" + ext)
    f.save(dest)
    as_of = request.values.get("as_of", "").strip()
    return redirect(f"/?as_of={as_of}" if as_of else "/")


@app.get("/download")
def download():
    path = _current_file()
    if not path:
        return redirect("/")
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


def _shell(title, body):
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{title}</title><style>{core.CSS.format(c=core.PAL)}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")


def _landing(bar):
    return _shell("Amul Reconciliation Dashboard", f"""
<h1>Amul Reconciliation Dashboard</h1>
<p class="sub">Upload a consolidated extract (parquet or csv) to see the analysis:
 PO vs payments &amp; DNs, GRN+DN≠invoice discrepancies, payments due, and payables
 aging on Amul's <b>{core.CREDIT_DAYS}-day</b> credit term.</p>
{bar}
<div class="banner"><b>Expected file</b><ul>
 <li>The consolidated file from the notebook (<code>amul_invoice_extract.parquet</code>)
  or its CSV — same columns: <code>source, invoice_id, po_number, grn_date,
  invoice_quantity, grn_quantity, dn_quantity, invoice_landing_price,
  grn_landing_price, net_amount, total_payment_value, vendor_name, city_name…</code></li>
 <li>The uploaded file is kept so the dashboard persists across refreshes
  (until the app restarts).</li>
</ul></div>""")


def _error(tb):
    return _shell("Error", f"""
<h1>Could not render</h1>
<div class="banner"><b>The uploaded file could not be analysed.</b>
 <p class="hint">Check it matches the expected columns. Details below.</p></div>
<pre style="white-space:pre-wrap;font-size:12px;background:var(--surface);
 border:1px solid var(--border);border-radius:10px;padding:14px;overflow:auto">{core.esc(tb)}</pre>
<p><a class="btn" href="/">Back</a></p>""")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
