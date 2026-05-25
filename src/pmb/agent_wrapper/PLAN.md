# Agent-wrapper - concrete N-week plan

This package is the **scaffold** for what the original pitch called "mechanic 1: smart in-session compression". Doing it properly is a multi-week project because it's no longer a memory tool - it's a thin client around the Claude API that competes (in scope) with Claude Code / Cursor / Cline / Aider.

This document tells you exactly what's done and exactly what's left.

---

## Today (what exists in code)

- [x] `budget.py` - token budget with `should_compact()` trigger
- [x] `policy.py` - `CompressionPolicy` protocol + `DropOldestNarrative` baseline
- [x] `loop.py` - `AgentLoop` that:
  - auto-detects PMB workspace from cwd
  - calls `engine.recall(user_msg)` on first turn, injects into system prompt
  - talks to Anthropic via the SDK
  - persists every turn via `engine.remember(user, assistant)`
  - runs compaction policy before each turn
  - has a basic REPL with `/exit`, `/stats`, `/recall <q>` slash-commands
- [x] `__main__.py` - `python -m pmb.agent_wrapper` entry
- [x] Optional `--consolidate-on-exit` to run `pmb consolidate` when session ends

This is enough to start a chat, see PMB memory injected, and have every turn persisted.

It is **not** a usable coding agent - see "Not done" below.

---

## Not done - the real work

### Week 1: Selective compression (the actual research question)

The whole pitch lives or dies here. `DropOldestNarrative` is a straw-man baseline. The interesting policy is:

- [ ] **`SelectivePolicy`** in `policy.py`:
  - Classify each message: `decision`, `fact`, `tool_result`, `narrative`, `error`.
  - Heuristics first: regex for `we decided`, `the answer is`, explicit `[FACT]` tags, tool blocks with structured output.
  - For ambiguous cases, optional small-LLM classifier (Haiku).
  - Keep `decision` + `fact` + `tool_result` + recent N turns verbatim.
  - Summarize `narrative` runs into 1-paragraph anchors via Haiku.
  - Drop content already in PMB memory (reachable via `recall`).
- [ ] Decision: do we summarize narrative once and freeze, or re-summarize on every compaction? Re-summarize wastes tokens; freeze loses adaptability. **Build both, A/B them.**
- [ ] Metric: token retention vs. recall@5 on follow-up "what did we decide about X" questions. Build a tiny eval harness.

### Week 2: Tool use

PMB itself is already an MCP-style interface. The wrapper should support generic Anthropic tool use:

- [ ] Tool-call plumbing in `loop.py` (parse `tool_use` blocks, dispatch, append `tool_result`).
- [ ] Built-in tools: file read / write / search via local FS, shell exec, PMB recall/remember.
- [ ] Permission system (ask the user before exec).

This is what makes it a "coding agent" instead of just a chat that remembers.

### Week 3: Real session lifecycle

- [ ] Resume previous session: load last N turns from PMB memory, re-inject as compressed history.
- [ ] Per-project conversation transcripts in `~/.pmb/workspaces/{id}/transcripts/`.
- [ ] `pmb-chat resume` to continue yesterday's session.

### Week 4: Polish + dogfood

- [ ] Streaming responses (current code waits for full message).
- [ ] Better REPL: arrow-key history, multi-line input, ANSI rendering of markdown.
- [ ] Per-session metrics: tokens used, compactions triggered, recall hit rate.
- [ ] At least 40 hours of real dogfooding by the author with `pmb feedback` on every recall.

After week 4, **honest decision point**: does the selective-compression result *actually feel different* from Claude Code's default? If no, archive the wrapper as a failed experiment. If yes, expand scope.

---

## Realistic estimate

**4 weeks of focused work** to get to "I would use this instead of Claude Code for a real task". Beyond that - auth, multi-model, persistent settings, tool ecosystem - is open-ended.

The single most important thing to validate **before week 4** is whether selective compression even helps. If by end of week 1 the eval harness can't show a measurable difference vs. `DropOldestNarrative`, the whole premise of the pitch is in doubt, and the cheaper alternative is just leaning harder on PMB consolidation + better `recall` injection inside Claude Code's existing MCP integration.

---

## Honest framing

This wrapper is a **bet**, not a built product. The bet is: "PMB + a custom client that does selective compression is meaningfully better than PMB + Claude Code." That bet is unproven. The scaffold above lets you test the bet in 1 week of real work instead of needing 3 months before you can see the first signal.

If the bet wins → continue building, eventually this becomes its own product.
If it loses → throw the wrapper away, keep PMB as the narrow tool it already is.
