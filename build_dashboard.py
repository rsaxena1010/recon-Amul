#!/usr/bin/env python3
"""
Build a self-contained (offline, no external JS/CSS) HTML reconciliation
dashboard from the consolidated Amul invoice parquet.

Usage:
    python build_dashboard.py [INPUT_PARQUET] [OUTPUT_HTML]

Defaults:
    INPUT_PARQUET = ~/amul_recon/amul_invoice_extract.parquet
    OUTPUT_HTML   = ~/amul_recon/amul_recon_dashboard.html

Design: palette + mark rules follow the data-viz method (validated categorical
+ status colors). Charts are inline SVG so the file opens anywhere with no
network access.
"""
import os
import sys
import html
import pandas as pd
import numpy as np

TOL = 0.001  # float tolerance for quantity comparisons

# ---------------------------------------------------------------- palette ----
# Validated reference palette (light / dark handled via CSS custom props).
PAL = dict(
    surface="#fcfcfb", page="#f9f9f7", ink="#0b0b0b", ink2="#52514e",
    muted="#898781", grid="#e1e0d9", axis="#c3c2b7",
    blue="#2a78d6", aqua="#1baf7a", yellow="#eda100", violet="#4a3aa7",
    good="#0ca30c", warning="#fab219", serious="#ec835a", critical="#d03b3b",
)

# ------------------------------------------------------------- formatting ----
def cr(x):
    """Format a rupee amount in crore."""
    if pd.isna(x):
        return "—"
    return f"₹{x/1e7:,.2f} Cr"

def rupees(x):
    if pd.isna(x):
        return "—"
    return f"₹{x:,.0f}"

def num(x):
    if pd.isna(x):
        return "—"
    return f"{x:,.0f}"

def esc(x):
    return html.escape("" if x is None else str(x))


# --------------------------------------------------------------- metrics -----
def load_and_enrich(path):
    df = pd.read_parquet(path)
    df["po_value"]      = df["po_quantity"]      * df["po_landing_price"]
    df["invoice_value"] = df["invoice_quantity"] * df["invoice_landing_price"]
    df["grn_value"]     = df["net_amount"]                    # grn_qty * grn_landing_price
    df["dn_value"]      = df["dn_quantity"]      * df["invoice_landing_price"]
    df["inward_qty"]    = df["grn_quantity"]     + df["dn_quantity"]
    df["qty_variance"]  = df["invoice_quantity"] - df["inward_qty"]   # +ve = short, -ve = excess
    df["net_payable"]   = df["invoice_value"]    - df["dn_value"]
    df["is_discrepant"] = df["qty_variance"].abs() > TOL
    return df


def invoice_rollup(df):
    inv = df.groupby("invoice_id").agg(
        source=("source", "first"),
        entity=("entity_name", "first"),
        vendor=("vendor_name", "first"),
        city=("city_name", "first"),
        po_value=("po_value", "sum"),
        invoice_value=("invoice_value", "sum"),
        grn_value=("grn_value", "sum"),
        dn_value=("dn_value", "sum"),
        net_payable=("net_payable", "sum"),
        dn_qty=("dn_quantity", "sum"),
        qty_var_abs=("qty_variance", lambda s: s.abs().sum()),
        disc_lines=("is_discrepant", "sum"),
        payment=("total_payment_value", "first"),
        utr=("utr_numbers", "first"),
        lines=("invoice_id", "size"),
    ).reset_index()

    inv["has_payment"] = inv["payment"].notna() & (inv["payment"] > 0)
    # "concern" = an unresolved quantity mismatch (GRN + DN != invoice qty).
    # A DN on its own is a resolution of short supply, not an open concern.
    inv["has_concern"] = inv["qty_var_abs"] > TOL

    def bucket(r):
        if r["has_payment"]:
            return "Paid – qty variance" if r["has_concern"] else "Paid – clean"
        return "Due – open concern" if r["has_concern"] else "Due – clean (release)"

    inv["bucket"] = inv.apply(bucket, axis=1)
    return inv


BUCKET_ORDER = ["Paid – clean", "Paid – qty variance",
                "Due – clean (release)", "Due – open concern"]
