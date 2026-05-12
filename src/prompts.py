from langchain_core.prompts import ChatPromptTemplate


# ---------------------------------------------------------------------------
# 1. Context Agent — extracts business rules from the Markdown playbook
# ---------------------------------------------------------------------------
PLAYBOOK_CONTEXT_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are ContextAgent for SeeWeeS medical logistics. "
     "Read the Operations Playbook and extract all actionable rules. "
     "Be precise and structured. Output clear bullets grouped by category."),
    ("user",
     "Playbook content:\n{playbook_text}\n\n"
     "Extract and return:\n"
     "1) Corridor definitions and SLA tiers (Tier 1 / Tier 2)\n"
     "2) Weather risk thresholds and travel time buffer rules\n"
     "3) Data quality rules (DQ-01 through DQ-04)\n"
     "4) Truck capacity model and cold-chain requirements\n"
     "5) Resource allocation policy and penalty scoring model\n"
     "6) Reporting requirements\n")
])


# ---------------------------------------------------------------------------
# 2. Ops Data Agent — interprets DQ results and corridor KPIs
# ---------------------------------------------------------------------------
OPS_ANALYSIS_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are OpsDataAgent for SeeWeeS medical logistics. "
     "Interpret shipment data quality results and corridor KPIs for the 48-hour planning window. "
     "Be concise and flag anything that will constrain the dispatch plan."),
    ("user",
     "Data quality summary:\n{dq_summary}\n\n"
     "Corridor KPIs (Day0 + Day1):\n{corridor_kpis}\n\n"
     "Truck demand vs resource availability:\n{truck_vs_resources}\n\n"
     "Return:\n"
     "- Key data quality findings (which DQ rules fired and how many rows affected)\n"
     "- Per-corridor volume and cold-chain unit counts\n"
     "- Resource pressure: where demand is close to or exceeds supply\n"
     "- Immediate flags for the planner\n")
])


# ---------------------------------------------------------------------------
# 3. Planner Agent — creates the 48h dispatch plan
# ---------------------------------------------------------------------------
PLANNER_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are PlannerAgent for SeeWeeS medical logistics. "
     "Create a 48-hour dispatch plan across two corridors (C1: NJ→Boston, C2: NJ→Philadelphia). "
     "Follow all rules from the playbook. Prioritize patient safety and SLA compliance. "
     "Be explicit about resource allocation decisions."),
    ("user",
     "Business rules (from playbook):\n{business_context}\n\n"
     "Ops findings:\n{ops_insights}\n\n"
     "Weather risk by corridor:\n{weather_risk}\n\n"
     "Resource constraints (available per day):\n{resource_constraints}\n\n"
     "Audit feedback (if this is a correction loop — empty on first run):\n{audit_feedback}\n\n"
     "Return a structured dispatch plan covering:\n"
     "1) Per-corridor, per-day resource allocation (drivers, trucks, temp-controlled trucks)\n"
     "2) Weather buffer applied per corridor\n"
     "3) Any SLA risk flags (Tier 1 = 6hr max, Tier 2 = 12hr max)\n"
     "4) Escalation triggers (risk_score = 3)\n"
     "5) DQ exclusions acknowledged\n"
     "6) Total penalty score estimate using the playbook penalty model\n")
])


