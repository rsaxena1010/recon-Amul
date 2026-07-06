#!/usr/bin/env python3
"""
Shared reconciliation analysis + dashboard renderer.

Canonical logic used by BOTH:
  - the notebook / build_dashboard.py (static HTML export), and
  - the hosted web app (web/app.py).

Renders a self-contained HTML page (inline SVG, no external assets).

Credit terms: Amul credit period is CREDIT_DAYS day(s) from GRN — an invoice is
payable that many days after its GRN date, and overdue past that if unpaid.
"""
import os
import html
import pandas as pd
import numpy as np

TOL = 0.001
CREDIT_DAYS = 1  # Amul: payable 1 day from GRN date

PAL = dict(
    surface="#fcfcfb", page="#f9f9f7", ink="#0b0b0b", ink2="#52514e",
    muted="#898781", grid="#e1e0d9", axis="#c3c2b7",
    blue="#2a78d6", aqua="#1baf7a", yellow="#eda100", violet="#4a3aa7",
    good="#0ca30c", warning="#fab219", serious="#ec835a", critical="#d03b3b",
)

# ------------------------------------------------------------- formatting ----
def cr(x):
    return "—" if pd.isna(x) else f"₹{x/1e7:,.2f} Cr"

def rupees(x):
    return "—" if pd.isna(x) else f"₹{x:,.0f}"

def num(x):
    return "—" if pd.isna(x) else f"{x:,.0f}"

def esc(x):
    return html.escape("" if x is None else str(x))

def dfmt(x):
    return "—" if pd.isna(x) else pd.Timestamp(x).strftime("%d %b %Y")


