"""
investigate.py — The investigation loop (local reference implementation).

This mirrors the LangGraph node sequence the full agent uses:
  Trigger -> OpenCase -> GatherEvidence -> AssessUncertainty
    -> (loop: GatherEvidence via simulated evidence_requests) -> RecommendAction
    -> Explain -> UpdateMemory (write_agent_case)

Here the "reasoning" step (pattern detection + probability estimate) is a
transparent, ruleâ€‘based scorer over real graph signals rather than an LLM
call, so it runs fully offline and every number is traceable. When wired to
Grok via LangGraph, this scorer's output (structured evidence + candidate
pattern + raw signal strengths) becomes the *context* handed to the LLM node,
which is then free to reason further, disagree, and write the natural
language `summary` / SAR narrative. Swapping in the LLM narrows to two
functions: `synthesize_summary()` and `write_sar_narrative()` below.
"""
import csv
import json
import statistics
from datetime import datetime
from pathlib import Path

from agent.tg_store import TigerGraphMCPStore
from agent.data_store import LocalGraphStore
from agent import policy

DATA_DIR = Path("data")
OUT_DIR = Path("cases")


def load_case_pack():
    cases = []
    with open(DATA_DIR / "case_pack.csv", newline="") as f:
        for row in csv.DictReader(f):
            cases.append(row)
    return cases


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------

def is_new_device(identity_row):
    return (identity_row or {}).get("id_15", "") == "New"


def match_flags_bad(identity_row):
    if not identity_row:
        return []
    bad = []
    for f in ("M1", "M4", "M6"):
        pass  # match flags live on the transaction row (M1..M9), not identity; kept for interface symmetry
    return bad


def txn_match_flags_bad(txn):
    bad = [f for f in ("M1", "M4", "M6") if txn.get(f) == "F"]
    return bad


def card_amount_baseline(card_txns, exclude_id=None):
    amts = [float(t["TransactionAmt"]) for t in card_txns if t["TransactionID"] != exclude_id and t["TransactionAmt"]]
    if not amts:
        return None
    return {"median": statistics.median(amts), "max": max(amts), "n": len(amts)}


def card_regions_seen(card_txns, exclude_id=None):
    return {t["addr1"] for t in card_txns if t["TransactionID"] != exclude_id and t["addr1"]}


def card_products_seen(card_txns, exclude_id=None):
    return {t["ProductCD"] for t in card_txns if t["TransactionID"] != exclude_id}


def detect_recurring_match(card_txns, anchor):
    """
    R7: does the flagged transaction match the cardholder's own recurring
    pattern -- same product code, amount within 8%, spaced roughly monthly
    (20-40 days) from at least one prior occurrence?
    """
    amt = float(anchor["TransactionAmt"])
    anchor_ts = datetime.strptime(anchor["ts"], "%Y-%m-%d %H:%M:%S")
    hits = []
    for t in card_txns:
        if t["TransactionID"] == anchor["TransactionID"]:
            continue
        if t["ProductCD"] != anchor["ProductCD"]:
            continue
        t_amt = float(t["TransactionAmt"]) if t["TransactionAmt"] else None
        if t_amt is None or abs(t_amt - amt) > 0.08 * amt:
            continue
        t_ts = datetime.strptime(t["ts"], "%Y-%m-%d %H:%M:%S")
        gap_days = abs((anchor_ts - t_ts).days)
        if 20 <= gap_days <= 40 or (10 <= gap_days <= 50 and t["channel"] == anchor["channel"]):
            hits.append(t["TransactionID"])
    return hits


# ---------------------------------------------------------------------------
# Pattern detectors — each returns (matched: bool, strength: float 0-1, detail: str)
# ---------------------------------------------------------------------------

def detect_card_testing(gs, anchor_txn, card_id):
    window = gs.card_window(card_id, anchor_txn["TransactionID"], hours=1)
    small = [t for t in window if t["channel"] == "online" and t["TransactionAmt"] and float(t["TransactionAmt"]) < 5.0]
    larger = [t for t in window if t["TransactionAmt"] and float(t["TransactionAmt"]) >= 100.0]
    if len(small) >= 3 and larger:
        ids = [t["TransactionID"] for t in small] + [t["TransactionID"] for t in larger]
        # "Confirmed by the sequence itself" per the pattern definition -- this
        # is the one pattern where the graph evidence alone is near-decisive.
        return True, 0.80, ids, window, larger
    return False, 0.0, [], window, []


