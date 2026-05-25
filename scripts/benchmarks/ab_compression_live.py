"""
Live A/B: SelectivePolicy vs DropOldestNarrative, real claude CLI.

Drives a scripted ~10-turn conversation through `pmb-chat` AgentLoop with
each policy in turn, then issues probe questions at the end and scores
whether the model correctly recalled decisions injected mid-conversation.

This is the real-world version of scripts/ab_compression.py. It costs
~10-15 minutes of `claude -p` calls per policy — heavy enough that we
keep the turn count modest (10) and probe count tight (3).

Output: per-probe verdict + survival_rate per policy.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

_here = Path(__file__).resolve()
sys.path.insert(0, str(_here.parent.parent / "src"))


# Each turn is `(role, content)`. Decisions/facts are interspersed so the
# narrative load between them forces compaction before the probes.
NARR = (
    "Could you walk through how the request flow handles a stale auth token "
    "when the backend rotates secrets — I want to make sure the retry logic "
    "doesn't double-decrement the rate-limit counter and what happens if "
    "the user closes the tab mid-refresh. Also, is there a metric I can pin "
    "to a dashboard, because last week we saw a spike in 401s we couldn't "
    "trace clearly. Walk me through the steps in order."
)

SCRIPT = [
    # (role, content) — only user turns are driven; assistant is filled in by claude
    ("user", "We're starting a new debugging session on the api-server repo."),
    # decision #1 at turn 2
    ("user", "Decision: we are switching from MySQL to Postgres 17 because of write throughput. Acknowledge and continue."),
    ("user", NARR),
    ("user", NARR),
    # fact #2 at turn 5
    ("user", "[FACT] refresh token expiry = 7 days, access token = 15 minutes."),
    ("user", NARR),
    ("user", NARR),
    # decision #3 at turn 8
    ("user", "We have chosen argon2id for password hashing with 64MB memory cost. Got it?"),
    ("user", NARR),
    ("user", NARR),
]

PROBES = [
    ("which database engine did we decide on for the api-server",
     ["postgres"]),
    ("how long does the refresh token live",
     ["7 day", "seven day"]),
    ("which password hashing algorithm did we agree on",
     ["argon2"]),
]


def make_engine(tmp_home: Path, tmp_ws: Path):
    from pmb.core.engine import Engine
    os.environ["PMB_HOME"] = str(tmp_home)
    return Engine(cwd=tmp_ws, pmb_home=tmp_home)


def run_one(policy_name: str, transport: str = "claude") -> dict:
    from pmb.agent_wrapper.budget import TokenBudget
    from pmb.agent_wrapper.policy import SelectivePolicy, DropOldestNarrative
    from pmb.agent_wrapper.loop import AgentLoop, AgentConfig

    tmp_home = Path(tempfile.mkdtemp(prefix=f"pmb-ab-{policy_name}-"))
    tmp_ws = Path(tempfile.mkdtemp(prefix=f"pmb-ab-ws-{policy_name}-"))

    eng = make_engine(tmp_home, tmp_ws)

    # Tight budget so we genuinely force compaction within the 10 turns
    cfg = AgentConfig(
        model="haiku", transport=transport,
        window=6000, target_max=0.45,   # ~2700-token threshold
        recall_top_k=3,
        persist_turns=False,
        selective_compression=(policy_name == "selective"),
        consolidate_on_exit=False,
    )
    # Build the right policy directly so the comparison is apples-to-apples
    budget = TokenBudget(window=cfg.window, target_max=cfg.target_max)
    if policy_name == "selective":
        policy = SelectivePolicy(budget, engine=eng, llm=None, keep_last_n=4,
                                  max_summary_chars=160)
    else:
        policy = DropOldestNarrative(budget, keep_last_n=4)
    loop = AgentLoop(engine=eng, config=cfg, policy=policy)

    # Drive scripted turns
    turn_log = []
    t0 = time.time()
    for i, (_role, content) in enumerate(SCRIPT, 1):
        t_turn = time.time()
        try:
            reply = loop.turn(content)
        except Exception as e:
            reply = f"[ERROR] {e}"
        turn_log.append({
            "turn": i,
            "elapsed_s": round(time.time() - t_turn, 1),
            "messages_after": len(loop.messages),
            "reply_head": reply[:120],
        })
        print(f"  turn {i:2d} done in {turn_log[-1]['elapsed_s']}s; messages={turn_log[-1]['messages_after']}")

    # Probes (these also go through compact, but the model sees compressed history)
    probe_results = []
    for q, expected in PROBES:
        t_probe = time.time()
        try:
            answer = loop.turn(q)
        except Exception as e:
            answer = f"[ERROR] {e}"
        low = answer.lower()
        hit = any(sub.lower() in low for sub in expected)
        probe_results.append({
            "probe": q,
            "expected_any_of": expected,
            "answer_head": answer[:240],
            "hit": hit,
            "elapsed_s": round(time.time() - t_probe, 1),
        })
        verdict = "HIT " if hit else "MISS"
        print(f"  probe [{verdict}] {q!r}")

    total_elapsed = round(time.time() - t0, 1)
    survival = sum(1 for p in probe_results if p["hit"]) / len(probe_results)

    # Cleanup
    import gc, shutil
    del loop
    gc.collect()
    for d in (tmp_home, tmp_ws):
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass

    return {
        "policy": policy_name,
        "n_probes": len(PROBES),
        "n_hits": sum(1 for p in probe_results if p["hit"]),
        "survival_rate": survival,
        "total_elapsed_seconds": total_elapsed,
        "probes": probe_results,
        "turns": turn_log,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--transport", default="claude",
                   choices=("claude", "anthropic", "ollama"))
    args = p.parse_args()

    print(f"Transport: {args.transport}")
    print(f"Script:    {len(SCRIPT)} turns + {len(PROBES)} probes")
    print()
    print("=" * 70)
    print("Run 1/2: DropOldestNarrative (baseline)")
    print("=" * 70)
    baseline = run_one("drop_oldest", transport=args.transport)

    print()
    print("=" * 70)
    print("Run 2/2: SelectivePolicy")
    print("=" * 70)
    selective = run_one("selective", transport=args.transport)

    print()
    print("=" * 70)
    print(" A/B summary")
    print("=" * 70)
    delta = selective["survival_rate"] - baseline["survival_rate"]
    print(f"  Baseline  survival: {baseline['n_hits']}/{baseline['n_probes']}  "
          f"({baseline['survival_rate']:.0%})  "
          f"elapsed {baseline['total_elapsed_seconds']}s")
    print(f"  Selective survival: {selective['n_hits']}/{selective['n_probes']}  "
          f"({selective['survival_rate']:.0%})  "
          f"elapsed {selective['total_elapsed_seconds']}s")
    print(f"  Δ: {delta:+.0%}")

    out = Path(os.environ.get("PMB_AB_OUT", "pmb_ab_live.json"))
    out.write_text(json.dumps({"baseline": baseline, "selective": selective},
                              indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nDetails: {out}")


if __name__ == "__main__":
    main()
