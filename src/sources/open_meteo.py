"""Open-Meteo (free, no key). Used for:
- past 72 h rainfall (how wet the ground already is)
- a backup forecast when the Google Weather API is unavailable
- tide predictions (Marine API, sea_level_height_msl)
"""
from datetime import datetime, timedelta

import requests

FORECAST = "https://api.open-meteo.com/v1/forecast"
MARINE = "https://marine-api.open-meteo.com/v1/marine"


def _get(url, **params):
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _with_offset(times, offset_s):
    sign = "+" if offset_s >= 0 else "-"
    hh, mm = divmod(abs(offset_s) // 60, 60)
    return [f"{t}:00{sign}{hh:02d}:{mm:02d}" for t in times]


def fetch_rain(lat, lon, tz, now_iso):
    """Returns (past72_mm, forecast_hours)."""
    data = _get(
        FORECAST, latitude=lat, longitude=lon, timezone=tz, past_days=3, forecast_days=3,
        hourly="precipitation,precipitation_probability,wind_gusts_10m,apparent_temperature,weather_code",
    )
    h = data["hourly"]
    times = _with_offset(h["time"], data["utc_offset_seconds"])
    now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    past72, hours = 0.0, []
    for i, t in enumerate(times):
        dt = datetime.fromisoformat(t)
        if dt <= now:
            if dt > now - timedelta(hours=72):
                past72 += h["precipitation"][i] or 0
        else:
            hours.append({
                "time": t,
                "precip_mm": h["precipitation"][i] or 0.0,
                "precip_prob": h["precipitation_probability"][i],
                "thunder_prob": 60 if (h["weather_code"][i] or 0) >= 95 else 0,
                "gust_kmh": h["wind_gusts_10m"][i],
                "heat_index_c": h["apparent_temperature"][i],
            })
    return round(past72, 1), hours


def fetch_high_tides(lat, lon, tz, days=3):
    data = _get(MARINE, latitude=lat, longitude=lon, timezone=tz,
                hourly="sea_level_height_msl", forecast_days=days)
    h = data["hourly"]
    times = _with_offset(h["time"], data["utc_offset_seconds"])
    levels = h["sea_level_height_msl"]
    tides = []
    for i in range(1, len(levels) - 1):
        a, b, c = levels[i - 1], levels[i], levels[i + 1]
        if None not in (a, b, c) and b >= a and b > c:
            tides.append({"time": times[i], "height_m": b})
    series = [{"time": t, "height_m": v} for t, v in zip(times, levels)]
    return tides, series
