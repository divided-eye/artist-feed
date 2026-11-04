"""The purchase list: Bandcamp tracks and releases saved from the feed to buy later, and whether
each one (or anything in the feed) is already in your Bandcamp purchases ("Owned").

Owned is worked out from the purchases Link Finder reads (collection_items and their track lists):
- the same link: the track or release itself was bought;
- a track whose release was bought: its release's link, or the same track title by the same artist
  (or on the same Bandcamp page) in a release you bought. This covers a single track in the feed
  from an album you bought, also when the album was sold on a label's page. Titles are compared
  with version tags kept, so someone's remix is not taken for the original.
A release counts as owned when its link was bought or every one of its tracks is owned, and as
partly owned when only some are."""

import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from . import db, lookup, refresh


def canon(url: str) -> str:
    """A link tidied for comparing: https, lower-case host, no query, fragment or trailing slash."""
    u = urlparse((url or "").strip())
    if not u.netloc:
        return (url or "").strip().lower()
    host = u.netloc.lower().removeprefix("www.")
    return f"https://{host}{u.path.rstrip('/')}"


def _host(url: str) -> str:
    return urlparse(canon(url)).netloc


def _titles(artist: str, title: str):
    """(artist, title) pairs a track may be written as: label pages often title tracks "Artist - Title"."""
    out = [(artist, title)]
    first, sep, rest = (title or "").partition(" - ")
    if sep and rest.strip():
        out.append((first, rest))
    return [(lookup.plain(a or ""), lookup.plain(t or "")) for a, t in out if lookup.plain(t or "")]


_cache = {"at": None, "index": None}


def _index():
    """What you own, read again whenever the purchases change."""
    mark = db.query("SELECT (SELECT COUNT(*) FROM collection_items) AS i, (SELECT COUNT(*) FROM collection_tracks) AS t")[0]
    stamp = (db.kv_get("collection_synced_at"), mark["i"], mark["t"])
    if _cache["at"] == stamp:
        return _cache["index"]
    urls, by_artist, by_host = set(), set(), set()
    items = db.query("SELECT key, title, band, url, kind FROM collection_items")
    hosts = {}
    for it in items:
        urls.add(canon(it["url"]))
        hosts[it["key"]] = _host(it["url"])
        if it["kind"] == "t":                                   # a single track bought on its own
            for a, t in _titles(it["band"], it["title"]):
                by_artist.add((a, t)); by_host.add((hosts[it["key"]], t))
    bands = {it["key"]: it["band"] for it in items}
    for tr in db.query("SELECT item_key, title, artist FROM collection_tracks"):
        host = hosts.get(tr["item_key"], "")
        for artist in {tr["artist"] or "", bands.get(tr["item_key"], "")} - {""}:
            for a, t in _titles(artist, tr["title"]):
                by_artist.add((a, t))
        for _, t in _titles("", tr["title"]):
            by_host.add((host, t))
    index = {"urls": urls, "by_artist": by_artist, "by_host": by_host, "any": bool(items)}
    _cache.update(at=stamp, index=index)
    return index


def track_owned(url, post_url, artist, title, index=None) -> bool:
    ix = index or _index()
    if not ix["any"]:
        return False
    if canon(url) in ix["urls"] or (post_url and canon(post_url) in ix["urls"]):
        return True
    host = _host(post_url or url)
    for a, t in _titles(artist, title):
        if (a, t) in ix["by_artist"] or (host, t) in ix["by_host"]:
            return True
    return False


def release_owned(url, artist, tracks, index=None):
    """("yes" | "part" | "", owned tracks, all tracks) for a release and its [(artist, title, url)] tracks."""
    ix = index or _index()
    if not ix["any"]:
        return "", 0, len(tracks)
    if canon(url) in ix["urls"]:
        return "yes", len(tracks), len(tracks)
    n = sum(1 for a, t, u in tracks if track_owned(u or url, url, a or artist, t, ix))
    return ("yes" if tracks and n == len(tracks) else "part" if n else ""), n, len(tracks)


def _post_tracks(post_ids):
    out = {}
    ids = [i for i in post_ids if i]
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        rows = db.query("SELECT t.post_id, t.title, COALESCE(t.artist, p.author, '') AS artist, t.url FROM tracks t "
                        f"JOIN posts p ON p.id=t.post_id WHERE t.post_id IN ({','.join('?' * len(part))}) ORDER BY t.position",
                        part)
        for r in rows:
            out.setdefault(r["post_id"], []).append((r["artist"], r["title"], r["url"]))
    return out


def saved_keys() -> set:
    return {r["key"] for r in db.query("SELECT key FROM purchase_list")}


def annotate_posts(posts):
    """Cards: "saved", and "owned" ("yes", "part" or "") with "owned_of" [owned, all] for a release."""
    bc = [p for p in posts if p.get("source") == "bandcamp"]
    if not bc:
        return posts
    ix, saved = _index(), saved_keys()
    tracks = _post_tracks([p["id"] for p in bc]) if ix["any"] else {}
    for p in bc:
        artist = p.get("author") or p.get("artist") or ""
        state, n, m = release_owned(p["url"], artist, tracks.get(p["id"], []), ix)
        p["owned"], p["owned_of"], p["saved"] = state, [n, m], canon(p["url"]) in saved
    return posts


