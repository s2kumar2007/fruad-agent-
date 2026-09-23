"""
policy.py — Fraud Policy v1.0, encoded exactly as specified in the README.
Action names, approval routes, and rule numbers are used verbatim so they can
be cited directly in case evidence and next_best_actions.
"""

AUTO_ACTIONS = {
    "ALLOW_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS", "WARN_CUSTOMER",
    "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "GENERATE_REPORT", "CREATE_CASE",
    "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD",
}
L1_ACTIONS = {"DECLINE_TRANSACTION"}  # BLOCK_CARD is L1 or L2 depending on exposure
L2_ONLY_ACTIONS = {"BLOCK_ALL_CARDS", "FILE_REPORT"}


def route_for(action, exposure_usd=0.0):
    if action == "BLOCK_CARD":
        return "L1" if exposure_usd <= 2500 else "L2"
    if action in AUTO_ACTIONS:
        return "auto"
    if action in L1_ACTIONS:
        return "L1"
    if action in L2_ONLY_ACTIONS:
        return "L2"
    return "L1"  # safe default for anything unrecognized


def act(action, reason, exposure_usd=0.0):
    return {"action": action, "route": route_for(action, exposure_usd), "reason": reason}
