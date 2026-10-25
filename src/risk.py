"""Risk engine: turns forecast + warnings + tides into one alert level.

Pure function, no network calls, so it can be tested with fixtures.

Input shape (all times ISO 8601 with timezone):
    {
      "now": "...",
      "hourly": [{"time", "precip_mm", "precip_prob", "thunder_prob",
                  "gust_kmh", "heat_index_c"}],
      "current": {"precip_mm": float | None},
      "past72_mm": float | None,
      "high_tides": [{"time", "height_m"}],
      "tide_series": [{"time", "height_m"}]          (optional, hourly),
      "recent_hourly": [{"time", "precip_mm"}]       (optional, rain already fallen),
      "pagasa": {"rainfall": None | "yellow" | "orange" | "red",
                 "tcws": int | None, "tcws_nearby": int | None,
                 "tc_active": bool, "tc_name": str | None,
                 "thunderstorm": None | "advisory" | "warning"}
    }
"""
from datetime import datetime, timedelta

LEVELS = ["NORMAL", "WATCH", "WARNING", "EMERGENCY"]


def rank(level):
    return LEVELS.index(level)


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _highest(value, table):
    """Highest level in `table` ({level: threshold}) that `value` meets."""
    if value is None:
        return None
    hit = None
    for level, threshold in table.items():
        if value >= threshold and (hit is None or rank(level) > rank(hit)):
            hit = level
    return hit


def _bump(level):
    return LEVELS[min(rank(level) + 1, len(LEVELS) - 1)]