# --------------------------------------------------------------- loading -----
def read_any(path):
    """Read parquet or csv into a DataFrame."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".parquet", ".pq"):
        return pd.read_parquet(path)
    if ext in (".csv", ".txt"):
        return pd.read_csv(path)
    # try parquet then csv
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.read_csv(path)


REQUIRED = ["source", "invoice_id", "grn_date", "invoice_quantity",
            "grn_quantity", "dn_quantity"]


def enrich(df):
    df = df.copy()
    for c in ("invoice_date", "grn_date"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in ("po_quantity", "po_landing_price", "invoice_quantity",
              "invoice_landing_price", "grn_quantity", "grn_landing_price",
              "dn_quantity", "net_amount", "total_payment_value"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df["po_value"]      = df["po_quantity"]      * df["po_landing_price"]
    df["invoice_value"] = df["invoice_quantity"] * df["invoice_landing_price"]
    df["grn_value"]     = df["net_amount"]
    df["dn_value"]      = df["dn_quantity"]      * df["invoice_landing_price"]
    df["inward_qty"]    = df["grn_quantity"]     + df["dn_quantity"]
    df["qty_variance"]  = df["invoice_quantity"] - df["inward_qty"]
    df["net_payable"]   = df["invoice_value"]    - df["dn_value"]
    df["is_discrepant"] = df["qty_variance"].abs() > TOL
    return df


def aging_bucket(days):
    if days <= 0:
        return "Within credit"
    if days <= 7:
        return "Overdue 1–7d"
    if days <= 30:
        return "Overdue 8–30d"
    if days <= 60:
        return "Overdue 31–60d"
    return "Overdue >60d"

AGING_ORDER = ["Within credit", "Overdue 1–7d", "Overdue 8–30d",
               "Overdue 31–60d", "Overdue >60d"]
AGING_COLOR = {
    "Within credit": PAL["blue"], "Overdue 1–7d": PAL["warning"],
    "Overdue 8–30d": PAL["serious"], "Overdue 31–60d": PAL["critical"],
    "Overdue >60d": "#9b1c1c",
}


def invoice_rollup(df, as_of):
    inv = df.groupby("invoice_id").agg(
        source=("source", "first"),
        vendor=("vendor_name", "first") if "vendor_name" in df else ("source", "first"),
        city=("city_name", "first") if "city_name" in df else ("source", "first"),
        po_value=("po_value", "sum"),
        invoice_value=("invoice_value", "sum"),
        grn_value=("grn_value", "sum"),
        dn_value=("dn_value", "sum"),
        net_payable=("net_payable", "sum"),
        dn_qty=("dn_quantity", "sum"),
        qty_var_abs=("qty_variance", lambda s: s.abs().sum()),
        disc_lines=("is_discrepant", "sum"),
        grn_date=("grn_date", "max"),
        payment=("total_payment_value", "first"),
        utr=("utr_numbers", "first") if "utr_numbers" in df else ("source", "first"),
        lines=("invoice_id", "size"),
    ).reset_index()

    inv["has_payment"] = inv["payment"].notna() & (inv["payment"] > 0)
    inv["has_concern"] = inv["qty_var_abs"] > TOL

    # credit-day / due-date
    inv["due_date"] = inv["grn_date"] + pd.Timedelta(days=CREDIT_DAYS)
    inv["days_overdue"] = (pd.Timestamp(as_of) - inv["due_date"]).dt.days
    inv["days_overdue"] = inv["days_overdue"].fillna(0).clip(lower=0).astype(int)
    inv["overdue"] = (~inv["has_payment"]) & (pd.Timestamp(as_of) > inv["due_date"])
    inv["aging"] = np.where(
        inv["has_payment"], "Paid",
        inv["days_overdue"].map(aging_bucket))

    def bucket(r):
        if r["has_payment"]:
            return "Paid – qty variance" if r["has_concern"] else "Paid – clean"
        return "Due – open concern" if r["has_concern"] else "Due – clean (release)"
    inv["bucket"] = inv.apply(bucket, axis=1)
    return inv


BUCKET_ORDER = ["Paid – clean", "Paid – qty variance",
                "Due – clean (release)", "Due – open concern"]
BUCKET_COLOR = {
    "Paid – clean": PAL["good"], "Paid – qty variance": PAL["warning"],
    "Due – clean (release)": PAL["blue"], "Due – open concern": PAL["critical"],
}


# ------------------------------------------------------------ svg helpers ----
def hbar_chart(rows, unit="cr", width=680, bar_h=30, gap=14, pad_l=170, pad_r=130):
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
        out.append(f'<text x="{pad_l-10}" y="{y+bar_h/2+4}" text-anchor="end" class="lbl">{esc(label)}</text>')
        out.append(f'<rect x="{pad_l}" y="{y}" width="{bw:.1f}" height="{bar_h}" rx="4" fill="{color}"><title>{esc(label)}: {disp}</title></rect>')
        out.append(f'<text x="{pad_l+bw+8:.1f}" y="{y+bar_h/2+4}" class="val">{disp}</text>')
    out.append("</svg>")
    return "".join(out)


def donut(seg, size=200, thick=34):
    total = sum(v for _, v, _ in seg) or 1
    r = size / 2
    inner = r - thick
    cx = cy = r
    out = [f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img" class="donut">']
    ang = -90.0
    for label, val, color in seg:
        frac = val / total
        sweep = frac * 360
        a0, a1 = np.radians(ang), np.radians(ang + sweep)
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
    out.append(f'<text x="{cx}" y="{cy-4}" text-anchor="middle" class="donut-num">{int(total)}</text>')
    out.append(f'<text x="{cx}" y="{cy+16}" text-anchor="middle" class="donut-cap">invoices</text>')
    out.append("</svg>")
    return "".join(out)


def legend(items):
    li = "".join(
        f'<div class="leg-row"><span class="sw" style="background:{c}"></span>'
        f'<span class="leg-lbl">{esc(l)}</span><span class="leg-val">{v}</span></div>'
        for l, v, c in items)
    return f'<div class="legend">{li}</div>'


def table(headers, rows, aligns=None, cls="data"):
    aligns = aligns or ["left"] * len(headers)
    th = "".join(f'<th style="text-align:{a}">{esc(h)}</th>' for h, a in zip(headers, aligns))
    trs = []
    for r in rows:
        tds = "".join(f'<td style="text-align:{a}">{c}</td>' for c, a in zip(r, aligns))
        trs.append(f"<tr>{tds}</tr>")
    body = "".join(trs) or '<tr><td class="empty">No rows.</td></tr>'
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def kpi(label, value, sub="", tone=""):
    return (f'<div class="kpi {tone}"><div class="kpi-lbl">{esc(label)}</div>'
            f'<div class="kpi-val">{value}</div><div class="kpi-sub">{esc(sub)}</div></div>')


# ------------------------------------------------------------------ render ---
def render_page(df, as_of, upload_html="", data_note=""):
    df = enrich(df)
    inv = invoice_rollup(df, as_of)

    n_lines, n_inv = len(df), len(inv)
    sources = ", ".join(sorted(df["source"].dropna().unique()))
    gmin, gmax = df["grn_date"].min(), df["grn_date"].max()
    date_range = f"{dfmt(gmin)} – {dfmt(gmax)}"

    po_v, inv_v = df["po_value"].sum(), df["invoice_value"].sum()
    grn_v, dn_v = df["grn_value"].sum(), df["dn_value"].sum()
    npay_v = df["net_payable"].sum()
    pay_v = inv.loc[inv["has_payment"], "payment"].sum()

    disc = df[df["is_discrepant"]].copy()
    short_u = df.loc[df.qty_variance > TOL, "qty_variance"].sum()
    excess_u = (-df.loc[df.qty_variance < -TOL, "qty_variance"]).sum()

    due = inv[~inv["has_payment"]].copy()
    overdue = due[due["overdue"]]
    overdue_val = overdue["net_payable"].sum()

    # value flow
    flow = hbar_chart([
        ("PO value", po_v, PAL["muted"]),
        ("Invoice value", inv_v, PAL["blue"]),
        ("GRN (received) value", grn_v, PAL["aqua"]),
        ("DN (debit note) value", dn_v, PAL["serious"]),
        ("Net payable", npay_v, PAL["violet"]),
    ])

    # payment buckets
    bcount = inv.groupby("bucket").size().reindex(BUCKET_ORDER).fillna(0).astype(int)
    bval = inv.groupby("bucket")["net_payable"].sum().reindex(BUCKET_ORDER).fillna(0)
    seg = [(b, int(bcount[b]), BUCKET_COLOR[b]) for b in BUCKET_ORDER]
    don = donut(seg)
    leg = legend([(b, f'{int(bcount[b])} · {cr(bval[b])}', BUCKET_COLOR[b]) for b in BUCKET_ORDER])

    # payables aging (unpaid only)
    ag = due.groupby(pd.Categorical(due["aging"], categories=AGING_ORDER, ordered=True), observed=False)
    ag_val = ag["net_payable"].sum().reindex(AGING_ORDER).fillna(0)
    ag_cnt = ag.size().reindex(AGING_ORDER).fillna(0).astype(int)
    aging_chart = hbar_chart(
        [(f"{b} ({int(ag_cnt[b])})", float(ag_val[b]), AGING_COLOR[b]) for b in AGING_ORDER])

    # discrepancy table
    disc["var_value"] = disc["qty_variance"].abs() * np.where(
        disc["qty_variance"] > 0, disc["invoice_landing_price"], disc["grn_landing_price"])
    disc["dir"] = np.where(disc["qty_variance"] > 0, "Short", "Excess")
    dtop = disc.sort_values("var_value", ascending=False).head(30)
    drows = [[
        esc(r.invoice_id), esc(getattr(r, "po_number", "")),
        esc(getattr(r, "city_name", "")), esc((getattr(r, "item_name", "") or "")[:36]),
        num(r.invoice_quantity), num(r.grn_quantity), num(r.dn_quantity),
        f'{r.qty_variance:,.0f}',
        f'<span class="tag {"crit" if r.qty_variance>0 else "ser"}">{esc(r.dir)}</span>',
        rupees(r.var_value),
    ] for r in dtop.itertuples()]
    dtable = table(
        ["Invoice", "PO", "City", "Item", "Inv qty", "GRN qty", "DN qty",
         "Variance", "Type", "Variance ₹"], drows,
        ["left", "left", "left", "left", "right", "right", "right", "right", "left", "right"])

    # due tables (with due date + overdue)
    def due_rows(d):
        d = d.sort_values(["overdue", "net_payable"], ascending=[False, False]).head(30)
        out = []
        for r in d.itertuples():
            od = (f'<span class="tag crit">{r.days_overdue}d</span>'
                  if r.overdue else '<span class="tag ok">within</span>')
            out.append([esc(r.invoice_id), esc(r.vendor), esc(r.city),
                        cr(r.net_payable), dfmt(r.due_date), od])
        return out
    hdr_due = ["Invoice", "Vendor", "City", "Net payable", "Due date", "Overdue"]
    al_due = ["left", "left", "left", "right", "left", "left"]
    t_due_clean = table(hdr_due, due_rows(due[~due["has_concern"]]), al_due)
    t_due_concern = table(hdr_due, due_rows(due[due["has_concern"]]), al_due)

    # by city
    if "city_name" in df:
        city = df.groupby("city_name").agg(
            po=("po_value", "sum"), invv=("invoice_value", "sum"),
            grn=("grn_value", "sum"), dn=("dn_value", "sum"),
            net=("net_payable", "sum"), disc=("is_discrepant", "sum"),
        ).sort_values("net", ascending=False).head(20).reset_index()
        crows = [[esc(r.city_name), cr(r.po), cr(r.invv), cr(r.grn), cr(r.dn),
                  cr(r.net), f'{int(r.disc)}'] for r in city.itertuples()]
        ctable = table(["City", "PO", "Invoice", "GRN", "DN", "Net payable", "Disc lines"],
                       crows, ["left"] + ["right"]*6)
    else:
        ctable = "<p class='hint'>No city_name column.</p>"

    # by vendor
    if "vendor_name" in df:
        ven = df.groupby("vendor_name").agg(
            invoices=("invoice_id", "nunique"), net=("net_payable", "sum"),
            dn=("dn_value", "sum"), disc=("is_discrepant", "sum"),
        ).sort_values("net", ascending=False).head(20).reset_index()
        vrows = [[esc((r.vendor_name or "")[:52]), f'{int(r.invoices)}',
                  cr(r.net), cr(r.dn), f'{int(r.disc)}'] for r in ven.itertuples()]
        vtable = table(["Vendor", "Invoices", "Net payable", "DN value", "Disc lines"],
                       vrows, ["left", "right", "right", "right", "right"])
    else:
        vtable = "<p class='hint'>No vendor_name column.</p>"

    kpis = "".join([
        kpi("PO value", cr(po_v), "purchase orders"),
        kpi("Invoice value", cr(inv_v), f"{n_inv:,} invoices"),
        kpi("GRN received value", cr(grn_v), "goods received"),
        kpi("DN value", cr(dn_v), f"{int((df.dn_quantity>TOL).sum()):,} lines w/ DN", "tone-ser"),
        kpi("Net payable", cr(npay_v), "invoice − DN"),
        kpi("Payment recorded", cr(pay_v), "batch-level ⚠", "tone-warn"),
        kpi("Discrepant lines", f"{len(disc):,}", f"of {n_lines:,} lines",
            "tone-crit" if len(disc) else ""),
        kpi("Overdue payable", cr(overdue_val), f"{len(overdue)} invoices past due",
            "tone-crit" if len(overdue) else ""),
    ])

    caveats = []
    if "GPOS" not in df["source"].unique():
        caveats.append("<b>GPOS (Blinkit) rows are absent</b> — this reflects HPTech "
                       "(Zomato Hyperpure) only.")
    caveats.append("<b>Payment values are batch-level</b> — <code>total_payment_value</code> is "
                   "constant per invoice (a vendor payment batch), not a per-invoice settlement. "
                   "“Paid” = a payment/UTR record exists.")
    caveats.append(f"<b>Credit term = {CREDIT_DAYS} day</b> — an invoice is payable "
                   f"{CREDIT_DAYS} day after GRN and <b>overdue</b> past that if unpaid. "
                   f"“As of” date drives overdue calc.")
    cav_html = "".join(f"<li>{c}</li>" for c in caveats)

    as_of_str = pd.Timestamp(as_of).strftime("%Y-%m-%d")

    return PAGE_TEMPLATE.format(
        css=CSS.format(c=PAL), upload=upload_html, sources=esc(sources), date_range=esc(date_range),
        n_inv=f"{n_inv:,}", n_lines=f"{n_lines:,}", as_of=as_of_str,
        data_note=esc(data_note), cav=cav_html, kpis=kpis, flow=flow,
        don=don, leg=leg, aging=aging_chart, ndisc=f"{len(disc):,}",
        short=num(short_u), excess=num(excess_u), dtable=dtable,
        ndue=len(due), nover=len(overdue), t_clean=t_due_clean, t_concern=t_due_concern,
        n_clean=len(due[~due["has_concern"]]), n_concern=len(due[due["has_concern"]]),
        ctable=ctable, vtable=vtable, credit=CREDIT_DAYS,
        c=PAL,
    )


def build(path, out_html, as_of=None):
    """Static export used by the notebook / build_dashboard.py."""
    if as_of is None:
        as_of = pd.Timestamp.today().normalize()
    df = read_any(path)
    page = render_page(df, as_of, upload_html="",
                       data_note=f"Source file: {os.path.basename(path)}")
    with open(out_html, "w") as f:
        f.write(page)
    df2 = enrich(df)
    inv = invoice_rollup(df2, as_of)
    return out_html, dict(lines=len(df2), invoices=len(inv),
                          discrepant=int(df2["is_discrepant"].sum()),
                          due=int((~inv["has_payment"]).sum()),
                          overdue=int(inv["overdue"].sum()))


# ------------------------------------------------------------------- CSS -----
CSS = """
:root {{ --surface:{c[surface]}; --page:{c[page]}; --ink:{c[ink]}; --ink2:{c[ink2]};
  --muted:{c[muted]}; --grid:{c[grid]}; --axis:{c[axis]}; --border:rgba(11,11,11,0.10); }}