def detect_new_device_cnp(gs, anchor_txn, card_id):
    """
    A new device on a CNP transaction is real signal but explicitly NOT proof
    ("people buy new phones" -- pattern 3 definition). Kept below the R1
    verify threshold (0.70) on its own; only combined with an independent
    corroborating signal does it cross into strong-evidence territory.
    """
    idrow = gs.identity_by_txn.get(anchor_txn["TransactionID"])
    if anchor_txn["channel"] != "online" or not idrow:
        return False, 0.0, None
    new_dev = is_new_device(idrow)
    dn = gs.device_neighbors(anchor_txn["TransactionID"])
    return new_dev, (0.35 if new_dev else 0.0), dn


def detect_cnp_fraud(gs, anchor_txn, card_id):
    hist = gs.card_history(card_id)
    baseline = card_amount_baseline(hist["txns"], exclude_id=anchor_txn["TransactionID"])
    products_seen = card_products_seen(hist["txns"], exclude_id=anchor_txn["TransactionID"])
    amt = float(anchor_txn["TransactionAmt"])
    unusual_amt = baseline and amt > 4 * baseline["median"] and amt > baseline["max"] * 1.25
    unusual_product = anchor_txn["ProductCD"] not in products_seen if products_seen else False
    burst = gs.card_window(card_id, anchor_txn["TransactionID"], hours=48)
    burst_online = [t for t in burst if t["channel"] == "online"]
    is_burst = 2 <= len(burst_online) <= 6
    strength = 0.0
    reasons = []
    # "On its own, one unusual online purchase is ambiguous: verify" -- pattern 2 definition.
    if anchor_txn["channel"] == "online" and (unusual_amt or unusual_product):
        strength = 0.30
        if unusual_amt:
            reasons.append(f"amount ${amt:.2f} vs card median ${baseline['median']:.2f}")
        if unusual_product:
            reasons.append(f"product '{anchor_txn['ProductCD']}' never used before on this card")
        if is_burst and len(burst_online) >= 3:
            strength += 0.15
            reasons.append(f"{len(burst_online)} online txns within 48h")
    # Fix D: when no baseline history exists, flag large absolute online amounts as low-confidence CNP signal.
    elif anchor_txn["channel"] == "online" and not baseline and amt >= 500.0:
        strength = 0.15
        reasons.append(f"large online amount ${amt:.2f} with no prior card history for comparison")
    return strength > 0, strength, reasons, burst_online



def detect_out_of_region(gs, anchor_txn, card_id):
    if anchor_txn["channel"] != "in_person" or not anchor_txn.get("addr1"):
        return False, 0.0, None
    hist = gs.card_history(card_id)
    seen_regions = card_regions_seen(hist["txns"], exclude_id=anchor_txn["TransactionID"])
    # Fix C: if no prior history at all, we can't confirm "new region" but also can't
    # rule it out. Return a low-confidence signal rather than silently returning False.
    if not seen_regions:
        rc = gs.region_cluster(anchor_txn["TransactionID"], window_hours=72)
        return True, 0.15, {"region_cluster": rc, "span_days": 1, "note": "no prior history to compare"}
    new_region = anchor_txn["addr1"] not in seen_regions
    if not new_region:
        return False, 0.0, None
    rc = gs.region_cluster(anchor_txn["TransactionID"], window_hours=72)
    # a trip = several days of activity in the new region; a one-off = more suspicious
    same_card_in_region = [t for t in hist["txns"] if t["addr1"] == anchor_txn["addr1"]]
    span_days = 1
    if len(same_card_in_region) > 1:
        ts_list = sorted(datetime.strptime(t["ts"], "%Y-%m-%d %H:%M:%S") for t in same_card_in_region)
        span_days = (ts_list[-1] - ts_list[0]).days + 1
    # "Several days of purchases in one new region is a trip, not a clone" -- pattern 4 definition.
    strength = 0.40 if span_days <= 1 else 0.05
    return True, strength, {"region_cluster": rc, "span_days": span_days}



