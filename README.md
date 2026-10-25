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

## Setup

### 1. Google Weather API key
1. Go to <https://console.cloud.google.com/>, create a project (e.g. `cansaga-weather`), and enable billing.
2. Enable **Weather API**: APIs & Services → Library → search "Weather API" → Enable.
3. APIs & Services → Credentials → Create credentials → API key. Edit it → *API restrictions* → restrict to **Weather API**.
4. Optional: set a budget alert (Billing → Budgets) at a small amount so you're never surprised.

### 2. Email (Gmail app password)
Google Account → Security → 2-Step Verification (must be on) → **App passwords** → create one named "cansaga-weather".

### 3. SMS (Semaphore, optional)
Sign up at <https://semaphore.co>, load credit (about ₱0.50 per SMS), copy the API key.
Free alternative: install the **ntfy** app, subscribe to a hard-to-guess topic name, and set `NTFY_TOPIC` instead.

### 4. Add the secrets to GitHub
Repo → Settings → Secrets and variables → Actions → *New repository secret*:

| Secret | Example |
|---|---|
| `GOOGLE_MAPS_API_KEY` | `AIza…` |
| `SMTP_USER` | `you@gmail.com` |
| `SMTP_PASSWORD` | the 16-letter app password |
| `ALERT_EMAILS` | `you@gmail.com,family@gmail.com` |
| `SEMAPHORE_API_KEY` | from semaphore.co |
| `ALERT_PHONES` | `09171234567,09181234567` |
| `SEMAPHORE_SENDER` | optional, your approved sender name |
| `NTFY_TOPIC` | optional, e.g. `cansaga-flood-x7k2p` |

Or from a terminal: `gh secret set GOOGLE_MAPS_API_KEY` (it prompts for the value).

### 5. Test the alerts
Actions → **Weather check** → *Run workflow* → set *force_level* to `EMERGENCY`.
You should get an email and SMS marked as a test. The next scheduled run will send "All clear".

## Local use

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q
.venv/bin/python src/run.py --dry-run                     # live data, no alerts sent
.venv/bin/python src/run.py --fixture tests/fixtures/dec2011_storm.json
.venv/bin/python -m http.server -d docs                   # open http://localhost:8000
```

## Tuning
After the first few rainy weeks, compare the dashboard with what actually happened on your street,
and adjust `config.yaml`. Set `location.lat/lon` to a pin on your own street, and add your
barangay / MDRRMO hotline numbers under `dashboard.hotlines`.

## Possible upgrades
- **WeatherNext 3 ensemble (BigQuery):** probability of heavy rain 3–7 days ahead.
- **Google Flood Hub API (waitlist):** add if its flash-flood or river forecasts cover Metro Cebu.
- **Rain gauge or water-level sensor** at home for true street-level readings.
