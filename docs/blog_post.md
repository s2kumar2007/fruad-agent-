# Agentic Fraud Investigation on TigerGraph: How We Built It for HHGoa'26

## What we built

An agent that investigates fraud the way a real analyst does: pull the transaction's neighborhood in the graph, check it against the bank's five documented fraud patterns, ask for confirmation when the evidence is thin, and only then recommend blocking a card or filing a report — with every step logged and traceable back to a specific graph query.

Given a fraud signal (a risk-scored transaction, a customer complaint, or an analyst's own request), the agent:
1. Opens a case and pulls the flagged transaction's card, device, and billing-region neighborhood from TigerGraph.
2. Checks it against card testing, card-not-present fraud, card-not-present-from-a-new-device, out-of-region use, and account takeover — plus a catch-all `undocumented` category for anything that doesn't fit.
3. Retrieves similar closed cases from graph memory to see whether this device or region has shown up in confirmed fraud before.
4. If the evidence is inconclusive, simulates a controlled evidence-gathering step (customer validation, step-up auth) and states its assumption explicitly rather than hiding it.
5. Recommends a next action — allow, block, monitor, escalate, file a SAR — routed through the bank's actual approval policy (auto / L1 / L2).
6. Writes its findings back into the graph as a new case, linked to whatever evidence and prior cases it used, so the next investigation can find it.

## Architecture

- **TigerGraph** holds the graph: customers, cards, transactions, device profiles, billing regions, closed cases, and the agent's own cases, connected by ownership, transaction-flow, shared-device, and case-similarity edges.
- **TigerGraph MCP** exposes six named queries as tools — `card_window`, `device_neighbors`, `region_cluster`, `card_history`, `similar_closed_cases`, and `write_agent_case` — each with a matching Python function so the agent's reasoning logic doesn't care whether it's talking to a live instance or a local mirror during development.
- **LangGraph** runs the investigation as an explicit state machine: Trigger → OpenCase → GatherEvidence → AssessUncertainty → (loop back to GatherEvidence, capped) → RecommendAction → Explain → UpdateMemory. The loop has a hard cap independent of the confidence threshold, so a stuck investigation can't run forever even if the probability estimate never settles.
- **Grok** handles reasoning and synthesis — turning structured evidence into a natural-language case summary and SAR narrative — but never substitutes for the graph traversal itself. The pattern detection and probability scoring are rule-based and fully traceable; the LLM explains them, it doesn't invent them.
- **GraphRAG**: the fraud policy, five pattern definitions, and regulatory references are chunked and retrieved alongside graph evidence, so the context handed to the LLM is synthesized, not a raw document dump.

## How TigerGraph is used, specifically

Every claim in a case's evidence log cites the exact query that produced it — `query:card_window(card_id=C12382-K1,hours=2)`, `query:device_neighbors(txn_id=3450629)` — so an analyst (or a judge) can re-run the same traversal and get the same answer. Case memory works the same way both directions: closing a case writes an `AgentCase` vertex connected to its evidence transactions, connected cards, and any `ClosedCase` vertices it turned out to resemble; opening a new case queries that same similarity edge to pull relevant history before the agent even starts reasoning.

## What we learned

The single biggest accuracy lever wasn't the LLM — it was getting the graph traversal right. Two bugs in particular would have quietly wrecked the benchmark if we'd shipped them: a device fingerprint that collided on generic signatures (a plain "Windows + Chrome" combination is shared by hundreds of unrelated cards in this dataset, not a real shared-device signal), and a self-referential edge case where a card's own device history got reported back as a "connected" second compromised card. Neither would have been obvious from the final JSON output alone — they only surfaced by checking cluster sizes and re-reading what the evidence claims were actually saying.

We also learned that letting the "assumed customer response" be a function of the same probability that triggered the request is a subtle trap: it turns every borderline signal into confirmed fraud by construction, which contradicts the dataset's own framing that roughly half of flagged activity is legitimate. Grounding the simulated response in evidence independent of the triggering probability — a shared device on a *confirmed* closed case, or a card-testing sequence with a cleared large purchase — fixed that.

## What we'd improve

- Replace the rule-based probability scorer with a hybrid: keep the deterministic detectors for auditability, but let the LLM flag disagreements for analyst review rather than only synthesizing prose after the fact.
- Extend `similar_closed_cases` retrieval to use TigerGraph's vector index over case narratives, not just structural matches (shared device/region/pattern), to catch semantically similar fraud that doesn't share an explicit graph edge.
- Add a feedback loop where analyst overrides of the agent's recommendation get written back as training signal for the next case, closing the loop the case-memory design implies but doesn't yet fully exploit.
