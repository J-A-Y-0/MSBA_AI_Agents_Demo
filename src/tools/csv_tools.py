from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, List
import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# Item Master — canonical truth table from playbook Appendix A
# ---------------------------------------------------------------------------
CANONICAL_ITEM_MASTER = {
    10021: {"canonical_item_id": "RMD",    "item_name": "Remdesivir",          "temp_control": "Cold (2-8C)",          "product_class": "Antiviral"},
    10022: {"canonical_item_id": "INS-LIS","item_name": "Insulin Lispro",       "temp_control": "Cold (2-8C)",          "product_class": "Endocrine"},
    10023: {"canonical_item_id": "INS-ASP","item_name": "Insulin Aspart",       "temp_control": "Cold (2-8C)",          "product_class": "Endocrine"},
    10035: {"canonical_item_id": "PMB-KEY","item_name": "Pembrolizumab",        "temp_control": "Cold (2-8C)",          "product_class": "Oncology Biologic"},
    10040: {"canonical_item_id": "EPI-AI", "item_name": "Epinephrine Auto-Injector","temp_control": "Room Temp (20-25C)","product_class": "Emergency"},
    10050: {"canonical_item_id": "HEP-SOD","item_name": "Heparin Sodium",       "temp_control": "Room Temp (20-25C)",  "product_class": "Anticoagulant"},
    10060: {"canonical_item_id": "MOR-SUL","item_name": "Morphine Sulfate",     "temp_control": "Controlled Storage",  "product_class": "Controlled"},
    10070: {"canonical_item_id": "ALB-INH","item_name": "Albuterol Inhaler",    "temp_control": "Room Temp (20-25C)",  "product_class": "Respiratory"},
    10071: {"canonical_item_id": "LEV-INH","item_name": "Levalbuterol Inhaler", "temp_control": "Room Temp (20-25C)",  "product_class": "Respiratory"},
    99999: {"canonical_item_id": "EXP-ONC","item_name": "Experimental Oncology Drug","temp_control": "Strict Cold Chain (-20C)","product_class": "Clinical Trial"},
}

# Legacy item_id → canonical item_id mapping (Appendix A.3)
LEGACY_ID_MAP = {
    10020: 10021,   # Vendor legacy ID for Remdesivir 100mg
    20021: 10021,   # Old system used 200xx for strength variants
    1070:  10070,   # Truncated ID
}

# Name alias → item_id mapping (Appendix A.2)
NAME_ALIAS_MAP = {
    "remdesivir 100 mg":            10021,
    "remdesivir 200 mg":            10021,
    "pembrolizumab (keytruda)":     10035,
    "epipen auto injector":         10040,
    "heparin na":                   10050,
    "morphine sulphate":            10060,
    "albuterol inhaler 90mcg":      10070,
}

# Cold-chain product classes (require temp-controlled trucks)
COLD_CHAIN_CLASSES = {"Antiviral", "Endocrine", "Oncology Biologic", "Clinical Trial"}

# Truck capacity constants (Playbook §8)
TRUCK_CAPACITY_UNITS = 10
PACKING_BUFFER = 1.10

# SLA tiers (Playbook §7)
SLA_TIERS = {
    "C1_I95_NJ_BOS": {"tier": 1, "max_transit_hr": 6},
    "C2_NJ_PHL":     {"tier": 2, "max_transit_hr": 12},
}

# Penalty model (Playbook §13.2)
PENALTY = {
    "tier1_sla":      100,
    "tier2_sla":       40,
    "cold_chain":      80,
    "non_sla_delay":   10,
}


@dataclass
class CsvAnalysisResult:
    # Planning window data (Day0 + Day1 only)
    planning_df: pd.DataFrame
    # History data (for trend context)
    history_df: pd.DataFrame
    # DQ report
    dq_summary: Dict[str, Any]
    # Corridor-level KPIs for the 48h window
    corridor_kpis: Dict[str, Any]
    # Truck requirements per corridor per day
    truck_requirements: Dict[str, Any]
    # Resource constraints loaded from resource file
    resource_constraints: Dict[str, Any]


