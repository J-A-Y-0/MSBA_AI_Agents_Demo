# SeeWeeS Multi-Agent Dispatch System
**UCLA MSBA AI Agents Project Challenge 2026**

A multi-agent LangGraph system for 48-hour medical logistics dispatch planning across two delivery corridors (NJ→Boston, NJ→Philadelphia), with a self-correcting audit loop that validates every dispatch plan against the SeeWeeS Operations Playbook before generating the final executive report.

---

## Architecture

```
[playbook_context] → [csv_analysis] → [weather] → [planner] → [audit] → [report] → [email]
                                                       ↑            |
                                                       └── FAIL ────┘
                                                     (loop, max 3x)
```

| Node | Role |
|---|---|
| `playbook_context` | Loads Markdown playbook as plain text; extracts business rules |
| `csv_analysis` | Loads multi-corridor CSV; applies DQ-01→04 checks; computes corridor KPIs |
| `weather` | Fetches live weather for all 9 corridor waypoints via Open-Meteo; returns per-corridor risk |
| `planner` | Generates 48-hour dispatch plan across both corridors |
| `audit` | **New** — validates plan against 5 playbook rules; loops back to planner on FAIL |
| `report` | Generates HTML executive report with audit trail |
| `email` | Sends report by email (optional) |

---

## Setup

### 1. Create a virtual environment
```bash
python -m venv venv
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure environment
The `.env` file is pre-configured. To use your own API key edit `.env`:
```
GROQ_API_KEY="your_groq_api_key_here"
```
Get a free key at: https://console.groq.com

### 4. Run the system
```bash
cd src
python main.py
```

The report is saved to `src/dispatch_report.html`. Open it in any browser.

---

## Output

The HTML report contains:
1. **Top 3 Action Items** — urgent decisions for leadership
2. **Data Quality Summary** — valid vs excluded units, DQ violation breakdown
3. **Weather Risk by Corridor** — color-coded risk scores + travel buffers applied
4. **Resource Allocation Table** — drivers/trucks per corridor per day
5. **Dispatch Plan Summary** — per-corridor unit counts and SLA flags
6. **Audit Trail** — number of loops, violations caught and corrected

---

## Audit Rules (from Playbook)

| Rule | Description |
|---|---|
| R01 | risk_score = 3 → plan must include escalation + 40% travel buffer |
| R02 | DQ violations must be counted and acknowledged in the plan |
| R03 | Cold-chain items must be assigned to temp-controlled trucks |
| R04 | Resource limits must not be exceeded (6 drivers, 4 std trucks, 2 temp-controlled/day) |
| R05 | Tier 1 SLA shipments at weather risk must be explicitly flagged |

---

## Data Sources

| File | Description |
|---|---|
| `data-for-enhancement/Incoming_shipments_14d_multi_corridor.csv` | 14-day shipment feed, 2 corridors, intentional DQ issues |
| `data-for-enhancement/Resource_availability_48h.csv` | Driver and truck availability by day |
| `data-for-enhancement/SeeWeeS Specialty Dispatch Playbook.md` | Authoritative business rules and Item Master |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | Yes | Groq API key (free at console.groq.com) |
| `WEATHER_TZ` | No | Timezone for weather API (default: America/New_York) |
| `REPORT_EMAIL_TO` | No | Email to send report to (leave blank to skip) |
| `LANGCHAIN_TRACING_V2` | No | Set `true` to enable LangSmith tracing |
