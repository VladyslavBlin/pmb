"""
Session tracking.

Session — это logical группировка событий, обычно соответствует одной сессии
работы с агентом (Claude Code, Cursor).

Detection:
- Explicit: `pmb session start [name]`
- Auto: gap > 1 hour между events создаёт новую session

State:
- {workspace_dir}/session.yaml хранит current session_id и start_time
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from pmb.core.workspace import Workspace


SESSION_GAP_SECONDS = 3600  # 1 hour без активности → новая сессия


@dataclass
class Session:
    id: str
    name: str
    started_at: float
    last_activity: float

    def is_stale(self, gap_seconds: float = SESSION_GAP_SECONDS) -> bool:
        return (time.time() - self.last_activity) > gap_seconds

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "started_at": self.started_at,
            "last_activity": self.last_activity,
        }


class SessionTracker:
    """Управление current session для workspace."""

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _state_file(self) -> Path:
        return self.workspace.storage_dir / "session.yaml"

    def current(self, auto_create: bool = True) -> Optional[Session]:
        f = self._state_file()
        if f.exists():
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = yaml.safe_load(fp) or {}
                if data:
                    sess = Session(
                        id=data["id"],
                        name=data.get("name", "default"),
                        started_at=data["started_at"],
                        last_activity=data["last_activity"],
                    )
                    if not sess.is_stale():
                        return sess
            except Exception:
                pass

        if auto_create:
            return self.start()
        return None

    def start(self, name: Optional[str] = None) -> Session:
        sess = Session(
            id=uuid.uuid4().hex[:12],
            name=name or f"session-{time.strftime('%Y%m%d-%H%M', time.gmtime())}",
            started_at=time.time(),
            last_activity=time.time(),
        )
        self._save(sess)
        return sess

    def end(self) -> Optional[Session]:
        sess = self.current(auto_create=False)
        if not sess:
            return None
        # Просто удаляем state file — следующий запрос создаст новую
        f = self._state_file()
        if f.exists():
            f.unlink()
        return sess

    def touch(self) -> Session:
        """Обновить last_activity. Создаёт новую сессию если предыдущая stale."""
        sess = self.current(auto_create=True)
        sess.last_activity = time.time()
        self._save(sess)
        return sess

    def _save(self, sess: Session):
        f = self._state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "w", encoding="utf-8") as fp:
            yaml.safe_dump(sess.to_dict(), fp)
