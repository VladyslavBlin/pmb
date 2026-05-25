## What this changes

A short summary.

## Why

The problem it solves or the feature it adds.

## How verified

- [ ] Tests pass (`pytest tests/test_graph.py tests/test_persons.py tests/test_goals_chains.py tests/test_fact_tree.py tests/test_recall_cache.py tests/test_config.py tests/test_redact.py tests/test_causation.py -q`)
- [ ] If recall accuracy could change: ran `python scripts/benchmark_locomo.py --n-conversations 3` and pasted the number
- [ ] If write-path latency could change: timed `engine.record_batch_async` with the smoke test in `scripts/bench_qa_scenarios.py`

## Notes for reviewer

Anything non-obvious about the implementation.
