"""Comprehensive Q&A scenarios test — simulates real user/AI interactions.

Tests across 8 scenarios with different memory shapes:
  1. Personal facts (pin trigger)
  2. Project facts (3-stack: lang/db/framework)
  3. Goals + progress updates
  4. Cross-lingual (RU/EN bridging)
  5. Multi-hop (graph traversal)
  6. Working memory (last N events)
  7. Dedup (paraphrase + translation)
  8. Research summaries

For each scenario: writes, then verifies via recalls/queries.
Reports per-step timing + final correctness checks.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def hr(label):
    print(f"\n{'='*72}\n  {label}\n{'='*72}")


def t(label, fn):
    t0 = time.perf_counter()
    out = fn()
    dt = (time.perf_counter() - t0) * 1000
    return out, dt


def check(label, condition, found=None):
    mark = "PASS" if condition else "FAIL"
    extra = f"  ({found})" if found else ""
    print(f"    [{mark}] {label}{extra}")
    return condition


def main():
    tmp_home = Path(tempfile.mkdtemp(prefix="qa-bench-"))
    tmp_ws = Path(tempfile.mkdtemp(prefix="qa-bench-ws-"))
    os.environ["PMB_HOME"] = str(tmp_home)
    os.environ["PYTHONIOENCODING"] = "utf-8"

    print("Loading engine...")
    t0 = time.perf_counter()
    from pmb.core.engine import Engine
    eng = Engine(cwd=tmp_ws)
    eng.search.embed("warmup")  # ensure model loaded for fair tests
    print(f"  ready in {(time.perf_counter()-t0)*1000:.0f}ms")

    n_pass = 0
    n_fail = 0
    timings = []

    # ============================================================
    hr("Scenario 1 — Personal facts with 'remember' trigger")
    # ============================================================
    _, dt = t("write", lambda: eng.record_batch([{
        "type": "fact_tree",
        "main": "User's birthday is March 14",
        "subfacts": ["Usually celebrates at home", "Likes chocolate cake"],
        "importance": 0.95, "pin": True,
    }]))
    timings.append(("S1 pin write", dt))
    print(f"    [INFO] write took {dt:.0f}ms")

    pack, dt = t("recall ru", lambda: eng.recall("когда у меня день рождения?"))
    timings.append(("S1 recall RU", dt))
    ok = pack.results and "march" in pack.results[0].content.lower()
    n_pass += check("RU query finds birthday", ok, f"top: {pack.results[0].content[:60] if pack.results else 'none'}")
    n_fail += not ok

    pack, dt = t("recall en", lambda: eng.recall("when is my birthday?"))
    timings.append(("S1 recall EN", dt))
    ok = pack.results and "march" in pack.results[0].content.lower()
    n_pass += check("EN query also finds it", ok, f"score: {pack.results[0].score:.2f}" if pack.results else "")
    n_fail += not ok

    # ============================================================
    hr("Scenario 2 — Project facts (multi-stack)")
    # ============================================================
    _, dt = t("write stack", lambda: eng.record_batch([
        {"type": "fact", "content": "User works on pmb-dashboard"},
        {"type": "fact", "content": "Frontend: Next.js 16 with App Router"},
        {"type": "fact", "content": "Backend: FastAPI on Python 3.12"},
        {"type": "fact", "content": "Database: Postgres 17, no LanceDB"},
    ]))
    timings.append(("S2 batch write", dt))

    pack, dt = t("recall stack", lambda: eng.recall("какой у меня стек?", top_k=5))
    timings.append(("S2 stack recall", dt))
    contents = " ".join(r.content.lower() for r in pack.results)
    has_next = "next" in contents
    has_fast = "fastapi" in contents
    has_pg = "postgres" in contents
    n_pass += check("recall mentions Next.js", has_next)
    n_pass += check("recall mentions FastAPI", has_fast)
    n_pass += check("recall mentions Postgres", has_pg)
    n_fail += (not has_next) + (not has_fast) + (not has_pg)

    # ============================================================
    hr("Scenario 3 — Goals with progress")
    # ============================================================
    g_ulid, dt = t("create goal", lambda: eng.record_goal(
        "Ship pmb-dashboard v1.0 by end of June 2026",
        status="in_progress",
        due_at=1782000000,
    ))
    timings.append(("S3 goal create", dt))
    _, dt = t("update 30%", lambda: eng.update_goal(g_ulid, progress=30))
    _, dt = t("update 60%", lambda: eng.update_goal(g_ulid, progress=60))

    goals, dt = t("list", lambda: eng.list_goals(status="in_progress"))
    timings.append(("S3 list_goals", dt))
    ok = any("pmb-dashboard" in g["title"].lower() and g["progress"] >= 60 for g in goals)
    n_pass += check("goal listed with progress >=60%", ok,
                   f"{len(goals)} open" )
    n_fail += not ok

    # ============================================================
    hr("Scenario 4 — Cross-lingual (RU data, EN query)")
    # ============================================================
    _, dt = t("save RU fact", lambda: eng.record_fact(
        "У пользователя аллергия на арахис, носить EpiPen", importance=0.9,
    ))
    timings.append(("S4 RU save", dt))

    pack, dt = t("EN query", lambda: eng.recall("do I have any allergies?"))
    timings.append(("S4 EN query → RU data", dt))
    ok = pack.results and ("peanut" in pack.results[0].content.lower() or
                            "аллерги" in pack.results[0].content.lower())
    n_pass += check("EN query finds RU allergy fact", ok,
                   f"top: {pack.results[0].content[:50] if pack.results else 'none'}")
    n_fail += not ok

    # ============================================================
    hr("Scenario 5 — Multi-hop graph (people + projects)")
    # ============================================================
    _, dt = t("save people", lambda: eng.record_batch([
        {"type": "fact", "content": "Max is user's ex-colleague from Grammarly"},
        {"type": "fact", "content": "Meeting Max May 25 2026 at cafe on Podol"},
        {"type": "fact", "content": "Topic: discussing Rust startup idea"},
        {"type": "fact", "content": "User used to work at Grammarly until 2024"},
    ]))
    timings.append(("S5 batch", dt))

    pack, dt = t("multi-hop", lambda: eng.recall("Max startup discussion"))
    timings.append(("S5 multi-hop recall", dt))
    contents = " ".join(r.content.lower() for r in pack.results[:3])
    has_max = "max" in contents
    has_rust = "rust" in contents or "startup" in contents
    n_pass += check("multi-hop finds Max + Rust", has_max and has_rust)
    n_fail += not (has_max and has_rust)

    # ============================================================
    hr("Scenario 6 — Working memory (recent activity)")
    # ============================================================
    for i in range(5):
        eng.record_activity(f"User completed task #{i+1}", kind="completed")

    activities, dt = t("recent", lambda: eng.recent_activity(minutes=5))
    timings.append(("S6 recent_activity", dt))
    n_pass += check("recent_activity returns >=5 events",
                   len(activities) >= 5, f"{len(activities)} found")
    n_fail += len(activities) < 5

    recent, dt = t("what_just", lambda: eng.what_just_happened(n=5))
    timings.append(("S6 what_just_happened", dt))
    n_pass += check("what_just_happened returns 5", len(recent) == 5)
    n_fail += len(recent) != 5

    # ============================================================
    hr("Scenario 7 — Dedup (paraphrase + exact)")
    # ============================================================
    u1, _ = t("save fact", lambda: eng.record_fact("User prefers dark mode in editor"))
    u2, _ = t("save same", lambda: eng.record_fact("User prefers dark mode in editor"))
    u3, _ = t("save paraphrase", lambda: eng.record_fact("User likes dark theme in the editor"))

    n_pass += check("exact dedup (u1 == u2)", u1 == u2, f"both={u1}")
    n_fail += u1 != u2
    # Paraphrase may or may not auto-merge depending on similarity
    print(f"    [INFO] paraphrase ulid: {'same' if u1 == u3 else 'different'}")

    # ============================================================
    hr("Scenario 8 — Research summaries")
    # ============================================================
    _, dt = t("save research", lambda: eng.record_batch([
        {"type": "activity", "kind": "research",
         "content": "User asked about Next.js on May 24 2026; covered App Router, Server Components, deployment"},
        {"type": "activity", "kind": "research",
         "content": "User asked about Rust async on May 24 2026; tokio runtime, async/await mechanics"},
        {"type": "activity", "kind": "research",
         "content": "User asked about PHP on May 24 2026; ecosystem, syntax, use cases"},
    ]))
    timings.append(("S8 research save", dt))

    research_acts, dt = t("query research", lambda: eng.recent_activity(
        minutes=60, kind="research",
    ))
    timings.append(("S8 query research", dt))
    n_pass += check("returns all 3 research summaries", len(research_acts) >= 3,
                   f"{len(research_acts)} found")
    n_fail += len(research_acts) < 3

    pack, dt = t("recall PHP", lambda: eng.recall("что я спрашивал про PHP?"))
    contents = " ".join(r.content.lower() for r in pack.results[:3])
    n_pass += check("recall finds PHP research", "php" in contents)
    n_fail += "php" not in contents

    # ============================================================
    hr("SUMMARY")
    # ============================================================
    print(f"  Total checks: {n_pass + n_fail}")
    print(f"  PASS: {n_pass}")
    print(f"  FAIL: {n_fail}")
    print(f"  Success rate: {100*n_pass/(n_pass+n_fail):.1f}%")
    print()
    print("  Timing breakdown:")
    write_times = [dt for label, dt in timings if "save" in label.lower() or "write" in label.lower() or "create" in label.lower() or "batch" in label.lower()]
    read_times = [dt for label, dt in timings if "recall" in label.lower() or "query" in label.lower() or "list" in label.lower() or "what" in label.lower() or "recent" in label.lower()]
    if write_times:
        print(f"    writes — n={len(write_times)}  avg={sum(write_times)/len(write_times):.0f}ms  "
              f"min={min(write_times):.0f}ms  max={max(write_times):.0f}ms")
    if read_times:
        print(f"    reads  — n={len(read_times)}  avg={sum(read_times)/len(read_times):.0f}ms  "
              f"min={min(read_times):.0f}ms  max={max(read_times):.0f}ms")

    # Cleanup
    import gc, shutil
    del eng
    gc.collect()
    for p in (tmp_home, tmp_ws):
        for _ in range(3):
            try: shutil.rmtree(p); break
            except (OSError, PermissionError): time.sleep(0.3); gc.collect()


if __name__ == "__main__":
    main()