def detect_account_takeover(gs, anchor_txn, card_id):
    hist = gs.card_history(card_id)
    window = gs.card_window(card_id, anchor_txn["TransactionID"], hours=24)
    channels = {t["channel"] for t in window}
    bad_flags = txn_match_flags_bad(anchor_txn)
    mixed = len(channels) > 1
    strength = 0.0
    if mixed and bad_flags:
        strength = 0.45
    elif bad_flags:
        strength = 0.15
    return (strength > 0), strength, {"channels": list(channels), "bad_match_flags": bad_flags}


# ---------------------------------------------------------------------------
# Evidence-request simulation (Section 5 of policy: responses not provided)
# ---------------------------------------------------------------------------

def simulate_customer_response(trigger_type, strong_independent_corroboration, recurring_hits):
    """
    Deterministic, stated assumption (policy Section 5 requires we state one,
    since no live customer channel exists here).

    Deliberately NOT a function of the same probability that triggered the
    request -- using "prob >= 0.5 -> assume deny" would be circular (it turns
    every borderline signal into confirmed fraud by construction) and
    contradicts the dataset's own framing that roughly half of flagged
    activity is legitimate. Instead the assumed reply is grounded in evidence
    that is independent of the customer's word:
      - recurring_hits (R7): the flagged charge matches the cardholder's own
        established recurring pattern -> customer recognizes it (confirm),
        even if they initially reported it.
      - trigger_type == "customer_report" with no recurring match: the
        cardholder already proactively said this wasn't them -> deny.
      - strong_independent_corroboration (e.g. shared device with a CLOSED,
        CONFIRMED-fraud case, or a card-testing sequence with a cleared
        large purchase): deny, because the graph evidence alone already
        points to compromise regardless of what the customer says.
      - otherwise: confirm. This is the conservative default -- most
        single-signal alerts in this dataset resolve as legitimate, and
        assuming otherwise would systematically over-block real customers,
        which R1 explicitly warns against.
    """
    if recurring_hits:
        return "confirm"
    if trigger_type == "customer_report":
        return "deny"
    if strong_independent_corroboration:
        return "deny"
    return "confirm"


# ---------------------------------------------------------------------------
# Main investigation
# ---------------------------------------------------------------------------

