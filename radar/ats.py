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


def fetch_beesite(company: str, cfg: dict) -> list[dict]:
    """Beesite / Milch&Zucker job search API (e.g. Deutsche Bank). Config:
        {"ats":"beesite","host":"api-deutschebank.beesite.de",
         "site_url":"https://careers.db.com"}
    """
    import urllib.parse
    host = cfg["host"]
    site = cfg.get("site_url", f"https://{host}").rstrip("/")
    out = []
    start, count, total = 1, 100, None
    while True:
        data = urllib.parse.quote(json.dumps(
            {"LanguageCode": "en", "FirstItem": start, "CountItem": count}))
        d = _get_json(f"https://{host}/search/?data={data}")
        sr = d.get("SearchResult", {})
        if total is None:
            total = sr.get("SearchResultCountAll", 0)
        items = sr.get("SearchResultItems", [])
        for it in items:
            m = it.get("MatchedObjectDescriptor", {})
            title = m.get("PositionTitle", "")
            locs = m.get("PositionLocation") or []
            loc = ""
            if locs:
                loc = locs[0].get("CityName") or locs[0].get("CountryName") or ""
                if len(locs) > 1:
                    loc += f" (+{len(locs) - 1})"
            uri = m.get("PositionURI", "")
            url = uri if uri.startswith("http") else site + ("" if uri.startswith("/") else "/") + uri
            out.append({
                "uid": f"beesite:{host}:{m.get('PositionID')}",
                "company": company,
                "title": title,
                "location": loc,
                "url": url,
                "ats": "beesite",
                "posted_at": m.get("PublicationStartDate"),
                "expires_at": m.get("PublicationEndDate"),
                "category": classify(title),
            })
        start += len(items)
        if not items or start > (total or 0) or start > 3000:
            break
    return out


def fetch_jibe(company: str, cfg: dict) -> list[dict]:
    """Jibe / iCIMS front-end job API (e.g. SIG). Config:
        {"ats":"jibe","host":"careers.sig.com"}
    """
    host = cfg["host"].rstrip("/")
    out = []
    page, limit, total = 1, 100, None
    while True:
        d = _get_json(f"https://{host}/api/jobs?page={page}&limit={limit}")
        if total is None:
            total = d.get("totalCount", 0)
        jobs = d.get("jobs", [])
        for jb in jobs:
            j = jb.get("data", jb)
            title = j.get("title", "")
            loc = ", ".join(x for x in (j.get("city"), j.get("state"),
                                        j.get("country")) if x) or j.get("location_name", "")
            req, slug = j.get("req_id"), j.get("slug")
            url = (j.get("apply_url") or j.get("absolute_url")
                   or f"https://{host}/jobs/{req}/{slug}")
            out.append({
                "uid": f"jibe:{host}:{req}",
                "company": company,
                "title": title,
                "location": loc,
                "url": url,
                "ats": "jibe",
                "posted_at": j.get("posted_date") or j.get("create_date"),
                "category": classify(title),
            })
        page += 1
        if not jobs or len(out) >= (total or 0) or page > 60:
            break
    return out


_GS_QUERY = ("query($in:RoleSearchQueryInput!){roleSearch(searchQueryInput:$in)"
             "{totalCount items{roleId jobTitle jobFunction division lastPostedDate "
             "locations{city state country}}}}")


def fetch_gsgraphql(company: str, cfg: dict) -> list[dict]:
    """Goldman Sachs 'Higher' GraphQL roleSearch API (unauthenticated). Config:
        {"ats":"gsgraphql","host":"api-higher.gs.com","site_url":"https://higher.gs.com"}
    """
    host = cfg.get("host", "api-higher.gs.com")
    site = cfg.get("site_url", "https://higher.gs.com").rstrip("/")
    experiences = cfg.get("experiences", ["PROFESSIONAL", "EARLY_CAREER", "CAMPUS"])
    url = f"https://{host}/gateway/api/v1/graphql"
    headers = {"Origin": site, "Referer": site + "/"}
    out = []
    page, size, total = 0, 100, None
    while True:
        body = json.dumps({"query": _GS_QUERY, "variables": {"in": {
            "page": {"pageNumber": page, "pageSize": size},
            "experiences": experiences, "searchTerm": ""}}}).encode()
        data = _get_json(url, data=body, headers=headers)
        rs = (data.get("data") or {}).get("roleSearch") or {}
        if total is None:
            total = rs.get("totalCount", 0)
        items = rs.get("items", [])
        for it in items:
            title = it.get("jobTitle", "")
            locs = it.get("locations") or []
            loc = ""
            if locs:
                loc = locs[0].get("city") or locs[0].get("country") or ""
                if len(locs) > 1:
                    loc += f" (+{len(locs) - 1})"
            out.append({
                "uid": f"gsgraphql:{it.get('roleId')}",
                "company": company,
                "title": title,
                "location": loc,
                "url": f"{site}/roles/{it.get('roleId')}",
                "ats": "gsgraphql",
                "posted_at": it.get("lastPostedDate"),
                "category": classify(title),
            })
        page += 1
        if not items or len(out) >= (total or 0) or page > 40:
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

_CFG_BASED = {
    "workday": fetch_workday,
    "eightfold": fetch_eightfold,
    "beesite": fetch_beesite,
    "jibe": fetch_jibe,
    "gsgraphql": fetch_gsgraphql,
}

SUPPORTED = sorted(list(_SIMPLE) + list(_CFG_BASED) + ["link"])


def fetch_company(cfg: dict):
    """Dispatch on cfg['ats'].

    Returns a list of postings on success (possibly empty if the board is
    genuinely empty), or None if the fetch FAILED (network/HTTP error, bad
    config). Callers must treat None as "unknown — keep existing postings"
    so a transient outage doesn't wipe a company's roles. 'link' entries
    return [] (nothing to poll, but not a failure).
    """
    ats = cfg.get("ats")
    name = cfg.get("name", "?")
    if ats == "link":
        return []
    try:
        if ats in _SIMPLE:
            postings = _SIMPLE[ats](name, cfg["token"])
        elif ats in _CFG_BASED:
            postings = _CFG_BASED[ats](name, cfg)
        else:
            _log(f"  ! {name}: unknown ats '{ats}' (supported: {SUPPORTED})")
            return None
    except urllib.error.HTTPError as e:
        _log(f"  ! {name} [{ats}]: HTTP {e.code} — keeping existing roles")
        return None
    except Exception as e:  # noqa: BLE001
        _log(f"  ! {name} [{ats}]: {type(e).__name__}: {e} — keeping existing roles")
        return None
    # attach company-level tags
    tags = cfg.get("tags", [])
    for p in postings:
        p["tags"] = tags
    return postings
