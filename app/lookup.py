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

jobs = {}          # id -> {"rows": [...], "done": int, "total": int, "finished": bool, ...}
_tasks = set()
collection_state = {"syncing": False, "error": "", "progress": ""}


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

def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = re.sub(r"[\(\[][^\)\]]*(mix|edit|version|remaster|bonus|free|dub)[^\)\]]*[\)\]]", " ", text)
    text = re.sub(r"\b(feat|ft|featuring)\b.*$", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


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


def score(artist: str, title: str, cand: dict) -> tuple:
    """(title score, artist score) for a candidate {"name", "artists": [names...]}."""
    want_title, wanted = norm(title), artist_parts(artist)
    name = cand.get("name") or ""
    prefix, _, rest = name.partition(" - ")            # label pages often title tracks "Artist - Title"
    t = max(ratio(want_title, norm(name)), ratio(want_title, norm(rest)) if rest else 0.0)
    have = [p for n in cand.get("artists") or [] for p in artist_parts(n or "")]
    if rest:
        have += artist_parts(prefix)
    a = max((name_sim(w, h) for w in wanted for h in have), default=0.0)
    if not wanted:
        a = 0.6                                        # no artist given: the title alone is a weak match
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


async def get_release(net, url):
    """A release or track page, reduced to what matching needs. Remembered for six months."""
    saved = cache_get("release", url, 180)
    if saved is not None:
        return saved or None
    r = await net.get(url)
    if r.status_code == 429:
        raise Blocked("Bandcamp is rate-limiting requests")
    if r.status_code != 200:
        if r.status_code == 404:
            cache_put("release", url, {})
        return None
    rel = parse_release(r.text, url)
    data = {"url": rel["url"], "title": rel["title"] or "", "artist": rel["author"] or "",
            "year": (rel["published_at"] or "")[:4], "release": rel["release"] or "",
            "release_url": rel["release_url"] or "", "label": rel["label"] or "",
            "tracks": [{"title": t.title, "artist": t.artist or ""} for t in rel["tracks"] or []]}
    cache_put("release", url, data)
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
    cands = [{"name": t["title"], "artists": [t.get("artist")] + extra} for t in rel.get("tracks") or []]
    if not cands and rel.get("title"):              # no track list on the page: go by its title
        cands = [{"name": rel["title"], "artists": extra}]
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
    if len(parts) > 1:
        queries += [("t", f"{p} {title}") for p in parts[1:3]]       # each named artist on their own
    if len(norm(title)) >= 4:
        queries.append(("t", title))                                  # title alone; artist must still match
    queries.append(("a", f"{artist} {title}".strip()))                # a release named after the track
    if distinctive(title):
        queries.append(("a", title))
    seen, check = set(), None
    for kind, text in dict.fromkeys(queries):
        found = [c for c in await bandcamp_search(net, text, kind) if c["url"] not in seen]
        seen.update(c["url"] for c in found)
        want = "track" if kind == "t" else "album"
        cands = [dict(c, artists=[c.get("artist")]) for c in found if c["type"] in (want, "")]
        verdict, cand = best_candidate(artist, title, cands)
        steps.append(f'search {want}s "{text}": {len(cands)} result(s)'
                     + (f', {"match" if verdict == "bandcamp" else "possible"}: {cand["artist"]} — {cand["name"]}' if cand else ""))
        if not cand:
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
                      " (SELECT COUNT(*) FROM collection_tracks) AS tracks")[0]
    return {"fan": collection_fan(), "items": counts["items"], "tracks": counts["tracks"],
            "synced_at": db.kv_get("collection_synced_at"),
            "complete": db.kv_get("collection_complete") == "1", **collection_state}


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
            while token and (not complete or new):
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
        collection_state.update(syncing=False, progress="")


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
        by_key = {i["key"]: i for i in self.owned_items}
        self.by_name = {}                                # normalised credited name -> purchases
        for item in self.owned_items:
            credit = item["title"].partition(" - ")[0] if " - " in item["title"] else ""
            for name in dict.fromkeys(artist_parts(item["band"]) + artist_parts(credit)):
                self.by_name.setdefault(name, []).append(item)
        for t in db.query("SELECT item_key, title, artist FROM collection_tracks"):
            item = by_key.get(t["item_key"])
            if item:
                self._add(self.owned_tracks, t["title"], {
                    "name": t["title"], "artists": [t["artist"], item["band"]], "item": item})

    @staticmethod
    def _add(index, title, entry):
        index.setdefault(norm(title), []).append(entry)
        rest = title.partition(" - ")[2]
        if rest:                                         # "Artist - Title" is also findable by its title
            index.setdefault(norm(rest), []).append(entry)

    def owned_by(self, artist):
        """Purchases credited to this artist (or titled "Artist - ...")."""
        wanted = artist_parts(artist)
        out = {}
        for name, items in self.by_name.items():
            if any(name_sim(w, name) >= 0.85 for w in wanted):
                for item in items:
                    out.setdefault(item["key"], item)
        return list(out.values())


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
    owned = lib.owned_by(artist)
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

