# Agentic Fraud Investigation — TigerGraph HHGoa'26

## Status: Complete (Fully Live & Validated)

- ✅ **TigerGraph Schema & GSQL Queries**: `gsql/01_schema.gsql`, `gsql/02_loading.gsql`, and `gsql/03_queries.gsql` applied and installed on live TigerGraph Savanna (`FraudGraph`).
- ✅ **Installed Query Compilation**: All 6 GSQL queries (`card_window`, `device_neighbors`, `region_cluster`, `card_history`, `similar_closed_cases`, `write_agent_case`) compiled into C++ endpoints via `INSTALL QUERY ALL`.
- ✅ **Live Store Integration**: `agent/tg_store.py` (`TigerGraphMCPStore`) runs live graph queries over pyTigerGraph with `TG_SECRET` token authentication and high-throughput batch vertex fetching.
- ✅ **Full Benchmark Investigation**: All 20 benchmark cases investigated against the live graph (`cases/*.json`).
- ✅ **Schema & Rule Validation**: `python agent/validate_cases.py` passes with **ALL VALID** (verdicts, patterns, SAR consistency, action routing, stopping conditions).
- ✅ **Case Memory Write-Back**: `python -m agent.write_back` writes all 20/20 cases back to the live graph as `AgentCase` vertices connected via `AGENT_INVOLVES`, `AGENT_ON_CARD`, `AGENT_CONNECTED_TO`, and `SIMILAR_TO` edges.
- ✅ **Streamlit Analyst Dashboard**: `ui/dashboard.py` runs interactively, providing verdict filtering, case selection, full evidence inspection, action history, and SAR review.
- ✅ **Documentation**: Architectural write-up, blog post (`docs/blog_post.md`), and social post (`docs/social_post.md`) complete.

---

## Authentication: Database Secrets (Savanna REST API)

TigerGraph Savanna requires pre-generated Database Secrets rather than username/password:

1. In your Savanna console → select your graph instance (`FraudGraph`) → **Admin panel → Database Secrets → Add Secret**.
2. Copy the generated secret string.
3. Save it to your `.env` file as `TG_SECRET=<secret_string>`.

All components (`agent/tg_store.py`, `agent/write_back.py`, `run_live.py`) authenticate via:
```python
import pyTigerGraph as tg
from agent.tg_store import get_tg_token

token = get_tg_token(host=TG_HOST, secret=TG_SECRET, graph=TG_GRAPH)
conn = tg.TigerGraphConnection(host=TG_HOST, graphname=TG_GRAPH, apiToken=token)
conn.apiToken = token
```

---

## Dataset Prerequisites: `data/identity.csv`

The core repository contains the schema, queries, test cases, and derived graph artifacts. However:
- **`data/identity.csv` is NOT committed to this repository** due to size (144k+ rows, ~27MB).
- Anyone re-running the full local CSV derivation (`agent/build_graph_csvs.py`) or data loading pipeline from scratch must supply `data/identity.csv` (and `data/transactions.csv`) from the raw **IEEE-CIS / HHGOA_IEEE** dataset.
- The pipeline expects raw data at:
  - Default: `fraud-agent/data/identity.csv` and `fraud-agent/data/transactions.csv`
  - Or customized via environment variable: `HHGOA_DATA_DIR=/path/to/raw/data` (as resolved in `agent/data_store.py`).

---

## Quickstart & Verification

### 1. Inspect & Validate Cases
```bash
# Verify all 20 output case files against schema and business policy
python agent/validate_cases.py
```
Expected output: `ALL VALID`.

### 2. Launch Analyst Dashboard
```bash
streamlit run ui/dashboard.py
```

### 3. Re-run Investigation & Write-Back Against Live TigerGraph
Ensure `.env` contains `TG_HOST`, `TG_SECRET`, `TG_GRAPH`, and `XAI_API_KEY`:
```bash
# Re-run investigation over live graph
python -m agent.investigate

# Write cases back to live graph memory
python -m agent.write_back

# Or run the complete automated end-to-end pipeline:
python run_live.py
```

---

## Architecture & Policy Rules

- **Deterministic Pattern Detectors**: 5 documented patterns (`card_testing`, `card_not_present_fraud`, `card_not_present_new_device`, `out_of_region_use`, `account_takeover`) plus `undocumented` fallback.
- **Evidence Gathering Under Uncertainty**: Requests customer validation or step-up authentication when initial probability is in the ambiguous band (0.15 < prob < 0.85).
- **Policy Enforcement**: Strict action routing (`auto`, `L1`, `L2`) based on action severity and exposure thresholds.
- **Explainability**: Grok (`grok-beta`) generates natural language summaries and SAR narratives directly grounded in graph query outputs.

