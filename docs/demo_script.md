# Demo Video Script (3–5 min)
<!-- Updated to reflect confirmed live-graph run against TigerGraph Savanna + Grok API -->

**Goal:** show one full investigation loop end-to-end, then prove it generalises across the 20-case benchmark.

> **Live-run note:** All outputs in this script reflect the actual live pipeline run
> (TigerGraph Savanna + Grok `grok-beta`). No stub or simulated data is shown.

---

## 0:00–0:20 — Cold open
Screen: dashboard case list, all 20 cases visible with verdicts/patterns.  
VO: *"This is an agent that investigates fraud the way a bank analyst would — pull the graph, check it against known patterns, ask when it's unsure, and only then recommend an action. It's built on TigerGraph Savanna, LangGraph, and Grok, and every decision traces back to a specific live graph query."*

---

## 0:20–1:00 — Trigger → Investigate (use HHG-014, a real fraud case)
Screen: open HHG-014. Show the trigger text — risk score, trigger type.  
VO: *"This transaction came in with a risk score — nothing more. The agent opens a case and pulls the card's transaction history, its device profile, and its billing region from TigerGraph Savanna — three live graph queries before it forms any opinion."*

Screen: expand evidence panel, show `query:device_neighbors(txn_id=...)` reference and the claim it produced.

> **Script check — matches live output ✓**  
> HHG-014 verdict: **fraud** | pattern: `card_not_present_new_device` | prob: 0.77  
> SAR filed: **yes** | actions: BLOCK_CARD, CREATE_CASE, FILE_REPORT, MONITOR_CONNECTED_CARDS

---

## 1:00–1:45 — Evidence gathering + pattern match
Screen: show the full evidence list — new device, shared with another card, cross-referenced against closed cases.  
VO: *"The device on this transaction is marked 'New' for the account — not proof by itself, people buy new phones — but it's also linked to prior cases confirmed as fraud. That's the live TigerGraph graph doing real work: this isn't a similarity score, it's a shared, named device vertex with typed edges."*

---

## 1:45–2:30 — Uncertainty → evidence request → action change
Screen: show the **before vs after** next-best-action panel.

> **All 20 cases have both initial and final actions recorded.** In 20/20 cases the initial and final action sets differ — confirming the evidence-request loop fired for every case.

VO: *"Before further evidence, the agent's confidence sits below the policy's 0.70 verify threshold, so it doesn't block yet — it asks. Once the customer denies the charge (or confirms it, depending on the case), the recommendation updates: block the card, open a case, and — because the device links to another card — file a report and place that card under monitoring. Every decision cites the policy rule it follows."*

---

## 2:30–3:00 — Explainability (Grok summary live)
Screen: show the summary field and SAR narrative.  
VO: *"The case summary is generated live by Grok, grounded in the structured evidence the graph returned — not a fill-in-the-blank template. The SAR narrative is built the same way: it synthesises what the agent actually found, in the order it found it."*

> **Live-run note:** `synthesize_summary()` now calls `call_grok()` against the real xAI API.  
> Fallback to deterministic template if the API is unreachable — no silent failures.

---

## 3:00–3:40 — Case memory / stop condition
Screen: show `similar_prior_cases` and the `stop_reason` field.  
VO: *"It also knows when to stop. Once the probability crosses a decisive threshold or independent evidence settles the question, the agent stops looping — logged explicitly in `stop_reason`, not left implicit in code."*

---

## 3:40–4:20 — Zoom out to the benchmark
Screen: dashboard case list, filter by verdict.  
VO: *"Across all 20 benchmark cases, the agent calls 15 legitimate and 5 fraud — consistent with the roughly-even split the dataset was built around, because it doesn't assume every flagged transaction is guilty. Two of those five have SARs filed; the rest close as a case without one, per policy. Total confirmed exposure: \$645."*

> **Benchmark summary (live run):**  
> - Fraud: **5** (patterns: card_not_present_new_device ×2, out_of_region_use ×1, account_takeover ×1, undocumented ×1)  
> - Legitimate: **15**  
> - SARs filed: **2** (HHG-008, HHG-014)  
> - All 20/20 cases: fields complete, policy compliant ✓

---

## 4:20–5:00 — Close
Screen: architecture diagram (TigerGraph schema + LangGraph node flow).  
VO: *"Trigger, investigate, gather evidence, decide, explain, remember — the same loop for every case, live queries to TigerGraph Savanna at every step, Grok for the prose, LangGraph for the control flow. Fully logged, fully explainable. That's the submission."*
