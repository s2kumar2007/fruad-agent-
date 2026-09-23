"""
build_graph_csvs.py — Produces the flat CSVs that gsql/02_loading.gsql expects,
from the raw HHGOA_IEEE files. Run this once, then `gsql gsql/01_schema.gsql`,
then load via TigerGraph's loader (Savanna UI, GraphStudio, or gsql
`RUN LOADING JOB load_fraud_graph USING f_customers="data/customers.csv", ...`
pointing $sys.data_root at this output directory).
"""
import csv
from pathlib import Path
from agent.data_store import LocalGraphStore, device_key

OUT = Path("/home/claude/fraud-agent/data")
OUT.mkdir(exist_ok=True, parents=True)


def run():
    gs = LocalGraphStore(needed_customers=None)
    gs.load()

    with open(OUT / "customers.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["customer_id"])
        for cid in gs.card_of_customer:
            w.writerow([cid])

    with open(OUT / "cards.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["card_id", "card_network", "card_type"])
        for card_id, cid in gs.customer_of_card.items():
            sample_tid = gs.txns_by_card[card_id][0]
            t = gs.txn_by_id[sample_tid]
            w.writerow([card_id, t.get("card4", ""), t.get("card6", "")])

    with open(OUT / "transactions.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["txn_id", "amount", "ts", "dt_seconds", "product_cd", "channel",
                     "addr1", "addr2", "risk_score", "p_email", "r_email"])
        for tid, t in gs.txn_by_id.items():
            w.writerow([tid, t["TransactionAmt"], t["ts"], t["TransactionDT"], t["ProductCD"],
                        t["channel"], t["addr1"], t["addr2"], t["risk_score"],
                        t["P_emaildomain"], t["R_emaildomain"]])

    with open(OUT / "devices.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["device_key", "device_info", "os", "browser", "screen", "is_new_flag"])
        seen = set()
        for tid, dk in gs.device_by_txn.items():
            if dk in seen:
                continue
            seen.add(dk)
            idrow = gs.identity_by_txn[tid]
            w.writerow([dk, idrow.get("DeviceInfo", ""), idrow.get("id_30", ""),
                        idrow.get("id_31", ""), idrow.get("id_33", ""), idrow.get("id_15", "")])

    with open(OUT / "regions.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["region_code"])
        for r in gs.txns_by_region:
            w.writerow([r])

    domains = set()
    for t in gs.txn_by_id.values():
        if t["P_emaildomain"]:
            domains.add(t["P_emaildomain"])
        if t["R_emaildomain"]:
            domains.add(t["R_emaildomain"])
    with open(OUT / "emails.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["domain"])
        for d in domains:
            w.writerow([d])

    with open(OUT / "closed_cases.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "outcome", "pattern", "exposure_usd", "opened_at", "closed_at", "analyst_notes"])
        for c in gs.closed_cases:
            w.writerow([c["case_id"], c["outcome"], c["pattern"], c["exposure_usd"],
                        c["opened_at"], c["closed_at"], c.get("analyst_notes", "").replace("\n", " ")])

    with open(OUT / "edges_made.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["card_id", "txn_id"])
        for card_id, tids in gs.txns_by_card.items():
            for tid in tids:
                w.writerow([card_id, tid])

    with open(OUT / "edges_from_device.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["txn_id", "device_key"])
        for tid, dk in gs.device_by_txn.items():
            w.writerow([tid, dk])

    with open(OUT / "edges_billed_in.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["txn_id", "region_code"])
        for r, tids in gs.txns_by_region.items():
            for tid in tids:
                w.writerow([tid, r])

    with open(OUT / "edges_purchaser_email.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["txn_id", "domain"])
        for tid, t in gs.txn_by_id.items():
            if t["P_emaildomain"]:
                w.writerow([tid, t["P_emaildomain"]])

    with open(OUT / "edges_next.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["txn_id_a", "txn_id_b"])
        for card_id, tids in gs.txns_by_card.items():
            for a, b in zip(tids, tids[1:]):
                w.writerow([a, b])

    with open(OUT / "edges_closed_involves.csv", "w", newline="") as f, \
         open(OUT / "edges_closed_on_card.csv", "w", newline="") as f2, \
         open(OUT / "edges_closed_connected.csv", "w", newline="") as f3:
        w = csv.writer(f); w.writerow(["case_id", "txn_id"])
        w2 = csv.writer(f2); w2.writerow(["case_id", "card_id"])
        w3 = csv.writer(f3); w3.writerow(["case_id", "card_id"])
        for c in gs.closed_cases:
            for tid in (c.get("txn_ids") or "").split("|"):
                if tid:
                    w.writerow([c["case_id"], tid])
            w2.writerow([c["case_id"], c["card_id"]])
            for cc in (c.get("connected_card_ids") or "").split("|"):
                if cc:
                    w3.writerow([c["case_id"], cc])

    print(f"Wrote loader CSVs to {OUT}")


if __name__ == "__main__":
    run()
