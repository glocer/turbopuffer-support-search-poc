"""Cold-query probe. Run only after the namespace has been idle for a long time (e.g. overnight):
    .venv/bin/python bench/cold.py

turbopuffer has no switch to evict a namespace from cache, so cold latency can only be observed
after idle time. Each run sends the first queries after idle (6 configs x 5 queries, configs
interleaved, sequential) and appends every call, tagged with cache_temperature, to
results/<date>-cold-probes.csv. Repeat on several days to build up cold samples for p50/p99.
Each run uses a different slice of the query set so no query is repeated across runs.
"""

import csv
import time
from datetime import datetime, timezone

from common import DATE, RESULTS, ns, query_set
from latency import FILTERS, MODES, run_one

PER_RUN = 5
OUT = RESULTS / f"{DATE}-cold-probes.csv"
FIELDS = ["run_at_utc", "seq", "query_idx", "mode", "filter", "client_ms", "server_ms", "exec_ms",
          "cache_temperature", "cache_hit_ratio", "error"]


def main():
    queries, qvecs = query_set()
    prior = list(csv.DictReader(OUT.open())) if OUT.exists() else []
    runs = len({r["run_at_utc"] for r in prior})
    start = (runs * PER_RUN) % len(queries)
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n, out, seq = ns(), [], 0
    for i in range(start, start + PER_RUN):
        for mode in MODES:
            for fname, flt in FILTERS.items():
                rec = {"run_at_utc": run_at, "seq": seq, "query_idx": i, "mode": mode, "filter": fname, "error": ""}
                try:
                    m, _ = run_one(n, mode, flt, queries[i], qvecs[i].tolist())
                    rec |= {k: m[k] for k in FIELDS if k in m}
                except Exception as e:
                    rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
                out.append(rec)
                seq += 1
                print(f"{seq:>2} {mode:<6} {fname:<4} {rec.get('client_ms', '-'):>8} ms  {rec.get('cache_temperature', rec['error'])}")
    with OUT.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not prior:
            w.writeheader()
        w.writerows(out)
    cold = [r for r in out if r.get("cache_temperature") == "cold"]
    print(f"\nRun {runs + 1}: {len(cold)} of {len(out)} calls cold. Appended to {OUT.name}")


if __name__ == "__main__":
    main()
