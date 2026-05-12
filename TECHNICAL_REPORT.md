# SeeWeeS Multi-Agent Dispatch System
## Technical & Business Report
**UCLA MSBA AI Agents Project Challenge 2026**

---

## Executive Summary

**Stakeholder:** VP of Operations, SeeWeeS Medical Logistics

**Operational Pain Point:**
The existing SeeWeeS Ops Reporting Agent is a linear system — it retrieves shipment data and the operations playbook, generates a dispatch plan, and produces an HTML report in a single pass. This architecture has a critical blind spot: if the PlannerAgent produces a plan that violates a safety rule (e.g., failing to escalate a high-risk corridor, omitting data quality acknowledgments, or misallocating cold-chain resources), that violation goes straight into the final report with no checks. In time-critical medical logistics, a non-compliant plan reaching leadership without correction is a patient safety risk.

**Solution:**
We extended the LangGraph architecture to include a self-correcting Audit Loop. A new `AuditAgent` node sits between the `PlannerAgent` and `ReportAgent` and validates every dispatch plan against five rules extracted directly from the SeeWeeS Operations Playbook. If the plan fails, the system loops back to the `PlannerAgent` with specific correction instructions — up to three times — before producing the final report. The report itself now includes an audit trail, exact data quality metrics, per-corridor weather risk, and a color-coded resource allocation table.

**Business Impact:**
- Every dispatch plan is now systematically verified against the company's own safety protocols before reaching leadership
- Data quality violations (missing IDs, name mismatches, legacy identifiers) are detected, resolved, and reported automatically
- Resource constraint breaches are surfaced visually in the report before any truck leaves the warehouse
- The system reduces the risk of a non-compliant plan reaching the VP of Operations to near zero within the 48-hour planning window

---

## Key Assumptions

**1. Playbook as ground truth**
The `SeeWeeS Specialty Dispatch Playbook.md` is treated as the single authoritative source of business rules. All five audit rules, SLA tiers, truck capacity formulas, weather risk thresholds, and the resource allocation penalty model are derived directly from this document. No rules were invented.

**2. Weather data source**
Live weather risk is fetched from the Open-Meteo API (free, no key required) at each corridor waypoint. The playbook defines 5 waypoints for C1 (NJ→Boston) and 4 for C2 (NJ→Philadelphia). Corridor risk is computed as the maximum waypoint score across both forecast days, consistent with the playbook's aggregation policy (§5.1).

**3. Item Master reconciliation**
The canonical Item Master from Playbook Appendix A is embedded in `csv_tools.py` as a lookup table. Row-level DQ checks follow the decision rules in Appendix A.6 exactly: exact match → EXACT_MATCH, alias match → ALIAS_MATCH, legacy ID → LEGACY_ID_MAP, unresolvable → excluded (DQ-02).

**4. Resource file is authoritative**
Daily resource availability (6 drivers, 4 standard trucks, 2 temp-controlled trucks per day) is read directly from `Resource_availability_48h.csv`. The planner is given this data and the audit validates against it.

**5. Planning window**
Only rows where `is_planning_window = 1` (Day0 and Day1) are used for dispatch planning. Historical rows (`is_planning_window = 0`) provide trend context but do not affect resource allocation.

---

## Technical Methodology

### Architectural Enhancements

**Original flow:**
```
[pdf_context] → [csv_analysis] → [weather] → [planner] → [report] → [email]
```

**New flow:**
```
[playbook_context] → [csv_analysis] → [weather] → [planner] → [audit] → [report] → [email]
                                                       ↑            |
                                                       └── FAIL ────┘
                                                     (loop, max 3x)
```

**Changes made to the existing system:**

| Component | Change |
|---|---|
| `pdf_context` node | Replaced Chroma RAG (PDF + vector embeddings) with a plain text loader for the Markdown playbook. Removes the `chromadb` dependency and passes the full playbook in context. |
| `csv_analysis` node | Completely rewritten to handle the multi-corridor schema, apply Item Master DQ checks, and compute corridor-level KPIs and truck requirements. |
| `weather` node | Updated to loop over all 9 corridor waypoints (5 for C1, 4 for C2) and return per-corridor risk scores instead of a single location risk. |
| `planner` node | Updated prompt to handle two corridors, resource constraints, and accept audit feedback on correction loops. |
| `audit` node | **New.** Validates dispatch plan against 5 playbook rules. Returns PASS or FAIL with violations and corrections. Conditional edge loops back to planner on FAIL. |
| `report` node | Enhanced prompt produces 7-section HTML report including DQ summary, weather risk, resource allocation, dispatch summary, and audit trail. |
| `AppState` | Added 7 new fields: `dq_summary`, `corridor_kpis`, `truck_requirements`, `resource_constraints`, `audit_result`, `audit_violations`, `audit_history`. |

### New Node: AuditAgent

The `AuditAgent` is the core of this enhancement. It receives the dispatch plan, the data quality summary, the weather risk per corridor, and the resource constraints — and validates against five rules:

