"""End-to-end timing of the user's actual chat flow.

Mimics what the user did in Codex:
  1. Cold start
  2. "Запомни — у меня кошку зовут Барсик, она аллергична на курицу"
     -> record_fact_tree + pin
  3. "Запомни Кекс / аллергична на свинину" -> same
  4. Big batch — fixed JWT, v1.0, Max meeting, peanut allergy, LanceDB drop, Rust book
  5. Recall sweep: 6 questions

Prints per-step latency. Cleans up.
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


def main():
    tmp_home = Path(tempfile.mkdtemp(prefix="bench-home-"))
    tmp_ws = Path(tempfile.mkdtemp(prefix="bench-ws-"))
    os.environ["PMB_HOME"] = str(tmp_home)

    timings = {}

    hr("Cold start (Engine init + async prewarm spawn)")
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
    hr("1. 'Запомни Барсик' — first write, model loading in BG")
    # --------------------------------------------------------------
    def step1():
        r = eng.record_fact_tree(
            main="У пользователя есть кошка по имени Барсик",
            subfacts=["Барсик аллергична на курицу"],
            importance=0.95,
        )
        eng.pin(r["main_ulid"])
        return r
    r1, timings["step1_cold_write"] = step("record_fact_tree + pin (cold)", step1)

    # --------------------------------------------------------------
    hr("2. 'Запомни Кекс' — second write, model still loading?")
    # --------------------------------------------------------------
    print(f"  model_ready={eng.search.is_ready()}")
    def step2():
        r = eng.record_fact_tree(
            main="У пользователя есть вторая кошка по имени Кекс",
            subfacts=["Кекс аллергична на свинину"],
            importance=0.95,
        )
        eng.pin(r["main_ulid"])
        return r
    r2, timings["step2_write"] = step("record_fact_tree + pin", step2)

    # Wait for model to finish loading
    print(f"\n  Waiting for model to finish loading...")
    while not eng.search.is_ready():
        time.sleep(0.5)
    print(f"  model loaded after {(time.perf_counter()-prewarm_started)*1000:.0f}ms total")

    # --------------------------------------------------------------
    hr("3. Big batch (user's day recap — 7 items + subfacts)")
    # --------------------------------------------------------------
    big_items = [
        {"type": "activity", "kind": "edit",
         "content": "On May 24 2026 user fixed JWT 24h validation bug in PMB auth (3 hours)"},
        {"type": "goal",
         "title": "Ship PMB v1.0 by end of June 2026",
         "status": "in_progress", "due_at": 1782000000},
        {"type": "fact_tree",
         "main": "User is meeting Max on May 25 2026 at a cafe on Podol",
         "subfacts": ["Max is user's former colleague from Grammarly",
                      "Meeting topic: Rust startup idea"],
         "importance": 0.7},
        {"type": "fact_tree",
         "main": "User's peanut allergy worsened on May 24 2026",
         "subfacts": ["Doctor advised user to carry an EpiPen always",
                      "Check EpiPen expiry every 6 months"],
         "importance": 0.9},
        {"type": "fact",
         "content": "On May 24 2026 user removed LanceDB from PMB, "
                    "keeping SQLite only (LanceDB pulled ~200MB deps)",
         "importance": 0.8},
        {"type": "milestone", "chain_name": "rust_book_progress",
         "title": "Finished async chapter, 4 chapters left",
         "state": {"chapters_left": 4, "last_finished": "async"}},
    ]
    r3, timings["step3_batch"] = step(
        f"record_batch ({len(big_items)} items, warm)",
        lambda: eng.record_batch(big_items),
    )
    print(f"  -> n_ok={r3['n_ok']} n_failed={r3['n_failed']}")

    # --------------------------------------------------------------
    hr("4. Recall sweep — 6 typical questions")
    # --------------------------------------------------------------
    questions = [
        "кто такой Барсик?",
        "почему я выкинул LanceDB?",
        "кто такой Макс и когда встреча?",
        "какие у меня сейчас открытые цели?",
        "сколько глав осталось в Rust book?",
        "какие у меня аллергии?",
    ]
    recall_times = []
    for q in questions:
        def do_recall(q=q):
            return eng.recall(q, top_k=3)
        pack, dt = step(f"recall: {q[:40]}", do_recall)
        recall_times.append(dt)
        if pack.results:
            top = pack.results[0]
            print(f"           top: [{top.score:.2f}] {top.content[:60]}")
    timings["step4_recall_total"] = sum(recall_times)

    # --------------------------------------------------------------
    hr("SUMMARY — what user would experience")
    # --------------------------------------------------------------
    print(f"  Engine init (one-time per Codex restart):        {timings['engine_init']:>6.0f} ms")
    print(f"  First write (cold, model loading in BG):         {timings['step1_cold_write']:>6.0f} ms")
    print(f"  Second write:                                    {timings['step2_write']:>6.0f} ms")
    print(f"  Big batch (6 items):                             {timings['step3_batch']:>6.0f} ms")
    print(f"  Recall x 6 (total):                              {timings['step4_recall_total']:>6.0f} ms")
    print(f"  Recall x 6 (avg per question):                   {timings['step4_recall_total']/6:>6.0f} ms")
    print()
    print(f"  -> User-perceived total for this whole session:   "
          f"{sum(timings.values()):>6.0f} ms")

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
