# Scripts

Standalone utilities. Not shipped with the `pmb` package.

## `benchmarks/`

Reproducible measurement scripts. Run from repo root with the virtualenv active.

| Script | What it does |
|---|---|
| `benchmark_locomo.py` | Full LoCoMo benchmark (mem0/Letta/Zep comparable). See `--help`. |
| `bench_qa_scenarios.py` | 8 Q&A scenarios covering write/read/dedup/multilingual. |
| `bench_user_flow.py` | Simulates a real Codex-style user flow with timings. |
| `bench_full_flow.py` | Same with async writes + research-summary paths. |
| `benchmark.py` | Synthetic recall@K + latency over a generated workspace. |
| `benchmark_cross_event.py` | Cross-event retrieval (multi-hop bottleneck check). |
| `ab_compression.py`, `ab_compression_live.py` | A/B for `pmb-chat` compression policies. |

## `debug/`

One-off investigation scripts. Useful when a benchmark goes weird.

| Script | What it does |
|---|---|
| `analyze_failures.py`, `analyze_failures_all.py` | Bucket failed recall cases by category. |
| `debug_judge.py`, `debug_judge2.py` | LLM-judge investigations. |
| `debug_multihop.py` | Why a multi-hop question missed its evidence. |
| `profile_recall.py` | cProfile dump of one recall call. |

## `demos/`

Small examples for newcomers.

| Script | What it does |
|---|---|
| `e2e_test.py` | Full pmb usage in one file — write/read/inspect. |
| `demo_seed.py` | Seed a fresh workspace with example facts. |
| `demo_graph.py` | Build a small entity graph and dump stats. |

## Running

```bash
# Activate the virtualenv first
source .venv/bin/activate          # Windows: .venv\Scripts\activate

python scripts/benchmarks/benchmark_locomo.py --n-conversations 3
python scripts/demos/e2e_test.py
```
