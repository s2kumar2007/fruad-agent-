"""
write_back.py — Writes every resolved case in cases/*.json into TigerGraph as
an AgentCase vertex + edges, via the write_agent_case GSQL query
(gsql/03_queries.gsql). This is the "also written back to the graph"
requirement from the submission spec.

Usage (once TigerGraph Savanna is provisioned):
    export TG_HOST="https://<your-instance>.i.tgcloud.io"
    export TG_USERNAME="..."
    export TG_PASSWORD="..."
    export TG_GRAPH="FraudGraph"
    pip install pyTigerGraph --break-system-packages
    python3 -m agent.write_back

This does NOT run against a live instance from this sandbox (no network
egress to TigerGraph Cloud here) -- it's structured and ready to run as-is
once real credentials are exported.
"""
import glob
import json
import os


def get_connection():
    import pyTigerGraph as tg
    from dotenv import load_dotenv
    load_dotenv()
    host = os.environ.get("TG_HOST")
    username = os.environ.get("TG_USERNAME", "tigergraph")
    password = os.environ.get("TG_PASSWORD", "tigergraph")
    graph = os.environ.get("TG_GRAPH", "FraudGraph")
    conn = tg.TigerGraphConnection(host=host, username=username, password=password, graphname=graph)
    conn.getToken()
    return conn


def write_case(conn, case_id, answer):
    c = answer["case"]
    txn_ids = set(c["affected_txn_ids"])
    connected_cards = set(c["connected_card_ids"])
    similar_cases = set(c["similar_prior_cases"])
    card_id = None
    # card_id isn't stored directly in the answer file's case object (only in
    # case_pack.csv); the runner should pass it in, but we can recover it
    # from the first affected/first_suspicious txn's card via a lookup query
    # if needed. Left as a parameter here for clarity in production use.
    params = {
        "p_graph_case_id": c["graph_case_id"],
        "p_hhg_case_id": case_id,
        "p_status": c["status"],
        "p_verdict": c["verdict"],
        "p_prob": c["fraud_probability"],
        "p_pattern": c["pattern"],
        "p_exposure": c["exposure_usd"],
        "p_summary": c["summary"],
        "p_card_id": card_id or "",
        "p_txn_ids": list(txn_ids),
        "p_connected_card_ids": list(connected_cards),
        "p_similar_case_ids": list(similar_cases),
    }
    conn.runInstalledQuery("write_agent_case", params=params)


def run_all(case_pack_lookup):
    conn = get_connection()
    for path in sorted(glob.glob("cases/*.json")):
        answer = json.load(open(path))
        case_id = answer["case_id"]
        row = case_pack_lookup.get(case_id, {})
        answer["case"]["_card_id_for_writeback"] = row.get("card_id", "")
        write_case_with_card(conn, case_id, answer, row.get("card_id", ""))
        print(f"wrote {case_id} -> {answer['case']['graph_case_id']}")


def write_case_with_card(conn, case_id, answer, card_id):
    c = answer["case"]
    params = {
        "p_graph_case_id": c["graph_case_id"],
        "p_hhg_case_id": case_id,
        "p_status": c["status"],
        "p_verdict": c["verdict"],
        "p_prob": c["fraud_probability"],
        "p_pattern": c["pattern"],
        "p_exposure": c["exposure_usd"],
        "p_summary": c["summary"],
        "p_card_id": card_id,
        "p_txn_ids": c["affected_txn_ids"],
        "p_connected_card_ids": c["connected_card_ids"],
        "p_similar_case_ids": c["similar_prior_cases"],
    }
    conn.runInstalledQuery("write_agent_case", params=params)


if __name__ == "__main__":
    from agent.investigate import load_case_pack
    lookup = {row["case_id"]: row for row in load_case_pack()}
    run_all(lookup)
