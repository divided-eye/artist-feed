"""Tracklist lookup: for each "artist / title" row, find the Bandcamp release the
track is on; failing that, the artist's SoundCloud page.

Order of attempts per row:
  1. your own feed (tracks already collected from tracked Bandcamp pages) - no network
  2. Bandcamp's public search, then the track's page for the album link and date
  3. SoundCloud: a tracked page for that artist, else SoundCloud's people search
Results are remembered, so looking up the same track again is instant.
"""
import asyncio
import csv
import io
import json
import os
import re
import unicodedata
import uuid
from difflib import SequenceMatcher
from urllib.parse import quote

from . import db, refresh
from .sources import SOURCES
from .sources.bandcamp import parse_release, parse_search

PAUSE = float(os.environ.get("LOOKUP_PAUSE_SECONDS", "1.0"))   # between searches, to be polite
MAX_ROWS = int(os.environ.get("LOOKUP_MAX_ROWS", "500"))
SEARCH_URL = "https://bandcamp.com/search"
# Used only if the search page is unavailable. Not documented by Bandcamp.
SEARCH_API = "https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic"

jobs = {}          # id -> {"rows": [...], "done": int, "total": int, "finished": bool}
_tasks = set()


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
    pieces = re.split(r"\s*(?:,|&|\+|/|\bx\b|\bvs\.?\b|\band\b|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bwith\b)\s*",
                      text or "", flags=re.I)
    return list(dict.fromkeys(p for p in (norm(x) for x in [text] + pieces) if p))


def ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def score(artist: str, title: str, cand: dict) -> tuple:
    """(title score, artist score) of a candidate {name, artist} against the wanted track."""
    want_title, wanted = norm(title), artist_parts(artist)
    name = cand.get("name") or ""
    prefix, _, rest = name.partition(" - ")            # label pages often title tracks "Artist - Title"
    t = max(ratio(want_title, norm(name)), ratio(want_title, norm(rest)) if rest else 0.0)
    have = artist_parts(cand.get("artist") or "") + (artist_parts(prefix) if rest else [])
    a = max((ratio(w, h) for w in wanted for h in have), default=0.0)
    if not wanted:
        a = 0.6                                        # no artist given: title alone is a weak match
    return t, a


def judge(t: float, a: float) -> str:
    if t >= 0.88 and a >= 0.85:
        return "bandcamp"
    if (t >= 0.88 and a >= 0.55) or (t >= 0.75 and a >= 0.85):
        return "bandcamp_check"
    return ""


def best_candidate(artist, title, cands):
    ranked = sorted(((score(artist, title, c), c) for c in cands), key=lambda x: -(x[0][0] + x[0][1]))
    for (t, a), cand in ranked:
        verdict = judge(t, a)
        if verdict:
            return verdict, cand
    return "", None


def local_index():
    """Bandcamp tracks already in the feed, by normalised title."""
    index = {}
    for r in db.query(
            "SELECT t.title AS name, COALESCE(NULLIF(t.artist,''), NULLIF(p.author,''), ar.name) AS artist,"
            " p.url, COALESCE(NULLIF(p.release,''), p.title) AS release, p.published_at "
            "FROM tracks t JOIN posts p ON p.id=t.post_id JOIN accounts a ON a.id=p.account_id "
            "JOIN artists ar ON ar.id=a.artist_id WHERE a.source='bandcamp'"):
        index.setdefault(norm(r["name"]), []).append(r)
    return index


# ---------- the two sites ----------

class Blocked(Exception):
    pass


async def bandcamp_search(client, text: str) -> list:
    r = await client.get(SEARCH_URL, params={"q": text, "item_type": "t"})
    if r.status_code == 200:
        return parse_search(r.text)
    # Search page refused: try the search box's JSON endpoint before giving up.
    try:
        j = await client.post(SEARCH_API, json={"search_text": text, "search_filter": "t",
                                                "full_page": False, "fan_id": None})
        if j.status_code == 200:
            return [{"type": "track", "name": x.get("name"), "artist": x.get("band_name"),
                     "album": x.get("album_name"), "url": x.get("item_url_path"), "released": ""}
                    for x in (j.json().get("auto") or {}).get("results") or []
                    if x.get("type") == "t" and x.get("item_url_path")]
    except Exception:
        pass
    raise Blocked(f"Bandcamp search answered {r.status_code}")


async def from_bandcamp(client, artist, title):
    queries = [f"{artist} {title}".strip()]
    parts = artist_parts(artist)
    if len(parts) > 1:
        queries.append(f"{parts[1]} {title}")           # first named artist only
    elif len(norm(title)) >= 6:
        queries.append(title)
    verdict, cand, seen = "", None, []
    for i, text in enumerate(queries):
        if i:
            await asyncio.sleep(PAUSE)
        seen += [c for c in await bandcamp_search(client, text) if c["type"] in ("track", "")]
        verdict, cand = best_candidate(artist, title, seen)
        if verdict == "bandcamp":
            break
    if not cand:
        return None
    out = {"status": verdict, "site": "Bandcamp", "url": cand["url"], "release": cand.get("album") or "",
           "found": " — ".join(x for x in (cand.get("artist"), cand.get("name")) if x),
           "year": (re.search(r"\b(19|20)\d{2}\b", cand.get("released") or "") or [""])[0]}
    try:    # the track's own page gives the album it is on, and the release date
        page = await client.get(cand["url"])
        if page.status_code == 200:
            rel = parse_release(page.text, cand["url"])
            out["url"] = rel.get("release_url") or out["url"]
            out["release"] = rel.get("release") or out["release"]
            out["year"] = (rel.get("published_at") or "")[:4] or out["year"]
    except Exception:
        pass
    return out


