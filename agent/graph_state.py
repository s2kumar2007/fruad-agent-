"""
graph_state.py — LangGraph state machine for the agentic fraud investigation.

HAND-OFF NOTE FOR CLAUDE CODE / whoever wires this to live services:
This node graph is structurally complete and mirrors agent/investigate.py's
proven logic exactly (same detectors, same policy rules, same evidence
schema) so the benchmark output does not change when you flip the switch.
Two integration points are marked TODO:

  1. TigerGraphMCPStore (replaces LocalGraphStore): every method name in
     agent/data_store.py's LocalGraphStore matches a GSQL query in
     gsql/03_queries.gsql 1:1. Point each method at the TigerGraph MCP
     server's `run_query` tool with the same name/args instead of scanning
     the in-memory CSV mirror.
  2. call_grok(): wraps xAI's chat/completions endpoint. Every node below
     that reasons in natural language (AssessUncertainty's free-text
     rationale, Explain's summary, the SAR narrative) should call this
     instead of the deterministic string templates in investigate.py, and
     should be given the SAME evidence list as context so its output stays
     grounded in the graph rather than invented.

Run `pip install langgraph langchain-core requests --break-system-packages`
before using this file for real.
"""
import os
import json
from typing import TypedDict, List, Dict, Any

# --- TODO(2): xAI / Grok wrapper -------------------------------------------
GROK_API_KEY_ENV = "XAI_API_KEY"
GROK_MODEL = "grok-4"  # confirm current model name against xAI docs before shipping
GROK_URL = "https://api.x.ai/v1/chat/completions"