| Rule | Source | What is Checked |
|---|---|---|
| R01 | Playbook §6, Travel Time Buffer | risk_score = 3 must trigger escalation + 40% buffer in plan |
| R02 | Playbook §11, DQ-01 to DQ-04 | DQ violation counts must be acknowledged in the plan |
| R03 | Playbook §8, Truck Capacity | Cold-chain items must be assigned to temp-controlled trucks |
| R04 | Playbook §13, Resource Constraints | Resource limits (6 drivers, 4 std, 2 temp-controlled/day) must not be exceeded |
| R05 | Playbook §7, SLA Classes | Tier 1 shipments (C1, max 6hr) at weather risk must be explicitly flagged |

**Correction loop logic:**
```
attempt 1: audit → FAIL → feedback sent to planner → planner generates corrected plan
attempt 2: audit → FAIL → feedback sent to planner → planner generates corrected plan
attempt 3: audit → FAIL → PASS_WITH_WARNING (force through, flag for manual review)
         : audit → PASS → proceed to report
```

Correction feedback is structured: the AuditAgent outputs specific rule IDs and exact instructions for the PlannerAgent, not generic guidance.

### Data Quality Pipeline

The `csv_tools.py` Item Master reconciliation pipeline processes every row in the shipment CSV as follows:

```
Row arrives
  │
  ├─ item_id in LEGACY_ID_MAP? → remap, flag LEGACY_ID_MAP
  │
  ├─ item_id in canonical master? → EXACT_MATCH
  │
  ├─ item_name in alias map? → remap item_id, flag ALIAS_MATCH
  │
  └─ none of the above → flag DQ-02 (excluded from dispatch)

Then:
  ├─ unique_item_id missing? → flag DQ-01 (excluded from dispatch)
  ├─ unique_item_id duplicated? → flag DQ-04
  └─ item_name doesn't match canonical? → flag DQ-03
```

A row is excluded from dispatch only if it has DQ-01 (missing unique ID) or DQ-02 (unresolvable item). All other flags are reported but the row is dispatched.

### KPI Definitions

**Composite Truck Requirement** (per corridor, per day):
```
cold_chain_units     = count of rows where temp_control ≠ "Room Temp"
room_temp_units      = total_valid_units − cold_chain_units
temp_trucks_needed   = ⌈(cold_chain_units × 1.10) / 10⌉
standard_trucks_needed = ⌈(room_temp_units × 1.10) / 10⌉
drivers_needed       = temp_trucks_needed + standard_trucks_needed
```
Source: Playbook §8 (truck capacity = 10 units, packing buffer = 10%)

**Resource Pressure Score** (per day):
```
temp_controlled_gap = temp_trucks_needed (all corridors) − 2 (available)
standard_gap        = standard_trucks_needed (all corridors) − 4 (available)
driver_gap          = drivers_needed (all corridors) − 6 (available)
```
Any positive gap = resource constraint violation flagged in report.

**Weather Risk Score** (0–3 per corridor):
```
For each waypoint, for each forecast day:
  score += 1 if precipitation_sum ≥ 15 mm/day
  score += 1 if wind_gusts_10m_max ≥ 45 km/h
  score += 1 if temperature_2m_min ≤ 0°C
corridor_risk = max(waypoint_scores across all waypoints and both days)
```
Source: Playbook §6

**Penalty Score** (resource allocation objective, Playbook §13.2):
```
Tier 1 SLA violation:       100 pts/unit
Tier 2 SLA violation:        40 pts/unit
Cold-chain violation:        +80 pts/unit (additional)
Non-SLA delay:               10 pts/unit
Allocation objective: minimize total penalty score; on tie, minimize Tier 1 units affected
```

---

## Results & Validation

### Run Output Summary

| Metric | Value |
|---|---|
| Total shipment rows processed | 129 |
| Valid for dispatch | 124 |
| Excluded (DQ-01 missing unique ID) | 5 |
| Legacy ID remaps applied | 2 |
| Alias name resolutions applied | 14 |
| Audit loops (normal run) | 1 — passed first attempt |
| Audit loops (validation run) | 2 — FAIL → self-correction → PASS |
| Final audit status | PASS |

### Primary Run Log

The following terminal output is from a representative end-to-end run. It demonstrates successful pipeline execution, correct DQ metrics, and a first-attempt audit pass:

```
(venv) MacBook-Air-3:src micheal$ python main.py
  [Audit] PASS — proceeding to report
REPORT_EMAIL_TO not set → skipping email.

=== Report saved to dispatch_report.html ===

DATA QUALITY (from HTML report):
  Total Rows:                  129
  Valid for Dispatch:          124
  Excluded from Dispatch:        5
  DQ-01 Missing Unique ID:       5
  DQ-02 Invalid Item ID:         0
  DQ-03 Name Mismatch:           0
  DQ-04 Duplicate Unique ID:     0
  Legacy ID Remaps:              2
  Alias Name Resolutions:       14

WEATHER RISK (from HTML report):
  C1 (NJ→Boston):        Risk Score 1  →  +10% travel buffer  →  No escalation
  C2 (NJ→Philadelphia):  Risk Score 1  →  +10% travel buffer  →  No escalation

=== AUDIT SUMMARY ===
Final Result: PASS
Total Attempts: 1

  Attempt 1: PASS
```

