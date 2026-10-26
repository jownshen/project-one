"""PAGASA official warnings, read from the public website (there is no API).

- Visayas regional page: Heavy Rainfall Warnings + Thunderstorm advisories
- Tropical cyclone bulletin page: active storm + wind signals

The site layout can change. Every parser fails soft: the run continues
without PAGASA data and the dashboard shows the source as unavailable.
"""
import re
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

REGIONAL_URL = "https://www.pagasa.dost.gov.ph/regional-forecast/visprsd"
TCB_URL = "https://www.pagasa.dost.gov.ph/tropical-cyclone/severe-weather-bulletin"
HEADERS = {"User-Agent": "Mozilla/5.0 (cansaga-weather; personal flood watch)"}

ISSUED_RE = re.compile(
    r"Issued at\s*:?\s*(\d{1,2}:\d{2}\s*[AP]M),?\s*(\d{1,2}\s+[A-Za-z]+,?\s+\d{4})", re.I)
COLOR_RE = re.compile(r"\b(RED|ORANGE|YELLOW)\b", re.I)
SIGNAL_RE = re.compile(r"(?:Wind\s+Signal\s+No\.?|TCWS\s*(?:No\.?|#))\s*(\d)", re.I)
TC_NAME_RE = re.compile(
    r"\b(Super Typhoon|Typhoon|Severe Tropical Storm|Tropical Storm|Tropical Depression)\s+[\"“]?([A-Z][A-Za-z]+)")


# PAGASA's three thunderstorm tiers. A watch is a broad 12-hour outlook by
# province; advisories and warnings name towns and cover the next 1-2 hours.
STORM_RANK = {"watch": 1, "advisory": 2, "warning": 3}
STORM_VALID = {"watch": timedelta(hours=12), "advisory": timedelta(hours=3),
               "warning": timedelta(hours=3)}


def _storm_kind(text):
    m = re.search(r"thunderstorm\s+(watch|advisory|warning)", text, re.I)
    return m.group(1).lower() if m else "advisory"


def _norm(s):
    return re.sub(r"[\s#_-]", "", s).lower()


def _fetch(url):
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def _issued(text, tz):
    m = ISSUED_RE.search(text)
    if not m:
        return None
    try:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2).replace(',', '')}", "%I:%M %p %d %B %Y")
        return dt.replace(tzinfo=tz)
    except ValueError:
        return None


def covers(text, province, town):
    """'town' if the text names the town (or the whole province / Metro Cebu),
    'province' if it names only other parts of the province, else None."""
    flat = _norm(text)
    p, t = _norm(province), _norm(town)
    if p not in flat and t not in flat:
        return None
    if t in flat or f"metro{p}" in flat:
        return "town"
    # Province named with a list of towns in brackets that excludes ours.
    if re.search(rf"{p}\(", flat):
        bare = re.sub(rf"{p}\([^)]*\)", "", flat)
        return "town" if p in bare else "province"
    return "town"  # whole province named


def parse_regional(html, province, town, now):
    soup = BeautifulSoup(html, "html.parser")
    out = {"rainfall": None, "rainfall_text": None, "thunderstorm": None, "thunderstorm_text": None}

    rain = soup.find(id="rainfalls")
    rain_text = rain.get_text(" ", strip=True) if rain else ""
    if rain_text and "no heavy rainfall warning" not in rain_text.lower():
        issued = _issued(rain_text, now.tzinfo)
        if issued is None or now - issued <= timedelta(hours=6):
            # Each colour heading is followed by the areas it applies to.
            parts = COLOR_RE.split(rain_text)
            order = {"yellow": 1, "orange": 2, "red": 3}
            for color, segment in zip(parts[1::2], parts[2::2]):
                color = color.lower()
                if covers(segment, province, town) == "town":
                    if order[color] > order.get(out["rainfall"], 0):
                        out["rainfall"] = color
            if out["rainfall"]:
                out["rainfall_text"] = rain_text[:600]

    storms = soup.find(id="thunderstorms")
    if storms:
        for block in storms.find_all("div", recursive=False) or [storms]:
            text = block.get_text(" ", strip=True)
            kind = _storm_kind(text)
            issued = _issued(text, now.tzinfo)
            if issued is not None and now - issued > STORM_VALID[kind]:
                continue  # stale
            if covers(text, province, town) == "town":
                if STORM_RANK[kind] > STORM_RANK.get(out["thunderstorm"], 0):
                    out["thunderstorm"], out["thunderstorm_text"] = kind, text[:600]
    return out


def parse_tcb(html, province, town):
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    out = {"tc_active": False, "tc_name": None, "tcws": None, "tcws_nearby": None}
    if re.search(r"No Active Tropical Cyclone", text, re.I):
        return out
    out["tc_active"] = True
    m = TC_NAME_RE.search(text)
    if m:
        out["tc_name"] = f"{m.group(1)} {m.group(2)}"
    matches = list(SIGNAL_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        signal = int(m.group(1))
        hit = covers(text[m.end():end], province, town)
        if hit == "town":
            out["tcws"] = max(out["tcws"] or 0, signal)
        elif hit == "province":
            out["tcws_nearby"] = max(out["tcws_nearby"] or 0, signal)
    return out


def fetch(province, town, now):
    result = parse_regional(_fetch(REGIONAL_URL), province, town, now)
    result.update(parse_tcb(_fetch(TCB_URL), province, town))
    return result
