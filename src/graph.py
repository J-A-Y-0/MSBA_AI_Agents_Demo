from __future__ import annotations
import os
from datetime import datetime
from typing import TypedDict, Dict, Any, List, Optional

from langgraph.graph import StateGraph, END
from dotenv import load_dotenv

from tools.csv_tools import analyze_csv
from tools.weather_tools import get_corridor_weather_risk
from tools.email_tools import send_email_smtp
from agents import (
    run_context_agent,
    run_ops_agent,
    run_planner_agent,
    run_audit_agent,
    run_report_agent,
)

load_dotenv()

MAX_AUDIT_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
class AppState(TypedDict, total=False):
    # Input paths
    playbook_path: str
    csv_path: str
    resource_path: str

    # Context
    business_context: str

    # CSV analysis outputs
    dq_summary: Dict[str, Any]
    corridor_kpis: Dict[str, Any]
    truck_requirements: Dict[str, Any]
    ops_insights: str

    # Resource constraints
    resource_constraints: Dict[str, Any]

    # Weather
    weather_risk: Dict[str, Any]

    # Planner
    dispatch_plan: str
    audit_feedback: str

    # Audit loop
    audit_result: str          # "PASS", "FAIL", "PASS_WITH_WARNING"
    audit_violations: List[str]
    audit_corrections: List[str]
    audit_attempts: int
    audit_history: List[Dict[str, Any]]   # record of all audit loops

    # Report
    report_html: str


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def node_playbook_context(state: AppState) -> AppState:
    """Loads the Markdown playbook as plain text and extracts business rules."""
    playbook_path = state["playbook_path"]
    with open(playbook_path, "r", encoding="utf-8") as f:
        playbook_text = f.read()

    business_context = run_context_agent(playbook_text)
    return {"business_context": business_context}


def node_csv_analysis(state: AppState) -> AppState:
    """Loads multi-corridor CSV, applies DQ checks, computes corridor KPIs."""
    result = analyze_csv(
        csv_path=state["csv_path"],
        resource_path=state.get("resource_path"),
    )

    # Build a human-readable truck demand vs supply summary for the ops agent
    truck_vs_resources: Dict[str, Any] = {}
    for day, demand in result.truck_requirements.items():
        supply = result.resource_constraints.get(day, {})
        truck_vs_resources[day] = {
            "demand": demand,
            "supply": supply,
            "temp_controlled_gap": (
                demand.get("temp_controlled_trucks_needed", 0)
                - supply.get("truck_temp_controlled", 0)
            ),
            "standard_gap": (
                demand.get("standard_trucks_needed", 0)
                - supply.get("truck_standard", 0)
            ),
            "driver_gap": (
                demand.get("drivers_needed", 0)
                - supply.get("driver", 0)
            ),
        }

    ops_insights = run_ops_agent(
        dq_summary=result.dq_summary,
        corridor_kpis=result.corridor_kpis,
        truck_vs_resources=truck_vs_resources,
    )

    return {
        "dq_summary": result.dq_summary,
        "corridor_kpis": result.corridor_kpis,
        "truck_requirements": result.truck_requirements,
        "resource_constraints": result.resource_constraints,
        "ops_insights": ops_insights,
    }


def node_weather(state: AppState) -> AppState:
    """Fetches weather risk for all waypoints across both corridors."""
    tz = os.getenv("WEATHER_TZ", "America/New_York")
    weather_risk = get_corridor_weather_risk(tz=tz)
    return {"weather_risk": weather_risk}


def node_planner(state: AppState) -> AppState:
    """Generates or corrects the 48-hour dispatch plan."""
    plan = run_planner_agent(
        business_context=state.get("business_context", ""),
        ops_insights=state.get("ops_insights", ""),
        weather_risk=state.get("weather_risk", {}),
        resource_constraints=state.get("resource_constraints", {}),
        audit_feedback=state.get("audit_feedback", ""),
    )
    return {"dispatch_plan": plan}


