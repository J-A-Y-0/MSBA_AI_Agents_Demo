from __future__ import annotations
from dotenv import load_dotenv
load_dotenv()  # must be before importing graph/agents
from tracing import init_langsmith_tracing
init_langsmith_tracing()  # must be before importing graph/agents
from graph import build_graph
import os


if __name__ == "__main__":

    app = build_graph()

    state = {
        "playbook_path": "../data-for-enhancement/SeeWeeS Specialty Dispatch Playbook.md",
        "csv_path": "../data-for-enhancement/Incoming_shipments_14d_multi_corridor.csv",
        "resource_path": "../data-for-enhancement/Resource_availability_48h.csv",
    }

    final = app.invoke(state)

    report_html = final.get("report_html", "")

    # Save report to file
    report_file = "dispatch_report.html"
    with open(report_file, "w") as f:
        f.write(report_html)

    print(f"\n=== Report saved to {report_file} ===")
    print("\n=== REPORT (first 2000 chars) ===\n")
    print(report_html[:2000])

    audit_result = final.get("audit_result", "N/A")
    audit_attempts = final.get("audit_attempts", 0)
    print(f"\n=== AUDIT SUMMARY ===")
    print(f"Final Result: {audit_result}")
    print(f"Total Attempts: {audit_attempts}")

    history = final.get("audit_history", [])
    for entry in history:
        print(f"\n  Attempt {entry['attempt']}: {entry['result']}")
        if entry.get("violations"):
            for v in entry["violations"]:
                print(f"    Violation: {v}")
        if entry.get("corrections"):
            for c in entry["corrections"]:
                print(f"    Correction: {c}")
