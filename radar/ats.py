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

import html
import json
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .classify import classify

_UA = "job-radar/1.0 (+https://github.com)"
_TIMEOUT = 25


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _open(req):
    """urlopen that backs off and retries when the server throttles or hiccups."""
    for attempt in range(4):
        try:
            return urllib.request.urlopen(req, timeout=_TIMEOUT)
        except urllib.error.HTTPError as e:
            wait = e.headers.get("Retry-After") or ""
            # a long Retry-After is a block, not a hiccup: give up until the next run
            if e.code not in (429, 500, 502, 503, 504) or attempt == 3 \
                    or (wait.isdigit() and int(wait) > 60):
                raise
            time.sleep(int(wait) if wait.isdigit() else 4 * (attempt + 1))


def _get_json(url: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("User-Agent", _UA)
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with _open(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_text(url: str, data: bytes | None = None, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("User-Agent", _UA)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with _open(req) as resp:
        return resp.read().decode("utf-8", "ignore")


def _clean(fragment: str) -> str:
    """Strip tags/entities from an HTML fragment and collapse whitespace."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


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


def fetch_lever(company: str, token: str, host: str = "api.lever.co") -> list[dict]:
    url = f"https://{host}/v0/postings/{token}?mode=json"
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


# Workday throttles per data-centre pod (wd1, wd3, ...) across all tenants, so cap
# how many requests the worker threads send to one pod at a time
_POD_SLOTS: dict[str, threading.Semaphore] = {}
_POD_LOCK = threading.Lock()


def _pod_slot(host: str) -> threading.Semaphore:
    pod = host.split(".", 1)[-1]
    with _POD_LOCK:
        return _POD_SLOTS.setdefault(pod, threading.Semaphore(6))


def fetch_workday(company: str, cfg: dict) -> list[dict]:
    """Workday needs host + tenant + site in the config entry, e.g.
        {"ats":"workday","host":"company.wd5.myworkdayjobs.com",
         "tenant":"company","site":"External"}
    Optional: "sites": [...] merges several career sites of one tenant;
    "facets": {"jobFamilyGroup": ["<id>", ...]} runs one query per facet value
    (Workday serves at most 2000 results per query, so big boards need this).
    """
    host = cfg["host"].rstrip("/")
    tenant = cfg["tenant"]
    queries = [{param: [value]} for param, values in (cfg.get("facets") or {}).items()
               for value in values] or [{}]
    out, seen = [], set()
    limit = 20
    for site in cfg.get("sites") or [cfg["site"]]:
        base = f"https://{host}/wday/cxs/{tenant}/{site}"
        for facets in queries:
            offset = 0
            while True:
                body = json.dumps({"appliedFacets": facets, "limit": limit,
                                   "offset": offset, "searchText": ""}).encode()
                with _pod_slot(host):
                    data = _get_json(f"{base}/jobs", data=body)
                items = data.get("jobPostings", [])
                fresh = 0
                for j in items:
                    title = j.get("title", "")
                    path = j.get("externalPath", "")
                    uid = f"workday:{tenant}:{path}"
                    if uid in seen:
                        continue
                    seen.add(uid)
                    fresh += 1
                    out.append({
                        "uid": uid,
                        "company": company,
                        "title": title,
                        "location": j.get("locationsText", ""),
                        "url": f"https://{host}/en-US/{site}{path}",
                        "ats": "workday",
                        "posted_at": j.get("postedOn"),
                        "category": classify(title),
                    })
                offset += len(items)
                # past its 2000-result limit Workday wraps back to the first page,
                # so a full page with nothing new means we're done
                if len(items) < limit or (offset >= 2000 and not fresh) or offset >= 3000:
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
    host = cfg["host"]
    site = cfg.get("site_url", f"https://{host}").rstrip("/")
    out = []
    start, count, total = 1, 100, None
    while True:
        data = urllib.parse.quote(json.dumps(
            {"LanguageCode": "en", "SearchParameters": {"FirstItem": start, "CountItem": count}}))
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


def fetch_recruitee(company: str, token: str) -> list[dict]:
    data = _get_json(f"https://{token}.recruitee.com/api/offers/")
    out = []
    for j in data.get("offers", []):
        title = j.get("title", "")
        out.append({
            "uid": f"recruitee:{token}:{j.get('id')}",
            "company": company,
            "title": title,
            "location": j.get("location", ""),
            "url": j.get("careers_url", ""),
            "ats": "recruitee",
            "posted_at": j.get("published_at") or j.get("created_at"),
            "category": classify(title),
        })
    return out


def fetch_rippling(company: str, token: str) -> list[dict]:
    data = _get_json(f"https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs")
    out, seen = [], set()
    for j in data:
        if j.get("uuid") in seen:   # one row per location
            continue
        seen.add(j.get("uuid"))
        title = j.get("name", "")
        out.append({
            "uid": f"rippling:{token}:{j.get('uuid')}",
            "company": company,
            "title": title,
            "location": (j.get("workLocation") or {}).get("label", ""),
            "url": j.get("url", ""),
            "ats": "rippling",
            "posted_at": None,
            "category": classify(title),
        })
    return out


def fetch_gem(company: str, token: str) -> list[dict]:
    data = _get_json(f"https://api.gem.com/job_board/v0/{token}/job_posts/")
    out = []
    for j in data:
        title = j.get("title", "")
        out.append({
            "uid": f"gem:{token}:{j.get('id')}",
            "company": company,
            "title": title,
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "ats": "gem",
            "posted_at": j.get("first_published_at"),
            "category": classify(title),
        })
    return out


def fetch_pinpoint(company: str, token: str) -> list[dict]:
    data = _get_json(f"https://{token}.pinpointhq.com/postings.json")
    out = []
    for j in data.get("data", []):
        title = j.get("title", "")
        out.append({
            "uid": f"pinpoint:{token}:{j.get('id')}",
            "company": company,
            "title": title,
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("url", ""),
            "ats": "pinpoint",
            "posted_at": None,
            "category": classify(title),
        })
    return out


def fetch_personio(company: str, token: str) -> list[dict]:
    """Personio XML feed; boards live on .de or .com."""
    root = base = None
    for tld in ("de", "com"):
        base = f"https://{token}.jobs.personio.{tld}"
        try:
            root = ET.fromstring(_get_text(f"{base}/xml"))
            break
        except ET.ParseError:
            continue
    if root is None:
        raise ValueError("no Personio XML feed")
    out = []
    for p in root.iter("position"):
        title = (p.findtext("name") or "").strip()
        jid = p.findtext("id")
        out.append({
            "uid": f"personio:{token}:{jid}",
            "company": company,
            "title": title,
            "location": (p.findtext("office") or "").strip(),
            "url": f"{base}/job/{jid}",
            "ats": "personio",
            "posted_at": p.findtext("createdAt"),
            "category": classify(title),
        })
    return out


def fetch_teamtailor(company: str, token: str) -> list[dict]:
    """Teamtailor career site, read from its RSS feed (100 items per page)."""
    out = []
    offset = 0
    while True:
        feed = _get_text(f"https://{token}.teamtailor.com/jobs.rss?offset={offset}")
        items = re.findall(r"<item>(.*?)</item>", feed, re.S)
        for item in items:
            def tag(name: str) -> str:
                m = re.search(rf"<{name}>(.*?)</{name}>", item, re.S)
                return html.unescape(m.group(1)).strip() if m else ""
            title = tag("title")
            cities = [html.unescape(c) for c in re.findall(r"<tt:city>(.*?)</tt:city>", item)]
            loc = cities[0] if cities else tag("tt:name")
            if len(cities) > 1:
                loc += f" (+{len(cities) - 1})"
            out.append({
                "uid": f"teamtailor:{token}:{tag('guid')}",
                "company": company,
                "title": title,
                "location": loc,
                "url": tag("link"),
                "ats": "teamtailor",
                "posted_at": tag("pubDate") or None,
                "category": classify(title),
            })
        offset += len(items)
        if len(items) < 100 or offset >= 2000:
            break
    return out


def fetch_oraclecloud(company: str, cfg: dict) -> list[dict]:
    """Oracle Recruiting Cloud job search API (e.g. JPMorgan). Config:
        {"ats":"oraclecloud","host":"jpmc.fa.oraclecloud.com","site":"CX_1001"}
    """
    host = cfg["host"].rstrip("/")
    site = cfg["site"]
    cap = cfg.get("max", 10000)
    base = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    out = []
    offset, limit = 0, 200
    while True:
        url = (f"{base}?onlyData=true&expand=requisitionList.secondaryLocations"
               f"&finder=findReqs;siteNumber={site},limit={limit},offset={offset},"
               f"sortBy=POSTING_DATES_DESC")
        data = _get_json(url)
        items = ((data.get("items") or [{}])[0]).get("requisitionList") or []
        for j in items:
            title = j.get("Title", "")
            loc = j.get("PrimaryLocation") or ""
            more = len(j.get("secondaryLocations") or [])
            if more:
                loc += f" (+{more})"
            out.append({
                "uid": f"oraclecloud:{host.split('.')[0]}:{j.get('Id')}",
                "company": company,
                "title": title,
                "location": loc,
                "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{j.get('Id')}",
                "ats": "oraclecloud",
                "posted_at": j.get("PostedDate"),
                "category": classify(title),
            })
        offset += len(items)
        if len(items) < limit or offset >= cap:
            break
    return out


def fetch_amazon(company: str, cfg: dict) -> list[dict]:
    """amazon.jobs search API, one query per job category. Config:
        {"ats":"amazon","categories":["software-development"]}
    """
    out, seen = [], set()
    limit = 100
    for cat in cfg.get("categories") or [""]:
        offset = 0
        while True:
            q = {"result_limit": limit, "offset": offset, "sort": "recent"}
            if cat:
                q["category[]"] = cat
            d = _get_json("https://www.amazon.jobs/en/search.json?" + urllib.parse.urlencode(q))
            jobs = d.get("jobs") or []
            for j in jobs:
                jid = j.get("id_icims")
                if jid in seen:
                    continue
                seen.add(jid)
                title = j.get("title", "")
                try:
                    posted = datetime.strptime(" ".join((j.get("posted_date") or "").split()),
                                               "%B %d, %Y").date().isoformat()
                except ValueError:
                    posted = None
                out.append({
                    "uid": f"amazon:{jid}",
                    "company": company,
                    "title": title,
                    "location": j.get("normalized_location") or j.get("location", ""),
                    "url": "https://www.amazon.jobs" + j.get("job_path", ""),
                    "ats": "amazon",
                    "posted_at": posted,
                    "category": classify(title),
                })
            offset += len(jobs)
            # the API refuses offsets past 10000
            if len(jobs) < limit or offset >= 9900:
                break
    return out


def fetch_optiver(company: str, cfg: dict) -> list[dict]:
    """Optiver's own careers API (fixed pages of 16). Config: {"ats":"optiver"}"""
    site = cfg.get("site_url", "https://www.optiver.com").rstrip("/")
    out = []
    start, total = 0, None
    while True:
        d = _get_json(f"{site}/en/api/v1/jobs?from={start}&size=16")
        if total is None:
            total = d.get("totalCount", 0)
        items = d.get("items", [])
        for j in items:
            title = (j.get("title") or "").strip()
            out.append({
                "uid": f"optiver:{j.get('componentID')}",
                "company": company,
                "title": title,
                "location": j.get("location", ""),
                "url": site + j.get("href", ""),
                "ats": "optiver",
                "posted_at": None,
                "category": classify(title),
            })
        start += len(items)
        if not items or start >= (total or 0) or start >= 2000:
            break
    return out


_AVATURE_JOB = re.compile(
    r'<h3[^>]*article__header__text__title[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>'
    r'(.*?)</a>(.*?)(?=<h3[^>]*article__header__text__title|$)', re.S)


_AVATURE_LOCATION = (r'list-item-location[^>]*>(.*?)</span>',
                     r'alt="[^"]*Location[^"]*"[^>]*>\s*<p>(.*?)</p>',
                     r'paragraph_inner-span[^>]*>(.*?)</span>')


def fetch_avature(company: str, cfg: dict) -> list[dict]:
    """Avature career portal, read from its paginated HTML job list. Config:
        {"ats":"avature","url":"https://careers.twosigma.com/careers/OpenRoles"}
    """
    base = cfg["url"].rstrip("/")
    host = urllib.parse.urlparse(base).netloc
    out, seen = [], set()
    offset = 0
    while True:
        page = _get_text(f"{base}/?jobRecordsPerPage=50&jobOffset={offset}")
        fresh = 0
        for url, title, rest in _AVATURE_JOB.findall(page):
            url = html.unescape(url)
            # the job id is the last number in the link (.../JobDetail/<slug>/123 or ?jobId=123)
            ids = re.findall(r"\d{3,}", url)
            jid = ids[-1] if ids else url
            if jid in seen:
                continue
            seen.add(jid)
            fresh += 1
            title = _clean(title)
            # portals mark up the location differently; unknown layouts get none
            loc = next((m for m in (re.search(rx, rest, re.S) for rx in _AVATURE_LOCATION) if m), None)
            out.append({
                "uid": f"avature:{host}:{jid}",
                "company": company,
                "title": title,
                "location": _clean(loc.group(1)) if loc else "",
                "url": url,
                "ats": "avature",
                "posted_at": None,
                "category": classify(title),
            })
        # portals ignore the requested page size, so advance by what we got
        offset += fresh
        if not fresh or offset >= 3000:
            break
    return out


_CITADEL_CARD = re.compile(
    r'<a\s+class="careers-listing-card[^"]*"\s+href="([^"]+/careers/details/([^"/]+)/?)"'
    r'.*?data-position="([^"]*)"(.*?)</a>', re.S)


def fetch_citadel(company: str, cfg: dict) -> list[dict]:
    """Citadel / Citadel Securities careers listing (WordPress ajax). Config:
        {"ats":"citadel","site_url":"https://www.citadelsecurities.com"}
    """
    site = cfg.get("site_url", "https://www.citadel.com").rstrip("/")
    host = urllib.parse.urlparse(site).netloc
    headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
               "X-Requested-With": "XMLHttpRequest",
               "Referer": f"{site}/careers/open-opportunities/"}
    # the endpoint ignores `page`, so ask for everything in one request
    form = urllib.parse.urlencode({"page": 1, "sort_order": "DESC", "per_page": 1000,
                                   "action": "careers_listing_filter"}).encode()
    d = json.loads(_get_text(f"{site}/wp-admin/admin-ajax.php", data=form, headers=headers))
    out, seen = [], set()
    for url, slug, title, rest in _CITADEL_CARD.findall(d.get("content") or ""):
        if slug in seen:
            continue
        seen.add(slug)
        title = _clean(title)
        loc = re.search(r'careers-listing-card__location[^>]*>(.*?)</(?:div|span)>', rest, re.S)
        out.append({
            "uid": f"citadel:{host}:{slug}",
            "company": company,
            "title": title,
            "location": _clean(loc.group(1)) if loc else "",
            "url": url,
            "ats": "citadel",
            "posted_at": None,
            "category": classify(title),
        })
    return out


def fetch_deshaw(company: str, cfg: dict) -> list[dict]:
    """D. E. Shaw careers page: jobs are embedded as Next.js page data. Config: {"ats":"deshaw"}"""
    site = cfg.get("site_url", "https://www.deshaw.com").rstrip("/")
    page = _get_text(f"{site}/careers/choose-your-path")
    blob = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S).group(1)
    props = json.loads(blob)["props"]["pageProps"]
    out = []
    for j in (props.get("regularJobs") or []) + (props.get("internships") or []):
        title = j.get("displayName", "")
        slug = (j.get("data") or {}).get("jobUrl") or ""
        out.append({
            "uid": f"deshaw:{j.get('id')}",
            "company": company,
            "title": title,
            "location": ", ".join(o.get("name", "") for o in j.get("office") or []),
            "url": f"{site}/careers/{slug.lower()}",
            "ats": "deshaw",
            "posted_at": None,
            "category": classify(title),
        })
    return out


# --------------------------------------------------------------------------- #

_SIMPLE = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "lever-eu": lambda company, token: fetch_lever(company, token, "api.eu.lever.co"),
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
    "recruitee": fetch_recruitee,
    "rippling": fetch_rippling,
    "gem": fetch_gem,
    "pinpoint": fetch_pinpoint,
    "personio": fetch_personio,
    "teamtailor": fetch_teamtailor,
}

_CFG_BASED = {
    "workday": fetch_workday,
    "eightfold": fetch_eightfold,
    "beesite": fetch_beesite,
    "jibe": fetch_jibe,
    "gsgraphql": fetch_gsgraphql,
    "oraclecloud": fetch_oraclecloud,
    "amazon": fetch_amazon,
    "optiver": fetch_optiver,
    "avature": fetch_avature,
    "citadel": fetch_citadel,
    "deshaw": fetch_deshaw,
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
            # "tokens": [...] merges several boards of one company
            postings = []
            for token in cfg.get("tokens") or [cfg["token"]]:
                postings += _SIMPLE[ats](name, token)
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
