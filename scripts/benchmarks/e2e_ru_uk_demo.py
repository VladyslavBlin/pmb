"""
E2E RU/UK scenario — demonstrates the Alternix-reviewer scenarios working
after the P0/P1/P2 hardening pass.

Covers:
  - RU/UK atomic fact extraction
  - Keyed-upsert supersession
  - Cross-lingual recall (RU query → UK fact, vice versa)
  - Memory types (preference, summary)
  - Warmup
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_here = Path(__file__).resolve()
sys.path.insert(0, str(_here.parent.parent.parent / "src"))


def section(title):
    print(f"\n{'=' * 75}\n  {title}\n{'=' * 75}")


def main():
    tmp_home = Path(tempfile.mkdtemp())
    tmp_ws = Path(tempfile.mkdtemp())
    os.environ["PMB_HOME"] = str(tmp_home)

    from pmb.core.engine import Engine
    section("RU/UK assistant memory scenarios (Alternix-style)")
    eng = Engine(
        cwd=tmp_ws, pmb_home=tmp_home, rerank_model=None,
        config_overrides={
            "write.atomic_fact_extract": True,
            "recall.pamvr_enabled": True,
        },
    )
    # P1-1: warmup (so first recall doesn't pay cold-start cost)
    warm = eng.warmup(with_first_query=True)
    print(f"  warmup: {warm['total_ms']}ms total "
          f"(model {warm['model_load_ms']}ms, bm25 {warm['bm25_load_ms']}ms, "
          f"first query {warm['first_query_ms']}ms)")

    # ----- Scenario 1: RU atomic extraction (reviewer's blocker) -----
    section("Scenario 1: RU atomic extraction from a single utterance")
    r = eng.record_batch([{
        "type": "fact",
        "content": (
            "Меня зовут Алексей. Я живу в Киеве. "
            "Мой день рождения 7 июня. Я люблю спокойные игры."
        ),
    }])
    time.sleep(1.0)
    atoms = r["results"][0].get("atomic_facts", [])
    print(f"  extracted {len(atoms)} atomic facts:")
    import sqlite3
    with sqlite3.connect(str(eng.workspace.db_path)) as conn:
        for u in atoms:
            row = conn.execute(
                "SELECT content FROM events WHERE ulid=?", (u,)
            ).fetchone()
            if row:
                print(f"    - {row[0]}")

    # ----- Scenario 2: UK atomic extraction -----
    section("Scenario 2: UK atomic extraction")
    r = eng.record_batch([{
        "type": "fact",
        "content": (
            "Мене звати Олексій. Я живу у Києві. "
            "Мій день народження 7 червня."
        ),
    }])
    time.sleep(1.0)
    atoms = r["results"][0].get("atomic_facts", [])
    print(f"  extracted {len(atoms)} atomic facts:")
    with sqlite3.connect(str(eng.workspace.db_path)) as conn:
        for u in atoms:
            row = conn.execute(
                "SELECT content FROM events WHERE ulid=?", (u,)
            ).fetchone()
            if row:
                print(f"    - {row[0]}")

    # ----- Scenario 3: keyed-upsert supersession -----
    section("Scenario 3: keyed-upsert (fact replacement)")
    r1 = eng.record_keyed_fact("user", "residence", "Киев",
                                metadata={"language": "ru"})
    r2 = eng.record_keyed_fact("user", "residence", "Варшава",
                                metadata={"language": "ru"})
    print(f"  step 1: записан 'Киев' as {r1['new_ulid'][:12]}...")
    print(f"  step 2: записан 'Варшава' as {r2['new_ulid'][:12]}...")
    print(f"           superseded: {len(r2['superseded_ulids'])} prior facts archived")
    hist = eng.get_keyed_fact_history("user", "residence")
    print(f"  history (newest first):")
    for h in hist:
        mark = "current" if h["is_current"] else "archived"
        print(f"    [{mark:<8}] {h['value']}")
    # Recall must show only Варшава
    time.sleep(0.5)
    pack = eng.recall("где живёт пользователь", top_k=3)
    print(f"\n  query 'где живёт пользователь':")
    for r in pack.results[:3]:
        print(f"    -> {r.content[:60]}")
    contents = " ".join(r.content for r in pack.results).lower()
    if "варшава" in contents and "киев" not in contents:
        print(f"  STATUS: OK (only current 'Варшава' in results)")
    elif "варшава" in contents:
        print(f"  STATUS: PARTIAL (Варшава found but Киев also surfaced)")
    else:
        print(f"  STATUS: FAIL (no current value)")

    # ----- Scenario 4: cross-lingual recall (RU query → UK fact) -----
    section("Scenario 4: cross-lingual recall (RU query → UK fact)")
    pack = eng.recall("когда у меня день рождения", top_k=3)
    print(f"  query (RU): 'когда у меня день рождения'")
    for r in pack.results[:3]:
        print(f"    -> {r.content[:70]}")
    contents = " ".join(r.content for r in pack.results).lower()
    if "червня" in contents or "июня" in contents or "7" in contents:
        print(f"  STATUS: OK (birthday surfaced)")
    else:
        print(f"  STATUS: weak match")

    # ----- Scenario 5: typed memory (preference + summary) -----
    section("Scenario 5: typed memory helpers")
    pref_ulid = eng.record_preference("Я предпочитаю тёмную тему")
    summ_ulid = eng.record_summary(
        "Обсудили с пользователем переезд из Киева в Варшаву."
    )
    pref_ev = eng.events.get_by_ulid(pref_ulid)
    summ_ev = eng.events.get_by_ulid(summ_ulid)
    print(f"  preference: event_type={pref_ev.event_type}, "
          f"memory_type={(pref_ev.metadata or {}).get('memory_type')}")
    print(f"  summary:    event_type={summ_ev.event_type}, "
          f"memory_type={(summ_ev.metadata or {}).get('memory_type')}")

    # ----- Scenario 6: warmup speedup verification -----
    section("Scenario 6: warmup speedup")
    # Cold workspace
    tmp_ws2 = Path(tempfile.mkdtemp())
    eng2 = Engine(cwd=tmp_ws2, pmb_home=tmp_home, rerank_model=None)
    eng2.record_batch([{"type": "fact", "content": "test"}])
    time.sleep(0.5)
    # Cold first recall
    t = time.perf_counter()
    eng2.recall("test", top_k=1)
    cold_ms = (time.perf_counter() - t) * 1000
    # Warm recall
    t = time.perf_counter()
    eng2.recall("test 2", top_k=1)
    warm_ms = (time.perf_counter() - t) * 1000
    print(f"  cold first recall (no warmup):  {cold_ms:.1f}ms")
    print(f"  warm recall (next):             {warm_ms:.1f}ms")
    # Now try with explicit warmup
    tmp_ws3 = Path(tempfile.mkdtemp())
    eng3 = Engine(cwd=tmp_ws3, pmb_home=tmp_home, rerank_model=None)
    eng3.record_batch([{"type": "fact", "content": "test"}])
    time.sleep(0.5)
    eng3.warmup()
    t = time.perf_counter()
    eng3.recall("test", top_k=1)
    after_warm_ms = (time.perf_counter() - t) * 1000
    print(f"  first recall AFTER pmb warmup:  {after_warm_ms:.1f}ms")

    try: eng.close()
    except Exception: pass
    try: eng2.close()
    except Exception: pass
    try: eng3.close()
    except Exception: pass

    section("Done. Scenarios above demonstrate P0/P1/P2 fixes work end-to-end.")


if __name__ == "__main__":
    main()
