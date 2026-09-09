"""One vocabulary for sport names.

intervals.icu says ``Run``, FIT files say ``running``. Storing both would
mean every consumer has to know both, so everything is normalised here on
the way in.
"""

from __future__ import annotations

from typing import Final

RUN: Final = "Run"
RIDE: Final = "Ride"
SWIM: Final = "Swim"
WALK: Final = "Walk"
HIKE: Final = "Hike"
OTHER: Final = "Other"

CANONICAL: Final[frozenset[str]] = frozenset({RUN, RIDE, SWIM, WALK, HIKE, OTHER})

# Lower-cased spellings seen from either source.
_ALIASES: Final[dict[str, str]] = {
    "run": RUN,
    "running": RUN,
    "trailrun": RUN,
    "trail_running": RUN,
    "treadmill": RUN,
    "virtualrun": RUN,
    "ride": RIDE,
    "cycling": RIDE,
    "virtualride": RIDE,
    "gravelride": RIDE,
    "mountainbikeride": RIDE,
    "ebikeride": RIDE,
    "swim": SWIM,
    "swimming": SWIM,
    "openwaterswim": SWIM,
    "lap_swimming": SWIM,
    "walk": WALK,
    "walking": WALK,
    "hike": HIKE,
    "hiking": HIKE,
}


def normalise_sport(raw: str | None) -> str:
    """Map a source specific sport name onto the canonical vocabulary.

    Anything unknown becomes ``Other`` rather than being invented into a
    category it might not belong to.
    """
    if not raw:
        return OTHER
    key = raw.strip().lower().replace(" ", "_")
    if key in _ALIASES:
        return _ALIASES[key]
    return _ALIASES.get(key.replace("_", ""), OTHER)
