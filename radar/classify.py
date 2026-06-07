"""Best-effort classification of a job title into a category."""

_INTERN = (
    "intern", "internship", "co-op", "co op", "coop", "summer analyst",
    "summer associate", "placement", "apprentic", "working student",
    "industrial placement", "vacation scheme", "spring week", "insight",
)

_NEW_GRAD = (
    "new grad", "new graduate", "university grad", "campus", "graduate program",
    "graduate programme", "early career", "early-career", "entry level",
    "entry-level", "0-2 years", "recent graduate", "rotational", "associate program",
)

_EVENT = (
    "webinar", "hackathon", "info session", "information session", "open house",
    "career fair", "networking event", "tech talk", "coding challenge",
    "competition", "datathon", "puzzle", "estimathon", "event ",
)


def classify(title: str) -> str:
    """Return one of: internship, new-grad, full-time, event."""
    t = (title or "").lower()
    if any(k in t for k in _EVENT):
        return "event"
    if any(k in t for k in _INTERN):
        return "internship"
    if any(k in t for k in _NEW_GRAD):
        return "new-grad"
    return "full-time"
