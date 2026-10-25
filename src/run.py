"""One check: fetch sources -> risk level -> dashboard JSON -> alerts.

  python src/run.py                         # normal run (GitHub Actions)
  python src/run.py --dry-run               # print alerts instead of sending
  python src/run.py --fixture tests/fixtures/dec2011_storm.json
  python src/run.py --force-level EMERGENCY # send a real test alert
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import notify  # noqa: E402
from risk import LEVELS, evaluate  # noqa: E402
from sources import google_weather, open_meteo, pagasa  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LATEST = ROOT / "docs/data/latest.json"
HISTORY = ROOT / "docs/data/history.json"
STATE = ROOT / "docs/data/state.json"  # published with the site, read back next run
SECRET_VARS = ("GOOGLE_MAPS_API_KEY", "SMTP_PASSWORD", "SEMAPHORE_API_KEY", "NTFY_TOPIC")


def scrub(text):
    """Never let a key reach the public dashboard (requests errors include URLs)."""
    text = str(text)
    for var in SECRET_VARS:
        if os.environ.get(var):
            text = text.replace(os.environ[var], "***")
    return text[:300]


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def gather(cfg, now):
    loc, tz = cfg["location"], cfg["timezone"]
    now_iso = now.isoformat()
    inputs = {"now": now_iso, "hourly": [], "current": {}, "past72_mm": None,
              "high_tides": [], "pagasa": {}}
    sources, extra = {}, {"tide_series": []}

    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if key:
        try:
            inputs["hourly"] = google_weather.fetch_hourly(key, loc["lat"], loc["lon"], hours=48)
            inputs["current"] = google_weather.fetch_current(key, loc["lat"], loc["lon"])
            sources["forecast"] = {"ok": True, "name": "Google Weather API (WeatherNext AI)"}
        except Exception as e:
            sources["forecast"] = {"ok": False, "name": "Google Weather API", "error": scrub(e)}
    else:
        sources["forecast"] = {"ok": False, "name": "Google Weather API",
                               "error": "GOOGLE_MAPS_API_KEY not set"}

    try:
        past72, backup_hours = open_meteo.fetch_rain(loc["lat"], loc["lon"], tz, now_iso)
        inputs["past72_mm"] = past72
        sources["past_rain"] = {"ok": True, "name": "Open-Meteo (past 72 h rain)"}
        if not inputs["hourly"]:
            inputs["hourly"] = backup_hours[:48]
            sources["forecast_backup"] = {"ok": True, "name": "Open-Meteo backup forecast"}
    except Exception as e:
        sources["past_rain"] = {"ok": False, "name": "Open-Meteo", "error": scrub(e)}

    try:
        tides, series = open_meteo.fetch_high_tides(loc["tide_lat"], loc["tide_lon"], tz)
        inputs["high_tides"], extra["tide_series"] = tides, series
        sources["tides"] = {"ok": True, "name": "Open-Meteo Marine (tides)"}
    except Exception as e:
        sources["tides"] = {"ok": False, "name": "Open-Meteo Marine", "error": scrub(e)}

    try:
        inputs["pagasa"] = pagasa.fetch(cfg["pagasa"]["province"], cfg["pagasa"]["town"], now)
        sources["pagasa"] = {"ok": True, "name": "PAGASA warnings"}
    except Exception as e:
        sources["pagasa"] = {"ok": False, "name": "PAGASA warnings", "error": scrub(e)}

    return inputs, sources, extra


def build_dashboard(cfg, now, inputs, result, sources, extra):
    end = now + timedelta(hours=48)

    def in_window(t):
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return now - timedelta(hours=1) < dt <= end

    return {
        "generated_at": now.isoformat(),
        "location": {k: cfg["location"][k] for k in ("name", "lat", "lon")},
        "level": result["level"],
        "reasons": result["reasons"],
        "metrics": result["metrics"],
        "current": inputs.get("current") or {},
        "hourly": [
            {k: h.get(k) for k in ("time", "precip_mm", "precip_prob", "thunder_prob",
                                   "gust_kmh", "heat_index_c", "condition")}
            for h in inputs["hourly"] if in_window(h["time"])
        ],
        "high_tides": [t for t in inputs["high_tides"] if in_window(t["time"])],
        "tide_series": [t for t in extra.get("tide_series", []) if in_window(t["time"])],
        "pagasa": inputs.get("pagasa") or {},
        "sources": sources,
        "hotlines": cfg["dashboard"]["hotlines"],
        "thresholds": cfg["thresholds"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print alerts, don't send")
    ap.add_argument("--fixture", help="evaluate a saved input file and print the result")
    ap.add_argument("--force-level", choices=LEVELS, help="override the level (test alerts)")
    ap.add_argument("--test-channel", choices=["email", "sms", "push"],
                    help="with --force-level: send the test to this channel only")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    th, tz = cfg["thresholds"], ZoneInfo(cfg["timezone"])

    if args.fixture:
        inputs = json.loads(Path(args.fixture).read_text())
        print(json.dumps(evaluate(inputs, th), indent=1, ensure_ascii=False))
        return

    now = datetime.now(tz).replace(microsecond=0)
    inputs, sources, extra = gather(cfg, now)
    result = evaluate(inputs, th)
    # A forced test changes only what the alert says; the dashboard keeps the real level.
    alert_result = result
    if args.force_level:
        alert_result = {**result, "level": args.force_level, "reasons": [
            {"level": args.force_level, "kind": "test",
             "text": "TEST ALERT from the setup check - no real risk"}]}

    state = load_json(STATE, {})
    location = cfg["location"]["name"]
    dashboard_url = os.environ.get("DASHBOARD_URL", "")

    # A run with no forecast at all means the dashboard is going stale.
    if not inputs["hourly"]:
        state["failures"] = state.get("failures", 0) + 1
        if state["failures"] == cfg["alerts"]["failure_alert_after"]:
            msg = (f"The weather check for {location} has failed {state['failures']} times in a row."
                   f"\n\nSources: {json.dumps(sources, indent=1)}\n\nThe dashboard is not updating.")
            if args.dry_run:
                print(f"[dry-run] would email:\n{msg}")
            else:
                print(notify.send_email("Cansaga weather check is failing", msg))
    else:
        state["failures"] = 0

    if args.force_level:
        kind = "alert"  # a test always sends, ignoring the cooldown
    else:
        kind = notify.decide(state, result["level"], now, cfg["alerts"])
    if kind:
        subject, body, short = notify.compose(kind, alert_result, location, dashboard_url)
        if args.force_level:
            subject = f"[TEST] {subject}"
        if args.dry_run:
            print(f"[dry-run] would send {kind}:\n{subject}\n{body}\nSMS: {short}")
        else:
            priority = "default" if kind == "all_clear" else "urgent"
            print(kind, notify.send_all(subject, body, short, priority, only=args.test_channel))
    if kind and not args.test_channel:  # a single-channel test leaves alert state alone
        state["alert"] = (None if kind == "all_clear"
                          else {"level": alert_result["level"], "time": now.isoformat()})
    state["last_level"] = result["level"]
    state["last_run"] = now.isoformat()

    latest = build_dashboard(cfg, now, inputs, result, sources, extra)
    history = load_json(HISTORY, [])
    history.append({"time": now.isoformat(), "level": result["level"],
                    "rain_next_24h_mm": result["metrics"]["rain_next_24h_mm"],
                    "past72_mm": result["metrics"]["past72_mm"]})
    cutoff = (now - timedelta(days=cfg["dashboard"]["history_days"])).isoformat()
    history = [h for h in history if h["time"] >= cutoff]

    write_json(LATEST, latest)
    write_json(HISTORY, history)
    if not args.dry_run:
        write_json(STATE, state)
    print(f"{now:%Y-%m-%d %H:%M} level={result['level']} "
          + " ".join(f"{k}={'ok' if v['ok'] else 'FAIL'}" for k, v in sources.items()))


if __name__ == "__main__":
    main()
