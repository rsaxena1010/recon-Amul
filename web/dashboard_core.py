#!/usr/bin/env python3
"""
Reconciliation analysis using pandas — for the notebook static HTML export.

Compute lives here (pandas); all presentation is delegated to render.assemble
(dependency-free), which the pure-stdlib web app (app.py) also uses.
"""
import os
import pandas as pd
import numpy as np

import render as R
from render import TOL, CREDIT_DAYS  # noqa: F401 (re-exported for callers)


def read_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".parquet", ".pq"):
        return pd.read_parquet(path)
    if ext in (".csv", ".txt"):
        return pd.read_csv(path)
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.read_csv(path)


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


def invoice_rollup(df, as_of):
    inv = df.groupby("invoice_id").agg(
        source=("source", "first"),
        vendor=("vendor_name", "first") if "vendor_name" in df else ("source", "first"),
        city=("city_name", "first") if "city_name" in df else ("source", "first"),
        po_value=("po_value", "sum"), invoice_value=("invoice_value", "sum"),
        grn_value=("grn_value", "sum"), dn_value=("dn_value", "sum"),
        net_payable=("net_payable", "sum"), dn_qty=("dn_quantity", "sum"),
        qty_var_abs=("qty_variance", lambda s: s.abs().sum()),
        disc_lines=("is_discrepant", "sum"), grn_date=("grn_date", "max"),
        payment=("total_payment_value", "first"),
        lines=("invoice_id", "size"),
    ).reset_index()
    inv["has_payment"] = inv["payment"].notna() & (inv["payment"] > 0)
    inv["has_concern"] = inv["qty_var_abs"] > TOL
    inv["due_date"] = inv["grn_date"] + pd.Timedelta(days=CREDIT_DAYS)
    inv["days_overdue"] = (pd.Timestamp(as_of) - inv["due_date"]).dt.days
    inv["days_overdue"] = inv["days_overdue"].fillna(0).clip(lower=0).astype(int)
    inv["overdue"] = (~inv["has_payment"]) & (pd.Timestamp(as_of) > inv["due_date"])
    inv["aging"] = np.where(inv["has_payment"], "Paid",
                            inv["days_overdue"].map(R.aging_bucket))

    def bucket(r):
        if r["has_payment"]:
            return "Paid – qty variance" if r["has_concern"] else "Paid – clean"
        return "Due – open concern" if r["has_concern"] else "Due – clean (release)"
    inv["bucket"] = inv.apply(bucket, axis=1)
    return inv


def _dt(x):
    return "—" if pd.isna(x) else pd.Timestamp(x).strftime("%d %b %Y")


