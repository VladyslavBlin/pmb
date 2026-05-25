"""
LRU cache for recall results — the "working memory" buffer in front of the
hybrid search. Two purposes:

  1. Speed. If the agent issues the same recall twice within the TTL we
     skip the full embed + LanceDB + BM25 + graph + rerank pipeline.
  2. Stability. Agents often repeat the same query verbatim across turns;
     returning the exact same result keeps the conversation coherent.

Cache invalidation:
  - Time-based: entries expire after `ttl_seconds`.
  - Write-based: every event write bumps a workspace `generation` counter;
    entries born under an older generation are stale and dropped on the
    next get(). This is cheap and correct: any change to the corpus
    invalidates everything (we don't try to be clever about which queries
    are affected).
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class _Entry:
    value: Any
    born_at: float
    generation: int


class RecallCache:
    """Tiny LRU cache. Thread-unsafe — Engine is single-process anyway."""

    def __init__(self, max_size: int = 128, ttl_seconds: float = 300.0):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._data: OrderedDict[str, _Entry] = OrderedDict()
        self._generation = 0
        self.hits = 0
        self.misses = 0

    @property
    def enabled(self) -> bool:
        return self.max_size > 0

    def bump_generation(self) -> None:
        """Mark every existing entry stale (called after writes)."""
        self._generation += 1

    def get(self, key: str) -> Optional[Any]:
        if not self.enabled:
            return None
        ent = self._data.get(key)
        if ent is None:
            self.misses += 1
            return None
        if ent.generation != self._generation:
            del self._data[key]
            self.misses += 1
            return None
        if self.ttl_seconds > 0 and (time.time() - ent.born_at) > self.ttl_seconds:
            del self._data[key]
            self.misses += 1
            return None
        # LRU touch
        self._data.move_to_end(key)
        self.hits += 1
        return ent.value

    def put(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = _Entry(
            value=value, born_at=time.time(), generation=self._generation,
        )
        while len(self._data) > self.max_size:
            self._data.popitem(last=False)

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "size": len(self._data),
            "max_size": self.max_size,
            "ttl_seconds": self.ttl_seconds,
            "generation": self._generation,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (self.hits / total) if total else 0.0,
        }

    def clear(self) -> None:
        self._data.clear()
        self.hits = 0
        self.misses = 0


def make_recall_cache_key(
    query: str, top_k: int, recency_half_life_days: float,
    graph_boost: float, rerank: bool, rerank_top_n: int,
) -> str:
    """Stable key. We deliberately exclude time-of-day — TTL handles that."""
    return f"{query}|{top_k}|{recency_half_life_days}|{graph_boost}|{rerank}|{rerank_top_n}"
