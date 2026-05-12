from __future__ import annotations
from typing import Dict, Any
from langchain_groq import ChatGroq
from prompts import (
    PLAYBOOK_CONTEXT_PROMPT,
    OPS_ANALYSIS_PROMPT,
    PLANNER_PROMPT,
    AUDIT_PROMPT,
    REPORT_PROMPT,
)

llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0.2,
)


def run_context_agent(playbook_text: str) -> str:
    """Extracts structured business rules from the Markdown playbook."""
    return llm.invoke(
        PLAYBOOK_CONTEXT_PROMPT.format_messages(playbook_text=playbook_text)
    ).content


def run_ops_agent(
    dq_summary: Dict[str, Any],
    corridor_kpis: Dict[str, Any],
    truck_vs_resources: Dict[str, Any],
) -> str:
    """Interprets DQ results and corridor KPIs for the planner."""
    return llm.invoke(
        OPS_ANALYSIS_PROMPT.format_messages(
            dq_summary=dq_summary,
            corridor_kpis=corridor_kpis,
            truck_vs_resources=truck_vs_resources,
        )
    ).content


def run_planner_agent(
    business_context: str,
    ops_insights: str,
    weather_risk: Dict[str, Any],
    resource_constraints: Dict[str, Any],
    audit_feedback: str = "",
) -> str:
    """Generates the 48-hour dispatch plan. Accepts audit feedback on correction loops."""
    return llm.invoke(
        PLANNER_PROMPT.format_messages(
            business_context=business_context,
            ops_insights=ops_insights,
            weather_risk=weather_risk,
            resource_constraints=resource_constraints,
            audit_feedback=audit_feedback,
        )
    ).content


def run_audit_agent(
    dq_summary: Dict[str, Any],
    weather_risk: Dict[str, Any],
    resource_constraints: Dict[str, Any],
    dispatch_plan: str,
) -> Dict[str, Any]:
    """
    Audits the dispatch plan against the 5 playbook rules.
    Returns a dict with keys: result, violations, corrections.
    """
    response = llm.invoke(
        AUDIT_PROMPT.format_messages(
            dq_summary=dq_summary,
            weather_risk=weather_risk,
            resource_constraints=resource_constraints,
            dispatch_plan=dispatch_plan,
        )
    ).content

    return _parse_audit_response(response)


def run_report_agent(
    business_context: str,
    ops_insights: str,
    dq_summary: Dict[str, Any],
    weather_risk: Dict[str, Any],
    dispatch_plan: str,
    audit_trail: str,
    report_date: str,
) -> str:
    """Generates the final HTML report."""
    return llm.invoke(
        REPORT_PROMPT.format_messages(
            business_context=business_context,
            ops_insights=ops_insights,
            dq_summary=dq_summary,
            weather_risk=weather_risk,
            dispatch_plan=dispatch_plan,
            audit_trail=audit_trail,
            report_date=report_date,
        )
    ).content


def _parse_audit_response(response: str) -> Dict[str, Any]:
    """
    Parses the structured RESULT / VIOLATIONS / CORRECTIONS output
    from the AuditAgent into a Python dict.
    """
    result = "FAIL"
    violations = []
    corrections = []

    current_section = None

    for line in response.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith("RESULT:"):
            val = line.replace("RESULT:", "").strip().upper()
            result = val if val in ("PASS", "FAIL", "PASS_WITH_WARNING") else "FAIL"

        elif line.startswith("VIOLATIONS:"):
            current_section = "violations"
            inline = line.replace("VIOLATIONS:", "").strip()
            if inline and inline.lower() != "none":
                violations.append(inline)

        elif line.startswith("CORRECTIONS:"):
            current_section = "corrections"
            inline = line.replace("CORRECTIONS:", "").strip()
            if inline and inline.lower() != "none":
                corrections.append(inline)

        elif current_section == "violations":
            if line.lower() == "none":
                pass
            elif line.startswith("-"):
                violations.append(line.lstrip("-").strip())
            else:
                violations.append(line)

        elif current_section == "corrections":
            if line.lower() == "none":
                pass
            elif line.startswith("-"):
                corrections.append(line.lstrip("-").strip())
            else:
                corrections.append(line)

    return {
        "result": result,
        "violations": violations,
        "corrections": corrections,
    }