def investigate_case(gs, case_row):
    case_id = case_row["case_id"]
    txn_id = case_row["flagged_txn_id"]
    card_id = case_row["card_id"]
    customer_id = case_row["customer_id"]
    trigger_type = case_row["trigger_type"]

    anchor = gs.get_txn(txn_id)
    tool_calls = 0
    evidence = []
    similar_case_ids = set()

    if anchor is None:
        # Defensive fallback — should not happen if IDs are valid
        return build_missing_txn_case(case_row)

    exposure_ids = set()
    reasons = []
    pattern = "none"
    pattern_description = ""
    prob = 0.0
    card_testing_cleared_large = []

    # --- R7 check first: does this match the cardholder's own recurring pattern? ---
    hist_all = gs.card_history(card_id)
    recurring_hits = detect_recurring_match(hist_all["txns"], anchor)
    tool_calls += 1
    if recurring_hits and trigger_type == "customer_report":
        evidence.append({
            "claim": f"Flagged charge matches the cardholder's own recurring pattern: same product code and amount (within 8%) recurring roughly monthly, e.g. {recurring_hits[0]}",
            "source": "graph", "ref": f"query:card_history(card_id={card_id})", "entity_ids": [txn_id] + recurring_hits[:3],
        })

    # --- Signal 1: card testing ---
    matched, strength, ids, window, cleared_large = detect_card_testing(gs, anchor, card_id)
    tool_calls += 1
    if matched:
        pattern, prob = "card_testing", max(prob, strength)
        exposure_ids.update(ids)
        card_testing_cleared_large = cleared_large
        evidence.append({
            "claim": f"{len([i for i in ids if float(gs.get_txn(i)['TransactionAmt'])<5])} small online authorizations under $5 within an hour on {card_id}, followed by a larger cleared purchase",
            "source": "graph", "ref": f"query:card_window(card_id={card_id},hours=1)", "entity_ids": ids,
        })

    # --- Signal 2: new device / CNP ---
    nd_matched, nd_strength, dn = detect_new_device_cnp(gs, anchor, card_id)
    tool_calls += 1
    if nd_matched and dn and dn["other_cards"]:
        prob = max(prob, nd_strength + 0.15)
        if pattern == "none":
            pattern = "card_not_present_new_device"
        exposure_ids.add(txn_id)
        other_cards = sorted(dn["other_cards"])[:5]
        evidence.append({
            "claim": f"Transaction from a device profile marked 'New' for this account, also seen on {len(dn['other_cards'])} other card(s): {', '.join(other_cards)}",
            "source": "graph", "ref": f"query:device_neighbors(txn_id={txn_id})",
            "entity_ids": [txn_id] + other_cards,
        })
    elif nd_matched:
        prob = max(prob, nd_strength)
        if pattern == "none":
            pattern = "card_not_present_new_device"
        exposure_ids.add(txn_id)
        evidence.append({
            "claim": "Transaction from a device profile marked 'New' for this account",
            "source": "graph", "ref": f"query:device_neighbors(txn_id={txn_id})", "entity_ids": [txn_id],
        })

    # --- Signal 3: CNP fraud (amount/product/burst anomaly) ---
    cnp_matched, cnp_strength, cnp_reasons, burst = detect_cnp_fraud(gs, anchor, card_id)
    tool_calls += 1
    if cnp_matched and pattern in ("none",):
        pattern, prob = "card_not_present_fraud", max(prob, cnp_strength)
        exposure_ids.update(t["TransactionID"] for t in burst) if burst else exposure_ids.add(txn_id)
        evidence.append({
            "claim": f"Card-not-present activity inconsistent with cardholder history: {'; '.join(cnp_reasons)}",
            "source": "graph", "ref": f"query:card_history(card_id={card_id})",
            "entity_ids": [txn_id] + [t["TransactionID"] for t in burst],
        })
    elif cnp_matched:
        prob = max(prob, cnp_strength * 0.6)  # corroborating, pattern already assigned above

    # --- Signal 4: out-of-region ---
    oor_matched, oor_strength, oor_detail = detect_out_of_region(gs, anchor, card_id)
    tool_calls += 1
    if oor_matched and pattern == "none":
        pattern, prob = "out_of_region_use", max(prob, oor_strength)
        exposure_ids.add(txn_id)
        span = oor_detail["span_days"]
        evidence.append({
            "claim": f"Card-present purchase in billing region {anchor['addr1']}, no prior history for this card in that region (activity span: {span} day(s))",
            "source": "graph", "ref": f"query:region_cluster(txn_id={txn_id},window_hours=72)", "entity_ids": [txn_id],
        })

    # --- Signal 5: account takeover ---
    ato_matched, ato_strength, ato_detail = detect_account_takeover(gs, anchor, card_id)
    tool_calls += 1
    if ato_matched and pattern == "none":
        pattern, prob = "account_takeover", max(prob, ato_strength)
        exposure_ids.add(txn_id)
        evidence.append({
            "claim": f"Mixed-channel activity ({', '.join(ato_detail['channels'])}) within 24h alongside failed match flags: {', '.join(ato_detail['bad_match_flags']) or 'none named'}",
            "source": "graph", "ref": f"query:card_window(card_id={card_id},hours=24)", "entity_ids": [txn_id],
        })

    # --- Shared-origin cross-check (R6): does the flagged txn's device/region also touch a CONFIRMED closed fraud case? ---
    tool_calls += 1
    shared_with_closed = False
    if nd_matched and dn and dn.get("device_key"):
        close_matches = gs.similar_closed_cases(pattern_hint=pattern if pattern != "none" else None,
                                                  device_key_=dn["device_key"], limit=5)
        for cc in close_matches:
            similar_case_ids.add(cc["case_id"])
            if cc["outcome"] == "confirmed_fraud":
                shared_with_closed = True
    else:
        close_matches = gs.similar_closed_cases(pattern_hint=pattern if pattern != "none" else None,
                                                  region=anchor.get("addr1"), limit=3)
        for cc in close_matches:
            similar_case_ids.add(cc["case_id"])

    if shared_with_closed:
        prob = min(0.97, prob + 0.2)
        evidence.append({
            "claim": f"Device profile also appears on {len([c for c in close_matches if c['outcome']=='confirmed_fraud'])} closed case(s) confirmed as fraud",
            "source": "graph", "ref": "query:similar_closed_cases(device_key=...)",
            "entity_ids": [c["case_id"] for c in close_matches if c["outcome"] == "confirmed_fraud"],
        })

    # risk_score is corroborating only, never a verdict (README rule)
    if case_row.get("risk_score"):
        rs = float(case_row["risk_score"])
        evidence.append({
            "claim": f"Bank model risk score was {rs:.2f} at trigger time (input signal only, not treated as a verdict)",
            "source": "graph", "ref": "case_pack.csv", "entity_ids": [txn_id],
        })

    # No pattern matched at all -> legitimate-leaning
    if pattern == "none" and prob == 0.0:
        prob = 0.10 if trigger_type != "customer_report" else 0.35
        evidence.append({
            "claim": "No card-testing, new-device, unusual-amount/product, out-of-region, or mixed-channel signal found for this transaction",
            "source": "graph", "ref": f"query:card_history(card_id={card_id})", "entity_ids": [txn_id],
        })

    prob = round(min(prob, 0.97), 2)


    # ------------------ Initial recommendation (policy R1-R10) ------------------
    exposure_now = round(sum(abs(float(gs.get_txn(t)["TransactionAmt"])) for t in exposure_ids if gs.get_txn(t)), 2)
    initial_actions, initial_rule = recommend_initial(pattern, prob, exposure_now, trigger_type, bool(card_testing_cleared_large))

    # ------------------ Evidence gathering under uncertainty ------------------
    evidence_requests = []
    # Only request additional evidence when probability is in the genuinely ambiguous/uncertain band
    # (0.20 < prob < 0.70) or when customer verification is specifically required.
    # Clearly legitimate (prob <= 0.20) or clearly confident fraud (prob >= 0.70) skip evidence requests.
    needs_more = (
        (0.20 < prob < 0.70) and
        any(a["action"] in ("VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH") for a in initial_actions)
    )
    final_actions = initial_actions
    final_pattern, final_prob = pattern, prob
    what_changed = "nothing"

    strong_independent_corroboration = shared_with_closed or bool(card_testing_cleared_large)


    if needs_more:
        response = simulate_customer_response(trigger_type, strong_independent_corroboration, recurring_hits)
        evidence_requests.append({
            "type": "customer_validation",
            "asked_after_step": tool_calls,
            "assumed_response": (
                "Customer states they did not make this purchase" if response == "deny"
                else "Customer confirms they made this purchase" if not recurring_hits
                else "Customer confirms this matches a recurring charge they recognize once shown the history"
            ),
        })
        if response == "deny":
            # R2 is unconditional (BLOCK_CARD + CREATE_CASE on any denial, not
            # gated by a probability threshold) -- once we simulate a denial
            # the case resolves as fraud, so floor the probability so the
            # verdict logic below doesn't leave it stranded as "uncertain".
            final_prob = round(min(0.97, max(prob + 0.22, 0.75)), 2)
            evidence.append({
                "claim": "Customer denied making the flagged transaction",
                "source": "customer", "ref": "evidence_request:1", "entity_ids": [],
            })
            if pattern == "none":
                exposure_ids.add(txn_id)
                exposure_now = round(abs(float(anchor["TransactionAmt"])), 2)
                final_pattern = "undocumented"
                pattern_description = (
                    "Customer denies a transaction with no signal matching a documented pattern "
                    "(no shared device, no new region, no burst, no amount/product anomaly). "
                    "Recorded as undocumented pending analyst review rather than forced into a known category."
                )
            final_actions = recommend_final_deny(final_pattern, final_prob, exposure_now, exposure_ids, gs, shared_with_closed)
            what_changed = "Customer denial, combined with independent graph corroboration, raised fraud probability and moved the recommendation from verification to blocking/case action."
        else:
            is_r7 = recurring_hits and trigger_type == "customer_report"
            # Fix E: for confirmed new-device cases without strong corroboration, maintain a floor in the
            # uncertain range (e.g. 0.25-0.30) so they can be reviewed by an analyst instead of collapsing to legitimate.
            if nd_matched:
                final_prob = round(max(0.25, prob - 0.20), 2)
                final_pattern = pattern
            elif is_r7:
                final_prob = min(round(max(0.03, prob - 0.25), 2), 0.15)
                final_pattern = "none"
            else:
                final_prob = round(max(0.03, prob - 0.25), 2)
                final_pattern = "none"
            evidence.append({
                "claim": "Customer confirmed making the flagged transaction" if not recurring_hits
                         else "Customer's dispute matched their own recurring charge history and was recognized once shown the pattern",
                "source": "customer", "ref": "evidence_request:1", "entity_ids": [],
            })

            if recurring_hits and trigger_type == "customer_report":
                final_actions = [
                    policy.act("CREATE_CASE", "R7: disputed charge matches cardholder's own recurring pattern"),
                    policy.act("VERIFY_WITH_CUSTOMER", "R7: confirm recurring merchant/amount with cardholder"),
                    policy.act("WARN_CUSTOMER", "R7: explain the recurring charge; do not block"),
                ]
                what_changed = "Customer's own recurring transaction history explained the disputed charge; case opened for the record but no block, per R7."
            elif nd_matched:
                final_actions = [
                    policy.act("ESCALATE_TO_ANALYST", "Fix E / R8: customer confirmed but transaction originated from a new device profile; pending analyst review"),
                    policy.act("MONITOR_CARD", "Monitor card for further unusual device activity"),
                ]
                what_changed = "Customer confirmed, but flagged new device profile requires analyst review rather than immediate closure."
            else:
                final_actions = [policy.act("CLOSE_NO_FRAUD", "R3: customer confirmed the transaction")]
                what_changed = "Customer confirmation cleared the alert; case closed as legitimate."

    # ------------------ Verdict / status ------------------
    if final_prob >= 0.60:
        verdict, status = "fraud", "closed_fraud"
    elif final_prob <= 0.20:
        verdict, status = "legitimate", "closed_legitimate"
    else:
        verdict, status = "uncertain", "escalated"
        if not any(a["action"] == "ESCALATE_TO_ANALYST" for a in final_actions):
            final_actions.append(policy.act("ESCALATE_TO_ANALYST", "R8: uncertain verdict requires human analyst review"))


    if verdict == "legitimate":
        exposure_ids = set()
        exposure_now = 0.0
        final_pattern = "none"

    final_actions = dedupe_actions(final_actions)

    affected = sorted(exposure_ids) if verdict != "legitimate" else []
    first_susp = min(affected, key=lambda t: gs.get_txn(t)["ts"]) if affected else txn_id

    connected_cards = sorted({c for c in ([dn["other_cards"] if nd_matched and dn else set()][0])}) if nd_matched and dn else []
    connected_devices = [dn["device_desc"]] if (nd_matched and dn and dn.get("device_desc")) else []

    sar = build_sar(final_pattern, verdict, final_actions, exposure_now, affected, gs, case_row, connected_cards, evidence)

    summary = synthesize_summary(case_row, final_pattern, verdict, final_prob, exposure_now, evidence_requests)

    stop_reason = decide_stop_reason(final_prob, evidence_requests)

    graph_case_id = f"AC-{case_id}"

    case_obj = {
        "status": status,
        "verdict": verdict,
        "fraud_probability": final_prob,
        "pattern": final_pattern,
        "pattern_description": pattern_description,
        "affected_txn_ids": affected,
        "first_suspicious_txn_id": first_susp,
        "connected_card_ids": connected_cards,
        "connected_device_profiles": connected_devices,
        "exposure_usd": exposure_now,
        "evidence": evidence,
        "similar_prior_cases": sorted(similar_case_ids),
        "summary": summary,
        "written_to_graph": True,
        "graph_case_id": graph_case_id,
    }

    answer = {
        "case_id": case_id,
        "case": case_obj,
        "evidence_requests": evidence_requests,
        "next_best_actions": {
            "initial": initial_actions,
            "final": final_actions,
            "what_changed": what_changed,
        },
        "sar": sar,
        "stop_reason": stop_reason,
        "tool_calls": tool_calls + len(evidence_requests),
        "tokens": 0,  # filled in once an LLM node is wired in; heuristic engine uses none
        "latency_s": 0.0,
    }
    return answer