BUCKET_COLOR = {
    "Paid – clean": PAL["good"],
    "Paid – qty variance": PAL["warning"],
    "Due – clean (release)": PAL["blue"],
    "Due – open concern": PAL["critical"],
}
BUCKET_NOTE = {
    "Paid – clean": "Received matches invoice and payment recorded.",
    "Paid – qty variance": "Payment recorded but GRN+DN ≠ invoice — review for recovery.",
    "Due – clean (release)": "No open concern — payment can be released.",
    "Due – open concern": "Payment withheld pending quantity resolution.",
}


# ------------------------------------------------------------ svg helpers ----
def hbar_chart(rows, unit="cr", width=680, bar_h=30, gap=14, pad_l=150, pad_r=120):
    """rows = [(label, value, color)]. Horizontal bars, direct value labels."""
    vals = [v for _, v, _ in rows]
    vmax = max(vals + [1])
    n = len(rows)
    h = n * (bar_h + gap) + gap
    plot_w = width - pad_l - pad_r
    out = [f'<svg viewBox="0 0 {width} {h}" width="100%" role="img" class="chart">']
    for i, (label, val, color) in enumerate(rows):
        y = gap + i * (bar_h + gap)
        bw = max(2, plot_w * (val / vmax))
        disp = cr(val) if unit == "cr" else num(val)
        out.append(
            f'<text x="{pad_l-10}" y="{y+bar_h/2+4}" text-anchor="end" '
            f'class="lbl">{esc(label)}</text>')
        out.append(
            f'<rect x="{pad_l}" y="{y}" width="{bw:.1f}" height="{bar_h}" '
            f'rx="4" fill="{color}"><title>{esc(label)}: {disp}</title></rect>')
        out.append(
            f'<text x="{pad_l+bw+8:.1f}" y="{y+bar_h/2+4}" class="val">{disp}</text>')
    out.append("</svg>")
    return "".join(out)


def donut(seg, size=200, thick=34):
    """seg = [(label, value, color)]. Returns svg + legend html."""
    total = sum(v for _, v, _ in seg) or 1
    r = size / 2
    inner = r - thick
    cx = cy = r
    out = [f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
           f'role="img" class="donut">']
    ang = -90.0
    for label, val, color in seg:
        frac = val / total
        sweep = frac * 360
        a0 = np.radians(ang)
        a1 = np.radians(ang + sweep)
        large = 1 if sweep > 180 else 0
        x0, y0 = cx + r*np.cos(a0), cy + r*np.sin(a0)
        x1, y1 = cx + r*np.cos(a1), cy + r*np.sin(a1)
        xi0, yi0 = cx + inner*np.cos(a1), cy + inner*np.sin(a1)
        xi1, yi1 = cx + inner*np.cos(a0), cy + inner*np.sin(a0)
        if val > 0:
            out.append(
                f'<path d="M{x0:.2f},{y0:.2f} A{r},{r} 0 {large} 1 {x1:.2f},{y1:.2f} '
                f'L{xi0:.2f},{yi0:.2f} A{inner},{inner} 0 {large} 0 {xi1:.2f},{yi1:.2f} Z" '
                f'fill="{color}" stroke="var(--surface)" stroke-width="2">'
                f'<title>{esc(label)}: {int(val)} ({frac*100:.0f}%)</title></path>')
        ang += sweep
    out.append(f'<text x="{cx}" y="{cy-4}" text-anchor="middle" class="donut-num">'
               f'{int(total)}</text>')
    out.append(f'<text x="{cx}" y="{cy+16}" text-anchor="middle" class="donut-cap">'
               f'invoices</text>')
    out.append("</svg>")
    return "".join(out)


def legend(items):
    li = "".join(
        f'<div class="leg-row"><span class="sw" style="background:{c}"></span>'
        f'<span class="leg-lbl">{esc(l)}</span><span class="leg-val">{v}</span></div>'
        for l, v, c in items)
    return f'<div class="legend">{li}</div>'


def table(headers, rows, aligns=None, cls=""):
    aligns = aligns or ["left"] * len(headers)
    th = "".join(f'<th style="text-align:{a}">{esc(h)}</th>'
                 for h, a in zip(headers, aligns))
    trs = []
    for r in rows:
        tds = "".join(f'<td style="text-align:{a}">{c}</td>'
                      for c, a in zip(r, aligns))
        trs.append(f"<tr>{tds}</tr>")
    return (f'<table class="{cls}"><thead><tr>{th}</tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table>')


