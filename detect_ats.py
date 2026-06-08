#!/usr/bin/env python3
"""Detect which ATS a careers page uses, so you know how to add it.

    python3 detect_ats.py https://www.example.com/careers

Prints any ATS signatures found in the page HTML and the token to use in
companies.json. JS-rendered pages may hide the ATS — if nothing is found,
open the page, view source, and search for the vendor names yourself.
"""
import re
import sys
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 job-radar"

SIGS = [
    ("greenhouse", re.compile(r'(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9]+)', re.I)),
    ("greenhouse", re.compile(r'greenhouse\.io/embed/job_board\?for=([a-z0-9]+)', re.I)),
    ("lever",      re.compile(r'jobs\.lever\.co/([a-z0-9\-]+)', re.I)),
    ("ashby",      re.compile(r'jobs\.ashbyhq\.com/([a-z0-9\-]+)', re.I)),
    ("smartrecruiters", re.compile(r'smartrecruiters\.com/([A-Za-z0-9]+)', re.I)),
    ("workday",    re.compile(r'([a-z0-9]+)\.wd\d+\.myworkdayjobs\.com/(?:wday/cxs/[a-z0-9]+/)?([A-Za-z0-9_\-]+)', re.I)),
    ("workable",   re.compile(r'apply\.workable\.com/([a-z0-9\-]+)', re.I)),
    ("eightfold",  re.compile(r'([a-z0-9\-]+)\.eightfold\.ai', re.I)),
    ("avature",    re.compile(r'([a-z0-9\-]+)\.avature\.net', re.I)),
    ("icims",      re.compile(r'([a-z0-9\-]+)\.icims\.com', re.I)),
    ("taleo",      re.compile(r'([a-z0-9\-]+)\.taleo\.net', re.I)),
    ("recruitee",  re.compile(r'([a-z0-9\-]+)\.recruitee\.com', re.I)),
    ("teamtailor", re.compile(r'([a-z0-9\-]+)\.teamtailor\.com', re.I)),
]
POLLABLE = {"greenhouse", "lever", "ashby", "smartrecruiters", "workable", "workday", "eightfold"}


def main(url: str) -> int:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        html = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        print(f"fetch failed: {type(e).__name__}: {e}")
        return 1

    found = []
    for ats, rx in SIGS:
        m = rx.search(html)
        if m:
            found.append((ats, m.groups()))

    if not found:
        print("No ATS signature in the static HTML (likely JS-rendered or custom).")
        print("View source and search for: greenhouse, lever, ashby, workable,")
        print("eightfold, myworkdayjobs, smartrecruiters, avature, icims, taleo.")
        return 1

    print(f"Detected for {url}:\n")
    for ats, groups in found:
        token = groups[0]
        tag = "✓ pollable" if ats in POLLABLE else "✗ no public feed (use a link card)"
        print(f"  {ats:16} token/tenant = {token:28} {tag}")
        if ats in POLLABLE and ats not in ("workday", "eightfold"):
            print(f'    -> verify: python3 poll.py --check {ats} {token}')
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
