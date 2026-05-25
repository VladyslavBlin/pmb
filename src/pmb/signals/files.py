"""
File correlation tracking.

Идея: какие файлы часто модифицируются вместе → они логически связаны.
Когда юзер потом смотрит файл X, можно показать events связанные с files
которые часто modified together with X.

Подход: out-of-process наблюдение (нет hooks внутри Claude Code).
Сейчас работает через git: смотрим какие файлы в одном commit'е,
и считаем co-occurrence.

В дальнейшем можно добавить:
- Inotify/FSEvents наблюдение
- Hook от Claude Code (если станет API)
"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pmb.core.engine import Engine


class FileCorrelation:
    """Анализ co-occurrence файлов в git commits."""

    def __init__(self, engine: "Engine"):
        self.engine = engine

    def _get_git_events(self) -> list:
        return self.engine.events.list_active(
            self.engine.workspace.id, limit=10000, event_type="git",
        )

    def correlations(self, file_path: str, top_k: int = 10) -> list[tuple[str, int]]:
        """
        Найти файлы которые чаще всего модифицируются вместе с file_path.

        Returns list of (file_path, count) sorted descending.
        """
        target = file_path.replace("\\", "/").strip()
        events = self._get_git_events()

        co_occur: Counter = Counter()
        for ev in events:
            files = ev.metadata.get("files_changed", [])
            files_norm = [f.replace("\\", "/").strip() for f in files]
            if target in files_norm:
                for other in files_norm:
                    if other != target:
                        co_occur[other] += 1

        return co_occur.most_common(top_k)

    def all_files_touched(self, since_days: int = 30) -> list[tuple[str, int]]:
        """Все файлы упомянутые в git events за последние N дней + count."""
        events = self._get_git_events()
        cutoff = time.time() - since_days * 86400

        counter: Counter = Counter()
        for ev in events:
            if ev.timestamp < cutoff:
                continue
            for f in ev.metadata.get("files_changed", []):
                counter[f.replace("\\", "/")] += 1

        return counter.most_common()

    def file_history(self, file_path: str) -> list[dict]:
        """История commit'ов для одного файла."""
        target = file_path.replace("\\", "/").strip()
        events = self._get_git_events()

        history = []
        for ev in events:
            files = [f.replace("\\", "/").strip()
                     for f in ev.metadata.get("files_changed", [])]
            if target in files:
                history.append({
                    "ulid": ev.ulid,
                    "sha": ev.metadata.get("short_sha"),
                    "subject": ev.metadata.get("subject", ev.content[:80]),
                    "timestamp": ev.timestamp,
                    "author": ev.metadata.get("author"),
                })
        history.sort(key=lambda x: -x["timestamp"])
        return history
