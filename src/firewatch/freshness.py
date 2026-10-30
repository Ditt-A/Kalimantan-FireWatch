"""Per-layer freshness computed from observation and forecast validity times."""
from datetime import datetime, timezone
import pandas as pd

def timestamp(value):
    result = pd.Timestamp(value)
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")

def layer_freshness(forecast_week, alert_time, manifest=None, now=None):
    current = timestamp(now or datetime.now(timezone.utc))
    forecast = (manifest or {}).get("forecast", {})
    valid_from = timestamp(forecast.get("valid_from", forecast_week))
    valid_until = timestamp(forecast.get("valid_until", valid_from + pd.Timedelta(days=7)))
    latest_alert = timestamp(alert_time)
    return {
        "forecast": {
            "status": "expired" if current >= valid_until else "scheduled" if current < valid_from else "current",
            "valid_from": valid_from.isoformat(), "valid_until": valid_until.isoformat(),
            "issued_at": forecast.get("issued_at"), "feature_as_of": forecast.get("feature_as_of"),
        },
        "firms": {
            "status": "stale" if current - latest_alert > pd.Timedelta(hours=24) else "recent_detection",
            "latest_detection_at": latest_alert.isoformat(),
            "age_hours": round(max(0.0, (current - latest_alert).total_seconds() / 3600), 1),
            "note": "Detection age is not an ingestion-success heartbeat; zero detections may be valid.",
        },
    }
