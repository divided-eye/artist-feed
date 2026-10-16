"""Fetch new posts for accounts, on a timer and on demand."""
import asyncio
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


def progress():
    """Pages checked so far in the refreshes running now, and a rough estimate of the time left.

    The estimate uses this run's own pace once a few pages are done; before that, the pace of the
    last full refresh. Bandcamp's pauses make it uneven, so it is only a guide."""
    if not _runs:
        return None
    pages, done = sum(r["pages"] for r in _runs), sum(r["done"] for r in _runs)
    elapsed = time.monotonic() - min(r["started"] for r in _runs)
    try:
        saved = float(db.kv_get("refresh_seconds_per_page") or 0)
    except ValueError:
        saved = 0.0
    if done >= 10 or (done and not saved):
        per_page = elapsed / done
    elif saved:
        per_page = saved
    else:
        return {"pages": pages, "done": done, "seconds_left": None}
    return {"pages": pages, "done": done, "seconds_left": round(max(0, pages - done) * per_page)}


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
    run = {"pages": len(accounts), "done": 0, "started": time.monotonic()}
    _runs.append(run)
    try:
        sem = asyncio.Semaphore(CONCURRENCY)
        async with make_client() as client:
            await asyncio.gather(*(_refresh_one(client, sem, a, tally, run) for a in accounts))
        if account_ids is None and len(accounts) >= 5:      # remembered for the next estimate
            db.kv_set("refresh_seconds_per_page", f"{(time.monotonic() - run['started']) / len(accounts):.3f}")
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
