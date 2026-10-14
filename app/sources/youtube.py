"""YouTube: a channel's newest uploads, from the feed YouTube publishes for every channel.

The feed (youtube.com/feeds/videos.xml?channel_id=...) needs no key and lists a channel's 15
newest videos. It is addressed by the channel's id ("UC..."), so a channel given by its @handle
or an old /c/ or /user/ address is opened once to read its id, which is then remembered.
The feed gives no running times, so YouTube posts have no duration.
"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlparse, unquote

from .base import FetchResult, Post, Source, SourceError, Track, iso

FEED = "https://www.youtube.com/feeds/videos.xml"
# Answers the cookie question YouTube asks visitors from some countries, so the channel page itself comes back.
COOKIES = {"Cookie": "SOCS=CAI; CONSENT=YES+1"}   # sent as a header
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
ID_ON_PAGE = (re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{22})"'),
              re.compile(r'<meta itemprop="(?:identifier|channelId)" content="(UC[A-Za-z0-9_-]{22})"'),
              re.compile(r'"externalId":"(UC[A-Za-z0-9_-]{22})"'),
              re.compile(r'"browseId":"(UC[A-Za-z0-9_-]{22})"'))
RESERVED = {"watch", "results", "feed", "playlist", "shorts", "embed", "live", "gaming", "music", "premium",
            "account", "about", "t", "hashtag", "channel", "c", "user", "redirect"}


def _date(text):
    if not text:
        return None
    try:
        return iso(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def parse_feed(xml_text: str) -> tuple:
    """(channel name, [Post]) from a channel's video feed."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        raise SourceError("YouTube sent something that isn't a channel feed.")
    channel = (root.findtext("a:title", "", NS) or "").strip() or None
    posts = []
    for e in root.findall("a:entry", NS):
        vid = (e.findtext("yt:videoId", "", NS) or "").strip()
        if not vid:
            continue
        title = (e.findtext("a:title", "", NS) or "").strip() or "Untitled"
        link = e.find("a:link", NS)
        url = (link.get("href") if link is not None else "") or f"https://www.youtube.com/watch?v={vid}"
        thumb = e.find("media:group/media:thumbnail", NS)
        when = _date(e.findtext("a:published", "", NS))
        author = (e.findtext("a:author/a:name", "", NS) or "").strip() or channel
        posts.append(Post(external_id=vid, title=title, url=url,
                          image=(thumb.get("url") if thumb is not None else None) or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                          kind="track", author=author, published_at=when, posted_at=when, embed=vid,
                          tracks=[Track(title, None, ext_id=vid, url=url)]))
    return channel, posts


def channel_id_in(html: str):
    for pattern in ID_ON_PAGE:
        found = pattern.search(html or "")
        if found:
            return found.group(1)
    return None


class YouTube(Source):
    key = "youtube"
    label = "YouTube"
    color = "#ff3b3b"
    placeholder = "@handle or https://www.youtube.com/@handle"

    def normalize(self, text):
        text = (text or "").strip().rstrip("/")
        if not text:
            raise SourceError("Enter a YouTube channel: its @handle or address.")
        if re.search(r"(^|\.|//)(youtube\.com|youtu\.be)\b", text, re.I):
            if "://" not in text:
                text = "https://" + text
            parts = [unquote(p) for p in urlparse(text).path.split("/") if p]
            if not parts:
                raise SourceError("That address isn't a YouTube channel.")
            if parts[0].startswith("@"):
                handle = parts[0]
            elif parts[0] == "channel" and len(parts) > 1:
                handle = parts[1]
            elif parts[0] in ("c", "user") and len(parts) > 1:
                handle = f"{parts[0]}/{parts[1]}"
            elif parts[0].lower() not in RESERVED:
                handle = "c/" + parts[0]                     # youtube.com/SomeName: an old custom address
            else:
                raise SourceError("That's a video or page, not a channel. Use the channel's @handle or address.")
        else:
            handle = text if CHANNEL_ID.match(text) else "@" + text.lstrip("@")
        if CHANNEL_ID.match(handle):
            return handle, f"https://www.youtube.com/channel/{handle}"
        if handle.startswith("@"):
            name = handle[1:]
            if not re.fullmatch(r"[\w.\-·]{1,100}", name):
                raise SourceError("That doesn't look like a YouTube @handle.")
            handle = "@" + name.lower()
            return handle, f"https://www.youtube.com/{handle}"
        kind, _, name = handle.partition("/")
        if not re.fullmatch(r"[\w.\-]{1,100}", name):
            raise SourceError("That doesn't look like a YouTube channel address.")
        return f"{kind}/{name}", f"https://www.youtube.com/{kind}/{name}"

    async def fetch(self, client, account, known_ids):
        meta = account["meta"]
        result = FetchResult()
        cid = meta.get("channel_id") or (account["handle"] if CHANNEL_ID.match(account["handle"]) else None)
        if not cid:
            page = await client.get(account["url"], headers=COOKIES)
            if page.status_code == 404:
                raise SourceError("YouTube channel not found (check the handle).")
            page.raise_for_status()
            cid = channel_id_in(page.text)
            if not cid:
                raise SourceError("Couldn't read this channel's id from its YouTube page.")
            meta["channel_id"] = cid
        r = await client.get(FEED, params={"channel_id": cid}, headers=COOKIES)
        if r.status_code == 404:
            meta.pop("channel_id", None)                     # the channel may have gone, or its id changed
            raise SourceError("YouTube has no channel with this id any more.")
        r.raise_for_status()
        result.display_name, result.posts = parse_feed(r.text)
        return result
