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

state = {"refreshing": 0, "last_refresh": None, "held": 0}
_tasks = set()


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=BROWSER_HEADERS, follow_redirects=True, timeout=25)


async def _refresh_one(client, sem, account):
    async with sem:
        source = SOURCES.get(account["source"])
        if source is None:
            return
        try:
            result = await source.fetch(client, account, db.known_ids(account["id"]))
            db.save_result(account, result)
        except pace.Busy:
            state["held"] += 1      # not an error: tried again on the next refresh
        except SourceError as e:
            db.save_error(account["id"], str(e))
        except httpx.HTTPStatusError as e:
            db.save_error(account["id"], f"{source.label} answered with error {e.response.status_code}.")
        except httpx.HTTPError as e:
            db.save_error(account["id"], f"Couldn't reach {source.label} ({type(e).__name__}).")
        except Exception as e:  # a site changed its layout, most likely
            log.exception("refresh failed for %s", account["url"])
            db.save_error(account["id"], f"Unexpected problem reading this page: {e}")


async def refresh(account_ids=None):
    accounts = db.list_accounts(account_ids=account_ids)
    if not accounts:
        return
    state["refreshing"] += 1
    state["held"] = 0
    try:
        sem = asyncio.Semaphore(CONCURRENCY)
        async with make_client() as client:
            await asyncio.gather(*(_refresh_one(client, sem, a) for a in accounts))
        if account_ids is None:
            state["last_refresh"] = db.now()
            db.kv_set("last_refresh", state["last_refresh"])
    finally:
        state["refreshing"] -= 1


def refresh_in_background(account_ids=None):
    task = asyncio.create_task(refresh(account_ids))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def scheduler():
    state["last_refresh"] = db.kv_get("last_refresh")
    while True:
        try:
            await refresh()
        except Exception:
            log.exception("scheduled refresh failed")
        await asyncio.sleep(max(REFRESH_MINUTES, 5) * 60)
