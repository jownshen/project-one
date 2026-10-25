"""Google Maps Platform Weather API (forecasts blend Google DeepMind's
WeatherNext AI models with traditional weather models).

Docs: https://developers.google.com/maps/documentation/weather
"""
import requests

BASE = "https://weather.googleapis.com/v1"


def _get(path, key, lat, lon, **params):
    resp = requests.get(
        f"{BASE}/{path}",
        params={"key": key, "location.latitude": lat, "location.longitude": lon, **params},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def _num(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def fetch_hourly(key, lat, lon, hours=48):
    out, token = [], None
    while len(out) < hours:
        params = {"hours": hours, "pageSize": 24}
        if token:
            params["pageToken"] = token
        data = _get("forecast/hours:lookup", key, lat, lon, **params)
        for h in data.get("forecastHours", []):
            out.append({
                "time": h["interval"]["startTime"],
                "precip_mm": _num(h, "precipitation", "qpf", "quantity") or 0.0,
                "precip_prob": _num(h, "precipitation", "probability", "percent"),
                "thunder_prob": h.get("thunderstormProbability"),
                "gust_kmh": _num(h, "wind", "gust", "value"),
                "heat_index_c": _num(h, "heatIndex", "degrees"),
                "temp_c": _num(h, "temperature", "degrees"),
                "condition": _num(h, "weatherCondition", "description", "text"),
            })
        token = data.get("nextPageToken")
        if not token:
            break
    return out[:hours]


def fetch_current(key, lat, lon):
    data = _get("currentConditions:lookup", key, lat, lon)
    return {
        "time": data.get("currentTime"),
        "precip_mm": _num(data, "precipitation", "qpf", "quantity"),
        "precip_24h_mm": _num(data, "currentConditionsHistory", "qpf", "quantity"),
        "temp_c": _num(data, "temperature", "degrees"),
        "heat_index_c": _num(data, "heatIndex", "degrees"),
        "condition": _num(data, "weatherCondition", "description", "text"),
    }
