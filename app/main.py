import asyncio
import csv
import io
import json
import logging
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import db, lookup, pace, refresh, saved, sites
from .sources import SOURCES
from .sources.base import SourceError

VERSION = "74"   # shown beside the title, so you can tell which version is running
STATIC = os.path.join(os.path.dirname(__file__), "static")


_log = logging.getLogger("artist-feed")          # refresh summaries and problems, in the container's log
if not _log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _log.addHandler(_handler)
    _log.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app):
    db.conn()
    lookup.rekey_old_answers()
    lookup.rekey_known()
    task = None
    if os.environ.get("DISABLE_SCHEDULER") != "1":
        task = asyncio.create_task(refresh.scheduler())
    deep = asyncio.create_task(lookup.deep_read_forever()) if lookup.DEEP_AUTO else None
    buys = asyncio.create_task(lookup.purchases_forever()) if task else None
    yield
    for t in (task, deep, buys):
        if t:
            t.cancel()


app = FastAPI(title="Artist Feed", lifespan=lifespan)


class AccountIn(BaseModel):
    source: str
    handle: str


class ArtistIn(BaseModel):
    name: str = ""
    accounts: list[AccountIn] = []


class ArtistPatch(BaseModel):
    name: str


def _add_account(artist_id: int, acc: AccountIn) -> int:
    source = SOURCES.get(acc.source)
    if source is None:
        raise HTTPException(400, f"Unknown source '{acc.source}'.")
    try:
        handle, url = source.normalize(acc.handle)
    except SourceError as e:
        raise HTTPException(400, str(e))
    try:
        return db.add_account(artist_id, source.key, handle, url)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"{url} is already being tracked.")


def _require_artist(artist_id: int):
    if not db.query("SELECT id FROM artists WHERE id=?", (artist_id,)):
        raise HTTPException(404, "Artist not found.")


@app.get("/")
def index():
    # no-cache: the browser always checks for a newer page after an update
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})


ICON_FILES = (("favicon.png", "image/png"), ("favicon.ico", "image/x-icon"), ("favicon.svg", "image/svg+xml"))


@app.get("/favicon")
@app.get("/favicon.ico")
def favicon():
    """The page's icon: the first of favicon.png, favicon.ico, favicon.svg found in app/static."""
    for name, mime in ICON_FILES:
        path = os.path.join(STATIC, name)
        if os.path.isfile(path):
            return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-cache"})
    raise HTTPException(404, "No icon file.")


@app.get("/api/sources")
def sources():
    """The sites switched on. suggest: Find missing links can guess this site's page."""
    return [{"key": s.key, "label": s.label, "color": s.color, "placeholder": s.placeholder,
             "suggest": s.key in ("bandcamp", "soundcloud")}
            for s in SOURCES.values() if sites.is_on(s.key)]


class SiteSettings(BaseModel):
    sites: Optional[dict[str, bool]] = None      # site key: switched on
    lookup_sites: Optional[list[str]] = None     # the sites Link Finder searches


@app.get("/api/settings")
def get_settings():
    return dict(sites.settings(), fallback=lookup.fallback_pref(), soundcloud=lookup.follows_status())


@app.put("/api/settings")
def put_settings(body: SiteSettings):
    """Switch sites on or off, and choose which ones Link Finder searches."""
    try:
        for key, on in (body.sites or {}).items():
            sites.set_on(key, on)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if body.lookup_sites is not None:
        sites.set_lookup_sites(body.lookup_sites)
    lookup.sites_changed()
    return get_settings()


class FollowsIn(BaseModel):
    user: str = ""


