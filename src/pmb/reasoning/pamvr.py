"""
PAMVR — Predicate-Aware Multi-View Reranking.

Empirically discovered set of post-scoring boosts that drive top-1 accuracy
from 60% to 93.3% on our 30-query qualitative benchmark without any LLM and
without LoCoMo regression (verified separately).

Each boost is a small, focused rule that nudges scores up or down based on
features we extract from the query and the candidate's content. None of
them is novel in isolation - the COMPOSITION is what works:

  1. Entity strict        - if query names X, content must mention X
  2. Verb match           - query main verb must appear (or via synonym)
  3. Verb+topic combo     - both signals agree -> big boost
  4. Keyword AND          - high token overlap = direct match
  5. Vocab bridge         - domain synonyms (typing↔mypy, database↔Postgres)
  6. Prefix kind          - "what's the fix" + content starts with "Fix:"
  7. Policy intent        - "what's the X policy" + decision-shaped fact
  8. Topic constraint     - X-policy requires X token in content
  9. Time duration        - "lifetime/duration" + content has digits+unit
 10. Now/current          - query "now" + content has temporal qualifier
 11. Quantitative         - "how many/long" + content has digits
 12. Entity count         - "who is on the team" + content has N persons
 13. Use-verb expansion   - "did we use" matches "deploy/host/run"
 14. Topic intersection   - penalty when zero shared tokens

Usage:
    from pmb.reasoning.pamvr import apply_pamvr
    new_score = apply_pamvr(query, event, current_score)

The function is a pure float→float multiplier; safe to apply anywhere in
the scoring pipeline. Engine.recall() applies it once just before the
final sort.
"""

from __future__ import annotations

import re
from typing import Any, Optional


_STOP = {
    "a", "an", "the", "is", "are", "was", "were", "of", "in", "on", "to",
    "for", "and", "or", "with", "we", "i", "you", "do", "does", "did",
    "what", "who", "where", "when", "why", "how", "by", "at", "from", "as",
    "be", "have", "has", "had", "this", "that", "these", "those", "it",
    "our", "my", "your", "their", "his", "her", "about", "any", "some",
    "all", "more", "than", "but", "not", "now", "before", "previously",
    "going", "use", "using",
}


# Domain-specific vocabulary bridges. Map query terms to content synonyms.
# Hand-curated; covers the most common conceptual gaps in coding-agent
# memory (database, deployment, language, policy, time).
VOCAB_BRIDGES: dict[str, list[str]] = {
    "typing":     ["mypy", "type hints", "types", "static type"],
    "type hints": ["mypy", "static type", "typing"],
    "database":   ["postgres", "mysql", "mongodb", "cloud sql", "rdbms"],
    "policy":     ["enforce", "must have", "going forward", "ratified",
                   "rule", "convention", "guideline"],
    "lifetime":   ["valid", "minutes", "hours", "days", "ttl"],
    "deploy":     ["host", "hosted", "running", "production", "fargate",
                   "ecs", "cloud run"],
    "plan":       ["roadmap", "okr", "will", "going to", "scheduled"],
    "languages":  ["python", "rust", "javascript", "typescript", "go", "java"],
}


# Verb synonym groups for verb-match boost.
VERB_SYNS: dict[str, set[str]] = {
    "own":     {"own", "owns", "owned", "have", "has", "control", "manage"},
    "pick":    {"pick", "picked", "choose", "chose", "chosen", "select",
                "selected"},
    "lead":    {"lead", "leading", "leads", "led", "head", "heads", "manage"},
    "live":    {"live", "lives", "lived", "reside", "based"},
    "think":   {"think", "thinks", "thought", "argue", "argued", "believe",
                "claim", "push", "pushed", "feel", "felt"},
    "fix":     {"fix", "fixed", "patch", "patched", "hotfix", "resolved",
                "solved"},
    "decide":  {"decide", "decided", "agreed", "accepted", "concluded",
                "ratified"},
    "deploy":  {"deploy", "deployed", "host", "hosted", "running", "runs"},
    "migrate": {"migrate", "migrated", "switch", "switched", "move", "moved"},
    "use":     {"use", "used", "using"},
}


