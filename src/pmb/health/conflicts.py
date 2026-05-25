"""
Conflict Detector — выявляет противоречия между фактами разного времени.

Идея: один key (например `database`) может иметь разные value в разное время.
Это либо эволюция (Postgres 16 → Postgres 17, нормально), либо ошибка
(2 живых факта противоречат).

Стратегии детекции (без LLM-вызовов):
1. **Same-key heuristics**: ищем events типа "fact" где content имеет
   паттерн "X = Y" или "X is Y" с одинаковым X но разным Y.
2. **Semantic clusters**: для qa events — высокая similarity между
   вопросами (близкие по embedding) но низкая между ответами.

В Phase 3 — простая реализация через regex для facts. Semantic clusters
— TODO для следующей итерации.

Output: список FactConflict с suggested_resolution:
- "supersede": newer заменяет older — automatic resolution
- "concurrent": оба могут быть истинны (разные branches)
- "needs_review": требует ручного разбора
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from pmb.core.engine import Engine
    from pmb.core.events import Event


# Conservative patterns — only catch identifier-style keys, not free text.
# We deliberately dropped the prior generic "X is Y" pattern: it matched
# natural language like "the bug is fixed" and produced false conflicts.

_IDENT = r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*"

KEY_VALUE_PATTERNS = [
    # foo = value  (assignment, requires =, not just :)
    re.compile(rf"(?<![\w.])({_IDENT})\s*=\s*(.+)"),
    # project.foo = value, user.foo = value, also project.foo: value (config-style)
    re.compile(rf"(?<![\w.])((?:project|user)\.{_IDENT})\s*[=:]\s*(.+)", re.IGNORECASE),
    # "<ident> uses <value>" — single ident only
    re.compile(rf"(?<![\w.])({_IDENT})\s+uses\s+(.+)", re.IGNORECASE),
]

# Keys we refuse to treat as configuration — common natural-language words
# that would otherwise generate false-positive conflicts.
_KEY_BLOCKLIST = {
    "the", "a", "an", "this", "that", "it", "we", "i", "you", "they",
    "is", "was", "are", "be", "been", "being",
    "what", "where", "who", "when", "how", "why", "which",
    "and", "or", "but", "if", "then", "so", "because",
    "code", "function", "class", "method", "var", "let", "const",
    "true", "false", "null", "none", "yes", "no",
    "todo", "fixme", "note", "warning",
}

_MAX_VALUE_LEN = 80


@dataclass
class FactConflict:
    """Конфликт между двумя фактами."""

    key: str
    older_ulid: str
    older_value: str
    older_timestamp: float
    newer_ulid: str
    newer_value: str
    newer_timestamp: float
    suggested_resolution: str  # "supersede" | "concurrent" | "needs_review"
    confidence: float  # 0..1, насколько уверены что это конфликт

    def to_dict(self) -> dict:
        return asdict(self)


def extract_key_value(content: str) -> Optional[tuple[str, str]]:
    """
    Conservatively extract a (key, value) pair.

    Returns the first plausible pair, or None. Keys must be identifier-like
    and not in the natural-language blocklist. Values are trimmed at the
    first newline / sentence end and length-capped.
    """
    for pattern in KEY_VALUE_PATTERNS:
        m = pattern.search(content)
        if not m:
            continue
        key = m.group(1).strip().lower()
        value = m.group(2).strip()
        # Strip at first newline or sentence end
        value = value.split("\n")[0]
        # Cut at first ". " or "; " — keep abbreviations like "v1.2" intact
        for sep in (". ", "; ", " — ", " - "):
            idx = value.find(sep)
            if idx > 0:
                value = value[:idx]
        value = value.strip().rstrip(".,;:!?")
        if len(value) > _MAX_VALUE_LEN:
            value = value[:_MAX_VALUE_LEN].rstrip() + "…"

        # Filter
        if not (2 <= len(key) <= 40 and 2 <= len(value) <= _MAX_VALUE_LEN + 1):
            continue
        # Block common natural-language words from being treated as keys
        head = key.split(".")[0]
        if head in _KEY_BLOCKLIST:
            continue
        return (key, value)
    return None


def _values_seem_different(v1: str, v2: str) -> bool:
    """Грубая проверка: значения не идентичны и не подмножество одно другого."""
    v1n = v1.lower().strip().rstrip(".,;:!?")
    v2n = v2.lower().strip().rstrip(".,;:!?")
    if v1n == v2n:
        return False
    # Если одно — substring другого, это эволюция (e.g. "Postgres" vs "Postgres 17")
    if v1n in v2n or v2n in v1n:
        return False
    return True


class ConflictDetector:
    """Detects conflicts among facts in workspace."""

    def __init__(self, engine: "Engine"):
        self.engine = engine

    def detect(self, max_age_days: float = 365.0) -> list[FactConflict]:
        """
        Найти конфликты в active fact events.

        Возвращает список FactConflict, отсортированный по newer_timestamp DESC.
        """
        workspace_id = self.engine.workspace.id
        events = self.engine.events.list_active(
            workspace_id, limit=10000, event_type="fact",
        )
        # Также смотрим qa events на key=value паттерны
        qa_events = self.engine.events.list_active(
            workspace_id, limit=10000, event_type="qa",
        )
        all_candidates: list["Event"] = events + qa_events

        cutoff = time.time() - max_age_days * 86400.0
        all_candidates = [e for e in all_candidates if e.timestamp >= cutoff]

        # Group by key
        key_to_events: dict[str, list[tuple["Event", str]]] = {}
        for ev in all_candidates:
            kv = extract_key_value(ev.content)
            if not kv:
                continue
            key, value = kv
            key_to_events.setdefault(key, []).append((ev, value))

        conflicts: list[FactConflict] = []
        for key, items in key_to_events.items():
            if len(items) < 2:
                continue
            # Сортируем по timestamp asc
            items.sort(key=lambda x: x[0].timestamp)
            # Compare adjacent pairs (older vs newer)
            for i in range(len(items) - 1):
                older_ev, older_val = items[i]
                newer_ev, newer_val = items[i + 1]
                if not _values_seem_different(older_val, newer_val):
                    continue

                # Same session? Тогда concurrent vs supersede
                same_session = (
                    older_ev.source_session_id == newer_ev.source_session_id
                    and older_ev.source_session_id is not None
                )
                # > 7 days apart? — скорее supersede
                age_diff_days = (newer_ev.timestamp - older_ev.timestamp) / 86400.0

                if age_diff_days > 7:
                    resolution = "supersede"
                    confidence = 0.7
                elif same_session:
                    resolution = "needs_review"
                    confidence = 0.5
                else:
                    resolution = "concurrent"
                    confidence = 0.4

                conflicts.append(FactConflict(
                    key=key,
                    older_ulid=older_ev.ulid,
                    older_value=older_val,
                    older_timestamp=older_ev.timestamp,
                    newer_ulid=newer_ev.ulid,
                    newer_value=newer_val,
                    newer_timestamp=newer_ev.timestamp,
                    suggested_resolution=resolution,
                    confidence=confidence,
                ))

        conflicts.sort(key=lambda c: -c.newer_timestamp)
        return conflicts

    def auto_resolve(self, dry_run: bool = True, merge_via_llm: bool = False) -> dict:
        """
        Resolve 'supersede' conflicts.

        Two modes:
          - default (merge_via_llm=False): just archive the older value.
            Fast, deterministic, no API calls.
          - LLM-merge (merge_via_llm=True): also create a merged fact that
            preserves both contexts ("X was Y until DATE, now Z") and
            archives both originals. This is the human-memory reconsolidation
            analogy: instead of throwing the old version away we synthesize
            an updated one with provenance.

        In dry_run we never write — we just describe what we would do.
        """
        conflicts = self.detect()
        actions = []
        archived = 0
        merged_created = 0

        llm = None
        if merge_via_llm and not dry_run:
            try:
                from pmb.health.consolidate import resolve_llm_client
                # Read backend from engine config if available, fallback to auto
                backend = "auto"
                try:
                    backend = self.engine.config.get("consolidate.backend")
                except Exception:
                    pass
                llm = resolve_llm_client(backend=backend)
            except Exception as e:
                # Fall back to non-merge mode rather than failing
                llm = None
                merge_via_llm = False

        for c in conflicts:
            if c.suggested_resolution != "supersede" or c.confidence < 0.6:
                continue

            action = {
                "key": c.key,
                "older_ulid": c.older_ulid,
                "older_value": c.older_value,
                "newer_ulid": c.newer_ulid,
                "newer_value": c.newer_value,
                "mode": "merge" if merge_via_llm else "archive_older",
            }

            if dry_run:
                actions.append(action)
                continue

            if merge_via_llm and llm is not None:
                # Ask the LLM to write one fact that carries both contexts.
                merged_text = _ask_llm_to_merge(c, llm)
                if merged_text:
                    new_ulid = self.engine.record_fact(
                        fact=merged_text,
                        importance=0.85,
                        metadata={
                            "merged_from": [c.older_ulid, c.newer_ulid],
                            "merged_at": time.time(),
                            "key": c.key,
                        },
                    )
                    # Archive both originals — merged fact now carries truth
                    self.engine.events.archive(c.older_ulid)
                    self.engine.events.archive(c.newer_ulid)
                    archived += 2
                    merged_created += 1
                    action["new_ulid"] = new_ulid
                    action["merged_text"] = merged_text
                    actions.append(action)
                    continue
                # Fall through to plain archive if LLM merge failed
                action["mode"] = "archive_older_fallback"

            # Default supersede: archive older, keep newer
            self.engine.events.archive(c.older_ulid)
            archived += 1
            actions.append(action)

        return {
            "n_conflicts": len(conflicts),
            "n_supersede_candidates": sum(
                1 for c in conflicts
                if c.suggested_resolution == "supersede" and c.confidence >= 0.6
            ),
            "n_archived": archived,
            "n_merged": merged_created,
            "dry_run": dry_run,
            "actions": actions,
        }


_MERGE_PROMPT = """\
Two facts about the same key contradict each other. The newer fact is
the current truth; the older is history. Write ONE sentence that
preserves both — current state first, then history — using natural
language.

