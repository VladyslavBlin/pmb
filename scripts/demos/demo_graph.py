"""Quick visualization of what the PMB entity graph looks like.

Ingests ~15 realistic dev-chat events into a fresh workspace, then dumps:
  - all entities grouped by kind
  - top edges (co-occurrence weights)
  - neighbors of one focal entity (priming/spreading demo)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pmb.core.engine import Engine
from pmb.graph.store import GraphStore


EVENTS = [
    # Postgres cluster
    ("fact", "We use Postgres 17 on port 5433 for the api service"),
    ("fact", "Postgres backup runs nightly at 3am via pg_dump"),
    ("decision", "Switched from MySQL to Postgres for JSONB support"),
    ("qa", "User: how do I restart Postgres? Agent: systemctl restart postgresql"),
    # FastAPI cluster
    ("fact", "FastAPI app lives in src/api/main.py, uses async endpoints"),
    ("decision", "Chose FastAPI over Flask for async and type hints"),
    ("qa", "FastAPI middleware for auth is in src/api/auth.py"),
    # React cluster
    ("fact", "Frontend is React 18 with Vite, in apps/web/"),
    ("decision", "React Query for server state, Zustand for client state"),
    # Cross-links: API + Postgres
    ("fact", "FastAPI connects to Postgres via asyncpg pool size 20"),
    ("qa", "User: where's the db config? Agent: src/api/db.py reads DATABASE_URL"),
    # Cross-links: API + React
    ("fact", "React app calls FastAPI at /api/v1, see apps/web/src/client.ts"),
    # Random isolated nodes
    ("fact", "Redis is used for session storage, port 6379"),
    ("fact", "Docker Compose orchestrates Postgres, Redis, FastAPI for dev"),
    ("qa", "User asked about Postgres backup retention — keep 30 days"),
]


def main():
    tmp_home = Path(tempfile.mkdtemp(prefix="pmb-demo-graph-"))
    tmp_ws = Path(tempfile.mkdtemp(prefix="pmb-demo-ws-"))
    os.environ["PMB_HOME"] = str(tmp_home)

    eng = Engine(cwd=tmp_ws, pmb_home=tmp_home)
    print(f"Workspace: {eng.workspace.id}\n")

    print("=" * 70)
    print(f"Ingesting {len(EVENTS)} events...")
    print("=" * 70)
    for ev_type, content in EVENTS:
        if ev_type == "fact":
            eng.record_fact(content)
        else:
            eng.record_event(event_type=ev_type, content=content, importance=0.5)
    print(f"  done.\n")

    # Open graph store directly
    gs = GraphStore(eng.workspace.db_path)
    wsid = eng.workspace.id

    # Stats
    stats = gs.stats(wsid)
    print("=" * 70)
    print("Graph stats")
    print("=" * 70)
    print(f"  entities total: {stats['n_entities']}")
    print(f"  edges total:    {stats['n_edges']}")
    print(f"  by kind:        {stats['by_kind']}\n")

    # Top entities by kind
    print("=" * 70)
    print("Entities by kind (top 10 each)")
    print("=" * 70)
    for kind in sorted(stats["by_kind"].keys()):
        ents = gs.top_entities(wsid, kind=kind, limit=10)
        print(f"\n  [{kind}]  ({len(ents)} shown)")
        for e in ents:
            print(f"    - {e.name:<30s}  mentions={e.n_mentions}")

    # Top edges
    print("\n" + "=" * 70)
    print("Top co-occurrence edges (weight = times mentioned together)")
    print("=" * 70)
    import sqlite3
    with sqlite3.connect(eng.workspace.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT ea.name AS a_name, ea.kind AS a_kind,
                   eb.name AS b_name, eb.kind AS b_kind,
                   ed.weight
            FROM graph_edges ed
            JOIN graph_entities ea ON ea.id = ed.entity_a
            JOIN graph_entities eb ON eb.id = ed.entity_b
            WHERE ed.workspace_id = ?
            ORDER BY ed.weight DESC
            LIMIT 20
            """,
            (wsid,),
        ).fetchall()
    for r in rows:
        a = f"{r['a_name']} ({r['a_kind']})"
        b = f"{r['b_name']} ({r['b_kind']})"
        print(f"  {a:<35s} <--{r['weight']}--> {b}")

    # Neighbors of focal entity: Postgres
    print("\n" + "=" * 70)
    print("Spreading activation demo — neighbors of 'Postgres'")
    print("=" * 70)
    found = gs.find_entities_by_name(wsid, ["Postgres", "postgres", "PostgreSQL"])
    if not found:
        print("  (no Postgres entity found — check entity extractor)")
    else:
        focal = found[0]
        print(f"\n  focal: {focal.name} (kind={focal.kind}, mentions={focal.n_mentions})")
        print(f"  -> activates these neighbors when recalled:\n")
        nb = gs.neighbors(wsid, focal.id, top_k=10)
        for ent, weight in nb:
            print(f"    {ent.name:<25s} ({ent.kind:<10s}) weight={weight}")

    # ASCII viz of strongest cluster
    print("\n" + "=" * 70)
    print("ASCII viz (top edges, weights >= 2)")
    print("=" * 70)
    with sqlite3.connect(eng.workspace.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT ea.name AS a, eb.name AS b, ed.weight
            FROM graph_edges ed
            JOIN graph_entities ea ON ea.id = ed.entity_a
            JOIN graph_entities eb ON eb.id = ed.entity_b
            WHERE ed.workspace_id = ? AND ed.weight >= 2
            ORDER BY ed.weight DESC LIMIT 30
            """,
            (wsid,),
        ).fetchall()
    if not rows:
        print("  (no edges with weight >= 2; this is a tiny demo set)")
    else:
        for r in rows:
            bar = "=" * r["weight"]
            print(f"  {r['a']:<22s} {bar:<8s} {r['b']:<22s}  (w={r['weight']})")

    # Cleanup
    import gc, shutil, time
    del eng
    gc.collect()
    for p in (tmp_home, tmp_ws):
        for _ in range(3):
            try:
                shutil.rmtree(p, ignore_errors=False)
                break
            except (OSError, PermissionError):
                time.sleep(0.3)
                gc.collect()


if __name__ == "__main__":
    main()
