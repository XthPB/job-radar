"""Adapters for common Applicant Tracking Systems (ATS).

Each adapter fetches a company's public job board feed and returns a list of
*normalized* posting dicts:

    {
        "uid":       stable unique id  (str)
        "company":   company display name (str)
        "title":     role title (str)
        "location":  location text (str)
        "url":       public apply/posting URL (str)
        "ats":       ats type (str)
        "posted_at": ISO-8601 string or None  (when the ATS exposes it)
    }

Everything uses the Python standard library only (no pip install needed).
A failed fetch logs a warning and returns [] so one broken company never
kills the whole run.
"""

from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

from .classify import classify

_UA = "job-radar/1.0 (+https://github.com)"
_TIMEOUT = 25


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _get_json(url: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("User-Agent", _UA)
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ms_to_iso(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #

def fetch_greenhouse(company: str, token: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=false"
    data = _get_json(url)
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        out.append({
            "uid": f"greenhouse:{token}:{j.get('id')}",
            "company": company,
            "title": title,
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "ats": "greenhouse",
            "posted_at": j.get("updated_at"),
            "category": classify(title),
        })
    return out


def fetch_lever(company: str, token: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    data = _get_json(url)
    out = []
    for j in data:
        title = j.get("text", "")
        cats = j.get("categories") or {}
        out.append({
            "uid": f"lever:{token}:{j.get('id')}",
            "company": company,
            "title": title,
            "location": cats.get("location", ""),
            "url": j.get("hostedUrl", ""),
            "ats": "lever",
            "posted_at": _ms_to_iso(j.get("createdAt")),
            "category": classify(title),
        })
    return out


def fetch_ashby(company: str, token: str) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=false"
    data = _get_json(url)
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        title = j.get("title", "")
        out.append({
            "uid": f"ashby:{token}:{j.get('id')}",
            "company": company,
            "title": title,
            "location": j.get("location", ""),
            "url": j.get("jobUrl") or j.get("applyUrl", ""),
            "ats": "ashby",
            "posted_at": j.get("publishedAt"),
            "category": classify(title),
        })
    return out


def fetch_smartrecruiters(company: str, token: str) -> list[dict]:
    out = []
    offset = 0
    while True:
        url = (f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
               f"?limit=100&offset={offset}")
        data = _get_json(url)
        items = data.get("content", [])
        for j in items:
            title = j.get("name", "")
            loc = j.get("location") or {}
            loc_text = ", ".join(x for x in (loc.get("city"), loc.get("region"),
                                             loc.get("country")) if x)
            out.append({
                "uid": f"smartrecruiters:{token}:{j.get('id')}",
                "company": company,
                "title": title,
                "location": loc_text,
                "url": f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
                "ats": "smartrecruiters",
                "posted_at": j.get("releasedDate"),
                "category": classify(title),
            })
        total = data.get("totalFound", len(out))
        offset += len(items)
        if not items or offset >= total:
            break
    return out


def fetch_workday(company: str, cfg: dict) -> list[dict]:
    """Workday needs host + tenant + site in the config entry, e.g.
        {"ats":"workday","host":"company.wd5.myworkdayjobs.com",
         "tenant":"company","site":"External"}
    """
    host = cfg["host"].rstrip("/")
    tenant = cfg["tenant"]
    site = cfg["site"]
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    out = []
    offset = 0
    limit = 20
    total = None  # Workday only reports `total` on the first page
    while True:
        body = json.dumps({"appliedFacets": {}, "limit": limit,
                           "offset": offset, "searchText": ""}).encode()
        data = _get_json(f"{base}/jobs", data=body)
        items = data.get("jobPostings", [])
        if total is None:
            total = data.get("total", 0)
        for j in items:
            title = j.get("title", "")
            path = j.get("externalPath", "")
            out.append({
                "uid": f"workday:{tenant}:{path}",
                "company": company,
                "title": title,
                "location": j.get("locationsText", ""),
                "url": f"https://{host}/en-US/{site}{path}",
                "ats": "workday",
                "posted_at": j.get("postedOn"),
                "category": classify(title),
            })
        offset += len(items)
        # stop when a page is short, we've covered the reported total,
        # or we hit a safety cap (avoid runaway on huge boards)
        if len(items) < limit or offset >= (total or 0) or offset >= 3000:
            break
    return out


def fetch_workable(company: str, token: str) -> list[dict]:
    """Public Workable account feed (no auth)."""
    url = f"https://www.workable.com/api/accounts/{token}"
    data = _get_json(url)
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        loc = ", ".join(x for x in (j.get("city"), j.get("state"),
                                    j.get("country")) if x)
        code = j.get("shortcode")
        out.append({
            "uid": f"workable:{token}:{code}",
            "company": company,
            "title": title,
            "location": loc,
            "url": j.get("url") or j.get("application_url", ""),
            "ats": "workable",
            "posted_at": j.get("published_on"),
            "category": classify(title),
        })
    return out


def fetch_eightfold(company: str, cfg: dict) -> list[dict]:
    """Eightfold.ai public positions API. Config:
        {"ats":"eightfold","tenant":"mlp","domain":"mlp.com"}
    """
    tenant = cfg["tenant"]
    domain = cfg.get("domain", f"{tenant}.com")
    base = f"https://{tenant}.eightfold.ai/api/apply/v2/jobs"
    out = []
    start, num, total = 0, 100, None
    while True:
        url = f"{base}?domain={domain}&start={start}&num={num}"
        data = _get_json(url)
        if total is None:
            total = data.get("count", 0)
        positions = data.get("positions", [])
        for p in positions:
            title = p.get("name", "")
            out.append({
                "uid": f"eightfold:{tenant}:{p.get('id')}",
                "company": company,
                "title": title,
                "location": p.get("location", ""),
                "url": p.get("canonicalPositionUrl", ""),
                "ats": "eightfold",
                "posted_at": p.get("t_create") or p.get("t_update"),
                "category": classify(title),
            })
        start += len(positions)
        if not positions or start >= (total or 0) or start >= 3000:
            break
    return out


# --------------------------------------------------------------------------- #

_SIMPLE = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
}

SUPPORTED = sorted(list(_SIMPLE) + ["workday", "eightfold", "link"])


def fetch_company(cfg: dict) -> list[dict]:
    """Dispatch on cfg['ats']. Returns [] for unpollable 'link' entries."""
    ats = cfg.get("ats")
    name = cfg.get("name", "?")
    if ats == "link":
        return []
    try:
        if ats in _SIMPLE:
            postings = _SIMPLE[ats](name, cfg["token"])
        elif ats == "workday":
            postings = fetch_workday(name, cfg)
        elif ats == "eightfold":
            postings = fetch_eightfold(name, cfg)
        else:
            _log(f"  ! {name}: unknown ats '{ats}' (supported: {SUPPORTED})")
            return []
    except urllib.error.HTTPError as e:
        _log(f"  ! {name} [{ats}]: HTTP {e.code} — check the token")
        return []
    except Exception as e:  # noqa: BLE001
        _log(f"  ! {name} [{ats}]: {type(e).__name__}: {e}")
        return []
    # attach company-level tags
    tags = cfg.get("tags", [])
    for p in postings:
        p["tags"] = tags
    return postings
