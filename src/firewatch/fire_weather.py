"""Open-Meteo-backed Canadian Fire Weather Index context.

The module keeps the weather-provider call and the FWI calculation separate so
the provider can later be replaced by an observed BMKG/ERA5 feed without
changing the response-planning API.  It is intentionally a *weather-model
context*: it must not be presented as a field observation or an official fire
warning.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import math
from typing import Any
import hashlib
import json
import os
from pathlib import Path
from .releases import atomic_json

import requests


OPEN_METEO_FORECAST_URL = os.environ.get("FIREWATCH_WEATHER_URL", "https://api.open-meteo.com/v1/forecast")
OPEN_METEO_DOCUMENTATION_URL = "https://open-meteo.com/en/docs"
CANADIAN_FWI_SOURCE_URL = "https://cwfis.cfs.nrcan.gc.ca/index.php/background/dsm/fwi"
INITIAL_FFMC = 85.0
INITIAL_DMC = 6.0
INITIAL_DC = 15.0
HISTORY_DAYS = 92


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(float(value), lower), upper)


def ffmc_step(previous: float, temperature_c: float, relative_humidity_pct: float,
              wind_kmh: float, rainfall_mm: float) -> float:
    """Calculate one daily Fine Fuel Moisture Code step (Van Wagner FWI)."""
    temperature_c = float(temperature_c)
    relative_humidity_pct = _clamp(relative_humidity_pct, 0.0, 100.0)
    wind_kmh = max(0.0, float(wind_kmh))
    rainfall_mm = max(0.0, float(rainfall_mm))
    moisture = 147.2 * (101.0 - previous) / (59.5 + previous)
    if rainfall_mm > 0.5:
        rain_effective = rainfall_mm - 0.5
        moisture += (
            42.5 * rain_effective * math.exp(-100.0 / (251.0 - moisture))
            * (1.0 - math.exp(-6.93 / rain_effective))
        )
        if moisture > 150.0:
            moisture += 0.0015 * (moisture - 150.0) ** 2 * math.sqrt(rain_effective)
        moisture = min(moisture, 250.0)

    equilibrium_dry = (
        0.942 * relative_humidity_pct ** 0.679
        + 11.0 * math.exp((relative_humidity_pct - 100.0) / 10.0)
        + 0.18 * (21.1 - temperature_c) * (1.0 - math.exp(-0.115 * relative_humidity_pct))
    )
    if moisture < equilibrium_dry:
        equilibrium_wet = (
            0.618 * relative_humidity_pct ** 0.753
            + 10.0 * math.exp((relative_humidity_pct - 100.0) / 10.0)
            + 0.18 * (21.1 - temperature_c) * (1.0 - math.exp(-0.115 * relative_humidity_pct))
        )
        if moisture < equilibrium_wet:
            drying_rate = (
                0.424 * (1.0 - (relative_humidity_pct / 100.0) ** 1.7)
                + 0.0694 * math.sqrt(wind_kmh) * (1.0 - (relative_humidity_pct / 100.0) ** 8)
            ) * 0.581 * math.exp(0.0365 * temperature_c)
            moisture = equilibrium_wet - (equilibrium_wet - moisture) * 10.0 ** (-drying_rate)
    else:
        wetting_rate = (
            0.424 * (1.0 - ((100.0 - relative_humidity_pct) / 100.0) ** 1.7)
            + 0.0694 * math.sqrt(wind_kmh) * (1.0 - ((100.0 - relative_humidity_pct) / 100.0) ** 8)
        ) * 0.581 * math.exp(0.0365 * temperature_c)
        moisture = equilibrium_dry + (moisture - equilibrium_dry) * 10.0 ** (-wetting_rate)
    return _clamp(59.5 * (250.0 - moisture) / (147.2 + moisture), 0.0, 101.0)


def dmc_step(previous: float, temperature_c: float, relative_humidity_pct: float,
             rainfall_mm: float, month: int) -> float:
    """Calculate one daily Duff Moisture Code step for equatorial latitude."""
    temperature_c = max(-1.1, float(temperature_c))
    relative_humidity_pct = _clamp(relative_humidity_pct, 0.0, 100.0)
    rainfall_mm = max(0.0, float(rainfall_mm))
    drying = 1.894 * (temperature_c + 1.1) * (100.0 - relative_humidity_pct) * 9.0 * 1e-6
    value = max(0.0, float(previous))
    if rainfall_mm > 1.5:
        rain_effective = 0.92 * rainfall_mm - 1.27
        initial_moisture = 20.0 + math.exp(5.6348 - value / 43.43)
        if value <= 33.0:
            coefficient = 100.0 / (0.5 + 0.3 * value)
        elif value <= 65.0:
            coefficient = 14.0 - 1.3 * math.log(value)
        else:
            coefficient = 6.2 * math.log(value) - 17.2
        final_moisture = initial_moisture + 1000.0 * rain_effective / (48.77 + coefficient * rain_effective)
        value = max(0.0, 43.43 * (5.6348 - math.log(final_moisture - 20.0)))
    return max(0.0, value + 100.0 * drying)


def dc_step(previous: float, temperature_c: float, rainfall_mm: float, month: int) -> float:
    """Calculate one daily Drought Code step for equatorial latitude."""
    temperature_c = max(-2.8, float(temperature_c))
    rainfall_mm = max(0.0, float(rainfall_mm))
    drying = (0.36 * (temperature_c + 2.8) + 1.4) / 2.0
    value = max(0.0, float(previous))
    if rainfall_mm > 2.8:
        rain_effective = 0.83 * rainfall_mm - 1.27
        initial_moisture = 800.0 * math.exp(-value / 400.0)
        value = max(0.0, value - 400.0 * math.log(1.0 + 3.937 * rain_effective / initial_moisture))
    return max(0.0, value + drying)


def isi_from_ffmc(ffmc: float, wind_kmh: float) -> float:
    moisture = 147.2 * (101.0 - ffmc) / (59.5 + ffmc)
    spread = 19.115 * math.exp(-0.1386 * moisture) * (1.0 + moisture ** 5.31 / 49_300_000.0)
    return max(0.0, spread * math.exp(0.05039 * max(0.0, wind_kmh)))


def bui_from_dmc_dc(dmc: float, dc: float) -> float:
    if dmc <= 0.4 * dc:
        return max(0.0, 0.8 * dmc * dc / (dmc + 0.4 * dc)) if (dmc + 0.4 * dc) else 0.0
    return max(0.0, dmc - (1.0 - 0.8 * dc / (dmc + 0.4 * dc)) * (0.92 + (0.0114 * dmc) ** 1.7))


def fwi_from_isi_bui(isi: float, bui: float) -> float:
    factor = 0.626 * bui ** 0.809 + 2.0 if bui <= 80.0 else 1000.0 / (25.0 + 108.64 * math.exp(-0.023 * bui))
    intermediate = 0.1 * isi * factor
    return max(0.0, intermediate) if intermediate <= 1.0 else math.exp(2.72 * (0.434 * math.log(intermediate)) ** 0.647)


def fwi_class(fwi_value: float) -> str:
    """Map numeric FWI to the four dashboard input classes."""
    if fwi_value < 5.0:
        return "low"
    if fwi_value < 12.0:
        return "moderate"
    if fwi_value < 21.0:
        return "high"
    return "very_high"


def calculate_fwi_series(daily_weather: list[dict[str, Any]], initial_state: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Calculate daily FWI state from noon weather and previous-24h rain."""
    state = initial_state or {"ffmc": INITIAL_FFMC, "dmc": INITIAL_DMC, "dc": INITIAL_DC}
    ffmc, dmc, dc = state["ffmc"], state["dmc"], state["dc"]
    results: list[dict[str, Any]] = []
    for weather in daily_weather:
        timestamp = weather["timestamp"]
        ffmc = ffmc_step(ffmc, weather["temperature_c"], weather["relative_humidity_pct"], weather["wind_kmh"], weather["rainfall_24h_mm"])
        dmc = dmc_step(dmc, weather["temperature_c"], weather["relative_humidity_pct"], weather["rainfall_24h_mm"], timestamp.month)
        dc = dc_step(dc, weather["temperature_c"], weather["rainfall_24h_mm"], timestamp.month)
        isi = isi_from_ffmc(ffmc, weather["wind_kmh"])
        bui = bui_from_dmc_dc(dmc, dc)
        fwi_value = fwi_from_isi_bui(isi, bui)
        results.append({
            **weather, "ffmc": round(ffmc, 2), "dmc": round(dmc, 2), "dc": round(dc, 2),
            "isi": round(isi, 2), "bui": round(bui, 2), "fwi": round(fwi_value, 2), "fwi_class": fwi_class(fwi_value),
        })
    return results


