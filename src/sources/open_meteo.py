"""Open-Meteo (free, no key). Used for:
- past rainfall (how wet the ground already is; rain over the last few hours)
- a backup forecast when the Google Weather API is unavailable
- tide predictions (Marine API, sea_level_height_msl)
- rain and tides at the time of past floods (flood log)

Open-Meteo stamps precipitation at the END of the hour it fell in. Everything
returned here is re-stamped to the START of the hour, matching Google's
forecastHours, so `time` always means "the hour starting at".
"""
from datetime import date, datetime, timedelta

import requests

FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
MARINE = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_PAST_LIMIT_DAYS = 80  # older dates come from the archive (ERA5 reanalysis)


def _get(url, **params):
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _with_offset(times, offset_s):
    sign = "+" if offset_s >= 0 else "-"
    hh, mm = divmod(abs(offset_s) // 60, 60)
    return [f"{t}:00{sign}{hh:02d}:{mm:02d}" for t in times]


def _hour_start(t):
    return (datetime.fromisoformat(t) - timedelta(hours=1)).isoformat()


def fetch_rain(lat, lon, tz, now_iso, recent_h=12):
    """Returns (past72_mm, recent_hours, forecast_hours)."""
    data = _get(
        FORECAST, latitude=lat, longitude=lon, timezone=tz, past_days=3, forecast_days=3,
        hourly="precipitation,precipitation_probability,wind_gusts_10m,apparent_temperature,weather_code",
    )
    h = data["hourly"]
    times = _with_offset(h["time"], data["utc_offset_seconds"])
    now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    past72, recent, hours = 0.0, [], []
    for i, t in enumerate(times):
        end = datetime.fromisoformat(t)
        mm = h["precipitation"][i] or 0.0
        if end <= now:
            if end > now - timedelta(hours=72):
                past72 += mm
            if end > now - timedelta(hours=recent_h):
                recent.append({"time": _hour_start(t), "precip_mm": mm})
        else:
            hours.append({
                "time": _hour_start(t),
                "precip_mm": mm,
                "precip_prob": h["precipitation_probability"][i],
                "thunder_prob": 60 if (h["weather_code"][i] or 0) >= 95 else 0,
                "gust_kmh": h["wind_gusts_10m"][i],
                "heat_index_c": h["apparent_temperature"][i],
            })
    return round(past72, 1), recent, hours


def _tide_series(data):
    times = _with_offset(data["hourly"]["time"], data["utc_offset_seconds"])
    return [{"time": t, "height_m": v}
            for t, v in zip(times, data["hourly"]["sea_level_height_msl"]) if v is not None]


def high_tides(series):
    out = []
    for a, b, c in zip(series, series[1:], series[2:]):
        if b["height_m"] >= a["height_m"] and b["height_m"] > c["height_m"]:
            out.append(b)
    return out


def fetch_high_tides(lat, lon, tz, days=3):
    """Returns (high_tides, hourly_series), from yesterday through `days` ahead."""
    series = _tide_series(_get(MARINE, latitude=lat, longitude=lon, timezone=tz,
                               hourly="sea_level_height_msl", past_days=1, forecast_days=days))
    return high_tides(series), series


def fetch_rain_range(lat, lon, tz, start, end):
    """Hourly rain between two dates (inclusive), for looking back at past floods."""
    old = (date.today() - end).days > FORECAST_PAST_LIMIT_DAYS
    data = _get(ARCHIVE if old else FORECAST, latitude=lat, longitude=lon, timezone=tz,
                hourly="precipitation", start_date=start.isoformat(), end_date=end.isoformat())
    times = _with_offset(data["hourly"]["time"], data["utc_offset_seconds"])
    source = "Open-Meteo archive (ERA5 reanalysis)" if old else "Open-Meteo"
    return [{"time": _hour_start(t), "precip_mm": mm or 0.0}
            for t, mm in zip(times, data["hourly"]["precipitation"])], source


def fetch_tide_range(lat, lon, tz, start, end):
    return _tide_series(_get(MARINE, latitude=lat, longitude=lon, timezone=tz,
                             hourly="sea_level_height_msl",
                             start_date=start.isoformat(), end_date=end.isoformat()))
