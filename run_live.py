"""
run_live.py — End-to-end live pipeline runner.

Steps performed:
  1. Load credentials from .env
  2. Apply gsql/01_schema.gsql  (schema)
  3. Apply gsql/02_loading.gsql (loading job) + trigger it over data/*.csv
  4. Apply gsql/03_queries.gsql (installed queries)
  5. Verify connection with a vertex count
  6. Run agent.investigate   → writes cases/*.json
  7. Run agent.validate_cases → prints pass/fail
  8. Run agent.write_back    → pushes results to live graph, prints vertex/edge counts
  9. Print summary report

Usage:
    python run_live.py

Prerequisites:
    - .env file exists with TG_HOST, TG_SECRET, TG_GRAPH, XAI_API_KEY
      TG_SECRET is generated from the Savanna console → Database Secrets page.
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


def banner(msg):
    print(f"\n{'='*70}")
    print(f"  {msg}")
    print(f"{'='*70}")


# ---------------------------------------------------------------------------
# Step 1 – Validate credentials present
# ---------------------------------------------------------------------------
banner("Step 1 – Checking credentials")
missing = [v for v in ("TG_HOST", "TG_SECRET", "TG_GRAPH", "XAI_API_KEY")
           if not os.environ.get(v)]
if missing:
    print(f"[ERROR] Missing env vars: {', '.join(missing)}")
    print("  → Copy .env.example to .env and fill in all values, then re-run.")
    print("  → TG_SECRET comes from Savanna console → your graph → Database Secrets.")
    sys.exit(1)
print("  All required env vars present.")


# ---------------------------------------------------------------------------
# Step 2-4 – Apply schema, loading job, queries via pyTigerGraph GSQL
# ---------------------------------------------------------------------------
import pyTigerGraph as tg

banner("Step 2-4 – Connecting to TigerGraph and applying GSQL files")

try:
    # Use pre-generated DB secret — do NOT call createSecret() here.
    conn = tg.TigerGraphConnection(host=TG_HOST, graphname=TG_GRAPH)
    conn.apiToken = conn.getToken(TG_SECRET)
    print(f"  Connected to {TG_HOST}  graph={TG_GRAPH}")
except Exception as e:
    print(f"  [AUTH ERROR] {e}")
    ERRORS.append(f"Auth: {e}")
    sys.exit(1)

for step_num, gsql_file in [(2, "gsql/01_schema.gsql"),
                             (3, "gsql/02_loading.gsql"),
                             (4, "gsql/03_queries.gsql")]:
    banner(f"Step {step_num} – Applying {gsql_file}")
    try:
        gsql_text = Path(gsql_file).read_text()
        result = conn.gsql(gsql_text)
        print(result)
    except Exception as e:
        msg = f"[ERROR applying {gsql_file}] {e}"
        print(f"  {msg}")
        ERRORS.append(msg)


# ---------------------------------------------------------------------------
# Step 3b – Trigger loading job over data/*.csv
# ---------------------------------------------------------------------------
banner("Step 3b – Triggering loading job")
DATA_FILES = {
    "customers"               : "data/customers.csv",
    "cards"                   : "data/cards.csv",
    "devices"                 : "data/devices.csv",
    "regions"                 : "data/regions.csv",
    "emails"                  : "data/emails.csv",
    "closed_cases"            : "data/closed_cases.csv",
    "edges_from_device"       : "data/edges_from_device.csv",
    "edges_closed_involves"   : "data/edges_closed_involves.csv",
    "edges_closed_on_card"    : "data/edges_closed_on_card.csv",
    "edges_closed_connected"  : "data/edges_closed_connected.csv",
}
try:
    for label, fpath in DATA_FILES.items():
        if not Path(fpath).exists():
            print(f"  [SKIP] {fpath} not found locally")
            continue
        print(f"  Uploading {fpath} …")
        result = conn.uploadFile(fpath, fileTag=label, jobName="load_fraud_graph")
        print(f"    → {result}")
except Exception as e:
    msg = f"[ERROR during loading job] {e}"
    print(f"  {msg}")
    ERRORS.append(msg)


# ---------------------------------------------------------------------------
# Step 5 – Verify with vertex counts
# ---------------------------------------------------------------------------
banner("Step 5 – Verifying connection (vertex counts)")
try:
    counts = conn.getVertexCount("*")
    for vtype, cnt in counts.items():
        print(f"  {vtype:30s}  {cnt:>8,}")
    if all(v == 0 for v in counts.values()):
        print("  [WARN] All vertex counts are zero — loading job may not have run yet.")
except Exception as e:
    msg = f"[ERROR getting vertex counts] {e}"
    print(f"  {msg}")
    ERRORS.append(msg)


# ---------------------------------------------------------------------------
# Step 6 – Re-run investigate.py
# ---------------------------------------------------------------------------
banner("Step 6 – Running agent.investigate (live store)")
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
# Step 7 – Diff new cases vs stub-generated cases
# ---------------------------------------------------------------------------
banner("Step 7 – Diffing live vs stub cases")
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
    for field in ("verdict", "pattern", "fraud_probability"):
        if lc.get(field) != sc.get(field):
            diffs.append(f"{field}: stub={sc.get(field)!r} → live={lc.get(field)!r}")
    if set(a["action"] for a in lc.get("evidence", [])) != set(a.get("action","") for a in sc.get("evidence", [])):
        diffs.append("evidence trail changed")
    if diffs:
        print(f"  {cid}: CHANGED → " + "; ".join(diffs))
        CHANGED.append({"case_id": cid, "diffs": diffs})
    else:
        print(f"  {cid}: unchanged")

if not CHANGED:
    print("\n  No material changes between stub and live outputs.")
else:
    print(f"\n  {len(CHANGED)} case(s) changed materially.")


# ---------------------------------------------------------------------------
# Step 8 – Write-back all 20 cases
# ---------------------------------------------------------------------------
banner("Step 8 – Writing all cases back to live graph (agent.write_back)")
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
            print(f"  {cid} → written  (graph_case_id={answer['case']['graph_case_id']})")
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
# Step 9 – Validate
# ---------------------------------------------------------------------------
banner("Step 9 – Running validate_cases.py")
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
print(f"  Cases changed (stub → live): {len(CHANGED)}")
for c in CHANGED:
    print(f"    {c['case_id']}: {'; '.join(c['diffs'])}")
print(f"\n  Cases written to graph: {written}/20")
print(f"\n  Errors encountered ({len(ERRORS)}):")
for e in ERRORS:
    print(f"    ✗ {e}")
if not ERRORS:
    print("    None — clean run!")
