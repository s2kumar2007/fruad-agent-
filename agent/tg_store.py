import os
import csv
import requests
import pyTigerGraph as tg
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

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

class IdentityDictProxy:
    def __init__(self, store):
        self.store = store
        self._local_cache = None

    def _ensure_local(self):
        if self._local_cache is None:
            self._local_cache = {}
            import csv
            from pathlib import Path
            id_path = Path("data/identity.csv")
            if id_path.exists():
                try:
                    with open(id_path, newline="") as f:
                        for row in csv.DictReader(f):
                            self._local_cache[row["TransactionID"]] = row
                except Exception:
                    pass

    def get(self, txn_id):
        # 1. Try graph vertex/edge lookup first
        if self.store and self.store.conn:
            try:
                edges = self.store.conn.getEdges("Transaction", txn_id, "FROM_DEVICE", "DeviceProfile")
                if edges:
                    dev_id = edges[0].get("to_id")
                    devs = self.store.conn.getVerticesById("DeviceProfile", dev_id)
                    if devs:
                        attrs = devs[0].get("attributes", {})
                        flag = attrs.get("is_new_flag", "")
                        if flag:
                            return {"id_15": flag, "DeviceInfo": attrs.get("device_info", "")}
            except Exception:
                pass
        # 2. Fix B: fallback to local identity.csv dataset if not linked in graph
        self._ensure_local()
        return self._local_cache.get(txn_id)


