We built an agentic fraud investigation system for @TigerGraphDB's Hacker House Goa '26 track — an agent that actually investigates, not a classifier with extra steps.

Given a flagged transaction, it pulls the card's graph neighborhood from TigerGraph (device, region, prior cases), checks it against 5 documented fraud patterns, asks for evidence when it's genuinely unsure, and only then recommends blocking a card or filing a report — every claim traced back to the exact graph query that produced it.

Stack: TigerGraph + TigerGraph MCP for the graph tools, LangGraph for the investigation loop, Grok for reasoning/synthesis.

The thing that mattered most wasn't the LLM — it was getting the graph traversal right. A generic device fingerprint colliding across hundreds of unrelated cards would've quietly wrecked the results if we hadn't caught it by checking cluster sizes by hand.

20/20 benchmark cases, full evidence trail, SARs filed where policy requires it, next-best-action recorded before and after evidence requests.

Blog post + demo: [link]

#TigerGraph #AgenticAI #FraudDetection #HackerHouseGoa
