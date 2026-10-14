"""Bandcamp: read an artist's public /music page, then each new release page.

Bandcamp has no public API or artist RSS feed. The /music grid lists releases
but not dates, so each release that has not been seen before is opened once to
read its release date from the page's embedded metadata (JSON-LD).
"""
import asyncio
import json
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import FetchResult, Post, Source, SourceError, Track, iso

# Release pages opened per account per refresh. A long back catalogue fills in
# over the next few refreshes instead of hammering the site on day one.
MAX_NEW = int(os.environ.get("BANDCAMP_MAX_NEW", "12"))
PAUSE = float(os.environ.get("BANDCAMP_PAUSE_SECONDS", "0.7"))


def _clean(url: str) -> str:
    return url.split("?")[0].split("#")[0].rstrip("/")


def _kind(url: str) -> str:
    return "track" if "/track/" in url else "album"


def _meta(soup, prop):
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    return tag.get("content") if tag and tag.get("content") else None


def _parse_date(text):
    if not text:
        return None
    try:
        return iso(parsedate_to_datetime(text))          # "07 Mar 2025 00:00:00 GMT"
    except (TypeError, ValueError):
        pass
    try:
        return iso(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _seconds(value):
    """Seconds from a number or an ISO 8601 duration such as "P00H03M45S"."""
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", str(value or ""))
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (float(x) if x else 0 for x in m.groups())
    return (d * 86400 + h * 3600 + mi * 60 + s) or None


def _name(thing):
    return thing.get("name") if isinstance(thing, dict) else None


def _json_attr(soup, attr):
    holder = soup.find(attrs={attr: True})
    if not holder:
        return {}
    try:
        data = json.loads(holder[attr])
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def parse_profile(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    name = _meta(soup, "og:site_name")
    if not name:
        el = soup.select_one("#band-name-location .title")
        name = el.get_text(strip=True) if el else None
    photo = soup.select_one("img.band-photo")
    return {"name": name, "avatar": photo.get("src") if photo else None}


def parse_music_grid(html: str, base: str) -> list:
    """Releases listed on /music, in page order. Each: url, title, image, author."""
    soup = BeautifulSoup(html, "lxml")
    grid = soup.select_one("#music-grid")
    if grid is None:
        return []
    items, seen = [], set()

    def add(url, title, image, author):
        url = _clean(urljoin(base + "/", url))
        if url in seen or not re.search(r"/(album|track)/", url):
            return
        seen.add(url)
        items.append({"url": url, "title": title or url.rsplit("/", 1)[-1],
                      "image": image, "author": author})

    for li in grid.select("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        title, author = None, None
        t = li.select_one(".title")
        if t:
            override = t.select_one(".artist-override")
            if override:
                author = override.get_text(" ", strip=True)
                override.extract()
            title = t.get_text(" ", strip=True)
        img = li.find("img")
        image = None
        if img:
            image = img.get("data-original") or img.get("src")
            if image and image.endswith("0.gif"):
                image = None
        add(a["href"], title, image, author)

    # Big discographies keep the rest of the grid in a JSON attribute.
    raw = grid.get("data-client-items")
    if raw:
        try:
            for it in json.loads(raw):
                image = (f"https://f4.bcbits.com/img/a{it['art_id']}_2.jpg"
                         if it.get("art_id") else None)
                if it.get("page_url"):
                    add(it["page_url"], it.get("title"), image, it.get("artist"))
        except (ValueError, TypeError, KeyError):
            pass
    return items


def parse_release(html: str, url: str) -> dict:
    """Title, date, cover and artist from a single album/track page."""
    soup = BeautifulSoup(html, "lxml")
    out = {"url": _clean(url), "title": None, "image": None, "author": None,
           "published_at": None, "is_release": False,
           "label": None, "release": None, "embed": None, "tracks": None}
    ld_tracks, publisher = [], None

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            ld = json.loads(script.string or "")
        except ValueError:
            continue
        if not isinstance(ld, dict):
            continue
        if ld.get("@type") in ("MusicAlbum", "MusicRecording"):
            out["is_release"] = True
        out["title"] = ld.get("name") or out["title"]
        out["published_at"] = _parse_date(ld.get("datePublished")) or out["published_at"]
        image = ld.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        out["image"] = image or out["image"]
        by = ld.get("byArtist")
        if isinstance(by, dict):
            out["author"] = by.get("name")
        if isinstance(ld.get("@id"), str) and ld["@id"].startswith("http"):
            out["url"] = _clean(ld["@id"])
        publisher = _name(ld.get("publisher")) or publisher
        out["label"] = _name(ld.get("recordLabel")) or out["label"]
        out["release"] = _name(ld.get("inAlbum")) or out["release"]
        listed = ld.get("track")
        for entry in (listed.get("itemListElement") or []) if isinstance(listed, dict) else []:
            item = entry.get("item") if isinstance(entry, dict) else None
            if isinstance(item, dict) and item.get("name"):
                ld_tracks.append(Track(item["name"], _seconds(item.get("duration")),
                                       url=item.get("@id") if isinstance(item.get("@id"), str) else None))
        if ld.get("@type") == "MusicRecording" and not ld_tracks and ld.get("name"):
            ld_tracks.append(Track(ld["name"], _seconds(ld.get("duration"))))

    # The player's own data: track list with durations, and the ids its embed needs.
    tralbum = _json_attr(soup, "data-tralbum")
    current = tralbum.get("current") if isinstance(tralbum.get("current"), dict) else {}
    tracks = []
    for t in tralbum.get("trackinfo") or []:
        if not isinstance(t, dict) or not t.get("title"):
            continue
        ext = t.get("track_id") or t.get("id")
        link = t.get("title_link")
        tracks.append(Track(t["title"], _seconds(t.get("duration")), t.get("artist") or None,
                            str(ext) if ext else None,
                            _clean(urljoin(url, link)) if link else None))
    if tracks or ld_tracks:
        out["tracks"] = tracks or ld_tracks

    props = {}
    tag = soup.find("meta", attrs={"name": "bc-page-properties"})
    if tag and tag.get("content"):
        try:
            props = json.loads(tag["content"])
        except ValueError:
            props = {}
    item_id = props.get("item_id") or tralbum.get("id") or current.get("id")
    item_type = str(props.get("item_type") or tralbum.get("item_type") or current.get("type") or "")
    if item_id and item_type[:1] in ("a", "t"):
        out["embed"] = f"{'album' if item_type[0] == 'a' else 'track'}={item_id}"
    else:
        m = re.search(r"EmbeddedPlayer/(?:v=2/)?(album|track)=(\d+)", _meta(soup, "og:video") or "")
        if m:
            out["embed"] = f"{m.group(1)}={m.group(2)}"
    if tralbum:
        out["is_release"] = True

    if not out["published_at"]:
        # Older fallback: the player's data blob.
        holder = soup.find(attrs={"data-tralbum": True})
        if holder:
            try:
                current = json.loads(holder["data-tralbum"]).get("current", {})
                out["published_at"] = _parse_date(
                    current.get("release_date") or current.get("publish_date"))
                out["is_release"] = True
            except (ValueError, AttributeError):
                pass
    out["title"] = out["title"] or _meta(soup, "og:title")
    out["image"] = out["image"] or _meta(soup, "og:image")
    out["author"] = out["author"] or tralbum.get("artist") or None
    # A release on someone else's page (a label's) is credited to that page.
    if not out["label"] and publisher and out["author"] \
            and publisher.strip().lower() != out["author"].strip().lower():
        out["label"] = publisher
    if not out["release"]:
        out["release"] = out["title"]      # an album, or a stand-alone single
    return out


class Bandcamp(Source):
    key = "bandcamp"
    label = "Bandcamp"
    color = "#1da0c3"
    placeholder = "artistname or https://artistname.bandcamp.com"

    def short(self, handle):
        return handle[:-len(".bandcamp.com")] if handle.endswith(".bandcamp.com") else handle

    def normalize(self, text):
        text = text.strip().rstrip("/")
        if not text:
            raise SourceError("Enter a Bandcamp name or address.")
        if "://" not in text:
            text = "https://" + (text if "." in text else f"{text}.bandcamp.com")
        parts = urlparse(text)
        host = (parts.netloc or "").lower()
        if not host or " " in host:
            raise SourceError("That doesn't look like a Bandcamp address.")
        if host in ("bandcamp.com", "www.bandcamp.com"):
            raise SourceError("Use the artist's own page, e.g. artistname.bandcamp.com.")
        return host, f"https://{host}"

    async def _get(self, client, url):
        r = await client.get(url)
        if r.status_code == 404:
            raise SourceError("Bandcamp page not found (check the name).")
        if r.status_code == 429:
            raise SourceError("Bandcamp is rate-limiting this address; it will retry next refresh.")
        r.raise_for_status()
        return r

    async def fetch(self, client, account, known_ids):
        base = account["url"]
        r = await self._get(client, base + "/music")
        profile = parse_profile(r.text)
        result = FetchResult(display_name=profile["name"], avatar=profile["avatar"])
        items = parse_music_grid(r.text, base)

        if not items:
            # Artists with a single release: /music shows that release directly.
            rel = parse_release(r.text, str(r.url))
            if not rel["is_release"]:
                raise SourceError("No releases found on this Bandcamp page.")
            result.posts.append(Post(rel["url"], rel["title"] or "Untitled", rel["url"],
                                     rel["image"], _kind(rel["url"]), rel["author"],
                                     rel["published_at"], rel["label"], rel["release"],
                                     rel["embed"], rel["tracks"]))
            return result

        new = [it for it in items if it["url"] not in known_ids]
        for i, it in enumerate(new[:MAX_NEW]):
            if i:
                await asyncio.sleep(PAUSE)
            try:
                page = await self._get(client, it["url"])
                rel = parse_release(page.text, it["url"])
            except Exception:
                continue  # try again next refresh
            result.posts.append(Post(
                external_id=it["url"], url=it["url"],
                title=rel["title"] or it["title"],
                image=rel["image"] or it["image"],
                kind=_kind(it["url"]),
                author=rel["author"] or it["author"],
                published_at=rel["published_at"],
                label=rel["label"], release=rel["release"] or rel["title"] or it["title"],
                embed=rel["embed"], tracks=rel["tracks"]))
        left = len(new) - len(result.posts)
        if left > 0:
            result.warning = f"{left} older release(s) still to load; they fill in over the next refreshes."
        return result