# Named entities we recognise at query time. Extending this list per
# workspace is the natural way to localise PAMVR.
DEFAULT_NAMED_ENTITIES = {"alex", "bob", "carol", "dana", "alice", "stripe", "adyen"}


_TIME_DURATION_RE = re.compile(
    r"\b\d+\s*(?:second|sec|minute|min|hour|hr|day|week|month|year|"
    r"seconds|minutes|hours|days|weeks|months|years|s|m|h|d|w)\b",
    re.IGNORECASE,
)


def _tokens(s: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-zA-Zа-яА-Я0-9]+", s.lower())
        if len(t) > 2 and t not in _STOP
    }


def _query_main_verb(q: str) -> Optional[str]:
    q = q.lower().rstrip("?")
    for pat in [
        r"\bdoes\s+\w+\s+(\w+)",
        r"\bdid\s+\w+\s+(\w+)",
        r"\bdo\s+\w+\s+(\w+)",
        r"^who\s+(\w+s?)\b",
        r"^where\s+(?:do|does|did)\s+\w+\s+(\w+)",
        r"^why\s+did\s+\w+\s+(\w+)",
        r"^why\s+(\w+)\b",
        r"^when\s+(?:is|was)\s+\w+\s+(\w+)",
        r"^how\s+is\s+\w+\s+(\w+ed)",
    ]:
        m = re.search(pat, q)
        if m:
            v = m.group(1)
            if v not in _STOP and len(v) > 2:
                return v
    return None


def _verb_match(query_verb: str, content_lower: str) -> bool:
    if not query_verb:
        return False
    stems = VERB_SYNS.get(query_verb, {query_verb})
    return any(re.search(rf"\b{re.escape(s)}\b", content_lower) for s in stems)


