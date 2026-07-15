"""`--since` parsing. Takes `now` explicitly rather than reading the clock
itself, so callers (CLI) capture "now" exactly once per invocation and every
downstream computation (graph nodes included) stays a pure function of its
inputs -- no hidden clock dependency inside the pipeline.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_SINCE_RE = re.compile(r"^(\d+)([dhw])$")
_UNIT_TO_TIMEDELTA = {"d": "days", "h": "hours", "w": "weeks"}


def parse_since(since: str | None, now: datetime) -> datetime | None:
    """ "30d" / "12h" / "2w" -> now - that duration. None/"" -> None (all time)."""
    if not since:
        return None
    match = _SINCE_RE.match(since.strip())
    if not match:
        raise ValueError(f"invalid --since value {since!r}; expected e.g. '30d', '12h', '2w'")
    amount, unit = match.groups()
    return now - timedelta(**{_UNIT_TO_TIMEDELTA[unit]: int(amount)})
