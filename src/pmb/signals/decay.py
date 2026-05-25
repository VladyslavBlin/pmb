"""
Importance scoring and forgetting curve.

Это **anti-degradation** механизм:
- Часто-обращаемые memories поднимаются importance (reinforcement)
- Не использованные долго — падают (forgetting)
- При importance < 0.05 и age > 90 дней → archive (не удаляется навсегда)

Formula:
- На каждый recall hit: importance += boost * (1 - importance)  // saturating boost
- Daily decay: importance *= 0.985  (~half-life ~46 days если не используется)
- Pin: importance = 1.0 + флаг pinned (не decay'ся)

Запускается:
- recompute_importance(engine) — пересчитать после batch
- apply_decay(engine) — daily ритуал

В Phase 2 это ручные функции; в Phase 3 будет cron-like background scheduler.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pmb.core.engine import Engine

DAILY_DECAY_FACTOR = 0.985  # ~46 day half-life
RECALL_BOOST_RATE = 0.10
ARCHIVE_THRESHOLD_IMPORTANCE = 0.05
ARCHIVE_MIN_AGE_DAYS = 90


def boost_on_recall(current_importance: float, hit_score: float) -> float:
    """
    Apply boost к importance после recall hit.

    Saturating: чем ближе к 1.0, тем меньше прироста.
    hit_score (0..1) — насколько релевантным был hit (boost'им только сильные).
    """
    if hit_score < 0.3:
        return current_importance
    boost = RECALL_BOOST_RATE * hit_score
    new_imp = current_importance + boost * (1.0 - current_importance)
    return min(1.0, new_imp)


def apply_decay(engine: "Engine", days_since_last_decay: float = 1.0) -> dict:
    """
    Tier-aware daily decay.

    Each tier has its own decay rate (working forgets fast, semantic slow).
    Pinned events (importance >= 0.99) are still fully protected.
    Working-tier events that fall below threshold are archived MUCH sooner
    than episodic/semantic ones — keeps the hot working memory clean.
    """
    from pmb.core.events import TIER_DECAY_FACTORS, TIER_WORKING

    workspace_id = engine.workspace.id
    active = engine.events.list_active(workspace_id, limit=100000)

    n_decayed = 0
    n_archived = 0
    by_tier: dict[str, int] = {}
    now = time.time()
    archive_age_seconds = ARCHIVE_MIN_AGE_DAYS * 86400

    for ev in active:
        # Pinned (importance >= 0.99) не decay'ся
        if ev.importance >= 0.99:
            continue
        # Tier-specific factor; unknown tier falls back to the old constant
        tier_factor = TIER_DECAY_FACTORS.get(ev.tier, DAILY_DECAY_FACTOR)
        factor = tier_factor ** days_since_last_decay
        new_imp = ev.importance * factor

        # Бонус для recently accessed: не падает так сильно
        recent_boost = 0.0
        days_since_access = (now - ev.last_accessed) / 86400.0
        if days_since_access < 7.0:
            recent_boost = 0.05 * (1.0 - days_since_access / 7.0)
        new_imp = min(1.0, new_imp + recent_boost)

        engine.events.update_importance(ev.ulid, new_imp)
        n_decayed += 1
        by_tier[ev.tier] = by_tier.get(ev.tier, 0) + 1

        # Tier-aware archive policy:
        # - working tier: archive when low importance AND >3 days old (fast forget)
        # - other tiers: keep current 90-day floor
        if ev.tier == TIER_WORKING:
            min_age_for_archive = 3 * 86400
        else:
            min_age_for_archive = archive_age_seconds
        age_seconds = now - ev.timestamp
        if new_imp < ARCHIVE_THRESHOLD_IMPORTANCE and age_seconds > min_age_for_archive:
            engine.events.archive(ev.ulid)
            n_archived += 1

    return {
        "n_active_processed": len(active),
        "n_decayed": n_decayed,
        "n_archived": n_archived,
        "decayed_by_tier": by_tier,
    }


def recompute_importance(engine: "Engine") -> dict:
    """
    Пересчитать importance based на access patterns.

    Используется if хочешь reset state — обычно apply_decay достаточно.
    """
    active = engine.events.list_active(engine.workspace.id, limit=100000)
    now = time.time()

    n_updated = 0
    for ev in active:
        if ev.importance >= 0.99:
            continue

        # Base importance по event_type
        base = {
            "qa": 0.5,
            "fact": 0.7,
            "git": 0.5,
            "file": 0.4,
            "test": 0.5,
            "pin": 1.0,
        }.get(ev.event_type, 0.5)

        # Access bonus
        access_factor = min(0.3, ev.access_count * 0.05)

        # Recency factor
        days_old = (now - ev.timestamp) / 86400.0
        if days_old < 7:
            recency = 0.1
        elif days_old < 30:
            recency = 0.05
        elif days_old < 90:
            recency = 0.0
        else:
            recency = -0.1

        new_imp = max(0.0, min(1.0, base + access_factor + recency))
        engine.events.update_importance(ev.ulid, new_imp)
        n_updated += 1

    return {"n_updated": n_updated}
