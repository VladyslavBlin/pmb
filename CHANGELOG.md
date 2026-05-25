# Changelog

All notable changes to PMB are documented here.

## [0.1.0] - Initial public release

### Highlights

- Single-file install: `pip install -e .` exposes `pmb` on `$PATH`.
- 91.6 % evidence-recall@10 on LoCoMo full 10-conversation run (vs published mem0 / Letta / Zep numbers of 70-80 %).
- 90-140 ms p50 recall latency, 2 ms async writes.
- 13 semantic layers, 5 access paths, 3 tiers - every layer optional and configurable.
- Web dashboard + 5-tab terminal TUI for inspection.
- MCP server for Claude Code, Codex CLI, Cursor.
- Optional Ollama integration for fully-local LLM ops (consolidation, dedup verify, pmb-chat).

### Architecture decisions worth knowing

- **Local-first.** SQLite + LanceDB on disk; no daemon, no service, no network.
- **Lazy-by-default agent prompt.** PMB is OFF until an explicit trigger ("remember", personal fact, "what did I…"). General Q&A bypasses PMB entirely.
- **Async writes (fire-and-forget MCP).** `record_batch` returns in ~2 ms; embedding + LanceDB indexing happen in a background thread.
- **BM25 fallback for cold reads.** First `recall` after process start returns text-match results in ~100 ms while the sentence-transformers model finishes loading.
- **Multi-layer dedup.** L1 exact (5 ms) + L2 cosine ≥ 0.92 (50 ms) at write time; L2.5 borderline queue for LLM-verified merges; L3 dashboard review tab for ambiguous pairs.
- **Conservative defaults.** False-merge risk minimised: never merges across event types, threshold 0.92 leaves a safe gap.

### Tools and surfaces

- `pmb tui` - five tabs: Memory, Recall, Stats, Dedup, Tune
- `pmb dashboard` - HTTP dashboard on :8765 with Graph, Events, Performance, Duplicates, Recall Debug
- `pmb tune` - settings-only TUI (67 knobs)
- `pmb ollama` - health, install/use models, smoke test
- `pmb connect codex | claude | cursor` - wires MCP into the agent's config and rules file
- `pmb dedupe` / `pmb regraph` / `pmb prune-graph` - maintenance ops

### Settings (67 across 9 categories)

`recall` (36), `consolidate` (9), `dedup` (6), `chat` (5), `decay` (3), `embedding` (3), `feedback` (2), `ollama` (2), `mcp` (1). All exposed in TUI Tune tab, CLI `pmb config`, and dashboard.

### Known limits

- Single-machine. No cross-device sync.
- Cold first recall after process start blocks on model load if the BM25 fallback path is disabled.
- Code-AST extraction is Python-only (regex fallback for broken / partial code).
- Dashboard and TUI assume one workspace at a time.
