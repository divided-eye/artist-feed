"""SoundCloud: the same JSON endpoints the soundcloud.com site itself calls.

SoundCloud's RSS feeds only contain tracks an artist opted in, and are empty
for most musicians, so they are not used. The site's own API needs a public
"client_id" that is embedded in the site's JavaScript; it is looked up
automatically, cached, and looked up again when it stops working. Set
SOUNDCLOUD_CLIENT_ID to supply your own instead.
"""
import os
import re
from datetime import datetime
from urllib.parse import urlparse

import httpx

from .. import db
from .base import FetchResult, Post, Source, SourceError, Track, iso

API = "https://api-v2.soundcloud.com"
LIMIT = int(os.environ.get("SOUNDCLOUD_LIMIT", "30"))
MAX_PAGES = 4          # most pages read from one list in one visit
SCRIPT_RE = re.compile(r"https://a-v2\.sndcdn\.com/assets/[A-Za-z0-9._-]+\.js")
CLIENT_ID_RE = re.compile(r"""client_id["']?\s*[:=]\s*["']([A-Za-z0-9]{32})["']""")
RESERVED = {"discover", "stream", "search", "you", "upload", "charts", "pages", "settings"}


def _date(text):
    if not text:
        return None
    try:
        return iso(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        try:
            return iso(datetime.strptime(text, "%Y/%m/%d %H:%M:%S %z"))
        except ValueError:
            return None


def _big(url):
    return url.replace("-large.", "-t500x500.") if url else None


def parse_tracks(payload: dict) -> list:
    posts = []
    for t in payload.get("collection") or []:
        if not t.get("permalink_url") or t.get("kind", "track") != "track":
            continue
        user = t.get("user") or {}
        pub = t.get("publisher_metadata") or {}
        ms = t.get("full_duration") or t.get("duration")
        posts.append(Post(
            external_id=str(t.get("id") or t["permalink_url"]),
            title=t.get("title") or "Untitled",
            url=t["permalink_url"],
            image=_big(t.get("artwork_url") or user.get("avatar_url")),
            kind="track",
            author=user.get("username"),
            published_at=_date(t.get("display_date") or t.get("created_at")),
            posted_at=_date(t.get("created_at")),
            label=(t.get("label_name") or "").strip() or None,
            release=(pub.get("album_title") or pub.get("release_title") or "").strip() or None,
            tracks=[Track(t.get("title") or "Untitled", (ms / 1000) if ms else None,
                          ext_id=str(t["id"]) if t.get("id") else None)]))
    return posts


class SoundCloud(Source):
    key = "soundcloud"
    label = "SoundCloud"
    color = "#ff5500"
    placeholder = "username or https://soundcloud.com/username"

    def normalize(self, text):
        text = text.strip().rstrip("/")
        if not text:
            raise SourceError("Enter a SoundCloud username or address.")
        if "soundcloud.com" in text:
            if "://" not in text:
                text = "https://" + text
            path = urlparse(text).path.strip("/")
            handle = path.split("/")[0] if path else ""
        else:
            handle = text.lstrip("@")
        handle = handle.lower()
        if not re.fullmatch(r"[a-z0-9_-]+", handle) or handle in RESERVED:
            raise SourceError("That doesn't look like a SoundCloud profile.")
        return handle, f"https://soundcloud.com/{handle}"

    async def _client_id(self, client, force=False):
        fixed = os.environ.get("SOUNDCLOUD_CLIENT_ID")
        if fixed:
            return fixed
        cached = db.kv_get("soundcloud_client_id")
        if cached and not force:
            return cached
        home = await client.get("https://soundcloud.com/")
        home.raise_for_status()
        for script in reversed(SCRIPT_RE.findall(home.text)):
            js = await client.get(script)
            found = CLIENT_ID_RE.search(js.text) if js.status_code == 200 else None
            if found:
                db.kv_set("soundcloud_client_id", found.group(1))
                return found.group(1)
        raise SourceError("Couldn't get a SoundCloud key from soundcloud.com "
                          "(set SOUNDCLOUD_CLIENT_ID to supply one).")

    async def _api(self, client, path, params):
        headers = {"Accept": "application/json", "Origin": "https://soundcloud.com",
                   "Referer": "https://soundcloud.com/"}
        for attempt in (0, 1):
            cid = await self._client_id(client, force=bool(attempt))
            if path.startswith("http"):                 # a next_href: keep its own query (cursor etc.)
                url = httpx.URL(path)
                params = {**dict(url.params), **params}
                path = str(url.copy_with(query=None)).removeprefix(API)
            r = await client.get(API + path, params={**params, "client_id": cid}, headers=headers)
            if r.status_code in (401, 403) and attempt == 0 and not os.environ.get("SOUNDCLOUD_CLIENT_ID"):
                continue  # key expired: fetch a fresh one and retry once
            if r.status_code == 404:
                raise SourceError("SoundCloud profile not found (check the username).")
            if r.status_code == 429:
                raise SourceError("SoundCloud is rate-limiting this address; it will retry next refresh.")
            r.raise_for_status()
            return r.json()

    async def fetch(self, client, account, known_ids):
        meta = account["meta"]
        result = FetchResult()
        if not meta.get("user_id"):
            user = await self._api(client, "/resolve", {"url": account["url"]})
            if user.get("kind") != "user" or not user.get("id"):
                raise SourceError("That address is not a SoundCloud profile.")
            meta["user_id"] = user["id"]
            result.display_name = user.get("username")
            result.avatar = _big(user.get("avatar_url"))
        uid = meta["user_id"]
        seen, posts = set(), []

        def add(found):
            for p in found:
                if p.external_id not in seen:
                    seen.add(p.external_id)
                    posts.append(p)

        # The Tracks list. SoundCloud leaves out some tracks after choosing a page, so a page can
        # come back short (or empty) with more after it: keep going until there are enough.
        data = await self._api(client, f"/users/{uid}/tracks", {"limit": LIMIT, "linked_partitioning": 1})
        add(parse_tracks(data))
        short = bool(data.get("next_href"))
        result.trace.append(f"Tracks list: {len(data.get('collection') or [])} items"
                            + (", more pages" if short else ""))
        pages = 1
        while len(posts) < LIMIT and data.get("next_href") and pages < MAX_PAGES:
            data = await self._api(client, data["next_href"], {})
            add(parse_tracks(data))
            pages += 1
            result.trace.append(f"Tracks list page {pages}: {len(data.get('collection') or [])} items")
        if len(posts) < LIMIT and (short or not posts):
            # The profile's "All" tab lists its own uploads (and reposts, skipped) even when the
            # Tracks list leaves them out.
            stream, pages = {"next_href": None}, 0
            while len(posts) < LIMIT and pages < MAX_PAGES and (pages == 0 or stream.get("next_href")):
                stream = await self._api(client, stream["next_href"] or f"/stream/users/{uid}",
                                         {} if pages else {"limit": LIMIT, "linked_partitioning": 1})
                items = stream.get("collection") or []
                own = [i["track"] for i in items if i.get("type") == "track" and i.get("track")]
                before = len(posts)
                add(parse_tracks({"collection": own}))
                pages += 1
                result.trace.append(f"All tab page {pages}: {len(items)} items, {len(own)} of them its own tracks, "
                                    f"{len(posts) - before} not already found")
        result.posts = posts
        if not result.posts:
            user = await self._api(client, f"/users/{uid}", {})
            count = user.get("track_count") or 0
            result.trace.append(f"Profile ({user.get('permalink')}, id {uid}) says it has {count} tracks")
            if count:
                result.warning = (f"SoundCloud says this profile has {count} tracks but didn't list any; "
                                  "it will try again next refresh.")
        if result.posts and not result.display_name:
            result.display_name = result.posts[0].author
        return result