@app.post("/api/soundcloud/follows")
async def set_soundcloud_follows(body: FollowsIn):
    """Set your SoundCloud username (blank to stop) and read the profiles it follows now."""
    try:
        me = lookup.set_soundcloud_me(body.user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if me:
        async with refresh.make_client() as client:
            await lookup.followed_profiles(client, force=True)
    return lookup.follows_status()


@app.get("/api/artists")
def artists():
    focus = sites.focus_names()
    return [dict(a, focus=lookup.norm(a["name"]) in focus) for a in db.list_artists()]


@app.post("/api/artists", status_code=201)
async def create_artist(body: ArtistIn):
    wanted = [a for a in body.accounts if a.handle.strip()]
    if not wanted:
        raise HTTPException(400, "Add at least one page (Bandcamp, SoundCloud or YouTube).")
    # Validate everything before writing anything.
    for a in wanted:
        source = SOURCES.get(a.source)
        if source is None:
            raise HTTPException(400, f"Unknown source '{a.source}'.")
        try:
            handle, url = source.normalize(a.handle)
        except SourceError as e:
            raise HTTPException(400, str(e))
        if db.query("SELECT 1 FROM accounts WHERE source=? AND handle=?", (source.key, handle)):
            raise HTTPException(409, f"{url} is already being tracked.")
    first = SOURCES[wanted[0].source].normalize(wanted[0].handle)[0]
    artist_id = db.add_artist(body.name.strip() or first)
    ids = [_add_account(artist_id, a) for a in wanted]
    refresh.refresh_in_background(ids)
    return {"id": artist_id}


# --- the artist list as one table: a row per artist, a column per site ---

class TableRow(BaseModel):
    id: Optional[int] = None
    name: str = ""
    cells: dict[str, str] = {}
    youtube_lookup: Optional[bool] = None   # Link Finder may search YouTube for this artist or label
    keep_separate: bool = False             # shares a name with another row on purpose (different artists)
    focus: bool = False                     # shown by the Focus Feed button
    remove: bool = False                    # marked for removal (✕): stops being tracked, like a blank row
    skip: list[str] = []                 # cells cleared on purpose: "Find missing links" leaves them alone
    rejected: dict[str, list[str]] = {}  # guesses turned down, never offered again


def _left_empty() -> dict:
    """{artist name, normalised: {"skip": [...], "rejected": {...}}} as last saved."""
    try:
        return json.loads(db.kv_get("table_left_empty") or "{}")
    except ValueError:
        return {}


def _same_name_ok() -> set:
    """Names that more than one row shares on purpose (different artists with the same name)."""
    try:
        return set(json.loads(db.kv_get("same_name_ok") or "[]"))
    except ValueError:
        return set()


class TableIn(BaseModel):
    rows: list[TableRow]


def _table_rows():
    rows, remembered, on, youtube, same = [], _left_empty(), sites.enabled(), sites.youtube_names(), _same_name_ok()
    focus = sites.focus_names()
    for a in db.list_artists():
        cells = {key: [] for key in on}
        problems = []
        for acc in a["accounts"]:
            source = SOURCES.get(acc["source"])
            if source is None or source.key not in cells:      # a site switched off: kept, not shown
                continue
            short = source.short(acc["handle"])
            cells[source.key].append(short)
            if acc["last_error"]:
                problems.append(f"{source.label} {short}: {acc['last_error']}")
            elif not acc["last_checked"]:
                problems.append(f"{source.label} {short}: checking…")
        note = remembered.get(lookup.norm(a["name"])) or {}
        shown = lookup.join_names([a["name"]] + lookup.split_names(a.get("aliases") or ""))
        rows.append({"id": a["id"], "name": shown, "post_count": a["post_count"],
                     "youtube_lookup": lookup.norm(a["name"]) in youtube,
                     "focus": lookup.norm(a["name"]) in focus,
                     "same_name": lookup.norm(a["name"]) in same,
                     "cells": {k: ", ".join(v) for k, v in cells.items()}, "problems": problems,
                     "skip": [k for k in note.get("skip") or [] if k in cells and not cells[k]],
                     "rejected": note.get("rejected") or {}})
    return rows


@app.get("/api/table")
def get_table():
    return _table_rows()


@app.get("/api/artists.csv")
def export_csv():
    out = io.StringIO()
    writer = csv.writer(out)
    on = sites.enabled()
    writer.writerow(["name"] + on)
    for row in _table_rows():
        writer.writerow([row["name"]] + [row["cells"][k] for k in on])
    return Response(out.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="artists.csv"'})


class SuggestIn(BaseModel):
    name: str = ""
    cells: dict[str, str] = {}
    skip: list[str] = []
    avoid: dict[str, list[str]] = {}


@app.post("/api/artists/{artist_id}/check")
async def check_artist(artist_id: int):
    """Tracked Artists' "Check now": read one artist's pages straight away, then return its row."""
    ids = [a["id"] for a in db.list_accounts(artist_id=artist_id)]
    if not ids:
        raise HTTPException(404, "That artist has no pages to check.")
    await refresh.refresh(account_ids=ids)
    row = next((r for r in _table_rows() if r["id"] == artist_id), None)
    if row is None:
        raise HTTPException(404, "That artist is no longer tracked.")
    return row


@app.post("/api/table/suggest")
async def suggest_row(body: SuggestIn):
    """Guesses for the pages one row is missing. Nothing is saved."""
    try:
        first = (lookup.split_names(body.name) or [""])[0]          # other names in the cell are aliases
        found = await lookup.suggest_pages(first, body.cells, body.skip, body.avoid)
        if body.name.strip():
            found["name"] = ""                                      # never write over names already typed
        return found
    except lookup.Blocked:
        raise HTTPException(503, "Bandcamp is asking the app to slow down. Try again in a few minutes.")
    except SourceError as e:
        raise HTTPException(400, str(e))


@app.put("/api/table")
async def save_table(body: TableIn):
    """Replace the whole artist list with the rows sent. Nothing changes unless every row is valid."""
    existing = {a["id"]: a for a in db.list_artists()}
    errors, claimed, plan, remember, youtube, claimed_names, focus = [], {}, [], {}, set(), {}, set()
    allowed = _same_name_ok()
    on = set(sites.enabled())
    for index, row in enumerate(body.rows):
        if row.remove:
            continue                                # left out: an existing artist stops being tracked
        pages = []
        for key, text in row.cells.items():
            if key in SOURCES and key not in on:
                continue                            # a site switched off: its pages are left as they are
            for piece in filter(None, (t.strip() for t in re.split(r"[,;\n]+", text or ""))):
                source = SOURCES.get(key)
                if source is None:
                    errors.append({"row": index, "message": f"Unknown site '{key}'."})
                    continue
                try:
                    handle, url = source.normalize(piece)
                except SourceError as e:
                    errors.append({"row": index, "message": f"{source.label} \"{piece}\": {e}"})
                    continue
                if (key, handle) in claimed and claimed[(key, handle)] != index:
                    errors.append({"row": index, "message":
                                   f"{url} is also on row {claimed[(key, handle)] + 1}."})
                    continue
                if (key, handle) not in claimed:
                    claimed[(key, handle)] = index
                    pages.append((key, handle, url))
        names = lookup.split_names(row.name)       # the artist's name, then other names they go by
        name, aliases = (names[0] if names else ""), lookup.join_names(names[1:])
        if not name and not pages:
            continue  # a blank row; for an existing artist this removes them
        for n in dict.fromkeys(lookup.norm(x) for x in names):
            if n:
                claimed_names.setdefault(n, []).append(index)
                if row.keep_separate:
                    allowed.add(n)
        plan.append((row.id if row.id in existing else None, name or pages[0][1], pages, aliases))
        filled = {key for key, _, _ in pages}
        note = {"skip": [k for k in row.skip if k in SOURCES and k not in filled],
                "rejected": {k: list(dict.fromkeys(v))[:20] for k, v in row.rejected.items() if k in SOURCES and v}}
        if note["skip"] or note["rejected"]:
            remember[lookup.norm(plan[-1][1])] = note
        if row.focus:
            focus.add(lookup.norm(plan[-1][1]))
        if row.youtube_lookup:                      # every name the artist goes by
            youtube.update(lookup.norm(n) for n in [plan[-1][1]] + lookup.split_names(aliases))
    # Two rows for one artist (by any of their names) would leave Link Finder picking one of them:
    # they have to be merged, or one renamed - unless you say they are different artists.
    shared = {n: rows for n, rows in claimed_names.items() if len(rows) > 1}
    for n, rows in shared.items():
        if n in allowed:
            continue
        for i in rows:
            others = ", ".join(str(j + 1) for j in rows if j != i)
            errors.append({"row": i, "kind": "same_name", "message": f'Same name as row {others}: merge the rows '
                           "(keep every page in one), rename one, or keep both if they are different artists."})
    if errors:
        return JSONResponse({"detail": "Some rows need fixing; nothing was saved.",
                             "errors": errors}, status_code=400)

    keep, drop_accounts, renames, new_accounts, new_artists = [], [], [], [], []
    for artist_id, name, pages, aliases in plan:
        if artist_id is None or artist_id in keep:
            new_artists.append((name, aliases, pages))
            continue
        keep.append(artist_id)
        current = existing[artist_id]
        if name != current["name"] or aliases != (current.get("aliases") or ""):
            renames.append((artist_id, name, aliases))
        have = {(a["source"], a["handle"]): a["id"] for a in current["accounts"] if a["source"] in on}
        wanted = {(s, h) for s, h, _ in pages}
        drop_accounts += [acc_id for key, acc_id in have.items() if key not in wanted]
        new_accounts += [(artist_id, s, h, u) for s, h, u in pages if (s, h) not in have]
    added = db.apply_table(keep, drop_accounts, renames, new_accounts, new_artists)
    db.kv_set("table_left_empty", json.dumps(remember))
    db.kv_set("same_name_ok", json.dumps(sorted(n for n in shared if n in allowed)))   # only names still shared
    sites.set_focus_names(focus)
    if any(r.youtube_lookup is not None for r in body.rows):       # sent only while YouTube is on
        sites.set_youtube_names(youtube)
    lookup.forget_aliases()
    if added:
        refresh.refresh_in_background(added)
    return {"rows": _table_rows(), "added": len(added)}


@app.patch("/api/artists/{artist_id}")
def rename_artist(artist_id: int, body: ArtistPatch):
    _require_artist(artist_id)
    if not body.name.strip():
        raise HTTPException(400, "Name can't be empty.")
    db.execute("UPDATE artists SET name=? WHERE id=?", (body.name.strip(), artist_id))
    return {"ok": True}


@app.delete("/api/artists/{artist_id}")
def delete_artist(artist_id: int):
    _require_artist(artist_id)
    db.execute("DELETE FROM artists WHERE id=?", (artist_id,))
    return {"ok": True}


@app.post("/api/artists/{artist_id}/accounts", status_code=201)
async def create_account(artist_id: int, body: AccountIn):
    _require_artist(artist_id)
    account_id = _add_account(artist_id, body)
    refresh.refresh_in_background([account_id])
    return {"id": account_id}


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int):
    db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
    return {"ok": True}


def more_filters(year: Optional[str] = None, label: Optional[str] = None,
                 kind: Optional[str] = None, length: Optional[str] = None, hidden: Optional[str] = None,
                 preview: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None,
                 date_by: Optional[str] = None, focus: Optional[str] = None):
    """The optional filters every view and the export accept. hidden=1 also shows hidden items;
    preview=1 leaves out tracks that can't be listened to yet; since/until (YYYY-MM-DD) keep a
    date range, by posted date (date_by=posted, the default) or release date (date_by=released)."""
    day = lambda v: v if v and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else None
    return {"year": year, "label": label, "kind": kind, "length": length, "hidden": hidden == "1",
            "preview": preview == "1", "since": day(since), "until": day(until),
            "date_by": "released" if date_by == "released" else "posted",
            "focus_ids": _focus_ids() if focus == "1" else None}


def _focus_ids() -> list:
    """The artists ticked in the Focus column (focus=1: the Focus Feed button)."""
    names = sites.focus_names()
    return [r["id"] for r in db.query("SELECT id, name FROM artists") if lookup.norm(r["name"]) in names]


class HiddenIn(BaseModel):
    hidden: bool = True
    track: Optional[str] = None     # one track of the release (from the list); none: the whole release


class PlayedIn(BaseModel):
    track: str = ""
    played: bool = True


@app.post("/api/posts/{post_id}/hidden")
def hide_post(post_id: int, body: HiddenIn):
    """Hide a release from the feed, or one of its tracks from the list, or put it back."""
    done = db.set_track_hidden(post_id, body.track, body.hidden) if body.track is not None else db.set_hidden(post_id, body.hidden)
    if not done:
        raise HTTPException(404, "That item is no longer in the feed.")
    return {"hidden": db.facets()["hidden"]}


@app.post("/api/posts/{post_id}/played")
def mark_played(post_id: int, body: PlayedIn):
    """Remember that a release (track "") or one of its tracks has been listened to."""
    if not db.set_played(post_id, body.track, body.played):
        raise HTTPException(404, "That item is no longer in the feed.")
    return {"ok": True}


@app.get("/api/facets")
def facets():
    return db.facets()


@app.get("/api/feed")
def feed(source: Optional[str] = None, artist_id: Optional[int] = None,
         limit: int = 60, offset: int = 0, q: Optional[str] = None,
         more: dict = Depends(more_filters)):
    return db.feed(source, artist_id, max(1, min(limit, 200)), max(0, offset), q, more)


def _clock(seconds):
    if seconds is None:
        return ""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    return (f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours
            else f"{rest // 60}:{rest % 60:02d}")


@app.get("/api/tracks")
def tracks(source: Optional[str] = None, artist_id: Optional[int] = None,
           q: Optional[str] = None, sort: str = "date", dir: str = "desc",
           limit: int = 200, offset: int = 0, more: dict = Depends(more_filters)):
    return db.tracks(source, artist_id, q, sort, dir, max(1, min(limit, 1000)), max(0, offset), more)


@app.get("/api/tracks.csv")
def tracks_csv(source: Optional[str] = None, artist_id: Optional[int] = None,
               q: Optional[str] = None, sort: str = "date", dir: str = "desc",
               max: Optional[int] = None, more: dict = Depends(more_filters)):
    """The list view as a spreadsheet: every matching row, not just the ones on screen
    (or the first `max` of them, when the list is limited to a number of results)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["artist", "label", "release", "track", "duration", "year",
                     "released", "posted", "site", "url"])
    for r in db.tracks(source, artist_id, q, sort, dir, limit=max if max and max > 0 else None, more=more):
        day = (r["published_at"] or "")[:10]
        site = SOURCES[r["source"]].label if r["source"] in SOURCES else r["source"]
        writer.writerow([r["artist"], r["label"], r["release"], r["track"],
                         _clock(r["duration"]), day[:4], day, (r["posted"] or "")[:10],
                         site, r["url"]])
    # utf-8-sig so Excel reads accented names correctly
    return Response(out.getvalue().encode("utf-8-sig"), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="tracks.csv"'})


# --- tracklist lookup ---

def _job(job_id: str):
    job = lookup.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "That lookup is no longer available; run it again.")
    return job


class FanIn(BaseModel):
    fan: str = ""


@app.get("/api/collection")
def collection():
    return lookup.collection_status()


@app.post("/api/collection/tracklists")
async def read_tracklists():
    """Start (or carry on) reading the full track list of every purchase."""
    lookup.deep_read_in_background()
    return lookup.collection_status()


@app.post("/api/collection")
async def set_collection(body: FanIn):
    """Set the Bandcamp account whose purchases the lookup uses, and read them."""
    fan = body.fan.strip().rstrip("/").rsplit("/", 1)[-1]        # a name or the page's address
    if fan and not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", fan):
        raise HTTPException(400, "That doesn't look like a Bandcamp username.")
    db.kv_set("bandcamp_fan", fan)
    if fan:
        lookup.sync_in_background(full=True)
    return lookup.collection_status()


class LinkIn(BaseModel):
    artist: str = ""
    title: str = ""
    url: str = ""
    year: str = ""
    release: str = ""
    key: str = ""        # a Saved results row, for answers saved without the names as typed


@app.put("/api/lookup/link")
async def set_link(body: LinkIn):
    """Save (or, with an empty url, remove) your own link for a track."""
    if not (body.artist.strip() or body.title.strip()):
        raise HTTPException(400, "Which track is this for?")
    url = body.url.strip()
    if not url:
        lookup.clear_manual(body.artist, body.title, body.key.strip())
        result = {"status": "none", "note": "Your link was removed; run the lookup to search again"}
    else:
        if "://" not in url:
            url = "https://" + url
        if not re.fullmatch(r"https?://[^\s/]+\.[^\s]+", url):
            raise HTTPException(400, "That doesn't look like a web address.")
        year = body.year.strip()
        if year and not re.fullmatch(r"(19|20)\d{2}", year):
            raise HTTPException(400, "Year should be four digits, or left empty.")
        result = await lookup.set_manual(body.artist, body.title, url, year, body.release.strip(), body.key.strip())
    lookup.apply_to_jobs(body.artist, body.title, result)
    return result


@app.post("/api/lookup", status_code=201)
async def start_lookup(request: Request, fresh: bool = False, retry: str = ""):
    """Body: the tracklist itself - an .xlsx or CSV file's bytes, or pasted text.
    fresh=true searches everything again; retry=missing only the rows saved as not found."""
    data = await request.body()
    if len(data) > 5_000_000:
        raise HTTPException(413, "That file is too large (5 MB limit).")
    try:
        tracks = lookup.pick_tracks(lookup.read_table(data))
    except lookup.TracklistError as e:
        raise HTTPException(400, str(e))
    return lookup.view(lookup.start(tracks, fresh, retry == "missing"))


# --- everything Link Finder has saved, and your own list of spellings and links ---

@app.get("/api/saved")
def saved_results(q: str = "", filter: str = "", limit: int = 200, offset: int = 0, sort: str = "", dir: str = "asc"):
    return saved.listing(q, filter if filter in saved.FILTERS else "", max(1, min(limit, 1000)), max(0, offset),
                         sort, "desc" if dir == "desc" else "asc")


@app.get("/api/saved.csv")
def saved_csv(q: str = "", filter: str = "", sort: str = "", dir: str = "asc"):
    return Response(saved.to_csv(q, filter if filter in saved.FILTERS else "", sort, "desc" if dir == "desc" else "asc"), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="link-finder-saved.csv"'})


@app.delete("/api/saved")
def forget_saved(key: str):
    """Forget the saved answer (and any link you set) for one track; it is searched for next time."""
    return {"removed": saved.forget(key)}


class SpellingIn(BaseModel):
    key: str                 # the saved track, as Saved results lists it
    artist: str = ""
    title: str = ""
    variant: str = ""        # the list entry being corrected, when it had one
    like_key: str = ""       # the closest entry it was shown with, whose year, link and note are kept
    like_variant: str = ""


@app.put("/api/known/entry")
def set_spelling(body: SpellingIn):
    """Your spelling of one track's artist and title, typed in Saved results."""
    if "|" not in body.key:
        raise HTTPException(400, "Which track is this for?")
    like = (body.like_key, body.like_variant) if body.like_key else ()
    return lookup.set_spelling(body.key.partition("~")[0], body.artist, body.title, body.variant, like)


@app.delete("/api/known/entry")
def forget_entry(key: str, variant: str = ""):
    """Take one track out of your imported list; saved answers are kept."""
    if "|" not in key:
        raise HTTPException(400, "Which track is this for?")
    return lookup.forget_entry(key, variant)


@app.get("/api/known")
def known_list():
    return lookup.known_counts()


@app.post("/api/known")
async def import_known(request: Request):
    """Body: your list - an .xlsx or CSV file's bytes, or pasted rows. Artist and title columns,
    and optionally links; text written beside a link is kept as a note."""
    data = await request.body()
    if len(data) > 25_000_000:
        raise HTTPException(413, "That file is too large (25 MB limit).")
    try:
        return lookup.import_known(lookup.read_known(lookup.read_table(data)))
    except lookup.TracklistError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/known")
def clear_known():
    db.execute("DELETE FROM known_tracks")
    db.execute("DELETE FROM known_artists")
    lookup._known_cache.clear()
    return lookup.known_counts()


class LookupSettings(BaseModel):
    fallback: str


@app.get("/api/lookup/settings")
def lookup_settings():
    return {"fallback": lookup.fallback_pref()}


@app.put("/api/lookup/settings")
def set_lookup_settings(body: LookupSettings):
    """Which artist link to give when a track isn't found: "bandcamp" or "soundcloud"."""
    if body.fallback not in ("bandcamp", "soundcloud"):
        raise HTTPException(400, 'Choose "bandcamp" or "soundcloud".')
    lookup.set_fallback(body.fallback)
    return {"fallback": lookup.fallback_pref()}


@app.get("/api/lookup/{job_id}.csv")
def lookup_csv(job_id: str):
    return Response(lookup.to_csv(_job(job_id)), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="tracklist-links.csv"'})


@app.get("/api/lookup/{job_id}")
def lookup_status(job_id: str):
    return dict(lookup.view(_job(job_id)), paused=pace.paused_seconds())


class RetryIn(BaseModel):
    n: int


@app.post("/api/lookup/{job_id}/skip")
async def skip_lookup_row(job_id: str, body: RetryIn):
    """Stop searching for one row (now, or before its turn): it is left for you to fill in."""
    job = lookup.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "That lookup has expired. Run it again.")
    try:
        lookup.skip_row(job, body.n)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return lookup.view(job)


@app.post("/api/lookup/{job_id}/retry")
async def retry_lookup_row(job_id: str, body: RetryIn):
    """Search again for one row, ignoring its saved answer and Bandcamp's saved search answers."""
    job = _job(job_id)
    try:
        lookup.retry_row(job, body.n)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return dict(lookup.view(job), paused=pace.paused_seconds())


@app.post("/api/refresh")
async def refresh_now():
    return {"ok": True, "state": refresh.request_refresh()}


RELATED_HOURS = 24


@app.get("/api/related/{track_id}")
async def related_tracks(track_id: str, offset: int = 0, limit: int = 3):
    """A SoundCloud track's "Related tracks", read only when asked (the + beside a row in the
    list), kept for a day, and handed out a few at a time."""
    if not track_id.isdigit():
        raise HTTPException(400, "Related tracks are only available for SoundCloud tracks.")
    rows = lookup.cache_get("sc_related", track_id, RELATED_HOURS / 24)
    if rows is None:
        try:
            async with refresh.make_client() as client:
                rows = await SOURCES["soundcloud"].related(client, track_id)
        except SourceError as e:
            raise HTTPException(502, str(e))
        except Exception:
            _log.exception("related tracks for %s", track_id)
            raise HTTPException(502, "Couldn't reach SoundCloud for related tracks.")
        lookup.cache_put("sc_related", track_id, rows)
    offset, limit = max(0, offset), max(1, min(limit, 50))
    return {"tracks": rows[offset:offset + limit], "total": len(rows)}


@app.get("/api/downloads")
def get_downloads():
    """Purchases ready to download that you haven't been shown yet."""
    return lookup.downloads()


@app.post("/api/downloads/clear")
def clear_downloads():
    lookup.clear_downloads()
    return lookup.downloads()


@app.post("/api/downloads/check")
async def check_downloads():
    """Look for new purchases and pre-orders that have come out now."""
    try:
        await lookup.check_purchases()
    except lookup.Blocked:
        raise HTTPException(503, "Bandcamp is asking the app to slow down. Try again in a few minutes.")
    return lookup.downloads()


@app.get("/api/status")
def status():
    d = lookup.downloads()
    return {"version": VERSION, "refreshing": refresh.state["refreshing"] > 0,
            "downloads": {"fan": d["fan"], "url": d["url"], "count": d["count"],
                          "items": [f'{x["band"]} — {x["title"]}' for x in d["items"][:20]]},
            "bandcamp_on_hold": pace.on_hold() or refresh.state["held"] > 0,
            "last_refresh": refresh.state["last_refresh"], "queued": refresh.state["queued"],
            "last_result": refresh.state["last_result"],
            "progress": refresh.progress(),
            "refresh_minutes": refresh.REFRESH_MINUTES}
