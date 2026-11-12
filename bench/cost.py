"""Cost model: measured usage on 100k docs, extrapolated to 1M docs with turbopuffer's published prices.

    .venv/bin/python bench/cost.py      (run after bench/latency.py)

Inputs
- Namespace logical size from ns.metadata() (approx_logical_bytes).
- Billable bytes queried / returned per request, measured in results/<date>-latency-raw.csv.
- Published list prices (turbopuffer.com/pricing, checked 2026-11-11; page shows them as "≤", i.e. upper bounds):
  storage $0.33/GB-month, writes $2.00/GB, queries $1.00/PB queried.
  Writes: batch discount min(0.5, 0.2*(log10(KB)-1)); our ~1k-doc batches are several MB, so 50% off.
  Queries: billed bytes = max(1.28 GB, namespace size); marginal discount 80% for 32-128 GB, 96% above 128 GB.
  Data returned is billed at a flat per-GB rate the page does not publish, so it is reported as volume only.

Assumptions (stated in the output): linear size scaling with doc count, same doc shape as vscode issues,
1M hybrid queries per month, 10% of docs rewritten per month.

Outputs: results/<date>-cost.csv
"""

import csv
import json

import numpy as np

from common import DATE, RESULTS, ns, run_info

GB = 1e9
PRICE_STORAGE_GB_MONTH = 0.33
PRICE_WRITE_GB = 2.00
WRITE_BATCH_DISCOUNT = 0.50
PRICE_QUERY_PB = 1.00
MIN_QUERY_GB = 1.28
LAUNCH_MINIMUM = 16.00
QUERIES_PER_MONTH = 1_000_000
REWRITE_SHARE_PER_MONTH = 0.10
MEASURED_DOCS = 100_000


def billable_query_gb(size_gb):
    """Published marginal tiers on data queried, with the 1.28 GB minimum."""
    size_gb = max(size_gb, MIN_QUERY_GB)
    tiers = [(32, 1.0), (128, 0.2), (float("inf"), 0.04)]
    billed, lo = 0.0, 0.0
    for hi, rate in tiers:
        if size_gb > lo:
            billed += (min(size_gb, hi) - lo) * rate
        lo = hi
    return billed


def main():
    meta = ns().metadata()
    size_gb_100k = meta.approx_logical_bytes / GB
    bytes_per_doc = meta.approx_logical_bytes / meta.approx_row_count

    raw = list(csv.DictReader((RESULTS / f"{DATE}-latency-raw.csv").open()))
    measured = {}
    for mode in ("vector", "bm25", "hybrid"):
        rs = [r for r in raw if r["mode"] == mode and not r["error"]]
        measured[mode] = {
            "gb_queried_per_request": float(np.mean([int(r["bytes_queried"]) for r in rs])) / GB,
            "kb_returned_per_request": float(np.mean([int(r["bytes_returned"]) for r in rs])) / 1e3,
        }
    subqueries = {"vector": 1, "bm25": 1, "hybrid": 2}

    rows = []
    for docs in (MEASURED_DOCS, 1_000_000):
        size_gb = size_gb_100k * docs / MEASURED_DOCS
        storage = size_gb * PRICE_STORAGE_GB_MONTH
        initial_load = size_gb * PRICE_WRITE_GB * (1 - WRITE_BATCH_DISCOUNT)
        monthly_writes = initial_load * REWRITE_SHARE_PER_MONTH
        # Billable GB per hybrid request: measured at 100k, modelled from published tiers at 1M.
        if docs == MEASURED_DOCS:
            gb_per_query = measured["hybrid"]["gb_queried_per_request"]
            basis = "measured"
        else:
            gb_per_query = billable_query_gb(size_gb) * subqueries["hybrid"]
            basis = "modelled from published tiers"
        queries = gb_per_query * QUERIES_PER_MONTH / 1e6 * PRICE_QUERY_PB
        monthly = storage + monthly_writes + queries
        rows.append({
            "docs": docs,
            "logical_size_gb": round(size_gb, 3),
            "storage_usd_month": round(storage, 2),
            "initial_load_usd_one_time": round(initial_load, 2),
            "rewrites_usd_month": round(monthly_writes, 2),
            "hybrid_gb_billed_per_query": round(gb_per_query, 3),
            "query_gb_basis": basis,
            "queries_usd_month_at_1M_queries": round(queries, 2),
            "usage_usd_month": round(monthly, 2),
            "billed_usd_month_launch_plan": round(max(monthly, LAUNCH_MINIMUM), 2),
        })

    with (RESULTS / f"{DATE}-cost.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    info = run_info() | {
        "namespace_logical_bytes": meta.approx_logical_bytes,
        "namespace_rows": meta.approx_row_count,
        "bytes_per_doc": round(bytes_per_doc),
        "measured_per_request": measured,
        "assumptions": {
            "queries_per_month": QUERIES_PER_MONTH,
            "rewrite_share_per_month": REWRITE_SHARE_PER_MONTH,
            "write_batch_discount": WRITE_BATCH_DISCOUNT,
            "prices": "turbopuffer.com/pricing list prices checked 2026-11-11 (upper bounds); data-returned rate unpublished, excluded",
        },
    }
    (RESULTS / f"{DATE}-cost-info.json").write_text(json.dumps(info, indent=2))
    print(json.dumps(info, indent=2))
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
