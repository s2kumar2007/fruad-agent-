import os
import requests
import pyTigerGraph as tg
from dotenv import load_dotenv

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

    def get_txn(self, txn_id):
        if not self.conn: return None
        try:
            res = self.conn.getVerticesById("Transaction", txn_id)
            if res:
                attrs = res[0].get("attributes", {})
                attrs["TransactionID"] = txn_id
                return attrs
            return None
        except Exception:
            return None

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
        try:
            res = self.conn.runInstalledQuery("card_window", {"p_card_id": card_id, "p_txn_id": anchor_txn_id, "hours": hours})
            if res and len(res) > 0 and "Window" in res[0]:
                txns = []
                for t in res[0]["Window"]:
                    attrs = t["attributes"]
                    attrs["TransactionID"] = t["v_id"]
                    txns.append(attrs)
                return txns
            return []
        except Exception:
            return []

    def device_neighbors(self, txn_id):
        if not self.conn: return {"device_key": None, "device_desc": None, "other_txns": [], "other_cards": set()}
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
                            attrs = t["attributes"]
                            attrs["TransactionID"] = t["v_id"]
                            other_txns.append(attrs)
                    if "OtherCards" in d:
                        for c in d["OtherCards"]:
                            other_cards.add(c["v_id"])
                return {"device_key": dk, "device_desc": desc, "other_txns": other_txns, "other_cards": other_cards}
            return {"device_key": None, "device_desc": None, "other_txns": [], "other_cards": set()}
        except Exception:
            return {"device_key": None, "device_desc": None, "other_txns": [], "other_cards": set()}

    def region_cluster(self, txn_id, window_hours=48):
        if not self.conn: return {"region": None, "other_txns": [], "other_cards": set()}
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
                            attrs = t["attributes"]
                            attrs["TransactionID"] = t["v_id"]
                            other_txns.append(attrs)
                    if "OtherCards" in d:
                        for c in d["OtherCards"]:
                            other_cards.add(c["v_id"])
                return {"region": region, "other_txns": other_txns, "other_cards": other_cards}
            return {"region": None, "other_txns": [], "other_cards": set()}
        except Exception:
            return {"region": None, "other_txns": [], "other_cards": set()}

    def card_history(self, card_id):
        if not self.conn: return {"txns": [], "past_cases": []}
        try:
            res = self.conn.runInstalledQuery("card_history", {"p_card_id": card_id})
            txns = []
            past_cases = []
            if res:
                for d in res:
                    if "AllTxns" in d:
                        for t in d["AllTxns"]:
                            attrs = t["attributes"]
                            attrs["TransactionID"] = t["v_id"]
                            txns.append(attrs)
                    if "PastCases" in d:
                        for c in d["PastCases"]:
                            attrs = c["attributes"]
                            attrs["case_id"] = c["v_id"]
                            past_cases.append(attrs)
            return {"txns": txns, "past_cases": past_cases}
        except Exception:
            return {"txns": [], "past_cases": []}

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
                                attrs = t["attributes"]
                                attrs["TransactionID"] = t["v_id"]
                                out.append(attrs)
            return out
        except Exception:
            return []
