import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import flood_log  # noqa: E402
from make_fixtures import NOW, hours, scenario  # noqa: E402
from risk import evaluate  # noqa: E402

CFG = yaml.safe_load((ROOT / "config.yaml").read_text())
PHT = timezone(timedelta(hours=8))

ANKLE = {"id": "ankle", "time": "2026-10-24T23:00:00+08:00", "depth": "ankle",
         "conditions": {"rain_6h_mm": 6.9, "tide_max_m": 1.31}}
TYPHOON = {"id": "typhoon", "time": "2025-11-04T05:00:00+08:00", "depth": "first_floor",
           "conditions": {"rain_6h_mm": 123.4, "tide_max_m": 0.7}}


def rules():
    return flood_log.learn([ANKLE, TYPHOON], CFG["learning"])


def test_learn_one_rule_per_flood():
    ankle, typhoon = rules()
    assert ankle["level"] == "WARNING" and ankle["rain_6h_mm"] == 5.9 and ankle["tide_m"] == 1.21
    # Low tide during the typhoon flood: rule is rain-only.
    assert typhoon["level"] == "EMERGENCY" and typhoon["rain_6h_mm"] == 104.9
    assert typhoon["tide_m"] is None


def test_unknown_depth_is_skipped():
    assert flood_log.learn([{**ANKLE, "depth": "puddle"}], CFG["learning"]) == []


def tide_series(height_now):
    return [{"time": (NOW + timedelta(hours=h)).isoformat(), "height_m": height_now}
            for h in range(-6, 30)]


def th():
    return {**CFG["thresholds"], "learned": rules()}


def test_learned_rule_fires_on_rain_at_high_tide():
    inputs = scenario(hours({1: 2.0, 2: 2.0, 3: 2.0}))  # 6 mm: below the 7.5 mm/3 h WATCH line
    inputs["tide_series"] = tide_series(1.3)
    result = evaluate(inputs, th())
    assert result["level"] == "WARNING"
    assert "Oct 24, 2026 flood" in result["reasons"][0]["text"]


def test_learned_rule_needs_the_high_tide():
    inputs = scenario(hours({1: 2.0, 2: 2.0, 3: 2.0}), high_tides=[])
    inputs["tide_series"] = tide_series(0.8)
    assert evaluate(inputs, th())["level"] == "NORMAL"


def test_rain_already_fallen_counts():
    # 5 mm fell in the last 4 hours, 1 mm more forecast, high tide now.
    inputs = scenario(hours({0: 1.0}))
    inputs["recent_hourly"] = [{"time": (NOW - timedelta(hours=h)).isoformat(), "precip_mm": 1.25}
                               for h in range(1, 5)]
    inputs["tide_series"] = tide_series(1.3)
    assert evaluate(inputs, th())["level"] == "WARNING"


def test_typhoon_rule_fires_without_tide():
    inputs = scenario(hours({h: 20.0 for h in range(2, 8)}))  # 120 mm in 6 h
    inputs["tide_series"] = tide_series(0.3)
    result = evaluate(inputs, th())
    assert result["level"] == "EMERGENCY"
    assert any("Nov 4, 2025 flood" in r["text"] for r in result["reasons"])


def test_conditions_at():
    t = datetime(2026, 10, 24, 23, tzinfo=PHT)
    rain = [{"time": (t - timedelta(hours=h)).isoformat(), "precip_mm": 1.0} for h in range(1, 10)]
    tides = [{"time": (t - timedelta(hours=2)).isoformat(), "height_m": 1.31},
             {"time": (t - timedelta(hours=5)).isoformat(), "height_m": 1.9}]
    c = flood_log.conditions_at(t, rain, tides)
    assert c["rain_3h_mm"] == 3.0 and c["rain_6h_mm"] == 6.0
    assert c["tide_max_m"] == 1.31  # the 1.9 m tide was too early to count
