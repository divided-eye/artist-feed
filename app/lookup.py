"""Tracklist lookup: for each "artist / title" row, find the Bandcamp release the
track is on; failing that, the artist's Bandcamp page, then their SoundCloud page.

Bandcamp's search often misses a track when you search by its title, so the
search is only one of several places looked, in this order:

  1. your own feed (tracks collected from the pages you track)          no network
  2. your Bandcamp purchases (the public collection page of your account)
       a. track lists that come with the collection
       b. releases you own by that artist, opened to read their track lists
  3. Bandcamp search: "artist title" as a track, the title alone, and as an album
  4. the artist's own Bandcamp page: every release opened and scanned for the title
  5. fallbacks: the artist's Bandcamp page itself, then their SoundCloud page

Everything fetched is remembered (release track lists, artist pages, results),
so a second lookup of the same artist or track costs little or nothing.
Each result carries the list of steps tried, so a miss can be diagnosed.
"""
import asyncio
import csv
import io
import json
import os
import re
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from functools import lru_cache
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup

from . import db, pace, refresh
from .sources import SOURCES
from .sources.bandcamp import parse_music_grid, parse_profile, parse_release, parse_search

MAX_ROWS = int(os.environ.get("LOOKUP_MAX_ROWS", "500"))
SCAN_RELEASES = int(os.environ.get("LOOKUP_SCAN_RELEASES", "40"))  # releases opened per artist page, per row
OWNED_RELEASES = int(os.environ.get("LOOKUP_OWNED_RELEASES", "15"))  # purchases by the artist opened per row
COLLECTION_MAX_AGE_HOURS = float(os.environ.get("COLLECTION_MAX_AGE_HOURS", "24"))
SEARCH_URL = "https://bandcamp.com/search"
# Used only if the search page is unavailable. Not documented by Bandcamp.
SEARCH_API = "https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic"
COLLECTION_API = "https://bandcamp.com/api/fancollection/1/collection_items"
CACHE_VERSION = "v2"   # bump to forget results saved by an older, weaker lookup
ENGINE = 7             # raise when the lookup gets better at finding things: saved answers that were
                       # NOT a Bandcamp release are then searched for again instead of being reused

jobs = {}          # id -> {"rows": [...], "done": int, "total": int, "finished": bool, ...}
_tasks = set()
collection_state = {"syncing": False, "error": "", "progress": ""}
# Reading the full track list of every purchase (Bandcamp's collection only names one track each).
deep_state = {"running": False, "note": ""}
DEEP_EXTRA = float(os.environ.get("DEEP_READ_EXTRA_SECONDS", "1"))    # added to the normal gap: it is in no hurry
DEEP_AUTO = os.environ.get("DISABLE_DEEP_READ") != "1"


# ---------- reading the uploaded tracklist ----------

class TracklistError(Exception):
    pass


