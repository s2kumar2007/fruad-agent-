"""
data_store.py — Local mirror of the TigerGraph graph, built from the raw CSVs.

This module is deliberately structured so every public method here has a 1:1
GSQL counterpart in gsql/03_queries.gsql (card_window, device_neighbors,
region_cluster, card_history, similar_closed_cases, write_agent_case).
When TigerGraph MCP is wired in, swap `LocalGraphStore` for a
`TigerGraphMCPStore` that calls the same query names remotely — the agent
core (agent/graph_state.py) should not need to change.
"""
import csv
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATA_DIR = Path(os.environ.get("HHGOA_DATA_DIR", str(DEFAULT_DATA_DIR)))

# Columns we actually need from the 397-column transactions.csv.
# (V1-V339, most C/D/M columns are available but not pulled into memory by
# default — see `load_full_row` for on-demand access to any column.)
CORE_COLS = [
    "TransactionID", "TransactionDT", "TransactionAmt", "ProductCD",
    "card1", "card2", "card3", "card4", "card5", "card6",
    "addr1", "addr2", "dist1", "dist2", "P_emaildomain", "R_emaildomain",
    "customer_id", "ts", "channel", "risk_score",
]
# A handful of named match/count signals worth keeping cheaply
SIGNAL_COLS = ["C1", "C2", "C13", "C14", "D1", "D2", "D15", "M1", "M4", "M6"]


GENERIC_DEVICE_INFO = {"", "windows", "macos", "ios device", "linux"}
GENERIC_DEVICE_PREFIXES = ("rv:", "trident/")  # browser rendering-engine tokens, not device models


def device_key(row):
    """
    Stable key identifying a device profile, matching the GSQL DeviceProfile PK.

    A device fingerprint built only from DeviceInfo|OS|browser|screen collides
    heavily on generic desktop configs (e.g. plain "Windows" + Chrome +
    1920x1080 is shared by hundreds of unrelated cards in this dataset) --
    that's a fingerprint, not a shared device. We only call two transactions
    "same device" when either (a) DeviceInfo names a specific make/model
    (contains a model-like token, not just an OS name), or (b) the full
    extended fingerprint -- including network/browser-fingerprint fields
    id_13, id_17, id_19, id_20, id_21, id_02 -- matches exactly. Generic
    desktop signatures alone are treated as non-discriminative and excluded.
    """
    device_info = (row.get("DeviceInfo") or "").strip()
    di_lower = device_info.lower()
    specific_model = (
        device_info != ""
        and di_lower not in GENERIC_DEVICE_INFO
        and not di_lower.startswith(GENERIC_DEVICE_PREFIXES)
    )

    fields = ["DeviceInfo", "id_30", "id_31", "id_33", "id_13", "id_17", "id_19", "id_20", "id_21", "id_02"]
    parts = [(row.get(f) or "").strip() for f in fields]
    raw = "|".join(parts)
    if not raw.strip("|"):
        return ""
    if not specific_model:
        # generic desktop OS with no distinguishing model string: only trust an
        # exact match on the FULL extended fingerprint (all network fields present)
        if any(p == "" for p in parts[4:]):
            return ""  # not enough signal to safely call this "shared"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


