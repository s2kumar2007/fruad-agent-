"""
run_live.py - End-to-end live pipeline runner.

Steps performed:
  1. Load credentials from .env
  2. Apply gsql/01_schema.gsql  (schema)
  3. Apply gsql/02_loading.gsql (loading job) + trigger it over data/*.csv
  4. Apply gsql/03_queries.gsql (installed queries)
  5. Verify connection with a vertex count
  6. Run agent.investigate   -> writes cases/*.json
  7. Run agent.validate_cases -> prints pass/fail
  8. Run agent.write_back    -> pushes results to live graph, prints vertex/edge counts
  9. Print summary report

Usage:
    python run_live.py

Prerequisites:
    - .env file exists with TG_HOST, TG_SECRET, TG_GRAPH, XAI_API_KEY
      TG_SECRET is generated from the Savanna console -> Database Secrets page.
    - pip install pyTigerGraph python-dotenv requests
"""

import glob
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TG_HOST   = os.environ.get("TG_HOST", "")
TG_SECRET = os.environ.get("TG_SECRET", "")
TG_GRAPH  = os.environ.get("TG_GRAPH", "FraudGraph")
XAI_KEY   = os.environ.get("XAI_API_KEY", "")

ERRORS = []
REQUIRED_VERTEX_TYPES = {
    "Customer",
    "Card",
    "Transaction",
    "DeviceProfile",
    "EmailDomain",
    "BillingRegion",
    "ClosedCase",
    "AgentCase",
}


def banner(msg):
    print(f"\n{'='*70}")
    print(f"  {msg}")
    print(f"{'='*70}")


# ---------------------------------------------------------------------------
# Step 1 - Validate credentials present
# ---------------------------------------------------------------------------
banner("Step 1 - Checking credentials")
missing = [v for v in ("TG_HOST", "TG_SECRET", "TG_GRAPH", "XAI_API_KEY")
           if not os.environ.get(v)]
if missing:
    print(f"[ERROR] Missing env vars: {', '.join(missing)}")
    print("  -> Copy .env.example to .env and fill in all values, then re-run.")
    print("  -> TG_SECRET comes from Savanna console -> your graph -> Database Secrets.")
    sys.exit(1)
print("  All required env vars present.")


# ---------------------------------------------------------------------------
# Step 2-4 - Apply schema, loading job, queries via pyTigerGraph GSQL
# ---------------------------------------------------------------------------
import requests
import pyTigerGraph as tg

def get_tg_token(host, secret, graph=None):
    payload = {"secret": secret}
    if graph:
        payload["graph"] = graph

    resp = requests.post(
        f"{host}/gsql/v1/tokens",
        json=payload,
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"Token request failed: {data.get('message')}")
    return data["token"]

def apply_gsql_file(conn, gsql_file):
    gsql_text = Path(gsql_file).read_text()
    try:
        return conn.gsql(gsql_text)
    except Exception as e:
        if gsql_file == "gsql/01_schema.gsql" and "graph name conflicts" in str(e):
            without_create_graph = "\n".join(
                line for line in gsql_text.splitlines()
                if not line.strip().upper().startswith("CREATE GRAPH ")
            )
            return conn.gsql(without_create_graph)
        raise

def run_optional_gsql(conn, gsql_text, description):
    try:
        result = conn.gsql(gsql_text)
        print(result)
    except Exception as e:
        print(f"  [OK] Could not {description}; continuing because it may not exist yet. ({e})")

def graph_has_required_schema(host, secret, graph):
    token = get_tg_token(host, secret, graph)
    scoped_conn = tg.TigerGraphConnection(host=host, graphname=graph, apiToken=token)
    scoped_conn.apiToken = token
    return REQUIRED_VERTEX_TYPES.issubset(set(scoped_conn.getVertexTypes()))

banner("Step 2-4 - Connecting to TigerGraph and applying GSQL files")

try:
    global_token = get_tg_token(TG_HOST, TG_SECRET)
    conn = tg.TigerGraphConnection(host=TG_HOST, graphname=TG_GRAPH, apiToken=global_token)
    conn.apiToken = global_token
    print(f"  Connected to {TG_HOST}  graph={TG_GRAPH}  token_scope=global")
except Exception as e:
    print(f"  [AUTH ERROR] {e}")
    ERRORS.append(f"Auth: {e}")
    sys.exit(1)