def evaluate(inputs, th):
    now = _t(inputs["now"])
    horizon_end = now + timedelta(hours=th["horizon_h"])
    hours = sorted(
        (h for h in inputs.get("hourly", [])
         if now - timedelta(hours=1) < _t(h["time"]) <= horizon_end),
        key=lambda h: h["time"],
    )
    tides = [
        _t(t["time"]) for t in inputs.get("high_tides", [])
        if t.get("height_m") is None or t["height_m"] >= th["high_tide_min_m"]
    ]
    tide_window = timedelta(hours=th["high_tide_window_h"])

    reasons = []  # (level, text, kind); kind "rain" is affected by wet ground

    def add(level, text, kind="other"):
        if level:
            reasons.append({"level": level, "text": text, "kind": kind})

    # Rain in the next N hours: worst 3-hour window, total, and at high tide.
    max3, max3_start, max3_tide, max3_tide_start = 0.0, None, 0.0, None
    for i in range(max(len(hours) - 2, 0)):
        window = hours[i:i + 3]
        total = sum(h.get("precip_mm") or 0 for h in window)
        start = _t(window[0]["time"])
        end = start + timedelta(hours=3)
        if total > max3:
            max3, max3_start = total, start
        near_tide = any(start - tide_window <= ht <= end + tide_window for ht in tides)
        if near_tide and total > max3_tide:
            max3_tide, max3_tide_start = total, start
    total24 = sum(h.get("precip_mm") or 0 for h in hours)

    def when(dt):
        return dt.astimezone(now.tzinfo).strftime("%a %-I %p") if dt else ""

    add(_highest(max3, th["rain_3h_mm"]),
        f"{max3:.1f} mm of rain forecast in 3 hours (from {when(max3_start)})", "rain")
    add(_highest(total24, th["rain_24h_mm"]),
        f"{total24:.0f} mm of rain forecast in the next {th['horizon_h']} hours", "rain")
    add(_highest(max3_tide, th["rain_3h_at_high_tide_mm"]),
        f"{max3_tide:.1f} mm of rain in 3 hours around high tide (from {when(max3_tide_start)})"
        " - drainage into Cansaga Bay is slowest then", "rain")

    current_mm = (inputs.get("current") or {}).get("precip_mm")
    add(_highest(current_mm, th["current_rain_mm"]),
        f"{current_mm} mm of rain right now (current conditions)", "rain")

    thunder = max((h.get("thunder_prob") or 0 for h in hours), default=0)
    add(_highest(thunder, th["thunder_prob_pct"]), f"{thunder}% chance of thunderstorms")

    gust = max((h.get("gust_kmh") or 0 for h in hours), default=0)
    add(_highest(gust, th["gust_kmh"]), f"Wind gusts up to {gust:.0f} km/h")

    heat = max((h.get("heat_index_c") or 0 for h in hours), default=0)
    add(_highest(heat, th["heat_index_c"]),
        f"Heat index up to {heat:.0f} °C - limit time outdoors, drink water")

    # PAGASA official warnings.
    pg = inputs.get("pagasa") or {}
    rainfall_map = {"yellow": "WATCH", "orange": "WARNING", "red": "EMERGENCY"}
    if pg.get("rainfall") in rainfall_map:
        add(rainfall_map[pg["rainfall"]],
            f"PAGASA {pg['rainfall'].upper()} rainfall warning covers this area", "rain")
    if pg.get("tcws"):
        level = "WARNING" if pg["tcws"] == 1 else "EMERGENCY"
        add(level, f"Tropical Cyclone Wind Signal No. {pg['tcws']} raised for Cebu"
            + (f" ({pg['tc_name']})" if pg.get("tc_name") else ""))
    elif pg.get("tcws_nearby"):
        add("WATCH", f"Wind Signal No. {pg['tcws_nearby']} raised for other parts of Cebu"
            + (f" ({pg['tc_name']})" if pg.get("tc_name") else ""))
    elif pg.get("tc_active"):
        add("NORMAL", "A tropical cyclone is inside the Philippine Area of Responsibility"
            + (f" ({pg['tc_name']})" if pg.get("tc_name") else "")
            + " - no wind signal for Cebu yet")
    if pg.get("thunderstorm") == "warning":
        add("WARNING", "PAGASA thunderstorm warning covers Consolacion")
    elif pg.get("thunderstorm") == "advisory":
        add("WATCH", "PAGASA thunderstorm advisory covers Consolacion")

    # Learned from the flood log: conditions like a past flood on our street.
    # Rain counts the hours already fallen (recent_hourly) plus the forecast.
    forecast_start = hours[0]["time"] if hours else None
    series = sorted(
        [h for h in inputs.get("recent_hourly", [])
         if forecast_start is None or _t(h["time"]) < _t(forecast_start)] + hours,
        key=lambda h: h["time"])
    tide_points = [(_t(x["time"]), x["height_m"])
                   for x in inputs.get("tide_series") or inputs.get("high_tides", [])]
    for rule in th.get("learned", []):
        for i in range(max(len(series) - 5, 0)):
            window = series[i:i + 6]
            end = _t(window[-1]["time"]) + timedelta(hours=1)
            if end < now:
                continue  # already over
            rain6 = sum(h.get("precip_mm") or 0 for h in window)
            near = [hgt for tt, hgt in tide_points
                    if end - timedelta(hours=2) <= tt <= end + timedelta(hours=1)]
            tide = max(near, default=None)
            tide_ok = rule["tide_m"] is None or tide is None or tide >= rule["tide_m"]
            if rain6 >= rule["rain_6h_mm"] and tide_ok:
                tide_txt = f" with the tide at {tide:.2f} m" if rule["tide_m"] is not None and tide else ""
                add(rule["level"], f"Like {rule['label']}: {rain6:.1f} mm of rain in 6 hours"
                    f"{tide_txt} (by {when(end)})", "learned")
                break

    # Saturated ground: a full river and soaked soil make the same rain worse.
    past72 = inputs.get("past72_mm")
    wet = past72 is not None and past72 >= th["wet_ground_72h_mm"]
    if wet:
        for r in reasons:
            if r["kind"] == "rain":
                bumped = _bump(r["level"])
                if bumped != r["level"]:
                    r["level"] = bumped
                    r["text"] += " (raised one level: ground already saturated)"
        add("NORMAL", f"{past72:.0f} mm of rain in the past 72 hours - ground and river are already full")

    level = max((r["level"] for r in reasons), key=rank, default="NORMAL")
    reasons.sort(key=lambda r: rank(r["level"]), reverse=True)
    return {
        "level": level,
        "reasons": reasons,
        "metrics": {
            "max_rain_3h_mm": round(max3, 1),
            "rain_next_24h_mm": round(total24, 1),
            "max_rain_3h_at_high_tide_mm": round(max3_tide, 1),
            "past72_mm": None if past72 is None else round(past72, 1),
            "max_thunder_prob": thunder,
            "max_gust_kmh": gust,
            "max_heat_index_c": heat,
            "wet_ground": wet,
        },
    }
