"""Email digest of newly detected postings via SMTP.

Configured entirely through environment variables (set as GitHub Actions
secrets). If any required var is missing, email is silently skipped so the
poller still works without it.

    SMTP_HOST   e.g. smtp.gmail.com
    SMTP_PORT   e.g. 465 (SSL) or 587 (STARTTLS)
    SMTP_USER   login username
    SMTP_PASS   login password / app password
    MAIL_FROM   sender address (defaults to SMTP_USER)
    MAIL_TO     recipient address(es), comma-separated
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


def _cfg():
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASS")
    to = os.environ.get("MAIL_TO")
    if not all([host, user, pw, to]):
        return None
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "465")),
        "user": user,
        "pw": pw,
        "from": os.environ.get("MAIL_FROM", user),
        "to": [a.strip() for a in to.split(",") if a.strip()],
    }


def _format(new_postings: list[dict], site_url: str | None) -> tuple[str, str]:
    n = len(new_postings)
    subject = f"Job Radar: {n} new posting{'s' if n != 1 else ''}"

    lines = [f"{n} new posting{'s' if n != 1 else ''} detected:\n"]
    by_company: dict[str, list[dict]] = {}
    for p in new_postings:
        by_company.setdefault(p["company"], []).append(p)
    for company in sorted(by_company):
        lines.append(f"\n=== {company} ===")
        for p in by_company[company]:
            loc = f"  [{p['location']}]" if p.get("location") else ""
            lines.append(f"  • ({p['category']}) {p['title']}{loc}")
            lines.append(f"    {p['url']}")
    if site_url:
        lines.append(f"\nDashboard: {site_url}")
    return subject, "\n".join(lines)


def send_digest(new_postings: list[dict], site_url: str | None = None) -> bool:
    if not new_postings:
        return False
    cfg = _cfg()
    if not cfg:
        print("  (email skipped: SMTP_* env vars not set)")
        return False

    subject, body = _format(new_postings, site_url)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(cfg["to"])
    msg.set_content(body)

    try:
        if cfg["port"] == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=ctx) as s:
                s.login(cfg["user"], cfg["pw"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(cfg["host"], cfg["port"]) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(cfg["user"], cfg["pw"])
                s.send_message(msg)
        print(f"  ✉  emailed digest to {', '.join(cfg['to'])}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  ! email failed: {type(e).__name__}: {e}")
        return False
