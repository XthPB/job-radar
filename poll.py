#!/usr/bin/env python3
"""Job Radar — poll company ATS feeds, diff against last run, surface new roles.

Usage:
    python3 poll.py                 # full run (used by GitHub Actions + locally)
    python3 poll.py --check ashby janestreet [Display Name]
                                    # test one feed, print count + sample titles
    python3 poll.py --list          # list configured companies

New postings are written to docs/postings.json (the dashboard reads this) and
emailed if SMTP_* env vars are configured.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from radar import ats, store, notify, rolefilter

ROOT = os.path.dirname(os.path.abspath(__file__))
COMPANIES = os.path.join(ROOT, "companies.json")
STATE = os.path.join(ROOT, "state", "seen.json")
OUT = os.path.join(ROOT, "docs", "postings.json")

# Set to your GitHub Pages URL once known; shown in the email footer.
SITE_URL = os.environ.get("SITE_URL")


def load_companies() -> list[dict]:
    with open(COMPANIES, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [c for c in data
            if "_section" not in c and c.get("name") and c.get("enabled", True)]


def cmd_check(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: poll.py --check <ats> <token> [name]  "
              "(or --check workday <host> <tenant> <site> [name])")
        return 2
    ats_type = argv[0]
    if ats_type == "workday":
        host, tenant, site = argv[1], argv[2], argv[3]
        name = argv[4] if len(argv) > 4 else tenant
        cfg = {"name": name, "ats": "workday", "host": host,
               "tenant": tenant, "site": site}
    elif ats_type == "eightfold":
        tenant = argv[1]
        domain = argv[2] if len(argv) > 2 else f"{tenant}.com"
        name = argv[3] if len(argv) > 3 else tenant
        cfg = {"name": name, "ats": "eightfold", "tenant": tenant, "domain": domain}
    elif ats_type == "beesite":
        host = argv[1]
        site = argv[2] if len(argv) > 2 else f"https://{host}"
        name = argv[3] if len(argv) > 3 else host
        cfg = {"name": name, "ats": "beesite", "host": host, "site_url": site}
    elif ats_type == "jibe":
        host = argv[1]
        name = argv[2] if len(argv) > 2 else host
        cfg = {"name": name, "ats": "jibe", "host": host}
    elif ats_type == "gsgraphql":
        cfg = {"name": "Goldman Sachs", "ats": "gsgraphql"}
    else:
        token = argv[1]
        name = argv[2] if len(argv) > 2 else token
        cfg = {"name": name, "ats": ats_type, "token": token}

    postings = ats.fetch_company(cfg) or []
    print(f"\n{name} [{ats_type}] → {len(postings)} postings")
    for p in postings[:15]:
        loc = f"  [{p['location']}]" if p.get("location") else ""
        print(f"  ({p['category']:>10}) {p['title']}{loc}")
    if len(postings) > 15:
        print(f"  ... and {len(postings) - 15} more")
    return 0 if postings else 1


def cmd_list() -> int:
    for c in load_companies():
        ident = c.get("token") or c.get("tenant") or c.get("url", "")
        print(f"  {c['name']:<28} {c['ats']:<16} {ident}")
    return 0


def run() -> int:
    companies = load_companies()
    keep = rolefilter.make_filter(rolefilter.load_settings(ROOT))
    now_iso = datetime.now(timezone.utc).isoformat()
    print(f"Job Radar run @ {now_iso}  ({len(companies)} companies)")

    current: list[dict] = []
    links: list[dict] = []
    succeeded: set[str] = set()
    failed: list[str] = []
    for c in companies:
        if c.get("ats") == "link":
            links.append({"company": c["name"], "url": c["url"],
                          "tags": c.get("tags", [])})
            continue
        result = ats.fetch_company(c)
        if result is None:        # feed errored — keep this company's existing roles
            failed.append(c["name"])
            continue
        succeeded.add(c["name"])
        got = [p for p in result if keep(p["title"])]
        if got:
            print(f"  ✓ {c['name']:<28} {len(got)} relevant postings")
        current.extend(got)

    if failed:
        print(f"  ⚠ {len(failed)} feed(s) failed (roles preserved): {', '.join(failed)}")

    seen = store.load_seen(STATE)
    new_postings = store.diff(seen, current, now_iso, succeeded)
    store.save_json(STATE, seen)

    active = store.active_postings(seen)
    store.save_json(OUT, {
        "generated_at": now_iso,
        "count": len(active),
        "postings": active,
        "links": sorted(links, key=lambda x: x["company"]),
    })

    print(f"\n{len(active)} active postings, {len(new_postings)} new this run")
    for p in new_postings:
        print(f"  NEW: {p['company']} — {p['title']}")

    notify.send_digest(new_postings, SITE_URL)
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--check":
        return cmd_check(argv[1:])
    if argv and argv[0] == "--list":
        return cmd_list()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
