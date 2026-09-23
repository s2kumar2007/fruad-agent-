"""
dashboard.py — Analyst dashboard for the fraud investigation agent.
Run: streamlit run ui/dashboard.py
Reads directly from cases/*.json (the same files submitted as deliverables),
so what the demo shows is exactly what was scored -- no separate "demo data".
"""
import json
import glob
import streamlit as st
import pandas as pd

st.set_page_config(page_title="Fraud Investigation Agent", layout="wide")

CASES_DIR = "/home/claude/fraud-agent/cases"


@st.cache_data
def load_cases():
    rows = []
    raw = {}
    for path in sorted(glob.glob(f"{CASES_DIR}/*.json")):
        d = json.load(open(path))
        raw[d["case_id"]] = d
        rows.append({
            "case_id": d["case_id"],
            "verdict": d["case"]["verdict"],
            "pattern": d["case"]["pattern"],
            "probability": d["case"]["fraud_probability"],
            "exposure_usd": d["case"]["exposure_usd"],
            "sar_filed": d["sar"]["file"],
            "final_actions": ", ".join(a["action"] for a in d["next_best_actions"]["final"]),
            "evidence_requests": len(d["evidence_requests"]),
        })
    return pd.DataFrame(rows), raw


df, raw = load_cases()

st.title("🕵️ Agentic Fraud Investigation — Case Dashboard")
st.caption("TigerGraph + LangGraph + Grok · HHGoa'26 · 20-case benchmark")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Cases investigated", len(df))
col2.metric("Fraud verdicts", int((df["verdict"] == "fraud").sum()))
col3.metric("Legitimate verdicts", int((df["verdict"] == "legitimate").sum()))
col4.metric("SARs filed", int(df["sar_filed"].sum()))

st.divider()

left, right = st.columns([1, 2])

with left:
    st.subheader("Case list")
    verdict_filter = st.multiselect("Filter by verdict", options=df["verdict"].unique().tolist(),
                                     default=df["verdict"].unique().tolist())
    filtered = df[df["verdict"].isin(verdict_filter)]
    st.dataframe(
        filtered[["case_id", "verdict", "pattern", "probability", "exposure_usd", "sar_filed"]],
        use_container_width=True, hide_index=True,
    )
    selected = st.selectbox("Open case", filtered["case_id"].tolist())

with right:
    if selected:
        d = raw[selected]
        c = d["case"]
        st.subheader(f"Case {selected}")

        badge_color = {"fraud": "🔴", "legitimate": "🟢", "uncertain": "🟡"}[c["verdict"]]
        st.markdown(f"### {badge_color} {c['verdict'].upper()} — {c['pattern'].replace('_', ' ')}")
        st.progress(c["fraud_probability"], text=f"Fraud probability: {c['fraud_probability']:.2f}")

        m1, m2, m3 = st.columns(3)
        m1.metric("Exposure", f"${c['exposure_usd']:,.2f}")
        m2.metric("Status", c["status"])
        m3.metric("Graph case ID", c["graph_case_id"])

        st.markdown("**Summary**")
        st.info(c["summary"])

        st.markdown("---")
        st.markdown("#### Investigation timeline")

        st.markdown("**1. Evidence gathered**")
        for i, ev in enumerate(c["evidence"], 1):
            with st.expander(f"Evidence {i}: {ev['claim'][:80]}...", expanded=False):
                st.write(f"**Claim:** {ev['claim']}")
                st.code(ev["ref"], language="text")
                st.write(f"**Source:** {ev['source']}")
                if ev["entity_ids"]:
                    st.write(f"**Entity IDs:** {', '.join(ev['entity_ids'])}")

        if d["evidence_requests"]:
            st.markdown("**2. Evidence requested under uncertainty**")
            for req in d["evidence_requests"]:
                st.warning(f"Requested `{req['type']}` after step {req['asked_after_step']}. "
                           f"Assumed response: *{req['assumed_response']}*")
        else:
            st.markdown("**2. Evidence requested under uncertainty**")
            st.write("None needed — evidence was sufficient after the initial graph pass.")

        st.markdown("**3. Next-best-action — before vs. after**")
        a1, a2 = st.columns(2)
        with a1:
            st.caption("Initial")
            for a in d["next_best_actions"]["initial"]:
                st.write(f"`{a['action']}` ({a['route']}) — {a['reason']}")
        with a2:
            st.caption("Final")
            for a in d["next_best_actions"]["final"]:
                st.write(f"`{a['action']}` ({a['route']}) — {a['reason']}")
        st.caption(f"What changed: {d['next_best_actions']['what_changed']}")

        st.markdown("**4. Stopping condition**")
        st.write(d["stop_reason"])

        if c["similar_prior_cases"]:
            st.markdown("**5. Case memory — similar prior cases retrieved**")
            st.write(", ".join(c["similar_prior_cases"]))

        if d["sar"]["file"]:
            st.markdown("---")
            st.markdown("#### 📄 Suspicious Activity Report")
            st.write(f"**Reason:** {d['sar']['reason']}")
            st.write(f"**Subjects:** {', '.join(d['sar']['subjects'])}")
            st.write(f"**Total amount:** ${d['sar']['total_amount_usd']:,.2f}")
            st.text_area("Narrative", d["sar"]["narrative"], height=150)

        with st.expander("Raw JSON (full answer file)"):
            st.json(d)