def kpi(label, value, sub="", tone=""):
    return (f'<div class="kpi {tone}"><div class="kpi-lbl">{esc(label)}</div>'
            f'<div class="kpi-val">{value}</div>'
            f'<div class="kpi-sub">{esc(sub)}</div></div>')


# ------------------------------------------------------------------ build ----
def build(path, out_html):
    df = load_and_enrich(path)
    inv = invoice_rollup(df)

    n_lines = len(df)
    n_inv = len(inv)
    sources = ", ".join(sorted(df["source"].unique()))
    gmin = pd.to_datetime(df["grn_date"]).min()
    gmax = pd.to_datetime(df["grn_date"]).max()
    date_range = f"{gmin:%d %b %Y} – {gmax:%d %b %Y}"

    po_v   = df["po_value"].sum()
    inv_v  = df["invoice_value"].sum()
    grn_v  = df["grn_value"].sum()
    dn_v   = df["dn_value"].sum()
    npay_v = df["net_payable"].sum()
    pay_v_inv = inv.loc[inv["has_payment"], "payment"].sum()  # invoice-level, batch caveat

    disc = df[df["is_discrepant"]].copy()
    short_u = df.loc[df.qty_variance > TOL, "qty_variance"].sum()
    excess_u = (-df.loc[df.qty_variance < -TOL, "qty_variance"]).sum()

    due = inv[~inv["has_payment"]].copy()
    due_concern = due[due["has_concern"]]
    due_clean = due[~due["has_concern"]]

    # ---- value flow chart ----
    flow = hbar_chart([
        ("PO value", po_v, PAL["muted"]),
        ("Invoice value", inv_v, PAL["blue"]),
        ("GRN (received) value", grn_v, PAL["aqua"]),
        ("DN (debit note) value", dn_v, PAL["serious"]),
        ("Net payable", npay_v, PAL["violet"]),
    ], unit="cr")

    # ---- payment buckets ----
    bcount = inv.groupby("bucket").size().reindex(BUCKET_ORDER).fillna(0).astype(int)
    bval = inv.groupby("bucket")["net_payable"].sum().reindex(BUCKET_ORDER).fillna(0)
    seg = [(b, int(bcount[b]), BUCKET_COLOR[b]) for b in BUCKET_ORDER]
    don = donut(seg)
    leg = legend([(b, f'{int(bcount[b])} · {cr(bval[b])}', BUCKET_COLOR[b])
                  for b in BUCKET_ORDER])

    # ---- discrepancy table (top 25 by |variance value|) ----
    # price short lines at invoice landing, excess lines at GRN landing
    # (excess lines are often invoiced at qty 0, so invoice price is 0)
    disc["var_value"] = disc["qty_variance"].abs() * np.where(
        disc["qty_variance"] > 0, disc["invoice_landing_price"], disc["grn_landing_price"])
    disc["dir"] = np.where(disc["qty_variance"] > 0, "Short (billed > received)",
                           "Excess (received > billed)")
    dtop = disc.sort_values("var_value", ascending=False).head(25)
    drows = [[
        esc(r.invoice_id), esc(r.po_number), esc(r.city_name),
        esc((r.item_name or "")[:38]),
        num(r.invoice_quantity), num(r.grn_quantity), num(r.dn_quantity),
        f'{r.qty_variance:,.0f}',
        f'<span class="tag {"crit" if r.qty_variance>0 else "ser"}">{esc(r.dir.split()[0])}</span>',
        rupees(r.var_value),
    ] for r in dtop.itertuples()]
    dtable = table(
        ["Invoice", "PO", "City", "Item", "Inv qty", "GRN qty", "DN qty",
         "Variance", "Direction", "Variance ₹"],
        drows,
        aligns=["left", "left", "left", "left", "right", "right", "right",
                "right", "left", "right"],
        cls="data")

    # ---- payments due tables ----
    def due_rows(d):
        d = d.sort_values("net_payable", ascending=False).head(25)
        return [[esc(r.invoice_id), esc(r.vendor), esc(r.city),
                 num(r.lines), cr(r.net_payable),
                 num(r.dn_qty) if r.dn_qty else "—",
                 f'{r.disc_lines}' if r.disc_lines else "—"]
                for r in d.itertuples()]
    hdr_due = ["Invoice", "Vendor", "City", "Lines", "Net payable",
               "DN qty", "Disc lines"]
    al_due = ["left", "left", "left", "right", "right", "right", "right"]
    t_due_clean = table(hdr_due, due_rows(due_clean), al_due, "data")
    t_due_concern = table(hdr_due, due_rows(due_concern), al_due, "data")

    # ---- by city summary ----
    city = df.groupby("city_name").agg(
        po=("po_value", "sum"), invv=("invoice_value", "sum"),
        grn=("grn_value", "sum"), dn=("dn_value", "sum"),
        net=("net_payable", "sum"), disc=("is_discrepant", "sum"),
    ).sort_values("net", ascending=False).head(15).reset_index()
    crows = [[esc(r.city_name), cr(r.po), cr(r.invv), cr(r.grn), cr(r.dn),
              cr(r.net), f'{int(r.disc)}'] for r in city.itertuples()]
    ctable = table(["City", "PO", "Invoice", "GRN", "DN", "Net payable", "Disc lines"],
                   crows, ["left"] + ["right"]*6, "data")

    # ---- by vendor summary ----
    ven = df.groupby("vendor_name").agg(
        invoices=("invoice_id", "nunique"), net=("net_payable", "sum"),
        dn=("dn_value", "sum"), disc=("is_discrepant", "sum"),
    ).sort_values("net", ascending=False).head(15).reset_index()
    vrows = [[esc((r.vendor_name or "")[:52]), f'{int(r.invoices)}',
              cr(r.net), cr(r.dn), f'{int(r.disc)}'] for r in ven.itertuples()]
    vtable = table(["Vendor", "Invoices", "Net payable", "DN value", "Disc lines"],
                   vrows, ["left", "right", "right", "right", "right"], "data")

    # ---- KPIs ----
    kpis = "".join([
        kpi("PO value", cr(po_v), "purchase orders"),
        kpi("Invoice value", cr(inv_v), f"{n_inv:,} invoices"),
        kpi("GRN received value", cr(grn_v), "goods received"),
        kpi("DN value", cr(dn_v), f"{int((df.dn_quantity>TOL).sum()):,} lines w/ DN", "tone-ser"),
        kpi("Net payable", cr(npay_v), "invoice − DN"),
        kpi("Payment recorded", cr(pay_v_inv), "batch-level ⚠", "tone-warn"),
        kpi("Discrepant lines", f"{len(disc):,}", f"of {n_lines:,} lines",
            "tone-crit" if len(disc) else ""),
        kpi("Payments due", f"{len(due):,}", f"{len(due_concern)} w/ open concern",
            "tone-warn" if len(due) else ""),
    ])

    # ---- caveats ----
    caveats = []
    if "GPOS" not in df["source"].unique():
        caveats.append("<b>GPOS (Blinkit) rows are absent</b> — this dashboard "
                       "currently reflects HPTech (Zomato Hyperpure) only.")
    caveats.append("<b>Payment values are batch-level</b> — <code>total_payment_value</code> "
                   "is constant per invoice and reflects a vendor payment batch, not a "
                   "per-invoice settlement. “Paid” means a payment/UTR record exists; "
                   "rupee totals are not an invoice-level balance.")
    cav_html = "".join(f"<li>{c}</li>" for c in caveats)

    src_tone = "tone-warn"

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Amul Reconciliation Dashboard</title>
<style>
:root {{
  --surface:{PAL['surface']}; --page:{PAL['page']}; --ink:{PAL['ink']};
  --ink2:{PAL['ink2']}; --muted:{PAL['muted']}; --grid:{PAL['grid']};
  --axis:{PAL['axis']}; --border:rgba(11,11,11,0.10);
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --surface:#1a1a19; --page:#0d0d0d; --ink:#fff; --ink2:#c3c2b7;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10); }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--page); color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif; font-size:14px;
  line-height:1.5; }}
