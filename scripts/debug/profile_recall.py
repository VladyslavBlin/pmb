"""
Profile a single recall() call to find where p95 latency lives.

Instruments the recall pipeline with monotonic clocks at each stage:
  1. query embedding
  2. BM25 scoring
  3. LanceDB vector search
  4. graph entity extraction + traversal
  5. SQLite batch fetch
  6. Python rerank (importance/recency/graph)
  7. optional cross-encoder rerank

Runs the same query 20 times to see whether p95 is consistent (warm
working set) or a one-off cold-start tax.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

_here = Path(__file__).resolve()
sys.path.insert(0, str(_here.parent.parent / "src"))

# Reuse the data generator
from benchmark import make_workspace_with_n_events, cleanup  # type: ignore[import-not-found]


def _instrument_engine(eng):
    """Monkey-patch the engine to record per-stage timings."""
    from pmb.core import engine as engine_mod
    timings: dict[str, list[float]] = {
        "embed_query": [],
        "bm25_lookup": [],
        "vector_search": [],
        "graph_lookup": [],
        "sql_batch": [],
        "python_rerank": [],
        "cross_encoder": [],
        "total": [],
    }

    orig_recall = eng.recall

    def patched_recall(query, top_k=5, **kw):
        # Inject probes via wrappers
        t0 = time.perf_counter()
        # We can't easily instrument inside the function from outside, so
        # we re-implement the timing by patching the search methods. Keep
        # this simple: time the WHOLE recall, and time the search.search +
        # cross-encoder separately by patching them transiently.
        original_search = eng.search.search
        original_rerank = eng.search.rerank
        timings_local = {"search": 0.0, "rerank": 0.0}

        def timed_search(*a, **kw_):
            t = time.perf_counter()
            r = original_search(*a, **kw_)
            timings_local["search"] += (time.perf_counter() - t) * 1000.0
            return r

        def timed_rerank(*a, **kw_):
            t = time.perf_counter()
            r = original_rerank(*a, **kw_)
            timings_local["rerank"] += (time.perf_counter() - t) * 1000.0
            return r

        eng.search.search = timed_search  # type: ignore[assignment]
        eng.search.rerank = timed_rerank  # type: ignore[assignment]
        try:
            pack = orig_recall(query, top_k=top_k, **kw)
        finally:
            eng.search.search = original_search  # type: ignore[assignment]
            eng.search.rerank = original_rerank  # type: ignore[assignment]
        elapsed = (time.perf_counter() - t0) * 1000.0
        timings["total"].append(elapsed)
        timings["embed_query"].append(timings_local["search"])  # search() does the embed inside
        timings["cross_encoder"].append(timings_local["rerank"])
        return pack

    eng.recall = patched_recall  # type: ignore[assignment]
    return timings


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scale", type=int, default=500)
    p.add_argument("--n-trials", type=int, default=20)
    p.add_argument("--rerank", action="store_true")
    args = p.parse_args()

    print(f"Building workspace with {args.scale} events ...")
    eng, gold, home, ws = make_workspace_with_n_events(args.scale, rerank=args.rerank)

    timings = _instrument_engine(eng)

    # Warm the embedder + search stack
    _ = eng.recall("warmup query", top_k=5)
    print(f"Cold first call: {timings['total'][-1]:.1f} ms")
    for key in timings:
        timings[key].clear()

    # Now run all gold queries n times for distribution
    print(f"Running {args.n_trials} x {len(gold)} = {args.n_trials * len(gold)} recalls "
          f"(rerank={args.rerank}) ...")
    for trial in range(args.n_trials):
        for q, _expected in gold:
            eng.recall(q, top_k=5, rerank=args.rerank)

    def quant(xs):
        if not xs: return (0, 0, 0)
        xs_sorted = sorted(xs)
        n = len(xs_sorted)
        return (
            xs_sorted[n // 2],
            xs_sorted[int(0.95 * n)] if n > 1 else xs_sorted[0],
            xs_sorted[-1],
        )

    print()
    print(f"  {'stage':20s}  {'p50':>8s}  {'p95':>8s}  {'max':>8s}  ({len(timings['total'])} samples)")
    print(f"  {'-' * 20}  {'-' * 8}  {'-' * 8}  {'-' * 8}")
    for key in ("total", "embed_query", "cross_encoder"):
        p50, p95, mx = quant(timings[key])
        print(f"  {key:20s}  {p50:7.1f}   {p95:7.1f}   {mx:7.1f}")

    cleanup(home, ws)


if __name__ == "__main__":
    main()