def node_audit(state: AppState) -> AppState:
    """Audits the dispatch plan. Loops back to planner on FAIL (max 3 attempts)."""
    attempts = state.get("audit_attempts", 0)
    history  = state.get("audit_history", [])

    audit = run_audit_agent(
        dq_summary=state.get("dq_summary", {}),
        weather_risk=state.get("weather_risk", {}),
        resource_constraints=state.get("resource_constraints", {}),
        dispatch_plan=state.get("dispatch_plan", ""),
    )

    # If this is the final attempt and still failing, force PASS_WITH_WARNING
    if audit["result"] == "FAIL" and (attempts + 1) >= MAX_AUDIT_ATTEMPTS:
        audit["result"] = "PASS_WITH_WARNING"
        audit["violations"].append("Max audit attempts reached — manual review required")

    history.append({
        "attempt": attempts + 1,
        "result": audit["result"],
        "violations": audit["violations"],
        "corrections": audit["corrections"],
    })

    # Build feedback string for planner on next loop
    feedback = ""
    if audit["result"] == "FAIL" and audit["corrections"]:
        feedback = "CORRECTION REQUIRED — previous plan failed audit.\n"
        feedback += "Violations found:\n" + "\n".join(f"  - {v}" for v in audit["violations"])
        feedback += "\n\nRequired fixes:\n" + "\n".join(f"  - {c}" for c in audit["corrections"])

    return {
        "audit_result": audit["result"],
        "audit_violations": audit["violations"],
        "audit_corrections": audit["corrections"],
        "audit_attempts": attempts + 1,
        "audit_history": history,
        "audit_feedback": feedback,
    }


def node_report(state: AppState) -> AppState:
    """Generates the final HTML report including the audit trail."""
    # Build audit trail string for the report
    history = state.get("audit_history", [])
    if not history:
        audit_trail = "No audit history available."
    else:
        lines = []
        for entry in history:
            lines.append(f"Attempt {entry['attempt']}: {entry['result']}")
            if entry["violations"]:
                for v in entry["violations"]:
                    lines.append(f"  Violation: {v}")
            if entry["corrections"]:
                for c in entry["corrections"]:
                    lines.append(f"  Correction: {c}")
        audit_trail = "\n".join(lines)

    html = run_report_agent(
        business_context=state.get("business_context", ""),
        ops_insights=state.get("ops_insights", ""),
        dq_summary=state.get("dq_summary", {}),
        weather_risk=state.get("weather_risk", {}),
        dispatch_plan=state.get("dispatch_plan", ""),
        audit_trail=audit_trail,
        report_date=datetime.now().strftime("%B %d, %Y"),
    )
    return {"report_html": html}


def node_email(state: AppState) -> AppState:
    """Sends the HTML report by email if REPORT_EMAIL_TO is configured."""
    to_email = os.getenv("REPORT_EMAIL_TO", "").strip()
    if not to_email:
        print("REPORT_EMAIL_TO not set → skipping email.")
        return {}
    send_email_smtp(
        subject="SeeWeeS 48-Hour Dispatch Report",
        html_body=state["report_html"],
        to_email=to_email,
    )
    return {}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route_after_audit(state: AppState) -> str:
    """Returns 'planner' to loop back on FAIL, or 'report' to proceed."""
    if state.get("audit_result") == "FAIL" and state.get("audit_attempts", 0) < MAX_AUDIT_ATTEMPTS:
        print(f"  [Audit] FAIL — looping back to planner (attempt {state['audit_attempts']})")
        return "planner"
    print(f"  [Audit] {state.get('audit_result')} — proceeding to report")
    return "report"


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def build_graph():
    g = StateGraph(AppState)

    g.add_node("playbook_context", node_playbook_context)
    g.add_node("csv_analysis",     node_csv_analysis)
    g.add_node("weather",          node_weather)
    g.add_node("planner",          node_planner)
    g.add_node("audit",            node_audit)
    g.add_node("report",           node_report)
    g.add_node("email",            node_email)

    g.set_entry_point("playbook_context")
    g.add_edge("playbook_context", "csv_analysis")
    g.add_edge("csv_analysis",     "weather")
    g.add_edge("weather",          "planner")
    g.add_edge("planner",          "audit")

    # The audit loop — conditional edge
    g.add_conditional_edges(
        "audit",
        route_after_audit,
        {"planner": "planner", "report": "report"},
    )

    g.add_edge("report", "email")
    g.add_edge("email",  END)

    return g.compile()
