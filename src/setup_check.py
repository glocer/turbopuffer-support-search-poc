"""Step 2 setup check: keys, embedding speed, and a "hello namespace" round trip.

Usage (from the project root):
    .venv/bin/python src/setup_check.py keys      # verify GitHub + turbopuffer keys
    .venv/bin/python src/setup_check.py fetch     # download 1k issues to data/
    .venv/bin/python src/setup_check.py embed     # time local embedding of the 1k issues
    .venv/bin/python src/setup_check.py hello     # upsert to poc-hello, run 3 queries, delete it

Keys are read from .env and never printed.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW_1K = DATA / "issues_1k.jsonl"
VEC_1K = DATA / "vectors_1k.npy"
REPO = "microsoft/vscode"
MODEL = "BAAI/bge-small-en-v1.5"
HELLO_NS = "poc-hello"
N = 1000
MAX_CHARS = 2000  # bge-small reads at most 512 tokens; longer text is wasted

load_dotenv(ROOT / ".env")


def gh_headers():
    return {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }


def tpuf_client():
    import turbopuffer

    return turbopuffer.Turbopuffer(
        api_key=os.environ["TURBOPUFFER_API_KEY"],
        region=os.environ["TURBOPUFFER_REGION"],
    )


def doc_text(issue):
    return (issue["title"] + "\n\n" + (issue["body"] or ""))[:MAX_CHARS]


def cmd_keys():
    r = requests.get("https://api.github.com/rate_limit", headers=gh_headers(), timeout=30)
    r.raise_for_status()
    core = r.json()["resources"]["core"]
    print(f"GitHub OK: rate limit {core['limit']}/h, {core['remaining']} remaining")

    tpuf = tpuf_client()
    names = [ns.id for ns in tpuf.namespaces()]
    print(f"turbopuffer OK: region {os.environ['TURBOPUFFER_REGION']}, {len(names)} namespaces {names}")


def cmd_fetch():
    DATA.mkdir(exist_ok=True)
    issues, page = [], 1
    while len(issues) < N:
        r = requests.get(
            f"https://api.github.com/repos/{REPO}/issues",
            headers=gh_headers(),
            params={"state": "all", "sort": "created", "direction": "desc", "per_page": 100, "page": page},
            timeout=60,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for it in batch:
            if "pull_request" in it:  # the issues API also returns PRs
                continue
            issues.append({
                "id": it["number"],
                "title": it["title"],
                "body": it["body"],
                "state": it["state"],
                "state_reason": it.get("state_reason"),
                "labels": [l["name"] for l in it["labels"]],
                "created_at": it["created_at"],
                "closed_at": it["closed_at"],
                "comments": it["comments"],
                "url": it["html_url"],
            })
        page += 1
    issues = issues[:N]
    with RAW_1K.open("w") as f:
        for it in issues:
            f.write(json.dumps(it) + "\n")
    print(f"Fetched {len(issues)} issues in {page - 1} pages -> {RAW_1K.relative_to(ROOT)}")
    print(f"Newest #{issues[0]['id']} ({issues[0]['created_at']}), oldest #{issues[-1]['id']} ({issues[-1]['created_at']})")


def load_1k():
    return [json.loads(line) for line in RAW_1K.open()]


def cmd_embed():
    import torch
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    t0 = time.perf_counter()
    model = SentenceTransformer(MODEL, device=device)
    load_s = time.perf_counter() - t0

    texts = [doc_text(it) for it in load_1k()]
    model.encode(texts[:32], batch_size=32)  # warm-up
    t0 = time.perf_counter()
    vecs = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    secs = time.perf_counter() - t0
    np.save(VEC_1K, vecs.astype(np.float32))

    rate = len(texts) / secs
    print(f"Device: {device} | model load {load_s:.1f}s | dims {vecs.shape[1]}")
    print(f"Embedded {len(texts)} issues in {secs:.1f}s ({rate:.0f} docs/s)")
    print(f"Extrapolated: 50k = {50_000 / rate / 60:.1f} min, 100k = {100_000 / rate / 60:.1f} min")


def cmd_hello():
    import torch
    from sentence_transformers import SentenceTransformer

    issues, vecs = load_1k(), np.load(VEC_1K)
    tpuf = tpuf_client()
    ns = tpuf.namespace(HELLO_NS)

    t0 = time.perf_counter()
    ns.write(
        upsert_columns={
            "id": [it["id"] for it in issues],
            "vector": vecs.tolist(),
            "title": [it["title"] for it in issues],
            "text": [doc_text(it) for it in issues],
            "state": [it["state"] for it in issues],
            "labels": [it["labels"] for it in issues],
            "created_at": [it["created_at"] for it in issues],
            "url": [it["url"] for it in issues],
        },
        distance_metric="cosine_distance",
        schema={
            "text": {"type": "string", "full_text_search": True},
            "title": {"type": "string", "full_text_search": True},
            "labels": {"type": "[]string"},
            "created_at": {"type": "datetime"},
        },
    )
    print(f"Upserted {len(issues)} docs to {HELLO_NS} in {time.perf_counter() - t0:.1f}s")

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(MODEL, device=device)
    question = "terminal freezes on startup"
    # bge models recommend this prefix for short retrieval queries
    qvec = model.encode("Represent this sentence for searching relevant passages: " + question,
                        normalize_embeddings=True).tolist()

    def show(label, res, ms):
        rows = res.rows or []
        print(f"\n{label} ({ms:.0f} ms, {len(rows)} hits)")
        for r in rows[:3]:
            print(f"  #{r.id} [{r['state']}] {r['title'][:80]}")

    attrs = ["title", "state", "url"]
    t0 = time.perf_counter()
    res = ns.query(rank_by=("vector", "ANN", qvec), limit=5, include_attributes=attrs)
    show(f"Vector: '{question}'", res, (time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    res = ns.query(rank_by=("text", "BM25", question), limit=5, include_attributes=attrs)
    show(f"BM25: '{question}'", res, (time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    res = ns.query(rank_by=("vector", "ANN", qvec), limit=5, include_attributes=attrs,
                   filters=("state", "Eq", "open"))
    show(f"Vector + filter state=open: '{question}'", res, (time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    res = ns.multi_query(
        queries=[
            {"rank_by": ("vector", "ANN", qvec), "limit": 20, "include_attributes": attrs},
            {"rank_by": ("text", "BM25", question), "limit": 20, "include_attributes": attrs},
        ],
        rerank_by=("RRF",),
    )
    ms = (time.perf_counter() - t0) * 1000
    print(f"\nHybrid (multi_query + server-side RRF) ({ms:.0f} ms): response type {type(res).__name__}")
    print(f"  fields: {[f for f in dir(res) if not f.startswith('_')][:15]}")

    ns.delete_all()
    remaining = [n.id for n in tpuf.namespaces()]
    print(f"\nDeleted {HELLO_NS}. Namespaces now: {remaining}")


if __name__ == "__main__":
    {"keys": cmd_keys, "fetch": cmd_fetch, "embed": cmd_embed, "hello": cmd_hello}[sys.argv[1]]()
