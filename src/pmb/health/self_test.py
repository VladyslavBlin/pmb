"""
Self-Test Runner — quantifies memory degradation over time.

Идея: периодически (раз в неделю) система берёт случайные старые memories,
формирует тестовые запросы из них, и пытается их вспомнить через recall.
Если recall accuracy падает — это сигнал деградации.

Метрика: % старых memories которые recall находит в top-K за их же ключевыми
словами/фразой.

Подход к генерации queries:
- Для каждого выбранного event берём первые 8-15 значимых токенов content'а
- Это становится query
- Expected — тот же event_ulid

Это "scratch your own itch" benchmark — измеряет на реальных данных юзера,
не на синтетике. Результаты сохраняются в health_log.jsonl и доступны через
`pmb health` CLI.

Anti-bias measure: query сильно отличается от content (только подмножество
токенов в случайном порядке) — иначе trivial.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from pmb.core.engine import Engine


# Слова-стопы которые не годятся как query keywords
STOPWORDS = {
    "это", "что", "как", "если", "когда", "то", "мы", "я", "ты", "он", "она",
    "его", "её", "их", "наш", "ваш", "мой", "твой", "тот", "этот", "там",
    "тут", "вот", "уже", "ещё", "только", "также", "ну", "же", "ли", "бы",
    "можно", "нужно", "может", "должен", "и", "а", "но", "или", "что-то",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "should",
    "can", "could", "may", "might", "must", "and", "or", "but", "if",
    "of", "in", "on", "at", "to", "for", "with", "from", "by", "as",
    "user", "assistant", "q", "a",
}


def _significant_tokens(text: str, n_keep: int = 8) -> list[str]:
    """
    Извлечь значимые токены: слова >= 3 chars, не stopwords, без дубликатов.
    """
    raw = re.findall(r"\w+", text.lower())
    seen = set()
    out = []
    for tok in raw:
        if len(tok) < 3:
            continue
        if tok in STOPWORDS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= n_keep:
            break
    return out


def generate_test_query(content: str, rng: random.Random) -> Optional[str]:
    """
    Генерирует test query из content'а event'а.

    Берём 4-7 значимых токенов в случайном порядке. Если меньше 4 значимых
    токенов — событие не годится для self-test (слишком короткое).
    """
    tokens = _significant_tokens(content, n_keep=12)
    if len(tokens) < 4:
        return None
    n = rng.randint(4, min(7, len(tokens)))
    sample = rng.sample(tokens, n)
    return " ".join(sample)


@dataclass
class SelfTestResult:
    timestamp: float
    n_tested: int
    accuracy_at_1: float
    accuracy_at_3: float
    accuracy_at_5: float
    avg_rank: Optional[float]
    failed_queries: list[dict] = field(default_factory=list)
    workspace_id: str = ""
    n_total_active: int = 0
    # Session-aware loose metric: counts as success if ANY event from the
    # same session appears in top-K. Closer to "did the retriever find the
    # right neighborhood" than the strict exact-ulid match. Falls back to
    # strict when events have no session_id.
    session_accuracy_at_5: Optional[float] = None
    session_coverage: float = 0.0  # fraction of samples that had a session_id
    # When n_tested == 0, why: "no_content_events" or "all_events_younger_than_min_age"
    empty_reason: Optional[str] = None
    eligible_min_age_days: float = 1.0
    n_too_recent: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class SelfTestRunner:
    """
    Runs self-tests and persists results in health_log.jsonl.
    """

    def __init__(self, engine: "Engine", seed: Optional[int] = None):
        self.engine = engine
        self.rng = random.Random(seed if seed is not None else int(time.time()))

    def _log_path(self) -> Path:
        return self.engine.workspace.storage_dir / "health_log.jsonl"

    def run(
        self,
        n_samples: int = 20,
        min_age_days: float = 1.0,
        top_k_max: int = 10,
        max_failed_to_save: int = 5,
    ) -> SelfTestResult:
        """
        Запустить self-test.

        Berut events старше min_age_days, sample n_samples из них, генерирует
        query из каждого, прогоняет recall, считает попадания.

        Args:
            n_samples: сколько events протестировать
            min_age_days: минимальный возраст event'а (свежие skip — слишком easy)
            top_k_max: сколько результатов запрашивать у recall
            max_failed_to_save: сохраняем детали failed queries
        """
        workspace_id = self.engine.workspace.id
        active = self.engine.events.list_active(workspace_id, limit=10000)

        # Filter: старше min_age_days и контентные (qa, fact, git)
        cutoff_ts = time.time() - min_age_days * 86400.0
        eligible_types = {"qa", "fact", "git"}
        eligible = [
            e for e in active
            if e.timestamp <= cutoff_ts and e.event_type in eligible_types
        ]

        if not eligible:
            # Distinguish "empty workspace" from "everything is too fresh" — both
            # produce zero results but the second one tells the user to wait.
            n_recent = sum(
                1 for e in active
                if e.timestamp > cutoff_ts and e.event_type in eligible_types
            )
            reason = (
                "no_content_events"
                if not any(e.event_type in eligible_types for e in active)
                else "all_events_younger_than_min_age"
                if n_recent > 0
                else "no_content_events"
            )
            return SelfTestResult(
                timestamp=time.time(),
                n_tested=0,
                accuracy_at_1=0.0,
                accuracy_at_3=0.0,
                accuracy_at_5=0.0,
                avg_rank=None,
                workspace_id=workspace_id,
                n_total_active=len(active),
                empty_reason=reason,
                eligible_min_age_days=min_age_days,
                n_too_recent=n_recent,
            )

        # Sample
        sample_size = min(n_samples, len(eligible))
        samples = self.rng.sample(eligible, sample_size)

        n_at_1 = 0
        n_at_3 = 0
        n_at_5 = 0
        n_session_at_5 = 0
        n_with_session = 0
        ranks = []
        failed = []

        # Map session_id -> set(ulids) for loose-mode success check
        session_map: dict[str, set[str]] = {}
        for e in active:
            if e.source_session_id:
                session_map.setdefault(e.source_session_id, set()).add(e.ulid)

        for ev in samples:
            query = generate_test_query(ev.to_text(), self.rng)
            if not query:
                continue

            pack = self.engine.recall(query=query, top_k=top_k_max)
            rank = None
            for i, r in enumerate(pack.results, 1):
                if r.ulid == ev.ulid:
                    rank = i
                    break

            # Session-loose match: any same-session ulid within top-5
            session_hit = False
            if ev.source_session_id:
                n_with_session += 1
                sibling_set = session_map.get(ev.source_session_id, set())
                for r in pack.results[:5]:
                    if r.ulid in sibling_set:
                        session_hit = True
                        break

            if rank is not None:
                ranks.append(rank)
                if rank <= 1:
                    n_at_1 += 1
                if rank <= 3:
                    n_at_3 += 1
                if rank <= 5:
                    n_at_5 += 1
            else:
                if len(failed) < max_failed_to_save:
                    failed.append({
                        "ulid": ev.ulid,
                        "query": query,
                        "expected_content_preview": ev.content[:100],
                        "top3_in_results": [
                            {"ulid": r.ulid, "score": r.score, "content_preview": r.content[:60]}
                            for r in pack.results[:3]
                        ],
                    })
            if session_hit:
                n_session_at_5 += 1

        n_tested = len([s for s in samples if generate_test_query(s.to_text(), self.rng) is not None])
        # n_tested здесь нестабилен (rng state), считаем правильно через ranks + failed
        n_tested = len(ranks) + len(failed) + (sample_size - len(ranks) - len(failed))
        # Простая правильная формула:
        n_attempted = sample_size
        # Но queries которые не удалось сгенерировать (None) тоже нужно вычесть
        # Reset and count properly:
        attempted = 0
        for s in samples:
            if generate_test_query(s.to_text(), random.Random(0)) is not None:
                attempted += 1
        n_tested = max(attempted, len(ranks) + len(failed))

        session_acc = (n_session_at_5 / n_with_session) if n_with_session else None
        result = SelfTestResult(
            timestamp=time.time(),
            n_tested=n_tested,
            accuracy_at_1=n_at_1 / max(n_tested, 1),
            accuracy_at_3=n_at_3 / max(n_tested, 1),
            accuracy_at_5=n_at_5 / max(n_tested, 1),
            avg_rank=(sum(ranks) / len(ranks)) if ranks else None,
            failed_queries=failed,
            workspace_id=workspace_id,
            n_total_active=len(active),
            session_accuracy_at_5=session_acc,
            session_coverage=(n_with_session / max(n_tested, 1)) if n_tested else 0.0,
        )

        self._append_log(result)
        return result

    def _append_log(self, result: SelfTestResult):
        log = self._log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")

    def history(self, limit: int = 20) -> list[SelfTestResult]:
        """Прочитать historic self-test results."""
        log = self._log_path()
        if not log.exists():
            return []
        results = []
        with open(log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    results.append(SelfTestResult(**data))
                except Exception:
                    continue
        return results[-limit:]

    def trend(self) -> dict:
        """
        Trend analysis: есть ли деградация?

        Returns:
        {
            "n_runs": int,
            "first_run_acc5": float,
            "last_run_acc5": float,
            "delta_pp": float,
            "verdict": "stable" | "degrading" | "improving" | "insufficient"
        }
        """
        hist = self.history(limit=100)
        if len(hist) < 2:
            return {"n_runs": len(hist), "verdict": "insufficient"}

        first = hist[0].accuracy_at_5
        last = hist[-1].accuracy_at_5
        delta = (last - first) * 100  # pp

        if abs(delta) < 5.0:
            verdict = "stable"
        elif delta < 0:
            verdict = "degrading"
        else:
            verdict = "improving"

        return {
            "n_runs": len(hist),
            "first_run_acc5": first,
            "last_run_acc5": last,
            "delta_pp": delta,
            "verdict": verdict,
            "first_timestamp": hist[0].timestamp,
            "last_timestamp": hist[-1].timestamp,
        }
