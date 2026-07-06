#!/usr/bin/env python3
"""
Presentation layer — dependency-free (stdlib only, no pandas/numpy).

Shared by BOTH:
  - web/dashboard_core.py (pandas compute, for the notebook static export), and
  - web/app.py (pure-stdlib compute, for the hosted web app).

Callers compute the numbers + table rows however they like, then hand structured
values to assemble(), which builds the SVG charts, tables and the full HTML page.
"""
import html
import math

TOL = 0.001
CREDIT_DAYS = 1  # Amul: payable 1 day from GRN date

PAL = dict(
    surface="#fcfcfb", page="#f9f9f7", ink="#0b0b0b", ink2="#52514e",
    muted="#898781", grid="#e1e0d9", axis="#c3c2b7",
    blue="#2a78d6", aqua="#1baf7a", yellow="#eda100", violet="#4a3aa7",
    good="#0ca30c", warning="#fab219", serious="#ec835a", critical="#d03b3b",
)

BUCKET_ORDER = ["Paid – clean", "Paid – qty variance",
                "Due – clean (release)", "Due – open concern"]
BUCKET_COLOR = {
    "Paid – clean": PAL["good"], "Paid – qty variance": PAL["warning"],
    "Due – clean (release)": PAL["blue"], "Due – open concern": PAL["critical"],
}

AGING_ORDER = ["Within credit", "Overdue 1–7d", "Overdue 8–30d",
               "Overdue 31–60d", "Overdue >60d"]
AGING_COLOR = {
    "Within credit": PAL["blue"], "Overdue 1–7d": PAL["warning"],
    "Overdue 8–30d": PAL["serious"], "Overdue 31–60d": PAL["critical"],
    "Overdue >60d": "#9b1c1c",
}


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


# ------------------------------------------------------------- formatting ----
def cr(x):
    return "—" if x is None else f"₹{x/1e7:,.2f} Cr"

def rupees(x):
    return "—" if x is None else f"₹{x:,.0f}"

def num(x):
    return "—" if x is None else f"{x:,.0f}"

def esc(x):
    return html.escape("" if x is None else str(x))

def tag_type(is_short):
    kind = "crit" if is_short else "ser"
    label = "Short" if is_short else "Excess"
    return f'<span class="tag {kind}">{label}</span>'

def tag_overdue(overdue, days):
    return (f'<span class="tag crit">{int(days)}d</span>' if overdue
            else '<span class="tag ok">within</span>')


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
        a0, a1 = math.radians(ang), math.radians(ang + sweep)
        large = 1 if sweep > 180 else 0
        x0, y0 = cx + r*math.cos(a0), cy + r*math.sin(a0)
        x1, y1 = cx + r*math.cos(a1), cy + r*math.sin(a1)
        xi0, yi0 = cx + inner*math.cos(a1), cy + inner*math.sin(a1)
        xi1, yi1 = cx + inner*math.cos(a0), cy + inner*math.sin(a0)
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


def table(headers, rows, aligns=None):
    aligns = aligns or ["left"] * len(headers)
    th = "".join(f'<th style="text-align:{a}">{esc(h)}</th>' for h, a in zip(headers, aligns))
    trs = []
    for r in rows:
        tds = "".join(f'<td style="text-align:{a}">{c}</td>' for c, a in zip(r, aligns))
        trs.append(f"<tr>{tds}</tr>")
    body = "".join(trs) or '<tr><td class="empty">No rows.</td></tr>'
    return f'<table class="data"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def kpi(label, value, sub="", tone=""):
    return (f'<div class="kpi {tone}"><div class="kpi-lbl">{esc(label)}</div>'
            f'<div class="kpi-val">{value}</div><div class="kpi-sub">{esc(sub)}</div></div>')