.wrap {{ max-width:1160px; margin:0 auto; padding:28px 22px 60px; }}
h1 {{ font-size:24px; margin:0 0 2px; }}
.sub {{ color:var(--ink2); margin:0 0 20px; }}
.sub b {{ color:var(--ink); }}
.card {{ background:var(--surface); border:1px solid var(--border);
  border-radius:12px; padding:20px 22px; margin:16px 0; }}
h2 {{ font-size:16px; margin:0 0 4px; }}
.hint {{ color:var(--muted); font-size:12.5px; margin:0 0 16px; }}
.banner {{ background:color-mix(in srgb, {PAL['warning']} 14%, var(--surface));
  border:1px solid color-mix(in srgb, {PAL['warning']} 45%, var(--border));
  border-radius:12px; padding:14px 18px; margin:16px 0; }}
.banner b {{ color:var(--ink); }}
.banner ul {{ margin:6px 0 0; padding-left:18px; }} .banner li {{ margin:3px 0; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }}
@media (max-width:820px) {{ .kpis {{ grid-template-columns:repeat(2,1fr); }} }}
.kpi {{ background:var(--surface); border:1px solid var(--border);
  border-radius:12px; padding:14px 16px; }}
.kpi-lbl {{ color:var(--muted); font-size:12px; text-transform:uppercase;
  letter-spacing:.03em; }}