class TigerGraphMCPStore:
    def __init__(self, needed_customers=None):
        self.host   = os.environ.get("TG_HOST")
        self.secret = os.environ.get("TG_SECRET")
        self.graph  = os.environ.get("TG_GRAPH", "FraudGraph")

        if self.host and self.secret:
            try:
                token = get_tg_token(self.host, self.secret, self.graph)
                self.conn = tg.TigerGraphConnection(
                    host=self.host,
                    graphname=self.graph,
                    apiToken=token,
                )
                self.conn.apiToken = token
            except Exception as e:
                print(f"TigerGraph connection error: {e}")
                self.conn = None
        else:
            self.conn = None
        
        self.identity_by_txn = IdentityDictProxy(self)
        self._txn_cache = {}
        self._local_card_txn_ids = None
        self._local_graph_txns = None

    def _normalize_txn(self, v_id, attrs):
        mapping = {
            "amount": "TransactionAmt",
            "dt_seconds": "TransactionDT",
            "p_email": "P_emaildomain",
            "r_email": "R_emaildomain",
            "product_cd": "ProductCD"
        }
        for k, v in list(attrs.items()):
            if k in mapping:
                attrs[mapping[k]] = v
        attrs["TransactionID"] = v_id
        return attrs

    def get_txn(self, txn_id):
        if not self.conn: return None
        if txn_id in self._txn_cache:
            return self._txn_cache[txn_id]
        try:
            res = self.conn.getVerticesById("Transaction", txn_id)
            if res:
                norm = self._normalize_txn(txn_id, res[0].get("attributes", {}))
                self._txn_cache[txn_id] = norm
                return norm
            return None
        except Exception:
            return None

    def get_txns_batch(self, txn_ids):
        if not self.conn or not txn_ids: return []
        to_fetch = [t for t in txn_ids if t not in self._txn_cache]
        if to_fetch:
            chunk_size = 200
            for i in range(0, len(to_fetch), chunk_size):
                chunk = to_fetch[i:i + chunk_size]
                try:
                    res = self.conn.getVerticesById("Transaction", chunk)
                    for item in res:
                        v_id = item.get("v_id")
                        norm = self._normalize_txn(v_id, item.get("attributes", {}))
                        self._txn_cache[v_id] = norm
                except Exception:
                    pass
        return [self._txn_cache[t] for t in txn_ids if t in self._txn_cache]

    def _ensure_local_card_history_cache(self):
        if self._local_card_txn_ids is not None and self._local_graph_txns is not None:
            return
        from collections import defaultdict

        self._local_card_txn_ids = defaultdict(list)
        self._local_graph_txns = {}

        data_dir = Path("data")
        made_path = data_dir / "edges_made.csv"
        txns_path = data_dir / "graph_transactions.csv"
        if not made_path.exists() or not txns_path.exists():
            return

        try:
            with open(made_path, newline="") as f:
                for row in csv.DictReader(f):
                    card_id = row.get("card_id")
                    txn_id = row.get("txn_id")
                    if card_id and txn_id:
                        self._local_card_txn_ids[card_id].append(txn_id)

            with open(txns_path, newline="") as f:
                for row in csv.DictReader(f):
                    txn_id = row.get("txn_id")
                    if not txn_id:
                        continue
                    self._local_graph_txns[txn_id] = self._normalize_txn(txn_id, {
                        "amount": row.get("amount", ""),
                        "ts": row.get("ts", ""),
                        "dt_seconds": row.get("dt_seconds", ""),
                        "product_cd": row.get("product_cd", ""),
                        "channel": row.get("channel", ""),
                        "addr1": row.get("addr1", ""),
                        "addr2": row.get("addr2", ""),
                        "risk_score": row.get("risk_score", ""),
                        "p_email": row.get("p_email", ""),
                        "r_email": row.get("r_email", ""),
                    })
        except Exception:
            self._local_card_txn_ids = defaultdict(list)
            self._local_graph_txns = {}

    def _local_card_history(self, card_id):
        self._ensure_local_card_history_cache()
        tids = self._local_card_txn_ids.get(card_id, []) if self._local_card_txn_ids else []
        txns = [self._local_graph_txns[t] for t in tids if t in self._local_graph_txns]
        txns.sort(key=lambda t: t.get("ts", ""))
        for t in txns:
            self._txn_cache[t["TransactionID"]] = t
        return txns

    def card_id_for(self, customer_id, txn_id):
        if not self.conn: return None
        try:
            edges = self.conn.getEdges("Transaction", txn_id, "reverse_MADE", "Card")
            if edges:
                return edges[0].get("to_id")
            return None
        except Exception:
            return None

    def card_window(self, card_id, anchor_txn_id, hours=2):
        if not self.conn: return []
        # Try installed query first; fall back to edge traversal on 404
        try:
            res = self.conn.runInstalledQuery("card_window", {"p_card_id": card_id, "p_txn_id": anchor_txn_id, "hours": hours})
            if res and len(res) > 0 and "Window" in res[0]:
                txns = [self._normalize_txn(t["v_id"], t["attributes"]) for t in res[0]["Window"]]
                for t in txns:
                    self._txn_cache[t["TransactionID"]] = t
                return txns
        except Exception:
            pass
        # Fallback: get all card transactions via MADE edge, filter in Python
        try:
            from datetime import datetime
            anchor = self.get_txn(anchor_txn_id)
            if not anchor or not anchor.get("ts"):
                return []
            anchor_ts = datetime.strptime(anchor["ts"], "%Y-%m-%d %H:%M:%S")
            edges = self.conn.getEdges("Card", card_id, "MADE", "Transaction")
            tids = [e["to_id"] for e in edges if e.get("to_id")]
            all_txns = self.get_txns_batch(tids)
            out = []
            for txn in all_txns:
                if not txn.get("ts"):
                    continue
                t_ts = datetime.strptime(txn["ts"], "%Y-%m-%d %H:%M:%S")
                if abs((t_ts - anchor_ts).total_seconds()) <= hours * 3600:
                    out.append(txn)
            return sorted(out, key=lambda x: x.get("ts", ""))
        except Exception:
            return []




    def device_neighbors(self, txn_id):
        empty = {"device_key": None, "device_desc": None, "other_txns": [], "other_cards": set()}
        if not self.conn: return empty
        # Try installed query first
        try:
            res = self.conn.runInstalledQuery("device_neighbors", {"p_txn_id": txn_id})
            if res and len(res) > 0:
                dev = res[0].get("Dev", [])
                dk = dev[0]["v_id"] if dev else None
                desc = None
                other_txns = []
                other_cards = set()
                if dev:
                    attrs = dev[0]["attributes"]
                    desc = f"{attrs.get('DeviceInfo','?')} | {attrs.get('id_30','?')} | {attrs.get('id_31','?')} | {attrs.get('id_33','?')}"
                for d in res:
                    if "OtherTxns" in d:
                        for t in d["OtherTxns"]:
                            other_txns.append(self._normalize_txn(t["v_id"], t["attributes"]))
                    if "OtherCards" in d:
                        for c in d["OtherCards"]:
                            other_cards.add(c["v_id"])
                return {"device_key": dk, "device_desc": desc, "other_txns": other_txns, "other_cards": other_cards}
        except Exception:
            pass
        # Fallback: traverse FROM_DEVICE edges directly
        try:
            dev_edges = self.conn.getEdges("Transaction", txn_id, "FROM_DEVICE", "DeviceProfile")
            if not dev_edges:
                return empty
            dev_id = dev_edges[0].get("to_id")
            if not dev_id:
                return empty
            devs = self.conn.getVerticesById("DeviceProfile", dev_id)
            desc = None
            if devs:
                a = devs[0].get("attributes", {})
                desc = f"{a.get('DeviceInfo','?')} | {a.get('id_30','?')} | {a.get('id_31','?')} | {a.get('id_33','?')}"
            # Find all other transactions using this device
            dev_txn_edges = self.conn.getEdges("DeviceProfile", dev_id, "reverse_FROM_DEVICE", "Transaction")
            other_txns = []
            other_cards = set()
            # Determine anchor card via MADE edge
            anchor_card_edges = self.conn.getEdges("Transaction", txn_id, "reverse_MADE", "Card")
            anchor_card = anchor_card_edges[0].get("to_id") if anchor_card_edges else None
            for e in dev_txn_edges:
                other_tid = e.get("to_id")
                if not other_tid or other_tid == txn_id:
                    continue
                t = self.get_txn(other_tid)
                if not t:
                    continue
                other_txns.append(t)
                card_edges = self.conn.getEdges("Transaction", other_tid, "reverse_MADE", "Card")
                for ce in card_edges:
                    cid = ce.get("to_id")
                    if cid and cid != anchor_card:
                        other_cards.add(cid)
            return {"device_key": dev_id, "device_desc": desc, "other_txns": other_txns, "other_cards": other_cards}
        except Exception:
            return empty



    def region_cluster(self, txn_id, window_hours=48):
        empty = {"region": None, "other_txns": [], "other_cards": set()}
        if not self.conn: return empty
        # Try installed query first
        try:
            res = self.conn.runInstalledQuery("region_cluster", {"p_txn_id": txn_id, "window_hours": window_hours})
            if res and len(res) > 0:
                region = None
                other_txns = []
                other_cards = set()
                for d in res:
                    if "Region" in d and d["Region"]:
                        region = d["Region"][0]["v_id"]
                    if "OtherTxns" in d:
                        for t in d["OtherTxns"]:
                            other_txns.append(self._normalize_txn(t["v_id"], t["attributes"]))
                    if "OtherCards" in d:
                        for c in d["OtherCards"]:
                            other_cards.add(c["v_id"])
                if region:
                    return {"region": region, "other_txns": other_txns, "other_cards": other_cards}
        except Exception:
            pass
        # Fallback: traverse BILLED_IN edges directly
        try:
            from datetime import datetime
            anchor = self.get_txn(txn_id)
            if not anchor or not anchor.get("addr1"):
                return empty
            anchor_ts = datetime.strptime(anchor["ts"], "%Y-%m-%d %H:%M:%S")
            region = anchor.get("addr1")
            # Get all transactions billed in the same region via reverse edge
            bil_edges = self.conn.getEdges("Transaction", txn_id, "BILLED_IN", "BillingRegion")
            if not bil_edges:
                return empty
            region_id = bil_edges[0].get("to_id")
            if not region_id:
                return empty
            # Find other transactions in this region
            rev_edges = self.conn.getEdges("BillingRegion", region_id, "reverse_BILLED_IN", "Transaction")
            other_txns = []
            other_cards = set()
            for e in rev_edges:
                other_tid = e.get("to_id")
                if not other_tid or other_tid == txn_id:
                    continue
                t = self.get_txn(other_tid)
                if not t or not t.get("ts"):
                    continue
                t_ts = datetime.strptime(t["ts"], "%Y-%m-%d %H:%M:%S")
                if abs((t_ts - anchor_ts).total_seconds()) <= window_hours * 3600:
                    other_txns.append(t)
                    card_edges = self.conn.getEdges("Transaction", other_tid, "reverse_MADE", "Card")
                    for ce in card_edges:
                        cid = ce.get("to_id")
                        if cid:
                            other_cards.add(cid)
            return {"region": region_id, "other_txns": other_txns, "other_cards": other_cards}
        except Exception:
            return empty



    def card_history(self, card_id):
        if not self.conn: return {"txns": [], "past_cases": []}
        # ------------------------------------------------------------------
        # Fix A: installed query may not be deployed yet (404). Fall back to
        # raw edge traversal which always works.
        # ------------------------------------------------------------------
        txns = []
        past_cases = []
        try:
            res = self.conn.runInstalledQuery("card_history", {"p_card_id": card_id})
            if res:
                for d in res:
                    if "AllTxns" in d:
                        for t in d["AllTxns"]:
                            txns.append(self._normalize_txn(t["v_id"], t["attributes"]))
                    if "PastCases" in d:
                        for c in d["PastCases"]:
                            attrs = c["attributes"]
                            attrs["case_id"] = c["v_id"]
                            past_cases.append(attrs)
            local_txns = self._local_card_history(card_id)
            # Fix A: the installed query can be stale/misloaded and return only
            # the flagged anchor. Prefer the loader-derived full card timeline
            # whenever graph output is suspiciously thin.
            if local_txns and len(local_txns) > len(txns):
                txns = local_txns
            if txns:
                return {"txns": txns, "past_cases": past_cases}
            # Fall through to edge-traversal fallback if query returned nothing
        except Exception:
            pass
        # Fallback: traverse MADE edges directly from the Card vertex
        try:
            edges = self.conn.getEdges("Card", card_id, "MADE", "Transaction")
            tids = [e["to_id"] for e in edges if e.get("to_id")]
            txns = self.get_txns_batch(tids)
            txns.sort(key=lambda t: t.get("ts", ""))
        except Exception:
            pass
        local_txns = self._local_card_history(card_id)
        if local_txns and len(local_txns) > len(txns):
            txns = local_txns
        # Closed cases: traverse ON_CARD edges (ClosedCase -> Card)
        try:
            cc_edges = self.conn.getEdges("Card", card_id, "reverse_ON_CARD", "ClosedCase")
            for e in cc_edges:
                cc_id = e.get("to_id")
                if not cc_id:
                    continue
                verts = self.conn.getVerticesById("ClosedCase", cc_id)
                if verts:
                    attrs = verts[0].get("attributes", {})
                    attrs["case_id"] = cc_id
                    past_cases.append(attrs)
        except Exception:
            pass
        return {"txns": txns, "past_cases": past_cases}


    def similar_closed_cases(self, pattern_hint=None, device_key_=None, region=None, limit=5):
        if not self.conn: return []
        try:
            res = self.conn.runInstalledQuery("similar_closed_cases", {
                "p_pattern_hint": pattern_hint or "", 
                "p_device_key": device_key_ or "", 
                "p_region_code": region or ""
            })
            cases = []
            if res:
                seen = set()
                for d in res:
                    for key in ["ViaDevice", "ViaRegion", "ViaPattern"]:
                        if key in d:
                            for c in d[key]:
                                if c["v_id"] not in seen:
                                    seen.add(c["v_id"])
                                    attrs = c["attributes"]
                                    attrs["case_id"] = c["v_id"]
                                    cases.append(attrs)
            return cases[:limit]
        except Exception:
            return []

    def customer_baseline(self, customer_id, exclude_card=None):
        if not self.conn: return []
        try:
            edges = self.conn.getEdges("Customer", customer_id, "OWNS", "Card")
            out = []
            for e in edges:
                card_id = e.get("to_id")
                if card_id == exclude_card:
                    continue
                res = self.conn.runInstalledQuery("card_history", {"p_card_id": card_id})
                if res:
                    for d in res:
                        if "AllTxns" in d:
                            for t in d["AllTxns"]:
                                out.append(self._normalize_txn(t["v_id"], t["attributes"]))
            return out
        except Exception:
            return []

