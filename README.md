# Cansaga Flood Watch

Flood and severe-weather early warning for Barangay Cansaga, Consolacion, Cebu.
Every 30 minutes a GitHub Action checks:

| Source | What it gives | Key needed |
|---|---|---|
| Google Weather API | Hourly forecast for the exact point, from Google DeepMind's WeatherNext AI blended with traditional models | `GOOGLE_MAPS_API_KEY` |
| PAGASA website | Heavy rainfall warnings, thunderstorm advisories, typhoon wind signals for Consolacion | none |
| Open-Meteo | Past 72 h rain (how full the ground and river are), tides, backup forecast | none |

It turns these into one level, **NORMAL / WATCH / WARNING / EMERGENCY**, shows it on a
phone-friendly dashboard (GitHub Pages), and sends **email + SMS only for EMERGENCY**.

This adds to PAGASA and Consolacion MDRRMO advisories. It doesn't replace them.

## How the level is decided

Thresholds live in `config.yaml` and follow PAGASA's rainfall warning system
(yellow 7.5–15 mm in 3 h, orange 15–30, red >30) and the Dec 2011 Cansaga River flood (185 mm in a day).

| Level | Any one of |
|---|---|
| WATCH | ≥7.5 mm in 3 h · ≥50 mm in 24 h · ≥60% thunderstorm chance · PAGASA yellow or thunderstorm advisory · heat index ≥39 °C · wind signal elsewhere in Cebu |
| WARNING | ≥15 mm in 3 h · ≥100 mm in 24 h · ≥7.5 mm in 3 h within 2 h of high tide · PAGASA orange · Signal No. 1 · gusts ≥60 km/h · heat index ≥42 °C |
| EMERGENCY | ≥30 mm in 3 h · ≥150 mm in 24 h · ≥15 mm in 3 h at high tide · PAGASA red · Signal No. 2+ · ≥20 mm of rain right now |

If more than 100 mm fell in the past 72 h, every rain trigger moves up one level.

**Alerts:** an alert is sent when EMERGENCY starts, then a reminder every 6 h while it lasts.
"All clear" is sent once the level drops to WATCH or below.
If the forecast can't be fetched 3 runs in a row, you get an email that the dashboard has gone quiet.

## Flood log: learning from real floods

`data/flood_log.yaml` records floods seen on our street (depth, time, rain, nearby river readings).
After adding one, run `python src/flood_log.py`. It:

1. looks up the rain (past 1/3/6/24/72 h) and tide at the time of each flood
2. replays the original rules to show whether they would have warned 6 h and 1 h before
3. turns each flood into a **learned rule**: "≥85% of that 6-hour rain (and, if the tide was high,
   a tide within 10 cm of it) means WARNING / EMERGENCY". Ankle/knee-deep floods give WARNING,
   waist-deep and worse give EMERGENCY.

The 30-minute check applies the learned rules to rain that has already fallen plus the forecast.
With more floods logged, the rules sharpen; with a few dozen, a proper statistical model becomes possible.

## Tuning
After the first few rainy weeks, compare the dashboard with what actually happened on your street,
and adjust `config.yaml`. The forecast point is Cansaga Barangay Hall; since this repo is public,
keep it a public landmark, never a home address. Local hotline numbers go under `dashboard.hotlines`.

## Possible upgrades
- **WeatherNext 3 ensemble (BigQuery):** probability of heavy rain 3–7 days ahead.
- **Google Flood Hub API (waitlist):** add if its flash-flood or river forecasts cover Metro Cebu.
- **Rain gauge or water-level sensor** at home for true street-level readings.