for step_num, gsql_file in [(2, "gsql/01_schema.gsql"),
                             (3, "gsql/02_loading.gsql")]:
    banner(f"Step {step_num} - Applying {gsql_file}")
    try:
        if step_num == 2 and graph_has_required_schema(TG_HOST, TG_SECRET, TG_GRAPH):
            print("  Required FraudGraph vertex types are already present; schema is applied.")
        else:
            if step_num == 3:
                run_optional_gsql(conn, f"USE GRAPH {TG_GRAPH}\nDROP JOB load_fraud_graph", "drop existing loading job")
            result = apply_gsql_file(conn, gsql_file)
            print(result)
            if step_num == 2 and "Please 'use global' first" in str(result):
                if graph_has_required_schema(TG_HOST, TG_SECRET, TG_GRAPH):
                    print("  Required FraudGraph vertex types are already present; treating schema as applied.")
                else:
                    raise RuntimeError("Schema DDL did not apply and required vertex types are missing.")
    except Exception as e:
        msg = f"[ERROR applying {gsql_file}] {e}"
        print(f"  {msg}")
        ERRORS.append(msg)
        if step_num == 2:
            print("  Step 2 failed; stopping before loading, queries, and live graph operations.")
            sys.exit(1)

try:
    graph_token = get_tg_token(TG_HOST, TG_SECRET, TG_GRAPH)
    conn = tg.TigerGraphConnection(host=TG_HOST, graphname=TG_GRAPH, apiToken=graph_token)
    conn.apiToken = graph_token
    print(f"  Switched to graph-scoped token for {TG_GRAPH}.")
except Exception as e:
    print(f"  [AUTH ERROR] {e}")
    ERRORS.append(f"Graph auth: {e}")
    sys.exit(1)

banner("Step 4 - Applying gsql/03_queries.gsql")
try:
    run_optional_gsql(
        conn,
        "USE GRAPH {graph}\nDROP QUERY card_window, device_neighbors, region_cluster, card_history, similar_closed_cases, write_agent_case".format(graph=TG_GRAPH),
        "drop existing queries",
    )
    result = apply_gsql_file(conn, "gsql/03_queries.gsql")
    print(result)
except Exception as e:
    msg = f"[ERROR applying gsql/03_queries.gsql] {e}"
    print(f"  {msg}")
    ERRORS.append(msg)


# ---------------------------------------------------------------------------
# Step 3b - Trigger loading job over data/*.csv
# ---------------------------------------------------------------------------
banner("Step 3a - Building loader CSVs")
try:
    import agent.build_graph_csvs as csv_builder
    csv_builder.run()

    # Check for missing source files and warn loudly
    from agent.data_store import DATA_DIR
    if not (DATA_DIR / "identity.csv").exists():
        print("  [WARNING] data/identity.csv is MISSING!")
        print("            Device signals (edges_from_device.csv, devices.csv) will be empty.")
        print("            Device-based fraud detection rules will be non-functional.")
    if not (DATA_DIR / "closed_cases_history.csv").exists():
        print("  [WARNING] data/closed_cases_history.csv is MISSING!")
        print("            Case memory (closed_cases.csv, etc.) will be empty.")
        print("            Prior case retrieval will be non-functional.")
except Exception as e:
    msg = f"[ERROR building loader CSVs] {e}"
    print(f"  {msg}")
    ERRORS.append(msg)

banner("Step 3b - Triggering loading job")
DATA_FILES = {
    "f_customers"    : "data/customers.csv",
    "f_cards"        : "data/cards.csv",
    "f_txns"         : "data/graph_transactions.csv",
    "f_devices"      : "data/devices.csv",
    "f_regions"      : "data/regions.csv",
    "f_emails"       : "data/emails.csv",
    "f_closed"       : "data/closed_cases.csv",
    "e_made"         : "data/edges_made.csv",
    "e_device"       : "data/edges_from_device.csv",
    "e_billed"       : "data/edges_billed_in.csv",
    "e_email"        : "data/edges_purchaser_email.csv",
    "e_next"         : "data/edges_next.csv",
    "e_closed_inv"   : "data/edges_closed_involves.csv",
    "e_closed_card"  : "data/edges_closed_on_card.csv",
    "e_closed_conn"  : "data/edges_closed_connected.csv",
}
try:
    for label, fpath in DATA_FILES.items():
        if not Path(fpath).exists():
            print(f"  [SKIP] {fpath} not found locally")
            continue
        print(f"  Uploading {fpath} ...")
        result = conn.runLoadingJobWithFile(fpath, fileTag=label, jobName="load_fraud_graph")
        print(f"    -> {result}")
except Exception as e:
    msg = f"[ERROR during loading job] {e}"
    print(f"  {msg}")
    ERRORS.append(msg)


# ---------------------------------------------------------------------------
# Step 5 - Verify with vertex counts
# ---------------------------------------------------------------------------
banner("Step 5 - Verifying connection (vertex counts)")
try:
    counts = conn.getVertexCount("*")
    for vtype, cnt in counts.items():
        print(f"  {vtype:30s}  {cnt:>8,}")
    if all(v == 0 for v in counts.values()):
        print("  [WARN] All vertex counts are zero - loading job may not have run yet.")
except Exception as e:
    msg = f"[ERROR getting vertex counts] {e}"
    print(f"  {msg}")
    ERRORS.append(msg)