def render_page(df, as_of, upload_html="", data_note=""):
    df = enrich(df)
    inv = invoice_rollup(df, as_of)
    n_lines, n_inv = len(df), len(inv)

    disc = df[df["is_discrepant"]].copy()
    disc["var_value"] = disc["qty_variance"].abs() * np.where(
        disc["qty_variance"] > 0, disc["invoice_landing_price"], disc["grn_landing_price"])
    dtop = disc.sort_values("var_value", ascending=False).head(30)
    disc_rows = [[
        R.esc(r.invoice_id), R.esc(getattr(r, "po_number", "")),
        R.esc(getattr(r, "city_name", "")), R.esc((getattr(r, "item_name", "") or "")[:36]),
        R.num(r.invoice_quantity), R.num(r.grn_quantity), R.num(r.dn_quantity),
        f"{r.qty_variance:,.0f}", R.tag_type(r.qty_variance > 0), R.rupees(r.var_value),
    ] for r in dtop.itertuples()]

    due = inv[~inv["has_payment"]].copy()

    def due_rows(d):
        d = d.sort_values(["overdue", "net_payable"], ascending=[False, False]).head(30)
        return [[R.esc(r.invoice_id), R.esc(r.vendor), R.esc(r.city),
                 R.cr(r.net_payable), _dt(r.due_date), R.tag_overdue(r.overdue, r.days_overdue)]
                for r in d.itertuples()]

    city_rows, vendor_rows = [], []
    if "city_name" in df:
        city = df.groupby("city_name").agg(
            po=("po_value", "sum"), invv=("invoice_value", "sum"),
            grn=("grn_value", "sum"), dn=("dn_value", "sum"),
            net=("net_payable", "sum"), disc=("is_discrepant", "sum"),
        ).sort_values("net", ascending=False).head(20).reset_index()
        city_rows = [[R.esc(r.city_name), R.cr(r.po), R.cr(r.invv), R.cr(r.grn),
                      R.cr(r.dn), R.cr(r.net), f"{int(r.disc)}"] for r in city.itertuples()]
    if "vendor_name" in df:
        ven = df.groupby("vendor_name").agg(
            invoices=("invoice_id", "nunique"), net=("net_payable", "sum"),
            dn=("dn_value", "sum"), disc=("is_discrepant", "sum"),
        ).sort_values("net", ascending=False).head(20).reset_index()
        vendor_rows = [[R.esc((r.vendor_name or "")[:52]), f"{int(r.invoices)}",
                        R.cr(r.net), R.cr(r.dn), f"{int(r.disc)}"] for r in ven.itertuples()]

    bc = inv.groupby("bucket").size()
    bv = inv.groupby("bucket")["net_payable"].sum()
    ac = due.groupby("aging").size()
    av = due.groupby("aging")["net_payable"].sum()

    ctx = dict(
        sources=", ".join(sorted(df["source"].dropna().unique())),
        date_range=f'{_dt(df["grn_date"].min())} – {_dt(df["grn_date"].max())}',
        n_inv=n_inv, n_lines=n_lines,
        as_of_str=pd.Timestamp(as_of).strftime("%Y-%m-%d"),
        data_note=data_note, upload_html=upload_html,
        gpos_absent=("GPOS" not in df["source"].unique()),
        po_v=float(df["po_value"].sum()), inv_v=float(df["invoice_value"].sum()),
        grn_v=float(df["grn_value"].sum()), dn_v=float(df["dn_value"].sum()),
        npay_v=float(df["net_payable"].sum()),
        pay_v=float(inv.loc[inv["has_payment"], "payment"].sum()),
        n_dn_lines=int((df["dn_quantity"] > TOL).sum()),
        bucket_counts={b: int(bc.get(b, 0)) for b in R.BUCKET_ORDER},
        bucket_vals={b: float(bv.get(b, 0.0)) for b in R.BUCKET_ORDER},
        aging_counts={b: int(ac.get(b, 0)) for b in R.AGING_ORDER},
        aging_vals={b: float(av.get(b, 0.0)) for b in R.AGING_ORDER},
        n_disc=int(len(disc)),
        short_u=float(df.loc[df.qty_variance > TOL, "qty_variance"].sum()),
        excess_u=float(-df.loc[df.qty_variance < -TOL, "qty_variance"].sum()),
        disc_rows=disc_rows,
        n_due=int(len(due)), n_overdue=int(due["overdue"].sum()),
        overdue_val=float(due.loc[due["overdue"], "net_payable"].sum()),
        n_clean=int((~due["has_concern"]).sum()), n_concern=int(due["has_concern"].sum()),
        due_clean_rows=due_rows(due[~due["has_concern"]]),
        due_concern_rows=due_rows(due[due["has_concern"]]),
        city_rows=city_rows, vendor_rows=vendor_rows,
    )
    return R.assemble(ctx)


def build(path, out_html, as_of=None):
    if as_of is None:
        as_of = pd.Timestamp.today().normalize()
    df = read_any(path)
    page = render_page(df, as_of, data_note=f"Source file: {os.path.basename(path)}")
    with open(out_html, "w") as f:
        f.write(page)
    d2 = enrich(df)
    inv = invoice_rollup(d2, as_of)
    return out_html, dict(lines=len(d2), invoices=len(inv),
                          discrepant=int(d2["is_discrepant"].sum()),
                          due=int((~inv["has_payment"]).sum()),
                          overdue=int(inv["overdue"].sum()))


if __name__ == "__main__":
    import sys
    inp = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/amul_recon/amul_invoice_extract.parquet")
    outp = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser(
        "~/amul_recon/amul_recon_dashboard.html")
    print("Wrote", *build(inp, outp))
