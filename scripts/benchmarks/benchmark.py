"""
Accelerated PMB benchmark - synthetic but realistic.

Generates events across 5 thematic clusters with paraphrased gold queries,
then measures retrieval quality and latency at a few scale points.

This is NOT a substitute for real dogfooding. Synthetic gold-labels flatter
the system because the generator's notion of "relevant" is the same as the
evaluator's. Treat these numbers as a *lower bound on engineering quality*
(if recall@5 is bad here, it'll be worse on real data), not a guarantee.

Usage:
    python -m pmb.scripts.benchmark             # default scale points
    python -m pmb.scripts.benchmark --scales 100 500
    python -m pmb.scripts.benchmark --skip-consolidate
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

# Make `python pmb/scripts/benchmark.py` work without install
_here = Path(__file__).resolve()
sys.path.insert(0, str(_here.parent.parent / "src"))


# ----------------------------------------------------------------------
# Synthetic workload
# ----------------------------------------------------------------------


TOPICS = {
    "database": {
        "facts": [
            "Production runs Postgres 14 with WAL replication on AWS RDS.",
            "Switched primary store from MySQL to Postgres for write throughput.",
            "Postgres shared_buffers tuned to 4GB, effective_cache_size 12GB.",
            "Connection pool via pgbouncer with 50 connections per app pod.",
            "Read replicas at 2 nodes, async replication, ~50ms lag observed.",
            "Migrated user table to bigint ids — int4 was running out.",
            "Indexes on (org_id, created_at desc) — query plan now uses them.",
            "Vacuum schedule: autovacuum tuned, manual vacuum on weekends.",
            "Database backups via pgBackRest, daily full + hourly diff.",
            "DB connection string lives in env DATABASE_URL.",
        ],
        "queries": [
            ("which sql engine do we use in prod", "Postgres"),
            ("why did we move off mysql", "MySQL to Postgres"),
            ("what's the value of shared_buffers", "4GB"),
            ("how many db connections per pod", "50 connections"),
            ("how is database replication configured", "Read replicas"),
            ("what backup tool runs against the db", "pgBackRest"),
            ("where is the database connection string", "DATABASE_URL"),
            ("when does vacuum happen", "autovacuum"),
            ("what index speeds up our common query", "org_id"),
            ("how do connection pools work here", "pgbouncer"),
        ],
    },
    "frontend": {
        "facts": [
            "Frontend is Next.js 14 app router with server components.",
            "Styling via Tailwind 3, no CSS modules.",
            "State managed by Zustand stores per feature.",
            "Component library is shadcn/ui, copied not imported.",
            "Forms use react-hook-form with zod validation schemas.",
            "Image optimization via next/image with remote loader.",
            "Page transitions disabled — too much layout shift.",
            "Fonts: Inter variable, self-hosted to avoid CLS.",
            "All client interactions analytics-tracked via PostHog.",
            "Mobile breakpoint at 768px, design is mobile-first.",
        ],
        "queries": [
            ("what framework runs the ui", "Next.js"),
            ("which css system are we using", "Tailwind"),
            ("how is form validation done", "zod"),
            ("which state management library", "Zustand"),
            ("what handles form state in components", "react-hook-form"),
            ("where do client analytics events go", "PostHog"),
            ("how do we render images on the frontend", "next/image"),
            ("are we using a component library", "shadcn"),
            ("what font is used for the ui", "Inter"),
            ("which viewport is the design optimized for", "mobile-first"),
        ],
    },
    "auth": {
        "facts": [
            "Authentication is JWT with refresh tokens, 15min access expiry.",
            "Refresh tokens stored in httpOnly secure cookies, 7 day expiry.",
            "OAuth providers: Google and GitHub via NextAuth.",
            "Password hashing uses argon2id with 64MB memory cost.",
            "Sessions are stateless — only JWT validation server-side.",
            "Rate limit on login: 5 attempts per 15 minutes per IP.",
            "2FA via TOTP, optional, stored in users.totp_secret.",
            "Magic link login uses single-use signed tokens, 10min expiry.",
            "All auth events logged to audit_log table with actor info.",
            "Email verification required before first login.",
        ],
        "queries": [
            ("how do users sign in", "JWT"),
            ("where do refresh tokens live", "httpOnly"),
            ("which providers can users link", "Google and GitHub"),
            ("how are passwords hashed", "argon2"),
            ("what's the access token lifetime", "15min"),
            ("is two-factor available", "TOTP"),
            ("are there login rate limits", "5 attempts"),
            ("how does magic link work", "signed tokens"),
            ("where are auth events recorded", "audit_log"),
            ("must users verify email", "Email verification"),
        ],
    },
    "devops": {
        "facts": [
            "Deployment to AWS via Terraform-managed EKS cluster.",
            "Container builds use multi-stage Dockerfiles with distroless base.",
            "CI is GitHub Actions, deploys on tagged releases only.",
            "Secrets managed by AWS Secrets Manager, mounted via CSI driver.",
            "Observability stack: OpenTelemetry traces to Honeycomb.",
            "Logs ship to CloudWatch with 30 day retention.",
            "Database migrations run as a pre-deploy Job in k8s.",
            "Blue-green deploy via Argo Rollouts, manual promotion.",
            "DNS managed by Route53, ACM certs auto-renewed.",
            "WAF rules block known bad IPs at the ALB layer.",
        ],
        "queries": [
            ("where do we deploy our containers", "EKS"),
            ("how is infrastructure described", "Terraform"),
            ("what handles ci pipelines", "GitHub Actions"),
            ("where are secrets stored", "Secrets Manager"),
            ("how do we observe traces", "OpenTelemetry"),
            ("what tool ships logs", "CloudWatch"),
            ("how do db migrations run on deploy", "pre-deploy Job"),
            ("what is our deploy strategy", "Blue-green"),
            ("how is dns managed", "Route53"),
            ("what blocks malicious traffic", "WAF"),
        ],
    },
    "testing": {
        "facts": [
            "Backend tests run via pytest with pytest-asyncio for async paths.",
            "Frontend tests use Vitest with React Testing Library.",
            "End-to-end tests are Playwright, run nightly in CI.",
            "Coverage target is 70% line coverage, enforced via pytest-cov.",
            "Fixtures live in tests/fixtures/, loaded via conftest.py.",
            "Database tests use a separate test DB with truncate between tests.",
            "Snapshot tests live for components without complex props.",
            "Load tests use Locust, scenarios in tests/load/.",
            "Mutation tests run weekly with mutmut, slow on full repo.",
            "Test data factories use polyfactory for dataclass-based seeds.",
        ],
        "queries": [
            ("which test runner is used in python", "pytest"),
            ("how do we test react components", "Vitest"),
            ("what runs end to end tests", "Playwright"),
            ("what is our coverage target", "70%"),
            ("where are pytest fixtures defined", "conftest"),
            ("how do we test the database", "truncate"),
            ("what runs load testing", "Locust"),
            ("are mutation tests in use", "mutmut"),
            ("how do we generate seed data", "polyfactory"),
            ("how often do e2e tests run", "nightly"),
        ],
    },
}


# Noise: unrelated facts that shouldn't match any of the 50 queries
NOISE_FACTS = [
    "Team standup is at 10am UTC on weekdays.",
    "Office printer is on the 3rd floor near the kitchen.",
    "Conference budget is reset on January 1st.",
    "Code review SLA is 24 hours during work week.",
    "Sprint planning happens every other Monday.",
    "Onboarding doc lives in the team wiki under 'New hires'.",
    "Time off requests go through BambooHR.",
    "Demo day is the last Friday of the month.",
    "All hands meeting on first Tuesday at 4pm.",
    "Pull requests must have at least one approval before merge.",
    "We use Notion for product specs and meeting notes.",
    "Office wifi password rotates quarterly.",
    "Coffee machine maintenance happens every Friday.",
    "Engineering values doc was last updated in Q2.",
    "All public docs are in English.",
    "Slack channel #incidents pings on PagerDuty alerts.",
    "Quarterly business review on the last week of each quarter.",
    "New laptop policy: refresh every 3 years.",
    "Annual security training is mandatory for all staff.",
    "Vacation accrual rate is 1.67 days per month.",
]


def make_workspace_with_n_events(
    n_total: int, seed: int = 17, rerank: bool = False,
) -> tuple:
    """Build an isolated PMB workspace populated with n_total events.

    Returns (engine, gold_queries_list, tmp_home, tmp_ws).
    `gold_queries_list` = [(query_str, expected_content_substring), ...]
    """
    rng = random.Random(seed)
    tmp_home = Path(tempfile.mkdtemp(prefix=f"pmb-bench-{n_total}-"))
    tmp_ws = Path(tempfile.mkdtemp(prefix=f"pmb-ws-{n_total}-"))
    os.environ["PMB_HOME"] = str(tmp_home)

    from pmb.core.engine import Engine
    eng = Engine(
        cwd=tmp_ws, pmb_home=tmp_home,
        rerank_model="cross-encoder/ms-marco-MiniLM-L-6-v2" if rerank else None,
    )

    # Compose events: cycle through topics so embeddings cluster naturally
    all_facts: list[tuple[str, str]] = []  # (topic, text)
    for topic, payload in TOPICS.items():
        for f in payload["facts"]:
            all_facts.append((topic, f))
    # Add noise
    for nf in NOISE_FACTS:
        all_facts.append(("noise", nf))

    # If we need more events than topics provide, paraphrase by appending an idx
    while len(all_facts) < n_total:
        # cheap paraphrase: pick an existing fact and add a clause
        src_topic, src_text = rng.choice(all_facts)
        paraphrase = src_text + f" Update note {rng.randint(100, 999)}: still active."
        all_facts.append((src_topic, paraphrase))

    rng.shuffle(all_facts)
    all_facts = all_facts[:n_total]

    # Write events. Vary importance + spread timestamps to test recency rerank.
    now = time.time()
    for i, (topic, text) in enumerate(all_facts):
        # Use record_event to bypass redaction overhead for synthetic load
        eng.record_event(
            event_type="fact",
            content=text,
            importance=0.6 if topic != "noise" else 0.4,
            metadata={"_topic": topic, "_bench_idx": i},
        )

    # Build gold queries (50 per scale; same set across scales for comparability)
    gold: list[tuple[str, str]] = []
    for topic, payload in TOPICS.items():
        for q, expected in payload["queries"]:
            gold.append((q, expected))

    return eng, gold, tmp_home, tmp_ws


def measure_recall(
    eng, gold_queries, graph_boost: float, rerank: bool = False,
) -> dict:
    """Run all gold queries, compute IR metrics."""
    ranks: list[Optional[int]] = []
    latencies_ms: list[float] = []
    for q, expected_substr in gold_queries:
        t0 = time.perf_counter()
        pack = eng.recall(q, top_k=10, graph_boost=graph_boost, rerank=rerank)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        rank: Optional[int] = None
        for i, r in enumerate(pack.results, 1):
            if expected_substr.lower() in r.content.lower():
                rank = i
                break
        ranks.append(rank)

    found = [r for r in ranks if r is not None]
    def at_k(k):
        return sum(1 for r in found if r <= k) / len(ranks)
    mrr = sum(1.0 / r for r in found) / len(ranks) if ranks else 0.0
    p50 = statistics.median(latencies_ms) if latencies_ms else 0.0
    p95 = sorted(latencies_ms)[int(0.95 * len(latencies_ms))] if latencies_ms else 0.0
    return {
        "n_queries": len(gold_queries),
        "recall@1": round(at_k(1), 3),
        "recall@5": round(at_k(5), 3),
        "recall@10": round(at_k(10), 3),
        "mrr": round(mrr, 3),
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "n_missed": len(ranks) - len(found),
    }


def dir_size_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def cleanup(*paths: Path):
    import gc
    gc.collect()
    for p in paths:
        for _ in range(3):
            try:
                if p.exists():
                    shutil.rmtree(p)
                break
            except OSError:
                time.sleep(0.3)
                gc.collect()


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    return f"{n/1024/1024:.2f} MB"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--scales", type=int, nargs="+", default=[100, 500, 2000])
    p.add_argument("--graph-boosts", type=float, nargs="+", default=[0.0, 0.15, 0.3])
    p.add_argument("--rerank", action="store_true",
                   help="Also measure with cross-encoder rerank=True on top-25")
    p.add_argument("--skip-consolidate", action="store_true")
    args = p.parse_args()

    print("=" * 78)
    print(" PMB benchmark - synthetic 5-topic workload")
    print("=" * 78)
    print(f"Scales:        {args.scales}")
    print(f"Graph boosts:  {args.graph_boosts}")
    print(f"Gold queries:  {sum(len(t['queries']) for t in TOPICS.values())} (10 per topic x 5)")
    print()

    rows: list[dict] = []

    for n in args.scales:
        print(f"--- Building workspace with {n} events ---")
        t_build = time.time()
        eng, gold, home, ws = make_workspace_with_n_events(n, rerank=args.rerank)
        elapsed_build = time.time() - t_build
        storage = dir_size_bytes(home)
        print(f"  built in {elapsed_build:.1f}s, storage {fmt_size(storage)}")

        # Decide which rerank values to sweep
        rerank_values = [False, True] if args.rerank else [False]

        for boost in args.graph_boosts:
            for rr in rerank_values:
                t0 = time.time()
                metrics = measure_recall(
                    eng, gold, graph_boost=boost, rerank=rr,
                )
                t_recall = time.time() - t0
                metrics.update({"n_events": n, "graph_boost": boost,
                                "rerank": rr,
                                "build_seconds": round(elapsed_build, 1),
                                "storage_bytes": storage,
                                "eval_seconds": round(t_recall, 1)})
                rows.append(metrics)
                tag = " +RR" if rr else "    "
                print(
                    f"  boost={boost:.2f}{tag}  "
                    f"R@1={metrics['recall@1']:.2f}  "
                    f"R@5={metrics['recall@5']:.2f}  "
                    f"R@10={metrics['recall@10']:.2f}  "
                    f"MRR={metrics['mrr']:.2f}  "
                    f"p50={metrics['p50_ms']}ms  p95={metrics['p95_ms']}ms"
                )

        # Optional consolidation sanity check at the smallest scale
        if n == args.scales[0] and not args.skip_consolidate:
            print("  --- one real consolidation via claude CLI (best-effort) ---")
            try:
                result = eng.consolidate(
                    backend="auto",
                    similarity_threshold=0.4,
                    min_cluster_size=3,
                    max_clusters=2,
                )
                print(f"    clusters_found={result['n_clusters_found']}, "
                      f"consolidated={result['n_consolidated']}, "
                      f"archived={result['n_archived']}")
                for r in result["results"][:3]:
                    print(f"    [{r['cluster_size']} ev, sim={r['avg_similarity']:.2f}, "
                          f"conf={r['confidence']:.2f}] "
                          f"{'STORED: ' + r['summary'] if r['consolidated'] else 'SKIP: ' + r['reasoning'][:80]}")
            except Exception as e:
                print(f"    skipped: {e}")

        cleanup(home, ws)

    # Final summary
    print()
    print("=" * 78)
    print(" Summary")
    print("=" * 78)
    print(json.dumps(rows, indent=2))
    # Also persist to disk so the result isn't lost to stdout buffering
    out_path = Path(os.environ.get("PMB_BENCH_OUT", "pmb_bench_results.json"))
    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nResults written to {out_path}")


if __name__ == "__main__":
    main()