# ---------------------------------------------------------------------------
# Step 6 - Re-run investigate.py
# ---------------------------------------------------------------------------
banner("Step 6 - Running agent.investigate (live store)")
import importlib, agent.investigate as inv
importlib.reload(inv)

stub_cases = {}
for p in sorted(glob.glob("cases/*.json")):
    with open(p) as f:
        d = json.load(f)
        stub_cases[d["case_id"]] = d

try:
    inv.run_all()
    print("  investigate.py completed.")
except Exception as e:
    msg = f"[ERROR in investigate] {e}"
    print(f"  {msg}")
    ERRORS.append(msg)


# ---------------------------------------------------------------------------
# Step 7 - Diff new cases vs stub-generated cases
# ---------------------------------------------------------------------------
banner("Step 7 - Diffing live vs stub cases")
CHANGED = []
for p in sorted(glob.glob("cases/*.json")):
    with open(p) as f:
        live = json.load(f)
    cid = live["case_id"]
    stub = stub_cases.get(cid)
    if not stub:
        print(f"  {cid}: no stub baseline")
        continue
    lc, sc = live["case"], stub["case"]
    diffs = []
    for field in ("verdict", "pattern", "fraud_probability", "exposure_usd"):
        if lc.get(field) != sc.get(field):
            diffs.append(f"{field}: stub={sc.get(field)!r} -> live={lc.get(field)!r}")

    live_final_actions = [a.get("action", "") for a in live.get("next_best_actions", {}).get("final", [])]
    stub_final_actions = [a.get("action", "") for a in stub.get("next_best_actions", {}).get("final", [])]
    if live_final_actions != stub_final_actions:
        diffs.append(f"final_actions: stub={stub_final_actions!r} -> live={live_final_actions!r}")

    live_evidence = {(e.get("claim", ""), e.get("source", ""), e.get("ref", "")) for e in lc.get("evidence", [])}
    stub_evidence = {(e.get("claim", ""), e.get("source", ""), e.get("ref", "")) for e in sc.get("evidence", [])}
    if live_evidence != stub_evidence:
        diffs.append("evidence trail changed")
    if diffs:
        print(f"  {cid}: CHANGED -> " + "; ".join(diffs))
        CHANGED.append({"case_id": cid, "diffs": diffs})
    else:
        print(f"  {cid}: unchanged")

if not CHANGED:
    print("\n  No material changes between stub and live outputs.")
else:
    print(f"\n  {len(CHANGED)} case(s) changed materially.")


# ---------------------------------------------------------------------------
# Step 8 - Write-back all 20 cases
# ---------------------------------------------------------------------------
banner("Step 8 - Writing all cases back to live graph (agent.write_back)")
import agent.write_back as wb
written = 0
write_errors = []
try:
    case_pack = inv.load_case_pack()
    lookup = {row["case_id"]: row for row in case_pack}
    for path in sorted(glob.glob("cases/*.json")):
        with open(path) as f:
            answer = json.load(f)
        cid = answer["case_id"]
        card_id = lookup.get(cid, {}).get("card_id", "")
        try:
            wb.write_case_with_card(conn, cid, answer, card_id)
            written += 1
            print(f"  {cid} -> written  (graph_case_id={answer['case']['graph_case_id']})")
        except Exception as e:
            msg = f"{cid}: {e}"
            write_errors.append(msg)
            print(f"  {cid}: [ERROR] {e}")
    print(f"\n  Wrote {written}/20 cases successfully.")
    if write_errors:
        ERRORS.extend(write_errors)
except Exception as e:
    msg = f"[ERROR in write_back setup] {e}"
    print(f"  {msg}")
    ERRORS.append(msg)

# Verify written vertex count
try:
    ac_count = conn.getVertexCount("AgentCase")
    print(f"  AgentCase vertex count in graph: {ac_count}")
except Exception as e:
    print(f"  [WARN] Could not fetch AgentCase count: {e}")


# ---------------------------------------------------------------------------
# Step 9 - Validate
# ---------------------------------------------------------------------------
banner("Step 9 - Running validate_cases.py")
import subprocess, sys as _sys
result = subprocess.run(
    [_sys.executable, "agent/validate_cases.py"],
    capture_output=True, text=True
)
print(result.stdout)
if result.returncode != 0:
    print(result.stderr)
    ERRORS.append("validate_cases.py returned non-zero exit code")


# ---------------------------------------------------------------------------
# Final report
# ---------------------------------------------------------------------------
banner("FINAL REPORT")
print(f"  Cases changed (stub -> live): {len(CHANGED)}")
for c in CHANGED:
    print(f"    {c['case_id']}: {'; '.join(c['diffs'])}")
print(f"\n  Cases written to graph: {written}/20")
print(f"\n  Errors encountered ({len(ERRORS)}):")
for e in ERRORS:
    print(f"    x {e}")
if not ERRORS:
    print("    None - clean run!")
