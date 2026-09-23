import json
from pathlib import Path

REQUIRED_TOP = {"case_id", "case", "evidence_requests", "next_best_actions", "sar"}
REQUIRED_CASE = {
    "status", "verdict", "fraud_probability", "pattern", "pattern_description",
    "affected_txn_ids", "first_suspicious_txn_id", "connected_card_ids",
    "connected_device_profiles", "exposure_usd", "evidence", "similar_prior_cases",
    "summary", "written_to_graph", "graph_case_id",
}
REQUIRED_NBA = {"initial", "final", "what_changed"}
REQUIRED_SAR = {"file", "reason", "narrative", "subjects", "total_amount_usd", "activity_dates"}
VALID_PATTERNS = {"card_testing", "card_not_present_fraud", "card_not_present_new_device",
                   "out_of_region_use", "account_takeover", "undocumented", "none"}
VALID_VERDICTS = {"fraud", "legitimate", "uncertain"}
VALID_STATUS = {"open", "closed_fraud", "closed_legitimate", "escalated"}

ok = True
for p in sorted(Path("/home/claude/fraud-agent/cases").glob("*.json")):
    d = json.load(open(p))
    missing_top = REQUIRED_TOP - d.keys()
    missing_case = REQUIRED_CASE - d.get("case", {}).keys()
    missing_nba = REQUIRED_NBA - d.get("next_best_actions", {}).keys()
    missing_sar = REQUIRED_SAR - d.get("sar", {}).keys()
    problems = []
    if missing_top: problems.append(f"missing top-level: {missing_top}")
    if missing_case: problems.append(f"missing case fields: {missing_case}")
    if missing_nba: problems.append(f"missing next_best_actions fields: {missing_nba}")
    if missing_sar: problems.append(f"missing sar fields: {missing_sar}")
    if d["case"]["pattern"] not in VALID_PATTERNS: problems.append(f"bad pattern: {d['case']['pattern']}")
    if d["case"]["verdict"] not in VALID_VERDICTS: problems.append(f"bad verdict: {d['case']['verdict']}")
    if d["case"]["status"] not in VALID_STATUS: problems.append(f"bad status: {d['case']['status']}")
    if d["case"]["verdict"] == "legitimate":
        if d["case"]["affected_txn_ids"]: problems.append("legitimate but affected_txn_ids not empty")
        if d["case"]["exposure_usd"] != 0: problems.append("legitimate but exposure_usd != 0")
        if d["sar"]["file"]: problems.append("legitimate but sar.file is true")
    for a in d["next_best_actions"]["initial"] + d["next_best_actions"]["final"]:
        if not {"action", "route", "reason"} <= a.keys():
            problems.append(f"malformed action entry: {a}")
    if problems:
        ok = False
        print(f"{p.name}: {'; '.join(problems)}")
    else:
        print(f"{p.name}: OK  (verdict={d['case']['verdict']}, pattern={d['case']['pattern']}, "
              f"sar.file={d['sar']['file']}, actions={[a['action'] for a in d['next_best_actions']['final']]})")

print("\nALL VALID" if ok else "\nSCHEMA PROBLEMS FOUND")
