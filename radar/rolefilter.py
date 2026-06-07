"""Keep only roles you'd actually apply to.

Reads settings.json (if present) for `role_keywords` and `exclude_keywords`.
A posting is kept when its title matches at least one role keyword AND no
exclude keyword. Matching is word-boundary based so short tokens like "ai"
don't match "email" / "domain".

Set role_keywords to [] in settings.json to disable filtering (track all).
"""

from __future__ import annotations

import json
import os
import re

_DEFAULTS = {
    "role_keywords": [
        # software engineering
        "software", "engineer", "engineering", "developer", "swe", "programmer",
        "full stack", "fullstack", "full-stack", "backend", "back-end",
        "frontend", "front-end", "infrastructure", "platform", "systems",
        "distributed", "compiler", "embedded", "firmware", "devops", "sre",
        "reliability", "security engineer", "mobile", "ios", "android", "web",
        "machine learning", "ml", "ai", "data engineer", "data scientist",
        "research engineer", "research scientist", "applied scientist",
        # quant
        "quant", "quantitative", "trader", "trading", "strategist",
        "systematic", "low latency", "researcher", "research analyst",
        # early career
        "intern", "internship", "co-op", "coop", "new grad", "new graduate",
        "graduate", "early career", "early-career", "university", "campus",
        "apprentice", "rotational",
    ],
    "exclude_keywords": [],
}


def load_settings(root: str) -> dict:
    path = os.path.join(root, "settings.json")
    cfg = dict(_DEFAULTS)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def _compile(words):
    if not words:
        return None
    pat = "|".join(re.escape(w) for w in words)
    return re.compile(r"(?<![a-z0-9])(?:" + pat + r")(?![a-z0-9])", re.IGNORECASE)


def make_filter(settings: dict):
    inc = _compile(settings.get("role_keywords"))
    exc = _compile(settings.get("exclude_keywords"))

    def keep(title: str) -> bool:
        t = title or ""
        if exc and exc.search(t):
            return False
        if inc is None:
            return True
        return bool(inc.search(t))

    return keep