def annotate_tracks(rows):
    """List rows (one per track): "saved" and "owned" (True/False)."""
    bc = [r for r in rows if r.get("source") == "bandcamp"]
    if not bc:
        return rows
    ix, saved = _index(), saved_keys()
    for r in bc:
        if r.get("track"):
            r["owned"] = track_owned(r["url"], r.get("post_url"), r.get("artist") or "", r["track"], ix)
        else:                                                   # a release with no track list: its own link only
            r["owned"] = release_owned(r.get("post_url") or r["url"], r.get("artist") or "", [], ix)[0] == "yes"
        r["saved"] = canon(r["url"]) in saved
    return rows


# ---------- the list itself ----------

def items():
    """Everything saved, newest first, each with whether it is owned now."""
    rows = db.query("SELECT * FROM purchase_list ORDER BY added_at DESC, rowid DESC")
    ix = _index()
    tracks = _post_tracks([r["post_id"] for r in rows if r["kind"] == "release"]) if ix["any"] else {}
    for r in rows:
        if r["kind"] == "track":
            r["owned"] = "yes" if track_owned(r["url"], r["post_url"], r["artist"], r["title"], ix) else ""
            r["owned_of"] = None
        else:
            state, n, m = release_owned(r["url"], r["artist"], tracks.get(r["post_id"], []), ix)
            r["owned"], r["owned_of"] = state, [n, m]
    return rows


def add(url, title, artist="", release="", kind="release", image="", post_id=None, post_url=""):
    key = canon(url)
    if not key.startswith("https://") or ("bandcamp.com" not in key and not post_id):   # a feed post may use its own domain
        raise ValueError("Only Bandcamp links can be saved.")
    db.execute("INSERT INTO purchase_list(key, url, title, artist, release, kind, image, post_id, post_url, added_at) "
               "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET title=excluded.title, artist=excluded.artist,"
               " release=excluded.release, image=excluded.image",
               (key, url.strip(), title or url, artist or "", release or "", "track" if kind == "track" else "release",
                image or "", post_id, post_url or "", db.now()))


def remove(url):
    db.execute("DELETE FROM purchase_list WHERE key=?", (canon(url),))


def remove_owned():
    gone = [r["key"] for r in items() if r["owned"] == "yes"]
    for key in gone:
        db.execute("DELETE FROM purchase_list WHERE key=?", (key,))
    return len(gone)


def count():
    """How many saved items you don't own yet (the number on the button)."""
    return sum(1 for r in items() if r["owned"] != "yes")


# ---------- Bandcamp Fridays ----------

# The dates Bandcamp announced (midnight to midnight Pacific time). Newer dates are read from
# isitbandcampfriday.com once a day; these are used until then, and if the site can't be read.
KNOWN_FRIDAYS = ["2026-02-06", "2026-03-06", "2026-05-01", "2026-08-07", "2026-09-04", "2026-10-02",
                 "2026-11-06", "2026-12-04"]
FRIDAYS_URL = "https://isitbandcampfriday.com/"
_MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                           "august", "september", "october", "november", "december"])}


def dates_in(text: str, today: date) -> list:
    """Fridays mentioned on a page, as ISO dates: "2026-11-06", or "November 6, 2026" / "Nov 6"
    (a date with no year is taken as the next one to come). Anything not a Friday is ignored."""
    found = set()
    for y, m, d in re.findall(r"\b(20\d\d)-(\d\d)-(\d\d)\b", text):
        found.add((int(y), int(m), int(d)))
    for name, d, y in re.findall(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d\d))?\b",
                                 text, re.I):
        month = next(v for k, v in _MONTHS.items() if k.startswith(name.lower()[:3]))
        year = int(y) if y else (today.year if (month, int(d)) >= (today.month, today.day) else today.year + 1)
        found.add((year, month, int(d)))
    out = []
    for y, m, d in found:
        try:
            when = date(y, m, d)
        except ValueError:
            continue
        if when.weekday() == 4:
            out.append(when.isoformat())
    return sorted(out)


def fridays() -> list:
    saved = json.loads(db.kv_get("bandcamp_fridays") or "{}")
    return sorted(set(KNOWN_FRIDAYS) | set(saved.get("dates") or []))


def fridays_stale() -> bool:
    saved = json.loads(db.kv_get("bandcamp_fridays") or "{}")
    at = saved.get("at") or ""
    return at < (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


async def read_fridays():
    """Read the dates from isitbandcampfriday.com (at most once a day; failures wait a day too)."""
    saved = json.loads(db.kv_get("bandcamp_fridays") or "{}")
    dates = saved.get("dates") or []
    try:
        async with refresh.make_client() as client:
            r = await client.get(FRIDAYS_URL, timeout=20)
        if r.status_code == 200:
            dates = sorted(set(dates) | set(dates_in(r.text, date.today())))
    except Exception:
        pass
    db.kv_set("bandcamp_fridays", json.dumps({"dates": dates, "at": db.now()}))
