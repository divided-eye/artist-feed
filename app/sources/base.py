"""What every source (Bandcamp, SoundCloud, ...) has to provide.

To add a site: copy one of the existing modules, subclass Source, and register
it in app/sources/__init__.py. Nothing else in the app needs to change; the UI
builds its inputs and filters from the registered sources.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class Track:
    title: str
    duration: Optional[float] = None   # seconds
    artist: Optional[str] = None       # only when it differs from the release's artist
    ext_id: Optional[str] = None       # the site's own id for the track, if any
    url: Optional[str] = None


@dataclass
class Post:
    external_id: str            # stable per account; usually the post URL
    title: str
    url: str
    image: Optional[str] = None
    kind: Optional[str] = None  # "album", "track", ...
    author: Optional[str] = None
    published_at: Optional[str] = None  # UTC, "YYYY-MM-DDTHH:MM:SSZ"
    label: Optional[str] = None
    release: Optional[str] = None       # the album/EP this belongs to
    embed: Optional[str] = None         # what the site's embeddable player needs (see index.html)
    tracks: Optional[list] = None       # list of Track; None = leave stored tracks alone


@dataclass
class FetchResult:
    posts: list = field(default_factory=list)
    display_name: Optional[str] = None
    avatar: Optional[str] = None
    warning: Optional[str] = None  # shown next to the account in the UI


class SourceError(Exception):
    """A problem worth showing to the user as-is."""


class Source:
    key = ""
    label = ""
    color = "#888888"
    placeholder = ""

    def short(self, handle: str) -> str:
        """How a stored handle is shown in the artist table (must survive normalize())."""
        return handle

    def normalize(self, text: str) -> tuple:
        """Turn whatever the user typed into (handle, profile_url)."""
        raise NotImplementedError

    async def fetch(self, client, account: dict, known_ids: set) -> FetchResult:
        """Return recent posts. `account["meta"]` is a dict the source may
        update to remember things between runs (it is saved afterwards)."""
        raise NotImplementedError


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
