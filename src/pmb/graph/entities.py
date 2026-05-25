"""
Entity extraction from raw event text.

Rule-based and fast — runs at event-write time. Designed so we never block
on an LLM call during normal use. Deep semantic extraction can be added
later in the consolidation pass.

Three layers:

1. **File paths.** Regex for posix-style paths (`src/auth.py`, `tests/test_x.py`)
   and bare filenames with a programming-language extension. Plus the
   `files_changed` metadata field already populated by git sync.

2. **Tech names.** Closed-set match against `KNOWN_TECHS`. Trades recall for
   precision — better to miss `Bun` than to call every English word a tech.
   Case-insensitive but only on word boundaries.

3. **Concepts.** Conservative noun-like tokens (length ≥ 4, not in stopword
   list, not pure-digit). Capped per event to avoid bloat.

All entity names are normalized: lowercased, whitespace-collapsed. Files
keep their case to stay matchable against real file paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


# Known technologies — closed set, lowercase canonical name → display alias list.
# Match against any alias on word boundaries.
KNOWN_TECHS: dict[str, list[str]] = {
    "postgres": ["postgres", "postgresql", "pg"],
    "mysql": ["mysql"],
    "sqlite": ["sqlite"],
    "redis": ["redis"],
    "mongodb": ["mongodb", "mongo"],
    "kafka": ["kafka"],
    "rabbitmq": ["rabbitmq"],
    "docker": ["docker"],
    "kubernetes": ["kubernetes", "k8s"],
    "terraform": ["terraform"],
    "ansible": ["ansible"],
    "fastapi": ["fastapi"],
    "django": ["django"],
    "flask": ["flask"],
    "starlette": ["starlette"],
    "react": ["react"],
    "nextjs": ["nextjs", "next.js"],
    "vue": ["vue", "vuejs"],
    "vite": ["vite"],
    "webpack": ["webpack"],
    "rollup": ["rollup"],
    "esbuild": ["esbuild"],
    "tailwind": ["tailwind", "tailwindcss"],
    "typescript": ["typescript", "ts"],
    "python": ["python"],
    "rust": ["rust"],
    "golang": ["golang", "go"],
    "node": ["node", "nodejs"],
    "bun": ["bun"],
    "deno": ["deno"],
    "jwt": ["jwt"],
    "oauth": ["oauth", "oauth2"],
    "graphql": ["graphql"],
    "rest": ["rest"],
    "grpc": ["grpc"],
    "pytest": ["pytest"],
    "jest": ["jest"],
    "vitest": ["vitest"],
    "lancedb": ["lancedb"],
    "qdrant": ["qdrant"],
    "pinecone": ["pinecone"],
    "chroma": ["chroma", "chromadb"],
    "anthropic": ["anthropic", "claude"],
    "openai": ["openai", "gpt", "gpt-4", "gpt-4o", "codex"],
    "ollama": ["ollama"],
    "huggingface": ["huggingface", "hf"],
    "llama": ["llama", "llama3"],
    "qwen": ["qwen"],
    "mistral": ["mistral"],
}


# Build a single combined regex for all aliases for one pass over text
def _build_tech_regex() -> re.Pattern:
    aliases = []
    for variants in KNOWN_TECHS.values():
        for v in variants:
            aliases.append(re.escape(v))
    # Sort longest-first so 'nextjs' matches before 'next'
    aliases.sort(key=len, reverse=True)
    pattern = r"(?<![\w/.-])(?:" + "|".join(aliases) + r")(?!\w)"
    return re.compile(pattern, re.IGNORECASE)


_TECH_RE = _build_tech_regex()


# File path: posix-style with a known extension, or bare filename.ext
_FILE_RE = re.compile(
    r"(?<![\w/])"
    r"(?:[A-Za-z0-9_\-./]+/)*"               # optional dirs
    r"[A-Za-z0-9_\-.]+"
    r"\.(?:py|js|ts|tsx|jsx|go|rs|java|kt|swift|rb|php|c|h|cpp|hpp|cs|"
    r"sql|sh|ps1|yml|yaml|toml|json|md|html|css|scss|vue|svelte|conf|"
    r"ini|env|lock|cfg|exe|dll|so|dylib|bat|cmd)\b"
)

# Windows-style absolute paths: C:\foo\bar\..., \\server\share\..., or any
# token containing 2+ backslashes / forward slashes. The concept extractor
# strips these wholesale before tokenizing so AppData/Roaming/Users etc.
# don't pollute the concept set.
_WINPATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\)[A-Za-z0-9_\-.\\/ :]+",
    re.IGNORECASE,
)


_STOPWORDS = {
    # Articles, demonstratives, pronouns
    "the", "a", "an", "this", "that", "these", "those", "it", "we", "you",
    "they", "them", "their", "his", "her", "hers", "him", "she", "him",
    "your", "yours", "mine", "ours", "us", "our",
    # Auxiliary / modal verbs
    "is", "are", "was", "were", "be", "been", "being", "have", "has",
    "had", "do", "does", "did", "will", "would", "should", "could", "may",
    "might", "must", "can", "cannot", "shall", "want", "wants", "wanted",
    "need", "needs", "needed", "got", "get", "gets", "getting", "make",
    "makes", "made", "making", "take", "takes", "took", "taken",
    # Conjunctions / discourse
    "and", "or", "but", "if", "then", "so", "because", "since", "though",
    "although", "however", "while", "until", "unless", "even", "either",
    "neither", "nor", "yet", "still",
    # Prepositions
    "of", "in", "on", "at", "to", "for", "with", "from", "by", "as",
    "into", "onto", "upon", "over", "under", "above", "below", "between",
    "among", "against", "around", "about", "after", "before", "behind",
    "during", "through", "throughout", "across", "along", "via", "near",
    # Question / Wh-words
    "what", "where", "who", "whom", "whose", "when", "how", "why", "which",
    "whether",
    # Dialogue roles (filter so "user" / "agent" / "assistant" don't bloat)
    "user", "agent", "assistant", "system", "bot", "human", "ai",
    "chatbot", "model", "tool",
    # Generic technical terms — too common to be useful as concept nodes
    "code", "function", "class", "method", "value", "name", "data",
    "type", "object", "instance", "default", "config", "setting",
    "settings", "param", "params", "arg", "args",
    # Action / connector words
    "using", "used", "use", "uses", "based", "set", "sets", "setting",
    "got", "gets", "done", "doing",
    # Time-related fragments
    "today", "yesterday", "tomorrow", "now", "later", "soon", "ago",
    "week", "weeks", "month", "months", "year", "years", "day", "days",
    "hour", "hours", "minute", "minutes",
    # Conversation filler
    "okay", "yeah", "yes", "sure", "right", "good", "great", "fine",
    "thanks", "thank", "please", "hello", "hi", "hey", "bye",
    "sounds", "looks", "seems", "say", "says", "said", "tell", "told",
    "ask", "asked", "talk", "talked", "speak", "spoke",
    # Common verbs (past tense often pollutes)
    "decided", "decides", "deciding", "switched", "switches", "switching",
    "broke", "breaks", "broken", "made", "makes", "making",
    "ran", "runs", "running", "went", "goes", "going",
    "came", "comes", "coming", "lived", "lives", "living",
    "added", "adds", "adding", "removed", "removes", "removing",
    "flew", "flies", "flying", "moved", "moves", "moving",
    # Misc descriptors
    "primary", "secondary", "old", "new", "latest", "current", "previous",
    "first", "second", "third", "last", "next", "final", "initial",
    "main", "core", "basic", "simple", "complex", "important", "key",
    # Self-referential project nouns
    "project", "session", "layer", "feature", "module", "service",
    "endpoint", "endpoints", "request", "response",
    "frontend", "backend", "fullstack", "client", "server", "database",
    "cache", "queue", "worker", "pipeline", "monorepo", "platform",
    "framework", "library", "package", "bundle", "build", "deploy",
    "deployment", "release", "version", "support",
    # Common path / Windows folder tokens that survive as bare concepts
    "appdata", "roaming", "programfiles", "programdata", "users",
    "desktop", "documents", "downloads", "system32", "windows", "apps",
    # Acronym fragments that aren't useful as nodes
    "lgbtq", "lgbt", "covid",
}


_CONCEPT_RE = re.compile(r"\b[a-z][a-z0-9_]{3,}\b")


def extract_file_paths(text: str) -> list[str]:
    """Find file paths inside text. Case-preserving."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _FILE_RE.finditer(text):
        norm = m.group(0).replace("\\", "/").strip()
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def extract_techs(text: str) -> list[str]:
    """Return canonical tech names (lowercased) mentioned in text."""
    seen: set[str] = set()
    # Map alias → canonical for fast lookup
    alias_to_canon: dict[str, str] = {}
    for canon, aliases in KNOWN_TECHS.items():
        for a in aliases:
            alias_to_canon[a.lower()] = canon

    for m in _TECH_RE.finditer(text):
        canon = alias_to_canon.get(m.group(0).lower())
        if canon and canon not in seen:
            seen.add(canon)
    return sorted(seen)


