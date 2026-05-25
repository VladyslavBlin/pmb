"""
Adaptive Layer Routing (Improvement E).

PMB has 6 semantic layers stored in events:
  raw events, entities, reflections, causation edges, arcs, atomic facts,
  bi-temporal index.

Different question types are best answered by different layers:
  "What port does Postgres use?"        → atomic facts (direct lookup)
  "When did Alice meet Bob?"            → temporal index + facts
  "What happened after the migration?"  → causation edges + raw
  "Tell me about the auth refactor"     → arcs + raw
  "Why did Bob leave?"                  → reflections + inferential search

This module:
  1. Classifies the query into one or more intent types via cheap regex.
  2. Returns per-layer multipliers that the recall pipeline applies to
     base scores, biasing which layer surfaces in the top-K.

It does NOT replace existing recall layers — it tunes their relative
contribution PER QUERY. Layers stay always-on (for safety) but their
weight is dynamic.

Cost: <0.1ms (regex over query string). No LLM.

Why this matters:
  Without routing, atomic facts (Improvement D) dominate top-K because they
  outnumber other event types ~15:1. Great for direct lookups, bad for
  narrative questions (where raw session context is richer). Routing
  re-balances based on query intent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ----------------------------------------------------------------------
# Intent detection patterns
# ----------------------------------------------------------------------

_TEMPORAL_RE = re.compile(
    r"\b(when|date|day|month|year|after|before|during|since|until|"
    r"yesterday|today|tomorrow|last|next|ago|earlier|previously|"
    r"january|february|march|april|may|june|july|august|september|"
    r"october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b",
    re.IGNORECASE,
)

_MULTIHOP_RE = re.compile(
    r"\b(after|before|because|due to|caused|led to|then|next|earlier|"
    r"previously|why did|what happened (?:after|when)|following|preceding|"
    r"subsequently|as a result|consequence|prior to|in response|reaction|"
    r"triggered|prompted|since|until)\b",
    re.IGNORECASE,
)

_NARRATIVE_RE = re.compile(
    r"\b(history of|tell me about|what's been|how did .* end up|"
    r"summary of|overview|the story of|arc|journey|evolution|"
    r"what (?:has )?happened (?:with|to)|all about|what do (?:we|you) know about|"
    r"talk through|walk me through|recap|context)\b",
    re.IGNORECASE,
)

_INFERENTIAL_RE = re.compile(
    r"\b(why|would|could|should|might|imply|suggest|seem|appear|"
    r"is .* (?:a|an) |consider(?:ed)? |type of|kind of)\b",
    re.IGNORECASE,
)

# "Direct lookup" pattern — short factual question
_DIRECT_RE = re.compile(
    r"^\s*(what|who|where|which) (is|are|was|were|did) ",
    re.IGNORECASE,
)


# ----------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------

@dataclass
class LayerWeights:
    """Multiplier for each layer's contribution to final recall score.
    1.0 = unchanged. 0.0 = disable layer's boost. 2.0 = double weight."""
    facts_boost: float = 1.0         # event_type='fact_atom' importance
    reflections_boost: float = 1.0   # event_type='reflection' importance
    raw_boost: float = 1.0           # event_type='qa'/'fact'/'event' etc.
    graph_boost_mul: float = 1.0     # multiplier on graph_boost config
    causation_boost_mul: float = 1.0 # multiplier on causation_boost
    arc_boost_mul: float = 1.0       # multiplier on arc_boost
    temporal_boost_mul: float = 1.0  # multiplier on temporal_boost
    ppr_weight_mul: float = 1.0      # multiplier on ppr_weight


@dataclass
class QueryIntent:
    query: str
    types: list[str] = field(default_factory=list)
    weights: LayerWeights = field(default_factory=LayerWeights)
    rationale: str = ""


# ----------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------

class QueryRouter:
    """Classifies queries and returns layer multipliers.

    Pure stateless — same query → same weights.

    Multiple intent types can fire simultaneously; in that case
    multipliers compose (max of competing values, since boosts are
    additive in scoring).
    """

    def classify(self, query: str) -> QueryIntent:
        if not query or not query.strip():
            return QueryIntent(query=query, types=["direct"], rationale="empty")

        q = query.strip()
        types: list[str] = []
        notes: list[str] = []

        if _TEMPORAL_RE.search(q):
            types.append("temporal")
            notes.append("temporal pattern matched")
        if _MULTIHOP_RE.search(q):
            types.append("multi_hop")
            notes.append("multi-hop pattern matched")
        if _NARRATIVE_RE.search(q):
            types.append("narrative")
            notes.append("narrative pattern matched")
        if _INFERENTIAL_RE.search(q):
            types.append("inferential")
            notes.append("inferential pattern matched")

        # Direct lookup if nothing else strongly fired, or query is short
        # factual ("what is X?" style)
        if not types or (_DIRECT_RE.match(q) and len(q.split()) <= 8):
            types.append("direct")
            notes.append("direct lookup pattern")

        # Compose weights
        weights = LayerWeights()

        if "direct" in types:
            # Direct lookups benefit from atomic facts (mem0-style).
            # Raw context is less important; arcs/causation hurt.
            weights.facts_boost = 1.5
            weights.arc_boost_mul = 0.5
            weights.causation_boost_mul = 0.5
            weights.ppr_weight_mul = 0.5

        if "temporal" in types:
            # Temporal questions need date-anchored events.
            # Boost temporal proximity heavily; facts also help for date lookup.
            weights.temporal_boost_mul = max(weights.temporal_boost_mul, 2.0)
            weights.facts_boost = max(weights.facts_boost, 1.3)
            weights.arc_boost_mul = min(weights.arc_boost_mul, 0.7)

        if "multi_hop" in types:
            # Multi-hop needs causation walk + PPR; less single-fact reliance.
            weights.causation_boost_mul = max(weights.causation_boost_mul, 2.0)
            weights.ppr_weight_mul = max(weights.ppr_weight_mul, 1.5)
            weights.graph_boost_mul = max(weights.graph_boost_mul, 1.5)
            weights.facts_boost = max(weights.facts_boost, 1.2)

        if "narrative" in types:
            # Narrative queries want arc summaries + raw context.
            # Penalize fragmented atomic facts.
            weights.arc_boost_mul = max(weights.arc_boost_mul, 2.0)
            weights.raw_boost = max(weights.raw_boost, 1.5)
            weights.facts_boost = min(weights.facts_boost, 0.6)

        if "inferential" in types:
            # "Why / would / should" — reflections capture interpretations.
            weights.reflections_boost = max(weights.reflections_boost, 1.5)
            weights.raw_boost = max(weights.raw_boost, 1.2)

        return QueryIntent(
            query=query,
            types=types,
            weights=weights,
            rationale="; ".join(notes),
        )