async def from_soundcloud(net, artist, steps):
    wanted = artist_parts(artist)
    if not wanted:
        return None
    for acc in db.query("SELECT a.url, a.display_name, ar.name FROM accounts a JOIN artists ar "
                        "ON ar.id=a.artist_id WHERE a.source='soundcloud'"):
        if {norm(acc["name"]), norm(acc["display_name"] or "")} & set(wanted):
            return {"status": "soundcloud", "site": "SoundCloud", "url": acc["url"],
                    "found": acc["display_name"] or acc["name"], "note": "A page you track"}
    try:
        best = None
        for text in dict.fromkeys([artist] + ([wanted[1]] if len(wanted) > 1 else [])):
            data = await SOURCES["soundcloud"]._api(net.client, "/search/users", {"q": text, "limit": 10})
            for u in data.get("collection") or []:
                s = max(max(name_sim(w, norm(u.get("username") or "")),
                            ratio(w.replace(" ", ""), norm(u.get("permalink") or "").replace(" ", ""))) for w in wanted)
                if s >= 0.85 and u.get("permalink_url"):
                    key = (round(s, 2), u.get("followers_count") or 0)
                    if best is None or key > best[0]:
                        best = (key, u)
            if best:
                break
        if best:
            steps.append(f'SoundCloud: profile "{best[1].get("username")}"')
            return {"status": "soundcloud", "site": "SoundCloud", "url": best[1]["permalink_url"],
                    "found": best[1].get("username") or ""}
        steps.append("SoundCloud: no profile with that name")
    except Exception as e:
        steps.append(f"SoundCloud: search failed ({type(e).__name__})")
    return {"status": "soundcloud_search", "site": "SoundCloud", "found": "",
            "url": "https://soundcloud.com/search/people?q=" + quote(artist),
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
    db.execute("INSERT INTO lookup_cache(key, result, at) VALUES(?,?,?) "
               "ON CONFLICT(key) DO UPDATE SET result=excluded.result, at=excluded.at",
               (key, json.dumps(result), db.now()))


async def find(net, lib, artist, title, state):
    """All the steps for one track. Returns (result, keep) - keep is False when a site refused
    requests part-way, so the answer may be incomplete and should not be remembered."""
    steps, keep, check, pages = [], True, None, []

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
    if state["bandcamp_ok"] and title:
        try:
            # 2. your purchases
            result, check = await owned_step(net, lib, artist, title, steps)
            if result:
                return done(result)
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
            pages = await artist_pages(net, lib, artist, steps, state["search_ok"])
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
    if check:
        return done(check)
    if pages:
        return done({"status": "bandcamp_artist", "site": "Bandcamp", "url": pages[0][0], "found": pages[0][1],
                     "release": "", "year": "",
                     "note": "Artist's page; the track isn't on any release there now"})
    result = await from_soundcloud(net, artist, steps)
    return done(result or {"status": "none", "note": "No artist name to search for"})


async def _run(job, fresh):
    if collection_fan() and collection_is_stale():
        job["phase"] = "Reading your Bandcamp purchases…"
        await sync_collection(on_progress=lambda text: job.update(phase=text))
        if collection_state["error"]:
            job["warning"] = collection_state["error"] + " Continuing without your purchases."
    job["phase"] = ""
    lib = Library()
    state = {"bandcamp_ok": True, "search_ok": True, "warning": ""}
    async with refresh.make_client() as client:
        net = Net(client, on_wait=lambda text: job.update(phase=text))
        for row in job["rows"]:
            key = _cache_key(row["artist"], row["title"])
            manual = _cached(_manual_key(row["artist"], row["title"]))
            result = manual or (None if fresh else _cached(key))
            if manual:
                pass                                   # your own link always wins, even when searching again
            elif result is None:
                try:
                    result, keep = await find(net, lib, row["artist"], row["title"], state)
                except Exception as e:
                    result, keep = {"status": "error", "steps": [f"{type(e).__name__}: {e}"[:200]], "note":
                                    f"Couldn't complete the search ({type(e).__name__}); run it again to retry"}, False
                if keep and result.get("note") != "In your feed":
                    _remember(key, result)
            else:
                result = dict(result, steps=(result.get("steps") or []) + ["(saved result from an earlier lookup)"])
            row.update({"site": "", "url": "", "year": "", "release": "", "found": "", "note": "", "steps": []})
            row.update(result)
            job["done"] += 1
            if state["warning"]:
                job["warning"] = state["warning"]
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
            await _run(job, fresh)
        except Exception as e:
            job["warning"] = f"The lookup stopped early: {type(e).__name__}: {e}"
            job["finished"] = True
    task = asyncio.create_task(guarded())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job


STATUS_TEXT = {"manual": "Link you set", "bandcamp": "Bandcamp release", "bandcamp_check": "Bandcamp, check match",
               "bandcamp_artist": "Bandcamp artist page",
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
