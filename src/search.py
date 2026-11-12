"""Search the vscode-issues namespace in turbopuffer: vector, BM25 or hybrid, with filters.

CLI (from the project root):
    .venv/bin/python src/search.py "terminal freezes on startup"
    .venv/bin/python src/search.py "file watcher high CPU" --mode bm25 --state open --label macos
    .venv/bin/python src/search.py "IntelliSense slow" --after 2025-11-11

Keys come from .env and are never printed.
"""

import argparse
import os
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

NAMESPACE = "vscode-issues"
MODEL = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # bge retrieval prefix
FUSION_DEPTH = 50  # candidates per sub-query before RRF fusion
RESULT_ATTRS = ["title", "state", "state_reason", "labels", "created_at", "url", "text"]
MODES = ("hybrid", "vector", "bm25")


@lru_cache(maxsize=1)
def _model():
    import torch
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    return SentenceTransformer(MODEL, device=device)


@lru_cache(maxsize=1)
def _ns():
    import turbopuffer

    tpuf = turbopuffer.Turbopuffer(
        api_key=os.environ["TURBOPUFFER_API_KEY"],
        region=os.environ["TURBOPUFFER_REGION"],
    )
    return tpuf.namespace(NAMESPACE)


def embed_query(text):
    return _model().encode(QUERY_PREFIX + text, normalize_embeddings=True).tolist()


def _date(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def build_filters(state=None, state_reason=None, labels=None, created_after=None, created_before=None):
    """Return a turbopuffer filter expression, or None if no filters are set."""
    conds = []
    if state:
        conds.append(("state", "Eq", state))
    if state_reason:
        conds.append(("state_reason", "Eq", state_reason))
    for label in labels or []:
        conds.append(("labels", "Contains", label))
    if created_after:
        conds.append(("created_at", "Gte", _date(created_after)))
    if created_before:
        conds.append(("created_at", "Lt", _date(created_before)))
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else ("And", conds)


def _snippet(text, title, n=300):
    body = text[len(title):].strip() if text.startswith(title) else text
    body = " ".join(body.split())
    return body[:n] + ("…" if len(body) > n else "")


def _get(row, key, default=None):
    """Read an attribute safely. turbopuffer omits null attributes from rows (e.g. closed_at on open tickets)."""
    return getattr(row, key, default)


def _to_result(row):
    created = _get(row, "created_at")
    title = _get(row, "title", "")
    return {
        "id": row.id,
        "title": title,
        "state": _get(row, "state"),
        "state_reason": _get(row, "state_reason") or None,
        "labels": _get(row, "labels") or [],
        "created_at": created.date().isoformat() if hasattr(created, "date") else str(created or "")[:10],
        "url": _get(row, "url"),
        "snippet": _snippet(_get(row, "text") or "", title),
    }


def search(query, mode="hybrid", limit=10, **filter_args):
    """Search tickets. Returns (results, timing_ms). filter_args go to build_filters()."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    filters = build_filters(**filter_args)
    extra = {"filters": filters} if filters else {}
    ns = _ns()

    qvec = embed_query(query) if mode in ("hybrid", "vector") else None
    t0 = time.perf_counter()
    if mode == "vector":
        rows = ns.query(rank_by=("vector", "ANN", qvec), limit=limit,
                        include_attributes=RESULT_ATTRS, **extra).rows
    elif mode == "bm25":
        rows = ns.query(rank_by=("text", "BM25", query), limit=limit,
                        include_attributes=RESULT_ATTRS, **extra).rows
    else:
        res = ns.multi_query(
            queries=[
                {"rank_by": ("vector", "ANN", qvec), "limit": FUSION_DEPTH,
                 "include_attributes": RESULT_ATTRS, **extra},
                {"rank_by": ("text", "BM25", query), "limit": FUSION_DEPTH,
                 "include_attributes": RESULT_ATTRS, **extra},
            ],
            rerank_by=("RRF",),
        )
        rows = res.results[0].rows[:limit]
    ms = (time.perf_counter() - t0) * 1000
    return [_to_result(r) for r in rows or []], ms


def get_ticket(ticket_id):
    """Fetch one ticket's stored attributes and text (title + body, up to 8,000 chars)."""
    rows = _ns().query(filters=("id", "Eq", int(ticket_id)), limit=1,
                       include_attributes=RESULT_ATTRS + ["closed_at", "comments"]).rows
    if not rows:
        return None
    r = rows[0]
    out = _to_result(r)
    out.pop("snippet")
    closed = _get(r, "closed_at")
    out["closed_at"] = str(closed)[:10] if closed else None  # API returns ISO strings
    out["comments"] = _get(r, "comments")
    out["text"] = _get(r, "text", "")
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query")
    p.add_argument("--mode", choices=MODES, default="hybrid")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--state", choices=["open", "closed"])
    p.add_argument("--reason", dest="state_reason", choices=["completed", "not_planned", "duplicate"])
    p.add_argument("--label", dest="labels", action="append")
    p.add_argument("--after", dest="created_after", help="YYYY-MM-DD")
    p.add_argument("--before", dest="created_before", help="YYYY-MM-DD")
    a = p.parse_args()

    results, ms = search(a.query, a.mode, a.limit, state=a.state, state_reason=a.state_reason,
                         labels=a.labels, created_after=a.created_after, created_before=a.created_before)
    print(f"[{a.mode}] '{a.query}' -> {len(results)} results in {ms:.0f} ms")
    for r in results:
        labels = ",".join(r["labels"][:3])
        print(f"  #{r['id']} [{r['state']}{'/' + r['state_reason'] if r['state_reason'] else ''}] "
              f"{r['created_at']} {r['title'][:75]}  ({labels})")


if __name__ == "__main__":
    main()