@media (prefers-color-scheme: dark) {{ :root {{ --surface:#1a1a19; --page:#0d0d0d;
  --ink:#fff; --ink2:#c3c2b7; --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10); }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--page); color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif; font-size:14px; line-height:1.5; }}
.wrap {{ max-width:1160px; margin:0 auto; padding:26px 22px 60px; }}
h1 {{ font-size:23px; margin:0 0 2px; }}
.sub {{ color:var(--ink2); margin:0 0 16px; }} .sub b {{ color:var(--ink); }}
.card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px;
  padding:20px 22px; margin:16px 0; }}
h2 {{ font-size:16px; margin:0 0 4px; }} h3 {{ font-size:13.5px; margin:0 0 8px; }}
.hint {{ color:var(--muted); font-size:12.5px; margin:0 0 16px; }}
.banner {{ background:color-mix(in srgb,{c[warning]} 14%,var(--surface));
  border:1px solid color-mix(in srgb,{c[warning]} 45%,var(--border)); border-radius:12px;
  padding:14px 18px; margin:16px 0; }}
.banner ul {{ margin:6px 0 0; padding-left:18px; }} .banner li {{ margin:3px 0; }}
.uploadbar {{ display:flex; flex-wrap:wrap; gap:14px; align-items:center;
  background:var(--surface); border:1px solid var(--border); border-radius:12px;
  padding:14px 18px; margin:14px 0; }}
.uploadbar form {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
.uploadbar input[type=file] {{ font-size:13px; }}
.uploadbar input[type=date] {{ padding:6px 8px; border:1px solid var(--axis);
  border-radius:8px; background:var(--surface); color:var(--ink); }}
.btn {{ background:{c[blue]}; color:#fff; border:0; border-radius:8px; padding:8px 16px;
  font-weight:600; cursor:pointer; font-size:13px; }}
.btn.ghost {{ background:transparent; color:{c[blue]}; border:1px solid {c[blue]}; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }}
@media (max-width:820px) {{ .kpis {{ grid-template-columns:repeat(2,1fr); }} }}
.kpi {{ background:var(--surface); border:1px solid var(--border); border-radius:12px;
  padding:14px 16px; }}
.kpi-lbl {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.03em; }}
.kpi-val {{ font-size:22px; font-weight:650; margin:4px 0 1px; font-variant-numeric:tabular-nums; }}
.kpi-sub {{ color:var(--ink2); font-size:12px; }}
.kpi.tone-crit {{ box-shadow:inset 3px 0 0 {c[critical]}; }}
.kpi.tone-warn {{ box-shadow:inset 3px 0 0 {c[warning]}; }}
.kpi.tone-ser {{ box-shadow:inset 3px 0 0 {c[serious]}; }}
.chart .lbl {{ fill:var(--ink2); font-size:12.5px; }}
.chart .val {{ fill:var(--ink); font-size:12.5px; font-weight:600; font-variant-numeric:tabular-nums; }}
.split {{ display:grid; grid-template-columns:220px 1fr; gap:24px; align-items:center; }}
@media (max-width:720px) {{ .split {{ grid-template-columns:1fr; }} }}
.donut-num {{ fill:var(--ink); font-size:30px; font-weight:680; font-variant-numeric:tabular-nums; }}
.donut-cap {{ fill:var(--muted); font-size:12px; }}
.legend {{ display:flex; flex-direction:column; gap:8px; }}
.leg-row {{ display:flex; align-items:center; gap:10px; }}
.sw {{ width:14px; height:14px; border-radius:4px; flex:none; }}
.leg-lbl {{ flex:1; }} .leg-val {{ color:var(--ink2); font-variant-numeric:tabular-nums; }}
table.data {{ width:100%; border-collapse:collapse; font-size:13px; font-variant-numeric:tabular-nums; }}
table.data th {{ color:var(--muted); font-weight:600; font-size:11.5px; text-transform:uppercase;
  letter-spacing:.03em; padding:8px 10px; border-bottom:1px solid var(--axis); position:sticky;
  top:0; background:var(--surface); }}
table.data td {{ padding:7px 10px; border-bottom:1px solid var(--grid); }}
table.data td.empty {{ color:var(--muted); }}
table.data tbody tr:hover {{ background:color-mix(in srgb,{c[blue]} 6%,var(--surface)); }}
.scroll {{ max-height:460px; overflow:auto; border:1px solid var(--border); border-radius:10px; }}
.tag {{ font-size:11px; padding:1px 7px; border-radius:20px; font-weight:600; color:#fff; }}
.tag.crit {{ background:{c[critical]}; }} .tag.ser {{ background:{c[serious]}; }}
.tag.ok {{ background:transparent; color:var(--muted); border:1px solid var(--axis); }}
.two {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
@media (max-width:820px) {{ .two {{ grid-template-columns:1fr; }} }}
code {{ background:color-mix(in srgb,var(--muted) 18%,var(--surface)); padding:1px 5px;
  border-radius:5px; font-size:12px; }}
.foot {{ color:var(--muted); font-size:12px; margin-top:26px; }}
"""

PAGE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Amul Reconciliation Dashboard</title>
<style>{css}</style></head><body><div class="wrap">
<h1>Amul Reconciliation Dashboard</h1>
<p class="sub">Source: <b>{sources}</b> · GRN window <b>{date_range}</b> ·
 <b>{n_inv}</b> invoices · <b>{n_lines}</b> line items · as of <b>{as_of}</b>
 <span style="color:var(--muted)">· {data_note}</span></p>
{upload}
<div class="banner"><b>Read before using these figures</b><ul>{cav}</ul></div>
<div class="kpis">{kpis}</div>
<div class="card"><h2>PO value vs Invoice, GRN, DN &amp; Net payable</h2>
 <p class="hint">PO = ordered; Invoice = billed; GRN = value received; DN = debit-note
  (short-supply); Net payable = Invoice − DN.</p>{flow}</div>
<div class="card"><h2>Payment status by invoice</h2>
 <p class="hint">“Paid” = a payment/UTR record present. “Open concern” = GRN+DN ≠ invoice qty.</p>
 <div class="split">{don}{leg}</div></div>
<div class="card"><h2>Payables aging — credit {credit} day from GRN</h2>
 <p class="hint">Unpaid invoices bucketed by days past the due date (GRN + {credit}d).
  {nover} of {ndue} unpaid invoices are overdue.</p>{aging}</div>
<div class="card"><h2>Quantity discrepancies — GRN + DN ≠ Invoice</h2>
 <p class="hint">{ndisc} line(s). Short-received units: <b>{short}</b>; excess-received: <b>{excess}</b>.
  Top 30 by variance value.</p><div class="scroll">{dtable}</div></div>
<div class="card"><h2>Payments due</h2>
 <p class="hint">{ndue} invoice(s) with no payment recorded — {n_clean} clean, {n_concern} with
  open concern. Overdue vs within-credit per the “as of” date.</p>
 <div class="two">
  <div><h3 style="color:{c[blue]}">Rightfully due — release ({n_clean})</h3>
   <div class="scroll">{t_clean}</div></div>
  <div><h3 style="color:{c[critical]}">Due but held — open concern ({n_concern})</h3>
   <div class="scroll">{t_concern}</div></div></div></div>
<div class="card"><h2>By city</h2><div class="scroll">{ctable}</div></div>
<div class="card"><h2>By vendor</h2><div class="scroll">{vtable}</div></div>
<p class="foot">Offline dashboard · no external assets · Amul reconciliation.</p>
</div></body></html>"""


if __name__ == "__main__":
    import sys
    inp = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/amul_recon/amul_invoice_extract.parquet")
    outp = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser(
        "~/amul_recon/amul_recon_dashboard.html")
    p, s = build(inp, outp)
    print("Wrote", p, s)
