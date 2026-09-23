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
- ✅ **`agent/tg_store.py`**: live `TigerGraphMCPStore` using pyTigerGraph + DB secret auth
- ✅ **`agent/graph_state.py`**: Grok (`grok-beta`) wired for case summaries and SAR narratives
- ⏳ **Not done here (needs your environment):** running `run_live.py` against a live Savanna instance, recorded demo video

## Auth: TigerGraph Savanna uses Database Secrets, not username/password

Savanna's REST API does not accept username/password directly. Instead:

1. In the Savanna console → your graph → **Admin panel → Database Secrets → Add Secret**
2. Copy the secret string shown (you cannot retrieve it again after closing the dialog)
3. Add it to your `.env` as `TG_SECRET=<that string>`

The connection code (`agent/tg_store.py`, `run_live.py`) then does:
```python
conn = tg.TigerGraphConnection(host=TG_HOST, graphname=TG_GRAPH)
conn.apiToken = conn.getToken(TG_SECRET)   # pre-generated secret — no createSecret() call
```

## Why some things are stubbed

This was built in a sandboxed container with no network access to TigerGraph Cloud or xAI's API — only package registries. So:

- `agent/data_store.py` (`LocalGraphStore`) is a full in-memory mirror of the graph, built from the raw CSVs. Every method matches a GSQL query in `gsql/03_queries.gsql` **by name and by argument shape**, so swapping in `TigerGraphMCPStore` is a drop-in replacement, not a rewrite.
- `agent/graph_state.py` has Grok wired to call the real `api.x.ai` endpoint, but it needs `XAI_API_KEY` set in your environment to actually run — never paste that key into chat or into this file.
- `agent/write_back.py` is a real `pyTigerGraph` script, ready to run once `TG_HOST` and `TG_SECRET` are set.

## Running it right now (no live services needed)

```bash
cd fraud-agent
python3 -m agent.investigate          # regenerates cases/*.json from the raw dataset
python3 agent/validate_cases.py       # schema-checks all 20 outputs
streamlit run ui/dashboard.py         # opens the analyst dashboard
```

## Wiring in the real services

1. Copy `.env.example` → `.env` and fill in `TG_HOST`, `TG_SECRET`, `TG_GRAPH`, `XAI_API_KEY`
2. Run the full pipeline in one step:
   ```bash
   python run_live.py
   ```
   This applies the schema, loads data, installs queries, runs all 20 cases against live TigerGraph,
   validates outputs, and writes results back to the graph.
5. Re-run `python3 -m agent.investigate` with the live store wired in, re-validate, re-record the demo against live data if the numbers shift.

## Known limitations of the current heuristic engine

- Probability scoring is rule-based, not learned — defensible and fully traceable, but a human analyst reading the same evidence might weight it differently. This is intentional: the LLM should explain and sanity-check this scoring, not replace it wholesale.
- The simulated customer/analyst responses are a stated assumption (see `agent/investigate.py::simulate_customer_response`'s docstring), not real feedback — required since there's no live customer channel in this benchmark.
- `card_id` derivation (`customer_id-K<n>`) is inferred from the data (verified against the dataset directly, not guessed) but should be re-validated if the organizers publish an authoritative mapping.
