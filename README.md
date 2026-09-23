# Agentic Fraud Investigation — TigerGraph HHGoa'26

## Status

- ✅ Phase 0: Dataset read, summarized, schema understood
- ✅ Phase 1: GSQL schema + queries written; loader CSVs generated from real data (`data/`)
- ✅ Phase 2: Investigation logic (policy engine + 5 pattern detectors + memory retrieval) — proven correct against all 20 benchmark cases
- ✅ Phase 2b: LangGraph state machine (`agent/graph_state.py`) — compiles and runs end-to-end
- ✅ Phase 3: Evidence-gathering + action stubs, logged and clearly marked simulated
- ✅ Phase 4: All 20 benchmark answer files generated and schema-validated (`cases/*.json`)
- ✅ Phase 5: Streamlit dashboard (`ui/dashboard.py`) — smoke-tested, runs clean
- ✅ Phase 6: Blog post, demo script, social post drafted (`docs/`)
- ⬜ **Not done here (needs your environment):** live TigerGraph Savanna connection, TigerGraph MCP server wiring, real Grok API calls, actual write-back to a live graph, recorded demo video

## Why some things are stubbed

This was built in a sandboxed container with no network access to TigerGraph Cloud or xAI's API — only package registries. So:

- `agent/data_store.py` (`LocalGraphStore`) is a full in-memory mirror of the graph, built from the raw CSVs. Every method matches a GSQL query in `gsql/03_queries.gsql` **by name and by argument shape**, so swapping in a TigerGraph MCP-backed store is a drop-in replacement, not a rewrite.
- `agent/graph_state.py` has Grok wired to call the real `api.x.ai` endpoint, but it needs `XAI_API_KEY` set in your environment to actually run — never paste that key into chat or into this file.
- `agent/write_back.py` is a real `pyTigerGraph` script, ready to run once `TG_HOST` / `TG_USERNAME` / `TG_PASSWORD` are set.

## Running it right now (no live services needed)

```bash
cd fraud-agent
python3 -m agent.investigate          # regenerates cases/*.json from the raw dataset
python3 agent/validate_cases.py       # schema-checks all 20 outputs
streamlit run ui/dashboard.py         # opens the analyst dashboard
```

## Wiring in the real services (for Claude Code)

1. **TigerGraph Savanna**: provision it, enable auto-stop/auto-start, then:
   ```bash
   gsql gsql/01_schema.gsql
   # load data/*.csv per gsql/02_loading.gsql, then:
   gsql gsql/03_queries.gsql
   ```
2. **TigerGraph MCP**: point it at the instance; write a `TigerGraphMCPStore` class in `agent/tigergraph_mcp_store.py` implementing the same method signatures as `LocalGraphStore` (`card_window`, `device_neighbors`, `region_cluster`, `card_history`, `similar_closed_cases`). Swap the import at the top of `agent/graph_state.py`.
3. **Grok**: `export XAI_API_KEY=...` (never in chat), confirm the model string in `agent/graph_state.py` against current xAI docs.
4. **Write-back**: `export TG_HOST/TG_USERNAME/TG_PASSWORD`, `python3 -m agent.write_back`.
5. Re-run `python3 -m agent.investigate` with the live store wired in, re-validate, re-record the demo against live data if the numbers shift.

## Known limitations of the current heuristic engine

- Probability scoring is rule-based, not learned — defensible and fully traceable, but a human analyst reading the same evidence might weight it differently. This is intentional: the LLM should explain and sanity-check this scoring, not replace it wholesale.
- The simulated customer/analyst responses are a stated assumption (see `agent/investigate.py::simulate_customer_response`'s docstring), not real feedback — required since there's no live customer channel in this benchmark.
- `card_id` derivation (`customer_id-K<n>`) is inferred from the data (verified against the dataset directly, not guessed) but should be re-validated if the organizers publish an authoritative mapping.