def read_table(data: bytes) -> list:
    """Rows of cells from an .xlsx file, a CSV file, or text pasted from a spreadsheet."""
    if data[:4] == b"PK\x03\x04":
        from openpyxl import load_workbook
        try:
            sheet = load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
            return [["" if c is None else str(c).strip() for c in row]
                    for row in sheet.iter_rows(values_only=True)]
        except Exception:
            raise TracklistError("Couldn't read that Excel file. Try saving it as CSV.")
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise TracklistError("Old-style .xls files aren't supported. Save as .xlsx or CSV, "
                             "or copy the rows and paste them.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    lines = [l for l in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if l.strip()]
    if not lines:
        return []
    sample = "\n".join(lines[:20])
    if "\t" in sample:
        return [[c.strip() for c in l.split("\t")] for l in lines]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    return [[c.strip() for c in row] for row in csv.reader(lines, delimiter=delimiter)]


TITLE_HEADERS = ("title", "track title", "track name", "song", "song title", "name", "track")


def pick_tracks(rows: list) -> list:
    """[{n, artist, title}] from the table, whatever other columns it has.

    With a header row, the "Artist" column and the first "Title" column are used
    (a later "Title" column, such as the name of a mix, is ignored).
    """
    rows = [r for r in rows if any(c for c in r)]
    artist_col = title_col = None
    start = 0
    for i, row in enumerate(rows[:15]):
        heads = [c.casefold().strip(" :#") for c in row]
        if "artist" in heads or "artists" in heads:
            artist_col = heads.index("artist") if "artist" in heads else heads.index("artists")
            for want in TITLE_HEADERS:
                found = [j for j, h in enumerate(heads) if h == want and j != artist_col]
                if found:
                    title_col = found[0]
                    break
            if title_col is None:
                raise TracklistError('Found an "Artist" column but no "Title" column.')
            start = i + 1
            break
    out = []
    for row in rows[start:]:
        cell = lambda j: row[j].strip() if j is not None and j < len(row) else ""
        if artist_col is not None:
            artist, title = cell(artist_col), cell(title_col)
        else:
            cells = [c for c in row]
            if len(cells) >= 3 and re.fullmatch(r"\d+[.)]?", cells[0].strip() or "x"):
                artist, title = cells[1].strip(), cells[2].strip()       # number, artist, title
            elif len(cells) >= 2 and cells[1].strip():
                artist, title = cells[0].strip(), cells[1].strip()       # artist, title
            else:                                                        # "Artist - Title"
                line = re.sub(r"^\s*\d+[.)]?\s+", "", cells[0])
                parts = re.split(r"\s+[-–—]\s+", line, maxsplit=1)
                artist, title = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", line.strip())
        if artist or title:
            out.append({"n": len(out) + 1, "artist": artist, "title": title})
    if not out:
        raise TracklistError("No tracks found. The list needs an artist and a title for each track.")
    if len(out) > MAX_ROWS:
        raise TracklistError(f"That list has {len(out)} tracks; the limit is {MAX_ROWS} at a time.")
    return out


# ---------- matching ----------

# Words that mark a bracketed tag as "someone's version of this track".
_REMIX_WORD = (r"remix|rmx|re-?mix|edit|re-?edit|bootleg|boot|flip|refix|re-?fix|rework|re-?work|re-?rub|"
               r"remake|re-?lick|mash-?up|redo|re-?twist|re-?fit|cover|treatment|version|mix|dub|vip")
_REMIX_TAG = re.compile(r"[\(\[]\s*([^\)\]]*?)\s*\b(?:%s)\b\s*[\)\]]" % _REMIX_WORD, re.I)
_ANY_TAG = re.compile(r"[\(\[][^\)\]]*(?:mix|edit|version|remaster|bonus|free|dub|\b(?:%s)\b)[^\)\]]*[\)\]]" % _REMIX_WORD)
# Words in such a tag that describe the version rather than name a person.
_GENERIC = set("""original extended radio club instrumental vocal dub vip album single full short long clean
    dirty explicit main alternate alt demo live final new old digital vinyl bonus free download unreleased
    dubplate special mini intro outro acapella remastered remaster mono stereo official unofficial exclusive
    the a an his her their own self dubstep garage jungle dnb bass uk us deep dark heavy 140 2step ukg
    halftime breaks breakbeat techno house drill grime footwork juke trap amapiano riddim hardcore rave
    ambient acid electro bassline speed slow sped slowed reverb pitched festival tool dj edit refix flip
    bootleg remix rework mix version unofficial steppers stepper roller rollers sound system soundsystem""".split())


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()


def norm(text: str) -> str:
    """For comparing titles and names: no accents, case, punctuation, "feat. X", or version tags
    such as "(Original Mix)" and "(IOWLA Flip)" (who made a version is checked separately)."""
    text = _ANY_TAG.sub(" ", _fold(text))
    text = re.sub(r"\b(feat|ft|featuring)\b.*$", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def plain(text: str) -> str:
    """Like norm, but keeps everything, including version tags."""
    return re.sub(r"[^a-z0-9]+", " ", _fold(text)).strip()


@lru_cache(maxsize=8192)
def remix_credits(title: str) -> tuple:
    """Who made this version, as written: "Temperature (River Dub Edit)" -> ("River Dub", "River").

    The second form drops describing words from the ends ("Jah Beer Dub Refix" -> "Jah Beer"), since
    it can't be known whether "Dub" is part of the name. Empty for "(Original Mix)", "(VIP)" etc.
    """
    out = []
    for inner in _REMIX_TAG.findall(title or ""):
        words = [w for w in inner.replace("&", " & ").split() if not w.strip(".,'").isdigit()]
        trimmed = list(words)
        while trimmed and (plain(trimmed[-1]) in _GENERIC or not plain(trimmed[-1])):
            trimmed.pop()
        while trimmed and (plain(trimmed[0]) in _GENERIC or not plain(trimmed[0])):
            trimmed.pop(0)
        if not trimmed:
            continue                                    # nothing but describing words
        for form in (trimmed, words):                   # the bare name first: "River", then "River Dub"
            text = " ".join(form).strip(" -&,")
            if text and text not in out:
                out.append(text)
    return tuple(out)


@lru_cache(maxsize=8192)
def remixer_names(title: str) -> tuple:
    """Normalised names of the people in remix_credits, each form and each person."""
    return tuple(dict.fromkeys(p for credit in remix_credits(title) for p in artist_parts(credit)))


def base_title(title: str) -> str:
    return _REMIX_TAG.sub(" ", title or "").strip()


def artist_parts(text: str) -> list:
    """Normalised names in an artist credit: the whole thing, then each artist in it."""
    pieces = re.split(r"\s*(?:,|&|\+|/|\bx\b|\bvs\.?\b|\band\b|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bwith\b)\s*",
                      text or "", flags=re.I)
    return list(dict.fromkeys(p for p in (norm(x) for x in [text] + pieces) if p))


def ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    m = SequenceMatcher(None, a, b)
    if m.real_quick_ratio() < 0.5 or m.quick_ratio() < 0.5:    # cheap checks first; below 0.5 never matters
        return 0.0
    return m.ratio()


def name_sim(a: str, b: str) -> float:
    """How alike two normalised names are; one being the other plus extra words counts as close
    ("somah" / "somah uk", "delimar" / "sasa delimar")."""
    r = ratio(a, b)
    if r < 0.9 and min(len(a), len(b)) >= 4:
        ta, tb = set(a.split()), set(b.split())
        if ta <= tb or tb <= ta:
            r = 0.9
    return r


def slug_words(url: str) -> str:
    """The readable part of a Bandcamp address: ".../album/dno021-kercha-open-the-door-lp" ->
    "dno021 kercha open the door lp"."""
    return (urlparse(url or "").path.rstrip("/").rsplit("/", 1)[-1]).replace("-", " ")


def release_context(rel: dict) -> list:
    """Places besides the artist credit where a release names its artist: its title and its address.
    Labels often credit a release to themselves and put the artist in the title only, e.g.
    "DNO021 - Kercha - Open The Door LP" by DNO Records."""
    return [rel.get("title"), rel.get("release"), slug_words(rel.get("url")), slug_words(rel.get("asked")),
            slug_words(rel.get("release_url"))]


def score(artist: str, title: str, cand: dict) -> tuple:
    """(title score, artist score) for a candidate {"name", "artists": [names...], "context": [text...]}.
    "context" is free text around the release (its title, its address); the wanted artist appearing
    there as whole words counts as an artist match."""
    want_title, wanted = norm(title), artist_parts(artist)
    name = cand.get("name") or ""
    prefix, _, rest = name.partition(" - ")            # label pages often title tracks "Artist - Title"
    t = max(ratio(want_title, norm(name)), ratio(want_title, norm(rest)) if rest else 0.0)
    have = [p for n in cand.get("artists") or [] for p in artist_parts(n or "")]
    if rest:
        have += artist_parts(prefix)
    a = max((name_sim(w, h) for w in wanted for h in have), default=0.0)
    if a < 0.9 and wanted:
        around = " " + " ".join(norm(c) for c in cand.get("context") or [] if c) + " "
        if any(len(w) >= 2 and f" {w} " in around for w in wanted):
            a = 0.9
    if not wanted:
        a = 0.6                                        # no artist given: the title alone is a weak match
    # Versions. Asked for someone's edit/remix/bootleg: the candidate must name that person, and
    # being theirs is as good as being the original artist's. Asked for the plain track: someone
    # else's version of it is only a "check" match.
    makers = remixer_names(title)
    if makers:
        told = " " + " ".join(plain(x) for x in [name] + list(cand.get("artists") or [])
                              + list(cand.get("context") or []) if x) + " "
        if any(f" {m} " in told for m in makers):
            a = max(a, 0.9)
        else:
            t = min(t, 0.7)
    elif remixer_names(name):
        t = min(t, 0.8)
    return t, a


def judge(t: float, a: float) -> str:
    if t >= 0.88 and a >= 0.85:
        return "bandcamp"
    if (t >= 0.88 and a >= 0.55) or (t >= 0.75 and a >= 0.85):
        return "bandcamp_check"
    return ""


def best_candidate(artist, title, cands):
    """The best acceptable candidate and its verdict ("bandcamp", "bandcamp_check" or "")."""
    best = ("", None, -1.0)
    for cand in cands:
        t, a = score(artist, title, cand)
        verdict = judge(t, a)
        rank = (2 if verdict == "bandcamp" else 1 if verdict else 0) * 10 + t + a
        if verdict and rank > best[2]:
            best = (verdict, cand, rank)
    return best[0], best[1]


def distinctive(title: str) -> bool:
    """Long enough that the title alone says something ("Dragon Language", not "Air" or "02")."""
    n = norm(title)
    return len(n) >= 8 or len(n.split()) >= 2


# ---------- small stores ----------

def cache_get(kind, key, max_age_days):
    rows = db.query("SELECT data, at FROM web_cache WHERE kind=? AND key=?", (kind, key))
    if not rows:
        return None
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(rows[0]["at"].replace("Z", "+00:00"))
    except ValueError:
        return None
    return json.loads(rows[0]["data"]) if age <= timedelta(days=max_age_days) else None


def cache_put(kind, key, data):
    db.execute("INSERT INTO web_cache(kind, key, data, at) VALUES(?,?,?,?) "
               "ON CONFLICT(kind, key) DO UPDATE SET data=excluded.data, at=excluded.at",
               (kind, key, json.dumps(data), db.now()))


class Blocked(Exception):
    """A site is refusing requests; stop asking it for the rest of this lookup."""


class Net:
    """Requests to Bandcamp, taken at the app-wide pace (see pace.py). A 429 answer is waited
    out and retried, several times, before it is passed back to the caller."""

    def __init__(self, client, on_wait=None):
        self.client, self.count, self.on_wait = client, 0, on_wait

    async def request(self, method, url, **kw):
        for attempt in range(pace.TRIES):
            await pace.wait()
            self.count += 1
            r = await self.client.request(method, url, **kw)
            if r.status_code != 429 or attempt == pace.TRIES - 1:
                if self.on_wait:
                    self.on_wait("")
                return r
            seconds = pace.pause_for(r, attempt)
            pace.slow_down(seconds)
            if self.on_wait:
                self.on_wait(f"Bandcamp asked for a pause; carrying on in {int(seconds)}s")

    async def get(self, url, **kw):
        return await self.request("GET", url, **kw)


# ---------- Bandcamp: pages ----------

def root_of(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


# Every track on every release page the lookup has ever opened, by normalised title.
# Built once from the saved pages, then added to as new pages are read.
_pages = {"index": None}


def _index_release(index, rel):
    if not rel or not rel.get("tracks"):
        return
    title = rel.get("title") or ""
    extra = [rel.get("artist"), title.partition(" - ")[0] if " - " in title else ""]
    for t in rel["tracks"]:
        entry = {"name": t["title"], "artists": [t.get("artist")] + extra, "rel": rel,
                 "context": release_context(rel)}
        index.setdefault(norm(t["title"]), []).append(entry)
        rest = t["title"].partition(" - ")[2]
        if rest:                                         # "Artist - Title" is also findable by its title
            index.setdefault(norm(rest), []).append(entry)


def page_index():
    if _pages["index"] is None:
        index = {}
        for row in db.query("SELECT data FROM web_cache WHERE kind='release'"):
            try:
                _index_release(index, json.loads(row["data"]))
            except ValueError:
                pass
        _pages["index"] = index
    return _pages["index"]


PAGE_FORMAT = 2   # 2: also keeps track names that are only written out in the description


def needs_rereading(saved, url) -> bool:
    """Saved by a version that only kept playable tracks, and it found at most one on an album:
    the real track list may be in the description (a preview mix plus a written list)."""
    return bool(saved) and saved.get("v", 1) < PAGE_FORMAT and "/album/" in url and len(saved.get("tracks") or []) <= 1


async def get_release(net, url):
    """A release or track page, reduced to what matching needs. Remembered for six months."""
    saved = cache_get("release", url, 180)
    if saved is not None and not needs_rereading(saved, url):
        return saved or None
    r = await net.get(url)
    if r.status_code == 429:
        raise Blocked("Bandcamp is rate-limiting requests")
    if r.status_code != 200:
        if r.status_code == 404:
            cache_put("release", url, {})
        return None
    rel = parse_release(r.text, url)
    data = {"url": rel["url"], "asked": url, "title": rel["title"] or "", "artist": rel["author"] or "",
            "year": (rel["published_at"] or "")[:4], "release": rel["release"] or "",
            "release_url": rel["release_url"] or "", "label": rel["label"] or "",
            "tracks": [{"title": t.title, "artist": t.artist or ""} for t in rel["tracks"] or []]
                      + [{"title": t, "artist": "", "listed": True} for t in rel["listed_tracks"]],
            "v": PAGE_FORMAT}
    cache_put("release", url, data)
    _index_release(page_index(), data)
    return data


async def get_grid(net, base):
    """An artist or label page: its name and the releases listed on it. Remembered for three days."""
    saved = cache_get("grid", base, 3)
    if saved is not None:
        return saved or None
    r = await net.get(base + "/music")
    if r.status_code == 429:
        raise Blocked("Bandcamp is rate-limiting requests")
    if r.status_code != 200:
        if r.status_code == 404:
            cache_put("grid", base, {})
        return None
    items = [{"title": i["title"], "url": i["url"], "artist": i["author"] or ""}
             for i in parse_music_grid(r.text, base)]
    if not items:                                   # a page with one release shows it directly
        rel = parse_release(r.text, str(r.url))
        if rel["is_release"]:
            items = [{"title": rel["title"] or "", "url": rel["url"], "artist": rel["author"] or ""}]
    data = {"name": parse_profile(r.text)["name"] or "", "items": items}
    cache_put("grid", base, data)
    return data


def match_in_release(artist, title, rel):
    """Is the wanted track on this release? Returns (verdict, matched track title)."""
    extra = [rel.get("artist"), rel.get("title", "").partition(" - ")[0] if " - " in rel.get("title", "") else ""]
    around = release_context(rel)
    cands = [{"name": t["title"], "artists": [t.get("artist")] + extra, "context": around}
             for t in rel.get("tracks") or []]
    if not cands and rel.get("title"):              # no track list on the page: go by its title
        cands = [{"name": rel["title"], "artists": extra, "context": around}]
    verdict, cand = best_candidate(artist, title, cands)
    return verdict, (cand or {}).get("name", "")


def release_result(rel, verdict, found, source):
    """The link to give for a matched page: the album when the page is a track on one."""
    return {"status": verdict, "site": "Bandcamp", "url": rel.get("release_url") or rel["url"],
            "release": rel.get("release") or rel.get("title") or "", "year": rel.get("year") or "",
            "found": found, "note": source}


# ---------- Bandcamp: search ----------

async def bandcamp_search(net, text: str, kind: str) -> list:
    """kind: "t" tracks, "a" albums, "b" artists and labels."""
    r = await net.get(SEARCH_URL, params={"q": text, "item_type": kind})
    if r.status_code == 200:
        return parse_search(r.text)
    # Search page refused: try the search box's JSON endpoint before giving up.
    try:
        j = await net.request("POST", SEARCH_API, json={"search_text": text, "search_filter": kind,
                                                        "full_page": False, "fan_id": None})
        if j.status_code == 200:
            names = {"t": "track", "a": "album", "b": "artist"}
            return [{"type": names.get(x.get("type"), ""), "name": x.get("name") or "",
                     "artist": x.get("band_name") or "", "album": x.get("album_name") or "",
                     "url": x.get("item_url_path") or x.get("item_url_root") or "", "released": ""}
                    for x in (j.json().get("auto") or {}).get("results") or []
                    if x.get("type") == kind and (x.get("item_url_path") or x.get("item_url_root"))]
    except Exception:
        pass
    raise Blocked(f"Bandcamp search answered {r.status_code}")


def year_in(text):
    m = re.search(r"\b(19|20)\d{2}\b", text or "")
    return m.group(0) if m else ""


async def search_step(net, artist, title, steps):
    """Bandcamp search, several ways. Returns a result or None."""
    parts = artist_parts(artist)
    queries = [("t", f"{artist} {title}".strip())]
    if remix_credits(title):                                          # as the remixer's own upload
        queries.append(("t", f"{remix_credits(title)[0]} {base_title(title)}"))
    if len(parts) > 1:
        queries += [("t", f"{p} {title}") for p in parts[1:3]]       # each named artist on their own
    if len(norm(title)) >= 4:
        queries.append(("t", title))                                  # title alone; artist must still match
    queries.append(("a", f"{artist} {title}".strip()))                # a release named after the track
    if distinctive(title):
        queries.append(("a", title))
    seen, check, verified = set(), None, set()
    for kind, text in dict.fromkeys(queries):
        found = [c for c in await bandcamp_search(net, text, kind) if c["url"] not in seen]
        seen.update(c["url"] for c in found)
        want = "track" if kind == "t" else "album"
        cands = [dict(c, artists=[c.get("artist")], context=[c.get("album"), slug_words(c.get("url"))])
                 for c in found if c["type"] in (want, "")]
        verdict, cand = best_candidate(artist, title, cands)
        steps.append(f'search {want}s "{text}": {len(cands)} result(s)'
                     + (f', {"match" if verdict == "bandcamp" else "possible"}: {cand["artist"]} — {cand["name"]}' if cand else ""))
        if not cand:
            # Right title, artist not named in the result (a label credits the release to itself):
            # open the closest few and check the artist against the page and the album's address.
            close = [c for c in cands if score(artist, title, c)[0] >= 0.88 and c["url"] not in verified][:3]
            for c in close:
                verified.add(c["url"])
                rel = await _safe_release(net, c["url"])
                v, name = match_in_release(artist, title, rel) if rel else ("", "")
                if v == "bandcamp":
                    steps.append(f'opened "{c["name"]}" by {c["artist"]}: the release is "{rel.get("release") or rel.get("title")}", artist confirmed')
                    return release_result(rel, v, f'{c["artist"]} \u2014 {name}', "Bandcamp search")
            continue
        out = {"status": verdict, "site": "Bandcamp", "url": cand["url"], "release": cand.get("album") or (cand["name"] if kind == "a" else ""),
               "found": " — ".join(x for x in (cand.get("artist"), cand.get("name")) if x),
               "year": year_in(cand.get("released")), "note": "Bandcamp search"}
        try:    # the page itself gives the album a track is on, and the release date
            rel = await get_release(net, cand["url"])
            if rel:
                out.update(url=rel.get("release_url") or out["url"], release=rel.get("release") or out["release"],
                           year=rel.get("year") or out["year"])
        except Blocked:
            raise
        except Exception:
            pass
        if verdict == "bandcamp":
            return out
        check = check or out
    return check


# ---------- Bandcamp: your purchases ----------

def collection_fan():
    return (db.kv_get("bandcamp_fan") or os.environ.get("BANDCAMP_FAN") or "").strip()


def collection_status():
    counts = db.query("SELECT (SELECT COUNT(*) FROM collection_items) AS items,"
                      " (SELECT COUNT(*) FROM collection_tracks) AS tracks,"
                      " (SELECT COUNT(DISTINCT url) FROM collection_items) AS releases,"
                      " (SELECT COUNT(DISTINCT c.url) FROM collection_items c JOIN web_cache w"
                      "   ON w.kind='release' AND w.key=c.url) AS releases_read")[0]
    return {"fan": collection_fan(), "items": counts["items"], "tracks": counts["tracks"],
            "synced_at": db.kv_get("collection_synced_at"),
            "complete": db.kv_get("collection_complete") == "1", "paused": pace.paused_seconds(),
            "releases": counts["releases"], "releases_read": counts["releases_read"],
            "deep_running": deep_state["running"], "deep_note": deep_state["note"],
            **collection_state}


def _store_collection(fan, items, tracklists):
    """Save one batch. Returns how many of the items were new."""
    by_key, by_featured = {}, {}
    for it in items:
        kind = it.get("tralbum_type") or ("t" if it.get("item_type") == "track" else "a")
        ident = it.get("tralbum_id") or it.get("item_id")
        if not ident or not it.get("item_url"):
            continue
        key = f"{kind}{ident}"
        by_key[key] = it
        if it.get("featured_track"):
            by_featured[it["featured_track"]] = key
    new = 0
    with db._lock:                                   # one transaction per batch
        c = db.conn()
        for key, it in by_key.items():
            if not c.execute("SELECT 1 FROM collection_items WHERE key=?", (key,)).fetchone():
                new += 1
            c.execute("INSERT INTO collection_items(key, fan, title, band, url, kind) VALUES(?,?,?,?,?,?) "
                      "ON CONFLICT(key) DO UPDATE SET title=excluded.title, band=excluded.band, url=excluded.url",
                      (key, fan, it.get("item_title") or "", it.get("band_name") or "",
                       it["item_url"].split("?")[0], key[0]))
        for list_key, tracks in (tracklists or {}).items():
            tracks = [t for t in tracks or [] if isinstance(t, dict) and t.get("title")]
            key = list_key if list_key in by_key else next(
                (by_featured[t["id"]] for t in tracks if t.get("id") in by_featured), None)
            if not key or not tracks:
                continue
            c.execute("DELETE FROM collection_tracks WHERE item_key=?", (key,))
            c.executemany("INSERT INTO collection_tracks(item_key, position, title, artist) VALUES(?,?,?,?)",
                          [(key, t.get("track_number") or i + 1, t["title"], t.get("artist") or "")
                           for i, t in enumerate(tracks)])
        c.commit()
    return new


async def sync_collection(full=False, on_progress=None):
    """Read the public collection page of the configured Bandcamp account, newest purchases first.

    The first read works back through every purchase; if it is interrupted it carries on from
    where it stopped next time. Once complete, later reads only fetch purchases made since.
    full=True starts a complete re-read (when the previous one had finished).
    """
    fan = collection_fan()
    if not fan or collection_state["syncing"]:
        return
    collection_state.update(syncing=True, error="", progress="Reading your Bandcamp purchases\u2026")
    pace._state["foreground"] += 1

    def say(text):
        collection_state["progress"] = text
        if on_progress:
            on_progress(text)

    done = total = 0
    try:
        if db.query("SELECT 1 FROM collection_items WHERE fan<>? LIMIT 1", (fan,)):
            db.execute("DELETE FROM collection_tracks")          # a different account: start over
            db.execute("DELETE FROM collection_items")
            db.kv_set("collection_complete", "")
            db.kv_set("collection_token", "")
        complete = db.kv_get("collection_complete") == "1"
        if full and complete:
            complete = False
            db.kv_set("collection_complete", "")
            db.kv_set("collection_token", "")
        async with refresh.make_client() as client:
            waiting = {"text": ""}
            net = Net(client, on_wait=lambda text: (waiting.update(text=text), text and say(text)))
            r = await net.get(f"https://bandcamp.com/{quote(fan)}")
            if r.status_code == 404:
                raise ValueError(f'No Bandcamp fan page called "{fan}".')
            if r.status_code != 200:
                raise ValueError(f"Bandcamp answered {r.status_code} for your collection page.")
            holder = BeautifulSoup(r.text, "lxml").select_one("#pagedata[data-blob]")
            blob = json.loads(holder["data-blob"]) if holder else {}
            fan_id = (blob.get("fan_data") or {}).get("fan_id")
            info = blob.get("collection_data") or {}
            if not fan_id:
                raise ValueError("Couldn't read that collection page (is the collection public?).")
            cache = (blob.get("item_cache") or {}).get("collection") or {}
            order = info.get("sequence") or list(cache)
            new = _store_collection(fan, [cache[k] for k in order if k in cache],
                                    (blob.get("tracklists") or {}).get("collection") or {})
            total = info.get("item_count") or 0
            # Not finished yet: carry on from the saved place. Finished: only what is newer.
            token = (db.kv_get("collection_token") or info.get("last_token")) if not complete else info.get("last_token")
            asked = set()
            while token and token not in asked and (not complete or new):
                asked.add(token)                         # never ask for the same place twice
                done = db.query("SELECT COUNT(*) AS n FROM collection_items")[0]["n"]
                say(f"Reading your Bandcamp purchases: {done:,} of {total:,}" if total else
                    f"Reading your Bandcamp purchases: {done:,}")
                j = await net.request("POST", COLLECTION_API, json={
                    "fan_id": fan_id, "older_than_token": token, "count": 100})
                if j.status_code != 200:
                    raise ValueError(f"Bandcamp answered {j.status_code} while reading your purchases.")
                data = j.json()
                items = data.get("items") or []
                new = _store_collection(fan, items, data.get("tracklists") or {})
                token = data.get("last_token") if data.get("more_available") and items else None
                if not complete:
                    db.kv_set("collection_token", token or "")   # where to carry on if interrupted
            if not complete:
                db.kv_set("collection_complete", "1")
        db.kv_set("collection_synced_at", db.now())
    except Exception as e:
        have = db.query("SELECT COUNT(*) AS n FROM collection_items")[0]["n"]
        reason = str(e) if isinstance(e, ValueError) else f"Couldn't reach Bandcamp ({type(e).__name__})."
        if db.kv_get("collection_complete") != "1" and have:
            reason += (f" {have:,}" + (f" of {total:,}" if total else "") + " purchases are saved so far; "
                       "the next read carries on from there.")
        collection_state["error"] = reason
    finally:
        pace._state["foreground"] -= 1
        collection_state.update(syncing=False, progress="")
    if DEEP_AUTO:
        deep_read_in_background()


async def deep_read():
    """Open every purchased release that hasn't been read yet and save its whole track list.

    Bandcamp's collection only names one track per purchase, so without this most tracks you own
    can only be found by searching. It runs once, in the background, a page every few seconds;
    it waits while a lookup is running, survives restarts (each page is saved as it is read),
    and afterwards only new purchases need reading.
    """
    if deep_state["running"] or collection_state["syncing"]:
        return
    deep_state.update(running=True, note="")
    try:
        todo = [r["url"] for r in db.query(
            "SELECT url, MIN(rowid) AS first FROM collection_items WHERE url NOT IN "
            "(SELECT key FROM web_cache WHERE kind='release') GROUP BY url ORDER BY first")]
        for r in db.query("SELECT w.key, w.data FROM web_cache w WHERE w.kind='release' AND w.key IN "
                          "(SELECT url FROM collection_items)"):
            try:                                     # pages worth a second look (see needs_rereading)
                if needs_rereading(json.loads(r["data"]), r["key"]):
                    todo.append(r["key"])
            except ValueError:
                pass
        if not todo:
            return
        async with refresh.make_client() as client:
            net = Net(client)
            for url in todo:
                while any(not job["finished"] for job in jobs.values()):
                    deep_state["note"] = "waiting for the lookup to finish"
                    await asyncio.sleep(2)                # lookups go first
                deep_state["note"] = ""
                try:
                    await get_release(net, url)
                except Blocked:                           # still refused after every retry: rest a while
                    deep_state["note"] = "Bandcamp asked for a long pause; carrying on in 15 minutes"
                    await asyncio.sleep(900)
                except Exception:
                    pass                                  # left unread; picked up on the next run
                if DEEP_EXTRA:
                    await asyncio.sleep(DEEP_EXTRA)
    finally:
        deep_state.update(running=False, note="")


def deep_read_in_background():
    if deep_state["running"]:
        return
    task = asyncio.create_task(deep_read())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def deep_read_forever():
    """Started with the app: finishes the read after a restart, and picks up new purchases."""
    await asyncio.sleep(30)
    while True:
        try:
            if collection_fan() and not collection_state["syncing"]:
                await deep_read()
        except Exception:
            pass
        await asyncio.sleep(1800)


def sync_in_background(full=False):
    task = asyncio.create_task(sync_collection(full))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def collection_is_stale():
    at = db.kv_get("collection_synced_at")
    if not at or db.kv_get("collection_complete") != "1":     # never read, or the first read isn't finished
        return True
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return age > timedelta(hours=COLLECTION_MAX_AGE_HOURS)


class Library:
    """Tracks already known without searching: the feed, and your purchases."""

    def __init__(self):
        self.feed, self.owned_tracks, self.owned_items = {}, {}, []
        for r in db.query(
                "SELECT t.title AS name, COALESCE(NULLIF(t.artist,''), NULLIF(p.author,''), ar.name) AS artist,"
                " p.url, COALESCE(NULLIF(p.release,''), p.title) AS release, p.published_at "
                "FROM tracks t JOIN posts p ON p.id=t.post_id JOIN accounts a ON a.id=p.account_id "
                "JOIN artists ar ON ar.id=a.artist_id WHERE a.source='bandcamp'"):
            r["artists"] = [r["artist"]]
            self._add(self.feed, r["name"], r)
        self.owned_items = db.query("SELECT key, title, band, url, kind FROM collection_items ORDER BY rowid")
        self.owned_urls = {i["url"] for i in self.owned_items}
        self._by_artist = {}
        by_key = {i["key"]: i for i in self.owned_items}
        self.by_name = {}                                # normalised credited name -> purchases
        for item in self.owned_items:
            credit = item["title"].partition(" - ")[0] if " - " in item["title"] else ""
            for name in dict.fromkeys(artist_parts(item["band"]) + artist_parts(credit)):
                self.by_name.setdefault(name, []).append(item)
            # title and address as plain words, for artists only named there
            item["around"] = " " + norm(item["title"]) + " " + norm(slug_words(item["url"])) + " "
        for t in db.query("SELECT item_key, title, artist FROM collection_tracks"):
            item = by_key.get(t["item_key"])
            if item:
                self._add(self.owned_tracks, t["title"], {
                    "name": t["title"], "artists": [t["artist"], item["band"]], "item": item,
                    "context": [item["title"], slug_words(item["url"])]})

    @staticmethod
    def _add(index, title, entry):
        index.setdefault(norm(title), []).append(entry)
        rest = title.partition(" - ")[2]
        if rest:                                         # "Artist - Title" is also findable by its title
            index.setdefault(norm(rest), []).append(entry)

    def owns(self, rel):
        """Is this saved page (or the album it belongs to) one of your purchases?"""
        return any(rel.get(k) in self.owned_urls for k in ("asked", "url", "release_url") if rel.get(k))

    def owned_for(self, artist, title):
        """Purchases credited to the artist, or to whoever made this version of the track."""
        out = {i["key"]: i for i in self.owned_by(artist)}
        for credit in remix_credits(title):
            for i in self.owned_by(credit):
                out.setdefault(i["key"], i)
        return list(out.values())

    def owned_by(self, artist):
        """Purchases credited to this artist (or titled "Artist - ...")."""
        if artist in self._by_artist:
            return self._by_artist[artist]
        wanted = artist_parts(artist)
        out = {}
        for name, items in self.by_name.items():
            if any(name_sim(w, name) >= 0.85 for w in wanted):
                for item in items:
                    out.setdefault(item["key"], item)
        named = [w for w in wanted if len(w) >= 2]
        for item in self.owned_items:                    # "DNO021 - Kercha - Open The Door LP" by DNO Records
            if item["key"] not in out and any(f" {w} " in item["around"] for w in named):
                out[item["key"]] = item
        self._by_artist[artist] = list(out.values())
        return self._by_artist[artist]


async def owned_step(net, lib, artist, title, steps):
    """Look in your purchases. Returns (result or None, a weaker "check" result or None)."""
    if not lib.owned_items:
        return None, None
    check = None
    # a. track lists that came with the collection
    cands = lib.owned_tracks.get(norm(title), [])
    verdict, cand = best_candidate(artist, title, cands)
    if cand:
        rel = await _safe_release(net, cand["item"]["url"])
        out = {"status": verdict, "site": "Bandcamp", "url": cand["item"]["url"], "release": cand["item"]["title"],
               "year": (rel or {}).get("year", ""), "note": "In your purchases",
               "found": f'{cand["item"]["band"]} — {cand["name"]}'}
        steps.append(f'purchases: track "{cand["name"]}" on "{cand["item"]["title"]}"'
                     + ("" if verdict == "bandcamp" else " (artist only partly matches)"))
        if verdict == "bandcamp":
            return out, None
        check = out
    elif cands and distinctive(title):
        # Same title, artist not recognisable from the purchase list: open the release to check.
        for cand in cands[:3]:
            rel = await _safe_release(net, cand["item"]["url"])
            v, name = match_in_release(artist, title, rel) if rel else ("", "")
            if v == "bandcamp":
                steps.append(f'purchases: "{name}" on "{rel["title"]}", artist confirmed on its page')
                return release_result(rel, v, f'{rel["artist"]} — {name}', "In your purchases"), None
    # b. releases you own by this artist: a purchase named after the track, or open them and read the track lists
    owned = lib.owned_for(artist, title)
    for item in owned:
        if ratio(norm(title), norm(item["title"])) >= 0.88 or ratio(norm(title), norm(item["title"].partition(" - ")[2])) >= 0.88:
            rel = await _safe_release(net, item["url"])
            steps.append(f'purchases: you own "{item["title"]}" by {item["band"]}')
            return {"status": "bandcamp", "site": "Bandcamp", "url": item["url"], "release": item["title"],
                    "year": (rel or {}).get("year", ""), "found": f'{item["band"]} — {item["title"]}',
                    "note": "In your purchases"}, None
    opened = 0
    for item in owned[:OWNED_RELEASES]:
        rel = await _safe_release(net, item["url"])
        opened += 1
        v, name = match_in_release(artist, title, rel) if rel else ("", "")
        if v:
            steps.append(f'purchases: opened {opened} release(s) by this artist, found "{name}" on "{rel["title"]}"')
            out = release_result(rel, v, f'{rel["artist"] or item["band"]} — {name}', "In your purchases")
            if v == "bandcamp":
                return out, None
            check = check or out
    steps.append(f"purchases: no match ({len(cands)} same-title track(s), {len(owned)} release(s) by this artist"
                 + (f", {opened} opened" if opened else "") + ")")
    return None, check


async def _safe_release(net, url):
    try:
        return await get_release(net, url)
    except Blocked:
        raise
    except Exception:
        return None


# ---------- Bandcamp: the artist's own page ----------

async def artist_pages(net, lib, artist, steps, search_ok):
    """Bandcamp pages that belong to the artist(s): [(root url, page name)]."""
    saved = cache_get("artistpage", norm(artist), 30)
    if saved is not None:
        return [tuple(x) for x in saved]
    pages, parts = {}, artist_parts(artist)
    names = parts[1:3] if len(parts) > 1 else parts[:1]
    for part in names:
        if search_ok:
            for c in await bandcamp_search(net, part, "b"):
                if c["type"] in ("artist", "label", "") and name_sim(part, norm(c["name"])) >= 0.85:
                    pages.setdefault(root_of(c["url"]), c["name"])
        # An obvious address that search didn't offer; kept only if the page's own name matches.
        guess = f"https://{part.replace(' ', '')}.bandcamp.com"
        if guess not in pages and re.fullmatch(r"[a-z0-9]{2,}", part.replace(" ", "")):
            try:
                grid = await get_grid(net, guess)
            except Blocked:
                raise
            except Exception:
                grid = None
            if grid and name_sim(part, norm(grid["name"])) >= 0.85:
                pages.setdefault(guess, grid["name"])
    out = list(pages.items())[:4]
    steps.append("artist page: " + (", ".join(u.replace("https://", "") for u, _ in out) if out else "none found"))
    if search_ok:
        cache_put("artistpage", norm(artist), out)
    return out


async def scan_step(net, artist, title, pages, steps):
    """Open the releases on the artist's page(s) and look for the title. Returns a result or None."""
    budget, check = SCAN_RELEASES, None
    for base, _name in pages:
        grid = await get_grid(net, base)
        items = (grid or {}).get("items") or []
        # A release named after the track is opened first; then everything else, in page order.
        items = sorted(items, key=lambda i: -max(ratio(norm(title), norm(i["title"])),
                                                 ratio(norm(title), norm(i["title"].partition(" - ")[2]))))
        opened = 0
        for item in items:
            was_saved = cache_get("release", item["url"], 180) is not None
            if not was_saved:
                if budget <= 0:
                    break
                budget -= 1
            rel = await _safe_release(net, item["url"])
            opened += 1
            v, name = match_in_release(artist, title, rel) if rel else ("", "")
            if v:
                steps.append(f'{base.replace("https://", "")}: found "{name}" on "{rel["title"]}" ({opened} of {len(items)} releases read)')
                out = release_result(rel, v, f'{rel["artist"]} — {name}', "Found on the artist's page")
                if v == "bandcamp":
                    return out
                check = check or out
        steps.append(f'{base.replace("https://", "")}: title not on any of {opened} release(s) read'
                     + (f" ({len(items) - opened} not read)" if opened < len(items) else ""))
    return check


# ---------- SoundCloud ----------

def _is_profile_of(user, names) -> tuple:
    """(tier, similarity): how well a SoundCloud user fits the wanted names. names[0] is the whole
    credit ("nightmare oni"); later ones are the individual people in it. Matching the whole credit
    (tier 2) beats matching one person in it (tier 1): "Nightmare & Oni" is one act with one profile."""
    shown = norm(user.get("username") or "")
    address = plain(user.get("permalink") or "").replace(" ", "")
    best = (0, 0.0)
    for n, name in enumerate(names):
        squashed = name.replace(" ", "")
        sim = max(name_sim(name, shown), ratio(squashed, address),
                  ratio(squashed, address.replace("and", "")) if "and" in address else 0.0)
        if sim >= 0.85:
            best = max(best, (2 if n == 0 else 1, round(sim, 2)))
    return best


async def from_soundcloud(net, artist, steps, title="", credits=()):
    """The SoundCloud profile to link when the track itself wasn't found.

    credits: for an edit/remix/bootleg, who made it, as written ("River", then "River Dub") - the
    profile wanted is then theirs, not the original artist's.

    Names alone are a weak guide (there are many profiles called "River"), so the track is looked
    for on SoundCloud first: whoever uploaded it, if their name fits, is the right profile.
    """
    forms = list(credits) or [artist]
    name_sets = [artist_parts(f) for f in forms if artist_parts(f)]
    if not name_sets:
        return None
    every = [n for names in name_sets for n in names]
    whose = "Remixer's profile" if credits else ""
    for acc in db.query("SELECT a.url, a.display_name, ar.name FROM accounts a JOIN artists ar "
                        "ON ar.id=a.artist_id WHERE a.source='soundcloud'"):
        if {norm(acc["name"]), norm(acc["display_name"] or "")} & set(every):
            return {"status": "soundcloud", "site": "SoundCloud", "url": acc["url"],
                    "found": acc["display_name"] or acc["name"], "note": "A page you track"}
    try:
        # 1. the track itself on SoundCloud: its uploader
        if title:
            queries = [f"{artist} {title}".strip()]
            if credits:
                queries.append(f"{credits[0]} {base_title(title)}")
            best = None
            for text in dict.fromkeys(queries):
                data = await SOURCES["soundcloud"]._api(net.client, "/search/tracks", {"q": text, "limit": 20})
                for tr in data.get("collection") or []:
                    user, name = tr.get("user") or {}, tr.get("title") or ""
                    if not user.get("permalink_url") or not tr.get("permalink_url"):
                        continue
                    same = max(ratio(norm(title), norm(name)), ratio(norm(title), norm(name.partition(" - ")[2])))
                    if same < 0.85:
                        continue
                    if credits:      # must be this version: its maker named in the title or as the uploader
                        told = f" {plain(name)} {plain(user.get('username') or '')} "
                        if not any(f" {m} " in told for m in remixer_names(title)):
                            continue
                    fit = max(_is_profile_of(user, names) for names in name_sets)
                    if fit[0] and (best is None or (fit, same) > best[0]):
                        best = ((fit, same), user, name, tr)
                if best:
                    break
            if best:                                     # the track itself: link straight to it
                user, tr = best[1], best[3]
                steps.append(f'SoundCloud: "{best[2]}" was uploaded by {user.get("username")}')
                free = bool(tr.get("downloadable")) or "free" in (tr.get("purchase_title") or "").lower()
                when = tr.get("release_date") or tr.get("display_date") or tr.get("created_at") or ""
                return {"status": "soundcloud_track", "site": "SoundCloud", "url": tr["permalink_url"],
                        "found": f'{user.get("username") or ""} \u2014 {best[2]}', "release": "",
                        "year": when[:4] if re.fullmatch(r"(19|20)\d{2}", when[:4]) else "",
                        "note": ("Free download on " if free else "On ")
                                + ("the remixer's SoundCloud" if credits else "the artist's SoundCloud")}
            steps.append("SoundCloud: the track isn't there under a matching profile")
        # 2. a profile with the right name; the whole credit before the people in it
        best = None
        for form, names in zip(forms, name_sets):
            for text in dict.fromkeys([form] + [n for n in names[1:3]]):
                data = await SOURCES["soundcloud"]._api(net.client, "/search/users", {"q": text, "limit": 20})
                for u in data.get("collection") or []:
                    fit = _is_profile_of(u, names)
                    if fit[0] and u.get("permalink_url"):
                        key = (fit, u.get("followers_count") or 0)
                        if best is None or key > best[0]:
                            best = (key, u)
                if best and best[0][0][0] == 2:
                    break                                # the whole credit matched: no need to try parts
            if best:
                break
        if best:
            steps.append(f'SoundCloud: profile "{best[1].get("username")}", matched by name only')
            return {"status": "soundcloud", "site": "SoundCloud", "url": best[1]["permalink_url"],
                    "found": best[1].get("username") or "",
                    "note": (whose + ", matched by name only") if whose else "Matched by name only"}
        steps.append("SoundCloud: no profile with that name")
    except Exception as e:
        steps.append(f"SoundCloud: search failed ({type(e).__name__})")
    return {"status": "soundcloud_search", "site": "SoundCloud", "found": "",
            "url": "https://soundcloud.com/search/people?q=" + quote(forms[0]),
            "note": "No matching profile; this opens a SoundCloud search"}


# ---------- running a lookup ----------

def _cache_key(artist, title):
    return f"{CACHE_VERSION}|{norm(artist)}|{norm(title)}"


def _manual_key(artist, title):
    # No version in this key: a link you set yourself outlives changes to the lookup.
    return f"manual|{norm(artist)}|{norm(title)}"


def site_of(url):
    host = (urlparse(url).netloc or "").lower()
    return "Bandcamp" if "bandcamp" in host else "SoundCloud" if "soundcloud" in host else host.removeprefix("www.")


async def set_manual(artist, title, url, year="", release=""):
    """Save your own link for a track. It is used from then on, whatever a search would find."""
    result = {"status": "manual", "manual": True, "site": site_of(url), "url": url, "year": year,
              "release": release, "found": "", "note": "", "steps": ["link set by you"]}
    if "bandcamp" in url and not (year and release):
        try:        # fill in the year and release name from the page, if that is quick
            async with refresh.make_client() as client:
                rel = await asyncio.wait_for(get_release(Net(client), url), timeout=10)
            if rel:
                result["year"] = year or rel.get("year") or ""
                result["release"] = release or rel.get("release") or rel.get("title") or ""
        except Exception:
            pass
    _remember(_manual_key(artist, title), result)
    return result


def clear_manual(artist, title):
    db.execute("DELETE FROM lookup_cache WHERE key=?", (_manual_key(artist, title),))


def apply_to_jobs(artist, title, result):
    """Update rows for this track in lookups still on screen, so the table and export agree."""
    key = (norm(artist), norm(title))
    for job in jobs.values():
        for row in job["rows"]:
            if (norm(row["artist"]), norm(row["title"])) == key and row["status"] != "pending":
                row.update({"site": "", "url": "", "year": "", "release": "", "found": "", "note": "", "steps": []})
                row.update(result)


def _cached(key):
    rows = db.query("SELECT result FROM lookup_cache WHERE key=?", (key,))
    return json.loads(rows[0]["result"]) if rows else None


def _remember(key, result):
    result = dict(result, engine=ENGINE)
    db.execute("INSERT INTO lookup_cache(key, result, at) VALUES(?,?,?) "
               "ON CONFLICT(key) DO UPDATE SET result=excluded.result, at=excluded.at",
               (key, json.dumps(result), db.now()))


async def find(net, lib, artist, title, state):
    """All the steps for one track. Returns (result, keep) - keep is False when a site refused
    requests part-way, so the answer may be incomplete and should not be remembered."""
    steps, keep, check, pages, maker_pages = [], True, None, [], []

    def done(result):
        result["steps"] = steps
        return result, keep

    # 1. the feed
    verdict, hit = best_candidate(artist, title, lib.feed.get(norm(title), []))
    if verdict == "bandcamp":
        steps.append("feed: already collected from a page you track")
        return done({"status": "bandcamp", "site": "Bandcamp", "url": hit["url"], "release": hit["release"],
                     "year": (hit["published_at"] or "")[:4], "note": "In your feed",
                     "found": f"{hit['artist']} — {hit['name']}"})
    # 1b. any release page read during an earlier lookup (all of its tracks were saved, not just the one wanted)
    #     A release you own is preferred over another release that happens to carry the same track.
    known = page_index().get(norm(title), [])
    mine = [c for c in known if lib.owns(c["rel"])]
    verdict, hit = best_candidate(artist, title, mine)
    if verdict != "bandcamp":
        verdict, hit = best_candidate(artist, title, known)
    earlier = None
    if hit:
        rel = hit["rel"]
        owned = lib.owns(rel)
        steps.append(f'pages read earlier: "{hit["name"]}" on "{rel.get("title")}"'
                     + ("" if verdict == "bandcamp" else " (artist only partly matches)"))
        earlier = release_result(rel, verdict, f'{rel.get("artist") or artist} \u2014 {hit["name"]}',
                                 "In your purchases" if owned else "On a page read earlier")
        if verdict == "bandcamp" and (owned or not lib.owned_for(artist, title)):
            return done(earlier)
    if state["bandcamp_ok"] and title:
        try:
            # 2. your purchases
            result, check = await owned_step(net, lib, artist, title, steps)
            if result:
                return done(result)
            if earlier and earlier["status"] == "bandcamp":
                return done(earlier)                   # not among your purchases: the page read earlier stands
            check = check or earlier
            # 3. Bandcamp search
            try:
                result = await search_step(net, artist, title, steps)
            except Blocked as e:
                state["search_ok"] = False
                steps.append(f"search unavailable: {e}")
                result = None
            if result and result["status"] == "bandcamp":
                return done(result)
            check = check or result
            # 4. the artist's own page
            for credit in remix_credits(title):       # an edit, remix or bootleg: its maker's page first
                maker_pages = await artist_pages(net, lib, credit, steps, state["search_ok"])
                if maker_pages:
                    break
            pages = maker_pages + [p for p in await artist_pages(net, lib, artist, steps, state["search_ok"])
                                   if p not in maker_pages]
            result = await scan_step(net, artist, title, pages, steps) if pages else None
            if result and result["status"] == "bandcamp":
                return done(result)
            check = check or result
        except Blocked as e:
            state["bandcamp_ok"], keep = False, False
            steps.append(f"stopped asking Bandcamp: {e}")
            state["warning"] = (f"{e}, even after waiting. Rows after that were only checked against your "
                                "feed, your saved purchases and SoundCloud; run the lookup again later.")
        if not state["search_ok"]:
            keep = False
    elif title:
        keep = False
        verdict, cand = best_candidate(artist, title, lib.owned_tracks.get(norm(title), []))
        if cand:
            check = {"status": verdict, "site": "Bandcamp", "url": cand["item"]["url"], "year": "",
                     "release": cand["item"]["title"], "note": "In your purchases",
                     "found": f'{cand["item"]["band"]} — {cand["name"]}'}
    # 5. fallbacks
    check = check or earlier
    if check:
        return done(check)
    # Both possible artist links are worked out and kept, so the preference can change later
    # without searching again.
    options = {"track": None, "bandcamp": None, "soundcloud": None, "search": None}

    def page_option(page, whose):
        return {"status": "bandcamp_artist", "site": "Bandcamp", "url": page[0], "found": page[1],
                "release": "", "year": "", "note": f"{whose} page; the track isn't on any release there now"}

    # An edit, remix, bootleg or flip belongs to whoever made it: link to them, not the original artist.
    credits = remix_credits(title)
    if credits:
        if maker_pages:
            options["bandcamp"] = page_option(maker_pages[0], "Remixer's")
        sc = await from_soundcloud(net, artist, steps, title, credits)
        if sc and sc["status"] in ("soundcloud", "soundcloud_track"):
            options["track" if sc["status"] == "soundcloud_track" else "soundcloud"] = sc
        if not (options["track"] or options["bandcamp"] or options["soundcloud"]):
            steps.append(f'no page found for "{credits[0]}"; falling back to the artist')
    if not (options["track"] or options["bandcamp"] or options["soundcloud"]):
        artist_only = [p for p in pages if p not in maker_pages]
        if artist_only:
            options["bandcamp"] = page_option(artist_only[0], "Artist's")
        sc = await from_soundcloud(net, artist, steps, title)
        if sc:
            options[{"soundcloud_track": "track", "soundcloud": "soundcloud"}.get(sc["status"], "search")] = sc
    return done(dict(pick_fallback(options), options=options))


FALLBACK_STATUSES = ("soundcloud_track", "bandcamp_artist", "soundcloud", "soundcloud_search", "none")


def fallback_pref() -> str:
    """Which artist link to give when the track itself isn't found: "bandcamp" or "soundcloud"."""
    value = (db.kv_get("lookup_fallback") or os.environ.get("LOOKUP_FALLBACK") or "soundcloud").strip().lower()
    return value if value in ("bandcamp", "soundcloud") else "soundcloud"


def pick_fallback(options: dict) -> dict:
    """The track on SoundCloud if it was found there; else the preferred artist link, else the
    other one, else a SoundCloud search."""
    order = ("soundcloud", "bandcamp") if fallback_pref() == "soundcloud" else ("bandcamp", "soundcloud")
    for key in ("track",) + order + ("search",):      # the track itself beats any artist link
        if options.get(key):
            return dict(options[key])
    return {"status": "none", "note": "No artist name to search for"}


def with_current_fallback(saved: dict):
    """A saved not-found answer, re-pointed to the currently preferred artist link.
    Returns None when it was saved before both links were kept and needs searching again."""
    if saved.get("status") not in FALLBACK_STATUSES:
        return saved
    if saved.get("options"):
        out = dict(saved)
        out.update({k: "" for k in ("site", "url", "found", "note", "release", "year")})
        out.update(pick_fallback(saved["options"]))
        return out
    if fallback_pref() == "soundcloud" and saved.get("status") == "bandcamp_artist":
        return None                    # no SoundCloud link was looked for at the time
    return saved


def set_fallback(value: str):
    db.kv_set("lookup_fallback", value)
    for job in jobs.values():          # lookups still on screen follow the new choice
        for row in job["rows"]:
            if row.get("options") and row.get("status") in FALLBACK_STATUSES:
                row.update({k: "" for k in ("site", "url", "found", "note", "release", "year")})
                row.update(pick_fallback(row["options"]))


def find_local(lib, artist, title):
    """Answer from what is already saved - feed, purchases, pages read earlier - with no request
    to any site. Returns a result, or None when the track needs searching for."""
    def owned_result(item, name, how):
        rel = cache_get("release", item["url"], 3650) or {}
        return {"status": "bandcamp", "site": "Bandcamp", "url": rel.get("release_url") or item["url"],
                "release": rel.get("release") or item["title"],
                "year": rel.get("year", ""), "note": "In your purchases", "steps": [how],
                "found": f'{item["band"]} \u2014 {name}'}

    verdict, hit = best_candidate(artist, title, lib.feed.get(norm(title), []))
    if verdict == "bandcamp":
        return {"status": "bandcamp", "site": "Bandcamp", "url": hit["url"], "release": hit["release"],
                "year": (hit["published_at"] or "")[:4], "note": "In your feed",
                "found": f"{hit['artist']} \u2014 {hit['name']}",
                "steps": ["feed: already collected from a page you track"]}
    if not title:
        return None
    verdict, cand = best_candidate(artist, title, lib.owned_tracks.get(norm(title), []))
    if verdict == "bandcamp":
        return owned_result(cand["item"], cand["name"],
                            f'purchases: track "{cand["name"]}" on "{cand["item"]["title"]}"')
    owned = lib.owned_for(artist, title)
    for item in owned:
        if max(ratio(norm(title), norm(item["title"])), ratio(norm(title), norm(item["title"].partition(" - ")[2]))) >= 0.88:
            return owned_result(item, item["title"], f'purchases: you own "{item["title"]}" by {item["band"]}')
    known = page_index().get(norm(title), [])
    verdict, hit = best_candidate(artist, title, [c for c in known if lib.owns(c["rel"])])
    mine = verdict == "bandcamp"
    if not mine:
        verdict, hit = best_candidate(artist, title, known)
    if verdict == "bandcamp" and (mine or not owned):
        rel = hit["rel"]
        out = release_result(rel, verdict, f'{rel.get("artist") or artist} \u2014 {hit["name"]}',
                             "In your purchases" if mine else "On a page read earlier")
        out["steps"] = [f'pages read earlier: "{hit["name"]}" on "{rel.get("title")}"']
        return out
    return None


def _fill(row, result):
    row.update({"site": "", "url": "", "year": "", "release": "", "found": "", "note": "", "steps": [],
                "options": None})
    row.update(result)


async def _run(job, fresh):
    """Three passes, quickest first, so everything already known shows up straight away:
       1. saved answers, your feed, your purchases and pages read earlier - no requests at all
       2. release years for those answers - one request each, only where the page isn't saved
       3. searching Bandcamp and artist pages for whatever is left - the slow part"""
    state = {"bandcamp_ok": True, "search_ok": True, "warning": ""}
    todo = []

    def quick_pass(rows, lib):
        left = []
        for row in rows:
            manual = _cached(_manual_key(row["artist"], row["title"]))   # your own link always wins
            saved = manual or (None if fresh else _cached(_cache_key(row["artist"], row["title"])))
            if saved and not manual and saved.get("status") != "bandcamp" and saved.get("engine", 0) < ENGINE:
                saved = None                               # a miss from an older version: try again
            if saved and not manual:
                saved = with_current_fallback(saved)
            if saved and not manual:
                saved = dict(saved, steps=(saved.get("steps") or []) + ["(saved result from an earlier lookup)"])
            result = saved or find_local(lib, row["artist"], row["title"])
            if result:
                _fill(row, result)
                job["done"] += 1
            else:
                left.append(row)
        return left

    job["phase"] = "Checking what is already saved\u2026"
    await asyncio.sleep(0)
    lib = Library()
    todo = quick_pass(job["rows"], lib)

    if collection_fan() and collection_is_stale():
        job["phase"] = "Reading your Bandcamp purchases\u2026"
        before = len(lib.owned_items)
        await sync_collection(on_progress=lambda text: job.update(phase=text))
        if collection_state["error"]:
            job["warning"] = collection_state["error"] + " Carrying on with the purchases already saved."
        lib = Library()
        if len(lib.owned_items) != before:                 # new purchases: check the rest against them
            todo = quick_pass(todo, lib)

    async with refresh.make_client() as client:
        net = Net(client)
        # 2. years for answers found in your purchases whose page hasn't been read yet
        need_year = [r for r in job["rows"] if r["status"] == "bandcamp" and not r["year"]
                     and r["site"] == "Bandcamp" and r["note"] == "In your purchases"]
        for n, row in enumerate(need_year, 1):
            if not state["bandcamp_ok"]:
                break
            job["phase"] = f"Found {job['done']} of {job['total']}; reading release years ({n} of {len(need_year)})"
            try:
                rel = await get_release(net, row["url"])
                if rel and rel.get("year"):
                    row["year"] = rel["year"]
            except Blocked as e:
                state["bandcamp_ok"] = False
                job["warning"] = f"{e}, even after waiting. Run the lookup again later for the rest."
            except Exception:
                pass
        # 3. everything not answered yet
        for n, row in enumerate(todo, 1):
            job["phase"] = (f"Searching Bandcamp for \u201c{row['title']}\u201d by {row['artist']} "
                            f"({n} of {len(todo)} not already saved)")
            try:
                result, keep = await find(net, lib, row["artist"], row["title"], state)
            except Exception as e:
                result, keep = {"status": "error", "steps": [f"{type(e).__name__}: {e}"[:200]], "note":
                                f"Couldn't complete the search ({type(e).__name__}); run it again to retry"}, False
            if keep and result.get("note") != "In your feed":
                _remember(_cache_key(row["artist"], row["title"]), result)
            _fill(row, result)
            job["done"] += 1
            if state["warning"]:
                job["warning"] = state["warning"]
    job["phase"] = ""
    job["finished"] = True


def start(tracks, fresh=False):
    if len(jobs) > 20:                       # keep only recent lookups in memory
        for old in list(jobs)[:-20]:
            jobs.pop(old, None)
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "total": len(tracks), "done": 0, "finished": False, "warning": "", "phase": "",
           "rows": [dict(t, status="pending", site="", url="", year="", release="", found="", note="", steps=[])
                    for t in tracks]}
    jobs[job_id] = job

    async def guarded():
        try:
            with pace.foreground():
                await _run(job, fresh)
        except Exception as e:
            job["warning"] = f"The lookup stopped early: {type(e).__name__}: {e}"
            job["finished"] = True
    task = asyncio.create_task(guarded())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job


STATUS_TEXT = {"manual": "Link you set", "bandcamp": "Bandcamp release", "bandcamp_check": "Bandcamp, check match",
               "bandcamp_artist": "Bandcamp artist page", "soundcloud_track": "SoundCloud track",
               "soundcloud": "SoundCloud artist page", "soundcloud_search": "SoundCloud search",
               "none": "Not found", "error": "Error", "pending": "Not looked up yet"}


def to_csv(job) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["#", "artist", "title", "link", "site", "release", "year", "result", "found as",
                     "note", "steps"])
    for r in job["rows"]:
        writer.writerow([r["n"], r["artist"], r["title"], r["url"], r["site"], r["release"], r["year"],
                         STATUS_TEXT.get(r["status"], r["status"]), r["found"], r["note"],
                         " | ".join(r.get("steps") or [])])
    return out.getvalue().encode("utf-8-sig")
