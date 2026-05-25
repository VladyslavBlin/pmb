"""
Cross-event benchmark — measures the case where a single answer requires
combining information from MULTIPLE events.

Example: "describe our backend stack" — a good answer cites Postgres + Redis
+ pgbouncer + replicas. No single event contains all of that.

For each query we list 2-3 substrings that should *all* appear somewhere in
the top-K results. Metric: hit_fraction = fraction of expected substrings
covered by top-K. Plus standard recall@K on the first expected substring.

Run before/after IDF graph weighting + with/without reranker to see which
config gives the best coverage on multi-event queries.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

_here = Path(__file__).resolve()
sys.path.insert(0, str(_here.parent.parent / "src"))


# Reuse the workload synthesizer from the single-event benchmark
from benchmark import (  # type: ignore[import-not-found]
    make_workspace_with_n_events, cleanup, dir_size_bytes, fmt_size,
)


# Cross-event queries: each query expects to surface 2-3 distinct substrings
# in top-K, drawn from different source events.
CROSS_EVENT_QUERIES: list[tuple[str, list[str]]] = [
    ("describe our backend storage stack",
        ["Postgres", "Redis", "pgbouncer"]),
    ("what is the database setup",
        ["Postgres 14", "replicas", "WAL"]),
    ("what's the auth strategy",
        ["JWT", "argon2", "Google and GitHub"]),
    ("how do users sign in and stay signed in",
        ["JWT", "refresh tokens", "httpOnly"]),
    ("tell me about devops",
        ["EKS", "Terraform", "GitHub Actions"]),
    ("how does ci and deploy work",
        ["GitHub Actions", "Blue-green", "pre-deploy Job"]),
    ("what does the frontend stack look like",
        ["Next.js", "Tailwind", "Zustand"]),
    ("describe the test pipeline",
        ["pytest", "Vitest", "Playwright"]),
    ("describe the auth tech",
        ["JWT", "argon2", "TOTP"]),
    ("describe our observability",
        ["OpenTelemetry", "CloudWatch", "Honeycomb"]),
]


def measure_cross_event(
    eng, queries, graph_boost: float, rerank: bool = False, top_k: int = 10,
) -> dict:
    coverages: list[float] = []
    found_first: list[bool] = []
    latencies_ms: list[float] = []
    misses: list[dict] = []

    for q, expected_subs in queries:
        t0 = time.perf_counter()
        pack = eng.recall(q, top_k=top_k, graph_boost=graph_boost, rerank=rerank)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

        all_text = " || ".join(r.content.lower() for r in pack.results)
        n_hits = sum(1 for sub in expected_subs if sub.lower() in all_text)
        coverage = n_hits / len(expected_subs)
        coverages.append(coverage)
        found_first.append(expected_subs[0].lower() in all_text)
        if coverage < 1.0:
            missing = [sub for sub in expected_subs if sub.lower() not in all_text]
            misses.append({"query": q, "coverage": coverage, "missing": missing})

    p50 = statistics.median(latencies_ms) if latencies_ms else 0.0
    p95 = sorted(latencies_ms)[int(0.95 * len(latencies_ms))] if latencies_ms else 0.0
    return {
        "n_queries": len(queries),
        "mean_coverage": round(sum(coverages) / max(1, len(coverages)), 3),
        "full_coverage_rate": round(sum(1 for c in coverages if c == 1.0) / max(1, len(coverages)), 3),
        "primary_recall": round(sum(found_first) / max(1, len(found_first)), 3),
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "n_misses": len(misses),
        "first_misses": misses[:3],
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--scales", type=int, nargs="+", default=[200])
    p.add_argument("--graph-boosts", type=float, nargs="+", default=[0.0, 0.15, 0.30])
    p.add_argument("--rerank", action="store_true")
    p.add_argument("--top-k", type=int, default=10)
    args = p.parse_args()

    print("=" * 78)
    print(" PMB cross-event benchmark")
    print("=" * 78)
    print(f"Scales:        {args.scales}")
    print(f"Graph boosts:  {args.graph_boosts}")
    print(f"Rerank:        {args.rerank}")
    print(f"Queries:       {len(CROSS_EVENT_QUERIES)} multi-event queries (2-3 subs each)")
    print(f"top_k:         {args.top_k}")
    print()

    rows: list[dict] = []

    for n in args.scales:
        print(f"--- Building workspace with {n} events ---")
        t_build = time.time()
        eng, _, home, ws = make_workspace_with_n_events(n, rerank=args.rerank)
        elapsed_build = time.time() - t_build
        storage = dir_size_bytes(home)
        print(f"  built in {elapsed_build:.1f}s, storage {fmt_size(storage)}")

        rerank_values = [False, True] if args.rerank else [False]
        for boost in args.graph_boosts:
            for rr in rerank_values:
                t0 = time.time()
                metrics = measure_cross_event(
                    eng, CROSS_EVENT_QUERIES,
                    graph_boost=boost, rerank=rr, top_k=args.top_k,
                )
                t_eval = time.time() - t0
                metrics.update({"n_events": n, "graph_boost": boost,
                                "rerank": rr,
                                "eval_seconds": round(t_eval, 1)})
                rows.append(metrics)
                tag = " +RR" if rr else "    "
                print(
                    f"  boost={boost:.2f}{tag}  "
                    f"mean_cov={metrics['mean_coverage']:.2f}  "
                    f"full_cov={metrics['full_coverage_rate']:.2f}  "
                    f"primary={metrics['primary_recall']:.2f}  "
                    f"p50={metrics['p50_ms']}ms"
                )
                if metrics["first_misses"]:
                    for m in metrics["first_misses"][:1]:
                        print(f"      e.g. miss: {m['query']!r} -> missing {m['missing']}")
        cleanup(home, ws)

    print()
    print("=" * 78)
    print(" Summary")
    print("=" * 78)
    print(json.dumps(rows, indent=2))
    out = Path(os.environ.get("PMB_BENCH_CE_OUT", "pmb_bench_cross_event.json"))
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nResults: {out}")


if __name__ == "__main__":
    main()
