"""
Console-configurable settings layer.

Two YAML files:
  - per-workspace: <workspace_storage>/config.yaml
  - global default: <PMB_HOME>/config.yaml

Resolution order (highest wins):
  1. explicit kwarg passed to Engine() or recall()
  2. per-workspace config.yaml
  3. global config.yaml
  4. hard-coded default in `DEFAULTS`

Schema is intentionally flat with dotted keys (`recall.bm25_weight`)
so the CLI can do  `pmb config set recall.bm25_weight 0.7` without
parsing nested YAML. Internally we mirror it as a nested dict for
readability when the YAML is opened in an editor.

Validation lives in `_TYPES`. Bad input prints a clear error and
refuses the write.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml


# ----------------------------------------------------------------------
# Schema — every knob the user can tune, with type and human help text
# ----------------------------------------------------------------------


@dataclass
class _Setting:
    type: type
    default: Any
    help: str
    choices: Optional[tuple] = None
    min: Optional[float] = None
    max: Optional[float] = None


# A single source of truth — every knob in PMB lives here. Adding a key
# here is the only step needed to expose it as `pmb config <key>`.
SCHEMA: dict[str, _Setting] = {
    # Recall / search
    "recall.bm25_weight": _Setting(
        float, 0.5, "Weight of BM25 in BM25+vector fusion (vec_weight = 1 - this)",
        min=0.0, max=1.0,
    ),
    "recall.top_k": _Setting(int, 5, "Default top-K returned by recall", min=1, max=100),
    "recall.recency_half_life_days": _Setting(
        float, 30.0, "Half-life for recency boost in days", min=0.5, max=3650.0,
    ),
    "recall.graph_boost": _Setting(
        float, 0.15, "Additive bonus from graph traversal (0 disables)",
        min=0.0, max=1.0,
    ),
    "recall.multi_entity_bonus": _Setting(
        float, 0.5,
        "Multi-hop bonus: events matching N query entities get graph weight "
        "× (1 + bonus*(N-1)). 0 disables. Helps multi-hop questions where "
        "answer event mentions multiple query entities.",
        min=0.0, max=2.0,
    ),
    "recall.causation_walk": _Setting(
        bool, True,
        "PMB v2: walk causation graph (event_edges) when query looks "
        "multi-hop (after/before/because/...). Surfaces bridge events that "
        "lexical search misses. Free if no edges exist.",
    ),
    "recall.causation_boost": _Setting(
        float, 0.10,
        "Additive bonus to events surfaced by causation walk (multiplied by "
        "importance and recency). Lower than graph_boost because causation "
        "edges are LLM-extracted and noisier.",
        min=0.0, max=1.0,
    ),
    "recall.arc_expansion": _Setting(
        bool, True,
        "PMB v2: search narrative arc summaries and inject member events "
        "into recall candidates. Especially helps 'tell me about X' and "
        "'history of Y' style queries.",
    ),
    "recall.arc_boost": _Setting(
        float, 0.08,
        "Additive bonus for events that belong to an arc matching the query.",
        min=0.0, max=1.0,
    ),
    "recall.collapse_reflections": _Setting(
        bool, True,
        "PMB v2: after scoring, collapse reflection events onto their source. "
        "Reflections are bridges (boost source's score) but the final result "
        "list returns the actual source events with their dia_ids/metadata.",
    ),
    "recall.ppr_enabled": _Setting(
        bool, False,
        "HippoRAG-style Personalized PageRank over the entity graph. "
        "Diffuses relevance through graph for multi-hop unlock. Off by "
        "default — adds noise to single-entity lookups. Enable for "
        "multi-hop-heavy workloads; combine with intent gating.",
    ),
    "recall.ppr_weight": _Setting(
        float, 0.5,
        "Weight of PPR contribution to final event score. Higher = trust graph more.",
        min=0.0, max=3.0,
    ),
    "recall.ppr_alpha": _Setting(
        float, 0.5,
        "PPR teleportation probability. 0.5 balances depth (low) vs locality "
        "(high). Lower = walks further out (more multi-hop), higher = stays "
        "near query entities.",
        min=0.05, max=0.95,
    ),
    "recall.ppr_iters": _Setting(
        int, 20,
        "PPR power iterations. 20 is plenty for graphs under 100k nodes.",
        min=5, max=200,
    ),
    "recall.ppr_always": _Setting(
        bool, False,
        "Run PPR even on single-entity / non-multi-hop queries. Off by default "
        "because PPR adds noise to exact-match lookups. Useful for benchmark "
        "comparisons or recall-heavy workloads.",
    ),
    "recall.adaptive_decompose": _Setting(
        bool, False,
        "PMB v2.2: when query looks multi-hop, LLM splits it into 2-3 atomic "
        "sub-queries, runs each, fuses via Reciprocal Rank Fusion. Costs 1 "
        "LLM call per multi-hop query (cached on disk). Off by default — "
        "enable for multi-hop heavy workloads. Single-hop queries unaffected.",
    ),
    "recall.reflection_to_edges": _Setting(
        bool, True,
        "Improvement B (HippoRAG 2 inspired): during reflection, link the "
        "LLM-extracted entities/people/themes BACK to the source event in "
        "the graph (not just to the reflection chunk). Source becomes "
        "findable via reflection vocabulary without a separate index hit. "
        "On by default — pure win, no cost.",
    ),
    "recall.temporal_enabled": _Setting(
        bool, True,
        "Improvement C (Zep/Graphiti inspired): parse explicit date "
        "references from events (regex, no LLM) into event_time metadata, "
        "and boost candidates by temporal proximity when the query has "
        "time markers. Cheap.",
    ),
    "recall.temporal_boost": _Setting(
        float, 0.20,
        "Weight of temporal-proximity contribution to final score.",
        min=0.0, max=2.0,
    ),
    "recall.temporal_half_life_days": _Setting(
        float, 14.0,
        "Days at which temporal proximity drops to 0.5. Lower = stricter "
        "time matching; higher = wider window.",
        min=0.5, max=3650.0,
    ),
    "recall.adaptive_routing": _Setting(
        bool, True,
        "Improvement E: classify query intent (direct/temporal/multi-hop/"
        "narrative/inferential) and re-weight layer boosts accordingly. "
        "Cheap (<0.1ms, no LLM). On by default — pure win.",
    ),
    "recall.predictive_enabled": _Setting(
        bool, True,
        "Improvement F: check predictive cache first. If pre-computed "
        "answer matches current query (cosine ≥ threshold), return cached "
        "top-K in ~3ms instead of running full recall (~80ms). "
        "Cache populated during sleep via `precompute_predictive_cache()`.",
    ),
    "recall.predictive_threshold": _Setting(
        float, 0.85,
        "Cosine similarity needed for a cached query to count as a hit.",
        min=0.5, max=1.0,
    ),
    "recall.predictive_ttl_days": _Setting(
        float, 7.0,
        "Days a cached entry stays valid. After this it's ignored on read "
        "(but kept until cleanup).",
        min=0.1, max=3650.0,
    ),
    "recall.person_extraction": _Setting(
        bool, True,
        "Improvement H: extract person entities (no ML) via speaker "
        "metadata + capitalized-word regex + verb-context + self-reinforcing "
        "dict. Boosts person-heavy queries (cat 1/3 in LoCoMo).",
    ),
    "recall.code_ast_extraction": _Setting(
        bool, True,
        "Improvement J (code half): when content looks like Python source, "
        "extract function/class/import entities via stdlib `ast`. Lets "
        "the graph layer answer code-structure queries.",
    ),
    "recall.typo_correction": _Setting(
        bool, True,
        "Improvement K: at recall start, fuzzy-match query tokens against "
        "known entity names (Levenshtein ≤ 2). 'Aliceee'→'alice', "
        "'Postgers'→'postgres'. Cheap, no ML. On by default.",
    ),
    "recall.graph_expansion_llm": _Setting(
        bool, False,
        "Use an LLM to extract concrete entities from abstract queries before "
        "graph traversal. Adds one LLM call per recall — off by default.",
    ),
    "recall.cache_size": _Setting(
        int, 128, "LRU cache size for recall queries (0 disables)",
        min=0, max=10000,
    ),
    "recall.cache_ttl_seconds": _Setting(
        float, 300.0,
        "How long a cached recall stays valid before re-running (5min default)",
        min=0.0, max=86400.0,
    ),
    "recall.spreading_activation": _Setting(
        bool, True,
        "Boost importance of graph neighbours of recall hits (priming). "
        "Decays over hours; mimics human spreading-activation.",
    ),
    "recall.spreading_boost": _Setting(
        float, 0.05,
        "Magnitude of priming boost added to each hit's graph neighbour",
        min=0.0, max=0.5,
    ),
    "recall.spreading_half_life_hours": _Setting(
        float, 2.0,
        "How fast the priming boost itself decays (hours)",
        min=0.1, max=168.0,
    ),
    "recall.rerank": _Setting(
        bool, False, "Use cross-encoder reranker on top-N hits",
    ),
    "recall.rerank_top_n": _Setting(
        int, 25, "Candidates fed into the reranker", min=5, max=200,
    ),
    "recall.rerank_model": _Setting(
        str, "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "HuggingFace cross-encoder model name",
    ),
    # Embedding
    "embedding.model": _Setting(
        str, "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "Embedding model name (any sentence-transformers id). Default is "
        "multilingual (50+ langs incl. RU/EN/ES/ZH). After changing run "
        "`pmb reindex` to re-embed existing events.",
    ),
    "embedding.backend": _Setting(
        str, "sentence-transformers",
        "Inference runtime",
        choices=("sentence-transformers", "fastembed"),
    ),
    "embedding.fastembed_model": _Setting(
        str, "sentence-transformers/all-MiniLM-L6-v2",
        "fastembed-compatible model id (used only when backend=fastembed)",
    ),
    # Decay / forgetting
    "decay.factor_per_day": _Setting(
        float, 0.985, "Daily importance decay multiplier (0..1)",
        min=0.5, max=1.0,
    ),
    "decay.archive_threshold": _Setting(
        float, 0.05, "Importance below this triggers archive (if also old)",
        min=0.0, max=1.0,
    ),
    "decay.archive_min_age_days": _Setting(
        float, 90.0, "Don't auto-archive events younger than this",
        min=0.0, max=3650.0,
    ),
    # Reinforcement
    "feedback.useful_boost_rate": _Setting(
        float, 0.08, "Per-call boost when feedback=useful (saturating)",
        min=0.0, max=1.0,
    ),
    "feedback.wrong_demote": _Setting(
        float, 0.05, "Per-call demote when feedback=wrong/irrelevant",
        min=0.0, max=1.0,
    ),
    # Consolidation
    "consolidate.backend": _Setting(
        str, "auto", "LLM backend for consolidation",
        choices=("auto", "claude", "anthropic", "ollama"),
    ),
    "consolidate.model": _Setting(
        str, "", "Override model name; empty = backend default",
    ),
    "consolidate.similarity_threshold": _Setting(
        float, 0.5, "Cosine similarity threshold for clustering",
        min=0.0, max=1.0,
    ),
    "consolidate.min_cluster_size": _Setting(
        int, 3, "Min events in a cluster", min=2, max=50,
    ),
    "consolidate.min_confidence": _Setting(
        float, 0.6, "LLM confidence threshold to store",
        min=0.0, max=1.0,
    ),
    "consolidate.since_days": _Setting(
        float, 14.0, "Look back N days for clustering",
        min=0.5, max=3650.0,
    ),
    "consolidate.auto_trigger": _Setting(
        bool, False,
        "Auto-run consolidation when thresholds are met (writes or time). "
        "Off by default — LLM calls cost time/money so explicit is safer.",
    ),
    "consolidate.auto_min_new_events": _Setting(
        int, 50,
        "Trigger threshold: N new events since last consolidation",
        min=1, max=100000,
    ),
    "consolidate.auto_min_days": _Setting(
        float, 7.0,
        "Trigger threshold: M days since last consolidation",
        min=0.5, max=365.0,
    ),
    # Ollama
    "ollama.url": _Setting(str, "", "Ollama URL (empty -> localhost:11434)"),
    "ollama.model": _Setting(str, "llama3.1:8b", "Ollama model id for consolidation"),
    # Agent wrapper / pmb-chat
    "chat.transport": _Setting(
        str, "auto", "pmb-chat transport",
        choices=("auto", "claude", "anthropic", "ollama"),
    ),
    "chat.model": _Setting(str, "haiku", "Model alias for pmb-chat"),
    "chat.window": _Setting(int, 200_000, "Token window", min=1024, max=10_000_000),
    "chat.target_max": _Setting(
        float, 0.75, "Fraction of window before compaction triggers",
        min=0.1, max=0.99,
    ),
    "chat.selective_compression": _Setting(
        bool, True, "Use SelectivePolicy (vs DropOldestNarrative)",
    ),

    # ------------------------------------------------------------------
    # Improvement U: Multi-layer dedup
    # ------------------------------------------------------------------
    "dedup.enable": _Setting(
        bool, True,
        "Master switch for write-time dedup (L1 exact + L2 semantic). "
        "Off → all writes go through unchanged (legacy behavior).",
    ),
    "dedup.enable_semantic": _Setting(
        bool, True,
        "L2: cosine-similarity dedup at write time. Off keeps only L1 "
        "(exact-text match). L2 adds ~50ms per write (embedding+search).",
    ),
    "dedup.cosine_high": _Setting(
        float, 0.92,
        "L2 high threshold — at or above this, the new write is silently "
        "merged into the existing canonical event. Conservative default; "
        "tighter = fewer false merges, looser = catches more dups.",
        min=0.5, max=0.999,
    ),
    "dedup.cosine_mid": _Setting(
        float, 0.80,
        "L2 mid threshold — pairs in [mid, high) are written as borderline "
        "candidates into the dedup queue for async LLM verification (L2.5).",
        min=0.5, max=0.99,
    ),
    "dedup.lookback_days": _Setting(
        float, 90.0,
        "How far back to search for dedup candidates. Older items are too "
        "stale to be likely duplicates; bounds the search for speed.",
        min=1.0, max=3650.0,
    ),
    "dedup.async_verify": _Setting(
        bool, True,
        "L2.5: enqueue borderline pairs for async LLM verify. Workers "
        "(Ollama or Anthropic) drain the queue via `pmb dedupe --run-pending`.",
    ),

    # ------------------------------------------------------------------
    # Improvement AA: fire-and-forget MCP record_batch
    # ------------------------------------------------------------------
    "mcp.record_batch_async": _Setting(
        bool, True,
        "MCP `record_batch` tool returns IMMEDIATELY after spawning "
        "background processing — no waiting for embedding/graph/LanceDB. "
        "Trade-off: ULIDs not returned synchronously, and recall called "
        "within ~1s of the write may miss the new events. Set False for "
        "synchronous semantics (testing/debugging).",
    ),
}


# ----------------------------------------------------------------------
# Conversion + validation
# ----------------------------------------------------------------------


def _coerce(value: Any, setting: _Setting) -> Any:
    """Convert YAML/CLI string into the right Python type, validate range/choices."""
    t = setting.type
    if value is None:
        return setting.default
    # Booleans from "true"/"false"/"1"/"0"
    if t is bool:
        if isinstance(value, bool):
            v = value
        elif isinstance(value, (int, float)):
            v = bool(value)
        else:
            s = str(value).strip().lower()
            if s in ("true", "1", "yes", "on"):
                v = True
            elif s in ("false", "0", "no", "off"):
                v = False
            else:
                raise ValueError(f"expected boolean, got {value!r}")
        return v
    if t is int:
        v = int(value)
    elif t is float:
        v = float(value)
    elif t is str:
        v = str(value)
    else:
        v = value
    if setting.choices and v not in setting.choices:
        raise ValueError(f"value {v!r} not in {setting.choices}")
    if setting.min is not None and v < setting.min:
        raise ValueError(f"value {v} below min {setting.min}")
    if setting.max is not None and v > setting.max:
        raise ValueError(f"value {v} above max {setting.max}")
    return v


# ----------------------------------------------------------------------
# YAML <-> flat dict helpers
# ----------------------------------------------------------------------


def _flatten(nested: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in nested.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, path))
        else:
            out[path] = v
    return out


def _unflatten(flat: dict[str, Any]) -> dict:
    out: dict = {}
    for key, value in flat.items():
        parts = key.split(".")
        cur = out
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value
    return out


# ----------------------------------------------------------------------
# Config holder
# ----------------------------------------------------------------------


class Config:
    """
    Layered config. Reads global + workspace YAMLs once, applies overrides
    on top. `get(key)` always returns a validated, typed value.
    """

    def __init__(
        self,
        workspace_dir: Optional[Path] = None,
        pmb_home: Optional[Path] = None,
        overrides: Optional[dict[str, Any]] = None,
    ):
        self.workspace_dir = workspace_dir
        self.pmb_home = pmb_home
        self._global = self._load(self.global_path) if pmb_home else {}
        self._workspace = self._load(self.workspace_path) if workspace_dir else {}
        self._overrides = dict(overrides or {})

    # -- paths --
    @property
    def global_path(self) -> Path:
        assert self.pmb_home is not None
        return self.pmb_home / "config.yaml"

    @property
    def workspace_path(self) -> Path:
        assert self.workspace_dir is not None
        return self.workspace_dir / "config.yaml"

    # -- I/O --
    @staticmethod
    def _load(p: Path) -> dict[str, Any]:
        if not p.exists():
            return {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                return {}
            return _flatten(data)
        except Exception:
            return {}

    @staticmethod
    def _save(p: Path, flat: dict[str, Any]) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            yaml.safe_dump(_unflatten(flat), f, sort_keys=False, allow_unicode=True)

    # -- lookup --
    def get(self, key: str) -> Any:
        if key not in SCHEMA:
            raise KeyError(f"unknown config key: {key!r}")
        setting = SCHEMA[key]
        for source in (self._overrides, self._workspace, self._global):
            if key in source and source[key] is not None and source[key] != "":
                try:
                    return _coerce(source[key], setting)
                except Exception:
                    continue
        return setting.default

    def effective(self) -> dict[str, Any]:
        """All keys with resolved values."""
        return {k: self.get(k) for k in SCHEMA}

    def source_of(self, key: str) -> str:
        """Where the current value comes from: override|workspace|global|default."""
        if key in self._overrides:
            return "override"
        if key in self._workspace:
            return "workspace"
        if key in self._global:
            return "global"
        return "default"

    # -- mutation --
    def set_workspace(self, key: str, value: Any) -> Any:
        """Set in the per-workspace file. Returns the typed value stored."""
        if key not in SCHEMA:
            raise KeyError(f"unknown config key: {key!r}")
        typed = _coerce(value, SCHEMA[key])
        self._workspace[key] = typed
        if self.workspace_dir:
            self._save(self.workspace_path, self._workspace)
        return typed

    def set_global(self, key: str, value: Any) -> Any:
        if key not in SCHEMA:
            raise KeyError(f"unknown config key: {key!r}")
        typed = _coerce(value, SCHEMA[key])
        self._global[key] = typed
        if self.pmb_home:
            self._save(self.global_path, self._global)
        return typed

    def reset_workspace(self, key: Optional[str] = None) -> None:
        """Remove key (or all keys) from the per-workspace file."""
        if key is None:
            self._workspace.clear()
        elif key in self._workspace:
            del self._workspace[key]
        if self.workspace_dir:
            self._save(self.workspace_path, self._workspace)
