# Alternix AI integration feedback

This note captures lightweight feedback from using PMB as an external memory layer for a local voice assistant project.

## Environment

- Assistant: Alternix AI / Alt, local web and voice assistant prototype.
- Main use case: long-term user memory across chats, facts about the user, friends, projects, preferences, and conversation summaries.
- Languages tested most often: Russian and Ukrainian, with Ukrainian used as the assistant response language.
- Integration style: PMB runs as a separate local memory service behind a small HTTP bridge, while the assistant backend decides what should be written or recalled.

## What worked well

- PMB is easy to run as an isolated memory component without coupling it tightly to the main assistant backend.
- Explicit fact recall works well when the stored fact and query use close wording, especially after normalizing names and entities on the assistant side.
- The local-first storage model fits a home assistant well because private memory stays on the server.
- The 0.2.1 release looks useful for real integrations: faster lightweight commands, import/export, `pmb why`, git-backed workspace sync, and embedder dimension checks are practical improvements.

## Issues seen in assistant integration

- The hardest part was not PMB storage itself, but deciding what should be written. A smaller LLM sometimes stores raw user wording instead of a clean atomic fact.
- Cross-language recall needs careful normalization. For example, a Russian query can mention `Алексей`, while the stored Ukrainian fact may use `Олексій`.
- Conversation summaries need to be separated from durable facts in the assistant UI and prompt injection layer, otherwise summaries can look like facts.
- For voice assistants, stale or contradictory memories need a clear update/delete flow so old chat context does not override newer PMB facts.

## Suggestions

- Add a small documented recipe for assistant-style memory extraction: `raw user message -> clean atomic fact -> metadata -> recall`.
- Add examples for multilingual name/entity aliases, especially RU/UK pairs.
- Add a simple integration example that runs PMB behind an HTTP bridge, since many non-MCP apps will want to call memory from a web backend.
- Consider documenting recommended metadata fields for assistants: `kind`, `entity`, `attribute`, `name`, `source`, `chat_id`, and `confidence`.

Overall, PMB was practical to integrate as a standalone memory brain. Most remaining quality problems came from the assistant router and the LLM-based fact extraction layer, not from PMB's ability to store and retrieve events.
