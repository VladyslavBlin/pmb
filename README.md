<div align="center">

# 🧠 PMB - Personal Memory Brain

### Local-first persistent memory for AI coding agents.
### Beats mem0 / Letta / Zep on retrieval. Runs on your machine. No API keys.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/protocol-MCP-purple.svg)](https://modelcontextprotocol.io)
[![LoCoMo Recall](https://img.shields.io/badge/LoCoMo%20recall%4010-91.6%25-success.svg)](#benchmarks)
[![Latency](https://img.shields.io/badge/p50%20latency-90ms-success.svg)](#benchmarks)
[![Local first](https://img.shields.io/badge/local--first-✓-success.svg)](#privacy--security)

[Quickstart](#-quickstart) · [Benchmarks](#-benchmarks) · [Architecture](#-architecture) · [Configuration](#-configuration) · [Ollama setup](#-fully-local-with-ollama) · [FAQ](#-faq)

</div>

---

## 📖 The problem

Your AI coding agent forgets everything between sessions.

```
You (Monday):     "Remember - we picked Postgres over MySQL because of JSONB."
Agent (Tuesday):  "What database does your project use?"
You:              😡
```

You can paste context every time. You can keep notes in another tool. Or you can give your agent a real memory that survives across sessions, across tools, across machine restarts - without sending your data to anyone.

**That's PMB.**

```
You (Monday):     "Remember - we picked Postgres over MySQL because of JSONB."
                    └────────► PMB stores it (2 ms, on your disk)

You (Tuesday):    "What database do we use?"
                    └────────► PMB recalls (90 ms)
Agent:            "Postgres, picked over MySQL for JSONB support."
```

---

## ⚡ What makes PMB different

|                          | PMB                  | mem0       | Letta      | Zep        |
| :----------------------- | :------------------: | :--------: | :--------: | :--------: |
| **LoCoMo recall@10**     | **91.6 %**           | ~70-75 %   | ~75-80 %   | ~80 %      |
| **p50 latency**          | **~90 ms**           | 1-3 s      | 1-3 s      | 1-3 s      |
| **Runs locally**         | ✅                   | ❌         | ❌         | ❌         |
| **API key required**     | ❌                   | ✅         | ✅         | ✅         |
| **Per-call cost**        | $0                   | $$         | $$         | $$         |
| **Multilingual**         | ✅ (50+ langs)       | EN-mostly  | EN-mostly  | EN-mostly  |
| **MCP-native**           | ✅                   | ❌         | ⚠️         | ⚠️         |
| **Inspectable storage**  | SQLite + LanceDB     | proprietary | proprietary | proprietary |

> Numbers for mem0/Letta/Zep are from their own published benchmarks on LoCoMo.
> PMB numbers reproduce with `python scripts/benchmarks/benchmark_locomo.py --n-conversations 10`.

---

## 🚀 Quickstart

> **TL;DR**
> ```bash
> git clone <repo> pmb && cd pmb
> python -m venv .venv && source .venv/bin/activate
> pip install -e .
> pmb connect codex          # or claude / cursor
> # restart your agent and say "remember - I prefer Postgres"
> ```

### Detailed

**1. Install (Python 3.11+ required).**

```bash
git clone <repo-url> pmb
cd pmb
python -m venv .venv

# Activate
source .venv/bin/activate                  # Linux / macOS
.venv\Scripts\activate                     # Windows PowerShell

pip install -e .
```

You now have a `pmb` command on your `$PATH`. Sanity-check:

```bash
pmb doctor
pmb stats
```

**2. Hook up your AI agent.** One command per agent:

```bash
pmb connect claude        # Anthropic Claude Code
pmb connect codex         # OpenAI Codex CLI
pmb connect cursor        # Cursor
```

This writes an MCP server entry into the agent's config (e.g. `~/.codex/config.toml`)
and appends a tiny rule block to `AGENTS.md` / `CLAUDE.md`.

**3. Use your agent normally.** PMB activates only on explicit memory triggers:

| What you say                                | What PMB does                                    |
| :------------------------------------------ | :----------------------------------------------- |
| `"remember - my cat is allergic to chicken"` | record a pinned fact (importance 0.95)           |
| `"I work on the pmb-dashboard project"`     | record a fact about you/your project             |
| `"what did I research about Next.js?"`      | pulls last research summaries                    |
| `"why did we pick Postgres?"`               | recalls the project decision                     |
| `"what is JWT?"`                            | **does nothing** - general questions bypass PMB  |

**4. Inspect what's stored.**

```bash
pmb tui            # terminal UI: Memory · Recall · Stats · Dedup · Tune
pmb dashboard      # web UI on http://127.0.0.1:8765
```

---

## 📊 Benchmarks

### Retrieval accuracy on LoCoMo (full 10-conversation run)

```
mean evidence_recall@10:  91.6%

per-conversation:
   conv-26  ████████████████████████  95.5%
   conv-30  ███████████████████████   92.4%
   conv-41  ██████████████████████    90.7%
   conv-42  ██████████████████████    91.5%
   conv-43  ████████████████████████  94.2%
   conv-44  ████████████████████████  94.3%
   conv-47  █████████████████████     89.5%
   conv-48  ███████████████████████   92.5%
   conv-49  █████████████████████     87.2%
   conv-50  █████████████████████     88.7%
                                       ↑ all 10 ≥ 87%
```

LoCoMo is the standard benchmark from Snap Research (10 conversations, ~199 QA pairs each, multi-turn memory probing). Cited by mem0, Letta, and Zep in their papers.

### Latency breakdown

```
operation                              p50      p95      notes
─────────────────────────────────────────────────────────────────
record_batch (MCP roundtrip)            2 ms    11 ms    fire-and-forget
recall (warm)                          90 ms   200 ms    hybrid BM25+vector
recall (cold, BM25 fallback)          100 ms   200 ms    no blocking on model load
recall (cache hit, repeated query)      0 ms     5 ms    LRU cache
recent_activity / list_goals            3 ms    10 ms    pure SQL
pin / unpin                             5 ms    15 ms    single SQLite UPDATE
```

Reproduce locally:

```bash
python scripts/benchmarks/bench_qa_scenarios.py        # 8 scenarios, 13 checks
python scripts/benchmarks/benchmark_locomo.py --n-conversations 3   # quick smoke
python scripts/benchmarks/benchmark_locomo.py --n-conversations 10  # full run
```

---

## 🏛 Architecture

```
                      ┌─────────────────────────────────────────────┐
                      │              AI agent                       │
                      │   (Codex CLI · Claude Code · Cursor · …)    │
                      └──────────────────┬──────────────────────────┘
                                         │  MCP (Model Context Protocol)
                                         ▼
                      ┌─────────────────────────────────────────────┐
                      │  PMB MCP server  -  12 tools by default     │
                      │  record_batch · recall · pin · list_goals · │
                      │  recent_activity · what_just_happened · …   │
                      └──────────────────┬──────────────────────────┘
                                         │
                                         ▼
                ┌────────────────────────────────────────────────┐
                │  Engine                                        │
                │  ─────────────────────────────────────────     │
                │  READ pipeline (12 stages, all gated):         │
                │   embed → BM25 → vector → graph traversal      │
                │   → causation walk → arc expansion → PPR       │
                │   → reranker → adaptive decompose → fusion     │
                │                                                │
                │  WRITE path (≤ 2 ms MCP return):               │
                │   sync: SQLite insert                          │
                │   async: embed → LanceDB → entity graph        │
                │   dedup: L1 exact + L2 cosine + L2.5 LLM-verify│
                └────────────────────────────────────────────────┘
                                         │
                       ┌─────────────────┴──────────────────┐
                       ▼                                    ▼
              ┌─────────────────┐                  ┌────────────────┐
              │     SQLite       │                 │    LanceDB     │
              │  events          │                 │  vectors       │
              │  graph_entities  │                 │  CLIP (images) │
              │  graph_edges     │                 └────────────────┘
              │  mcp_calls       │
              │  dedup_pending   │
              │  predictive_cache│
              └─────────────────┘
```

### Thirteen semantic layers

| Layer                       | What                                                    | Where                                  |
| :-------------------------- | :------------------------------------------------------ | :------------------------------------- |
| 1. Raw events               | every fact/qa/decision the user records                 | `events` table                         |
| 2. Entities                 | tech names, files, concepts (regex-extracted)           | `graph_entities`                       |
| 3. Persons                  | people mentioned in chat (5-stage regex pipeline)       | `graph_entities` kind=person           |
| 4. Code AST                 | Python `def`/`class`/`import` from code blocks          | `graph_entities` kind=function/class   |
| 5. Co-occurrence graph      | "A & B were in the same event" edges                    | `graph_edges`                          |
| 6. Typed causation edges    | `references`, `supersedes`, `caused_by`                 | `event_edges`                          |
| 7. Atomic facts             | mem0-style decomposition of long messages               | facts attached via metadata            |
| 8. Fact trees               | one main event + N linked subfacts                      | metadata.parent_ulid                   |
| 9. Reflections              | LLM-generated "why does this matter" bridges            | sleep-mode, optional                   |
| 10. Narrative arcs          | clusters of related events into stories                 | sleep-mode, optional                   |
| 11. Bi-temporal index       | `event_time` vs `system_time` (when vs recorded)        | metadata.event_time                    |
| 12. Activity log            | working-memory tier (3-day decay)                       | event_type=activity                    |
| 13. Goals + milestone chains| explicit goals with status + tracked metric evolution   | event_type=goal/milestone              |

### Five access paths at recall time

```
                                                     ┌→ BM25 (lexical)
                                                     │
                                                     ├→ vector (cosine, multilingual)
   query  →  classify  →  pick weights  →  fuse  →   ┼→ graph traversal
                              ↑                      │
                              │                      ├→ Personalized PageRank
                       (adaptive routing)            │
                                                     └→ predictive cache (sleep-baked)
```

All five fire in parallel where independent, results are merged with importance × recency × graph weights.

### Three memory tiers (mem0-style + biological model)

```
                  tier        decay rate    use
                  ──────────  ────────────  ──────────────────────
                  working     3 days        recent edits, AI logs
                  episodic    30 days       facts, events
                  semantic    no decay      pinned, goals, identity
```

---

## 🛠 What gets stored, when (and what doesn't)

PMB is **lazy by default**. The AI only touches it on explicit triggers:

```
┌──────────────────────────────────────┬─────────────────────────────────────────┐
│ Trigger phrase                       │ PMB action                              │
├──────────────────────────────────────┼─────────────────────────────────────────┤
│ "remember / запомни / save / pin"    │ record + pin (importance 0.95)          │
│ "I work on X"  •  "we use Y"         │ record fact (importance 0.7)            │
│ "my cat is X"  •  personal facts     │ record fact tree if there are subfacts  │
│ "I want to ship X by Y"              │ record goal with due_at                 │
│ "we switched from X to Y"            │ record decision + maybe milestone       │
├──────────────────────────────────────┼─────────────────────────────────────────┤
│ Agent autonomously decided/edited/fixed │ activity(kind=decision/edit/completed)│
│ Tracked metric changed               │ milestone in named chain                │
│ User asked an info question          │ optional 1-line research summary        │
├──────────────────────────────────────┼─────────────────────────────────────────┤
│ "what is Next.js?" (general Q)       │ ❌ no save, no recall - answers directly│
│ "how do I write a for loop?"         │ ❌ no save, no recall                   │
│ Debugging / coding help              │ ❌ no save, no recall                   │
└──────────────────────────────────────┴─────────────────────────────────────────┘
```

This is the design - PMB is a memory for **you**, not a log of every Q&A.

---

## 💻 CLI reference

```
pmb stats                  workspace summary (event count, by type, graph stats)
pmb list                   last N events
pmb recall "<query>"       search memory from the shell
pmb fact "<content>"       record a standalone fact
pmb pin <ulid>             pin a memory (max importance, no decay)
pmb forget <ulid>          archive (reversible)
pmb feedback <ulid> useful|wrong   tune importance based on real outcomes

pmb tui                    full TUI: Memory · Recall · Stats · Dedup · Tune
pmb dashboard              web UI on :8765
pmb tune                   settings-only TUI (67 knobs)

pmb connect codex|claude|cursor    auto-wire MCP into the agent
pmb ollama status|use|test         local LLM integration

pmb dedupe                 one-shot duplicate sweep
pmb regraph                rebuild the entity graph from events
pmb prune-graph            drop weak co-occurrence edges
pmb reindex                re-embed all events (after model change)
pmb reflect                LLM-generated bridges (sleep-mode)
pmb arcs cluster|list|show narrative arcs

pmb config get|set|list    flat-key tuning from the shell
pmb doctor                 health check (model, DB, MCP, …)
```

---

## ⚙️ Configuration

**67 settings**, organised by category. Browse / edit them three ways:

```bash
pmb tui              # interactive TUI, tab [5] Tune
pmb tune             # settings-only TUI
pmb config set recall.top_k 10           # one-liner from shell
```

### What you'll most likely want to tune

| Setting                   | Default | What it does                                       |
| :------------------------ | :-----: | :------------------------------------------------- |
| `recall.top_k`            | 5       | results returned per query                          |
| `recall.bm25_weight`      | 0.5     | BM25 vs vector mix (0 = pure vector, 1 = pure BM25)|
| `recall.rerank`           | false   | add cross-encoder reranker (+50 ms, +precision)    |
| `recall.recency_half_life_days` | 30 | how fast recent events outweigh old ones           |
| `dedup.cosine_high`       | 0.92    | merge threshold (higher = more conservative)       |
| `dedup.enable_semantic`   | true    | turn off to rely on exact-text dedup only          |
| `embedding.backend`       | sentence-transformers | switch to `fastembed` for 3-5× faster embed |
| `mcp.record_batch_async`  | true    | fire-and-forget MCP writes                         |
| `decay.factor_per_day`    | 0.985   | set to 1.0 to disable forgetting                   |
| `consolidate.auto_trigger`| false   | turn on for nightly LLM consolidation              |

Full list: `pmb config list` or open the TUI Tune tab.

---

## 🦙 Fully local with Ollama

PMB doesn't *need* a cloud LLM, ever. The vector embedder is local (sentence-transformers). The optional LLM-powered ops (consolidation, dedup verification, the `pmb-chat` standalone loop) can all run through Ollama:

```bash
# 1. Install Ollama → https://ollama.com/download
ollama serve &
ollama pull llama3.1:8b              # ~5 GB, balanced default

# 2. Point PMB at it
pmb ollama use balanced              # configures all LLM-using ops
pmb ollama status                    # health check
pmb ollama test                      # 1-shot PONG smoke test
```

Now PMB is **100% offline**:

```
┌────────────────────────────┬─────────────────────────────┐
│  Operation                 │  Runs where?                 │
├────────────────────────────┼─────────────────────────────┤
│  Embedding                 │  your machine (CPU/GPU)      │
│  Vector + BM25 + graph     │  your machine                │
│  record_batch / recall     │  your machine                │
│  Dedup L1+L2               │  your machine                │
│  Dedup L2.5 (LLM verify)   │  your machine via Ollama     │
│  Consolidation             │  your machine via Ollama     │
│  pmb-chat                  │  your machine via Ollama     │
└────────────────────────────┴─────────────────────────────┘
```

Full guide: [`docs/SETUP_OLLAMA.md`](docs/SETUP_OLLAMA.md).

---

## 🔒 Privacy & security

- **Local only.** PMB itself doesn't open any network connections. All data sits in `~/.pmb/`.
- **No telemetry.** PMB doesn't phone home, has no analytics, no usage reporting.
- **The agent has its own networking.** Claude Code talks to api.anthropic.com, Codex to OpenAI, etc. PMB has no control over that - but PMB doesn't add a second channel.
- **Secret redaction.** `record_fact` runs a regex scrubber over content (API keys, tokens, AWS/GCP creds patterns). It's not bulletproof; don't deliberately feed PMB secrets.
- **Single-user model.** Anyone with read access to `~/.pmb/workspaces/<id>/events.sqlite` can read all your memory.

See [`SECURITY.md`](SECURITY.md) for the full threat model and vulnerability reporting.

---

## 🗺 Roadmap

### Shipped in v0.1
- [x] 13-layer semantic engine, 5 access paths, 3 tiers
- [x] MCP server with 50+ tools (12 exposed by default)
- [x] Web dashboard + 5-tab TUI
- [x] Async fire-and-forget writes (~2 ms MCP response)
- [x] BM25 fallback for cold reads (no blocking model load)
- [x] Multi-layer dedup (exact + cosine + LLM-verify)
- [x] Cross-lingual recall (multilingual MiniLM by default)
- [x] Per-MCP-call performance tracking
- [x] Ollama backend for fully-local LLM ops
- [x] 91.6 % LoCoMo evidence-recall@10

### Considering for v0.2
- [ ] Persistent daemon mode - `pmb daemon start`, every Codex session connects to a hot process (no cold start)
- [ ] PyPI publication - `pip install pmb`
- [ ] Web dashboard: workspace switcher, settings tab
- [ ] LLM-judge benchmark wired into CI for regression catching
- [ ] Auto-backup / export-import commands
- [ ] First-class macOS / Linux testing (Windows is the primary CI target today)

### Not planned
- Multi-user, multi-device, cloud sync. PMB is single-machine on purpose.
- A new GUI framework. The dashboard stays vanilla HTML+JS; the TUI stays Textual.
- Plugin marketplaces, model hubs, third-party tool stores.

---

## ❓ FAQ

<details>
<summary><b>How is this different from just pasting context every time?</b></summary>

Pasting works for one or two facts. PMB survives across **every** session of **every** agent that supports MCP, indefinitely. And it surfaces context you forgot you ever mentioned.
</details>

<details>
<summary><b>Why not just use mem0 / Letta / Zep?</b></summary>

- They're cloud services with per-call costs and rate limits.
- They send your conversations to their servers.
- On the public LoCoMo benchmark, PMB recalls more correctly (91.6 % vs 70-80 %).
- They're 10-30× slower per call.

If those trade-offs are acceptable for you, by all means use them. PMB is for people who want local + fast + free + accurate, in that order.
</details>

<details>
<summary><b>Will PMB slow down my AI agent?</b></summary>

Writes: no - MCP returns in ~2 ms (fire-and-forget). Reads: ~90 ms warm, ~100 ms cold (BM25 fallback). The agent's own LLM thinking is the dominant latency in any chat turn, by 10-100×.

If you suspect PMB specifically is slow, open `pmb tui` → tab [3] Stats. It shows the actual per-call timings.
</details>

<details>
<summary><b>What if I use multiple projects?</b></summary>

PMB defaults to one global workspace (your personal memory follows you across projects). If you want isolation per project, drop a `.pmb/workspace.yaml` in each project root with a unique `id` - PMB picks it up automatically.
</details>

<details>
<summary><b>Does it work with [my agent]?</b></summary>

Anything that speaks MCP: Claude Code, Codex CLI, Cursor, and any future tool that adopts the protocol. For custom agents (Ollama wrappers, your own loop) see `docs/SETUP_OLLAMA.md` for the call patterns.
</details>

<details>
<summary><b>Can I see what was stored?</b></summary>

Three ways: `pmb tui` (Memory tab), `pmb dashboard` (Events), or just `sqlite3 ~/.pmb/workspaces/<id>/events.sqlite` and run SQL. The store is plain SQLite - nothing proprietary.
</details>

<details>
<summary><b>How do I delete a memory?</b></summary>

`pmb forget <ulid>` archives it (reversible). To purge entirely, open the SQLite file and `DELETE` the row, or use `pmb dedupe --undo` to restore something you didn't mean to merge.
</details>

<details>
<summary><b>What if my workspace gets corrupted?</b></summary>

SQLite is robust; the `mcp_calls` and `events` tables are append-mostly. Worst case, copy `~/.pmb/workspaces/<id>/` and start fresh - nothing else depends on this state.

Auto-backup is on the v0.2 roadmap.
</details>

<details>
<summary><b>Why "Personal Memory Brain"?</b></summary>

Because it's personal (not a team product), it stores memory (not just chat history), and "brain" because the architecture is loosely inspired by working memory → episodic → semantic transitions in actual neuroscience. The marketing department was overruled.
</details>

---

## 🤝 Contributing

PRs welcome. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) first - it explains where things go, what's in scope, and what's not.

In short:
- One concern per PR.
- New write-path code must stay sub-100 ms on warm cache.
- If recall accuracy could change, include a LoCoMo number with the PR.

---

## 📄 License

**Apache License 2.0** - see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

Same license as **mem0**, **Letta**, and **Zep** community editions. Apache 2.0 includes an explicit patent grant from every contributor - important for AI/ML projects where patent ambiguity can otherwise scare off enterprise users.

If you use PMB in a paper or product, citation is appreciated but not required - see [`CITATION.cff`](CITATION.cff).

---

<div align="center">

**Built to forget less.**

[⬆ back to top](#-pmb--personal-memory-brain)

</div>
