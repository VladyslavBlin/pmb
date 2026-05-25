"""Seed the current PMB workspace with demo data so the dashboard has
something to show."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pmb.core.engine import Engine


def main():
    eng = Engine()
    print(f"Workspace: {eng.workspace.name} ({eng.workspace.id})")

    # 1. Plain facts
    eng.record_fact("We use Postgres 17 on port 5433 for the api service")
    eng.record_fact("FastAPI app lives in src/api/main.py with async endpoints")
    eng.record_fact("Redis is used for session storage on port 6379")
    eng.record_fact("Switched from MySQL to Postgres for JSONB support last week")
    eng.record_fact("Frontend is React 18 with Vite in apps/web/")

    # 2. Q/A pairs (with speaker → triggers person extraction)
    eng.record_event(
        event_type="qa",
        content="User: How do I restart Postgres?\nAgent: systemctl restart postgresql on the host",
        metadata={"speaker": "user"},
    )
    eng.record_event(
        event_type="qa",
        content="Alice: I'm flying to Paris on January 8 for the conference\nBob: That sounds great",
        metadata={"speaker": "Alice"},
    )
    eng.record_event(
        event_type="qa",
        content="Bob: I met Caroline at the LGBTQ conference last week\nAlice: How was it?",
        metadata={"speaker": "Bob"},
    )

    # 3. Code (triggers code AST extraction)
    eng.record_event(
        event_type="code",
        content=(
            "import asyncpg\n"
            "from fastapi import FastAPI\n\n"
            "app = FastAPI()\n\n"
            "async def authenticate(user, password):\n"
            "    '''Verify user credentials against Postgres.'''\n"
            "    return user == 'admin'\n\n"
            "class AuthManager:\n"
            "    def login(self, user): pass\n"
            "    def logout(self, user): pass\n"
        ),
    )

    # 4. Decisions
    eng.record_event(
        event_type="decision",
        content="Decided on Postgres over MySQL for JSONB and stronger types",
    )
    eng.record_event(
        event_type="decision",
        content="Use Redis cache layer between FastAPI and Postgres for session data",
    )

    # Stats
    n = eng.events.count(eng.workspace.id, include_archived=False)
    gstats = eng.graph_stats()
    print(f"Events: {n}")
    print(f"Graph entities: {gstats['n_entities']}, edges: {gstats['n_edges']}")
    print(f"By kind: {gstats['by_kind']}")


if __name__ == "__main__":
    main()
