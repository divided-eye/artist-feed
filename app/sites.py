"""Which sites are switched on, and which ones Link Finder searches.

A site that is switched off is gone from the app: no chip, no column in Tracked Artists, no
refreshes, its posts left out of the feed and Link Finder never uses it. Pages you track on it
are kept, so switching it back on brings everything back.

Link Finder searches the sites ticked for it, in a fixed order of preference: Bandcamp, then
SoundCloud, then YouTube. YouTube is off for Link Finder until you tick it, and even then is only
searched for the artists and labels ticked for it in Tracked Artists.
"""
import json

from . import db
from .sources import SOURCES

LOOKUP_ORDER = ("bandcamp", "soundcloud", "youtube")     # first found wins, in this order
LOOKUP_DEFAULT = ("bandcamp", "soundcloud")


def _load(key, default):
    try:
        value = json.loads(db.kv_get(key) or "null")
    except ValueError:
        value = None
    return value if isinstance(value, list) else list(default)


def is_on(key: str) -> bool:
    return key in SOURCES and key not in _load("sites_off", [])


def enabled() -> list:
    """The keys of the sites switched on, in the order they are shown."""
    off = set(_load("sites_off", []))
    return [k for k in SOURCES if k not in off]


def set_on(key: str, on: bool):
    if key not in SOURCES:
        raise ValueError(f"Unknown site '{key}'.")
    off = set(_load("sites_off", []))
    off.discard(key) if on else off.add(key)
    if len(off) >= len(SOURCES):
        raise ValueError("At least one site has to stay on.")
    db.kv_set("sites_off", json.dumps(sorted(off)))


def lookup_sites() -> list:
    """The sites Link Finder searches, best first: ticked for it, and switched on."""
    chosen = set(_load("lookup_sites", LOOKUP_DEFAULT))
    return [k for k in LOOKUP_ORDER if k in chosen and is_on(k)]


def lookup_chosen() -> list:
    """What is ticked for Link Finder, whether or not the site is switched on."""
    chosen = set(_load("lookup_sites", LOOKUP_DEFAULT))
    return [k for k in LOOKUP_ORDER if k in chosen]


def set_lookup_sites(keys):
    keys = [k for k in LOOKUP_ORDER if k in set(keys)]
    db.kv_set("lookup_sites", json.dumps(keys))


def youtube_names() -> set:
    """Tidied names (and other names) of the artists and labels Link Finder may search YouTube for."""
    return set(_load("youtube_lookup", []))


def set_youtube_names(names):
    db.kv_set("youtube_lookup", json.dumps(sorted(set(n for n in names if n))))


def settings() -> dict:
    return {"sites": [{"key": s.key, "label": s.label, "color": s.color, "on": is_on(s.key)} for s in SOURCES.values()],
            "lookup_sites": lookup_chosen(), "lookup_order": list(LOOKUP_ORDER)}
