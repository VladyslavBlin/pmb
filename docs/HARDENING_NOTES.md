# Hardening notes

Working document for technical-debt passes that are too risky to fold
into ordinary feature work. Each section is a **pre-inventory** for a
future targeted pass: list the candidates first, decide the policy
before changing code.

---

## Phase 1 - import hygiene (DONE)

- `pmb/__init__.py` and `pmb/core/__init__.py` switched to PEP 562 lazy
  attribute access. `import pmb` no longer drags in LanceDB,
  sentence-transformers, numpy, rank_bm25, fastmcp, yaml, torch or
  transformers. Measured: 2.4 ms (was ~14 s when Engine was eager).
- `tests/conftest.py` added with shared fixtures + sys.path fallback.
- `tests/test_lightweight_imports.py` added - 9 subprocess-based smoke
  tests that lock in the lazy-import property.
- All 88 core tests still pass.

---

## Phase 2 (proposed) - exception-handler audit

Today `src/pmb/` contains **144 bare `except Exception:` blocks across
32 files**. Most of these are deliberate. Some are not. Mass conversion
would either spam logs or hide the deliberate ones; the right approach
is a categorised audit before any change.

### Tier 1 - keep silent (do NOT touch)

These swallow errors **on purpose**. Adding logging here would either
flood stderr on every TUI/test run or expose real users to scary
warnings about expected conditions.

| File:line | Why it's deliberate |
|---|---|
| `cli/main.py:37-38` | stdout encoding reconfigure - Textual / pytest replace `sys.stdout` with objects that lack `.encoding` |
| `mcp/server.py:30-31` | Same stdout encoding reconfigure on MCP server boot |
| `mcp/perf.py:78-79` | Inline comment: `# never break MCP`. Telemetry must not interfere with tool calls |
| `mcp/server.py:1166-1169` | Inline comment: `# filter best-effort; if FastMCP API changes, all tools stay` |
| `reasoning/images.py:95-103, 136, 168` | Optional PIL / CLIP fall-throughs - they trigger when the user hasn't installed `[multimodal]` |
| `core/engine.py:3670-3671` | `Engine.close()` cleanup - tempfile and connection teardown |
| `signals/git.py:*` | `git` invocations that legitimately fail on non-git workspaces |
| `signals/session.py:74-75` | session-tracker write failures (best-effort feature) |
| `health/consolidate.py:*` | LLM-backend failures with explicit fallback to MockLLM |

**Policy:** leave these untouched. If a future contributor wants
visibility, they can wrap each one with a single-line debug log at
`logger.debug` level - this stays silent at production verbosity and
only surfaces when explicitly enabled.

### Tier 2 - candidate for `logger.debug` (medium-risk)

These have an existing inline comment indicating intent. Adding a debug
log preserves behaviour but gives you a trail when investigating
recall regressions.

| File:line | Suggested wording |
|---|---|
| `core/engine.py:681-682` | `# fall through to queue on failure` -> `log.debug("inline dedup failed, queueing for retry: %s", exc)` |
| `core/engine.py:797-798` | `# If we can't read vectors, sweep just returns empty` -> debug |
| `core/engine.py:2140-2141` | `# cache failure → normal recall` -> debug |
| `core/engine.py:1402-1403` | recall stage 4 fallback - context-dependent, read it first |

**Policy:** OK to convert one at a time, each behind its own commit
with a test that proves the fallback path still runs.

### Tier 3 - inspect each individually (high cardinality)

`core/engine.py` alone has 71 of these. They're inside the recall and
write pipelines and most of them protect cross-cutting features (graph
extraction, fact tree, person extraction) where a failure in one layer
must not break the others. **Do not bulk-edit.** Pick five at a time,
trace the call site, and decide.

The grep that produced this inventory:
```
rg "except\s+Exception\s*:\s*$" src/pmb/ --type py -c | sort -t: -k2 -n -r
```

Top three files by count:
- `core/engine.py` - 71
- `core/search.py` - 8
- `mcp/server.py` - 8

### Where the value is

Converting a handful of the **import-time** and **cache-failure**
blocks (Tier 2) into `logger.debug` calls would already give us most of
the observability win at near-zero risk. The remaining 130+ blocks can
wait until we have an actual incident pointing at one of them.

---

## Phase 3 (proposed) - remove `sys.path.insert` boilerplate from tests