def _resolve_item(row: pd.Series):
    """
    Resolves a single row to canonical item master data.
    Returns (resolved_item_id, canonical_info, dq_flags)
    dq_flags is a list of rule IDs triggered.
    """
    item_id = row.get("item_id")
    item_name = str(row.get("item_name", "")).strip()
    dq_flags = []

    # Step 1: check legacy ID map
    if item_id in LEGACY_ID_MAP:
        item_id = LEGACY_ID_MAP[item_id]
        dq_flags.append("LEGACY_ID_MAP")

    # Step 2: check canonical master
    canonical = CANONICAL_ITEM_MASTER.get(item_id)

    # Step 3: if still not found, try name alias
    if canonical is None:
        alias_key = item_name.lower()
        mapped_id = NAME_ALIAS_MAP.get(alias_key)
        if mapped_id:
            item_id = mapped_id
            canonical = CANONICAL_ITEM_MASTER.get(item_id)
            dq_flags.append("ALIAS_MATCH")
        else:
            dq_flags.append("DQ-02")  # item_id not in master
            return item_id, None, dq_flags

    # Step 4: check name mismatch (alias not already flagged)
    if "ALIAS_MATCH" not in dq_flags:
        canonical_name = canonical["item_name"].lower()
        if item_name.lower() not in canonical_name and canonical_name not in item_name.lower():
            # Check alias map
            alias_key = item_name.lower()
            if alias_key in NAME_ALIAS_MAP:
                dq_flags.append("ALIAS_MATCH")
            else:
                dq_flags.append("DQ-03")  # name mismatch

    return item_id, canonical, dq_flags


def _apply_dq_checks(df: pd.DataFrame) -> pd.DataFrame:
    """
    Applies DQ-01 through DQ-04 to the dataframe.
    Adds columns: resolved_item_id, canonical_item_id, product_class,
                  temp_control, dq_flags, is_valid
    """
    resolved_ids = []
    canonical_ids = []
    product_classes = []
    temp_controls = []
    all_dq_flags = []
    is_valids = []

    seen_unique_ids = {}

    for idx, row in df.iterrows():
        flags = []

        # DQ-01: missing unique_item_id
        uid = row.get("unique_item_id")
        if pd.isna(uid) or str(uid).strip() == "":
            flags.append("DQ-01")

        # DQ-04: duplicate unique_item_id
        if not pd.isna(uid) and str(uid).strip() != "":
            uid_str = str(uid).strip()
            if uid_str in seen_unique_ids:
                flags.append("DQ-04")
            else:
                seen_unique_ids[uid_str] = idx

        # Resolve item identity
        resolved_id, canonical, id_flags = _resolve_item(row)
        flags.extend(id_flags)

        resolved_ids.append(resolved_id)

        if canonical:
            canonical_ids.append(canonical["canonical_item_id"])
            product_classes.append(canonical["product_class"])
            temp_controls.append(canonical["temp_control"])
        else:
            canonical_ids.append("UNKNOWN")
            product_classes.append("UNKNOWN")
            temp_controls.append("UNKNOWN")

        all_dq_flags.append(flags)

        # Row is valid for dispatch only if: no DQ-01 and no DQ-02
        is_valid = "DQ-01" not in flags and "DQ-02" not in flags
        is_valids.append(is_valid)

    df = df.copy()
    df["resolved_item_id"] = resolved_ids
    df["canonical_item_id"] = canonical_ids
    df["product_class"] = product_classes
    df["temp_control"] = temp_controls
    df["dq_flags"] = all_dq_flags
    df["dq_flags_str"] = [", ".join(f) if f else "OK" for f in all_dq_flags]
    df["is_valid"] = is_valids
    df["needs_cold_chain"] = df["product_class"].isin(COLD_CHAIN_CLASSES)

    return df


def _build_dq_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """Builds a human-readable DQ summary dict."""
    total = len(df)
    valid = int(df["is_valid"].sum())
    excluded = total - valid

    dq01 = int(df["dq_flags_str"].str.contains("DQ-01").sum())
    dq02 = int(df["dq_flags_str"].str.contains("DQ-02").sum())
    dq03 = int(df["dq_flags_str"].str.contains("DQ-03").sum())
    dq04 = int(df["dq_flags_str"].str.contains("DQ-04").sum())
    legacy = int(df["dq_flags_str"].str.contains("LEGACY_ID_MAP").sum())
    alias  = int(df["dq_flags_str"].str.contains("ALIAS_MATCH").sum())

    return {
        "total_rows": total,
        "valid_for_dispatch": valid,
        "excluded_from_dispatch": excluded,
        "DQ-01_missing_unique_id": dq01,
        "DQ-02_invalid_item_id": dq02,
        "DQ-03_name_mismatch": dq03,
        "DQ-04_duplicate_unique_id": dq04,
        "legacy_id_remapped": legacy,
        "alias_name_resolved": alias,
    }