# ------------------------------------------------------------------ assemble -
def assemble(ctx):
    """Build the full HTML page from computed primitives (see keys used below)."""
    flow = hbar_chart([
        ("PO value", ctx["po_v"], PAL["muted"]),
        ("Invoice value", ctx["inv_v"], PAL["blue"]),
        ("GRN (received) value", ctx["grn_v"], PAL["aqua"]),
        ("DN (debit note) value", ctx["dn_v"], PAL["serious"]),
        ("Net payable", ctx["npay_v"], PAL["violet"]),
    ])
    seg = [(b, ctx["bucket_counts"].get(b, 0), BUCKET_COLOR[b]) for b in BUCKET_ORDER]
    don = donut(seg)
    leg = legend([(b, f'{ctx["bucket_counts"].get(b, 0)} · {cr(ctx["bucket_vals"].get(b, 0))}',
                   BUCKET_COLOR[b]) for b in BUCKET_ORDER])
    aging = hbar_chart([(f'{b} ({ctx["aging_counts"].get(b, 0)})',
                         ctx["aging_vals"].get(b, 0.0), AGING_COLOR[b]) for b in AGING_ORDER])

    dtable = table(
        ["Invoice", "PO", "City", "Item", "Inv qty", "GRN qty", "DN qty",
         "Variance", "Type", "Variance ₹"], ctx["disc_rows"],
        ["left", "left", "left", "left", "right", "right", "right", "right", "left", "right"])
    hdr_due = ["Invoice", "Vendor", "City", "Net payable", "Due date", "Overdue"]
    al_due = ["left", "left", "left", "right", "left", "left"]
    t_clean = table(hdr_due, ctx["due_clean_rows"], al_due)
    t_concern = table(hdr_due, ctx["due_concern_rows"], al_due)
    ctable = table(["City", "PO", "Invoice", "GRN", "DN", "Net payable", "Disc lines"],
                   ctx["city_rows"], ["left"] + ["right"]*6)
    vtable = table(["Vendor", "Invoices", "Net payable", "DN value", "Disc lines"],
                   ctx["vendor_rows"], ["left", "right", "right", "right", "right"])

    kpis = "".join([
        kpi("PO value", cr(ctx["po_v"]), "purchase orders"),
        kpi("Invoice value", cr(ctx["inv_v"]), f'{ctx["n_inv"]:,} invoices'),
        kpi("GRN received value", cr(ctx["grn_v"]), "goods received"),
        kpi("DN value", cr(ctx["dn_v"]), f'{ctx["n_dn_lines"]:,} lines w/ DN', "tone-ser"),
        kpi("Net payable", cr(ctx["npay_v"]), "invoice − DN"),
        kpi("Payment recorded", cr(ctx["pay_v"]), "batch-level ⚠", "tone-warn"),
        kpi("Discrepant lines", f'{ctx["n_disc"]:,}', f'of {ctx["n_lines"]:,} lines',
            "tone-crit" if ctx["n_disc"] else ""),
        kpi("Overdue payable", cr(ctx["overdue_val"]), f'{ctx["n_overdue"]} invoices past due',
            "tone-crit" if ctx["n_overdue"] else ""),
    ])

    caveats = []
    if ctx.get("gpos_absent"):
        caveats.append("<b>GPOS (Blinkit) rows are absent</b> — this reflects HPTech "
                       "(Zomato Hyperpure) only.")
    caveats.append("<b>Payment values are batch-level</b> — <code>total_payment_value</code> is "
                   "constant per invoice (a vendor payment batch), not a per-invoice settlement. "
                   "“Paid” = a payment/UTR record exists.")
    caveats.append(f"<b>Credit term = {CREDIT_DAYS} day</b> — an invoice is payable "
                   f"{CREDIT_DAYS} day after GRN and <b>overdue</b> past that if unpaid. "
                   f"“As of” date drives overdue calc.")
    cav = "".join(f"<li>{c}</li>" for c in caveats)

    return PAGE_TEMPLATE.format(
        css=CSS.format(c=PAL), upload=ctx.get("upload_html", ""),
        sources=esc(ctx["sources"]), date_range=esc(ctx["date_range"]),
        n_inv=f'{ctx["n_inv"]:,}', n_lines=f'{ctx["n_lines"]:,}', as_of=esc(ctx["as_of_str"]),
        data_note=esc(ctx.get("data_note", "")), cav=cav, kpis=kpis, flow=flow,
        don=don, leg=leg, aging=aging, ndisc=f'{ctx["n_disc"]:,}',
        short=num(ctx["short_u"]), excess=num(ctx["excess_u"]), dtable=dtable,
        ndue=ctx["n_due"], nover=ctx["n_overdue"], t_clean=t_clean, t_concern=t_concern,
        n_clean=ctx["n_clean"], n_concern=ctx["n_concern"],
        ctable=ctable, vtable=vtable, credit=CREDIT_DAYS, c=PAL)


def shell(title, body):
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{esc(title)}</title><style>{CSS.format(c=PAL)}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")


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
.filterbar {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center;
  background:var(--surface); border:1px solid var(--border); border-radius:12px;
  padding:12px 18px; margin:0 0 14px; }}
.filterbar input[type=date] {{ padding:6px 8px; border:1px solid var(--axis);
  border-radius:8px; background:var(--surface); color:var(--ink); }}
.filterbar .flabel {{ color:var(--muted); font-size:12.5px; }}
.btn {{ background:{c[blue]}; color:#fff; border:0; border-radius:8px; padding:8px 16px;
  font-weight:600; cursor:pointer; font-size:13px; text-decoration:none; }}
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
