import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import notify  # noqa: E402
import run  # noqa: E402
from make_fixtures import NOW, hours, scenario, tides  # noqa: E402
from risk import evaluate  # noqa: E402
from sources import pagasa  # noqa: E402

CFG = yaml.safe_load((ROOT / "config.yaml").read_text())
TH = CFG["thresholds"]
FIX = ROOT / "tests/fixtures"
PHT = timezone(timedelta(hours=8))


@pytest.mark.parametrize("name, expected", [
    ("normal_day", "NORMAL"),
    ("dec2011_storm", "EMERGENCY"),
    ("typhoon_signal2", "EMERGENCY"),
    ("heat_day", "WARNING"),
])
def test_fixture_scenarios(name, expected):
    inputs = json.loads((FIX / f"{name}.json").read_text())
    assert evaluate(inputs, TH)["level"] == expected


def test_dec2011_storm_totals():
    result = evaluate(json.loads((FIX / "dec2011_storm.json").read_text()), TH)
    assert result["metrics"]["rain_next_24h_mm"] == 185


def test_moderate_rain_away_from_high_tide_is_watch():
    inputs = scenario(hours({3: 3, 4: 3, 5: 3}), high_tides=tides(15, 27))
    assert evaluate(inputs, TH)["level"] == "WATCH"


def test_same_rain_at_high_tide_is_warning():
    inputs = scenario(hours({3: 3, 4: 3, 5: 3}), high_tides=tides(6, 18))
    result = evaluate(inputs, TH)
    assert result["level"] == "WARNING"
    assert "high tide" in result["reasons"][0]["text"]


def test_weak_high_tide_is_ignored():
    weak = [{"time": (NOW + timedelta(hours=6)).isoformat(), "height_m": 0.3}]
    inputs = scenario(hours({3: 3, 4: 3, 5: 3}), high_tides=weak)
    assert evaluate(inputs, TH)["level"] == "WATCH"


def test_saturated_ground_raises_rain_level():
    rain = {3: 6, 4: 6, 5: 6}  # 18 mm / 3 h = WARNING on dry ground
    assert evaluate(scenario(hours(rain), high_tides=tides(20)), TH)["level"] == "WARNING"
    wet = evaluate(scenario(hours(rain), past72=120, high_tides=tides(20)), TH)
    assert wet["level"] == "EMERGENCY"
    assert wet["metrics"]["wet_ground"]


def test_saturated_ground_does_not_raise_heat():
    assert evaluate(scenario(hours(heat=43), past72=150), TH)["level"] == "WARNING"


def test_current_heavy_rain_is_emergency():
    inputs = scenario(hours(), current={"precip_mm": 25})
    assert evaluate(inputs, TH)["level"] == "EMERGENCY"


@pytest.mark.parametrize("pg, expected", [
    ({"rainfall": "yellow"}, "WATCH"),
    ({"rainfall": "orange"}, "WARNING"),
    ({"rainfall": "red"}, "EMERGENCY"),
    ({"tcws": 1}, "WARNING"),
    ({"tcws_nearby": 1}, "WATCH"),
    ({"tc_active": True}, "NORMAL"),
    ({"thunderstorm": "watch"}, "WATCH"),
    ({"thunderstorm": "advisory"}, "WATCH"),
    ({"thunderstorm": "warning"}, "WARNING"),
])
def test_pagasa_levels(pg, expected):
    assert evaluate(scenario(hours(), pagasa=pg), TH)["level"] == expected


def test_past_hours_beyond_horizon_ignored():
    rain = {30: 40}  # heavy rain 30 h out is beyond the 24 h horizon
    assert evaluate(scenario(hours(rain)), TH)["level"] == "NORMAL"


# --- alert decisions -------------------------------------------------------

ALERTS = CFG["alerts"]


def test_alert_lifecycle():
    t0 = NOW
    state = {}
    assert notify.decide(state, "WARNING", t0, ALERTS) is None
    assert notify.decide(state, "EMERGENCY", t0, ALERTS) == "alert"
    state["alert"] = {"level": "EMERGENCY", "time": t0.isoformat()}
    assert notify.decide(state, "EMERGENCY", t0 + timedelta(hours=2), ALERTS) is None
    assert notify.decide(state, "EMERGENCY", t0 + timedelta(hours=6), ALERTS) == "reminder"
    # Dropping one level is not enough to stand down (avoids flapping).
    assert notify.decide(state, "WARNING", t0 + timedelta(hours=1), ALERTS) is None
    assert notify.decide(state, "WATCH", t0 + timedelta(hours=1), ALERTS) == "all_clear"


