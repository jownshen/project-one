"""Email / SMS / phone-push alerts. Channels are configured by environment
variables (GitHub repo secrets); any channel without settings is skipped.

  Email: SMTP_USER, SMTP_PASSWORD, ALERT_EMAILS (comma-separated),
         optional SMTP_HOST (default smtp.gmail.com), SMTP_PORT (465)
  SMS:   SEMAPHORE_API_KEY, ALERT_PHONES (comma-separated, 09xxxxxxxxx),
         optional SEMAPHORE_SENDER
  Push:  NTFY_TOPIC (free ntfy.sh app; needs mobile data)
"""
import os
import re
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

import requests

from risk import rank


def decide(state, level, now, cfg):
    """Returns "alert", "reminder", "all_clear" or None.

    `state["alert"]` is the active alert ({"level", "time"}) or None.
    """
    active = state.get("alert")
    if rank(level) >= rank(cfg["min_level"]):
        if not active:
            return "alert"
        if rank(level) > rank(active["level"]):
            return "alert"
        last = datetime.fromisoformat(active["time"])
        if now - last >= timedelta(hours=cfg["cooldown_h"]):
            return "reminder"
        return None
    # Hysteresis: only stand down once things are clearly calmer.
    if active and rank(level) <= rank("WATCH"):
        return "all_clear"
    return None


def compose(kind, result, location, dashboard_url):
    level = result["level"]
    top = [r["text"] for r in result["reasons"] if r["level"] == level][:3]
    if kind == "all_clear":
        subject = f"All clear: {location} back to {level}"
        short = f"CANSAGA WEATHER: All clear, risk down to {level}."
        body = f"Conditions in {location} have eased to {level}.\n"
    else:
        prefix = "Reminder - still " if kind == "reminder" else ""
        subject = f"{prefix}{level}: flood/weather risk in {location}"
        short = f"CANSAGA WEATHER {prefix.upper()}{level}: {top[0] if top else ''}"
        body = f"{prefix}{level} for {location}.\n\n" + "\n".join(f"- {t}" for t in top) + (
            "\n\nFollow PAGASA and Consolacion MDRRMO instructions. Move valuables up, charge"
            " phones, and be ready to evacuate if told to.\n")
    if dashboard_url:
        body += f"\nDashboard: {dashboard_url}\n"
        short += f" {dashboard_url}"
    return subject, body, short[:300]


def _split(name):
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def send_email(subject, body):
    to = _split("ALERT_EMAILS")
    user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    if not (to and user and pw):
        return "email: not configured"
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
    msg.set_content(body)
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=30) as s:
        s.login(user, pw)
        s.send_message(msg)
    return f"email: sent to {len(to)}"


def send_sms(text):
    key, phones = os.environ.get("SEMAPHORE_API_KEY"), _split("ALERT_PHONES")
    if not (key and phones):
        return "sms: not configured"
    phones = [re.sub(r"[\s+()-]", "", p) for p in phones]
    data = {"apikey": key, "number": ",".join(phones), "message": text}
    if os.environ.get("SEMAPHORE_SENDER"):
        data["sendername"] = os.environ["SEMAPHORE_SENDER"]
    resp = requests.post("https://api.semaphore.co/api/v4/messages", data=data, timeout=30)
    if not resp.ok:
        # Actions logs are public: show Semaphore's reason, but never the numbers.
        raise RuntimeError(f"HTTP {resp.status_code}: {_redact(resp.text)} | account: {_sms_account(key)}")
    statuses = [m.get("status") for m in resp.json() if isinstance(m, dict)]
    return f"sms: sent to {len(phones)} ({', '.join(map(str, statuses))})"


def _redact(text):
    return re.sub(r"\+?\d{10,13}", "<number>", text)[:300]


def _sms_account(key):
    try:
        acct = requests.get("https://api.semaphore.co/api/v4/account",
                            params={"apikey": key}, timeout=15).json()
        return {k: acct.get(k) for k in ("status", "credit_balance")}
    except Exception as e:
        return f"unavailable ({type(e).__name__})"


def send_push(subject, text, priority):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return "push: not configured"
    resp = requests.post(f"https://ntfy.sh/{topic}", data=text.encode(),
                         headers={"Title": subject, "Priority": priority}, timeout=30)
    resp.raise_for_status()
    return f"push: sent"


def send_all(subject, body, short, priority="urgent", only=None):
    """`only` limits sending to one channel ("email", "sms" or "push") for tests."""
    results = []
    channels = {"email": (send_email, (subject, body)), "sms": (send_sms, (short,)),
                "push": (send_push, (subject, short, priority))}
    for name, (fn, args) in channels.items():
        if only and name != only:
            continue
        try:
            results.append(fn(*args))
        except Exception as e:  # one broken channel must not block the others
            results.append(f"{fn.__name__}: FAILED {e}")
    return results