def extract_concepts(text: str, max_n: int = 8) -> list[str]:
    """
    Conservative concept extraction: lowercase tokens of length ≥ 4 that
    aren't stopwords, aren't already tech names (those go to extract_techs),
    aren't pure digits, and aren't substrings of file paths.

    Capped at `max_n` to keep edge density manageable.
    """
    tech_set = set(extract_techs(text))
    file_blob = " ".join(extract_file_paths(text)).lower()

    # Strip Windows-style paths BEFORE tokenizing so AppData/Roaming/Users
    # don't leak into the concept set.
    scrubbed = _WINPATH_RE.sub(" ", text)

    seen: set[str] = set()
    out: list[str] = []
    for m in _CONCEPT_RE.finditer(scrubbed.lower()):
        tok = m.group(0)
        if tok in _STOPWORDS or tok in seen:
            continue
        if tok in tech_set:
            continue
        if tok.isdigit():
            continue
        if tok in file_blob:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= max_n:
            break
    return out


@dataclass
class ExtractedEntities:
    files: list[str]
    techs: list[str]
    concepts: list[str]

    def all_named(self) -> list[tuple[str, str]]:
        """Returns [(kind, name), ...] in stable order."""
        out = [("file", f) for f in self.files]
        out += [("tech", t) for t in self.techs]
        out += [("concept", c) for c in self.concepts]
        return out


class EntityExtractor:
    """Stateless wrapper around the three layers."""

    def __init__(self, max_concepts: int = 8):
        self.max_concepts = max_concepts

    def extract(self, text: str, files_hint: Iterable[str] = ()) -> ExtractedEntities:
        """
        Extract entities. `files_hint` lets the caller pass git-sync's
        `files_changed` metadata so file entities don't depend on regex
        finding them inside the formatted commit text.
        """
        files = list(dict.fromkeys(
            [*extract_file_paths(text), *(_normalize_path(f) for f in files_hint)]
        ))
        techs = extract_techs(text)
        concepts = extract_concepts(text, max_n=self.max_concepts)
        return ExtractedEntities(files=files, techs=techs, concepts=concepts)


def _normalize_path(p: str) -> str:
    return p.replace("\\", "/").strip()
