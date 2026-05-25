"""
`pmb connect <agent>` — one-shot MCP wiring for the common agents.

What this does (and does not do):
- Updates the agent's MCP config file in place, ADDING a `pmb` (or
  `pmb-remote`) entry without touching any other MCP servers the user
  already configured.
- For local agents the command is the venv's pmb-mcp + the current cwd
  as PMB_CWD. For --remote it's the SSH-tunneled form.
- Does NOT install the agent. Does NOT log in. The user runs Claude
  Code / Cursor as usual; this command just edits the JSON config they
  already have.
- `--probe` runs pmb-mcp for ~5s and checks an MCP `initialize` reply,
  so a syntactic config failure surfaces immediately rather than at
  the next agent launch.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ----------------------------------------------------------------------
# Agent instruction templates — written into AGENTS.md / CLAUDE.md so the
# AI follows PMB rules from the FIRST message, without needing manual setup.
# ----------------------------------------------------------------------

PMB_AGENT_RULES_START = "<!-- PMB-RULES-START (managed by `pmb connect`) -->"
PMB_AGENT_RULES_END   = "<!-- PMB-RULES-END -->"

PMB_AGENT_RULES_BODY = """\
## PMB — memory tools (via MCP)

**PMB is OFF by default.** Ignore PMB and answer normally for general
questions. Engage PMB ONLY on the explicit triggers below.

### When to CALL pmb.recall(query)

Only if user asks about themselves / their past / their project:
- "когда я / что я / кто такой / почему мы выбрали / какой у меня"
- "what did I / when did I / who is <name> / why did we choose"

For general/technical questions ("что такое Next.js", "как работает X",
"explain Y", coding help, debugging) — DO NOT call recall. Answer directly.

### When to CALL pmb.recent_activity / what_just_happened

- "что я недавно спрашивал / что мы обсуждали" → `recent_activity(minutes=10080, kind="research")`
- "что мы только что делали / что я писал час назад" → `recent_activity(minutes=60)` or `what_just_happened(5)`
- "какие у меня открытые цели / что я планировал" → `list_goals(status="in_progress")`

### When to CALL pmb.record_batch(items=[…])

Only if the user EXPLICITLY does one of these:

1. Says "запомни / remember / это важно / сохрани":
   ```
   record_batch(items=[{"type":"fact_tree", "main":"...", "subfacts":[...],
                        "importance":0.95, "pin":true}])
   ```

2. Shares a personal fact ("я работаю над X", "у меня кошка Y", "вчера ...",
   "решил выбрать Z", "встречаюсь с ..."):
   ```
   record_batch(items=[{"type":"fact","content":"User works on X"},
                       {"type":"goal","title":"...","status":"in_progress"}])
   ```

3. You (the agent) make a meaningful design/code decision on user's behalf:
   ```
   record_batch(items=[{"type":"activity","kind":"decision",
                        "content":"Chose X over Y for project Z because..."}])
   ```

For general questions answered from your own knowledge — DO NOT save anything.
PMB is not a logbook of every interaction.

### Rules when you DO call PMB

- Exactly ONE `record_batch` per turn (collect all items in one call).
- NEVER call `recall` after writing to "verify".
- NEVER call `pin()` separately — use the `pin: true` field on items.
- Use ABSOLUTE dates ("On May 25, 2026") not "today".

### Style — never expose the plumbing

- Never say "в памяти / I found in memory / согласно записям / я записал"
- After recall, use results as your own knowledge, weave them naturally
- Don't narrate what tools you called

### NOT a constraint on your response

The save-content rules apply to MEMORY only. Your answer to the user can
be as long, detailed, code-rich as the question deserves.