def _hour_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")


def _weather_directory() -> Path:
    return Path(os.environ.get("FIREWATCH_WEATHER_CACHE", Path(__file__).resolve().parents[2] / "data/operational/weather"))


@lru_cache(maxsize=512)
def _open_meteo_payload(latitude: float, longitude: float, cache_hour: str) -> dict[str, Any]:
    key = hashlib.sha256(f"{latitude}:{longitude}:{cache_hour}".encode()).hexdigest()
    path = _weather_directory() / "payloads" / (key + ".json")
    if path.exists():
        return json.loads(path.read_text())
    params = {
        "latitude": latitude, "longitude": longitude, "timezone": "auto", "past_days": HISTORY_DAYS,
        "forecast_days": 1, "wind_speed_unit": "kmh",
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation",
    }
    if os.environ.get("FIREWATCH_WEATHER_API_KEY"):
        params["apikey"] = os.environ["FIREWATCH_WEATHER_API_KEY"]
    response = requests.get(OPEN_METEO_FORECAST_URL, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if "hourly" not in payload or "time" not in payload["hourly"]:
        raise ValueError("Open-Meteo hourly series is unavailable")
    payload["_fetched_at_utc"] = datetime.now(timezone.utc).isoformat()
    atomic_json(path, payload)
    return payload


def _noon_weather(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], datetime | None]:
    hourly = payload["hourly"]
    timestamps = [datetime.fromisoformat(value) for value in hourly["time"]]
    temperature = hourly["temperature_2m"]
    humidity = hourly["relative_humidity_2m"]
    wind = hourly["wind_speed_10m"]
    precipitation = hourly["precipitation"]
    if not all(len(values) == len(timestamps) for values in (temperature, humidity, wind, precipitation)):
        raise ValueError("Open-Meteo hourly variables have inconsistent lengths.")
    current_time = payload.get("current", {}).get("time")
    current_local = datetime.fromisoformat(current_time) if current_time else None
    daily: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps):
        if timestamp.hour != 12 or any(value is None for value in (temperature[index], humidity[index], wind[index])):
            continue
        start = max(0, index - 23)
        preceding = timestamps[start:index + 1]
        if len(preceding) != 24 or (preceding[-1] - preceding[0]).total_seconds() != 23 * 3600:
            continue
        rain_values = precipitation[start:index + 1]
        if any(value is None for value in rain_values):
            continue
        daily.append({"timestamp": timestamp, "temperature_c": float(temperature[index]), "relative_humidity_pct": float(humidity[index]), "wind_kmh": float(wind[index]), "rainfall_24h_mm": float(sum(rain_values))})
    return daily, current_local


