# Security Policy

## What PMB stores

PMB writes everything to a local SQLite file (`~/.pmb/workspaces/<id>/events.sqlite`) and a local LanceDB directory. **Nothing is sent to any external service** by PMB itself.

Caveats worth knowing:

- The **AI agent** plugged into PMB (Codex CLI / Claude Code / Cursor / etc.) talks to its own LLM provider. PMB has no control over that channel.
- The **embedding model** runs locally (sentence-transformers, no network at inference time after the first download).
- The **Ollama backend**, if you enable it, talks to your local Ollama server (default `http://localhost:11434`). It does not leave your machine unless you configure it to.
- The optional **Anthropic backend** for `pmb consolidate` does send the clustered text to api.anthropic.com when invoked. Off by default.

## Reporting vulnerabilities

If you believe you have found a security issue:

1. Do not open a public issue.
2. Email the maintainer or open a GitHub Security Advisory.
3. Include a minimal repro and what you think the impact is.

We will acknowledge within a few days and aim to publish a fix or workaround within two weeks for serious issues.

## Threat model in scope

- Untrusted input via MCP tool calls (the agent may pass arbitrary text into `record_*`).
- File-path traversal in `pmb` CLI arguments.
- SQL injection (we use parameterised queries everywhere; a regression is a bug).
- Resource exhaustion via huge content blobs (mitigated by 5000-char cap in `record_batch`).

## Out of scope

- Confidentiality of data the user *chooses* to record. PMB is a memory store - if you feed it secrets they will be stored. Use `record_fact ... metadata={"redact": true}` or rely on the built-in regex redactor for known secret shapes.
- Multi-user isolation. PMB is single-user. Anyone with access to your `~/.pmb/` directory can read all your memory.
- Network-level attacks on Ollama or LanceDB. Those are upstream concerns.
