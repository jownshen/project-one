"""Flood log: floods seen on our street, each looked up against the rain and
tide at the time, then turned into "learned" rules the risk engine uses.

  python src/flood_log.py     # after editing data/flood_log.yaml

Each flood becomes one rule: "this much rain in 6 hours (and, if the tide was
high, a tide this high) flooded the street before". The rule fires a little
below what was seen (see `learning` in config.yaml), so a repeat is caught early.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from risk import evaluate  # noqa: E402
from sources import open_meteo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data/flood_log.yaml"
ENRICHED = ROOT / "data/flood_log_enriched.json"
REPLAY_LEADS_H = (6, 1)


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _rain_between(rain, start, end):
    return round(sum(h["precip_mm"] for h in rain if start <= _t(h["time"]) < end), 1)


def conditions_at(t, rain, tides):
    """Rain in the hours before `t`, and the highest tide from 2 h before to 1 h after."""
    near = [x for x in tides if t - timedelta(hours=2) <= _t(x["time"]) <= t + timedelta(hours=1)]
    top = max(near, key=lambda x: x["height_m"], default=None)
    return {
        **{f"rain_{h}h_mm": _rain_between(rain, t - timedelta(hours=h), t) for h in (1, 3, 6, 24, 72)},
        "tide_max_m": top["height_m"] if top else None,
        "tide_max_time": top["time"] if top else None,
    }


def replay(t, rain, tides, th):
    """What the original rules would have said `lead` hours before, given a perfect forecast."""
    out = {}
    for lead in REPLAY_LEADS_H:
        now = t - timedelta(hours=lead)
        inputs = {
            "now": now.isoformat(),
            "hourly": [{"time": h["time"], "precip_mm": h["precip_mm"]} for h in rain
                       if now - timedelta(hours=1) < _t(h["time"]) <= now + timedelta(hours=24)],
            "past72_mm": _rain_between(rain, now - timedelta(hours=72), now),
            "high_tides": open_meteo.high_tides(tides),
        }
        result = evaluate(inputs, {**th, "learned": []})
        out[f"{lead}h_before"] = {
            "level": result["level"],
            "reason": result["reasons"][0]["text"] if result["reasons"] else None,
        }
    return out


def enrich(event, cfg):
    loc, tz = cfg["location"], cfg["timezone"]
    t = _t(event["time"])
    start, end = (t - timedelta(days=4)).date(), (t + timedelta(days=1)).date()
    rain, rain_source = open_meteo.fetch_rain_range(loc["lat"], loc["lon"], tz, start, end)
    tides = open_meteo.fetch_tide_range(loc["tide_lat"], loc["tide_lon"], tz, start, end)
    window = [h for h in rain if t - timedelta(hours=12) <= _t(h["time"]) < t + timedelta(hours=6)]
    return {
        **event,
        "conditions": conditions_at(t, rain, tides),
        "replay": replay(t, rain, tides, cfg["thresholds"]),
        "hourly_rain": window,
        "rain_source": rain_source,
    }


def learn(enriched, lcfg):
    """One rule per flood: fire at a margin below the rain (and high tide) seen then."""
    rules = []
    for e in enriched:
        level = lcfg["depth_levels"].get(e["depth"])
        c = e.get("conditions") or {}
        if not level or c.get("rain_6h_mm") is None:
            continue
        tide = c.get("tide_max_m")
        rules.append({
            "level": level,
            "event": e["id"],
            "label": f"the {_t(e['time']):%b %-d, %Y} flood ({e['depth'].replace('_', ' ')} deep)",
            "rain_6h_mm": round(c["rain_6h_mm"] * lcfg["rain_margin"], 1),
            # Only a high tide counts as part of the cause; a low tide was a bystander.
            "tide_m": (round(tide - lcfg["tide_margin_m"], 2)
                       if tide is not None and tide >= lcfg["tide_relevant_m"] else None),
        })
    return rules


def load_rules(cfg):
    try:
        enriched = json.loads(ENRICHED.read_text())
    except FileNotFoundError:
        return [], []
    return enriched, learn(enriched, cfg["learning"])


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    events = yaml.safe_load(LOG.read_text())["events"]
    enriched = [enrich(e, cfg) for e in sorted(events, key=lambda e: e["time"])]
    ENRICHED.write_text(json.dumps(enriched, indent=1, ensure_ascii=False) + "\n")
    for e in enriched:
        c = e["conditions"]
        print(f"\n{e['id']}: {e['depth']} deep at {e['time']}")
        print(f"  rain before: 1h {c['rain_1h_mm']} · 3h {c['rain_3h_mm']} · 6h {c['rain_6h_mm']}"
              f" · 24h {c['rain_24h_mm']} · 72h {c['rain_72h_mm']} mm  ({e['rain_source']})")
        print(f"  highest tide nearby: {c['tide_max_m']} m at {c['tide_max_time']}")
        for k, v in e["replay"].items():
            print(f"  original rules, {k.replace('_', ' ')}: {v['level']} - {v['reason']}")
    print("\nLearned rules:")
    for r in learn(enriched, cfg["learning"]):
        tide = f" and tide >= {r['tide_m']} m" if r["tide_m"] is not None else ""
        print(f"  {r['level']}: >= {r['rain_6h_mm']} mm in 6 h{tide}  (from {r['label']})")


if __name__ == "__main__":
    main()
