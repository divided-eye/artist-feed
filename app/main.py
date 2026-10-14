import asyncio
import csv
import io
import json
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import db, lookup, pace, refresh
from .sources import SOURCES
from .sources.base import SourceError

VERSION = "32"   # shown beside the title, so you can tell which version is running
STATIC = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app):
    db.conn()
    task = None
    if os.environ.get("DISABLE_SCHEDULER") != "1":
        task = asyncio.create_task(refresh.scheduler())
    deep = asyncio.create_task(lookup.deep_read_forever()) if lookup.DEEP_AUTO else None
    yield
    for t in (task, deep):
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


@app.get("/api/sources")
def sources():
    return [{"key": s.key, "label": s.label, "color": s.color, "placeholder": s.placeholder}
            for s in SOURCES.values()]


@app.get("/api/artists")
def artists():
    return db.list_artists()


@app.post("/api/artists", status_code=201)
async def create_artist(body: ArtistIn):
    wanted = [a for a in body.accounts if a.handle.strip()]
    if not wanted:
        raise HTTPException(400, "Add at least one Bandcamp or SoundCloud page.")
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
    skip: list[str] = []                 # cells cleared on purpose: "Find missing links" leaves them alone
    rejected: dict[str, list[str]] = {}  # guesses turned down, never offered again


def _left_empty() -> dict:
    """{artist name, normalised: {"skip": [...], "rejected": {...}}} as last saved."""
    try:
        return json.loads(db.kv_get("table_left_empty") or "{}")
    except ValueError:
        return {}


class TableIn(BaseModel):
    rows: list[TableRow]


