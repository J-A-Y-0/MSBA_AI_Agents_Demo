from __future__ import annotations
from typing import Dict, Any
import requests

# ---------------------------------------------------------------------------
# Corridor waypoints — authoritative from Playbook §3.2
# ---------------------------------------------------------------------------
CORRIDOR_WAYPOINTS = {
    "C1_I95_NJ_BOS": [
        {"id": "C1_W1", "city": "Newark NJ",      "lat": 40.7357, "lon": -74.1724},
        {"id": "C1_W2", "city": "Bronx NY",        "lat": 40.8448, "lon": -73.8648},
        {"id": "C1_W3", "city": "New Haven CT",    "lat": 41.3083, "lon": -72.9279},
        {"id": "C1_W4", "city": "Providence RI",   "lat": 41.8240, "lon": -71.4128},
        {"id": "C1_W5", "city": "Boston MA",       "lat": 42.3601, "lon": -71.0589},
    ],
    "C2_NJ_PHL": [
        {"id": "C2_W1", "city": "Newark NJ",       "lat": 40.7357, "lon": -74.1724},
        {"id": "C2_W2", "city": "New Brunswick NJ","lat": 40.4862, "lon": -74.4518},
        {"id": "C2_W3", "city": "Trenton NJ",      "lat": 40.2204, "lon": -74.7643},
        {"id": "C2_W4", "city": "Philadelphia PA", "lat": 39.9526, "lon": -75.1652},
    ],
}

# Weather risk thresholds (Playbook §6)
PRECIP_THRESHOLD_MM  = 15.0
WIND_THRESHOLD_KMH   = 45.0
FREEZE_THRESHOLD_C   = 0.0


def _fetch_waypoint_weather(lat: float, lon: float, tz: str = "America/New_York") -> Dict[str, Any]:
    """Fetches 2-day daily forecast for a single waypoint from Open-Meteo."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_sum,temperature_2m_min,wind_gusts_10m_max",
        "timezone": tz,
        "forecast_days": 2,
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _score_waypoint(forecast: Dict[str, Any]) -> int:
    """
    Computes risk score (0-3) for a single waypoint across the 2-day window.
    Each flag (heavy rain, high wind, freezing) adds 1 point.
    Returns the max day score across Day0 and Day1.
    """
    daily = forecast.get("daily", {})
    precip_list = daily.get("precipitation_sum", []) or []
    gusts_list  = daily.get("wind_gusts_10m_max", []) or []
    tmin_list   = daily.get("temperature_2m_min", []) or []

    max_score = 0
    for i in range(min(2, len(precip_list))):
        precip = precip_list[i] if i < len(precip_list) else 0.0
        gusts  = gusts_list[i]  if i < len(gusts_list)  else 0.0
        tmin   = tmin_list[i]   if i < len(tmin_list)   else 99.0

        score = (
            int(precip >= PRECIP_THRESHOLD_MM) +
            int(gusts  >= WIND_THRESHOLD_KMH) +
            int(tmin   <= FREEZE_THRESHOLD_C)
        )
        max_score = max(max_score, score)

    return max_score


def _travel_buffer(risk_score: int) -> str:
    """Returns travel time buffer string per Playbook §6, Table 5.2."""
    buffers = {0: "No buffer", 1: "+10%", 2: "+25%", 3: "+40% + ESCALATION REQUIRED"}
    return buffers.get(risk_score, "No buffer")


def get_corridor_weather_risk(tz: str = "America/New_York") -> Dict[str, Any]:
    """
    Fetches weather for every waypoint across both corridors.
    Returns per-corridor risk score (max across waypoints) + travel buffer.
    """
    results: Dict[str, Any] = {}

    for corridor_id, waypoints in CORRIDOR_WAYPOINTS.items():
        corridor_max_score = 0
        waypoint_details = []

        for wp in waypoints:
            try:
                forecast = _fetch_waypoint_weather(wp["lat"], wp["lon"], tz)
                score = _score_waypoint(forecast)
                daily = forecast.get("daily", {})

                waypoint_details.append({
                    "waypoint_id": wp["id"],
                    "city": wp["city"],
                    "risk_score": score,
                    "max_precip_mm": max(daily.get("precipitation_sum", [0]) or [0]),
                    "max_gusts_kmh": max(daily.get("wind_gusts_10m_max", [0]) or [0]),
                    "min_temp_c":    min(daily.get("temperature_2m_min", [99]) or [99]),
                })
                corridor_max_score = max(corridor_max_score, score)

            except Exception as e:
                waypoint_details.append({
                    "waypoint_id": wp["id"],
                    "city": wp["city"],
                    "risk_score": 0,
                    "error": str(e),
                })

        results[corridor_id] = {
            "corridor_risk_score": corridor_max_score,
            "travel_buffer": _travel_buffer(corridor_max_score),
            "escalation_required": corridor_max_score >= 3,
            "waypoints": waypoint_details,
        }

    return results


# ---------------------------------------------------------------------------
# Legacy shim — keeps existing graph.py node_weather working if called
# ---------------------------------------------------------------------------
def get_weather_forecast(lat: str, lon: str, tz: str) -> Dict[str, Any]:
    return _fetch_waypoint_weather(float(lat), float(lon), tz)


def derive_dispatch_weather_risk(forecast: Dict[str, Any]) -> Dict[str, Any]:
    """Kept for backward compatibility."""
    daily = forecast.get("daily", {})
    precip = daily.get("precipitation_sum", []) or []
    gusts  = daily.get("wind_gusts_10m_max", []) or []
    tmin   = daily.get("temperature_2m_min", []) or []

    max_precip = max(precip) if precip else 0.0
    max_gusts  = max(gusts)  if gusts  else 0.0
    min_temp   = min(tmin)   if tmin   else None

    flags = {
        "heavy_rain_risk": max_precip >= PRECIP_THRESHOLD_MM,
        "high_wind_risk":  max_gusts  >= WIND_THRESHOLD_KMH,
        "freezing_risk":   (min_temp is not None and min_temp <= FREEZE_THRESHOLD_C),
    }
    score = sum(flags.values())

    return {
        "max_precip_mm_day":  float(max_precip),
        "max_wind_gust_kmh":  float(max_gusts),
        "min_temp_c":         float(min_temp) if min_temp is not None else None,
        "risk_flags":         flags,
        "risk_score_0_3":     score,
    }