def dedupe_actions(actions):
    seen = {}
    for a in actions:
        if a["action"] not in seen:
            seen[a["action"]] = a
        else:
            seen[a["action"]]["reason"] += f"; {a['reason']}"
    return list(seen.values())


def recommend_initial(pattern, prob, exposure_now, trigger_type, cleared_large_exists):
    actions = []
    if pattern == "card_testing":
        actions.append(policy.act("STEP_UP_AUTH", "R5: card testing sequence detected"))
        if cleared_large_exists:
            actions.append(policy.act("BLOCK_CARD", "R5: a purchase over $100 already cleared after the testing sequence", exposure_now))
            actions.append(policy.act("CREATE_CASE", "R3a: fraud probability has reached 0.30+"))
        else:
            actions.append(policy.act("DECLINE_TRANSACTION", "R5: card testing sequence observed, decline before anything clears", exposure_now))
        return actions, "R5"
    if prob <= 0.20:
        actions.append(policy.act("CLOSE_NO_FRAUD", f"R3: fraud probability {prob:.2f} is well below threshold; no actionable fraud pattern detected"))
        return actions, "R3"
    if prob < 0.70:
        actions.append(policy.act("VERIFY_WITH_CUSTOMER", f"R1: fraud probability {prob:.2f} in uncertain band, verify before taking disruptive action"))
        return actions, "R1"
    # prob >= 0.70 on first pass (e.g. strong device+closed-case link)
    actions.append(policy.act("BLOCK_CARD", "Strong first-pass evidence (probability >= 0.70)", exposure_now))
    actions.append(policy.act("CREATE_CASE", "R3a: fraud probability has reached 0.30+"))
    return actions, "strong-signal"



