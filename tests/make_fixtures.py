"""Regenerates the scenario fixtures in tests/fixtures/. Run from the repo root:
    python tests/make_fixtures.py
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

PHT = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=PHT)
OUT = Path(__file__).parent / "fixtures"


def hours(rain=None, heat=34, gust=20, thunder=10):
    rain = rain or {}
    return [{
        "time": (NOW + timedelta(hours=i)).isoformat(),
        "precip_mm": rain.get(i, 0.0),
        "precip_prob": 90 if rain.get(i) else 5,
        "thunder_prob": thunder,
        "gust_kmh": gust,
        "heat_index_c": heat,
    } for i in range(48)]


def tides(*offsets):
    return [{"time": (NOW + timedelta(hours=h)).isoformat(), "height_m": 1.5} for h in offsets]


def scenario(hourly, past72=5.0, high_tides=None, pagasa=None, current=None):
    return {"now": NOW.isoformat(), "hourly": hourly, "current": current or {},
            "past72_mm": past72, "high_tides": high_tides or tides(10, 22, 34, 46),
            "pagasa": pagasa or {}}


SCENARIOS = {
    "normal_day": scenario(hours()),
    # Dec 2011: 185 mm in a day, the night the Cansaga River overflowed.
    "dec2011_storm": scenario(
        hours({h: mm for h, mm in zip(range(6, 24), [3, 5, 8, 12, 18, 25, 30, 22, 15, 12,
                                                    10, 8, 6, 4, 3, 2, 1, 1])}),
        past72=60),
    "typhoon_signal2": scenario(hours({h: 2.0 for h in range(10)}, gust=55),
                                pagasa={"tc_active": True, "tc_name": "Typhoon Example", "tcws": 2}),
    "heat_day": scenario(hours(heat=43)),
}

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, data in SCENARIOS.items():
        (OUT / f"{name}.json").write_text(json.dumps(data, indent=1) + "\n")
        print("wrote", name)
