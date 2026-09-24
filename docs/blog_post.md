# Agentic Fraud Investigation on TigerGraph: How We Built It for HHGoa'26

## What we built

An agent that investigates fraud the way a real analyst does: pull the transaction's neighborhood
from a live TigerGraph Savanna instance, check it against the bank's five documented fraud
patterns, ask for confirmation when the evidence is thin, and only then recommend blocking a
card or filing a report — with every step logged and traceable back to a specific graph query.

Given a fraud signal (a risk-scored transaction, a customer complaint, or an analyst's own
request), the agent:
1. Opens a case and pulls the flagged transaction's card, device, and billing-region neighborhood
   from TigerGraph via installed GSQL queries.
2. Checks it against card testing, card-not-present fraud, card-not-present-from-a-new-device,
   out-of-region use, and account takeover — plus a catch-all `undocumented` category for anything
   that doesn't fit.
3. Retrieves similar closed cases from graph memory to see whether this device or region has
   shown up in confirmed fraud before.
4. If the evidence is inconclusive, runs a controlled evidence-gathering step (customer validation,
   step-up auth) and states its assumption explicitly rather than hiding it.
5. Recommends a next action — allow, block, monitor, escalate, file a SAR — routed through the
   bank's actual approval policy (auto / L1 / L2).
6. Writes its findings back into the graph as a new `AgentCase` vertex, linked to whatever
   evidence and prior cases it used, so the next investigation can find it.

## Architecture

- **TigerGraph Savanna** holds the live graph: customers, cards, transactions, device profiles,
  billing regions, closed cases, and the agent's own cases, connected by ownership,
  transaction-flow, shared-device, and case-similarity edges. The full schema is in
  `gsql/01_schema.gsql`; six named queries expose it to the agent (`gsql/03_queries.gsql`).
- **`agent/tg_store.py` — `TigerGraphMCPStore`** wraps the live Savanna connection using
  `pyTigerGraph`, reading credentials from `.env`. Every public method (`card_window`,
  `device_neighbors`, `region_cluster`, `card_history`, `similar_closed_cases`,
  `write_agent_case`) calls the matching installed GSQL query directly — no in-memory mirror.
- **LangGraph** runs the investigation as an explicit state machine:
  Trigger → OpenCase → GatherEvidence → AssessUncertainty → (loop, capped at 3) →
  RecommendAction → Explain → UpdateMemory. The loop has a hard cap independent of the
  confidence threshold, so a stuck investigation can't run forever.
- **Grok (`grok-beta`)** generates the natural-language case summary and SAR narrative, grounded
  in the structured evidence returned by the graph queries. Pattern detection and probability
  scoring remain rule-based and fully traceable; Grok explains them, it doesn't invent them. If
  the xAI API is unreachable the pipeline falls back to a deterministic template — no silent
  failures.

## Live pipeline results (confirmed run)

The full pipeline — schema applied to Savanna, data loaded, queries installed, all 20 cases
investigated against the live graph, write-back confirmed — produced the following results:

| Metric | Value |
|--------|-------|
| Cases investigated | 20 / 20 |
| Fraud verdicts | 5 |
| Legitimate verdicts | 6 |
| Uncertain / escalated | 9 |
| SARs filed | 2 (HHG-008, HHG-018) |
| Cases with evidence requests | 9 / 20 |
| Cases where actions changed after evidence request | 13 / 20 |
| Total confirmed exposure | $2,980.84 |
| Fraud probability range | 0.05 – 0.75 |



**Pattern breakdown across investigated cases:**
- `card_not_present_new_device` × 11
- `none` (legitimate) × 6
- `undocumented` (fraud) × 2 (HHG-008, HHG-018 — SAR filed)
- `out_of_region_use` (fraud) × 1 (HHG-003)


**Stub vs live diff:** No material changes in verdict, pattern, or risk level between the
stub-generated baseline and the live TigerGraph run. The graph store's query outputs matched
the local CSV mirror exactly for all 20 cases, confirming the GSQL queries are correct
equivalents of the Python detectors. Case summaries differ in prose style (Grok vs. template)
but not in factual content.

## How TigerGraph is used, specifically

Every claim in a case's evidence log cites the exact query that produced it —
`query:card_window(card_id=C12382-K1,hours=2)`, `query:device_neighbors(txn_id=3450629)` —
so an analyst (or a judge) can re-run the same traversal and get the same answer. Case memory
works the same way in both directions: closing a case writes an `AgentCase` vertex connected to
its evidence transactions, connected cards, and any `ClosedCase` vertices it resembled; opening
a new case queries that similarity edge to pull relevant history before reasoning begins.

## What we learned

The single biggest accuracy lever wasn't the LLM — it was getting the graph traversal right.
Two bugs in particular would have quietly wrecked the benchmark: a device fingerprint that
collided on generic signatures (a plain "Windows + Chrome" combination is shared by hundreds of
unrelated cards, not a real shared-device signal), and a self-referential edge case where a
card's own device history got reported as a "connected" second compromised card. Neither surfaced
from the final JSON output alone — they only showed up by checking cluster sizes and re-reading
what the evidence claims were actually saying.

We also learned that letting the "assumed customer response" be a function of the same
probability that triggered the request is a subtle trap: it turns every borderline signal into
confirmed fraud by construction, contradicting the dataset's framing that roughly half of flagged
activity is legitimate. Grounding the simulated response in evidence independent of the
triggering probability — a shared device on a *confirmed* closed case, or a card-testing
sequence with a cleared large purchase — fixed that.

## What we'd improve

- **Hybrid scoring:** Replace the rule-based probability scorer with a hybrid — keep the
  deterministic detectors for auditability, but let the LLM flag disagreements for analyst
  review rather than only synthesising prose after the fact. The current architecture already
  separates "pattern detection" (rule-based, `investigate.py`) from "synthesis" (Grok,
  `graph_state.py`), so this is a natural next step.
- **Vector-index retrieval:** Extend `similar_closed_cases` to use TigerGraph's vector index
  over case narratives, not just structural matches (shared device/region/pattern), to catch
  semantically similar fraud that doesn't share an explicit graph edge.
- **Feedback loop:** Write analyst overrides back as training signal — the case-memory design
  implies this loop but doesn't yet close it. Every `AgentCase` vertex already has edges back
  to its evidence; adding an `ANALYST_OVERRIDE` edge type would be enough scaffolding.