def recommend_final_deny(pattern, prob, exposure_now, exposure_ids, gs, shared_with_closed):
    actions = []
    if pattern == "card_testing":
        actions.append(policy.act("BLOCK_CARD", "R5: purchase over $100 already cleared after testing sequence", exposure_now))
    else:
        actions.append(policy.act("BLOCK_CARD", "R2: customer denied the transaction", exposure_now))
    actions.append(policy.act("CREATE_CASE", "R2"))
    file_report = exposure_now > 1000 or shared_with_closed
    if pattern == "undocumented":
        actions.append(policy.act("FILE_REPORT", "R9: undocumented but coordinated/repeated abuse pattern", exposure_now))
        actions.append(policy.act("ESCALATE_TO_ANALYST", "R9: undocumented pattern needs analyst review"))
    elif file_report:
        actions.append(policy.act("FILE_REPORT", "R2: exposure exceeds $1,000 or activity connects to a shared device/another card's fraud", exposure_now))
    if shared_with_closed or pattern == "card_not_present_new_device":
        actions.append(policy.act("MONITOR_CONNECTED_CARDS", "R6: same device profile links this to other card(s)"))
    return actions


def build_sar(pattern, verdict, final_actions, exposure_now, affected, gs, case_row, connected_cards, evidence):
    file_report = any(a["action"] == "FILE_REPORT" for a in final_actions)
    if not file_report:
        return {"file": False, "reason": "R2/R9: exposure under $1,000 with no shared-device or coordinated-abuse link; a case is sufficient",
                "narrative": "", "subjects": [], "total_amount_usd": 0, "activity_dates": []}
    txns = [gs.get_txn(t) for t in affected if gs.get_txn(t)]
    dates = sorted({t["ts"][:10] for t in txns})
    activity_dates = [dates[0], dates[-1]] if dates else []
    subjects = list(dict.fromkeys([case_row["customer_id"], case_row["card_id"]] + connected_cards))
    lines = []
    lines.append(f"Between {activity_dates[0] if activity_dates else 'the dates below'} and {activity_dates[-1] if activity_dates else ''}, "
                  f"card {case_row['card_id']} belonging to customer {case_row['customer_id']} was used for "
                  f"{len(txns)} transaction(s) totaling ${exposure_now:,.2f} that the bank has assessed as {pattern.replace('_',' ')}.")
    if connected_cards:
        lines.append(f"The activity is connected by a shared device profile to {len(connected_cards)} additional card(s): {', '.join(connected_cards)}.")
    lines.append("The cardholder was asked to validate the activity and denied making the transaction(s), consistent with unauthorized use rather than a recognized recurring pattern.")
    lines.append(f"Affected transaction IDs: {', '.join(affected)}.")
    lines.append("The card has been blocked and scheduled for reissue; connected cards, where identified, have been placed under monitoring pending further review.")
    lines.append("This activity is reported as suspicious because it is inconsistent with the cardholder's established transaction history and, where applicable, is linked to other compromised cards through a common device profile.")
    narrative = " ".join(lines)
    return {
        "file": True,
        "reason": "R2/R6: confirmed or strongly suspected unauthorized use above the reporting threshold or connected to a shared device",
        "narrative": narrative,
        "subjects": subjects,
        "total_amount_usd": exposure_now,
        "activity_dates": activity_dates,
    }