Examples:
  older: "database = MySQL"
  newer: "database = Postgres 17"
  output: "Database is currently Postgres 17 (was MySQL until the recent migration)."

  older: "auth = JWT only"
  newer: "auth = JWT + 2FA via TOTP"
  output: "Auth uses JWT with 2FA TOTP added on top (was JWT-only before)."

Now write a single-sentence merged fact for the following — JSON only,
no prose or code fences:

{"merged": "<your one sentence>"}

Key:   %(key)s
Older: %(older)s
Newer: %(newer)s
"""


def _ask_llm_to_merge(c, llm) -> str:
    """Ask the LLM to produce a merged fact preserving both contexts."""
    import json
    prompt = _MERGE_PROMPT % {
        "key": c.key,
        "older": c.older_value,
        "newer": c.newer_value,
    }
    try:
        out = llm.consolidate([prompt])
    except Exception:
        return ""
    # The shared consolidate path returns {summary, confidence, ...}. Try
    # to find a JSON {"merged": "..."} object anywhere in the text.
    for key in ("summary", "reasoning"):
        raw = out.get(key) if isinstance(out, dict) else None
        if not raw:
            continue
        a = raw.find("{")
        b = raw.rfind("}")
        if a < 0 or b <= a:
            continue
        try:
            obj = json.loads(raw[a : b + 1])
            merged = obj.get("merged", "").strip()
            if merged:
                return merged
        except Exception:
            continue
    return ""