async def from_soundcloud(client, artist):
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
            data = await SOURCES["soundcloud"]._api(client, "/search/users", {"q": text, "limit": 10})
            for u in data.get("collection") or []:
                s = max(ratio(w, norm(u.get("username") or "")) for w in wanted)
                if s >= 0.85 and u.get("permalink_url"):
                    key = (round(s, 2), u.get("followers_count") or 0)
                    if best is None or key > best[0]:
                        best = (key, u)
            if best:
                break
        if best:
            return {"status": "soundcloud", "site": "SoundCloud", "url": best[1]["permalink_url"],
                    "found": best[1].get("username") or ""}
    except Exception:
        pass
    return {"status": "soundcloud_search", "site": "SoundCloud", "found": "",
            "url": "https://soundcloud.com/search/people?q=" + quote(artist),
            "note": "No matching profile; this opens a SoundCloud search"}


# ---------- running a lookup ----------

def _cache_key(artist, title):
    return norm(artist) + "|" + norm(title)


def _cached(key):
    rows = db.query("SELECT result FROM lookup_cache WHERE key=?", (key,))
    return json.loads(rows[0]["result"]) if rows else None


def _remember(key, result):
    db.execute("INSERT INTO lookup_cache(key, result, at) VALUES(?,?,?) "
               "ON CONFLICT(key) DO UPDATE SET result=excluded.result, at=excluded.at",
               (key, json.dumps(result), db.now()))


async def _run(job, fresh):
    local = local_index()
    bandcamp_ok = True
    async with refresh.make_client() as client:
        for row in job["rows"]:
            key = _cache_key(row["artist"], row["title"])
            result, used_network, keep = None, False, True
            # 1. the feed
            verdict, hit = best_candidate(row["artist"], row["title"], local.get(norm(row["title"]), []))
            if verdict == "bandcamp":
                result = {"status": "bandcamp", "site": "Bandcamp", "url": hit["url"], "release": hit["release"],
                          "year": (hit["published_at"] or "")[:4], "note": "In your feed",
                          "found": f"{hit['artist']} — {hit['name']}"}
            if result is None and not fresh:
                result = _cached(key)
                keep = result is None
            try:
                # 2. Bandcamp search
                if result is None and bandcamp_ok and row["title"]:
                    used_network = True
                    try:
                        result = await from_bandcamp(client, row["artist"], row["title"])
                    except Blocked as e:
                        bandcamp_ok, keep = False, False
                        job["warning"] = (f"{e}. The remaining rows were only checked against your feed "
                                          "and SoundCloud; try again later.")
                # 3. SoundCloud
                if result is None:
                    used_network = True
                    keep = keep and bandcamp_ok
                    result = await from_soundcloud(client, row["artist"])
                if result is None:
                    result = {"status": "none", "note": "No artist name to search for"}
            except Exception as e:
                result, keep = {"status": "error", "note":
                                f"Couldn't complete the search ({type(e).__name__}); run it again to retry"}, False
            if keep and result.get("note") != "In your feed":
                _remember(key, result)
            row.update({"site": "", "url": "", "year": "", "release": "", "found": "", "note": ""})
            row.update(result)
            job["done"] += 1
            if used_network:
                await asyncio.sleep(PAUSE)
    job["finished"] = True


def start(tracks, fresh=False):
    if len(jobs) > 20:                       # keep only recent lookups in memory
        for old in list(jobs)[:-20]:
            jobs.pop(old, None)
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "total": len(tracks), "done": 0, "finished": False, "warning": "",
           "rows": [dict(t, status="pending", site="", url="", year="", release="", found="", note="")
                    for t in tracks]}
    jobs[job_id] = job

    async def guarded():
        try:
            await _run(job, fresh)
        except Exception as e:
            job["warning"] = f"The lookup stopped early: {e}"
            job["finished"] = True
    task = asyncio.create_task(guarded())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job


STATUS_TEXT = {"bandcamp": "Bandcamp release", "bandcamp_check": "Bandcamp, check match",
               "soundcloud": "SoundCloud artist page", "soundcloud_search": "SoundCloud search",
               "none": "Not found", "error": "Error", "pending": "Not looked up yet"}


def to_csv(job) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["#", "artist", "title", "link", "site", "release", "year", "result", "found as", "note"])
    for r in job["rows"]:
        writer.writerow([r["n"], r["artist"], r["title"], r["url"], r["site"], r["release"], r["year"],
                         STATUS_TEXT.get(r["status"], r["status"]), r["found"], r["note"]])
    return out.getvalue().encode("utf-8-sig")
