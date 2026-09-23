import json, glob, statistics

cases = []
for p in sorted(glob.glob("cases/*.json")):
    with open(p) as f:
        cases.append(json.load(f))

verdicts = [d["case"]["verdict"] for d in cases]
patterns = [d["case"]["pattern"] for d in cases]
sars     = [d["sar"]["file"] for d in cases]
probs    = [d["case"]["fraud_probability"] for d in cases]
exposures= [d["case"]["exposure_usd"] for d in cases if d["case"]["exposure_usd"] > 0]
actions  = [a["action"] for d in cases for a in d["next_best_actions"]["final"]]

print("=== CASE STATS ===")
print(f"Total cases:      {len(cases)}")
print(f"Fraud verdicts:   {verdicts.count('fraud')}")
print(f"Legitimate:       {verdicts.count('legitimate')}")
print(f"Uncertain:        {verdicts.count('uncertain')}")
print(f"SARs filed:       {sum(sars)}")
print()
print("Pattern breakdown:")
from collections import Counter
for pat, cnt in Counter(patterns).most_common():
    print(f"  {pat}: {cnt}")
print()
print("Final action breakdown:")
for act, cnt in Counter(actions).most_common():
    print(f"  {act}: {cnt}")
print()
print(f"Fraud prob range: {min(probs):.2f} – {max(probs):.2f}")
print(f"Avg fraud prob:   {statistics.mean(probs):.2f}")
print(f"Total exposure:   ${sum(exposures):,.2f}")
print()

# Evidence-request stats
ev_reqs = [d for d in cases if d["evidence_requests"]]
print(f"Cases needing evidence request: {len(ev_reqs)}")
denies  = sum(1 for d in ev_reqs if "denied" in d["evidence_requests"][0].get("assumed_response","").lower()
              or "did not make" in d["evidence_requests"][0].get("assumed_response","").lower())
confirms = len(ev_reqs) - denies
print(f"  -> assumed deny:    {denies}")
print(f"  -> assumed confirm: {confirms}")

# Action change between initial and final
changed = 0
for d in cases:
    ini = set(a["action"] for a in d["next_best_actions"]["initial"])
    fin = set(a["action"] for a in d["next_best_actions"]["final"])
    if ini != fin:
        changed += 1
print(f"Cases where initial->final actions changed: {changed}")
