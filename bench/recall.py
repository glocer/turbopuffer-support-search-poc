"""Recall@10 of turbopuffer ANN vs exact brute-force search over the same 100k vectors.

    .venv/bin/python bench/recall.py

Method
- Ground truth: exact cosine top 10 computed locally with numpy over data/vectors.npy (the exact
  vectors uploaded in Step 3), with the same filter applied as a mask.
- Candidate: turbopuffer ("vector", "ANN") top 10 with the same filter.
- recall@10 = |ANN top 10 ∩ exact top 10| / 10, averaged over 500 queries.
- Three filter cases: none (100%), state=open (~15.6% of docs), label "terminal" (~0.6% of docs).
  Selective filters are where approximate indexes usually lose recall.

Outputs: results/<date>-recall.csv, results/<date>-recall-raw.csv
"""

import csv
import json
import time

import numpy as np

from common import DATA, DATE, RESULTS, load_issues, ns, pct, query_set, run_info

K = 10


def main():
    queries, qvecs = query_set()
    issues = load_issues()
    vecs = np.load(DATA / "vectors.npy")
    ids = np.array([it["id"] for it in issues])
    assert len(ids) == len(vecs)

    cases = {
        "none": (None, np.ones(len(ids), bool)),
        "state=open": (("state", "Eq", "open"), np.array([it["state"] == "open" for it in issues])),
        "label=terminal": (("labels", "Contains", "terminal"), np.array(["terminal" in it["labels"] for it in issues])),
    }

    scores = qvecs @ vecs.T  # (500, 100k) cosine similarity, vectors are normalized
    n = ns()
    summary, raw = [], []
    for case, (flt, mask) in cases.items():
        masked = np.where(mask[None, :], scores, -np.inf)
        exact = ids[np.argsort(-masked, axis=1)[:, :K]]
        recalls, ms = [], []
        for i, qv in enumerate(qvecs.tolist()):
            extra = {"filters": flt} if flt else {}
            t0 = time.perf_counter()
            res = n.query(rank_by=("vector", "ANN", qv), limit=K, **extra)
            ms.append((time.perf_counter() - t0) * 1000)
            got = {r.id for r in res.rows or []}
            truth = set(exact[i][: min(K, int(mask.sum()))].tolist())
            r = len(got & truth) / len(truth)
            recalls.append(r)
            raw.append({"filter": case, "query_idx": i, "recall_at_10": r, "client_ms": round(ms[-1], 1)})
        rec = np.array(recalls)
        summary.append({
            "filter": case,
            "matching_docs": int(mask.sum()),
            "share_of_corpus_pct": round(mask.mean() * 100, 2),
            "queries": len(rec),
            "mean_recall_at_10": round(float(rec.mean()), 4),
            "p5_recall_at_10": round(pct(rec, 5), 2),
            "min_recall_at_10": round(float(rec.min()), 2),
            "pct_queries_perfect": round(float((rec == 1).mean()) * 100, 1),
            "client_p50_ms": round(pct(ms, 50), 1),
        })
        print(summary[-1], flush=True)

    with (RESULTS / f"{DATE}-recall.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)
    with (RESULTS / f"{DATE}-recall-raw.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(raw[0]))
        w.writeheader()
        w.writerows(raw)
    (RESULTS / f"{DATE}-recall-info.json").write_text(json.dumps(run_info() | {"k": K}, indent=2))


if __name__ == "__main__":
    main()
