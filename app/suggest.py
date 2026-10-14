"""Filling in an artist's missing Bandcamp or SoundCloud page for the Tracked Artists table.

Two kinds of evidence, strongest first:
  * a link from the page that is known (Bandcamp pages list their other sites; SoundCloud profiles
    list theirs, or mention them in the description)
  * a page whose own name matches the artist's name
Whatever is found is only a suggestion: the table marks it for review and nothing is saved until
the user saves.
"""
import re

from bs4 import BeautifulSoup

from . import lookup, pace, refresh
from .sources import SOURCES, SourceError

SC_LINK = re.compile(r"https?://(?:www\.|m\.)?soundcloud\.com/([A-Za-z0-9_-]+)/?(?=$|[\s\"'<?#)])")
BC_LINK = re.compile(r"(?:https?://)?([a-z0-9][a-z0-9-]*\.bandcamp\.com)\b", re.I)
SC_RESERVED = {"discover", "you", "stream", "search", "upload", "pages", "terms-of-use", "charts", "tags", "player"}


def _first(cell: str) -> str:
    return next((p.strip() for p in re.split(r"[,;\n]+", cell or "") if p.strip()), "")


async def _bandcamp_page(net, base):
    """(page name, SoundCloud handle linked from it or "") for a Bandcamp page."""
    r = await net.get(base + "/music")
    if r.status_code != 200:
        return "", ""
    soup = BeautifulSoup(r.text, "lxml")
    name = ""
    meta = soup.find("meta", attrs={"property": "og:site_name"}) or soup.find("meta", attrs={"property": "og:title"})
    if meta and meta.get("content"):
        name = meta["content"].strip()
    title = soup.select_one("#band-name-location .title")
    if title and title.get_text(strip=True):
        name = title.get_text(" ", strip=True)
    links = [a.get("href") or "" for a in soup.select("#band-links a[href]")] or \
            [a.get("href") or "" for a in soup.select("a[href*='soundcloud.com']")]
    for href in links:
        m = SC_LINK.search(href.strip() + " ")
        if m and m.group(1).lower() not in SC_RESERVED:
            return name, m.group(1).lower()
    return name, ""


async def _soundcloud_user(client, handle):
    sc = SOURCES["soundcloud"]
    try:
        user = await sc._api(client, "/resolve", {"url": f"https://soundcloud.com/{handle}"})
    except Exception:
        return None
    return user if isinstance(user, dict) and user.get("id") else None


async def _bandcamp_from_soundcloud(client, user):
    """A Bandcamp page the SoundCloud profile itself points to."""
    texts = [user.get("description") or "", user.get("website") or ""]
    try:
        profiles = await SOURCES["soundcloud"]._api(client, f"/users/soundcloud:users:{user['id']}/web-profiles", {})
        texts += [p.get("url") or "" for p in profiles if isinstance(p, dict)]
    except Exception:
        pass
    for text in texts:
        m = BC_LINK.search(text)
        if m:
            return m.group(1).lower()
    return ""


async def _soundcloud_by_name(client, name):
    names = lookup.artist_parts(name)[:1]
    if not names:
        return ""
    try:
        data = await SOURCES["soundcloud"]._api(client, "/search/users", {"q": name, "limit": 20})
    except Exception:
        return ""
    best = None
    for user in data.get("collection") or []:
        fit = lookup._is_profile_of(user, names)
        if fit[0] and user.get("permalink"):
            rank = (fit[1], user.get("followers_count") or 0)
            if best is None or rank > best[0]:
                best = (rank, user["permalink"].lower())
    return best[1] if best else ""


async def _bandcamp_by_name(net, name):
    whole = lookup.artist_parts(name)[:1]
    if not whole:
        return ""
    best = None
    for c in await lookup.bandcamp_search(net, name, "b"):
        sim = lookup.name_sim(whole[0], lookup.norm(c["name"]))
        if c["type"] in ("artist", "label", "") and sim >= 0.9 and (best is None or sim > best[0]):
            best = (sim, lookup.root_of(c["url"]))
    if best:
        return best[1].replace("https://", "")
    squashed = whole[0].replace(" ", "")
    if re.fullmatch(r"[a-z0-9]{3,}", squashed):          # the obvious address, kept only if its name matches
        try:
            grid = await lookup.get_grid(net, f"https://{squashed}.bandcamp.com")
        except lookup.Blocked:
            raise
        except Exception:
            grid = None
        if grid and lookup.name_sim(whole[0], lookup.norm(grid["name"])) >= 0.9:
            return f"{squashed}.bandcamp.com"
    return ""


async def suggest(name: str, cells: dict) -> dict:
    """What could fill this row's empty cells: {"name"|"bandcamp"|"soundcloud": {"value", "how"}}."""
    name = (name or "").strip()
    have = {"bandcamp": "", "soundcloud": ""}
    for key in have:
        try:
            have[key] = SOURCES[key].normalize(_first(cells.get(key) or ""))[0] if _first(cells.get(key) or "") else ""
        except SourceError:
            return {}                                     # a cell that doesn't parse: leave the row to the user
    if (have["bandcamp"] and have["soundcloud"] and name) or not (name or have["bandcamp"] or have["soundcloud"]):
        return {}
    out, sc_user = {}, None
    with pace.foreground():
        async with refresh.make_client() as client:
            net = lookup.Net(client)
            try:
                if have["bandcamp"] and not (have["soundcloud"] and name):
                    page_name, linked = await _bandcamp_page(net, f"https://{have['bandcamp']}")
                    if not name and page_name:
                        name = page_name
                        out["name"] = {"value": page_name, "how": "the name on their Bandcamp page"}
                    if linked and not have["soundcloud"]:
                        have["soundcloud"] = linked
                        out["soundcloud"] = {"value": linked, "how": "linked from their Bandcamp page"}
                if have["soundcloud"] and not (have["bandcamp"] and name):
                    sc_user = await _soundcloud_user(client, have["soundcloud"])
                    if sc_user and not name and sc_user.get("username"):
                        name = sc_user["username"].strip()
                        out["name"] = {"value": name, "how": "the name on their SoundCloud profile"}
                if not have["soundcloud"] and name:
                    found = await _soundcloud_by_name(client, name)
                    if found:
                        have["soundcloud"] = found
                        out["soundcloud"] = {"value": found, "how": "a SoundCloud profile with this name"}
                        sc_user = await _soundcloud_user(client, found)
                if not have["bandcamp"]:
                    linked = await _bandcamp_from_soundcloud(client, sc_user) if sc_user else ""
                    if linked:
                        out["bandcamp"] = {"value": linked, "how": "linked from their SoundCloud profile"}
                    elif name:
                        found = await _bandcamp_by_name(net, name)
                        if found:
                            out["bandcamp"] = {"value": found, "how": "a Bandcamp page with this name"}
            except lookup.Blocked:
                out["warning"] = "Bandcamp is rate-limiting requests; try the rest again in a few minutes."
    return out
