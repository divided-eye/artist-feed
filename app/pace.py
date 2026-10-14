"""One shared pace for every request the app makes to Bandcamp.

The feed refresh, the purchases reader and the tracklist lookup all draw from
the same queue, so together they never ask faster than one request per GAP
seconds. When Bandcamp answers 429 ("slow down"), everything pauses for the
time it asks, and the gap between requests widens for a while afterwards.
"""
import asyncio
import os
import time

GAP = float(os.environ.get("BANDCAMP_GAP_SECONDS", "1.5"))          # normal gap between requests
MAX_GAP = float(os.environ.get("BANDCAMP_MAX_GAP_SECONDS", "8"))
BACKOFF = float(os.environ.get("BANDCAMP_BACKOFF_SECONDS", "30"))   # first pause after a 429; doubles each time
TRIES = int(os.environ.get("BANDCAMP_TRIES", "6"))                  # attempts per request before giving up

_state = {"next": 0.0, "gap": GAP, "lock": None, "paused_until": 0.0}


async def wait():
    """Take the next slot in the queue."""
    if _state["lock"] is None:
        _state["lock"] = asyncio.Lock()
    async with _state["lock"]:
        delay = _state["next"] - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        _state["next"] = time.monotonic() + _state["gap"]
        _state["gap"] = max(GAP, _state["gap"] * 0.97)      # drift back to normal while things go well


def slow_down(seconds: float):
    """Bandcamp said 429: nobody asks again for `seconds`, and the gap widens."""
    _state["next"] = max(_state["next"], time.monotonic() + seconds)
    _state["paused_until"] = max(_state["paused_until"], time.time() + seconds)
    _state["gap"] = min(MAX_GAP, max(_state["gap"], GAP) * 1.6) if GAP else 0.0


def pause_for(response, attempt: int) -> float:
    """How long to stay quiet after a 429: what Bandcamp asks for, else a doubling wait."""
    try:
        asked = float(response.headers.get("retry-after", ""))
    except ValueError:
        asked = 0.0
    return min(600.0, max(asked, BACKOFF * (2 ** attempt)))


def paused_seconds() -> int:
    return max(0, int(_state["paused_until"] - time.time()))
