"""State persistence + diffing.

state/seen.json holds the full historical record keyed by posting uid:

    { uid: { ...posting fields..., "first_seen": iso, "last_seen": iso,
             "active": bool } }

diff() takes the freshly fetched postings, updates the record in place,
and returns the list of postings that are brand-new this run.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta


def load_seen(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def diff(seen: dict, current: list[dict], now_iso: str, succeeded=None,
         backfill=None, tracked=None) -> list[dict]:
    """Mutates `seen`. Returns the brand-new postings detected this run.

    `succeeded` is the set of company names whose feed was polled successfully
    this run. A posting is marked closed only if its company was polled OK and
    the posting no longer appears — so a transient feed outage never wipes (and
    later falsely re-surfaces) a company's roles. If `succeeded` is None, every
    company is assumed polled (legacy behaviour).

    `backfill` maps recently added (or re-synced) companies to their "added"
    timestamp. Until such a company has a posting first seen after that time,
    whatever it returns is roles that already existed: those are stored with
    "backfill": true and not reported as new.

    `tracked` is the set of companies that are still configured as feeds.
    Postings of any other company (removed, muted, or turned into a link card)
    are closed, since nothing will ever poll them again.
    """
    current_uids = set()
    new_postings = []
    backfill = backfill or {}
    started = {rec.get("company") for rec in seen.values()
               if rec.get("first_seen", "") >= backfill.get(rec.get("company"), "~")}

    for p in current:
        uid = p["uid"]
        current_uids.add(uid)
        if uid in seen:
            rec = seen[uid]
            # day-granular, so unchanged postings don't rewrite the state file every run
            if rec.get("last_seen", "")[:10] != now_iso[:10]:
                rec["last_seen"] = now_iso
            rec["active"] = True
            # refresh mutable fields in case the posting changed
            for k in ("title", "location", "url", "category", "tags", "posted_at", "expires_at"):
                if k in p:
                    rec[k] = p[k]
        else:
            rec = dict(p)
            rec["first_seen"] = now_iso
            rec["last_seen"] = now_iso
            rec["active"] = True
            seen[uid] = rec
            if p.get("company") in backfill and p.get("company") not in started:
                rec["backfill"] = True
            else:
                new_postings.append(rec)

    # close postings that have disappeared — but only for companies we actually
    # polled successfully this run (don't deactivate a feed that errored out)
    for uid, rec in seen.items():
        if uid not in current_uids:
            if succeeded is None or rec.get("company") in succeeded:
                rec["active"] = False
            elif tracked is not None and rec.get("company") not in tracked:
                rec["active"] = False

    return new_postings


def prune(seen: dict, now_iso: str, keep_days: int = 60) -> int:
    """Drop closed postings not seen for `keep_days`, so the state file stays bounded."""
    cutoff = (datetime.fromisoformat(now_iso) - timedelta(days=keep_days)).isoformat()
    stale = [uid for uid, rec in seen.items()
             if not rec.get("active") and rec.get("last_seen", "") < cutoff]
    for uid in stale:
        del seen[uid]
    return len(stale)


def active_postings(seen: dict) -> list[dict]:
    out = [r for r in seen.values() if r.get("active")]
    out.sort(key=lambda r: r.get("first_seen", ""), reverse=True)
    return out