def _table_rows():
    rows, remembered = [], _left_empty()
    for a in db.list_artists():
        cells = {key: [] for key in SOURCES}
        problems = []
        for acc in a["accounts"]:
            source = SOURCES.get(acc["source"])
            if source is None:
                continue
            short = source.short(acc["handle"])
            cells[source.key].append(short)
            if acc["last_error"]:
                problems.append(f"{source.label} {short}: {acc['last_error']}")
            elif not acc["last_checked"]:
                problems.append(f"{source.label} {short}: checking…")
        note = remembered.get(lookup.norm(a["name"])) or {}
        rows.append({"id": a["id"], "name": a["name"], "post_count": a["post_count"],
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
    writer.writerow(["name"] + list(SOURCES))
    for row in _table_rows():
        writer.writerow([row["name"]] + [row["cells"][k] for k in SOURCES])
    return Response(out.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="artists.csv"'})


class SuggestIn(BaseModel):
    name: str = ""
    cells: dict[str, str] = {}
    skip: list[str] = []
    avoid: dict[str, list[str]] = {}


@app.post("/api/table/suggest")
async def suggest_row(body: SuggestIn):
    """Guesses for the pages one row is missing. Nothing is saved."""
    try:
        return await lookup.suggest_pages(body.name, body.cells, body.skip, body.avoid)
    except lookup.Blocked:
        raise HTTPException(503, "Bandcamp is asking the app to slow down. Try again in a few minutes.")
    except SourceError as e:
        raise HTTPException(400, str(e))


@app.put("/api/table")
async def save_table(body: TableIn):
    """Replace the whole artist list with the rows sent. Nothing changes unless every row is valid."""
    existing = {a["id"]: a for a in db.list_artists()}
    errors, claimed, plan, remember = [], {}, [], {}
    for index, row in enumerate(body.rows):
        pages = []
        for key, text in row.cells.items():
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
        name = row.name.strip()
        if not name and not pages:
            continue  # a blank row; for an existing artist this removes them
        plan.append((row.id if row.id in existing else None, name or pages[0][1], pages))
        filled = {key for key, _, _ in pages}
        note = {"skip": [k for k in row.skip if k in SOURCES and k not in filled],
                "rejected": {k: list(dict.fromkeys(v))[:20] for k, v in row.rejected.items() if k in SOURCES and v}}
        if note["skip"] or note["rejected"]:
            remember[lookup.norm(plan[-1][1])] = note
    if errors:
        return JSONResponse({"detail": "Some rows need fixing; nothing was saved.",
                             "errors": errors}, status_code=400)

    keep, drop_accounts, renames, new_accounts, new_artists = [], [], [], [], []
    for artist_id, name, pages in plan:
        if artist_id is None or artist_id in keep:
            new_artists.append((name, pages))
            continue
        keep.append(artist_id)
        current = existing[artist_id]
        if name != current["name"]:
            renames.append((artist_id, name))
        have = {(a["source"], a["handle"]): a["id"] for a in current["accounts"]}
        wanted = {(s, h) for s, h, _ in pages}
        drop_accounts += [acc_id for key, acc_id in have.items() if key not in wanted]
        new_accounts += [(artist_id, s, h, u) for s, h, u in pages if (s, h) not in have]
    added = db.apply_table(keep, drop_accounts, renames, new_accounts, new_artists)
    db.kv_set("table_left_empty", json.dumps(remember))
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
                 preview: Optional[str] = None):
    """The optional filters every view and the export accept. hidden=1 also shows hidden items;
    preview=1 leaves out tracks that can't be listened to yet."""
    return {"year": year, "label": label, "kind": kind, "length": length, "hidden": hidden == "1",
            "preview": preview == "1"}


class HiddenIn(BaseModel):
    hidden: bool = True


class PlayedIn(BaseModel):
    track: str = ""
    played: bool = True


@app.post("/api/posts/{post_id}/hidden")
def hide_post(post_id: int, body: HiddenIn):
    """Hide a release from the feed, or put it back."""
    if not db.set_hidden(post_id, body.hidden):
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
               more: dict = Depends(more_filters)):
    """The list view as a spreadsheet: every matching row, not just the ones on screen."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["artist", "label", "release", "track", "duration", "year",
                     "released", "posted", "site", "url"])
    for r in db.tracks(source, artist_id, q, sort, dir, more=more):
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


@app.put("/api/lookup/link")
async def set_link(body: LinkIn):
    """Save (or, with an empty url, remove) your own link for a track."""
    if not (body.artist.strip() or body.title.strip()):
        raise HTTPException(400, "Which track is this for?")
    url = body.url.strip()
    if not url:
        lookup.clear_manual(body.artist, body.title)
        result = {"status": "none", "note": "Your link was removed; run the lookup to search again"}
    else:
        if "://" not in url:
            url = "https://" + url
        if not re.fullmatch(r"https?://[^\s/]+\.[^\s]+", url):
            raise HTTPException(400, "That doesn't look like a web address.")
        year = body.year.strip()
        if year and not re.fullmatch(r"(19|20)\d{2}", year):
            raise HTTPException(400, "Year should be four digits, or left empty.")
        result = await lookup.set_manual(body.artist, body.title, url, year, body.release.strip())
    lookup.apply_to_jobs(body.artist, body.title, result)
    return result


@app.post("/api/lookup", status_code=201)
async def start_lookup(request: Request, fresh: bool = False):
    """Body: the tracklist itself - an .xlsx or CSV file's bytes, or pasted text."""
    data = await request.body()
    if len(data) > 5_000_000:
        raise HTTPException(413, "That file is too large (5 MB limit).")
    try:
        tracks = lookup.pick_tracks(lookup.read_table(data))
    except lookup.TracklistError as e:
        raise HTTPException(400, str(e))
    return lookup.start(tracks, fresh)


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
    return dict(_job(job_id), paused=pace.paused_seconds())


@app.post("/api/refresh")
async def refresh_now():
    if not refresh.state["refreshing"]:
        refresh.refresh_in_background()
    return {"ok": True}


@app.get("/api/status")
def status():
    return {"version": VERSION, "refreshing": refresh.state["refreshing"] > 0,
            "bandcamp_on_hold": pace.on_hold() or refresh.state["held"] > 0,
            "last_refresh": refresh.state["last_refresh"],
            "refresh_minutes": refresh.REFRESH_MINUTES}