39 test files currently start with:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
```

With `pip install -e .` and the new `tests/conftest.py`, this is
redundant. Removing it from all 39 files is a mechanical change but
spans a lot of diff. Recommended: do it when the project has external
contributors so the diff is small per PR.

**Risk:** none if `make test` keeps passing. The conftest already adds
`src/` to `sys.path` as a fallback for direct invocations.

---

## Phase 4 (proposed) - type annotation tightening

Several public methods on `Engine` have implicit `Any` parameters
(`metadata`, `config_overrides`, etc.). Adding `TypedDict` definitions
would help IDE users without touching runtime behaviour. Not urgent.

---

## Phase 5 (DONE in this pass) - lazy LanceDB

The single biggest user-facing cost was `import lancedb` itself:
**22 seconds** to import the module on Windows (pyarrow + transitive
deps). `Engine.__init__` triggered that import via `HybridSearch.__init__`
even for read-only CLI commands.

Fix landed in `src/pmb/core/search.py`:

- `HybridSearch` no longer connects to LanceDB in `__init__`. The
  connection and table objects are lazy properties (`_table` triggers
  `_lancedb().connect(...)` on first access).
- `HybridSearch.reload_bm25()` is no longer called from `Engine.__init__`.
  It's called lazily on the first `.search()` or `.add()` call, guarded
  by `self._bm25_reloaded`.
- New `HybridSearch.is_vector_index_loaded()` mirrors the existing
  `is_ready()` for callers that care about state.

Measured cost of `pmb stats` (read-only CLI):

| Before | After |
| ---: | ---: |
| ~14 s | ~1 s |

CLI commands that don't touch vectors (`stats`, `list`, `pin`, `forget`,
`config`, `--help`, `doctor`) now skip the LanceDB import entirely.

---

## Findings from `scripts/benchmarks/scenario_test.py`

A scenario-style integration test (`scripts/benchmarks/scenario_test.py`)
exercises PMB the way a real coding-assistant agent would. **17 of 18
queries pass** across 9 scenarios. The two genuine limitations the test
surfaced:

### 1. Concept-to-instance ranking without reranker

When the user asks about a category ("What **database** does Alex prefer?")
and the answer is an instance ("PostgreSQL"), and other competing facts
share the surface form ("Alex prefers tea over coffee", "Alex prefers vim
over emacs", "Alex prefers dark mode over light mode"), PMB **without
reranking** may put one of the surface-form-similar facts at top-1.

- Workaround today: callers should use `top_k=3` and let the agent pick
  the relevant one from a small candidate set. Most agents handle this
  naturally - the LLM reads three facts and picks the one about databases.
- Why we don't enable rerank: cross-encoder reranking improves this case
  but regresses LoCoMo evidence-recall by 17 points (see ablation).
- Possible future fix: a *gated* reranker - run it only when the top-3
  scores are within a small epsilon of each other. Doesn't help when
  reranker actively picks the wrong one (which is what happens on LoCoMo).
- Track as a v0.2 improvement under "conceptual matching".

### 2. Agent-side lazy gating (intentional non-feature)

PMB *always* returns top-K for any query. It does **not** detect that
"What is a JWT token?" is general knowledge and skip retrieval. That
decision lives in the agent's system prompt (`pmb connect` injects the
canonical rule block).

This is documented in the README ("the lazy-by-default gate lives in
the agent, not in PMB") - it's not a bug, it's a separation of concerns.
Custom-agent integrators need to replicate that rule block, or accept
that PMB will surface stored facts on unrelated questions.

---

## Phase 6 (proposed) - record_batch fan-out

After the lazy-LanceDB fix landed, `record_batch(100 items)` with a hot
embedding model still takes ~11 s. Where it goes:

| Step | Cost |
|---|---:|
| Pure SQLite (`append_many` 100 events) | 66 ms |
| Batched embed of 100 texts (already batched via Improvement X) | 425 ms |
| 100× graph indexing (entity extract + edge upserts) | ~5 s |
| 100× temporal / event_time / causation edges | ~2 s |
| 100× L1 dedup hash lookup (cheap individually) | ~1 s |
| 100× per-call SQLite connection open/close | ~1.2 s |

The fix would be a `record_batch_bulk(skip_dedup=True, defer_graph=True)`
mode for bulk imports (`pmb import-jsonl`, LoCoMo ingestion, migration
from another tool). Normal agent traffic (1-20 items per turn) is
unaffected and already fast.

**Note:** the cost is hidden in MCP usage because `record_batch` is
fire-and-forget there (returns in ~2 ms, work happens off-thread).
Sync callers (CLI, tests, bench scripts) see the full cost.

---

## Useful one-liners

Re-run the import-weight check by hand:
```bash
python -c "import time; t=time.perf_counter(); import pmb; \
           print(f'{(time.perf_counter()-t)*1000:.1f}ms')"
```

Re-run the recall ablation:
```bash
python scripts/benchmarks/ablation_full.py --n-conversations 3
```

Check what changed in the SCHEMA defaults since last release:
```bash
git log -p src/pmb/config.py
```
