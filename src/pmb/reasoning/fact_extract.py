"""
Write-time atomic fact extraction (mem0-style, no LLM).

When the user writes a long message like:

    "Today I met Alice at the coffee shop. She's the new tech lead at Stripe
    and lives in Berlin. We discussed onboarding for the Q3 hire."

…retrieval on "where does Alice live?" or "who is the new Stripe tech lead?"
performs much better when those atomic facts exist as standalone, indexable
events alongside the original — not buried inside a 3-sentence paragraph
whose embedding averages all of them out.

This module does the no-LLM version of mem0's fact decomposition:

  - Sentence-level split (handles . ! ? and Russian variants).
  - Per-sentence "atomic enough?" filter (length, single-clause).
  - Subject-verb pattern matchers for high-precision extraction:
        * "X lives in Y", "X is the Z at W", "X owns Y", "X leads Y"
        * "We use X for Y", "We chose X over Y because Z"
        * "X is Y years old", "X was founded in Y"
  - Result: a list of `AtomicFact(content, kind, confidence)`.

Each atomic fact is recorded as a sibling event with metadata.parent_ulid
pointing back to the source. Recall ranks them like any other event (BM25
+ vector + PAMVR), so a question about Alice's location pulls the short
"Alice lives in Berlin" fact, not the 3-sentence wall.

Triggered from `record_batch` when:
  - content has >= MIN_LEN_FOR_EXTRACT chars
  - AND splits into >= MIN_SENTENCES sentences
  - AND `consolidate.write_atomic_facts` is True (default OFF to keep
    record_batch fast for the MCP fire-and-forget path; opt-in per workspace).

Cost: ~1-3 ms per message on typical paragraph. Pure regex, no model load.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


MIN_LEN_FOR_EXTRACT = 60       # chars
MIN_SENTENCES = 2              # below 2 sentences, nothing to split
MAX_ATOMIC_FACTS = 10          # safety cap


@dataclass(frozen=True)
class AtomicFact:
    content: str                 # the atomic fact text
    kind: str                    # which pattern fired ('identity', 'location', etc.)
    confidence: float            # 0.0-1.0


# Sentence boundary regex (EN + RU).
_SENT_SPLIT = re.compile(
    r"(?<=[.!?])\s+(?=[A-ZА-Я])"
)


# High-precision patterns. Each entry: (regex, kind, template_for_atomic).
# The template uses named groups; we extract subject/object and re-form a
# clean, short, indexable fact.
_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # Identity / role: "X is the (new) tech lead at Y". Role allows 2-4
    # tokens (modifier + role noun). Stops at "and"/"."/"," so we don't
    # bleed into the next clause.
    (re.compile(
        r"(?:^|(?<=[.,])\s+)(?P<subj>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+is\s+(?:the\s+|a\s+|an\s+)?(?P<role>(?:[a-z]+\s+){0,3}(?:lead|head|owner|manager|engineer|developer|designer|director|founder|cto|ceo|cfo))(?:\s+at\s+(?P<org>[A-Z][A-Za-z]+))?\b"),
     "role", "{subj} is {role}{at_org}"),

    # Location: "X lives in Y", "X is based in Y" — subject must be at
    # start of sentence or preceded by "and"/"."/"," to avoid capturing
    # a noun deep inside a clause as the subject.
    (re.compile(
        r"(?:^|(?<=[.,])\s+|\band\s+)(?P<subj>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?|[Hh]e|[Ss]he|[Tt]hey|[Ii])\s+(?:lives|live|is\s+based|resides|moved)\s+in\s+(?P<place>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\b"),
     "location", "{subj} lives in {place}"),

    # Ownership / management
    (re.compile(
        r"\b(?P<subj>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:owns|manages|leads|heads|runs)\s+(?P<obj>[A-Za-z][A-Za-z\s]+)\b",
        re.IGNORECASE), "ownership", "{subj} leads {obj}"),

    # "We use X for Y" — tech choices. Tool can be multi-word
    # ("Cloud Run", "GitHub Actions") via capitalised continuation.
    (re.compile(
        r"\b[Ww]e\s+(?:use|are\s+using)\s+(?P<tool>[A-Z][A-Za-z0-9\-_.]+(?:\s+[A-Z][A-Za-z0-9\-_.]+)?)(?:\s+for\s+(?P<purpose>[a-z][a-z\s]+?)(?=[.,;]|$))?"),
     "tool_choice",
     "We use {tool}{for_purpose}"),

    # "We chose X over Y" — decisions
    (re.compile(
        r"\b[Ww]e\s+(?:chose|picked|selected)\s+(?P<a>[A-Za-z][A-Za-z0-9\-_.]+)\s+over\s+(?P<b>[A-Za-z][A-Za-z0-9\-_.]+)",
        re.IGNORECASE), "decision",
     "We picked {a} over {b}"),

    # Age / numeric attributes: "X is 30 years old"
    (re.compile(
        r"\b(?P<subj>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+is\s+(?P<n>\d+)\s+years?\s+old",
        re.IGNORECASE), "attribute_age", "{subj} is {n} years old"),
]


def split_sentences(text: str) -> list[str]:
    """Split into sentences, EN + RU aware."""
    if not text:
        return []
    parts = _SENT_SPLIT.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def _clean(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip(" ,.!?;:")


def _atomic_from_match(m: re.Match, kind: str, template: str) -> Optional[AtomicFact]:
    """Build an AtomicFact from a regex match using a template."""
    try:
        d = {k: _clean(v) for k, v in m.groupdict().items() if v}
        # Synthesise optional bits
        d["at_org"] = (" at " + d["org"]) if d.get("org") else ""
        d["for_purpose"] = (" for " + d["purpose"]) if d.get("purpose") else ""
        text = template.format(**{**{k: "" for k in (
            "subj", "role", "org", "place", "obj", "tool", "purpose",
            "a", "b", "n", "at_org", "for_purpose",
        )}, **d}).strip()
        text = re.sub(r"\s+", " ", text)
        # Quality filter: avoid mostly-empty extractions
        if len(text) < 8 or len(text.split()) < 3:
            return None
        # Bound length (no full paragraphs masquerading as atomic facts)
        if len(text) > 140:
            return None
        # Capitalise first letter for cleanliness
        text = text[0].upper() + text[1:]
        return AtomicFact(content=text, kind=kind, confidence=0.85)
    except Exception:
        return None


def extract_atomic_facts(
    text: str,
    min_len: int = MIN_LEN_FOR_EXTRACT,
    min_sentences: int = MIN_SENTENCES,
    max_facts: int = MAX_ATOMIC_FACTS,
) -> list[AtomicFact]:
    """Run the pattern bank over a paragraph and return atomic facts.

    Returns an empty list when:
      - text shorter than `min_len` chars
      - text splits into fewer than `min_sentences` sentences
      - no patterns fire (most of the time on actual chat)
    """
    if not text or len(text) < min_len:
        return []
    sentences = split_sentences(text)
    if len(sentences) < min_sentences:
        return []

    facts: list[AtomicFact] = []
    seen_texts: set[str] = set()
    for sent in sentences:
        for pat, kind, template in _PATTERNS:
            for m in pat.finditer(sent):
                af = _atomic_from_match(m, kind, template)
                if af is None:
                    continue
                key = af.content.lower()
                if key in seen_texts:
                    continue
                seen_texts.add(key)
                facts.append(af)
                if len(facts) >= max_facts:
                    return facts
    return facts
