"""Shared setup for the Step 5 benchmarks: query set, embeddings, client and run metadata.

The query set is 495 issue titles sampled at random (seed 42) plus the 5 README sample questions.
Query vectors are embedded once with the same model and prefix as src/search.py and cached in data/.
"""

import json
import platform
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import search  # noqa: E402  (reuses embed_query, the client and constants)

DATA = ROOT / "data"
RESULTS = ROOT / "results"
DATE = "2026-11-11"
N_QUERIES = 500
SEED = 42
QUERIES_FILE = DATA / "bench_queries.json"
QVECS_FILE = DATA / "bench_query_vectors.npy"

SAMPLE_QUESTIONS = [
    "Why does the integrated terminal freeze on startup?",
    'What causes "Extension host terminated unexpectedly"?',
    "Are there open issues about high CPU usage from the file watcher on macOS?",
    "How have people fixed Remote-SSH connection timeouts?",
    "What IntelliSense performance problems were reported in the last year of the dataset?",
]


def load_issues():
    return [json.loads(line) for line in (DATA / "issues.jsonl").open()]


def query_set():
    """Return (queries, query_vectors). Builds and caches them on first call."""
    if QUERIES_FILE.exists() and QVECS_FILE.exists():
        return json.loads(QUERIES_FILE.read_text()), np.load(QVECS_FILE)
    titles = sorted({it["title"].strip() for it in load_issues() if len(it["title"].split()) >= 3})
    random.Random(SEED).shuffle(titles)
    queries = titles[: N_QUERIES - len(SAMPLE_QUESTIONS)] + SAMPLE_QUESTIONS
    vecs = np.array([search.embed_query(q) for q in queries], dtype=np.float32)
    QUERIES_FILE.write_text(json.dumps(queries, indent=1))
    np.save(QVECS_FILE, vecs)
    return queries, vecs


def ns():
    return search._ns()


def run_info():
    """Context recorded next to every result (benchmark hygiene rule)."""
    chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    return {
        "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hardware": f"{chip}, macOS {platform.mac_ver()[0]}",
        "client_location": "Toronto (home network)",
        "region": "gcp-northamerica-northeast2",
        "namespace": search.NAMESPACE,
        "docs": 100_000,
        "embedding": f"{search.MODEL}, 384 dims, cosine",
        "sdk": "turbopuffer 3.1.0 (Python)",
    }


def pct(values, p):
    return float(np.percentile(np.asarray(values, dtype=float), p)) if len(values) else float("nan")
