"""Latency benchmark: vector, BM25 and hybrid, unfiltered and filtered (state=open).

Run this FIRST after the namespace has been idle, so pass 1 catches cold queries:
    .venv/bin/python bench/latency.py

Method
- 500 queries x 6 configs, run one at a time (no concurrency), configs interleaved per query.
- Pass 1 runs right after idle (cold probe); pass 2 repeats the same queries (warm).
- Query requests match src/search.py exactly: top 10, same attributes; hybrid = multi_query
  (ANN + BM25, 50 candidates each) fused server-side with RRF.
- Query embedding is done before timing (local model, reported separately).
- client_ms = laptop wall time for the HTTP call (network included).
  server_ms = turbopuffer's server_total_ms. Each call is tagged with the response's cache_temperature.

Outputs: results/<date>-latency-raw.csv, results/<date>-latency.csv, results/<date>-latency-info.json
"""

import csv
import json
import time

from common import DATE, RESULTS, pct, query_set, run_info, ns, search

FILTERS = {"none": None, "open": ("state", "Eq", "open")}
MODES = ("vector", "bm25", "hybrid")
ATTRS = search.RESULT_ATTRS


def run_one(n, mode, flt, q, qv):
    extra = {"filters": flt} if flt else {}
    t0 = time.perf_counter()
    if mode == "vector":
        res = n.query(rank_by=("vector", "ANN", qv), limit=10, include_attributes=ATTRS, **extra)
        rows = res.rows or []
    elif mode == "bm25":
        res = n.query(rank_by=("text", "BM25", q), limit=10, include_attributes=ATTRS, **extra)
        rows = res.rows or []
    else:
        res = n.multi_query(
            queries=[
                {"rank_by": ("vector", "ANN", qv), "limit": search.FUSION_DEPTH, "include_attributes": ATTRS, **extra},
                {"rank_by": ("text", "BM25", q), "limit": search.FUSION_DEPTH, "include_attributes": ATTRS, **extra},
            ],
            rerank_by=("RRF",),
        )
        rows = (res.results[0].rows or [])[:10]
    client_ms = (time.perf_counter() - t0) * 1000
    p, b = res.performance, res.billing
    return {
        "client_ms": round(client_ms, 2),
        "server_ms": p.server_total_ms,
        "exec_ms": p.query_execution_ms,
        "cache_temperature": p.cache_temperature,
        "cache_hit_ratio": round(p.cache_hit_ratio, 3),
        "exhaustive_search_count": p.exhaustive_search_count,
        "bytes_queried": b.billable_logical_bytes_queried,
        "bytes_returned": b.billable_logical_bytes_returned,
        "rows": len(rows),
    }, res


def summarize(raw):
    out = []
    groups = [("cold", lambda r: r["cache_temperature"] == "cold"),
              ("warm", lambda r: r["pass"] == 2 and r["cache_temperature"] != "cold")]
    for mode in MODES:
        for fname in FILTERS:
            for label, keep in groups:
                rs = [r for r in raw if r["mode"] == mode and r["filter"] == fname and not r["error"] and keep(r)]
                c, s = [r["client_ms"] for r in rs], [r["server_ms"] for r in rs]
                out.append({
                    "mode": mode, "filter": fname, "temperature": label, "n": len(rs),
                    "client_p50_ms": round(pct(c, 50), 1), "client_p99_ms": round(pct(c, 99), 1),
                    "server_p50_ms": round(pct(s, 50), 1), "server_p99_ms": round(pct(s, 99), 1),
                    "client_max_ms": round(max(c), 1) if c else None,
                })
    return out


def main():
    queries, qvecs = query_set()
    t0 = time.perf_counter()
    for q in queries[:50]:
        search.embed_query(q)
    embed_ms = (time.perf_counter() - t0) * 1000 / 50

    info = run_info() | {"queries": len(queries), "embed_ms_per_query_local": round(embed_ms, 1)}
    n = ns()
    raw, first_dump = [], None
    for pass_no in (1, 2):
        t_pass = time.perf_counter()
        for i, (q, qv) in enumerate(zip(queries, qvecs.tolist())):
            for mode in MODES:
                for fname, flt in FILTERS.items():
                    rec = {"pass": pass_no, "query_idx": i, "mode": mode, "filter": fname, "error": ""}
                    try:
                        m, res = run_one(n, mode, flt, q, qv)
                        rec |= m
                        if first_dump is None:
                            first_dump = {"performance": res.performance.model_dump(), "billing": res.billing.model_dump()}
                    except Exception as e:  # keep going; errors are counted in the output
                        rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
                    raw.append(rec)
            if i % 50 == 49:
                cold = sum(r.get("cache_temperature") == "cold" for r in raw if r["pass"] == pass_no)
                print(f"pass {pass_no}: {i + 1}/{len(queries)} queries, {time.perf_counter() - t_pass:.0f}s, cold so far {cold}", flush=True)
        info[f"pass{pass_no}_seconds"] = round(time.perf_counter() - t_pass)

    info["first_response"] = first_dump
    info["errors"] = sum(bool(r["error"]) for r in raw)
    info["temperature_counts"] = {
        f"pass{p}": {t: sum(1 for r in raw if r["pass"] == p and r.get("cache_temperature") == t) for t in ("cold", "warm", "hot")}
        for p in (1, 2)
    }

    fields = ["pass", "query_idx", "mode", "filter", "client_ms", "server_ms", "exec_ms", "cache_temperature",
              "cache_hit_ratio", "exhaustive_search_count", "bytes_queried", "bytes_returned", "rows", "error"]
    with (RESULTS / f"{DATE}-latency-raw.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(raw)
    summary = summarize(raw)
    with (RESULTS / f"{DATE}-latency.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)
    (RESULTS / f"{DATE}-latency-info.json").write_text(json.dumps(info, indent=2, default=str))

    print(json.dumps({k: v for k, v in info.items() if k != "first_response"}, indent=2, default=str))
    for s in summary:
        print(s)


if __name__ == "__main__":
    main()