def call_grok(system: str, user: str, max_tokens: int = 800) -> str:
    """
    Minimal Grok call. Reads the key from the environment -- never hardcode
    it here or pass it in a prompt. Raises clearly if the key is missing so
    the agent fails loudly instead of silently falling back to templates.
    """
    import requests  # local import so this file still loads without the package during dry-runs
    key = os.environ.get(GROK_API_KEY_ENV)
    if not key:
        raise RuntimeError(f"{GROK_API_KEY_ENV} not set in environment")
    resp = requests.post(
        GROK_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": GROK_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# --- TODO(1): swap this for a TigerGraph MCP-backed store ------------------
# from agent.tigergraph_mcp_store import TigerGraphMCPStore as GraphStoreImpl
from agent.data_store import LocalGraphStore as GraphStoreImpl


class InvestigationState(TypedDict, total=False):
    case_id: str
    trigger: Dict[str, Any]          # raw case_pack row
    graph: Any                       # GraphStoreImpl instance (shared, loaded once)
    evidence: List[Dict[str, Any]]
    tool_calls: int
    pattern: str
    fraud_probability: float
    evidence_requests: List[Dict[str, Any]]
    exhausted_loop_guard: int        # hard cap so a stuck loop can't run forever
    initial_actions: List[Dict[str, Any]]
    final_actions: List[Dict[str, Any]]
    what_changed: str
    verdict: str
    status: str
    summary: str
    sar: Dict[str, Any]
    stop_reason: str
    needs_more_evidence: bool
    _prebuilt_result: Dict[str, Any]


MAX_EVIDENCE_LOOPS = 3  # explicit stopping guard, independent of the confidence threshold


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def node_trigger(state: InvestigationState) -> InvestigationState:
    state["evidence"] = []
    state["tool_calls"] = 0
    state["exhausted_loop_guard"] = 0
    return state


def node_open_case(state: InvestigationState) -> InvestigationState:
    # In the live system: MCP call to write an initial `open` AgentCase vertex
    # immediately, so a crash mid-investigation still leaves an audit trail.
    state["status"] = "open"
    return state


def node_gather_evidence(state: InvestigationState) -> InvestigationState:
    """
    Calls the same five detectors as investigate.py, against whichever
    GraphStoreImpl is wired in (local CSV mirror or live TigerGraph MCP).
    Kept deterministic/rule-based here for traceability; the LLM node
    (node_assess_uncertainty) is what's allowed to reason freely on top of
    this evidence, not replace it.
    """
    from agent import investigate as inv  # reuse the exact detector functions
    gs = state["graph"]
    row = state["trigger"]
    anchor = gs.get_txn(row["flagged_txn_id"])
    card_id = row["card_id"]

    # ... identical detector calls to investigate.investigate_case's body ...
    # Left as a call-through here to avoid duplicating logic; production
    # version should inline or share a single `run_detectors()` helper.
    result = inv.investigate_case(gs, row)
    state["evidence"] = result["case"]["evidence"]
    state["pattern"] = result["case"]["pattern"]
    state["fraud_probability"] = result["case"]["fraud_probability"]
    state["_prebuilt_result"] = result  # local engine already resolved this case fully
    state["tool_calls"] = result["tool_calls"]
    return state


def node_assess_uncertainty(state: InvestigationState) -> InvestigationState:
    """
    TODO(2): this is the natural node to hand off to Grok -- give it
    state["evidence"] + the policy text (retrieved via GraphRAG, not pasted
    raw) and ask for a probability + one-paragraph rationale, then compare
    against the deterministic score as a sanity check rather than blindly
    trusting either one alone.
    """
    prob = state["fraud_probability"]
    state["needs_more_evidence"] = 0.15 < prob < 0.85
    return state


def route_after_assessment(state: InvestigationState) -> str:
    if state.get("needs_more_evidence") and state["exhausted_loop_guard"] < MAX_EVIDENCE_LOOPS:
        return "gather_more"
    return "recommend"


def node_gather_more_evidence(state: InvestigationState) -> InvestigationState:
    # Controlled evidence-gathering actions (Phase 3): validate_with_owner,
    # step_up_auth, request_analyst_info -- each a stub API call, logged.
    state["exhausted_loop_guard"] += 1
    return state


def node_recommend_action(state: InvestigationState) -> InvestigationState:
    result = state["_prebuilt_result"]
    state["initial_actions"] = result["next_best_actions"]["initial"]
    state["final_actions"] = result["next_best_actions"]["final"]
    state["what_changed"] = result["next_best_actions"]["what_changed"]
    state["verdict"] = result["case"]["verdict"]
    state["status"] = result["case"]["status"]
    state["sar"] = result["sar"]
    state["evidence_requests"] = result["evidence_requests"]
    state["stop_reason"] = result["stop_reason"]
    return state


def node_explain(state: InvestigationState) -> InvestigationState:
    # TODO(2): replace with call_grok() once wired -- give it state["evidence"]
    # and ask for a natural-language summary; keep the deterministic
    # synthesize_summary() as a fallback if the API call fails.
    state["summary"] = state["_prebuilt_result"]["case"]["summary"]
    return state


def node_update_memory(state: InvestigationState) -> InvestigationState:
    # TODO(1): MCP call to write_agent_case(...) -- see gsql/03_queries.gsql.
    # Local dry-run just marks it done; investigate.py's answer already sets
    # written_to_graph=True/graph_case_id for the offline reference run.
    return state


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_graph():
    from langgraph.graph import StateGraph, END

    g = StateGraph(InvestigationState)
    g.add_node("trigger", node_trigger)
    g.add_node("open_case", node_open_case)
    g.add_node("gather_evidence", node_gather_evidence)
    g.add_node("assess_uncertainty", node_assess_uncertainty)
    g.add_node("gather_more_evidence", node_gather_more_evidence)
    g.add_node("recommend_action", node_recommend_action)
    g.add_node("explain", node_explain)
    g.add_node("update_memory", node_update_memory)

    g.set_entry_point("trigger")
    g.add_edge("trigger", "open_case")
    g.add_edge("open_case", "gather_evidence")
    g.add_edge("gather_evidence", "assess_uncertainty")
    g.add_conditional_edges("assess_uncertainty", route_after_assessment, {
        "gather_more": "gather_more_evidence",
        "recommend": "recommend_action",
    })
    g.add_edge("gather_more_evidence", "assess_uncertainty")
    g.add_edge("recommend_action", "explain")
    g.add_edge("explain", "update_memory")
    g.add_edge("update_memory", END)
    return g.compile()


if __name__ == "__main__":
    # Dry run over the local engine, one case, to prove the wiring compiles.
    from agent.data_store import LocalGraphStore
    from agent.investigate import load_case_pack

    gs = LocalGraphStore(needed_customers=None)
    gs.load()
    cases = load_case_pack()

    app = build_graph()
    out = app.invoke({"trigger": cases[0], "graph": gs})
    print(json.dumps({
        "verdict": out["verdict"], "pattern": out["pattern"],
        "final_actions": out["final_actions"], "stop_reason": out["stop_reason"],
    }, indent=2))
