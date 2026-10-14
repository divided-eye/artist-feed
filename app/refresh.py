"""Fetch new posts for accounts, on a timer and on demand."""
import asyncio
import logging
import os

import httpx

from . import db, pace
from .sources import SOURCES
from .sources.base import BROWSER_HEADERS, SourceError

log = logging.getLogger("artist-feed")

REFRESH_MINUTES = float(os.environ.get("REFRESH_MINUTES", "60"))
CONCURRENCY = int(os.environ.get("REFRESH_CONCURRENCY", "3"))
# Pages whose back catalogue is still loading are visited again this often, between full refreshes.
CATCH_UP_MINUTES = float(os.environ.get("CATCH_UP_MINUTES", "10"))

state = {"refreshing": 0, "last_refresh": None, "held": 0, "queued": False, "last_result": None}
_tasks = set()


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=BROWSER_HEADERS, follow_redirects=True, timeout=25)


async def _refresh_one(client, sem, account, tally):
    async with sem:
        source = SOURCES.get(account["source"])
        if source is None:
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
    accounts = db.list_accounts(account_ids=account_ids)
    if not accounts:
        return
    state["refreshing"] += 1
    state["held"] = 0
    tally = {"pages": len(accounts), "checked": 0, "new": 0, "errors": 0, "held": 0,
             "lookup_running": pace.on_hold()}
    try:
        sem = asyncio.Semaphore(CONCURRENCY)
        async with make_client() as client:
            await asyncio.gather(*(_refresh_one(client, sem, a, tally) for a in accounts))
        if account_ids is None:
            state["last_refresh"] = db.now()
            db.kv_set("last_refresh", state["last_refresh"])
            state["last_result"] = dict(tally, at=state["last_refresh"])
            log.info("refresh: %(checked)s of %(pages)s pages read, %(new)s new posts, %(errors)s problems, "
                     "%(held)s Bandcamp pages left for later", tally)
    finally:
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
