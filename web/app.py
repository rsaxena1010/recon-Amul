#!/usr/bin/env python3
"""
Amul Reconciliation web app — pure Python standard library (no third-party deps),
so the container image needs zero `pip install` (the build cluster's package proxy
is broken). Upload the consolidated CSV export and get the reconciliation
dashboard; the last upload persists across refreshes until the pod restarts.

Reads CSV (the notebook's amul_invoice_extract.csv). Parquet needs pyarrow, which
can't be installed in this build environment, so CSV is the supported input.
"""
import os
import csv
import io
import cgi
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import render as R

DATA_DIR = os.environ.get("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)
CURRENT = os.path.join(DATA_DIR, "current.csv")
MAX_BYTES = 400 * 1024 * 1024
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

NUM_COLS = ("po_quantity", "po_landing_price", "invoice_quantity",
            "invoice_landing_price", "grn_quantity", "grn_landing_price",
            "dn_quantity", "net_amount", "total_payment_value")


# --------------------------------------------------------------- parsing -----
def _f(x):
    if x is None:
        return None
    x = str(x).strip()
    if x == "" or x.lower() in ("nan", "none", "null"):
        return None
    try:
        return float(x)
    except ValueError:
        return None


def _d(x):
    if not x:
        return None
    s = str(x).strip().replace("T", " ")
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    head = s[:10]                       # date portion, drop any time
    try:
        return dt.date.fromisoformat(head)   # handles YYYY-MM-DD
    except ValueError:
        pass
    for fmt in ("%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    return None


def load_rows(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for raw in csv.DictReader(fh):
            r = dict(raw)
            for c in NUM_COLS:
                r[c] = _f(r.get(c))
            r["_grn_date"] = _d(r.get("grn_date"))
            rows.append(r)
    return rows


def _mul(a, b):
    return None if a is None or b is None else a * b


def _sum(vals):
    return sum(v for v in vals if v is not None)


# --------------------------------------------------------------- compute -----
def compute_ctx(rows, as_of, upload_html="", data_note=""):
    for r in rows:
        r["po_value"] = _mul(r.get("po_quantity"), r.get("po_landing_price"))
        r["invoice_value"] = _mul(r.get("invoice_quantity"), r.get("invoice_landing_price"))
        r["grn_value"] = r.get("net_amount")
        r["dn_value"] = _mul(r.get("dn_quantity"), r.get("invoice_landing_price"))
        iq = r.get("invoice_quantity") or 0.0
        gq = r.get("grn_quantity") or 0.0
        dq = r.get("dn_quantity") or 0.0
        r["qty_variance"] = iq - (gq + dq)
        iv = r["invoice_value"] or 0.0
        dv = r["dn_value"] or 0.0
        r["net_payable"] = iv - dv
        r["is_discrepant"] = abs(r["qty_variance"]) > R.TOL

    n_lines = len(rows)
    sources = sorted({(r.get("source") or "").strip() for r in rows if r.get("source")})
    grn_dates = [r["_grn_date"] for r in rows if r["_grn_date"]]
    gmin = min(grn_dates) if grn_dates else None
    gmax = max(grn_dates) if grn_dates else None

    # invoice rollup
    inv = {}
    for r in rows:
        k = r.get("invoice_id")
        it = inv.get(k)
        if it is None:
            it = inv[k] = dict(
                vendor=r.get("vendor_name") or "", city=r.get("city_name") or "",
                po_value=0.0, invoice_value=0.0, grn_value=0.0, dn_value=0.0,
                net_payable=0.0, dn_qty=0.0, qty_var_abs=0.0, disc_lines=0,
                grn_date=None, payment=r.get("total_payment_value"))
        it["po_value"] += r["po_value"] or 0.0
        it["invoice_value"] += r["invoice_value"] or 0.0
        it["grn_value"] += r["grn_value"] or 0.0
        it["dn_value"] += r["dn_value"] or 0.0
        it["net_payable"] += r["net_payable"] or 0.0
        it["dn_qty"] += r.get("dn_quantity") or 0.0
        it["qty_var_abs"] += abs(r["qty_variance"])
        it["disc_lines"] += 1 if r["is_discrepant"] else 0
        if r["_grn_date"] and (it["grn_date"] is None or r["_grn_date"] > it["grn_date"]):
            it["grn_date"] = r["_grn_date"]
        if it["payment"] is None and r.get("total_payment_value") is not None:
            it["payment"] = r.get("total_payment_value")

    for it in inv.values():
        it["has_payment"] = it["payment"] is not None and it["payment"] > 0
        it["has_concern"] = it["qty_var_abs"] > R.TOL
        it["due_date"] = it["grn_date"] + dt.timedelta(days=R.CREDIT_DAYS) if it["grn_date"] else None
        if it["due_date"]:
            it["days_overdue"] = max(0, (as_of - it["due_date"]).days)
            it["overdue"] = (not it["has_payment"]) and as_of > it["due_date"]
        else:
            it["days_overdue"], it["overdue"] = 0, False
        if it["has_payment"]:
            it["bucket"] = "Paid – qty variance" if it["has_concern"] else "Paid – clean"
            it["aging"] = "Paid"
        else:
            it["bucket"] = "Due – open concern" if it["has_concern"] else "Due – clean (release)"
            it["aging"] = R.aging_bucket(it["days_overdue"])

    invs = list(inv.values())
    n_inv = len(invs)

    bucket_counts = {b: 0 for b in R.BUCKET_ORDER}
    bucket_vals = {b: 0.0 for b in R.BUCKET_ORDER}
    for it in invs:
        bucket_counts[it["bucket"]] += 1
        bucket_vals[it["bucket"]] += it["net_payable"]

    due = [it for it in invs if not it["has_payment"]]
    aging_counts = {b: 0 for b in R.AGING_ORDER}
    aging_vals = {b: 0.0 for b in R.AGING_ORDER}
    for it in due:
        aging_counts[it["aging"]] += 1
        aging_vals[it["aging"]] += it["net_payable"]

    # discrepancy rows (top 30 by variance value)
    disc = [r for r in rows if r["is_discrepant"]]
    for r in disc:
        short = r["qty_variance"] > 0
        price = r.get("invoice_landing_price") if short else r.get("grn_landing_price")
        r["_var_value"] = abs(r["qty_variance"]) * (price or 0.0)
    disc.sort(key=lambda r: r["_var_value"], reverse=True)
    disc_rows = [[
        R.esc(r.get("invoice_id")), R.esc(r.get("po_number")), R.esc(r.get("city_name")),
        R.esc((r.get("item_name") or "")[:36]),
        R.num(r.get("invoice_quantity")), R.num(r.get("grn_quantity")), R.num(r.get("dn_quantity")),
        f'{r["qty_variance"]:,.0f}', R.tag_type(r["qty_variance"] > 0), R.rupees(r["_var_value"]),
    ] for r in disc[:30]]

    def due_rows(items):
        items = sorted(items, key=lambda it: (it["overdue"], it["net_payable"]), reverse=True)
        return [[R.esc(k_by_val(inv, it)), R.esc(it["vendor"]), R.esc(it["city"]),
                 R.cr(it["net_payable"]), _fmt_date(it["due_date"]),
                 R.tag_overdue(it["overdue"], it["days_overdue"])] for it in items[:30]]

    due_clean_rows = due_rows([it for it in due if not it["has_concern"]])
    due_concern_rows = due_rows([it for it in due if it["has_concern"]])

    # by city / vendor
    city_rows = _group_rows(rows, "city_name",
                            ["po_value", "invoice_value", "grn_value", "dn_value", "net_payable"])
    vendor_rows = _vendor_rows(rows)

    return dict(
        sources=", ".join(sources), date_range=f"{_fmt_date(gmin)} – {_fmt_date(gmax)}",
        n_inv=n_inv, n_lines=n_lines, as_of_str=as_of.strftime("%Y-%m-%d"),
        data_note=data_note, upload_html=upload_html,
        gpos_absent=("GPOS" not in sources),
        po_v=_sum(r["po_value"] for r in rows), inv_v=_sum(r["invoice_value"] for r in rows),
        grn_v=_sum(r["grn_value"] for r in rows), dn_v=_sum(r["dn_value"] for r in rows),
        npay_v=_sum(r["net_payable"] for r in rows),
        pay_v=_sum(it["payment"] for it in invs if it["has_payment"]),
        n_dn_lines=sum(1 for r in rows if (r.get("dn_quantity") or 0) > R.TOL),
        bucket_counts=bucket_counts, bucket_vals=bucket_vals,
        aging_counts=aging_counts, aging_vals=aging_vals,
        n_disc=len(disc),
        short_u=_sum(r["qty_variance"] for r in rows if r["qty_variance"] > R.TOL),
        excess_u=-_sum(r["qty_variance"] for r in rows if r["qty_variance"] < -R.TOL),
        disc_rows=disc_rows,
        n_due=len(due), n_overdue=sum(1 for it in due if it["overdue"]),
        overdue_val=_sum(it["net_payable"] for it in due if it["overdue"]),
        n_clean=sum(1 for it in due if not it["has_concern"]),
        n_concern=sum(1 for it in due if it["has_concern"]),
        due_clean_rows=due_clean_rows, due_concern_rows=due_concern_rows,
        city_rows=city_rows, vendor_rows=vendor_rows,
    )


def k_by_val(inv, target):
    for k, v in inv.items():
        if v is target:
            return k
    return ""


def _fmt_date(d):
    return "—" if d is None else d.strftime("%d %b %Y")


def _group_rows(rows, key, valcols, top=20):
    agg = {}
    for r in rows:
        k = r.get(key) or ""
        a = agg.setdefault(k, dict.fromkeys(valcols, 0.0))
        a.setdefault("_disc", 0)
        for c in valcols:
            a[c] += r.get(c) or 0.0
        a["_disc"] += 1 if r["is_discrepant"] else 0
    items = sorted(agg.items(), key=lambda kv: kv[1]["net_payable"], reverse=True)[:top]
    return [[R.esc(k), R.cr(a["po_value"]), R.cr(a["invoice_value"]), R.cr(a["grn_value"]),
             R.cr(a["dn_value"]), R.cr(a["net_payable"]), f'{a["_disc"]}'] for k, a in items]


def _vendor_rows(rows, top=20):
    agg = {}
    for r in rows:
        k = r.get("vendor_name") or ""
        a = agg.setdefault(k, dict(net=0.0, dn=0.0, disc=0, invs=set()))
        a["net"] += r.get("net_payable") or 0.0
        a["dn"] += r.get("dn_value") or 0.0
        a["disc"] += 1 if r["is_discrepant"] else 0
        a["invs"].add(r.get("invoice_id"))
    items = sorted(agg.items(), key=lambda kv: kv[1]["net"], reverse=True)[:top]
    return [[R.esc(k[:52]), f'{len(a["invs"])}', R.cr(a["net"]), R.cr(a["dn"]), f'{a["disc"]}']
            for k, a in items]


# --------------------------------------------------------------- server ------
def _upload_bar(loaded, as_of_str):
    cur = (f'<span style="color:var(--muted)">Loaded: <b style="color:var(--ink)">{R.esc(loaded)}</b></span>'
           if loaded else '<span style="color:var(--muted)">No file loaded yet.</span>')
    dl = '<a class="btn ghost" href="/download">Download source</a>' if loaded else ""
    return (f'<div class="uploadbar">'
            f'<form id="upForm" method="post" action="/upload" enctype="multipart/form-data">'
            f'<input id="upFile" type="file" name="file" accept=".csv,.txt,.parquet,.pq" required>'
            f'<input id="upAsOf" type="date" name="as_of" value="{as_of_str}" title="As-of date for overdue calc">'
            f'<button class="btn" type="submit">Upload &amp; analyse</button></form>'
            f'<form method="get" action="/"><input type="hidden" name="_" value="1">'
            f'<button class="btn ghost" type="submit">Refresh</button></form>{dl}{cur}'
            f'<span id="upMsg" style="color:var(--muted)"></span></div>'
            f'{_CONVERT_JS}')


# Browser-side: if a .parquet file is chosen, parse it (pure-JS hyparquet) and
# convert to CSV before upload — the server is pure-stdlib and can't read parquet.
# The reader is loaded from the self-hosted bundle if present (/static/parquet.min.js,
# baked in by a local CLI build), else from the jsdelivr CDN. CSV uploads unchanged.
_CONVERT_JS = """
<script>
(function(){
  var form=document.getElementById('upForm'); if(!form) return;
  var fileEl=document.getElementById('upFile'), msg=document.getElementById('upMsg');
  function toCSV(rows){
    if(!rows.length) return '';
    var cols=Object.keys(rows[0]);
    function esc(v){
      if(v===null||v===undefined) return '';
      if(v instanceof Date) v=v.toISOString();
      else if(typeof v==='bigint') v=v.toString();
      else v=String(v);
      return /[",\\n]/.test(v) ? '"'+v.replace(/"/g,'""')+'"' : v;
    }
    var out=[cols.join(',')];
    for(var i=0;i<rows.length;i++){var r=rows[i],line=[];for(var j=0;j<cols.length;j++)line.push(esc(r[cols[j]]));out.push(line.join(','));}
    return out.join('\\n');
  }
  function loadScript(src){return new Promise(function(res,rej){var s=document.createElement('script');s.src=src;s.onload=res;s.onerror=function(){rej(new Error('load failed'));};document.head.appendChild(s);});}
  function getReader(){
    if(window.HyParquet&&window.HyParquet.parquetReadObjects) return Promise.resolve(window.HyParquet.parquetReadObjects);
    return loadScript('/static/parquet.min.js').then(function(){
      if(window.HyParquet&&window.HyParquet.parquetReadObjects) return window.HyParquet.parquetReadObjects;
      throw new Error('no global');
    }).catch(function(){
      return import('https://cdn.jsdelivr.net/npm/hyparquet@1.26.2/+esm').then(function(m){return m.parquetReadObjects;});
    });
  }
  form.addEventListener('submit', function(ev){
    var f=fileEl.files[0]; if(!f) return;
    var name=f.name.toLowerCase();
    if(!(name.endsWith('.parquet')||name.endsWith('.pq'))) return;  // CSV: normal submit
    ev.preventDefault();
    msg.textContent='Loading parquet reader…';
    var reader;
    getReader().then(function(fn){reader=fn;msg.textContent='Converting parquet…';return f.arrayBuffer();})
      .then(function(ab){return reader({file:ab});})
      .then(function(rows){
        var csv=toCSV(rows);
        var fd=new FormData();
        fd.append('file', new Blob([csv],{type:'text/csv'}), 'converted.csv');
        fd.append('as_of', document.getElementById('upAsOf').value||'');
        msg.textContent='Uploading '+rows.length.toLocaleString()+' rows…';
        return fetch('/upload',{method:'POST',body:fd});
      })
      .then(function(){window.location.assign('/?as_of='+encodeURIComponent(document.getElementById('upAsOf').value||''));})
      .catch(function(e){msg.textContent='Parquet error: '+e.message+' — try uploading the CSV export instead.';});
  });
})();
</script>
"""


def _landing(bar):
    return R.shell("Amul Reconciliation Dashboard", f"""
<h1>Amul Reconciliation Dashboard</h1>
<p class="sub">Upload the consolidated <b>CSV or Parquet</b> file to see the analysis: PO vs
 payments &amp; DNs, GRN+DN≠invoice discrepancies, payments due, and payables aging on
 Amul's <b>{R.CREDIT_DAYS}-day</b> credit term.</p>
{bar}
<div class="banner"><b>Expected file</b><ul>
 <li>The notebook's consolidated output — <code>amul_invoice_extract.parquet</code> or
  <code>.csv</code>. Columns: <code>source, invoice_id, po_number, grn_date,
  invoice_quantity, grn_quantity, dn_quantity, invoice_landing_price, grn_landing_price,
  net_amount, total_payment_value, vendor_name, city_name…</code></li>
 <li>Parquet is parsed in your browser and converted to CSV before upload (the server is
  pure-stdlib). The upload persists across refreshes until the app restarts.</li>
</ul></div>""")


def _err(msg):
    return R.shell("Error", f'<h1>Could not render</h1>'
                   f'<div class="banner"><b>The uploaded file could not be analysed.</b>'
                   f'<p class="hint">Check it is the CSV export with the expected columns.</p></div>'
                   f'<pre style="white-space:pre-wrap;font-size:12px;background:var(--surface);'
                   f'border:1px solid var(--border);border-radius:10px;padding:14px;overflow:auto">'
                   f'{R.esc(msg)}</pre><p><a class="btn" href="/">Back</a></p>')


def _as_of(qs):
    raw = (qs.get("as_of", [""])[0] or "").strip()
    if raw:
        try:
            return dt.date.fromisoformat(raw[:10])
        except ValueError:
            pass
    return dt.date.today()


class Handler(BaseHTTPRequestHandler):
    server_version = "amul-recon/1.0"

    def _send(self, body, status=200, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path == "/health":
            return self._send('{"ok": true}', ctype="application/json")
        if u.path == "/static/parquet.min.js":
            fp = os.path.join(STATIC_DIR, "parquet.min.js")
            if os.path.exists(fp):
                with open(fp, "rb") as fh:
                    data = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return self._send("Not found", status=404, ctype="text/plain")
        if u.path == "/download":
            if os.path.exists(CURRENT):
                with open(CURRENT, "rb") as fh:
                    data = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.send_header("Content-Disposition",
                                 "attachment; filename=amul_invoice_extract.csv")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return self._send("", status=302, ctype="text/plain")
        if u.path != "/":
            return self._send("Not found", status=404, ctype="text/plain")

        as_of = _as_of(qs)
        as_of_str = as_of.strftime("%Y-%m-%d")
        if not os.path.exists(CURRENT):
            return self._send(_landing(_upload_bar(None, as_of_str)))
        try:
            rows = load_rows(CURRENT)
            ctx = compute_ctx(rows, as_of, upload_html=_upload_bar("current.csv", as_of_str),
                              data_note="file: current.csv")
            return self._send(R.assemble(ctx))
        except Exception as e:  # noqa: BLE001
            import traceback
            return self._send(_err(traceback.format_exc() or str(e)), status=500)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/upload":
            return self._send("Not found", status=404, ctype="text/plain")
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BYTES:
            return self._send(_err("File too large."), status=413)
        ctype = self.headers.get("Content-Type", "")
        env = {"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype,
               "CONTENT_LENGTH": str(length)}
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=env,
                                keep_blank_values=True)
        as_of = form.getvalue("as_of", "")
        if "file" not in form:
            return self._send("", status=302)
        item = form["file"]
        fname = getattr(item, "filename", "") or ""
        ext = os.path.splitext(fname)[1].lower()
        if ext not in (".csv", ".txt"):
            return self._send(_err(
                f"Received a '{ext}' file directly. Parquet is converted to CSV in the "
                f"browser before upload — enable JavaScript, or upload the CSV export."),
                status=400)
        with open(CURRENT, "wb") as out:
            out.write(item.file.read())
        self.send_response(303)
        self.send_header("Location", f"/?as_of={as_of}" if as_of else "/")
        self.end_headers()

    def log_message(self, *a):  # quieter logs
        pass


def main():
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