def test_compose_sms_is_short_and_not_test_prefixed():
    result = evaluate(json.loads((FIX / "dec2011_storm.json").read_text()), TH)
    _, body, sms = notify.compose("alert", result, "Cansaga", "https://example.org")
    assert len(sms) <= 160  # one SMS
    assert "http" not in sms  # PH networks block texts with links
    assert not sms.upper().startswith("TEST")  # Semaphore drops messages starting with TEST
    assert "EMERGENCY" in body and "https://example.org" in body


def test_scrub_hides_keys(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "AIzaSECRET123")
    assert "AIzaSECRET123" not in run.scrub("403 for url ...?key=AIzaSECRET123&location")


# --- PAGASA parsing --------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("#Cebu(Barili, CityOfCarcar, Aloguinsan)", "province"),
    ("#Cebu(Sibonga, MandaueCity, Consolacion, Liloan)", "town"),
    ("Yellow warning: Cebu, Bohol", "town"),
    ("Metro Cebu", "town"),
    ("#Bohol(Tubigon)", None),
])
def test_covers(text, expected):
    assert pagasa.covers(text, "Cebu", "Consolacion") == expected


def test_real_regional_page_fresh_advisory():
    # Advisory No. 22 names Consolacion, so it outranks the province-wide watch.
    html = (FIX / "pagasa_visprsd_2026-10-24.html").read_text()
    out = pagasa.parse_regional(html, "Cebu", "Consolacion", datetime(2026, 10, 24, 23, 0, tzinfo=PHT))
    assert out["rainfall"] is None
    assert out["thunderstorm"] == "advisory"


def test_real_regional_page_watch_lasts_12_hours():
    html = (FIX / "pagasa_visprsd_2026-10-24.html").read_text()
    out = pagasa.parse_regional(html, "Cebu", "Consolacion", datetime(2026, 10, 25, 9, 0, tzinfo=PHT))
    assert out["thunderstorm"] == "watch"


def test_real_regional_page_stale_watch_ignored():
    html = (FIX / "pagasa_visprsd_2026-10-24.html").read_text()
    out = pagasa.parse_regional(html, "Cebu", "Consolacion", datetime(2026, 10, 25, 12, 0, tzinfo=PHT))
    assert out["thunderstorm"] is None


def _storms(*blocks):
    return '<div id="thunderstorms">' + "".join(f"<div>{b}</div>" for b in blocks) + "</div>"


def test_advisory_outranks_watch_and_expires_after_3_hours():
    html = _storms(
        "Thunderstorm Watch #VISPRSD Issued at 10:00 AM 26 October 2026 Thunderstorm is MORE LIKELY over #Cebu",
        "Thunderstorm Advisory No. 9 #VISPRSD Issued at 11:00 AM 26 October 2026 expected over #Cebu(Consolacion, Liloan)")
    at = lambda h: pagasa.parse_regional(html, "Cebu", "Consolacion", datetime(2026, 10, 26, h, 0, tzinfo=PHT))
    assert at(12)["thunderstorm"] == "advisory"
    assert at(15)["thunderstorm"] == "watch"


def test_synthetic_red_rainfall_warning():
    html = ('<div id="rainfalls"><div>Heavy Rainfall Warning No. 4 #VISPRSD<br/>'
            'Issued at 2:00 PM 25 October 2026<br/>RED WARNING LEVEL: #Cebu(Consolacion, Liloan)'
            ' ORANGE WARNING LEVEL: #Bohol(Tubigon)</div></div>')
    out = pagasa.parse_regional(html, "Cebu", "Consolacion", datetime(2026, 10, 25, 15, 0, tzinfo=PHT))
    assert out["rainfall"] == "red"


def test_real_tcb_page_no_active_storm():
    out = pagasa.parse_tcb((FIX / "pagasa_tcb_none.html").read_text(), "Cebu", "Consolacion")
    assert out == {"tc_active": False, "tc_name": None, "tcws": None, "tcws_nearby": None}


def test_synthetic_tcb_signal_for_cebu():
    html = ("<p>Typhoon ODETTE</p><p>TCWS No. 3 Visayas: Bohol, Cebu</p>"
            "<p>TCWS No. 1 Visayas: Samar</p>")
    out = pagasa.parse_tcb(html, "Cebu", "Consolacion")
    assert out["tc_active"] and out["tcws"] == 3 and out["tc_name"] == "Typhoon ODETTE"


def test_sms_error_redacts_phone_numbers():
    body = '{"number": ["09171234567 is invalid", "+639181234567"]}'
    out = notify._redact(body)
    assert "9171234567" not in out and "9181234567" not in out