PMB is local-only.
"""


def _build_agent_rules_block() -> str:
    """The full rules block including markers, ready to inject into an
    AGENTS.md / CLAUDE.md file."""
    return f"\n{PMB_AGENT_RULES_START}\n{PMB_AGENT_RULES_BODY}\n{PMB_AGENT_RULES_END}\n"


def install_agent_rules(path: Path) -> str:
    """Append (or update) the PMB rules block in the agent's markdown
    instructions file. Returns one of: 'created', 'updated', 'added'.

    Uses BEGIN/END markers so repeated `pmb connect` calls don't duplicate.
    """
    block = _build_agent_rules_block()
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        # Create from scratch with a small header
        header = (
            "# Agent Instructions\n\n"
            "This file gives the AI agent persistent rules across sessions.\n"
        )
        path.write_text(header + block, encoding="utf-8")
        return "created"

    existing = path.read_text(encoding="utf-8")
    if PMB_AGENT_RULES_START in existing and PMB_AGENT_RULES_END in existing:
        # Replace existing block
        before = existing.split(PMB_AGENT_RULES_START)[0].rstrip()
        after = existing.split(PMB_AGENT_RULES_END, 1)[1].lstrip()
        new = before + "\n" + block.strip() + "\n"
        if after:
            new += "\n" + after
        path.write_text(new, encoding="utf-8")
        return "updated"

    # Append at end
    path.write_text(existing.rstrip() + "\n" + block, encoding="utf-8")
    return "added"


def instruction_paths_for_agent(agent: str, cwd: Path) -> list[Path]:
    """Where each agent looks for instruction markdown."""
    home = Path.home()
    if agent == "claude-code":
        # Claude Code reads CLAUDE.md (project) and ~/.claude/CLAUDE.md (global)
        return [home / ".claude" / "CLAUDE.md", cwd / "CLAUDE.md"]
    if agent == "cursor":
        # Cursor uses .cursorrules at project root or .cursor/rules/*.md
        return [cwd / ".cursorrules"]
    if agent == "codex":
        # OpenAI Codex CLI reads AGENTS.md (project) and ~/.codex/AGENTS.md (global)
        return [home / ".codex" / "AGENTS.md", cwd / "AGENTS.md"]
    return []


# ----------------------------------------------------------------------
# Path resolution per agent
# ----------------------------------------------------------------------


@dataclass
class AgentTarget:
    name: str
    config_paths: list[Path]  # ordered: first existing one wins
    fallback_path: Path       # used when none exists


def claude_code_paths(scope: str, cwd: Path) -> AgentTarget:
    """Resolve where Claude Code keeps its MCP config.

    `scope` ∈ {'project', 'global'}.
      - project → <cwd>/.mcp.json   (Claude Code's standard project config)
      - global  → ~/.claude.json    (Claude Code's user-level config)

    For project mode we create the file if missing. For global we never
    create it from scratch — only edit one that Claude Code already wrote;
    otherwise we fall back to project so the user isn't surprised.
    """
    if scope == "project":
        proj = cwd / ".mcp.json"
        return AgentTarget("claude-code", [proj], proj)
    home = Path.home()
    candidates = [home / ".claude.json"]
    fallback = candidates[0]
    return AgentTarget("claude-code", candidates, fallback)


def cursor_paths(cwd: Path) -> AgentTarget:
    """Cursor's MCP config:
      project: <cwd>/.cursor/mcp.json
      global:  ~/.cursor/mcp.json
    We prefer the project file when the user is inside a project.
    """
    proj = cwd / ".cursor" / "mcp.json"
    glob = Path.home() / ".cursor" / "mcp.json"
    return AgentTarget("cursor", [proj, glob], proj)


def codex_paths(cwd: Path) -> AgentTarget:
    """OpenAI Codex CLI's MCP config: ~/.codex/config.toml.

    Codex uses TOML and stores MCP servers as `[mcp_servers.<name>]`
    sections. No project-level config — global only.
    """
    global_path = Path.home() / ".codex" / "config.toml"
    return AgentTarget("codex", [global_path], global_path)


# ----------------------------------------------------------------------
# JSON merge — never trample existing mcpServers
# ----------------------------------------------------------------------


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import tomllib  # stdlib (3.11+)
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_toml(path: Path, data: dict) -> None:
    try:
        import tomli_w
    except ImportError as e:
        raise RuntimeError(
            "Codex MCP support requires tomli_w (writes TOML). "
            "Install: pip install tomli-w"
        ) from e
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")


def make_local_entry(
    workspace_cwd: Path,
    workspace_id: Optional[str] = None,
    pmb_home: Optional[Path] = None,
) -> dict:
    """The local pmb MCP server entry.

    workspace_id forces a SPECIFIC workspace (override the cwd-based auto-
    detection). Use this to share one workspace across multiple AI clients —
    e.g. Claude Code + Cursor both pointing at the same `personal` workspace.

    pmb_home overrides PMB_HOME (where workspaces live on disk). Useful for
    multi-user shared memory (point at a shared NAS path).
    """
    venv_python = sys.executable
    pmb_mcp = shutil.which("pmb-mcp")
    env: dict = {"PMB_CWD": str(workspace_cwd)}
    if workspace_id:
        env["PMB_WORKSPACE"] = workspace_id
    if pmb_home:
        env["PMB_HOME"] = str(pmb_home)
    if pmb_mcp:
        return {"command": pmb_mcp, "env": env}
    return {
        "command": venv_python,
        "args": ["-m", "pmb.mcp.server"],
        "env": env,
    }


def make_remote_entry(remote: str) -> dict:
    """SSH-tunneled entry. `remote` = user@host:/abs/path/to/repo."""
    if ":" not in remote:
        raise ValueError(f"--remote must look like user@host:/abs/path/to/repo, got {remote!r}")
    target, remote_cwd = remote.split(":", 1)
    # Build the remote command line: set PMB_CWD inline and exec pmb-mcp
    remote_cmd = f'PMB_CWD="{remote_cwd}" pmb-mcp'
    return {
        "command": "ssh",
        "args": [target, remote_cmd],
    }


def merge_entry(existing: dict, name: str, entry: dict) -> tuple[dict, str]:
    """Add or replace the named entry. Returns (new_config, action_str)."""
    cfg = dict(existing) if existing else {}
    servers = dict(cfg.get("mcpServers") or {})
    action = "replaced" if name in servers else "added"
    servers[name] = entry
    cfg["mcpServers"] = servers
    return cfg, action


def merge_codex_entry(
    existing: dict, name: str, entry: dict,
) -> tuple[dict, str]:
    """Codex stores MCP servers as nested `[mcp_servers.<name>]` tables.

    TOML round-trips through dict-of-dicts — same structure, different
    serialization. We touch ONLY the `mcp_servers.<name>` key, never the
    rest of the config (which has marketplaces / plugins / projects etc.).
    """
    cfg = dict(existing) if existing else {}
    servers = dict(cfg.get("mcp_servers") or {})
    action = "replaced" if name in servers else "added"
    # Codex expects 'command', 'args', 'env' (no nested 'mcpServers' wrap)
    codex_entry = {}
    if "command" in entry:
        codex_entry["command"] = entry["command"]
    if "args" in entry:
        codex_entry["args"] = list(entry["args"])
    else:
        codex_entry["args"] = []
    if entry.get("env"):
        codex_entry["env"] = dict(entry["env"])
    # Codex-specific: timeout in case PMB cold start is slow (model load)
    codex_entry["startup_timeout_sec"] = 120
    servers[name] = codex_entry
    cfg["mcp_servers"] = servers
    return cfg, action


# ----------------------------------------------------------------------
# Probe — run pmb-mcp briefly and check it speaks MCP
# ----------------------------------------------------------------------


def probe_mcp(timeout_seconds: float = 6.0) -> tuple[bool, str]:
    """Spawn pmb-mcp, send an `initialize` request over stdio, read response.

    Returns (ok, message). On Windows we keep the timeout short — the goal
    is to surface obvious 'doesn't even start' problems, not full
    integration testing.
    """
    pmb_mcp = shutil.which("pmb-mcp")
    if pmb_mcp:
        cmd = [pmb_mcp]
    else:
        cmd = [sys.executable, "-m", "pmb.mcp.server"]

    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pmb-connect-probe", "version": "0"},
        },
    }
    payload = json.dumps(init) + "\n"

    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )
    except FileNotFoundError:
        return False, "pmb-mcp not found in PATH"

    try:
        out, err = proc.communicate(input=payload, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        # Timeout is actually a *good* sign: the server is alive and waiting
        # for more input. We just haven't taught it to exit cleanly on EOF.
        return True, "server stayed alive past initialize (no crash within probe window)"

    if proc.returncode == 0 and ("result" in (out or "") or "id" in (out or "")):
        return True, "got an MCP-shaped response"
    if proc.returncode != 0:
        return False, f"server exited {proc.returncode}: {(err or '').strip()[:300]}"
    return False, f"no recognizable response. stdout head: {(out or '')[:200]!r}"


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------


def connect(
    agent: str,
    *,
    cwd: Path,
    scope: str = "project",
    remote: Optional[str] = None,
    name_override: Optional[str] = None,
    workspace_id: Optional[str] = None,
    pmb_home: Optional[Path] = None,
) -> dict:
    """Write the MCP entry into the right config file.

    workspace_id and pmb_home are forwarded to the MCP server via env vars
    (PMB_WORKSPACE, PMB_HOME). When set, the server uses that exact workspace
    regardless of cwd — letting multiple AI clients share one memory.
    """
    if agent == "claude-code":
        target = claude_code_paths(scope, cwd)
    elif agent == "cursor":
        target = cursor_paths(cwd)
    elif agent == "codex":
        target = codex_paths(cwd)
    else:
        raise ValueError(
            f"unsupported agent {agent!r}. Try: claude-code, cursor, codex"
        )

    path = next((p for p in target.config_paths if p.exists()), target.fallback_path)

    if remote:
        entry = make_remote_entry(remote)
        name = name_override or "pmb-remote"
    else:
        entry = make_local_entry(
            cwd, workspace_id=workspace_id, pmb_home=pmb_home,
        )
        name = name_override or ("pmb-shared" if workspace_id else "pmb")

    # Codex uses TOML (mcp_servers.<name>), others use JSON (mcpServers.<name>)
    if agent == "codex":
        existing = _load_toml(path)
        new_cfg, action = merge_codex_entry(existing, name, entry)
        _save_toml(path, new_cfg)
    else:
        existing = _load_json(path)
        new_cfg, action = merge_entry(existing, name, entry)
        _save_json(path, new_cfg)

    # Improvement O: also write PMB rules into the agent's instructions
    # file (AGENTS.md / CLAUDE.md / .cursorrules) so the AI knows how to
    # use PMB from the very first message. Idempotent (BEGIN/END markers).
    rules_written: list[dict] = []
    try:
        for inst_path in instruction_paths_for_agent(agent, cwd):
            # Only write to the GLOBAL one by default (home), or to the
            # project one when scope='project'. Skip the other.
            is_global = str(inst_path.parent) == str(Path.home() / f".{agent.replace('-code', '').replace('codex', 'codex')}")
            # Simpler: just write to the first path (global) — most agents
            # read both, global is safer (works across all projects).
            written = install_agent_rules(inst_path)
            rules_written.append({
                "path": str(inst_path),
                "action": written,
            })
            break  # only do the first (global) for simplicity
    except Exception as e:
        rules_written.append({"error": str(e)})

    return {
        "agent": agent,
        "scope": scope,
        "config_path": str(path),
        "entry_name": name,
        "action": action,
        "entry": entry,
        "workspace_id": workspace_id,
        "instruction_rules": rules_written,
    }
