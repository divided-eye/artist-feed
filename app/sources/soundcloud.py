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
MAX_PAGES = 4          # most pages with tracks on them read from one list in one visit
MAX_EMPTY_PAGES = 25   # most pages read in all, empty ones included
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


def _next_note(data):
    """For app.check: where SoundCloud's next page starts (its "offset" is often a date)."""
    href = data.get("next_href")
    if not href:
        return ""
    params = {k: v for k, v in httpx.URL(href).params.items() if k not in ("client_id", "limit", "linked_partitioning")}
    return ", more pages" + (f" (next from {', '.join(f'{k}={v}' for k, v in params.items())})" if params else "")


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

    async def _filled(self, client, items, trace, where):
        """SoundCloud sometimes lists a track as just its number (no title or link), most often for
        older ones: look those up by number so they aren't dropped."""
        full = [t for t in items if t.get("permalink_url")]
        bare = [t["id"] for t in items if not t.get("permalink_url") and t.get("id")
                and t.get("kind", "track") == "track"]
        if bare:
            got = []
            for i in range(0, len(bare), 50):
                found = await self._api(client, "/tracks", {"ids": ",".join(str(x) for x in bare[i:i + 50])})
                got += found if isinstance(found, list) else found.get("collection") or []
            full += [t for t in got if t.get("permalink_url")]
            trace.append(f"{where}: {len(bare)} listed without details, {len(got)} of them looked up")
        return {"collection": full}

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
        # come back short, or empty, with more after it. Empty pages are cheap, so more of them are
        # followed than full ones.
        data = await self._api(client, f"/users/{uid}/tracks", {"limit": LIMIT, "linked_partitioning": 1})
        add(parse_tracks(await self._filled(client, data.get("collection") or [], result.trace, "Tracks list")))
        short = bool(data.get("next_href"))
        result.trace.append(f"Tracks list: {len(data.get('collection') or [])} items" + _next_note(data))
        pages, full = 1, int(bool(data.get("collection")))
        while len(posts) < LIMIT and data.get("next_href") and full < MAX_PAGES and pages < MAX_EMPTY_PAGES:
            data = await self._api(client, data["next_href"], {})
            pages += 1
            full += bool(data.get("collection"))
            add(parse_tracks(await self._filled(client, data.get("collection") or [], result.trace,
                                                f"Tracks list page {pages}")))
            result.trace.append(f"Tracks list page {pages}: {len(data.get('collection') or [])} items" + _next_note(data))
        if len(posts) < LIMIT and (short or not posts):
            # The profile's "All" tab: its uploads, and reposts (kept only when they are its own tracks).
            stream, pages = {"next_href": None}, 0
            while len(posts) < LIMIT and pages < MAX_PAGES and (pages == 0 or stream.get("next_href")):
                stream = await self._api(client, stream["next_href"] or f"/stream/users/{uid}",
                                         {} if pages else {"limit": LIMIT, "linked_partitioning": 1})
                items = stream.get("collection") or []
                own = [i["track"] for i in items if i.get("track") and (
                    i.get("type") == "track" or str((i["track"].get("user") or {}).get("id") or i["track"].get("user_id")) == str(uid))]
                kinds = {}
                for i in items:
                    kinds[i.get("type") or "?"] = kinds.get(i.get("type") or "?", 0) + 1
                before = len(posts)
                pages += 1
                add(parse_tracks(await self._filled(client, own, result.trace, f"All tab page {pages}")))
                result.trace.append(f"All tab page {pages}: {len(items)} items "
                                    f"({', '.join(f'{n} {k}' for k, n in kinds.items()) or 'none'}), "
                                    f"{len(own)} of them its own tracks, {len(posts) - before} not already found")
        if len(posts) < LIMIT and (short or not posts):
            # The "Popular tracks" tab: its most played uploads, old ones included.
            try:
                top = await self._api(client, f"/users/{uid}/toptracks", {"limit": 50})
            except (SourceError, httpx.HTTPError) as e:      # an extra: never worth failing the page over
                top = {}
                result.trace.append(f"Popular tracks: couldn't read ({e})")
            items = top.get("collection") or []
            before = len(posts)
            add(parse_tracks(await self._filled(client, items, result.trace, "Popular tracks")))
            result.trace.append(f"Popular tracks: {len(items)} items, {len(posts) - before} not already found")
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