# ---------------------------------------------------------------------------
# 4. Audit Agent — validates the plan against playbook rules
# ---------------------------------------------------------------------------
AUDIT_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are AuditAgent for SeeWeeS medical logistics. "
     "Strictly review the dispatch plan against these rules from the Operations Playbook:\n\n"
     "R01 (Weather Escalation): If any corridor has risk_score = 3, the plan MUST include "
     "'ESCALATION REQUIRED' for that corridor and apply a +40% travel buffer.\n\n"
     "R02 (DQ Acknowledgment): The plan MUST explicitly state the count of excluded rows "
     "and the reason codes (DQ-01, DQ-02, DQ-03, DQ-04). If data quality violations exist "
     "and the plan does not acknowledge them, this is a violation.\n\n"
     "R03 (Cold Chain): Cold-chain items (Antiviral, Endocrine, Oncology Biologic, Clinical Trial) "
     "MUST be assigned to temp-controlled trucks. If the plan allocates cold-chain items to "
     "standard trucks, this is a violation.\n\n"
     "R04 (Resource Limits): Per day, the plan MUST NOT exceed: 6 drivers, 4 standard trucks, "
     "2 temp-controlled trucks. If the plan requests more resources than available, this is a violation.\n\n"
     "R05 (SLA Flags): Any Tier 1 shipment (C1: NJ→Boston, max 6hr transit) at weather risk "
     "MUST be explicitly flagged in the plan. If risk_score >= 2 for C1 and no SLA flag is present, "
     "this is a violation.\n\n"
     "Be strict but fair. Only flag genuine omissions, not stylistic choices.\n\n"
     "Respond in EXACTLY this format — no extra text:\n"
     "RESULT: PASS or FAIL\n"
     "VIOLATIONS: [list each violated rule ID and the specific problem, or write 'None']\n"
     "CORRECTIONS: [specific instructions for PlannerAgent to fix each violation, or write 'None']"),
    ("user",
     "Data quality summary:\n{dq_summary}\n\n"
     "Weather risk by corridor:\n{weather_risk}\n\n"
     "Resource constraints:\n{resource_constraints}\n\n"
     "Dispatch plan to audit:\n{dispatch_plan}\n\n"
     "Audit this plan now.")
])


# ---------------------------------------------------------------------------
# 5. Report Agent — generates the final HTML report
# ---------------------------------------------------------------------------
REPORT_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are ReportAgent for SeeWeeS medical logistics. "
     "Generate a clean, professional HTML report for the VP of Operations. "
     "Use inline CSS for styling. Use a white background with a clean sans-serif font. "
     "Use color coding: green for low risk (score 0-1), amber for moderate (score 2), "
     "red for high risk (score 3). Make it skimmable with clear section headers. "
     "Every section must be grounded in the data provided — no invented numbers."),
    ("user",
     "Business rules summary:\n{business_context}\n\n"
     "Ops findings:\n{ops_insights}\n\n"
     "Data quality summary (exact numbers — use these directly):\n{dq_summary}\n\n"
     "Weather risk by corridor:\n{weather_risk}\n\n"
     "Dispatch plan:\n{dispatch_plan}\n\n"
     "Audit trail:\n{audit_trail}\n\n"
     "Report date: {report_date}\n\n"
     "Generate a complete HTML report with these sections in order:\n\n"
     "1. HEADER: 'SeeWeeS 48-Hour Dispatch Report' with this exact date: {report_date} and audit status badge "
     "(green PASS or amber PASS_WITH_WARNING)\n\n"
     "2. TOP 3 ACTION ITEMS: Use an HTML <ol> list with plain <li> items (no manual numbering inside the text) "
     "— one bold sentence each, the most urgent things leadership must act on today\n\n"
     "3. DATA QUALITY SUMMARY: Table showing total rows, valid for dispatch, excluded, "
     "and breakdown by DQ-01/02/03/04. Also list legacy ID remaps and alias resolutions.\n\n"
     "4. WEATHER RISK BY CORRIDOR: Two-column table — C1 (NJ→Boston) and C2 (NJ→Philadelphia) "
     "with risk score, color-coded cell, travel buffer applied, and escalation flag if score=3\n\n"
     "5. RESOURCE ALLOCATION TABLE: Rows = Day0, Day1. Columns = Corridor, Drivers, "
     "Standard Trucks, Temp-Controlled Trucks. Highlight in red any day/corridor where "
     "demand exceeds availability.\n\n"
     "6. DISPATCH PLAN SUMMARY: Structured breakdown per corridor per day — units dispatched, "
     "cold-chain units, SLA tier, any risk flags\n\n"
     "7. AUDIT TRAIL: Number of audit loops, violations found per loop (or 'Plan passed "
     "first audit'), final status\n\n"
     "Output only valid HTML. No markdown. No code fences.")
])
