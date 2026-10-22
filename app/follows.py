"""Who an account follows, for adding them to Tracked Artists in one go.

SoundCloud lists the profiles any account follows. Bandcamp lists the artists and labels a fan
follows when the fan's page is public. YouTube keeps subscriptions private, so it isn't offered.
Nothing is saved here: the table shows the list, and you choose what to add and press Save.
"""
import json
import re
from urllib.parse import parse_qsl, quote, urlparse

from bs4 import BeautifulSoup

from . import db, lookup, pace, refresh
from .sources import SOURCES
from .sources.base import SourceError

BC_FOLLOWING_API = "https://bandcamp.com/api/fancollection/1/following_bands"
MAX = 5000                                     # most follows read from one account


class FollowsError(Exception):
    pass


def _tracked(source):
    return {r["handle"] for r in db.query("SELECT handle FROM accounts WHERE source=?", (source,))}


async def soundcloud(user: str) -> list:
    """[{name, handle, url}] for every profile a SoundCloud account follows."""
    me = (user or "").strip().rstrip("/")
    if "soundcloud.com" in me.lower():
        me = urlparse(me if "://" in me else "https://" + me).path.strip("/").split("/")[0]
    me = me.lstrip("@").lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,100}", me):
        raise FollowsError("That doesn't look like a SoundCloud username.")
    sc, out = SOURCES["soundcloud"], []
    async with refresh.make_client() as client:
        try:
            account = await sc._api(client, "/resolve", {"url": f"https://soundcloud.com/{me}"})
        except SourceError:
            raise FollowsError(f'No SoundCloud account "{me}" was found.')
        if account.get("kind") != "user" or not account.get("id"):
            raise FollowsError(f'"{me}" is not a SoundCloud profile.')
        path, params = f"/users/{account['id']}/followings", {"limit": 200, "linked_partitioning": 1}
        while len(out) < MAX:
            data = await sc._api(client, path, params)
            for u in data.get("collection") or []:
                handle = (u.get("permalink") or "").lower()
                if handle and re.fullmatch(r"[a-z0-9_-]+", handle):
                    out.append({"name": (u.get("username") or handle).strip(), "handle": handle,
                                "url": f"https://soundcloud.com/{handle}"})
            nxt = data.get("next_href")
            if not nxt:
                break
            page = urlparse(nxt)
            path, params = page.path, {k: v for k, v in parse_qsl(page.query) if k != "client_id"}
    return out


def _band(item) -> dict:
    """{name, handle, url} for one followed artist or label, or None."""
    if not isinstance(item, dict):
        return None
    hints = item.get("url_hints") if isinstance(item.get("url_hints"), dict) else {}
    url = item.get("url") or item.get("band_url") or ""
    if hints.get("custom_domain"):
        url = "https://" + hints["custom_domain"]
    elif hints.get("subdomain"):
        url = f"https://{hints['subdomain']}.bandcamp.com"
    if not url:
        return None
    try:
        handle, url = SOURCES["bandcamp"].normalize(url)
    except SourceError:
        return None
    return {"name": (item.get("name") or item.get("band_name") or handle).strip(), "handle": handle, "url": url}


def _band_list(data) -> list:
    """The followed artists and labels in one answer, whatever Bandcamp calls the list."""
    for key in ("followeers", "followeds", "following_bands", "items", "bands"):
        if isinstance(data.get(key), list):
            return data[key]
    return []


async def bandcamp(fan: str) -> list:
    """[{name, handle, url}] for every artist and label a Bandcamp fan follows (the fan page must be public)."""
    fan = (fan or "").strip().rstrip("/")
    if "bandcamp.com/" in fan:
        fan = urlparse(fan if "://" in fan else "https://" + fan).path.strip("/").split("/")[0]
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", fan):
        raise FollowsError("That doesn't look like a Bandcamp username.")
    out, seen = [], set()

    def add(items):
        for it in items:
            band = _band(it)
            if band and band["handle"] not in seen:
                seen.add(band["handle"])
                out.append(band)

    with pace.foreground():
        async with refresh.make_client() as client:
            net = lookup.Net(client)
            r = await net.get(f"https://bandcamp.com/{quote(fan)}")
            if r.status_code == 404:
                raise FollowsError(f'No Bandcamp fan page called "{fan}".')
            if r.status_code != 200:
                raise FollowsError(f"Bandcamp answered {r.status_code} for that fan page.")
            holder = BeautifulSoup(r.text, "lxml").select_one("#pagedata[data-blob]")
            try:
                blob = json.loads(holder["data-blob"]) if holder else {}
            except ValueError:
                blob = {}
            fan_id = (blob.get("fan_data") or {}).get("fan_id")
            if not fan_id:
                raise FollowsError("Couldn't read that fan page (is it public?).")
            info = blob.get("following_bands_data") or {}
            cache = (blob.get("item_cache") or {}).get("following_bands") or {}
            add([cache[k] for k in info.get("sequence") or list(cache) if k in cache])
            token = info.get("last_token") if (cache or info) else "9999999999:9999999999"
            asked = set()
            while token and token not in asked and len(out) < MAX:
                asked.add(token)
                j = await net.request("POST", BC_FOLLOWING_API, json={"fan_id": fan_id, "older_than_token": token, "count": 100})
                if j.status_code != 200:
                    if out:
                        break                          # keep what was read
                    raise FollowsError(f"Bandcamp answered {j.status_code} while reading who {fan} follows.")
                data = j.json()
                items = _band_list(data)
                add(items)
                token = data.get("last_token") if data.get("more_available") and items else None
    return out


async def read(site: str, user: str) -> dict:
    """The follows of one account, each marked when its page is already tracked."""
    if site == "soundcloud":
        found = await soundcloud(user)
    elif site == "bandcamp":
        found = await bandcamp(user)
    else:
        raise FollowsError("Follows can be read from SoundCloud or Bandcamp.")
    tracked = _tracked(site)
    return {"site": site, "items": [dict(f, tracked=f["handle"] in tracked) for f in found]}


def defaults() -> dict:
    """The usernames already given elsewhere in the app (Settings, Link Finder), to start from."""
    return {"soundcloud": lookup.soundcloud_me(), "bandcamp": lookup.collection_fan()}