def _compute_corridor_kpis(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Computes per-corridor, per-day KPIs for the planning window.
    Only uses rows where is_valid=True and is_planning_window=1.
    """
    plan_df = df[(df["is_planning_window"] == 1) & (df["is_valid"] == True)].copy()
    kpis = {}

    for corridor in plan_df["corridor_id"].unique():
        cdf = plan_df[plan_df["corridor_id"] == corridor]
        sla = SLA_TIERS.get(corridor, {"tier": 2, "max_transit_hr": 12})
        corridor_kpis = {}

        for day in ["Day0", "Day1"]:
            ddf = cdf[cdf["planning_day"] == day]
            total_units = len(ddf)
            cold_chain_units = int(ddf["needs_cold_chain"].sum())
            room_temp_units = total_units - cold_chain_units

            # Truck requirements (Playbook §8)
            # required_trucks = ceil((total_volume * 1.10) / 10)
            # Each unique_item_id = 1 volume unit
            cold_trucks_needed = int(np.ceil((cold_chain_units * PACKING_BUFFER) / TRUCK_CAPACITY_UNITS))
            std_trucks_needed  = int(np.ceil((room_temp_units  * PACKING_BUFFER) / TRUCK_CAPACITY_UNITS))

            corridor_kpis[day] = {
                "total_units": total_units,
                "cold_chain_units": cold_chain_units,
                "room_temp_units": room_temp_units,
                "sla_tier": sla["tier"],
                "max_transit_hr": sla["max_transit_hr"],
                "temp_controlled_trucks_needed": cold_trucks_needed,
                "standard_trucks_needed": std_trucks_needed,
                "drivers_needed": cold_trucks_needed + std_trucks_needed,
            }

        kpis[corridor] = corridor_kpis

    return kpis


def _compute_truck_requirements(corridor_kpis: Dict[str, Any]) -> Dict[str, Any]:
    """Flattens corridor KPIs into a per-day resource demand summary."""
    demand: Dict[str, Dict[str, int]] = {}

    for corridor, days in corridor_kpis.items():
        for day, metrics in days.items():
            if day not in demand:
                demand[day] = {
                    "temp_controlled_trucks_needed": 0,
                    "standard_trucks_needed": 0,
                    "drivers_needed": 0,
                }
            demand[day]["temp_controlled_trucks_needed"] += metrics["temp_controlled_trucks_needed"]
            demand[day]["standard_trucks_needed"]        += metrics["standard_trucks_needed"]
            demand[day]["drivers_needed"]                += metrics["drivers_needed"]

    return demand


def load_resource_constraints(resource_path: str) -> Dict[str, Any]:
    """Loads the Resource_availability_48h.csv into a structured dict."""
    df = pd.read_csv(resource_path)
    constraints: Dict[str, Dict[str, int]] = {}

    for _, row in df.iterrows():
        day = str(row["day"]).strip()
        rtype = str(row["resource_type"]).strip()
        count = int(row["available_count"])
        if day not in constraints:
            constraints[day] = {}
        constraints[day][rtype] = count

    return constraints


def analyze_csv(csv_path: str, resource_path: str | None = None) -> CsvAnalysisResult:
    """
    Main entry point. Loads multi-corridor CSV, applies DQ checks,
    computes corridor KPIs and truck requirements.
    """
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]

    # Parse dates
    if "shipment_date" in df.columns:
        df["shipment_date"] = pd.to_datetime(df["shipment_date"], errors="coerce")

    # Apply DQ checks to full dataset
    df = _apply_dq_checks(df)

    # Split into history vs planning window
    planning_df = df[df["is_planning_window"] == 1].copy()
    history_df  = df[df["is_planning_window"] == 0].copy()

    # Build outputs
    dq_summary       = _build_dq_summary(df)
    corridor_kpis    = _compute_corridor_kpis(df)
    truck_requirements = _compute_truck_requirements(corridor_kpis)

    # Resource constraints
    resource_constraints: Dict[str, Any] = {}
    if resource_path:
        resource_constraints = load_resource_constraints(resource_path)

    return CsvAnalysisResult(
        planning_df=planning_df,
        history_df=history_df,
        dq_summary=dq_summary,
        corridor_kpis=corridor_kpis,
        truck_requirements=truck_requirements,
        resource_constraints=resource_constraints,
    )
