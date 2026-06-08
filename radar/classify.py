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
    "career fair", "networking event", "tech talk", "datathon", "estimathon",
    "meetup", "meet up", "fireside", "open day", "insight day", "insight week",
    "spring week", "recruiting event", "info evening",
)

# phrases that contain an event keyword but are real ongoing roles, not calendar events
_NOT_EVENT = ("event driven", "event-driven", "events platform", "events engineer")


def classify(title: str) -> str:
    """Return one of: internship, new-grad, full-time, event."""
    t = (title or "").lower()
    if any(k in t for k in _EVENT) and not any(k in t for k in _NOT_EVENT):
        return "event"
    if any(k in t for k in _INTERN):
        return "internship"
    if any(k in t for k in _NEW_GRAD):
        return "new-grad"
    return "full-time"
