# Demo Video Script (3–5 min)

**Goal:** show one full investigation loop end-to-end, then prove it generalizes across the 20-case benchmark.

## 0:00–0:20 — Cold open
Screen: dashboard case list, all 20 cases visible with verdicts/patterns.
VO: "This is an agent that investigates fraud the way a bank analyst would — pull the graph, check it against known patterns, ask when it's unsure, and only then recommend an action. It's built on TigerGraph, LangGraph, and Grok, and every decision traces back to a specific graph query."

## 0:20–1:00 — Trigger → Investigate (pick HHG-014, a real fraud case)
Screen: open HHG-014. Show the trigger text ("Real-time model scored transaction... at 0.XX").
VO: "This transaction came in with a risk score, nothing more. The agent opens a case and pulls the card's transaction history, its device profile, and its billing region from TigerGraph — three graph queries before it forms any opinion."
Screen: expand the evidence panel, show the `query:device_neighbors(txn_id=...)` reference and the claim it produced.

## 1:00–1:45 — Evidence gathering + pattern match
Screen: show the full evidence list — new device, shared with another card, cross-referenced against closed cases.
VO: "The device on this transaction is marked 'New' for the account — not proof by itself, people buy new phones — but it's also linked to five prior cases confirmed as fraud. That's the graph doing real work: this isn't a similarity score, it's a shared, named device."

## 1:45–2:30 — Uncertainty → evidence request → action change
Screen: show the "before vs after" next-best-action panel.
VO: "Before further evidence, the agent's confidence sits below the policy's 0.70 verify threshold, so it doesn't block yet — it asks. Once the (simulated) customer denies the charge, the recommendation changes: block the card, open a case, and because the device links to another card, file a report and place that card under monitoring too. Every one of these decisions cites the policy rule it's following."

## 2:30–3:00 — Explainability
Screen: show the summary + SAR narrative.
VO: "The agent writes its own SAR narrative from the evidence log — not a template with blanks filled in, a synthesis of what it actually found, in the order it found it."

## 3:00–3:40 — Case memory / stop condition
Screen: show `similar_prior_cases` and the stop_reason field.
VO: "It also knows when to stop. Once the probability crosses a decisive threshold or independent evidence settles the question, it stops looping — that's logged explicitly, not left implicit in the code."

## 3:40–4:20 — Zoom out to the benchmark
Screen: dashboard case list, filter by verdict.
VO: "Across all 20 benchmark cases, the agent calls it 15 legitimate and 5 fraud — close to the roughly-even split the dataset was built around, because it doesn't assume every flagged transaction is guilty. Two of those five get a filed report; the rest close as a case without one, per policy."

## 4:20–5:00 — Close
Screen: architecture diagram (TigerGraph schema + LangGraph node flow).
VO: "Trigger, investigate, gather evidence, decide, explain, remember — the same loop for every case, fully logged, fully explainable. That's the submission."
