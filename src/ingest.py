"""Load the 100k most recent microsoft/vscode issues into turbopuffer.

Stages (run in order from the project root; each one is resumable or cheap to rerun):
    .venv/bin/python src/ingest.py fetch    # GitHub -> data/pages/*.json (cursor pagination, resumable)
    .venv/bin/python src/ingest.py build    # pages -> data/issues.jsonl (PRs removed, newest 100k)
    .venv/bin/python src/ingest.py embed    # issues -> data/vectors.npy (local bge-small, MPS)
    .venv/bin/python src/ingest.py upsert   # vectors + attributes -> turbopuffer namespace
    .venv/bin/python src/ingest.py verify   # doc count + spot-check queries

One document per issue:
  - vector: title + first 2,000 chars of body (bge-small reads ~512 tokens)
  - text (BM25): title + body capped at 8,000 chars
Keys come from .env and are never printed.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PAGES = DATA / "pages"
STATE = DATA / "fetch_state.json"
ISSUES = DATA / "issues.jsonl"
VECTORS = DATA / "vectors.npy"

REPO = "microsoft/vscode"
TARGET = 100_000
MODEL = "BAAI/bge-small-en-v1.5"
NAMESPACE = "vscode-issues"
EMBED_CHARS = 2_000
BM25_CHARS = 8_000
BATCH = 1_000

load_dotenv(ROOT / ".env")


def gh_session():
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    })
    return s


def tpuf_namespace():
    import turbopuffer

    tpuf = turbopuffer.Turbopuffer(
        api_key=os.environ["TURBOPUFFER_API_KEY"],
        region=os.environ["TURBOPUFFER_REGION"],
    )
    return tpuf, tpuf.namespace(NAMESPACE)


# ---------- fetch ----------

def count_issues_in_pages():
    n = 0
    for p in sorted(PAGES.glob("page_*.json")):
        n += sum(1 for it in json.loads(p.read_text()) if "pull_request" not in it)
    return n


def cmd_fetch():
    PAGES.mkdir(parents=True, exist_ok=True)
    if STATE.exists():
        state = json.loads(STATE.read_text())
    else:
        state = {
            "next_url": f"https://api.github.com/repos/{REPO}/issues"
                        "?state=all&sort=created&direction=desc&per_page=100",
            "page": 0,
        }
    have = count_issues_in_pages()
    s = gh_session()
    t0 = time.time()

    while have < TARGET and state["next_url"]:
        for attempt in range(5):
            r = s.get(state["next_url"], timeout=60)
            if r.status_code == 200:
                break
            if r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0":
                wait = int(r.headers.get("x-ratelimit-reset", time.time() + 60)) - time.time() + 5
                print(f"Rate limited, sleeping {wait:.0f}s", flush=True)
                time.sleep(max(wait, 5))
            else:
                time.sleep(2 ** attempt)
        r.raise_for_status()

        state["page"] += 1
        batch = r.json()
        (PAGES / f"page_{state['page']:05d}.json").write_text(json.dumps(batch))
        have += sum(1 for it in batch if "pull_request" not in it)
        state["next_url"] = r.links.get("next", {}).get("url")
        STATE.write_text(json.dumps(state))

        if state["page"] % 50 == 0:
            print(f"page {state['page']}: {have:,} issues, {time.time() - t0:.0f}s, "
                  f"rate remaining {r.headers.get('x-ratelimit-remaining')}", flush=True)

    print(f"Done: {state['page']} pages, {have:,} issues cached in {PAGES.relative_to(ROOT)}")


# ---------- build ----------

def cmd_build():
    seen, issues = set(), []
    for p in sorted(PAGES.glob("page_*.json")):
        for it in json.loads(p.read_text()):
            if "pull_request" in it or it["number"] in seen:
                continue
            seen.add(it["number"])
            issues.append({
                "id": it["number"],
                "title": it["title"],
                "body": it["body"] or "",
                "state": it["state"],
                "state_reason": it.get("state_reason") or "",
                "labels": [l["name"] for l in it["labels"]],
                "created_at": it["created_at"],
                "closed_at": it["closed_at"],
                "comments": it["comments"],
                "url": it["html_url"],
            })
    issues.sort(key=lambda x: x["created_at"], reverse=True)
    issues = issues[:TARGET]
    with ISSUES.open("w") as f:
        for it in issues:
            f.write(json.dumps(it) + "\n")
    open_n = sum(it["state"] == "open" for it in issues)
    print(f"Built {len(issues):,} issues -> {ISSUES.relative_to(ROOT)}")
    print(f"Range: #{issues[-1]['id']} ({issues[-1]['created_at'][:10]}) to "
          f"#{issues[0]['id']} ({issues[0]['created_at'][:10]}); open {open_n:,}, closed {len(issues) - open_n:,}")


def load_issues():
    return [json.loads(line) for line in ISSUES.open()]


def embed_text(it):
    return (it["title"] + "\n\n" + it["body"])[:EMBED_CHARS]


def bm25_text(it):
    return (it["title"] + "\n\n" + it["body"])[:BM25_CHARS]


# ---------- embed ----------

def cmd_embed():
    import torch
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = SentenceTransformer(MODEL, device=device)
    texts = [embed_text(it) for it in load_issues()]
    t0 = time.perf_counter()
    vecs = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=True)
    secs = time.perf_counter() - t0
    np.save(VECTORS, vecs.astype(np.float32))
    print(f"Embedded {len(texts):,} issues on {device} in {secs / 60:.1f} min ({len(texts) / secs:.0f} docs/s)")


# ---------- upsert ----------

def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def cmd_upsert():
    issues, vecs = load_issues(), np.load(VECTORS)
    assert len(issues) == len(vecs), "issues and vectors are out of sync; rerun embed"
    _, ns = tpuf_namespace()
    schema = {
        "title": {"type": "string", "full_text_search": True},
        "text": {"type": "string", "full_text_search": True},
        "labels": {"type": "[]string"},
        "created_at": {"type": "datetime"},
        "closed_at": {"type": "datetime"},
        "url": {"type": "string", "filterable": False},
    }
    t0 = time.perf_counter()
    for i in range(0, len(issues), BATCH):
        chunk, v = issues[i:i + BATCH], vecs[i:i + BATCH]
        for attempt in range(5):
            try:
                ns.write(
                    upsert_columns={
                        "id": [it["id"] for it in chunk],
                        "vector": v.tolist(),
                        "title": [it["title"] for it in chunk],
                        "text": [bm25_text(it) for it in chunk],
                        "state": [it["state"] for it in chunk],
                        "state_reason": [it["state_reason"] for it in chunk],
                        "labels": [it["labels"] for it in chunk],
                        "created_at": [ts(it["created_at"]) for it in chunk],
                        "closed_at": [ts(it["closed_at"]) for it in chunk],
                        "comments": [it["comments"] for it in chunk],
                        "url": [it["url"] for it in chunk],
                    },
                    distance_metric="cosine_distance",
                    schema=schema,
                )
                break
            except Exception as e:  # retry transient API errors
                if attempt == 4:
                    raise
                print(f"batch {i // BATCH}: {type(e).__name__}, retrying", flush=True)
                time.sleep(2 ** attempt)
        if (i // BATCH) % 10 == 9:
            print(f"upserted {i + len(chunk):,} docs, {time.perf_counter() - t0:.0f}s", flush=True)
    print(f"Upserted {len(issues):,} docs to {NAMESPACE} in {time.perf_counter() - t0:.0f}s")


# ---------- verify ----------

def cmd_verify():
    import torch
    from sentence_transformers import SentenceTransformer

    _, ns = tpuf_namespace()
    res = ns.query(aggregate_by={"n": ("Count",)})
    print(f"Docs in {NAMESPACE}: {res.aggregations['n']:,}")

    model = SentenceTransformer(MODEL, device="mps" if torch.backends.mps.is_available() else "cpu")
    attrs = ["title", "state", "created_at"]
    for q in ["terminal freezes on startup", "Extension host terminated unexpectedly"]:
        qv = model.encode("Represent this sentence for searching relevant passages: " + q,
                          normalize_embeddings=True).tolist()
        for label, rank_by in [("vector", ("vector", "ANN", qv)), ("bm25", ("text", "BM25", q))]:
            t0 = time.perf_counter()
            r = ns.query(rank_by=rank_by, limit=3, include_attributes=attrs)
            print(f"\n[{label}] '{q}' ({(time.perf_counter() - t0) * 1000:.0f} ms)")
            for row in r.rows:
                print(f"  #{row.id} [{row['state']}] {row['title'][:85]}")


if __name__ == "__main__":
    {"fetch": cmd_fetch, "build": cmd_build, "embed": cmd_embed,
     "upsert": cmd_upsert, "verify": cmd_verify}[sys.argv[1]]()