def fire_weather_context(latitude: float, longitude: float) -> dict[str, Any]:
    """Return a cached, source-labelled FWI context for a coordinate."""
    latitude, longitude = float(latitude), float(longitude)
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        raise ValueError("latitude/longitude must be valid WGS84 decimal degrees")
    payload = _open_meteo_payload(round(latitude, 3), round(longitude, 3), _hour_key())
    daily, current_local = _noon_weather(payload)
    if len(daily) < 30:
        raise ValueError("Open-Meteo did not return enough complete noon weather days for FWI.")
    # Today's future noon must not replace the most recent available noon.
    reference_time = current_local or datetime.now(timezone.utc).replace(tzinfo=None)
    available = [row for row in daily if row["timestamp"] <= reference_time]
    if len(available) < 30:
        raise ValueError("Not enough complete past noon observations")
    state_key = hashlib.sha256(f"{round(latitude, 3)}:{round(longitude, 3)}".encode()).hexdigest()
    state_path = _weather_directory() / "states" / (state_key + ".json")
    stored = json.loads(state_path.read_text()) if state_path.exists() else {}
    from datetime import timedelta
    boundary = (available[0]["timestamp"] - timedelta(days=1)).date().isoformat()
    initial = stored.get("days", {}).get(boundary)
    series = calculate_fwi_series(available, initial)
    states = stored.get("days", {})
    states.update({row["timestamp"].date().isoformat(): {name: row[name] for name in ("ffmc", "dmc", "dc")} for row in series})
    atomic_json(state_path, {"days": states, "updated_at_utc": datetime.now(timezone.utc).isoformat(), "initialization": "persistent_boundary" if initial else "standard_state_warmup"})
    latest = series[-1]
    target_time = latest["timestamp"]
    target_mode = "forecast" if current_local and target_time > current_local else "model_weather"
    return {
        "lookup_status": "available", "recommended_fwi_class": latest["fwi_class"],
        "fwi": latest["fwi"], "ffmc": latest["ffmc"], "dmc": latest["dmc"], "dc": latest["dc"], "isi": latest["isi"], "bui": latest["bui"],
        "weather_at_local_noon": {"timestamp": target_time.isoformat(), "temperature_c": latest["temperature_c"], "relative_humidity_pct": latest["relative_humidity_pct"], "wind_kmh": latest["wind_kmh"], "rainfall_previous_24h_mm": latest["rainfall_24h_mm"], "mode": target_mode},
        "timezone": payload.get("timezone"), "utc_offset_seconds": payload.get("utc_offset_seconds"), "history_days_used": len(available), "state_initialization": "persistent_boundary" if initial else "standard_state_warmup",
        "initial_state": {"ffmc": INITIAL_FFMC, "dmc": INITIAL_DMC, "dc": INITIAL_DC},
        "classification": {"bands": "FWI <5 low; 5–<12 moderate; 12–<21 high; ≥21 very high", "note": "Dashboard screening band; not an official local warning threshold."},
        "source": {"provider": "Open-Meteo Forecast API", "provider_documentation": OPEN_METEO_DOCUMENTATION_URL, "fwi_method": "Canadian Forest Fire Weather Index System; daily noon weather with previous-24h precipitation", "fwi_method_source": CANADIAN_FWI_SOURCE_URL, "fetched_at_utc": payload.get("_fetched_at_utc"), "cache_key_hour_utc": _hour_key()},
        "limitations": ["Open-Meteo is model weather, not a field observation or an official Indonesian warning.", "The calculation uses a standard initial FWI state and the complete prior-period history returned by the provider; ERA5/BMKG backfill can later replace the initialisation.", "FWI is a fire-weather indicator; peat fuel, incident perimeter, and fire type still need field confirmation."],
    }
