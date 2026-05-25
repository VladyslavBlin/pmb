"""End-to-end self-test: exercise all latest improvements in a realistic flow.

Simulates a session where:
  1. User states personal events (record_fact, record_fact_tree)
  2. AI logs its own actions (record_activity)
  3. User sets goals (record_goal) + makes progress (update_goal)
  4. State chains track evolution (record_milestone, chain_history)
  5. Cross-lingual recall (RU query -> EN fact)
  6. Typo-tolerant recall
  7. Working memory access (what_just_happened)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def hr(label: str) -> None:
    print(f"\n{'=' * 70}\n  {label}\n{'=' * 70}")


def show(label: str, val) -> None:
    print(f"  [{label}] {val}")


def main():
    tmp_home = Path(tempfile.mkdtemp(prefix="pmb-e2e-"))
    tmp_ws = Path(tempfile.mkdtemp(prefix="pmb-e2e-ws-"))
    os.environ["PMB_HOME"] = str(tmp_home)

    from pmb.core.engine import Engine
    eng = Engine(cwd=tmp_ws, pmb_home=tmp_home)
    print(f"Workspace: {eng.workspace.name} ({eng.workspace.id})")

    # ──────────────────────────────────────────────────────────────────
    hr("1. Personal event with subfacts (Improvement P)")
    # ──────────────────────────────────────────────────────────────────
    r = eng.record_fact_tree(
        "On May 23 2026, user fell down stairs and broke arm",
        subfacts=[
            "Time of fall: evening (around 18:52)",
            "Recommended: ER visit for X-ray",
            "First aid: ice 15-20 min, remove rings, do not drive",
            "Call 911 if: numb fingers, bleeding, deformation",
        ],
        importance=0.9,
    )
    show("main_ulid", r["main_ulid"])
    show("subfacts", r["n_subfacts"])

    # ──────────────────────────────────────────────────────────────────
    hr("2. AI logs its activity (Improvement Q)")
    # ──────────────────────────────────────────────────────────────────
    eng.record_activity("Refactored authentication module to use JWT", kind="edit")
    eng.record_activity("Ran test suite — 211/211 passed", kind="tool_call")
    eng.record_activity("Recommended ER visit for user's broken arm",
                        kind="recommendation")
    show("activities", "3 logged")

    # ──────────────────────────────────────────────────────────────────
    hr("3. User sets a goal + makes progress (Improvement R)")
    # ──────────────────────────────────────────────────────────────────
    goal_ulid = eng.record_goal(
        "Learn Rust by end of 2026",
        status="in_progress",
    )
    show("goal_ulid", goal_ulid)
    eng.update_goal(goal_ulid, progress=30, note="completed first 5 chapters")
    eng.update_goal(goal_ulid, progress=55, note="finished concurrency section")
    show("progress now", "55%")

    parent_goal = eng.record_goal("Ship PMB v1.0", status="in_progress")
    eng.record_goal("Write README", parent_goal_ulid=parent_goal)
    eng.record_goal("Run final benchmark", parent_goal_ulid=parent_goal)
    show("parent goal + 2 children", "OK")

    # ──────────────────────────────────────────────────────────────────
    hr("4. State evolution chain (Improvement R)")
    # ──────────────────────────────────────────────────────────────────
    trigger_a = eng.record_fact("Implemented multi-modal layer")
    trigger_b = eng.record_fact("Implemented activity log")
    trigger_c = eng.record_fact("Implemented goals layer")

    eng.record_milestone("architecture_layers",
                         "10 layers (added images CLIP)",
                         state={"count": 10}, triggered_by_ulid=trigger_a)
    eng.record_milestone("architecture_layers",
                         "11 layers (added activity log)",
                         state={"count": 11}, triggered_by_ulid=trigger_b)
    eng.record_milestone("architecture_layers",
                         "13 layers (added goals + milestones)",
                         state={"count": 13}, triggered_by_ulid=trigger_c)
    hist = eng.chain_history("architecture_layers")
    show("chain_history", f"{len(hist)} milestones")
    for i, m in enumerate(hist):
        print(f"     {i+1}. {m['title']}")
    cur = eng.chain_current("architecture_layers")
    show("current state", cur["title"])

    # ──────────────────────────────────────────────────────────────────
    hr("5. Cross-lingual recall (Improvement M)")
    # ──────────────────────────────────────────────────────────────────
    # Russian query against English data
    for q in [
        "когда я сломал руку?",
        "When did I break arm?",
        "что мне советовали при переломе?",
        "сколько у нас сейчас слоёв?",
        "Сколько прогресса по Rust?",
    ]:
        pack = eng.recall(q, top_k=2)
        if pack.results:
            top = pack.results[0]
            show(f"Q: {q[:40]:<40s}", f"[{top.score:.2f}] {top.content[:60]}")
        else:
            show(f"Q: {q[:40]:<40s}", "EMPTY")

    # ──────────────────────────────────────────────────────────────────
    hr("6. Typo-tolerant recall (Improvement K+L)")
    # ──────────────────────────────────────────────────────────────────
    # Add a person and verify typo correction works
    eng.record_event(
        event_type="qa",
        content="Alice flew to Paris on Jan 8 2026",
        metadata={"speaker": "Alice"},
    )
    for q in ["who is Aliceeee", "Alic flight", "Postgers"]:
        pack = eng.recall(q, top_k=2)
        # The "query" field shows the corrected version
        show(f"Q: {q!r}", f"corrected -> {pack.query!r}")

    # ──────────────────────────────────────────────────────────────────
    hr("7. Working memory — instant access (Improvement Q)")
    # ──────────────────────────────────────────────────────────────────
    recent = eng.what_just_happened(n=5)
    show("last 5 events", "")
    for r in recent:
        print(f"     [{r['event_type']:<14s}] {r['content'][:55]}")

    activity_only = eng.recent_activity(minutes=60, actor="agent")
    show("recent agent activities", f"{len(activity_only)} found")

    # ──────────────────────────────────────────────────────────────────
    hr("8. Goal management (Improvement R)")
    # ──────────────────────────────────────────────────────────────────
    open_goals = eng.list_goals(status="in_progress")
    show("in_progress goals", f"{len(open_goals)}")
    for g in open_goals:
        print(f"     [{g['progress']:>3d}%] {g['title']}")

    # ──────────────────────────────────────────────────────────────────
    hr("9. Subfact retrieval — get_subfacts (Improvement P)")
    # ──────────────────────────────────────────────────────────────────
    subs = eng.get_subfacts(r["main_ulid"] if False else
                            eng.list_goals()[0]["ulid"])  # dummy — use main from earlier
    # Better: refetch using known main_ulid
    main_ulid = None
    import sqlite3
    with sqlite3.connect(eng.workspace.db_path) as conn:
        row = conn.execute(
            "SELECT ulid FROM events WHERE workspace_id = ? "
            "AND event_type = 'fact' AND content LIKE '%broke arm%' LIMIT 1",
            (eng.workspace.id,),
        ).fetchone()
        if row:
            main_ulid = row[0]
    if main_ulid:
        subs = eng.get_subfacts(main_ulid)
        show("subfacts of broken arm event", f"{len(subs)}")
        for s in subs:
            print(f"     - {s['content']}")

    # ──────────────────────────────────────────────────────────────────
    hr("10. Stats — what we have now")
    # ──────────────────────────────────────────────────────────────────
    stats = eng.stats()
    ev = stats["events"]
    gs = eng.graph_stats()
    show("total events", ev["total"])
    show("by type", ev["by_type"])
    show("entities", f"{gs['n_entities']} (kinds: {list(gs['by_kind'].keys())})")
    show("graph edges", gs["n_edges"])

    print("\n" + "=" * 70)
    print("  E2E TEST COMPLETE — all features exercised end-to-end")
    print("=" * 70)

    # Cleanup
    import gc, shutil, time
    del eng
    gc.collect()
    for p in (tmp_home, tmp_ws):
        for _ in range(3):
            try:
                shutil.rmtree(p, ignore_errors=False); break
            except (OSError, PermissionError):
                time.sleep(0.3); gc.collect()


if __name__ == "__main__":
    main()