class LocalGraphStore:
    def __init__(self, needed_customers=None):
        """
        needed_customers: optional set of customer_ids to restrict the full scan to
        (plus whatever's needed for device/region cross-links). None = load all.
        """
        self.txn_by_id = {}                 # txn_id -> dict of core+signal cols
        self.txns_by_card = defaultdict(list)   # card_id -> [txn_id ...] sorted by ts
        self.card_of_customer = defaultdict(dict)  # customer_id -> {combo_key: card_id}
        self.customer_of_card = {}
        self.device_by_txn = {}             # txn_id -> device_key
        self.txns_by_device = defaultdict(list)
        self.txns_by_region = defaultdict(list)  # addr1 -> [txn_id]
        self.identity_by_txn = {}           # txn_id -> raw identity row (DeviceInfo, id_30, id_31, id_33, id_15, id_23 ...)
        self.closed_cases = []              # list of dict rows
        self.closed_cases_by_id = {}
        self.needed_customers = needed_customers
        self._loaded = False

    # ---------- Loading ----------

    def load(self, verbose=True):
        self._load_transactions(verbose)
        self._load_identity(verbose)
        self._load_closed_cases(verbose)
        self._assign_card_ids()
        self._index_devices_regions()
        self._loaded = True

    def _load_transactions(self, verbose):
        path = DATA_DIR / "transactions.csv"
        if not path.exists():
            raise FileNotFoundError(f"[ERROR] Required file not found: {path}")
        n = 0
        with open(path, newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                cid = row["customer_id"]
                if self.needed_customers is not None and cid not in self.needed_customers:
                    continue
                tid = row["TransactionID"]
                rec = {c: row.get(c, "") for c in CORE_COLS}
                for c in SIGNAL_COLS:
                    rec[c] = row.get(c, "")
                self.txn_by_id[tid] = rec
                n += 1
        if verbose:
            print(f"[data_store] loaded {n} transactions for {len(self.needed_customers) if self.needed_customers else 'ALL'} customers")

    def _load_identity(self, verbose):
        path = DATA_DIR / "identity.csv"
        if not path.exists():
            print(f"  [SKIP] {path} not found – proceeding without identity data.")
            return
        n = 0
        with open(path, newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                tid = row["TransactionID"]
                if tid not in self.txn_by_id:
                    continue
                self.identity_by_txn[tid] = row
                n += 1
        if verbose:
            print(f"[data_store] loaded {n} matching identity records")

    def _load_closed_cases(self, verbose):
        path = DATA_DIR / "closed_cases_history.csv"
        if not path.exists():
            print(f"  [SKIP] {path} not found – proceeding without closed case data.")
            return
        with open(path, newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                self.closed_cases.append(row)
                self.closed_cases_by_id[row["case_id"]] = row
        if verbose:
            print(f"[data_store] loaded {len(self.closed_cases)} closed cases")

    def _assign_card_ids(self):
        """card_id = customer_id + '-K' + rank of distinct (card1..card6) combo by first ts."""
        combos = defaultdict(list)  # customer_id -> [(ts, combo_key, txn_id)]
        for tid, rec in self.txn_by_id.items():
            combo = "|".join(rec[c] for c in ["card1", "card2", "card3", "card4", "card5", "card6"])
            combos[rec["customer_id"]].append((rec["ts"], combo, tid))
        for cid, items in combos.items():
            items.sort(key=lambda x: x[0])
            seen = {}
            rank = 0
            for ts, combo, tid in items:
                if combo not in seen:
                    rank += 1
                    seen[combo] = f"{cid}-K{rank}"
                card_id = seen[combo]
                self.txn_by_id[tid]["card_id"] = card_id
                self.customer_of_card[card_id] = cid
            self.card_of_customer[cid] = seen
        for tid, rec in self.txn_by_id.items():
            self.txns_by_card[rec["card_id"]].append(tid)
        for card_id, tids in self.txns_by_card.items():
            tids.sort(key=lambda t: self.txn_by_id[t]["ts"])

    def _index_devices_regions(self):
        for tid, rec in self.txn_by_id.items():
            idrow = self.identity_by_txn.get(tid)
            if idrow:
                dk = device_key(idrow)
                if dk:
                    self.device_by_txn[tid] = dk
                    self.txns_by_device[dk].append(tid)
            addr1 = rec.get("addr1", "")
            if addr1:
                self.txns_by_region[addr1].append(tid)

    # ---------- Query API (mirrors GSQL queries) ----------

    def get_txn(self, txn_id):
        return self.txn_by_id.get(txn_id)

    def card_id_for(self, customer_id, txn_id):
        return self.txn_by_id.get(txn_id, {}).get("card_id")

    def card_window(self, card_id, anchor_txn_id, hours=2):
        """Mirrors GSQL card_window(): all txns on this card within N hours of the anchor."""
        anchor = self.txn_by_id.get(anchor_txn_id)
        if not anchor:
            return []
        anchor_ts = datetime.strptime(anchor["ts"], "%Y-%m-%d %H:%M:%S")
        out = []
        for tid in self.txns_by_card.get(card_id, []):
            t = self.txn_by_id[tid]
            ts = datetime.strptime(t["ts"], "%Y-%m-%d %H:%M:%S")
            if abs((ts - anchor_ts).total_seconds()) <= hours * 3600:
                out.append(t)
        return sorted(out, key=lambda x: x["ts"])

    def device_neighbors(self, txn_id):
        """
        Mirrors GSQL device_neighbors(): device profile + other CARDS sharing it.
        Excludes the anchor transaction's own card entirely -- a cardholder's
        own device naturally appears on their own other purchases, and that is
        not a "connected card"; only a genuinely different card_id counts.
        """
        dk = self.device_by_txn.get(txn_id)
        anchor_card = self.txn_by_id.get(txn_id, {}).get("card_id")
        if not dk:
            return {"device_key": None, "device_desc": None, "other_txns": [], "other_cards": set()}
        idrow = self.identity_by_txn[txn_id]
        desc = f"{idrow.get('DeviceInfo','?')} | {idrow.get('id_30','?')} | {idrow.get('id_31','?')} | {idrow.get('id_33','?')}"
        other_txns = [self.txn_by_id[t] for t in self.txns_by_device[dk]
                      if t != txn_id and self.txn_by_id[t]["card_id"] != anchor_card]
        other_cards = {t["card_id"] for t in other_txns}
        return {"device_key": dk, "device_desc": desc, "other_txns": other_txns, "other_cards": other_cards}

    def region_cluster(self, txn_id, window_hours=48):
        """Mirrors GSQL region_cluster(): other cards billed in the same region within a window."""
        t0 = self.txn_by_id.get(txn_id)
        if not t0 or not t0.get("addr1"):
            return {"region": None, "other_txns": [], "other_cards": set()}
        anchor_ts = datetime.strptime(t0["ts"], "%Y-%m-%d %H:%M:%S")
        region = t0["addr1"]
        others = []
        for tid in self.txns_by_region.get(region, []):
            if tid == txn_id:
                continue
            t = self.txn_by_id[tid]
            ts = datetime.strptime(t["ts"], "%Y-%m-%d %H:%M:%S")
            if abs((ts - anchor_ts).total_seconds()) <= window_hours * 3600:
                others.append(t)
        other_cards = {t["card_id"] for t in others}
        return {"region": region, "other_txns": others, "other_cards": other_cards}

    def card_history(self, card_id):
        """Mirrors GSQL card_history(): full txn timeline + past closed cases on this card."""
        tids = self.txns_by_card.get(card_id, [])
        txns = [self.txn_by_id[t] for t in tids]
        past_cases = [c for c in self.closed_cases if c["card_id"] == card_id]
        return {"txns": txns, "past_cases": past_cases}

    def similar_closed_cases(self, pattern_hint=None, device_key_=None, region=None, limit=5):
        """Mirrors GSQL similar_closed_cases(): retrieval for case memory."""
        scored = []
        for c in self.closed_cases:
            score = 0
            if pattern_hint and c["pattern"] == pattern_hint:
                score += 2
            if region and self._case_touches_region(c, region):
                score += 1
            if device_key_ and self._case_touches_device(c, device_key_):
                score += 3
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: -x[0])
        return [c for _, c in scored[:limit]]

    def _case_touches_region(self, closed_case, region):
        for tid in (closed_case.get("txn_ids") or "").split("|"):
            t = self.txn_by_id.get(tid)
            if t and t.get("addr1") == region:
                return True
        return False

    def _case_touches_device(self, closed_case, dkey):
        for tid in (closed_case.get("txn_ids") or "").split("|"):
            if self.device_by_txn.get(tid) == dkey:
                return True
        return False

    def customer_baseline(self, customer_id, exclude_card=None):
        """All of a customer's other cards/txns — used to judge 'new region', 'new product', etc."""
        cards = self.card_of_customer.get(customer_id, {})
        out = []
        for combo, card_id in cards.items():
            if card_id == exclude_card:
                continue
            out.extend(self.txn_by_id[t] for t in self.txns_by_card[card_id])
        return out
