import json, glob

required_fields = [
    "status", "verdict", "fraud_probability", "pattern",
    "affected_txn_ids", "evidence", "summary", "graph_case_id", "exposure_usd"
]
issues = []

for p in sorted(glob.glob("cases/*.json")):
    with open(p) as f:
        d = json.load(f)
    cid = d["case_id"]
    c = d["case"]
    errs = []

    # Required case fields
    for field in required_fields:
        if field not in c:
            errs.append("missing case." + field)

    # next_best_actions completeness
    nba = d.get("next_best_actions", {})
    if not nba.get("initial"):      errs.append("missing next_best_actions.initial")
    if not nba.get("final"):        errs.append("missing next_best_actions.final")
    if "what_changed" not in nba:   errs.append("missing next_best_actions.what_changed")

    # SAR structure
    sar = d.get("sar", {})
    if "file" not in sar:           errs.append("missing sar.file")

    # Policy SAR check: must file when exposure > 1000 or undocumented pattern
    verdict  = c.get("verdict", "")
    pattern  = c.get("pattern", "")
    exposure = float(c.get("exposure_usd", 0))
    sar_filed = sar.get("file", False)
    final_actions = [a["action"] for a in nba.get("final", [])]
    has_file_report = "FILE_REPORT" in final_actions

    sar_required_by_policy = (
        verdict == "fraud" and (exposure > 1000 or pattern == "undocumented")
    )
    if sar_required_by_policy and not sar_filed:
        errs.append(
            "SAR required by policy (exposure=${:.2f}, pattern={}) but sar.file=False".format(
                exposure, pattern
            )
        )

    verdict_str = c.get("verdict", "?")
    actions_str = str(final_actions)
    if errs:
        issues.append((cid, errs))
        print("[ISSUE] {}: {}".format(cid, "; ".join(errs)))
    else:
        print("[  OK ] {}: verdict={}, sar={}, actions={}".format(
            cid, verdict_str, sar_filed, actions_str
        ))

print()
if not issues:
    print("All 20 cases: COMPLETE - no missing fields or policy violations")
else:
    print("{} case(s) have issues:".format(len(issues)))
    for cid, errs in issues:
        for e in errs:
            print("  {} -> {}".format(cid, e))
