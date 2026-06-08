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


def diff(seen: dict, current: list[dict], now_iso: str, succeeded=None) -> list[dict]:
    """Mutates `seen`. Returns the brand-new postings detected this run.

    `succeeded` is the set of company names whose feed was polled successfully
    this run. A posting is marked closed only if its company was polled OK and
    the posting no longer appears — so a transient feed outage never wipes (and
    later falsely re-surfaces) a company's roles. If `succeeded` is None, every
    company is assumed polled (legacy behaviour).
    """
    current_uids = set()
    new_postings = []

    for p in current:
        uid = p["uid"]
        current_uids.add(uid)
        if uid in seen:
            rec = seen[uid]
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
            new_postings.append(rec)

    # close postings that have disappeared — but only for companies we actually
    # polled successfully this run (don't deactivate a feed that errored out)
    for uid, rec in seen.items():
        if uid not in current_uids:
            if succeeded is None or rec.get("company") in succeeded:
                rec["active"] = False

    return new_postings


def active_postings(seen: dict) -> list[dict]:
    out = [r for r in seen.values() if r.get("active")]
    out.sort(key=lambda r: r.get("first_seen", ""), reverse=True)
    return out