.kpi-val {{ font-size:23px; font-weight:650; margin:4px 0 1px;
  font-variant-numeric:tabular-nums; }}
.kpi-sub {{ color:var(--ink2); font-size:12px; }}
.kpi.tone-crit {{ box-shadow:inset 3px 0 0 {PAL['critical']}; }}
.kpi.tone-warn {{ box-shadow:inset 3px 0 0 {PAL['warning']}; }}
.kpi.tone-ser  {{ box-shadow:inset 3px 0 0 {PAL['serious']}; }}
.chart .lbl {{ fill:var(--ink2); font-size:12.5px; }}
.chart .val {{ fill:var(--ink); font-size:12.5px; font-weight:600;
  font-variant-numeric:tabular-nums; }}
.split {{ display:grid; grid-template-columns:220px 1fr; gap:24px; align-items:center; }}
@media (max-width:720px) {{ .split {{ grid-template-columns:1fr; }} }}
.donut-num {{ fill:var(--ink); font-size:30px; font-weight:680;
  font-variant-numeric:tabular-nums; }}
.donut-cap {{ fill:var(--muted); font-size:12px; }}
.legend {{ display:flex; flex-direction:column; gap:8px; }}
.leg-row {{ display:flex; align-items:center; gap:10px; }}
.sw {{ width:14px; height:14px; border-radius:4px; flex:none; }}
.leg-lbl {{ flex:1; }} .leg-val {{ color:var(--ink2); font-variant-numeric:tabular-nums; }}
table.data {{ width:100%; border-collapse:collapse; font-size:13px;
  font-variant-numeric:tabular-nums; }}
table.data th {{ color:var(--muted); font-weight:600; font-size:11.5px;
  text-transform:uppercase; letter-spacing:.03em; padding:8px 10px;
  border-bottom:1px solid var(--axis); position:sticky; top:0;
  background:var(--surface); }}
table.data td {{ padding:7px 10px; border-bottom:1px solid var(--grid); }}
table.data tbody tr:hover {{ background:color-mix(in srgb,{PAL['blue']} 6%,var(--surface)); }}
.scroll {{ max-height:460px; overflow:auto; border:1px solid var(--border);
  border-radius:10px; }}
