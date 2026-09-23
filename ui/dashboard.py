"""
dashboard.py — Analyst dashboard for the fraud investigation agent.
Run: streamlit run ui/dashboard.py
Reads directly from cases/*.json (the same files submitted as deliverables),
so what the demo shows is exactly what was scored -- no separate "demo data".
"""
import json
import glob
import html
from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

st.set_page_config(page_title="Fraud Investigation Agent", layout="wide")

CASES_DIR = "cases"
UI_DIR = Path(__file__).parent
VCOL = {"fraud": "var(--fraud)", "legitimate": "var(--ok)", "uncertain": "var(--unc)"}

LOGO = """<svg class="logo" viewBox="0 0 120 120" aria-hidden="true">
<path class="sh" d="M60 8 L104 24 V58 C104 84 86 102 60 112 C34 102 16 84 16 58 V24 Z"/>
<path class="lk" d="M60 60 L60 36 M60 60 L38 60 M60 60 L82 58 M60 60 L60 86 M60 36 L82 58 M38 60 L60 86"/>
<circle class="nd" cx="60" cy="60" r="6"/><circle class="nd" cx="60" cy="36" r="4.5"/><circle class="nd" cx="38" cy="60" r="4.5"/><circle class="nd" cx="60" cy="86" r="4.5"/>
<circle class="sc" cx="82" cy="58" r="12"/><circle class="rd" cx="82" cy="58" r="6"/>
</svg>"""


def esc(x):
    # escape HTML, and "$" so Streamlit markdown does not treat it as LaTeX
    return html.escape(str(x)).replace("$", "&#36;")


def load_theme():
    css = (UI_DIR / "style.css").read_text()
    st.markdown("<style>" + "\n".join(l for l in css.splitlines() if l.strip()) + "</style>",
                unsafe_allow_html=True)
    components.html("<script>" + (UI_DIR / "effects.js").read_text() + "</script>", height=0)


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

load_theme()

st.markdown('<div class="brand">' + LOGO + '<div class="title">Agentic fraud investigation</div></div>'
            '<p class="sub">TigerGraph + LangGraph + Grok · HHGoa\'26 · 20-case benchmark</p>',
            unsafe_allow_html=True)

stats = [
    ("Cases investigated", len(df), ""),
    ("Fraud verdicts", int((df["verdict"] == "fraud").sum()), "f"),
    ("Legitimate verdicts", int((df["verdict"] == "legitimate").sum()), "l"),
    ("SARs filed", int(df["sar_filed"].sum()), ""),
]
st.markdown('<div class="stats">' + "".join(
    f'<div class="stat {k}"><b class="cu" data-n="{n}">0</b><span>{label}</span></div>'
    for label, n, k in stats) + '</div>', unsafe_allow_html=True)

st.divider()

left, right = st.columns([1, 2])

with left:
    st.subheader("Case list")
    verdict_filter = st.multiselect("Filter by verdict", options=df["verdict"].unique().tolist(),
                                     default=df["verdict"].unique().tolist())
    filtered = df[df["verdict"].isin(verdict_filter)]
    case_options = filtered["case_id"].tolist()
    selected = st.selectbox("Open case", options=case_options)
    st.dataframe(
        filtered[["case_id", "verdict", "pattern", "probability", "exposure_usd", "sar_filed"]],
        use_container_width=True, hide_index=True,
    )


with right:
    if selected:
        d = raw[selected]
        c = d["case"]
        v = c["verdict"]
        evid = "".join(
            f'<details><summary>{esc(ev["claim"][:80])}...</summary>'
            f'{esc(ev["claim"])}<br><code>{esc(ev["ref"])}</code><br>Source: {esc(ev["source"])}'
            + (f'<br>Entity IDs: {esc(", ".join(ev["entity_ids"]))}' if ev["entity_ids"] else "")
            + '</details>' for ev in c["evidence"])
        if d["evidence_requests"]:
            asks = "".join(
                f'<div class="ask">Requested <code>{esc(r["type"])}</code> after step {esc(r["asked_after_step"])}. '
                f'Assumed response: <i>{esc(r["assumed_response"])}</i></div>' for r in d["evidence_requests"])
        else:
            asks = '<div class="none">None needed — evidence was sufficient after the initial graph pass.</div>'

        def acts(lst):
            return "".join(f'<div><code>{esc(a["action"])}</code> ({esc(a["route"])}) — {esc(a["reason"])}</div>'
                           for a in lst)

        nba = d["next_best_actions"]
        prior = (f'<div class="step"><div class="h">Case memory — similar prior cases retrieved</div>'
                 f'{esc(", ".join(c["similar_prior_cases"]))}</div>') if c["similar_prior_cases"] else ""
        st.markdown(
            '<div class="panel sw">'
            f'<div class="head"><div class="ttl">Case {esc(selected)}</div>'
            f'<span class="badge {"fraud" if v == "fraud" else ""}" style="color:{VCOL[v]}">'
            f'<span class="dot" style="background:{VCOL[v]}"></span>{esc(v.capitalize())} · {esc(c["pattern"].replace("_", " "))}</span></div>'
            f'<div class="meter"><i style="width:{c["fraud_probability"] * 100:.0f}%;background:{VCOL[v]}"></i></div>'
            f'<small style="color:var(--mu)">Fraud probability {c["fraud_probability"]:.2f}</small>'
            '<div class="kv">'
            f'<div><span>Exposure</span><b class="cu" data-n="{c["exposure_usd"]}" data-d="2" data-pre="&#36;">&#36;0.00</b></div>'
            f'<div><span>Status</span><b>{esc(c["status"])}</b></div>'
            f'<div><span>Graph case ID</span><b>{esc(c["graph_case_id"])}</b></div></div>'
            f'<div class="sum">{esc(c["summary"])}</div>'
            '<div class="tl">'
            f'<div class="step"><div class="h">Evidence gathered</div>{evid}</div>'
            f'<div class="step"><div class="h">Evidence requested under uncertainty</div>{asks}</div>'
            '<div class="step"><div class="h">Next-best action, before and after</div>'
            f'<div class="ba"><div><em>Initial</em>{acts(nba["initial"])}</div><div><em>Final</em>{acts(nba["final"])}</div></div>'
            f'<div class="chg">What changed: {esc(nba["what_changed"])}</div></div>'
            f'<div class="step"><div class="h">Stopping condition</div>{esc(d["stop_reason"])}</div>'
            f'{prior}'
            '</div></div>', unsafe_allow_html=True)

        if d["sar"]["file"]:
            st.markdown("---")
            st.markdown("#### 📄 Suspicious Activity Report")
            st.write(f"**Reason:** {d['sar']['reason']}")
            st.write(f"**Subjects:** {', '.join(d['sar']['subjects'])}")
            st.write(f"**Total amount:** ${d['sar']['total_amount_usd']:,.2f}")
            st.text_area("Narrative", d["sar"]["narrative"], height=150)

        with st.expander("Raw JSON (full answer file)"):
            st.json(d)
            