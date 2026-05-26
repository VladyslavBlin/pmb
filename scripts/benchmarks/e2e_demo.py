"""
End-to-end demo: realistic use cases shown as transcripts.

For each scenario we show:
  📝 RECORDED:  what the agent stores during the conversation
  🤔 ASKED:     the query that comes later (sometimes days/weeks later in the story)
  🧠 RECALLED:  top results PMB returns, ranked

This is not a pass/fail benchmark - it's a demonstration that PMB does what
it's supposed to in plain-English scenarios. Read the transcript and judge
for yourself whether the answers are useful.

Run:
    python scripts/benchmarks/e2e_demo.py
    python scripts/benchmarks/e2e_demo.py --scenario 3   # one scenario only
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

_here = Path(__file__).resolve()
sys.path.insert(0, str(_here.parent.parent.parent / "src"))


@dataclass
class Step:
    kind: str  # "record" or "ask"
    note: str  # human description (one line)
    payload: dict = field(default_factory=dict)  # for record: items[]; for ask: query


@dataclass
class Scenario:
    title: str
    story: str
    steps: list[Step]


SCENARIOS: list[Scenario] = [
    # --------------------------------------------------------------
    Scenario(
        title="A. New developer onboarding (Monday → Tuesday)",
        story=(
            "Monday morning: an AI coding agent meets a new user and learns identity, "
            "stack, project, preferences. Tuesday: a fresh agent session asks about it."
        ),
        steps=[
            Step("record", "Mon 09:00 — pinned identity",
                 {"items": [
                     {"type": "fact",
                      "content": "User's name is Alex. Senior backend developer.",
                      "pin": True, "importance": 0.95},
                 ]}),
            Step("record", "Mon 09:15 — project & stack",
                 {"items": [
                     {"type": "fact",
                      "content": "Alex works on the order-service repo at acme/orders."},
                     {"type": "fact",
                      "content": "Alex uses Python 3.12 and Rust. Strong opinions about typing."},
                 ]}),
            Step("record", "Mon 10:30 — preferences",
                 {"items": [
                     {"type": "fact",
                      "content": "Alex prefers small functions over large classes."},
                     {"type": "fact",
                      "content": "Alex finds Java verbose and avoids it when possible."},
                 ]}),
            Step("record", "Mon 11:00 — quarter goal",
                 {"items": [
                     {"type": "goal",
                      "title": "Ship payment processor v1 by end of Q2",
                      "status": "in_progress",
                      "importance": 0.8},
                 ]}),
            Step("ask", "Tue 10:00 — fresh agent session, asks identity",
                 {"q": "Who am I working with?"}),
            Step("ask", "Tue 10:01 — what's their stack",
                 {"q": "What languages and stack does the user prefer?"}),
            Step("ask", "Tue 10:02 — anything to avoid suggesting",
                 {"q": "Any technologies the user dislikes?"}),
            Step("ask", "Tue 10:03 — current goal",
                 {"q": "What is the user's current quarterly goal?"}),
        ],
    ),

    # --------------------------------------------------------------
    Scenario(
        title="B. Bug investigation, recurring failure mode",
        story=(
            "Today: a JSONB null bug is found and fixed in migration 0042. "
            "Three weeks later: a similar bug appears. Agent asks if this has happened before."
        ),
        steps=[
            Step("record", "Day 1 — bug + root cause + fix as causal chain",
                 {"items": [
                     {"type": "activity",
                      "kind": "bug",
                      "content": "Bug found: order-service crashes when JSONB metadata column is NULL on the orders table."},
                     {"type": "fact",
                      "content": "Root cause: handler reads metadata without NULL coalescing; PostgreSQL returns NULL not '{}'."},
                     {"type": "activity",
                      "kind": "fix",
                      "content": "Fix: added COALESCE(metadata, '{}'::jsonb) to migration 0042 for the orders table."},
                     {"type": "activity",
                      "kind": "decision",
                      "content": "Decision: enforce NOT NULL on jsonb columns in next migration to prevent recurrence."},
                 ]}),
            Step("ask", "Day 22 — new but similar bug surfaces",
                 {"q": "Have we hit a NULL JSONB bug in this service before?"}),
            Step("ask", "Day 22 — what was the fix last time",
                 {"q": "What was the fix for the JSONB NULL issue?"}),
            Step("ask", "Day 22 — what's our policy now",
                 {"q": "What's our policy on JSONB columns to prevent this?"}),
        ],
    ),

    # --------------------------------------------------------------
    Scenario(
        title="C. Sprint continuity (Mon → Tue → Fri standup)",
        story=(
            "Sprint runs across multiple days. Agent tracks milestones as they happen. "
            "Friday standup asks for the week's summary."
        ),
        steps=[
            Step("record", "Mon — sprint goal kicked off",
                 {"items": [
                     {"type": "goal",
                      "title": "Redesign auth flow this sprint",
                      "status": "in_progress"},
                     {"type": "milestone",
                      "chain_name": "auth-redesign",
                      "title": "Spec written and reviewed"},
                 ]}),
            Step("record", "Tue — implementation progress",
                 {"items": [
                     {"type": "milestone",
                      "chain_name": "auth-redesign",
                      "title": "API endpoints scaffolded"},
                     {"type": "activity",
                      "kind": "pairing",
                      "content": "Pair-programmed with Bob on JWT verification edge cases."},
                 ]}),
            Step("record", "Wed — blockage and resolution",
                 {"items": [
                     {"type": "milestone",
                      "chain_name": "auth-redesign",
                      "title": "Refresh-token rotation implemented"},
                     {"type": "activity",
                      "kind": "decision",
                      "content": "Decided to use httpOnly cookies, not localStorage, for the refresh token."},
                 ]}),
            Step("record", "Thu — testing day",
                 {"items": [
                     {"type": "milestone",
                      "chain_name": "auth-redesign",
                      "title": "Integration tests green on staging"},
                 ]}),
            Step("ask", "Fri standup — what's the status",
                 {"q": "What's the auth redesign status?"}),
            Step("ask", "Fri standup — what did I work on with Bob",
                 {"q": "What did I work on with Bob this week?"}),
            Step("ask", "Fri standup — why httpOnly cookies",
                 {"q": "Why did we go with httpOnly cookies?"}),
        ],
    ),

    # --------------------------------------------------------------
    Scenario(
        title="D. Multilingual user — RU notes, EN code, mixed memory",
        story=(
            "The user records in their native language (Russian) sometimes; the AI agent "
            "writes in English. Queries arrive in either language and must match anything."
        ),
        steps=[
            Step("record", "User personal preferences in Russian",
                 {"items": [
                     {"type": "fact",
                      "content": "Предпочитаю чёрный чай без сахара. Кофе только утром после душа."},
                     {"type": "fact",
                      "content": "Кошку зовут Мурка, ей 4 года, аллергия на курицу."},
                 ]}),
            Step("record", "Agent observations in English",
                 {"items": [
                     {"type": "fact",
                      "content": "User's favorite editor is Helix; switched from Neovim three months ago."},
                     {"type": "fact",
                      "content": "User lives in Kyiv and frequently mentions weather affecting their mood."},
                 ]}),
            Step("ask", "Russian query about pet",
                 {"q": "Как зовут кошку и сколько ей лет?"}),
            Step("ask", "Russian query about beverage habits",
                 {"q": "Что я обычно пью утром?"}),
            Step("ask", "English query about an English-stored fact",
                 {"q": "What editor does the user use?"}),
            Step("ask", "English query about a Russian-stored fact",
                 {"q": "Does the user have any pet allergies?"}),
        ],
    ),

    # --------------------------------------------------------------
    Scenario(
        title="E. Conflict / supersession (decision changes over time)",
        story=(
            "User's deployment target changes twice over months. Months later the agent "
            "must answer 'where do we deploy' with the LATEST, not the FIRST, choice."
        ),
        steps=[
            Step("record", "January — initial choice",
                 {"items": [
                     {"type": "fact",
                      "content": "We are using AWS ECS Fargate for production deployment of all services."},
                 ]}),
            Step("record", "March — first migration discussed",
                 {"items": [
                     {"type": "fact",
                      "content": "Decision: migrate from AWS ECS to GCP Cloud Run next quarter for cost reasons."},
                     {"type": "activity",
                      "kind": "decision",
                      "content": "AWS ECS will be deprecated for new services starting April."},
                 ]}),
            Step("record", "May — migration completed",
                 {"items": [
                     {"type": "fact",
                      "content": "Production is now fully on GCP Cloud Run; AWS account closed except billing.",
                      "importance": 0.9, "pin": True},
                 ]}),
            Step("ask", "October (months later) — where do we deploy",
                 {"q": "Where does our production run?"}),
            Step("ask", "October — what was the previous setup",
                 {"q": "What did we use before our current deployment?"}),
        ],
    ),

    # --------------------------------------------------------------
    Scenario(
        title="F. Safety-critical pin (one needle in a haystack)",
        story=(
            "User has a critical allergy. Among dozens of unrelated daily facts, the agent "
            "must still surface the allergy on any food-related query."
        ),
        steps=[
            Step("record", "Critical fact, pinned",
                 {"items": [
                     {"type": "fact",
                      "content": "SAFETY: User has a severe, anaphylactic peanut allergy. Always check ingredient lists for peanut traces.",
                      "pin": True, "importance": 0.99},
                 ]}),
            Step("record", "20 daily facts unrelated to food/allergy",
                 {"items": [
                     {"type": "fact", "content": f"Random daily detail #{i}: weather, code, errands, meetings."}
                     for i in range(20)
                 ]}),
            Step("record", "Two food-related daily facts (unrelated to peanuts)",
                 {"items": [
                     {"type": "fact", "content": "Had a salad with feta cheese for lunch today."},
                     {"type": "fact", "content": "Cooked pasta with tomato sauce for dinner."},
                 ]}),
            Step("ask", "User asks for a snack suggestion",
                 {"q": "What snacks should I order for the team meeting?"}),
            Step("ask", "Friend invites user to a restaurant",
                 {"q": "Any food restrictions I should mention?"}),
        ],
    ),

    # --------------------------------------------------------------
    Scenario(
        title="G. Team decision with attribution",
        story=(
            "Three people debate, one compromise wins. Days later, the agent is asked who said what."
        ),
        steps=[
            Step("record", "Architecture meeting transcript chunks",
                 {"items": [
                     {"type": "fact",
                      "content": "Alice argued strongly for mandatory static type hints across the codebase."},
                     {"type": "fact",
                      "content": "Bob disagreed and pushed for gradual typing: hints only where they help."},
                     {"type": "fact",
                      "content": "Carol proposed a compromise: enforce mypy in CI for new code only, grandfather existing files."},
                     {"type": "activity",
                      "kind": "decision",
                      "content": "Team accepted Carol's proposal: mypy in CI for new code, existing files exempt for now."},
                 ]}),
            Step("ask", "Later — what was Alice's view",
                 {"q": "What did Alice think about typing?"}),
            Step("ask", "Later — what did Bob argue",
                 {"q": "What was Bob's position on type hints?"}),
            Step("ask", "Later — what's the final decision",
                 {"q": "What did the team actually decide on typing?"}),
        ],
    ),

    # --------------------------------------------------------------
    Scenario(
        title="H. Research / learning notes over weeks",
        story=(
            "User reads memory-architecture papers across a month. Then asks 'what did I learn about multi-hop'."
        ),
        steps=[
            Step("record", "Week 1 — HippoRAG",
                 {"items": [
                     {"type": "fact",
                      "content": "Read HippoRAG paper: uses Personalized PageRank over an entity knowledge graph to handle multi-hop retrieval. Outperforms naive RAG on multi-hop QA."},
                 ]}),
            Step("record", "Week 2 — GraphRAG",
                 {"items": [
                     {"type": "fact",
                      "content": "Read GraphRAG (Microsoft): clusters entities into communities via Leiden, summarises each community, retrieves by query-community match."},
                 ]}),
            Step("record", "Week 3 — Letta",
                 {"items": [
                     {"type": "fact",
                      "content": "Read Letta paper: explicit memory blocks the agent edits with structured tool calls. Different paradigm from RAG retrieval."},
                 ]}),
            Step("record", "Week 4 — Zep / Graphiti",
                 {"items": [
                     {"type": "fact",
                      "content": "Read Zep's Graphiti: temporal knowledge graph, dual-time index (event_time vs system_time), incremental entity merge."},
                 ]}),
            Step("ask", "User now planning their own multi-hop logic",
                 {"q": "What did I learn about multi-hop retrieval approaches?"}),
            Step("ask", "User wonders about temporal modelling",
                 {"q": "What approaches handle temporal reasoning in memory?"}),
            Step("ask", "User thinks about non-RAG approaches",
                 {"q": "What systems use explicit memory editing instead of retrieval?"}),
        ],
    ),

    # --------------------------------------------------------------
    Scenario(
        title="I. Activity log → 'what did I do this week'",
        story=(
            "User does five things across the week. Friday asks 'what did I do'."
        ),
        steps=[
            Step("record", "Monday",
                 {"items": [
                     {"type": "activity",
                      "kind": "fix",
                      "content": "Fixed login bug: race condition between session refresh and cookie set."},
                 ]}),
            Step("record", "Tuesday",
                 {"items": [
                     {"type": "activity",
                      "kind": "review",
                      "content": "Code review for PR #1234 from external contributor adding webhook support."},
                 ]}),
            Step("record", "Wednesday",
                 {"items": [
                     {"type": "activity",
                      "kind": "feature",
                      "content": "Implemented rate limiting middleware using a token bucket per API key."},
                 ]}),
            Step("record", "Thursday",
                 {"items": [
                     {"type": "activity",
                      "kind": "test",
                      "content": "Wrote integration tests covering the OAuth flow including refresh-token rotation."},
                 ]}),
            Step("record", "Friday morning",
                 {"items": [
                     {"type": "activity",
                      "kind": "release",
                      "content": "Released v0.3.2 to staging environment; smoke tests passed."},
                 ]}),
            Step("ask", "Friday standup — recap",
                 {"q": "What did I do this week?"}),
            Step("ask", "Friday standup — what's in the release",
                 {"q": "What's in the v0.3.2 release?"}),
        ],
    ),

    # --------------------------------------------------------------
    Scenario(
        title="J. Fact tree (hierarchical decomposition)",
        story=(
            "A single complex fact has many sub-aspects. Each sub-aspect must be reachable individually."
        ),
        steps=[
            Step("record", "Project infrastructure as a fact tree",
                 {"items": [
                     {"type": "fact_tree",
                      "main": "Production for the orders service is hosted on Google Cloud.",
                      "subfacts": [
                          "Application code runs on Cloud Run, region europe-west1.",
                          "Primary database is Postgres 15 on Cloud SQL with HA replicas.",
                          "Secrets are stored in Secret Manager and injected at deploy time.",
                          "Static assets are served via Cloud CDN with a 1-hour TTL.",
                          "Logging goes through Cloud Logging with a 30-day retention policy.",
                      ],
                      "pin": True},
                 ]}),
            Step("ask", "Where do we store secrets",
                 {"q": "Where do we keep secret values for the orders service?"}),
            Step("ask", "What database",
                 {"q": "What database do we use in production?"}),
            Step("ask", "Where do logs go",
                 {"q": "How long do we retain production logs?"}),
            Step("ask", "Main hosting question",
                 {"q": "Where does the orders service run?"}),
        ],
    ),
]


def _fresh_engine():
    from pmb.core.engine import Engine
    tmp_home = Path(tempfile.mkdtemp(prefix="pmb-demo-"))
    tmp_ws = Path(tempfile.mkdtemp(prefix="pmb-demo-ws-"))
    os.environ["PMB_HOME"] = str(tmp_home)
    eng = Engine(cwd=tmp_ws, pmb_home=tmp_home, rerank_model=None)
    # Force-load model so recalls are not bottlenecked by cold-start.
    _ = eng.search.model
    return eng


def _truncate(s: str, n: int = 90) -> str:
    s = " ".join(s.split())  # collapse whitespace
    return s if len(s) <= n else s[: n - 1] + "…"


def run_scenario(sc: Scenario, idx: int) -> None:
    print()
    print("─" * 78)
    print(f"  {idx}. {sc.title}")
    print("─" * 78)
    print(f"  story: {sc.story}")
    print()

    eng = _fresh_engine()
    step_n = 0
    try:
        for st in sc.steps:
            step_n += 1
            if st.kind == "record":
                items = st.payload["items"]
                eng.record_batch(items)
                time.sleep(1.2)  # let async embed catch up
                print(f"  📝 step {step_n}: {st.note}")
                for it in items:
                    t = it.get("type", "?")
                    pin = " 📌" if it.get("pin") else ""
                    if t == "fact_tree":
                        head = it.get("main") or it.get("content") or ""
                        print(f"       └─ [{t}{pin}] {_truncate(head, 80)}")
                        for sf in it.get("subfacts") or []:
                            print(f"             • {_truncate(sf, 78)}")
                    elif t == "goal":
                        print(f"       └─ [{t}{pin}] {_truncate(it.get('title',''), 80)}")
                    elif t == "milestone":
                        print(f"       └─ [{t}{pin}] {_truncate(it.get('title',''), 80)}"
                              f"  (chain={it.get('chain_name')})")
                    else:
                        print(f"       └─ [{t}{pin}] {_truncate(it.get('content',''), 80)}")
            else:
                q = st.payload["q"]
                print()
                print(f"  🤔 step {step_n}: {st.note}")
                print(f"       Q:  \"{q}\"")
                t0 = time.perf_counter()
                pack = eng.recall(q, top_k=3)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                if not pack.results:
                    print(f"       A:  (no results in {elapsed_ms:.0f} ms)")
                else:
                    print(f"       A:  top-{len(pack.results)} (in {elapsed_ms:.0f} ms):")
                    for i, r in enumerate(pack.results, 1):
                        print(f"            [{i}]  {_truncate(r.content, 78)}")
                print()
    finally:
        try:
            eng.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=int, default=None,
                    help="Run only one scenario by index (1-based)")
    args = ap.parse_args()

    print()
    print("=" * 78)
    print("  PMB End-to-End Use Case Demo")
    print("=" * 78)
    print(f"  {len(SCENARIOS)} scenarios. For each: what gets recorded,")
    print("  then what the agent asks later, then what PMB recalls.")

    if args.scenario:
        run_scenario(SCENARIOS[args.scenario - 1], args.scenario)
    else:
        for i, sc in enumerate(SCENARIOS, 1):
            run_scenario(sc, i)

    print()
    print("=" * 78)
    print("  Demo complete. Read above to judge whether the recalls are useful.")
    print("=" * 78)


if __name__ == "__main__":
    main()
