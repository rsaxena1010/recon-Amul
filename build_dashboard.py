#!/usr/bin/env python3
"""
Static HTML dashboard export for the notebook.

Thin shim over the canonical analysis in web/dashboard_core.py (the same module
the hosted web app uses), so notebook and app never drift.

Usage:
    python build_dashboard.py [INPUT_PARQUET] [OUTPUT_HTML]
    from build_dashboard import build
"""
import os
import sys

# Make web/dashboard_core.py importable regardless of cwd.
_WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
if _WEB not in sys.path:
    sys.path.insert(0, _WEB)

from dashboard_core import build, render_page, read_any, enrich, invoice_rollup  # noqa: E402,F401

if __name__ == "__main__":
    default_in = os.path.expanduser("~/amul_recon/amul_invoice_extract.parquet")
    default_out = os.path.expanduser("~/amul_recon/amul_recon_dashboard.html")
    inp = sys.argv[1] if len(sys.argv) > 1 else default_in
    outp = sys.argv[2] if len(sys.argv) > 2 else default_out
    path, stats = build(inp, outp)
    print("Wrote dashboard:", path)
    print("Stats:", stats)