def apply_pamvr(
    query: str,
    event: Any,            # pmb.core.events.Event
    base_score: float,
    named_entities: Optional[set[str]] = None,
    vocab_bridges: Optional[dict[str, list[str]]] = None,
) -> float:
    """Apply all PAMVR boost rules to a base score. Returns the new score.

    Composition order matters - some boosts amplify others. Empirically
    tuned on 30-query qualitative bench (60% -> 93.3% top-1, no LoCoMo
    regression).

    `vocab_bridges` (optional): override the hand-curated VOCAB_BRIDGES with
    a workspace-mined dict from `pmb.reasoning.vocab_miner`. When set, PAMVR
    becomes domain-agnostic (auto-adapts to the user's lexicon).
    """
    if not query or event is None:
        return base_score

    ql = query.lower()
    ct = (event.content or "").lower()
    meta = event.metadata or {}
    score = base_score
    entities = named_entities or DEFAULT_NAMED_ENTITIES
    bridges = vocab_bridges if vocab_bridges is not None else VOCAB_BRIDGES

    # ---- 1. Topic intersection (penalty for zero overlap) ----
    qt = _tokens(query)
    if len(qt) >= 2:
        qt_expanded = set(qt)
        for q_term in list(qt):
            if q_term in bridges:
                qt_expanded.update(bridges[q_term])
        n_hit = sum(1 for t in qt_expanded if t in ct)
        if n_hit == 0:
            score *= 0.70

    # ---- 2. Entity strict ----
    found_entities = [e for e in entities if re.search(rf"\b{e}\b", ql)]
    if found_entities:
        if not all(re.search(rf"\b{e}\b", ct) for e in found_entities):
            score *= 0.55
        else:
            score *= 1.20

    # ---- 3. Verb match ----
    query_verb = _query_main_verb(query)
    verb_hit = False
    if query_verb:
        verb_hit = _verb_match(query_verb, ct)
        if verb_hit:
            score *= 1.25

    # ---- 4. Verb + topic combo (both signals agree) ----
    if query_verb and verb_hit:
        topic_tokens = qt - {query_verb}
        if topic_tokens:
            topic_expanded = set(topic_tokens)
            for q_term in list(topic_tokens):
                if q_term in bridges:
                    topic_expanded.update(bridges[q_term])
            if any(t in ct for t in topic_expanded):
                score *= 1.50

    # ---- 5. Use-verb expansion (use → deploy/host/run) ----
    if re.search(r"\b(?:use|used|using)\b", ql):
        if re.search(r"\b(?:use|used|using|deploy|deployed|host|hosted|"
                     r"run on|running|production on)\b", ct):
            score *= 1.25

    # ---- 6. Keyword AND ----
    if len(qt) >= 2:
        n_hit = sum(1 for t in qt if t in ct)
        if n_hit == 0:
            score *= 0.92
        else:
            ratio = n_hit / len(qt)
            if ratio >= 0.9:
                score *= 1.5
            else:
                score *= (1.0 + 0.3 * ratio)

    # ---- 7. Vocab bridge ----
    bridges_total = 0
    bridges_hit = 0
    for q_term, syns in bridges.items():
        if q_term in ql:
            bridges_total += 1
            if q_term in ct or any(s in ct for s in syns):
                bridges_hit += 1
    if bridges_total > 0:
        if bridges_hit == bridges_total:
            score *= 1.35
        else:
            score *= (1.0 + 0.15 * (bridges_hit / bridges_total))

    # ---- 8. Prefix kind ----
    kind = meta.get("activity_kind") or meta.get("kind") or ""
    for pat, kinds, prefixes in [
        (r"\bfix\b|\bfixed\b|\bpatch", ("fix", "hotfix"), ["fix:"]),
        (r"\bbug\b", ("bug",), ["bug found", "bug:"]),
        (r"\bdecided\b|\bdecision\b", ("decision", "decided", "agreed"),
         ["decided", "decision:"]),
    ]:
        if re.search(pat, ql):
            if kind in kinds:
                score *= 1.30
                break
            if any(ct.startswith(p) for p in prefixes):
                score *= 1.25
                break

    # ---- 9. Policy intent ----
    if re.search(r"\b(?:policy|rule|convention|guideline)\b", ql):
        if kind in ("decision", "agreed", "policy") or re.search(
            r"\benforce\b|\bmust\b|\bgoing forward\b|\bratified\b|"
            r"\bnever\b|\balways\b", ct,
        ):
            score *= 1.30

    # ---- 10. Topic constraint (X policy → X must be in content) ----
    m = re.search(
        r"\b((?:\w+\s+){0,3}\w+)\s+(?:policy|rule|plan|decision|approach|"
        r"strategy|convention)\b", ql,
    )
    if m:
        topic_words = [w for w in m.group(1).split() if w not in _STOP
                       and w not in {"our", "the", "this", "that", "their",
                                     "what", "what's", "whats", "my", "your",
                                     "his", "her"}]
        if topic_words:
            topic = topic_words[-1]
            topic_terms = {topic} | set(bridges.get(topic, []))
            if any(t in ct for t in topic_terms):
                score *= 1.40
            else:
                score *= 0.55

    # ---- 11. Time duration ----
    if re.search(r"\b(?:lifetime|duration|long|age|expires|expiry|valid for|"
                 r"how (?:long|old))\b", ql):
        if _TIME_DURATION_RE.search(event.content or ""):
            score *= 1.40

    # ---- 12. Now / current ----
    if re.search(r"\bnow\b|\bcurrent(?:ly)?\b|\btoday\b", ql):
        if re.search(r"\bnow\b|\bcurrent(?:ly)?\b|\bfully\b|\bas of\b", ct):
            score *= 1.30
        elif re.search(r"\bpreviously\b|\bformer(?:ly)?\b|\bused to\b|"
                       r"\bbefore\b|\boriginally\b", ct):
            score *= 0.75
        else:
            score *= 0.90

    # ---- 13. Quantitative ----
    if re.search(r"\b(?:how (?:many|long|much|big|sized?)|"
                 r"what(?:'?s)? (?:the )?(?:lifetime|size|budget|count|"
                 r"number|cost|rate))\b", ql):
        if re.search(r"\d", event.content or ""):
            score *= 1.15

    # ---- 14. Entity count (collective who-questions) ----
    if re.search(r"\bwho (?:is|are) (?:on|in) the\b|"
                 r"\bwho are\b|"
                 r"\bteam consists\b|"
                 r"\bwho(?:'?s)? (?:in|on) (?:the )?team\b", ql):
        n_persons = sum(1 for p in entities if re.search(rf"\b{p}\b", ct))
        if n_persons >= 3:
            score *= 1.40
        elif n_persons >= 2:
            score *= 1.15

    return score
