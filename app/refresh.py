"""Fetch new posts for accounts, on a timer and on demand."""
import asyncio
import json
import logging
import os
import time

import httpx

from . import db, pace
from . import sites
from .sources import SOURCES
from .sources.base import BROWSER_HEADERS, SourceError

log = logging.getLogger("artist-feed")

REFRESH_MINUTES = float(os.environ.get("REFRESH_MINUTES", "60"))
# Pages read at the same time, per site. Each site has its own queue, so the quick sites (SoundCloud,
# YouTube) never wait behind Bandcamp, whose requests are spaced out (see pace.py).
CONCURRENCY = int(os.environ.get("REFRESH_CONCURRENCY", "3"))
# Pages whose back catalogue is still loading are visited again this often, between full refreshes.
CATCH_UP_MINUTES = float(os.environ.get("CATCH_UP_MINUTES", "10"))

state = {"refreshing": 0, "last_refresh": None, "held": 0, "queued": False, "last_result": None}
_runs = []          # the refreshes running now: {"pages", "done", "started"}
_tasks = set()


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=BROWSER_HEADERS, follow_redirects=True, timeout=25)


async def _refresh_one(client, sem, account, tally, run):
    try:
        await _read_one(client, sem, account, tally)
    finally:
        run["done"] += 1
        site = run["by"][account["source"]]
        site["done"] += 1
        if site["done"] == site["pages"]:
            site["finished"] = time.monotonic()


def _saved_pace() -> dict:
    try:
        saved = json.loads(db.kv_get("refresh_pace") or "{}")
    except ValueError:
        saved = {}
    return saved if isinstance(saved, dict) else {}


def progress():
    """Pages checked so far in the refreshes running now, and a rough estimate of the time left.

    Each site is timed on its own: Bandcamp pages are read two seconds apart (and wait out any pause
    Bandcamp asks for), while SoundCloud and YouTube pages are quick, so one overall pace would
    rise as the quick pages run out. A site's pace comes from this refresh once a few of its pages
    are done, before that from the last full refresh. The refresh ends when the slowest site does."""
    if not _runs:
        return None
    pages, done = sum(r["pages"] for r in _runs), sum(r["done"] for r in _runs)
    new = sum(r.get("tally", {}).get("new", 0) for r in _runs)          # new posts saved so far: the page offers to show them
    now, saved = time.monotonic(), _saved_pace()
    left, known = 0.0, False
    for run in _runs:
        elapsed = max(now - run["started"], 0.001)
        for key, site in run["by"].items():
            remaining = site["pages"] - site["done"]
            if remaining <= 0:
                continue
            if site["done"] >= 5 or (site["done"] and not saved.get(key)):
                each = elapsed / site["done"]
            elif saved.get(key):
                each = float(saved[key])
            else:
                continue
            known = True
            left = max(left, remaining * each)
    return {"pages": pages, "done": done, "seconds_left": round(left) if known else None, "new": new}


async def _read_one(client, sem, account, tally):
    async with sem:
        source = SOURCES.get(account["source"])
        if source is None or not sites.is_on(source.key):     # a site switched off isn't checked
            return
        problem = None
        try:
            known = db.known_ids(account["id"])
            result = await source.fetch(client, account, known)
            db.save_result(account, result)
            tally["new"] += sum(1 for p in result.posts if p.external_id not in known)
            tally["checked"] += 1
        except pace.Busy:
            state["held"] += 1      # not an error: tried again on the next refresh
            tally["held"] += 1
        except SourceError as e:
            problem = str(e)
        except httpx.HTTPStatusError as e:
            problem = f"{source.label} answered with error {e.response.status_code}."
        except httpx.HTTPError as e:
            problem = f"Couldn't reach {source.label} ({type(e).__name__})."
        except Exception as e:  # a site changed its layout, most likely
            log.exception("refresh failed for %s", account["url"])
            problem = f"Unexpected problem reading this page: {e}"
        if problem:
            db.save_error(account["id"], problem)
            log.warning("refresh: %s: %s", account["url"], problem)
            tally["errors"] += 1


async def refresh(account_ids=None):
    accounts = [a for a in db.list_accounts(account_ids=account_ids) if sites.is_on(a["source"])]
    if not accounts:
        return
    state["refreshing"] += 1
    state["held"] = 0
    tally = {"pages": len(accounts), "checked": 0, "new": 0, "errors": 0, "held": 0,
             "lookup_running": pace.on_hold()}
    run = {"pages": len(accounts), "done": 0, "started": time.monotonic(), "by": {}, "tally": tally}
    for a in accounts:
        run["by"].setdefault(a["source"], {"pages": 0, "done": 0})["pages"] += 1
    _runs.append(run)
    try:
        sems = {key: asyncio.Semaphore(CONCURRENCY) for key in run["by"]}      # one queue per site
        async with make_client() as client:
            await asyncio.gather(*(_refresh_one(client, sems[a["source"]], a, tally, run) for a in accounts))
        if account_ids is None:          # each site's pace, remembered for the next estimate
            pace_now = _saved_pace()
            for key, site in run["by"].items():
                if site["pages"] >= 5 and site.get("finished"):
                    pace_now[key] = round((site["finished"] - run["started"]) / site["pages"], 3)
            db.kv_set("refresh_pace", json.dumps(pace_now))
        if account_ids is None:
            state["last_refresh"] = db.now()
            db.kv_set("last_refresh", state["last_refresh"])
            state["last_result"] = dict(tally, at=state["last_refresh"])
            log.info("refresh: %(checked)s of %(pages)s pages read, %(new)s new posts, %(errors)s problems, "
                     "%(held)s Bandcamp pages left for later", tally)
    finally:
        _runs.remove(run)
        state["refreshing"] -= 1


async def refresh_when_free():
    """Asked for a full refresh while another (often a few new pages after a save) is running:
    do it as soon as that one ends, instead of ignoring the request."""
    try:
        while state["refreshing"]:
            await asyncio.sleep(1)
        await refresh()
    finally:
        state["queued"] = False


def request_refresh() -> str:
    """The Refresh Feed button: "started", or "queued" behind a refresh already running."""
    if state["queued"]:
        return "queued"
    if state["refreshing"]:
        state["queued"] = True
        task = asyncio.create_task(refresh_when_free())
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return "queued"
    refresh_in_background()
    return "started"


def refresh_in_background(account_ids=None):
    task = asyncio.create_task(refresh(account_ids))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def backlog_accounts() -> list:
    """Accounts with older releases still to load (see the Bandcamp source's warning)."""
    return [a["id"] for a in db.list_accounts() if "still to load" in (a.get("last_error") or "")]


async def scheduler():
    """A full refresh every REFRESH_MINUTES. In between, pages with a back catalogue still to
    load get short catch-up visits, so a long catalogue takes hours rather than days."""
    state["last_refresh"] = db.kv_get("last_refresh")
    every, step = max(REFRESH_MINUTES, 5) * 60, max(CATCH_UP_MINUTES, 2) * 60
    while True:
        try:
            await refresh()
        except Exception:
            log.exception("scheduled refresh failed")
        waited = 0.0
        while waited < every:
            nap = min(step, every - waited)
            await asyncio.sleep(nap)
            waited += nap
            if waited >= every:
                break
            try:
                behind = backlog_accounts()
                if behind and not pace.on_hold() and not state["refreshing"]:
                    await refresh(behind)
            except Exception:
                log.exception("catch-up refresh failed")
