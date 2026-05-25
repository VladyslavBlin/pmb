"""End-to-end bench mimicking real Codex usage AFTER all improvements W..AA.

Tests:
  1. Cold start (engine init + async prewarm)
  2. record_batch_async — should be ~2ms regardless of content size
  3. Big content (huge web-search results — content cap kicks in)
  4. Research-summary save (Next.js style — single activity, then questions)
  5. Recall sweep — including "what did I recently ask about?"
  6. Background drain wait — confirm data is queryable after wait
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def hr(label):
    print(f"\n{'='*70}\n  {label}\n{'='*70}")


def step(label, fn):
    t0 = time.perf_counter()
    out = fn()
    dt = (time.perf_counter() - t0) * 1000
    print(f"  {dt:>8.0f} ms  {label}")
    return out, dt


def wait_for_background_drain(eng, max_wait_seconds=15.0):
    """Wait until embed queue is empty AND model is loaded."""
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        queue_empty = not eng._embed_queue
        model_ready = eng.search.is_ready()
        if queue_empty and model_ready:
            return time.time() - (deadline - max_wait_seconds)
        time.sleep(0.2)
    return max_wait_seconds


def main():
    tmp_home = Path(tempfile.mkdtemp(prefix="bench-aa-home-"))
    tmp_ws = Path(tempfile.mkdtemp(prefix="bench-aa-ws-"))
    os.environ["PMB_HOME"] = str(tmp_home)

    timings = {}

    hr("Cold start")
    t0 = time.perf_counter()
    from pmb.core.engine import Engine
    eng = Engine(cwd=tmp_ws)
    timings["engine_init"] = (time.perf_counter() - t0) * 1000
    print(f"  {timings['engine_init']:>8.0f} ms  Engine init")

    # Simulate MCP server's async prewarm
    import threading
    prewarm_started = time.perf_counter()
    def _warm():
        try: eng.search.embed("warmup")
        except Exception: pass
    threading.Thread(target=_warm, daemon=True).start()

    # --------------------------------------------------------------
    hr("1. Two 'Запомни' writes via async path")
    # --------------------------------------------------------------
    def step1a():
        return eng.record_batch_async([{
            "type": "fact_tree",
            "main": "User has a cat named Barsik",
            "subfacts": ["Barsik is allergic to chicken"],
            "importance": 0.95,
        }])
    _, timings["step1a_async_write"] = step("Барсик record_batch_async", step1a)

    def step1b():
        return eng.record_batch_async([{
            "type": "fact_tree",
            "main": "User has a second cat named Keks",
            "subfacts": ["Keks is allergic to pork"],
            "importance": 0.95,
        }])
    _, timings["step1b_async_write"] = step("Кекс record_batch_async", step1b)

    # --------------------------------------------------------------
    hr("2. Big batch (user's day recap) via async path")
    # --------------------------------------------------------------
    big_items = [
        {"type": "activity", "kind": "edit",
         "content": "On May 24 2026 user fixed JWT 24h validation bug in PMB auth (3 hours)"},
        {"type": "goal", "title": "Ship PMB v1.0 by end of June 2026",
         "status": "in_progress", "due_at": 1782000000},
        {"type": "fact_tree",
         "main": "User is meeting Max on May 25 2026 at cafe on Podol",
         "subfacts": ["Max is user's former colleague from Grammarly",
                      "Meeting topic: Rust startup idea"],
         "importance": 0.7},
        {"type": "fact_tree",
         "main": "User's peanut allergy worsened on May 24 2026",
         "subfacts": ["Doctor advised user to carry an EpiPen always",
                      "Check EpiPen expiry every 6 months"],
         "importance": 0.9},
        {"type": "fact",
         "content": "On May 24 2026 user removed LanceDB, keeping SQLite only",
         "importance": 0.8},
        {"type": "milestone", "chain_name": "rust_book",
         "title": "Finished async chapter, 4 chapters left",
         "state": {"chapters_left": 4, "last_finished": "async"}},
    ]
    _, timings["step2_big_async"] = step(
        f"record_batch_async ({len(big_items)} items)",
        lambda: eng.record_batch_async(big_items),
    )

    # --------------------------------------------------------------
    hr("3. Research-summary write (Next.js style)")
    # --------------------------------------------------------------
    # Simulates what Codex should do after answering "расскажи про Next.js"
    research_summary = {
        "type": "activity",
        "kind": "research",
        "content": (
            "User asked about Next.js on May 24 2026; covered App Router, "
            "Server Components, deployment trade-offs (Vercel vs Docker), "
            "and Server Actions."
        ),
    }
    _, timings["step3_research"] = step(
        "research summary record_batch_async",
        lambda: eng.record_batch_async([research_summary]),
    )

    # --------------------------------------------------------------
    hr("4. HUGE content test (5000+ chars — should auto-truncate)")
    # --------------------------------------------------------------
    huge_content = (
        "Next.js is a React framework. " * 500   # ~14000 chars
    )
    _, timings["step4_huge"] = step(
        f"record_batch_async with {len(huge_content)}-char content",
        lambda: eng.record_batch_async([{
            "type": "fact", "content": huge_content, "importance": 0.5,
        }]),
    )

    # --------------------------------------------------------------
    hr("5. Wait for background drain")
    # --------------------------------------------------------------
    drain_t0 = time.perf_counter()
    waited = wait_for_background_drain(eng, max_wait_seconds=60.0)
    timings["step5_drain"] = (time.perf_counter() - drain_t0) * 1000
    print(f"  {timings['step5_drain']:>8.0f} ms  background drain (model load + embed)")
    print(f"  model ready: {eng.search.is_ready()}")
    print(f"  queue empty: {not eng._embed_queue}")

    # --------------------------------------------------------------
    hr("6. Recall sweep — including 'что недавно спрашивал' use case")
    # --------------------------------------------------------------
    questions = [
        ("кто такой Барсик?",                 "should hit Барсик fact_tree"),
        ("почему я выкинул LanceDB?",         "should hit LanceDB fact"),
        ("кто такой Макс и когда встреча?",   "should hit Max fact_tree"),
        ("какие у меня сейчас открытые цели?","via list_goals or recall"),
        ("какие у меня аллергии?",            "should hit allergy + Барсик + Кекс"),
    ]
    recall_times = []
    for q, hint in questions:
        def do_recall(q=q):
            return eng.recall(q, top_k=3)
        pack, dt = step(f"recall: {q[:40]}", do_recall)
        recall_times.append(dt)
        if pack.results:
            top = pack.results[0]
            print(f"           top: [{top.score:.2f}] {top.content[:60]}")
            print(f"           hint: {hint}")
    timings["step6_recall_total"] = sum(recall_times)

    # --------------------------------------------------------------
    hr("7. 'что недавно спрашивал?' via recent_activity(kind=research)")
    # --------------------------------------------------------------
    def step7():
        return eng.recent_activity(minutes=60, kind="research")
    research_activities, timings["step7_research_query"] = step(
        "recent_activity(kind=research)", step7,
    )
    print(f"  found {len(research_activities)} research activities:")
    for a in research_activities:
        print(f"    - {a['content'][:100]}")

    # --------------------------------------------------------------
    hr("SUMMARY")
    # --------------------------------------------------------------
    print(f"  Engine init:                      {timings['engine_init']:>6.0f} ms")
    print(f"  Барсик (async write):             {timings['step1a_async_write']:>6.0f} ms")
    print(f"  Кекс (async write):               {timings['step1b_async_write']:>6.0f} ms")
    print(f"  Big batch (6 items, async):       {timings['step2_big_async']:>6.0f} ms")
    print(f"  Research summary (async):         {timings['step3_research']:>6.0f} ms")
    print(f"  Huge content (~14KB -> capped):    {timings['step4_huge']:>6.0f} ms")
    print(f"  Background drain wait:            {timings['step5_drain']:>6.0f} ms")
    print(f"  Recall sweep (5 questions):       {timings['step6_recall_total']:>6.0f} ms")
    print(f"  Research activity query:          {timings['step7_research_query']:>6.0f} ms")
    print()
    print(f"  USER-FELT TOTAL (writes only):    "
          f"{timings['step1a_async_write'] + timings['step1b_async_write'] + timings['step2_big_async'] + timings['step3_research'] + timings['step4_huge']:>6.0f} ms")

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