**Significance:** The pipeline processed all 129 shipment rows, automatically resolved 14 name alias variants and 2 legacy IDs without human intervention, correctly excluded 5 rows with missing unique identifiers (DQ-01), and produced a compliant 48-hour dispatch plan in a single audit pass. Live weather was fetched for all 9 corridor waypoints via Open-Meteo, yielding a risk score of 1 for both corridors and a +10% travel time buffer per Playbook §6.

### Self-Correction Loop Validation Log

The following log shows the audit loop triggering a correction — the core behavior of the enhancement. The AuditAgent detected a violation in the PlannerAgent's first output and routed execution back with specific correction instructions before the report was generated:

```
(venv) MacBook-Air-3:src micheal$ python main.py
  [Audit] FAIL — looping back to planner (attempt 1)
  [Audit] PASS — proceeding to report
REPORT_EMAIL_TO not set → skipping email.

=== Report saved to dispatch_report.html ===

=== AUDIT SUMMARY ===
Final Result: PASS
Total Attempts: 2
```

**Significance:** On this run, the AuditAgent evaluated the PlannerAgent's first dispatch plan against the five playbook rules and returned FAIL. The system automatically routed execution back to the PlannerAgent with structured correction instructions (rule ID + specific fix required). The second plan passed the audit. The VP of Operations receives a report that has been verified against the company's own safety protocols — not a raw, unchecked LLM output. This is the safety guarantee the enhancement was designed to provide.

### Data Quality Findings
- **14 alias resolutions**: The system automatically resolved name variants including "Heparin Na" → Heparin Sodium, "EpiPen Auto Injector" → Epinephrine Auto-Injector, "Morphine Sulphate" → Morphine Sulfate, "Pembrolizumab (Keytruda)" → Pembrolizumab, and others
- **2 legacy ID remaps**: Item IDs `20021` and `1070` were correctly remapped via the Legacy ID Map in Appendix A.3
- **5 DQ-01 exclusions**: 5 rows with missing `unique_item_id` were excluded from dispatch planning and logged with reason code DQ-01

### Weather Risk
Both corridors returned a risk score of 1 (moderate), resulting in a +10% travel buffer applied to both C1 (NJ→Boston) and C2 (NJ→Philadelphia). No escalation was required (score < 3).

### Resource Allocation
Standard truck demand exceeded availability across all four day/corridor combinations — a legitimate operational constraint surfaced by the system and highlighted in red in the executive report. This is the primary action item for the VP of Operations.

### Validation Scenario Results

| Scenario | Setup | Result |
|---|---|---|
| Normal run | Full pipeline, real data | Audit PASS on attempt 1 |
| Self-correction loop | Non-deterministic planner output triggering rule violation | Audit FAIL → planner correction → PASS on attempt 2 |
| DQ violations | 5 missing unique IDs in data | Correctly detected, excluded, and reported |
| Legacy ID resolution | item_ids 20021 and 1070 in data | Correctly remapped via Item Master |
| Alias resolution | 14 name variants in data | All resolved to canonical names |
| Resource constraint | Standard truck demand > 4/day | Flagged red in report, surfaced as Action Item |

---

## Limitations & Next Steps

**Current limitations:**

1. **Weather data is real but not corridor-optimized** — Open-Meteo returns a single daily aggregate per waypoint. In production, hourly forecasts with route-segment timing would give more precise risk windows.

2. **Audit rules are in the prompt** — The five audit rules are embedded in the LLM prompt string. A business rule change requires a code edit. In production, audit rules should be externalized to `audit_rules.json` so the operations team can update thresholds without touching code.

3. **Single-run state** — The system has no persistent memory across runs. Each invocation starts fresh. A production system would store run history in a database to enable period-over-period comparison and trend analysis.

4. **LLM audit is probabilistic** — The AuditAgent uses an LLM to evaluate the plan, which means its judgments are not 100% deterministic. For high-stakes rules (e.g., resource limits), a production system would add a deterministic pre-check in Python before the LLM audit, ensuring hard constraints are never missed.

5. **Email delivery is optional** — SMTP is configured but not tested. In production, the report would be delivered via a secured internal dashboard rather than email.

**Next steps with more time:**

- Add a deterministic rule-checker layer before the LLM audit for hard numeric constraints (resource limits, SLA thresholds)
- Externalize audit rules to a JSON config file
- Add a SQLite checkpointer to persist state across runs and enable trend analysis
- Connect to a live hospital EHR or CRM for real-time hospital priority data
- Replace the CLI runner with a FastAPI endpoint so the system can be triggered by a scheduler or external event

---

*Report prepared: May 2026*
*Team: M#4 — UCLA MSBA AI Agents Project Challenge 2026*
