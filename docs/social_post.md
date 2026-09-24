We built an agentic fraud investigation system for @TigerGraphDB's Hacker House Goa '26 track — an agent that actually investigates, not a classifier with extra steps.

Given a flagged transaction, it pulls the card's graph neighborhood from a live TigerGraph Savanna instance (device, region, prior cases), checks it against 5 documented fraud patterns, asks for evidence when genuinely unsure, and only then recommends blocking a card or filing a report — every claim traced back to the exact live graph query that produced it.

Stack: TigerGraph Savanna + pyTigerGraph for live graph queries, LangGraph for the investigation state machine, Grok (grok-beta) for natural-language case summaries and SAR narratives.

Live benchmark results — 20/20 cases investigated against the live graph:
→ 5 fraud verdicts (card-not-present new device, out-of-region, undocumented)
→ 6 legitimate cleared — because not every flagged transaction is fraud
→ 9 uncertain escalated to analyst review with full graph evidence trail
→ 2 SARs filed where policy requires it (HHG-008, HHG-018)
→ 20/20 cases written back to TigerGraph as AgentCase vertices (confirmed live)
→ All 20 cases have initial + final next-best-actions recorded, evidence trail complete


The thing that mattered most wasn't the LLM — it was getting the graph traversal right. A generic device fingerprint colliding across hundreds of unrelated cards would have quietly wrecked the results if we hadn't caught it by checking cluster sizes and re-reading the evidence claims.

Full code, 20 case answer files, blog post + live demo: [link]

#TigerGraph #AgenticAI #FraudDetection #HackerHouseGoa #LangGraph #Grok