.tag {{ font-size:11px; padding:1px 7px; border-radius:20px; font-weight:600;
  color:#fff; }}
.tag.crit {{ background:{PAL['critical']}; }} .tag.ser {{ background:{PAL['serious']}; }}
.two {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
@media (max-width:820px) {{ .two {{ grid-template-columns:1fr; }} }}
.defs {{ color:var(--ink2); font-size:12.5px; }}
.defs code, code {{ background:color-mix(in srgb,var(--muted) 18%,var(--surface));
  padding:1px 5px; border-radius:5px; font-size:12px; }}
.foot {{ color:var(--muted); font-size:12px; margin-top:26px; }}
</style></head>
<body><div class="wrap">
<h1>Amul Reconciliation Dashboard</h1>
<p class="sub">Source: <b>{esc(sources)}</b> &nbsp;·&nbsp; GRN window <b>{esc(date_range)}</b>
 &nbsp;·&nbsp; <b>{n_inv:,}</b> invoices &nbsp;·&nbsp; <b>{n_lines:,}</b> line items</p>

<div class="banner"><b>Read before using these figures</b><ul>{cav_html}</ul></div>

<div class="kpis">{kpis}</div>

<div class="card">
  <h2>PO value vs Invoice, GRN, DN &amp; Net payable</h2>
  <p class="hint">PO = ordered value; Invoice = billed; GRN = value of goods actually
   received; DN = debit-note (short-supply) value; Net payable = Invoice − DN.</p>
  {flow}
</div>

<div class="card">
  <h2>Payment status by invoice</h2>
  <p class="hint">A payment/UTR record present = &ldquo;Paid&rdquo;. &ldquo;Open concern&rdquo;
   = invoice has a DN or a GRN+DN ≠ invoice quantity variance.</p>
  <div class="split">{don}{leg}</div>
</div>

<div class="card">
  <h2>Quantity discrepancies — GRN + DN ≠ Invoice</h2>
  <p class="hint">{len(disc):,} line(s) flagged. Short-received units: <b>{num(short_u)}</b>;
   excess-received units: <b>{num(excess_u)}</b>. Top 25 by variance value.</p>
  <div class="scroll">{dtable}</div>
</div>

<div class="card">
  <h2>Payments due</h2>
  <p class="hint">{len(due):,} invoice(s) with no payment recorded —
   {len(due_clean)} clean (release), {len(due_concern)} held for open concern.
   Top 25 each by net payable.</p>
  <div class="two">
    <div><h3 style="font-size:13.5px;margin:0 0 8px;color:{PAL['blue']}">
      Rightfully due — release ({len(due_clean)})</h3><div class="scroll">{t_due_clean}</div></div>
    <div><h3 style="font-size:13.5px;margin:0 0 8px;color:{PAL['critical']}">
      Due but held — open concern ({len(due_concern)})</h3><div class="scroll">{t_due_concern}</div></div>
  </div>
</div>

<div class="card">
  <h2>By city</h2>
  <div class="scroll">{ctable}</div>
</div>

<div class="card">
  <h2>By vendor</h2>
  <div class="scroll">{vtable}</div>
</div>

<div class="card defs">
  <h2>Definitions</h2>
  <ul>
   <li><b>PO value</b> = <code>po_quantity × po_landing_price</code></li>
   <li><b>Invoice value</b> = <code>invoice_quantity × invoice_landing_price</code></li>
   <li><b>GRN value</b> = <code>net_amount</code> (<code>grn_quantity × grn_landing_price</code>)</li>
   <li><b>DN value</b> = <code>dn_quantity × invoice_landing_price</code></li>
   <li><b>Net payable</b> = Invoice value − DN value</li>
   <li><b>Quantity variance</b> = <code>invoice_quantity − (grn_quantity + dn_quantity)</code>;
       non-zero = discrepancy</li>
   <li><b>Paid</b> = a payment amount / UTR is present for the invoice (batch-level; see banner)</li>
  </ul>
</div>

<p class="foot">Generated by build_dashboard.py from {esc(os.path.basename(path))} · offline / no external assets.</p>
</div></body></html>"""

    with open(out_html, "w") as f:
        f.write(doc)
    return out_html, dict(lines=n_lines, invoices=n_inv, discrepant=len(disc),
                          due=len(due), due_concern=len(due_concern))


if __name__ == "__main__":
    default_in = os.path.expanduser("~/amul_recon/amul_invoice_extract.parquet")
    default_out = os.path.expanduser("~/amul_recon/amul_recon_dashboard.html")
    inp = sys.argv[1] if len(sys.argv) > 1 else default_in
    outp = sys.argv[2] if len(sys.argv) > 2 else default_out
    path, stats = build(inp, outp)
    print("Wrote dashboard:", path)
    print("Stats:", stats)