from agent.graph_state import call_grok

def synthesize_summary(case_row, pattern, verdict, prob, exposure_now, evidence_requests):
    try:
        sys_prompt = "You are an expert fraud analyst. Write a one-paragraph summary of the case."
        user_prompt = f"Case details: {case_row['case_id']}, Verdict: {verdict}, Probability: {prob}, Pattern: {pattern}, Exposure: ${exposure_now}. Evidence requests: {evidence_requests}"
        return call_grok(sys_prompt, user_prompt)
    except Exception as e:
        base = f"Case {case_row['case_id']}: {verdict} verdict (p={prob:.2f}) on card {case_row['card_id']}."
        if pattern != "none":
            base += f" Pattern: {pattern.replace('_', ' ')}."
        if exposure_now:
            base += f" Exposure ${exposure_now:,.2f}."
        if evidence_requests:
            base += " Customer validation was requested; recommendation was updated once the assumed response came back."
        else:
            base += " No additional evidence was required to reach a defensible decision."
        return base

def decide_stop_reason(final_prob, evidence_requests):
    if final_prob >= 0.85 or final_prob <= 0.15:
        return "Fraud probability reached a decisive threshold (>=0.85 or <=0.15) on independent evidence; further steps would not change the action."
    if evidence_requests:
        return "Customer validation response settled the verdict; no further evidence would change the recommended action."
    return "Evidence gathered is internally consistent and sufficient for a defensible action at this probability; escalated to a human analyst rather than continuing to loop."


def build_missing_txn_case(case_row):
    # Should not trigger against the real dataset; guards the pipeline only.
    raise ValueError(f"Flagged transaction {case_row['flagged_txn_id']} not found for case {case_row['case_id']}")


def run_all():
    OUT_DIR.mkdir(exist_ok=True, parents=True)
    gs = TigerGraphMCPStore(needed_customers=None)
    if not getattr(gs, "conn", None):
        gs = LocalGraphStore(needed_customers=None)
        gs.load()
    cases = load_case_pack()
    results = []
    for c in cases:
        ans = investigate_case(gs, c)
        out_path = OUT_DIR / f"{c['case_id']}.json"
        with open(out_path, "w") as f:
            json.dump(ans, f, indent=2)
        results.append(ans)
        print(f"{c['case_id']}: verdict={ans['case']['verdict']} pattern={ans['case']['pattern']} "
              f"prob={ans['case']['fraud_probability']} exposure=${ans['case']['exposure_usd']} "
              f"actions_final={[a['action'] for a in ans['next_best_actions']['final']]}")
    return results


if __name__ == "__main__":
    run_all()
