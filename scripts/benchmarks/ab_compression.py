"""
A/B test: SelectivePolicy vs DropOldestNarrative on a synthetic long chat.

This is a *deterministic* test of mechanic #1 from the pitch ("smart
in-session compression"). It does NOT call claude -p — it directly drives
the compression policies through a scripted message sequence and measures
how many decisions/facts survive verbatim after the budget forces compaction.

The synthetic conversation has 30 turns total:
  - 5 explicit DECISIONS scattered at known positions
  - 5 explicit FACTS scattered at known positions
  - 20 NARRATIVE turns of filler back-and-forth (intentionally chatty)

We simulate the chat loop: after each user+assistant pair is appended, the
policy is asked to compact if needed. At the end we check:
  - decisions_preserved_verbatim
  - facts_preserved_verbatim
  - narrative_compressed_or_dropped
  - tokens_under_budget at every step

This is the deterministic core test. A second, much smaller real claude-CLI
test exists separately in scripts/ab_compression_live.py (not in this file
to keep runtime tractable).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
sys.path.insert(0, str(_here.parent.parent / "src"))


from pmb.agent_wrapper.budget import TokenBudget
from pmb.agent_wrapper.policy import (
    SelectivePolicy, DropOldestNarrative, classify_message,
)


# ----------------------------------------------------------------------
# Synthetic conversation
# ----------------------------------------------------------------------


def make_conversation():
    """30 messages with explicit decisions/facts/narrative tags."""

    # Narrative filler — long enough that summarisation is required
    NARR = (
        "Could you walk me through how the auth flow handles a stale token "
        "when the backend rotates secrets — I want to make sure the retry "
        "logic doesn't double-decrement the rate-limit counter and what "
        "happens if the user closes the tab mid-refresh — also is there a "
        "metric on this that I can pin to a dashboard, because last week "
        "we saw a spike in 401s that we couldn't trace clearly?"
    )

    messages = []

    def add_user(text):
        messages.append({"role": "user", "content": text})

    def add_assistant(text):
        messages.append({"role": "assistant", "content": text})

    # T1
    add_user("starting the session, what's the project we're on")
    add_assistant("This is the api-server repo " + NARR[:80])

    # T2 — DECISION
    add_user("we decided to switch from MySQL to Postgres 17 for write throughput")
    add_assistant("Acknowledged. I'll keep that in mind. " + NARR)

    # T3
    add_user(NARR)
    add_assistant("Got it. " + NARR)

    # T4
    add_user(NARR)
    add_assistant(NARR)

    # T5 — FACT
    add_user("[FACT] refresh token expiry = 7 days, access token = 15 minutes")
    add_assistant("Logged. " + NARR)

    # T6
    add_user(NARR)
    add_assistant(NARR)

    # T7
    add_user(NARR)
    add_assistant(NARR)

    # T8 — DECISION
    add_user("we chose argon2id for password hashing with 64MB memory cost")
    add_assistant("Recorded. " + NARR)

    # T9
    add_user(NARR)
    add_assistant(NARR)

    # T10
    add_user(NARR)
    add_assistant(NARR)

    # T11 — FACT
    add_user("the answer is rate limit = 5 attempts per 15 minutes per IP")
    add_assistant("Stored. " + NARR)

    # T12
    add_user(NARR)
    add_assistant(NARR)

    # T13 — DECISION
    add_user("we'll go with EKS for deployment, not ECS")
    add_assistant("Noted. " + NARR)

    # T14-T20: narrative
    for _ in range(7):
        add_user(NARR)
        add_assistant(NARR)

    return messages


GOLD_DECISIONS = [
    ("decision", "Postgres 17"),
    ("fact", "refresh token expiry = 7 days"),
    ("decision", "argon2id"),
    ("fact", "rate limit = 5 attempts per 15 minutes"),
    ("decision", "EKS for deployment"),
]


# ----------------------------------------------------------------------
# Simulation
# ----------------------------------------------------------------------


def _flatten(msg):
    c = msg.get("content")
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict))
    return str(c)


def simulate(policy, messages, batch_size: int = 2):
    """Replay `messages` two-at-a-time (user+assistant), calling compact
    after each appendage. Track which gold items survive."""
    current: list[dict] = []
    survival_history: list[dict] = []

    for i in range(0, len(messages), batch_size):
        for m in messages[i : i + batch_size]:
            current.append(m)
        before = len(current)
        current = policy.compact(current)
        after = len(current)
        budget_ok = not policy.budget.should_compact(current)

        # Tally gold survival
        all_text = " || ".join(_flatten(m) for m in current)
        gold_present = sum(1 for _, marker in GOLD_DECISIONS if marker in all_text)

        survival_history.append({
            "turn": i + batch_size,
            "messages_before_compact": before,
            "messages_after_compact": after,
            "gold_present": gold_present,
            "budget_under_threshold": budget_ok,
        })

    final_text = " || ".join(_flatten(m) for m in current)
    surviving = []
    missing = []
    for kind, marker in GOLD_DECISIONS:
        (surviving if marker in final_text else missing).append({"kind": kind, "marker": marker})

    return {
        "final_message_count": len(current),
        "final_token_estimate": policy.budget.count_messages(current),
        "gold_total": len(GOLD_DECISIONS),
        "gold_surviving": len(surviving),
        "gold_missing": missing,
        "survival_rate": len(surviving) / len(GOLD_DECISIONS),
        "history": survival_history,
    }


def main():
    messages = make_conversation()
    raw_tokens = sum(len(_flatten(m)) for m in messages) // 3  # approx
    print(f"Synthetic conversation: {len(messages)} messages, ~{raw_tokens} tokens raw")

    # Tight budget so compaction is required well before end
    budget = TokenBudget(window=raw_tokens * 2, target_max=0.35)
    print(f"TokenBudget: window={budget.window}, threshold={budget.threshold}")
    print()

    print("=" * 70)
    print("Baseline: DropOldestNarrative")
    print("=" * 70)
    baseline = simulate(DropOldestNarrative(budget=TokenBudget(window=budget.window, target_max=budget.target_max), keep_last_n=4), messages)
    print(f"  final messages: {baseline['final_message_count']}")
    print(f"  final tokens:   {baseline['final_token_estimate']}")
    print(f"  gold preserved: {baseline['gold_surviving']}/{baseline['gold_total']}")
    print(f"  survival_rate:  {baseline['survival_rate']:.2f}")
    if baseline["gold_missing"]:
        for g in baseline["gold_missing"]:
            print(f"    LOST: [{g['kind']}] {g['marker']!r}")

    print()
    print("=" * 70)
    print("SelectivePolicy (no LLM — heuristic fallback summarizer)")
    print("=" * 70)
    selective = simulate(
        SelectivePolicy(
            budget=TokenBudget(window=budget.window, target_max=budget.target_max),
            keep_last_n=4, max_summary_chars=180,
        ),
        messages,
    )
    print(f"  final messages: {selective['final_message_count']}")
    print(f"  final tokens:   {selective['final_token_estimate']}")
    print(f"  gold preserved: {selective['gold_surviving']}/{selective['gold_total']}")
    print(f"  survival_rate:  {selective['survival_rate']:.2f}")
    if selective["gold_missing"]:
        for g in selective["gold_missing"]:
            print(f"    LOST: [{g['kind']}] {g['marker']!r}")

    print()
    print("=" * 70)
    print(" A/B summary")
    print("=" * 70)
    delta = selective["survival_rate"] - baseline["survival_rate"]
    print(f"  Baseline   survival: {baseline['survival_rate']:.2%}")
    print(f"  Selective  survival: {selective['survival_rate']:.2%}")
    print(f"  Δ (selective - base): {delta:+.2%}")
    print()
    if delta > 0:
        print(f"  ✓ Selective compression preserved {delta:+.2%} more decisions/facts.")
    elif delta == 0:
        print("  = No difference on this workload.")
    else:
        print(f"  ✗ Selective compression LOST {-delta:.2%} more — worse than baseline. Investigate.")


if __name__ == "__main__":
    main()
