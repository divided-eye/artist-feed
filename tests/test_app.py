"""End-to-end test against stand-in Bandcamp and SoundCloud servers.

The pages below copy the structure of the real sites (music grid, JSON-LD on
release pages, api-v2 JSON). They check the app's logic; they cannot tell you
whether the real sites have changed - use `python -m app.check` for that.
"""
import json
import os
import tempfile
import time

import httpx
import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["DISABLE_SCHEDULER"] = "1"
os.environ["BANDCAMP_PAUSE_SECONDS"] = "0"
os.environ["BANDCAMP_MAX_NEW"] = "2"
os.environ["BANDCAMP_GAP_SECONDS"] = "0"
os.environ["DISABLE_DEEP_READ"] = "1"
os.environ["LOOKUP_FALLBACK"] = "bandcamp"     # the older tests were written with this preference
os.environ["DEEP_READ_EXTRA_SECONDS"] = "0"
os.environ["BANDCAMP_BACKOFF_SECONDS"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import db, refresh  # noqa: E402
from app.main import app  # noqa: E402
from app.sources import SOURCES  # noqa: E402
from app.sources.bandcamp import parse_music_grid, parse_release  # noqa: E402
from app.sources.base import SourceError  # noqa: E402

CID = "a" * 32
GRID = """<html><head><meta property="og:site_name" content="Night Ferry"></head><body>
<img class="band-photo" src="https://f4.bcbits.com/img/0001_21.jpg">
<ol id="music-grid" data-client-items='[{"art_id":333,"id":3,"page_url":"/album/third","title":"Third","type":"album"}]'>
 <li data-item-id="album-1" class="music-grid-item"><a href="/album/first?from=grid">
   <div class="art"><img src="https://f4.bcbits.com/img/a111_2.jpg"></div>
   <p class="title">First</p></a></li>
 <li data-item-id="track-2" class="music-grid-item"><a href="/track/second">
   <div class="art"><img src="/img/0.gif" data-original="https://f4.bcbits.com/img/a222_2.jpg"></div>
   <p class="title">Second <br><span class="artist-override">Guest Person</span></p></a></li>
</ol></body></html>"""


def release(name, date, kind="MusicAlbum", ld_extra=None, head=""):
    ld = {"@type": kind, "name": name, "datePublished": date,
          "image": f"https://f4.bcbits.com/img/{name}_10.jpg", "byArtist": {"name": "Night Ferry"}}
    ld.update(ld_extra or {})
    return f'<html><head>{head}<script type="application/ld+json">{json.dumps(ld)}</script></head></html>'


# An album on a label's page, with the player data Bandcamp embeds in the page.
TRALBUM = {"id": 111, "item_type": "album", "artist": "Night Ferry", "current": {"id": 111, "type": "album", "publish_date": "05 Mar 2025 16:00:00 GMT"},
           "trackinfo": [{"title": "Intro", "duration": 61.5, "track_id": 1001, "title_link": "/track/intro"},
                         {"title": "Long One", "duration": 3725, "track_id": 1002, "title_link": "/track/long-one",
                          "artist": "Guest Person"}]}
FIRST = release("First", "07 Mar 2025 00:00:00 GMT", ld_extra={"publisher": {"name": "Deep Label"}}).replace(
    "</head>", "</head><body><script data-tralbum='" + json.dumps(TRALBUM) + "'></script></body>").replace(
    "<head>", '<head><meta name="bc-page-properties" content=\'{"item_type":"a","item_id":111}\'>')
# A track page with only the JSON-LD and the share-player address to go on.
SECOND = release("Second", "01 Sep 2026 00:00:00 GMT", "MusicRecording",
                 {"duration": "P00H03M45S", "inAlbum": {"name": "First"}, "publisher": {"name": "Night Ferry"}},
                 '<meta property="og:video" content="https://bandcamp.com/EmbeddedPlayer/v=2/track=222/size=large/">')


def result(name, url, sub, released="released 2 May 2023", kind="TRACK"):
    return (f'<li class="searchresult data-search"><div class="result-info"><div class="itemtype">{kind}</div>'
            f'<div class="heading"><a href="{url}?from=search">\n {name}\n</a></div>'
            f'<div class="subhead">\n {sub}\n</div><div class="released">{released}</div>'
            f'<div class="itemurl"><a href="{url}?from=search">{url}</a></div></div></li>')


SEARCHES = {   # what the stand-in Bandcamp search returns for a query
    "Masot Especies Endémicas": result("Especies Endémicas", "https://masot.bandcamp.com/track/especies-end-micas", "from Flora by Masot"),
    "Other Guy Especies Endémicas": result("Especies Endémicas", "https://masot.bandcamp.com/track/especies-end-micas", "from Flora by Masot"),
    "idpa State I": result("Idpa - State I (Original Mix)", "https://somelabel.bandcamp.com/track/state-i", "from VA 004 by Some Label", "released 9 January 2021")
              + result("State Of Mind", "https://x.bandcamp.com/track/state", "by Idpa"),
}
SEARCHES.update({
    "Siadic Air": result("Air", "https://siadic.bandcamp.com/track/air", "from Traction EP by Siadic"),
    "What A World": result("YDX - What A World", "https://dubdenrecords.bandcamp.com/track/ydx-what-a-world",
                           "from Dubs From The Den Vol. 7 by Dub Den Records"),
    ("a", "Allen Mock Dragon Language"): result("Dragon Language", "https://observatory-records.bandcamp.com/album/dragon-language",
                                                "by Allen Mock", "released 3 March 2026", "ALBUM")
                                         + result("Dragon Language", "https://x.bandcamp.com/album/dl", "by Somebody Else", kind="ALBUM"),
    ("b", "somah"): result("Somah UK", "https://somahuk.bandcamp.com", "Bristol, UK", "", "ARTIST")
                    + result("Somahaus", "https://somahaus.bandcamp.com", "Berlin", "", "ARTIST"),
    # a label release: credited to the label, the artist only in the album's name and address
    "Kercha Trigger Activation": result("Trigger Activation", "https://dnorecords.bandcamp.com/track/trigger-activation",
                                        "from Open The Door LP by DNO Records")
                                 + result("Trigger Activation", "https://other.bandcamp.com/track/trigger-activation",
                                          "from Something Else by Other Label"),
    # a label release that only turns up when searching for the artist's name
    ("a", "Howman"): result("DNO022 - Howman - Trap Eastwood EP", "https://dnorecords.bandcamp.com/album/dno022-howman-trap-eastwood-ep",
                            "by DNO Records", "released 28 August 2026", "ALBUM")
                     + result("Unrelated Album", "https://zzz.bandcamp.com/album/unrelated", "by Someone", kind="ALBUM"),
    ("b", "nightmare"): result("Nightmare", "https://nightmare.bandcamp.com", "", "", "ARTIST"),
    ("b", "bbbb cccc"): result("Cccc", "https://ccc.bandcamp.com", "", "", "ARTIST"),
})


def grid(name, *albums):
    items = "".join(f'<li><a href="/album/{slug}"><p class="title">{title}</p></a></li>' for slug, title in albums)
    return f'<html><head><meta property="og:site_name" content="{name}"></head><body><ol id="music-grid">{items}</ol></body></html>'


def album(name, artist, *tracks, date="06 Jun 2026 00:00:00 GMT", extra=None):
    data = {"current": {"type": "album"}, "artist": artist,
            "trackinfo": [{"title": t, "duration": 200, "track_id": i + 1} for i, t in enumerate(tracks)]}
    return release(name, date, ld_extra=dict({"byArtist": {"name": artist}}, **(extra or {}))).replace(
        "</head>", "</head><body><script data-tralbum='" + json.dumps(data) + "'></script></body>")


import html as html_lib  # noqa: E402

FAN_BLOB = {"fan_data": {"fan_id": 77},
            "collection_data": {"sequence": ["a1", "a2"], "item_count": 4, "last_token": "tok1"},
            "item_cache": {"collection": {
                "a1": {"tralbum_type": "a", "tralbum_id": 1, "item_title": "Archivos Pt. 1", "band_name": "Masot",
                       "item_url": "https://masot.bandcamp.com/album/archivos-pt-1", "featured_track": 11},
                "a2": {"tralbum_type": "a", "tralbum_id": 2, "item_title": "WPR091 - Phoenix", "band_name": "Blocklab",
                       "item_url": "https://whitepeach.bandcamp.com/album/wpr091-phoenix", "featured_track": 21}}},
            "tracklists": {"collection": {"a1": [{"id": 11, "title": "01", "track_number": 1}, {"id": 12, "title": "02", "track_number": 2}],
                                          "a2": [{"id": 21, "title": "Phoenix"}]}}}
FAN_MORE = {"items": [{"tralbum_type": "a", "tralbum_id": 3, "item_title": "Especies Endémicas", "band_name": "Masot",
                       "item_url": "https://masot.bandcamp.com/album/especies-endemicas", "featured_track": 31},
                      {"tralbum_type": "a", "tralbum_id": 4, "item_title": "Half Empty", "band_name": "contactghost",
                       "item_url": "https://artikalmusic.bandcamp.com/album/half-empty?from=fanpub"}],
            "tracklists": {"a3": [{"id": 31, "title": "Intro Piece"}]}, "more_available": False, "last_token": "tok2"}
# A track bought on its own: the page names its album only in the player data.
DEAD_ANGLE = release("Dead Angle", "30 Sep 2026 00:00:00 GMT", "MusicRecording",
                     {"byArtist": {"name": "JOHNEY"}, "inAlbum": {"name": "SHKHT013 - JOHNEY"}}).replace(
    "</head>", "</head><body><script data-tralbum='" + json.dumps(
        {"item_type": "track", "album_url": "/album/shkht013-johney",
         "trackinfo": [{"title": "Dead Angle", "duration": 240, "track_id": 77}]}) + "'></script></body>")
PAGES = {
    ("shakhtarecords.bandcamp.com", "/track/dead-angle"): DEAD_ANGLE,
    ("bandcamp.com", "/divided_eye"): '<div id="pagedata" data-blob="' + html_lib.escape(json.dumps(FAN_BLOB)) + '"></div>',
    ("masot.bandcamp.com", "/album/archivos-pt-1"): album("Archivos Pt. 1", "Masot", "01", "02"),
    ("masot.bandcamp.com", "/album/especies-endemicas"): album("Especies Endémicas", "Masot", "Intro Piece", "Especies Endémicas"),
    ("whitepeach.bandcamp.com", "/album/wpr091-phoenix"): album("WPR091 - Phoenix", "Blocklab", "Phoenix", "Gargantua", date="01 Feb 2025 00:00:00 GMT"),
    ("artikalmusic.bandcamp.com", "/album/half-empty"): album("Half Empty", "contactghost", "Half Empty", "Spelling Mitsake"),
    ("siadic.bandcamp.com", "/track/air"): release("Air", "09 Sep 2024 00:00:00 GMT", "MusicRecording", {
        "byArtist": {"name": "Siadic"}, "inAlbum": {"name": "Traction EP", "@id": "https://siadic.bandcamp.com/album/traction-ep"}}),
    ("dubdenrecords.bandcamp.com", "/track/ydx-what-a-world"): release("YDX - What A World", "01 Jan 2026 00:00:00 GMT", "MusicRecording", {
        "byArtist": {"name": "Dub Den Records"}, "inAlbum": {"name": "Dubs From The Den Vol. 7", "@id": "https://dubdenrecords.bandcamp.com/album/dubs-from-the-den-vol-7"}}),
    ("dnorecords.bandcamp.com", "/track/trigger-activation"): release("Trigger Activation", "29 May 2026 00:00:00 GMT", "MusicRecording", {
        "byArtist": {"name": "DNO Records"}, "inAlbum": {"name": "Open The Door LP", "@id": "https://dnorecords.bandcamp.com/album/dno021-kercha-open-the-door-lp"}}),
    # one playable "mini mix"; the real track names are only written out under the buy box
    ("quantumsoul.bandcamp.com", "/music"): grid("Quantum Soul", ("evolution-part-1", "Evolution Part 1"), ("older-ep", "Older EP")),
    ("quantumsoul.bandcamp.com", "/album/older-ep"): album("Older EP", "Quantum Soul", "One", "Two"),
    ("quantumsoul.bandcamp.com", "/album/evolution-part-1"): album("Evolution Part 1", "Quantum Soul", "Evolution Part 1 - Mini Mix").replace(
        "</body>", '<ul><li class="buyItem digital"><h3>Digital Album</h3><div class="bd">Includes unlimited streaming via the free Bandcamp app.'
                   '<br><br>1. Serpentine<br>2. High Priest<br>3. The Message<br>4. Otherside<br>6. Dema Dub<br>7. Raw Signal (5:41)<br>8. Unite The Tribes</div></li></ul></body>'),
    # remixes and bootlegs: the original artist's page, and a remixer's page carrying the bootleg
    ("seanpaul.bandcamp.com", "/music"): grid("Sean Paul", ("hits", "Hits")),
    ("seanpaul.bandcamp.com", "/album/hits"): album("Hits", "Sean Paul", "Temperature", "Get Busy"),
    ("nightmare.bandcamp.com", "/music"): grid("Nightmare", ("bootlegs", "Bootlegs Vol. 1")),
    ("nightmare.bandcamp.com", "/album/bootlegs"): album("Bootlegs Vol. 1", "Nightmare", "Nas - You Owe Me (Nightmare & Oni 140 Bootleg)", "Other"),
    ("dnorecords.bandcamp.com", "/album/dno022-howman-trap-eastwood-ep"): album(
        "DNO022 - Howman - Trap Eastwood EP", "DNO Records", "Strip Search", "Frosty", date="28 Aug 2026 00:00:00 GMT"),
    # a subscriber-only track: not in the label's public list, not in search, but it has a page
    ("dungeonbeats.bandcamp.com", "/track/hannibal-rex-manyothers"): release(
        "Hannibal Rex - Manyothers", "16 Aug 2026 00:00:00 GMT", "MusicRecording", {"byArtist": {"name": "Dungeon Beats"}}),
    ("labelx.bandcamp.com", "/album/lx001"): album("LX001", "Label X", "Somebody - Catalogue Tune", "Other - Thing"),
    ("somahuk.bandcamp.com", "/music"): grid("Somah UK", ("older", "Older Thing"), ("gnosis-railroads", "Gnosis / Railroads")),
    ("somahuk.bandcamp.com", "/album/older"): album("Older Thing", "Somah", "Aaa", "Bbb"),
    ("somahuk.bandcamp.com", "/album/gnosis-railroads"): album("Gnosis / Railroads", "Somah", "Gnosis", "Railroads"),
    ("pugilist.bandcamp.com", "/music"): grid("Pugilist", ("blue", "Blue EP")),
    ("pugilist.bandcamp.com", "/album/blue"): album("Blue EP", "Pugilist", "Blue", "Green"),
    ("ccc.bandcamp.com", "/music"): grid("Cccc", ("conduit", "Conduit / Dissolution")),
    ("ccc.bandcamp.com", "/album/conduit"): album("Conduit / Dissolution", "Aaaa & Cccc", "Deep Thing", "Dissolution"),
}
searched = []
calls = {"expired_once": False}


related_calls = []


def handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    calls["requests"] = calls.get("requests", 0) + 1
    if host == "www.beatport.com" and path == "/search/tracks":
        q = request.url.params.get("q", "")
        tracks = [{"track_id": 501, "track_name": "Deep Groove", "mix_name": "Original Mix", "slug": "deep-groove",
                   "artists": [{"artist_id": 1, "artist_name": "Beat Artist"}], "remixers": [],
                   "release": {"release_id": 9, "release_name": "Deep Groove EP"}, "label": {"label_name": "BP Label"},
                   "publish_date": "2024-03-01"}] if "Deep Groove" in q else [
                  {"track_id": 502, "track_name": "Late Tune", "mix_name": "Extended Mix", "artists": [{"artist_name": "Beat Artist"}],
                   "release": {"release_name": "Late"}, "publish_date": "2025-01-01"}] if "Late Tune" in q else []
        data = {"props": {"pageProps": {"dehydratedState": {"queries": [{"state": {"data": {"data": tracks}}}]}}}}
        return httpx.Response(200, text='<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script></html>")
    if host == "api.discogs.com":
        if request.headers.get("Authorization") != "Discogs token=dg-secret":
            return httpx.Response(401)
        if path == "/database/search":
            ok = request.url.params.get("artist") == "Wax Only"
            return httpx.Response(200, json={"results": [{"id": 77, "title": "Wax Only - Pressed EP", "type": "release"}] if ok else []})
        if path == "/releases/77":
            return httpx.Response(200, json={"title": "Pressed EP", "year": 2019, "uri": "https://www.discogs.com/release/77-Wax-Only-Pressed-EP",
                                             "master_id": 700, "artists": [{"name": "Wax Only"}], "formats": [{"name": "Vinyl"}],
                                             "tracklist": [{"title": "Side Tune", "type_": "track"}, {"title": "Pressed Tune", "type_": "track"}]})
        return httpx.Response(404)
    if host == "nightferry.bandcamp.com":
        pages = {"/music": GRID,
                 "/album/first": FIRST,
                 "/track/second": SECOND,
                 "/album/third": release("Third", "15 Jan 2024 00:00:00 GMT")}
        return httpx.Response(200, text=pages[path]) if path in pages else httpx.Response(404)
    if host == "deeplabel.bandcamp.com" and path == "/music":      # a label page listing the same album
        return httpx.Response(200, text='<html><head><meta property="og:site_name" content="Deep Label"></head><body>'
                              '<ol id="music-grid"><li><a href="https://nightferry.bandcamp.com/album/first?label=9">'
                              '<p class="title">First</p></a></li></ol></body></html>')
    if (host, path) in PAGES:
        return httpx.Response(200, text=PAGES[(host, path)])
    if host == "bandcamp.com" and path == "/api/fancollection/1/collection_items":
        body = json.loads(request.content)
        assert body["fan_id"] == 77 and body["older_than_token"] == "tok1"
        if calls.get("collection_fail"):                  # Bandcamp saying "slow down", or an outage
            status = calls["collection_fail"].pop(0)
            return httpx.Response(status, headers={"retry-after": "0"} if status == 429 else {})
        return httpx.Response(200, json=FAN_MORE)
    if host == "bandcamp.com" and path == "/search":
        q, kind = request.url.params["q"], request.url.params["item_type"]
        searched.append(q)
        found = SEARCHES.get((kind, q), SEARCHES.get(q, "") if kind == "t" else "")
        return httpx.Response(200, text="<ul>" + found + "</ul>")
    if host == "masot.bandcamp.com" and path == "/track/especies-end-micas":
        return httpx.Response(200, text=release("Especies Endémicas", "02 May 2023 00:00:00 GMT", "MusicRecording", {
            "byArtist": {"name": "Masot"}, "inAlbum": {"name": "Flora", "@id": "https://masot.bandcamp.com/album/flora"}}))
    if host == "api-v2.soundcloud.com" and path == "/search/users" and request.url.params.get("client_id") == CID:
        users = {"River Dub": [{"username": "River Dub", "permalink": "user-865621610", "permalink_url": "https://soundcloud.com/user-865621610", "followers_count": 5}],
                 "River": [{"username": "River", "permalink": "river", "permalink_url": "https://soundcloud.com/river", "followers_count": 900000},
                           {"username": "River", "permalink": "riverirvine", "permalink_url": "https://soundcloud.com/riverirvine", "followers_count": 800}],
                 "Nightmare & Oni": [{"username": "NIGHTMARE", "permalink": "nightmare", "permalink_url": "https://soundcloud.com/nightmare", "followers_count": 500000},
                                     {"username": "Nightmare & Oni", "permalink": "nightmareandoni", "permalink_url": "https://soundcloud.com/nightmareandoni", "followers_count": 300}],
                 "Solo Guy": [{"username": "Solo Guy", "permalink": "sologuy-official", "permalink_url": "https://soundcloud.com/sologuy-official", "followers_count": 10}],
                 "Omega Nebula": [{"username": "Omega Nebula", "permalink": "omeganebula", "permalink_url": "https://soundcloud.com/omeganebula"}],
                 "Sean Paul": [{"username": "Sean Paul", "permalink": "seanpaul", "permalink_url": "https://soundcloud.com/seanpaul"}],
                 "Anecho": [{"username": "ANECHO", "permalink": "anechoicchamber", "permalink_url": "https://soundcloud.com/anechoicchamber"}],
                 "Nobody Known": [{"username": "nobody known fan", "permalink_url": "https://soundcloud.com/fan", "followers_count": 9000},
                                  {"username": "Nobody Known", "permalink_url": "https://soundcloud.com/nobodyknown", "followers_count": 12}]}
        return httpx.Response(200, json={"collection": users.get(request.url.params["q"], [])})
    if host == "api-v2.soundcloud.com" and path == "/search/tracks" and request.url.params.get("client_id") == CID:
        river = {"username": "River", "permalink": "riverirvine", "permalink_url": "https://soundcloud.com/riverirvine"}
        tracks = {"Sean Paul Temperature (River Dub Edit)": [
            {"title": "Temperature", "user": {"username": "Sean Paul", "permalink": "seanpaul", "permalink_url": "https://soundcloud.com/seanpaul"}},
            {"title": "Sean Paul - Temperature (River Dub Edit) [PREMIERE]", "user": {"username": "Some Channel", "permalink": "somechannel", "permalink_url": "https://soundcloud.com/somechannel"}},
            {"title": "Sean Paul - Temperature (River Dub Edit)", "user": river, "downloadable": True, "display_date": "2026-03-01T00:00:00Z",
             "permalink_url": "https://soundcloud.com/riverirvine/temperature-river-dub-edit"}],
            "Common Name Some Tune": [{"title": "Some Tune", "permalink_url": "https://soundcloud.com/the-real-common-name/some-tune", "created_at": "2024-05-05T00:00:00Z",
                                       "user": {"username": "Common Name", "permalink": "the-real-common-name", "permalink_url": "https://soundcloud.com/the-real-common-name"}}]}
        for found in tracks.values():
            for tr in found:
                tr.setdefault("permalink_url", "https://soundcloud.com/x/" + tr["title"].replace(" ", "-"))
        return httpx.Response(200, json={"collection": tracks.get(request.url.params["q"], [])})
    if host == "solo.bandcamp.com" and path == "/music":
        return httpx.Response(200, text=release("Only One", "02 Feb 2026 00:00:00 GMT").replace(
            '"@type"', '"@id": "https://solo.bandcamp.com/album/only-one", "@type"'))
    if host == "soundcloud.com":
        return httpx.Response(200, text='<script src="https://a-v2.sndcdn.com/assets/0-abc.js"></script>')
    if host == "a-v2.sndcdn.com":
        return httpx.Response(200, text=f'x={{client_id:"{CID}",y:1}}')
    if host == "api-v2.soundcloud.com":
        if request.url.params.get("client_id") != CID:
            return httpx.Response(401)
        if path == "/resolve" and request.url.params["url"].endswith("/mefan"):
            return httpx.Response(200, json={"kind": "user", "id": 7, "username": "Me"})
        if path == "/users/7/followings":
            if request.url.params.get("cursor") == "2":
                return httpx.Response(200, json={"collection": [
                    {"username": "Hidden Act", "permalink": "hidden-act-music", "permalink_url": "https://soundcloud.com/hidden-act-music"}]})
            return httpx.Response(200, json={"collection": [
                {"username": "River", "permalink": "riverirvine", "permalink_url": "https://soundcloud.com/riverirvine"},
                {"username": "Someone Else", "permalink": "someone", "permalink_url": "https://soundcloud.com/someone"}],
                "next_href": "https://api-v2.soundcloud.com/users/7/followings?cursor=2&limit=200&client_id=" + CID})
        if path == "/resolve":
            if not request.url.params["url"].endswith("/nightferry"):
                return httpx.Response(404)
            return httpx.Response(200, json={"kind": "user", "id": 42, "username": "Night Ferry",
                                             "avatar_url": "https://i1.sndcdn.com/avatars-1-large.jpg"})
        if path == "/tracks/900/related":
            related_calls.append(1)
            return httpx.Response(200, json={"collection": [
                {"kind": "track", "id": 1000 + i, "title": f"Rel {i}", "permalink_url": f"https://soundcloud.com/x/rel-{i}",
                 "duration": 30000, "full_duration": 181000, "created_at": f"2025-0{1 + i % 9}-01T00:00:00Z",
                 "user": {"username": f"Uploader {i}"}, "publisher_metadata": {"artist": "Pub Artist"} if i == 0 else {}}
                for i in range(10)] + [{"kind": "playlist", "id": 5}]})
        if path == "/users/42/tracks":
            return httpx.Response(200, json={"collection": [
                {"kind": "track", "id": 900, "title": "Demo", "permalink_url": "https://soundcloud.com/nightferry/demo",
                 "artwork_url": "https://i1.sndcdn.com/artworks-9-large.jpg", "created_at": "2026-10-01T10:00:00Z",
                 "duration": 30000, "full_duration": 200000, "label_name": "SC Label",
                 "publisher_metadata": {"album_title": "Demo EP"},
                 "user": {"username": "Night Ferry"}},
                {"kind": "track", "id": 901, "title": "No Art", "permalink_url": "https://soundcloud.com/nightferry/no-art",
                 "artwork_url": None, "display_date": "2026-08-01T10:00:00Z", "created_at": "2020-01-01T00:00:00Z",
                 "user": {"username": "Night Ferry", "avatar_url": "https://i1.sndcdn.com/avatars-1-large.jpg"}}]})
    if host == "www.youtube.com":
        youtube_calls.append(str(request.url))
        if path == "/@tubeband":
            return httpx.Response(200, text='<html><link rel="canonical" href="https://www.youtube.com/channel/UC1234567890123456789012"></html>')
        if path == "/feeds/videos.xml" and request.url.params["channel_id"] == "UC1234567890123456789012":
            return httpx.Response(200, text=YT_FEED)
        if path == "/results":
            q = request.url.params["search_query"]
            videos = YT_RESULTS.get(q, [])
            data = {"contents": {"sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": [
                {"videoRenderer": {"videoId": v[0], "title": {"runs": [{"text": v[1]}]}, "ownerText": {"runs": [{"text": v[2]}]},
                                   "lengthText": {"simpleText": "4:01"}}} for v in videos]}}]}}}
            return httpx.Response(200, text=f'<script>var ytInitialData = {json.dumps(data)};</script>')
    return httpx.Response(404)


youtube_calls = []
YT_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <title>Tube Band</title>
 <entry><yt:videoId>vid00000001</yt:videoId><title>Tube Band - Glass River</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=vid00000001"/>
  <author><name>Tube Band</name></author><published>2026-10-02T12:00:00+00:00</published>
  <media:group><media:thumbnail url="https://i1.ytimg.com/vi/vid00000001/hqdefault.jpg" width="480" height="360"/></media:group></entry>
 <entry><yt:videoId>vid00000002</yt:videoId><title>Studio Diary</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=vid00000002"/>
  <author><name>Tube Band</name></author><published>2026-09-01T12:00:00+00:00</published></entry>
</feed>"""
YT_RESULTS = {"Tube Band Hidden Lake": [("vid00000009", "Someone Else - Hidden Lake (cover)", "Covers Inc"),
                                        ("vid00000010", "Tube Band - Hidden Lake", "Tube Band Archive")],
              "Unticked Artist Lost Song": [("vid00000011", "Unticked Artist - Lost Song", "Unticked Artist")]}


@pytest.fixture(scope="module")
def client():
    refresh.make_client = lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True)
    with TestClient(app) as c:
        yield c


def wait(client):
    for _ in range(100):
        time.sleep(0.05)
        if not client.get("/api/status").json()["refreshing"]:
            return
    raise AssertionError("refresh never finished")


def test_normalize():
    bc, sc = SOURCES["bandcamp"], SOURCES["soundcloud"]
    assert bc.normalize("NightFerry") == ("nightferry.bandcamp.com", "https://nightferry.bandcamp.com")
    assert bc.normalize("https://nightferry.bandcamp.com/album/x")[1] == "https://nightferry.bandcamp.com"
    assert bc.normalize("music.customdomain.net")[1] == "https://music.customdomain.net"
    assert sc.normalize("https://soundcloud.com/NightFerry/tracks") == ("nightferry", "https://soundcloud.com/nightferry")
    assert sc.normalize("@nightferry")[0] == "nightferry"
    for bad in ("", "https://bandcamp.com"):
        with pytest.raises(SourceError):
            bc.normalize(bad)
    with pytest.raises(SourceError):
        sc.normalize("https://soundcloud.com/discover")


def test_parsers():
    items = parse_music_grid(GRID, "https://nightferry.bandcamp.com")
    assert [i["url"].rsplit("/", 1)[-1] for i in items] == ["first", "second", "third"]
    assert items[1]["title"] == "Second" and items[1]["author"] == "Guest Person"
    assert items[1]["image"].endswith("a222_2.jpg") and items[2]["image"].endswith("a333_2.jpg")
    rel = parse_release(release("First", "07 Mar 2025 00:00:00 GMT"), "https://x/album/first")
    assert rel["published_at"] == "2025-03-07T00:00:00Z" and rel["is_release"]


def test_add_and_feed(client):
    r = client.post("/api/artists", json={"accounts": [
        {"source": "bandcamp", "handle": "nightferry"}, {"source": "soundcloud", "handle": "nightferry"}]})
    assert r.status_code == 201
    wait(client)
    artist = client.get("/api/artists").json()[0]
    assert artist["name"] == "Night Ferry"            # taken from the page
    bc = next(a for a in artist["accounts"] if a["source"] == "bandcamp")
    assert "1 older release" in bc["last_error"]      # BANDCAMP_MAX_NEW=2 of 3
    feed = client.get("/api/feed").json()
    assert [p["title"] for p in feed] == ["Demo", "Second", "First", "No Art"]
    assert feed[0]["image"].endswith("artworks-9-t500x500.jpg")
    assert feed[3]["image"].endswith("avatars-1-t500x500.jpg")

    client.post("/api/refresh")                        # second pass picks up the rest, no duplicates
    wait(client)
    feed = client.get("/api/feed").json()
    assert [p["title"] for p in feed] == ["Demo", "Second", "First", "Third", "No Art"]
    assert [p["title"] for p in client.get("/api/feed?source=bandcamp").json()] == ["Second", "First", "Third"]
    assert client.get("/api/artists").json()[0]["accounts"][0]["last_error"] is None


def test_track_list(client):
    get = lambda query="": client.get("/api/tracks" + query).json()
    rows = get()
    assert [(r["release"], r["track"]) for r in rows] == [
        ("Demo EP", "Demo"), ("First", "Second"), ("", "No Art"),
        ("First", "Intro"), ("First", "Long One"), ("Third", "")]
    demo, second, _, intro, long_one, third = rows
    assert (demo["label"], demo["duration"], demo["source"]) == ("SC Label", 200.0, "soundcloud")
    assert (second["duration"], second["embed"], second["label"]) == (225.0, "track=222", "")
    assert (intro["label"], intro["embed"], intro["track_ext_id"], intro["artist"]) == ("Deep Label", "album=111", "1001", "Night Ferry")
    assert long_one["artist"] == "Guest Person" and long_one["url"] == "https://nightferry.bandcamp.com/track/long-one"
    assert third["duration"] is None and third["embed"] is None
    # posted = when it went up on the site; blank when the site doesn't say (never the release date)
    assert [(r["posted"] or "")[:10] for r in rows] == ["2026-10-01", "", "2020-01-01", "2025-03-05", "2025-03-05", ""]
    assert [r["published_at"][:10] for r in rows] == ["2026-10-01", "2026-09-01", "2026-08-01", "2025-03-07", "2025-03-07", "2024-01-15"]
    assert [r["track"] for r in get("?sort=posted&dir=asc")] == ["No Art", "Intro", "Long One", "Demo", "Second", ""]
    assert [r["track"] for r in get("?sort=released&dir=asc")][:2] == ["", "Intro"]

    assert [r["track"] for r in get("?sort=duration&dir=desc")][:2] == ["Long One", "Second"]
    assert get("?sort=duration&dir=asc")[-1]["release"] == "Third"            # unknown durations last
    assert [r["track"] for r in get("?sort=track&dir=asc")] == ["Demo", "Intro", "Long One", "No Art", "Second", ""]
    assert [r["track"] for r in get("?q=guest")] == ["Long One"]
    assert [r["track"] for r in get("?q=deep+intro")] == ["Intro"]
    assert [r["track"] for r in get("?source=soundcloud&limit=1&offset=1")] == ["No Art"]
    assert get("?q=100%25") == []

    # filters: year, label ("-" = none), type, length; shared by list, grid and export
    assert client.get("/api/facets").json() == {"years": ["2026", "2025", "2024"], "labels": ["Deep Label", "SC Label"], "hidden": 0}
    assert [r["track"] for r in get("?year=2025")] == ["Intro", "Long One"]
    assert [r["track"] for r in get("?label=Deep+Label&length=extended")] == ["Long One"]
    assert [r["track"] for r in get("?label=-")] == ["Second", "No Art", ""]
    assert [r["track"] for r in get("?kind=track&length=medium")] == ["Demo", "Second"]
    assert [r["track"] for r in get("?length=short")] == ["Intro"]
    assert [r["release"] for r in get("?kind=album&year=2024")] == ["Third"]
    titles = lambda query: [p["title"] for p in client.get("/api/feed" + query).json()]
    assert titles("?length=extended") == ["First"] and titles("?label=-&kind=album") == ["Third"]
    assert titles("?year=2026&source=bandcamp") == ["Second"]
    assert len(client.get("/api/tracks.csv?year=2025").content.decode("utf-8-sig").splitlines()) == 3

    # "Hide no-preview tracks": a Bandcamp track with no length has no stream yet. Third has no track list at all
    # (nothing known either way) and SoundCloud uploads always play, so they stay.
    from app import db
    db.execute("UPDATE tracks SET duration=NULL WHERE title='Intro'")
    assert [r["track"] for r in get("?preview=1")] == ["Demo", "Second", "No Art", "Long One", ""]
    assert "First" in titles("?preview=1")                                   # one of its tracks still plays
    assert "Intro" not in client.get("/api/tracks.csv?preview=1").content.decode("utf-8-sig")
    db.execute("UPDATE tracks SET duration=NULL WHERE title='Long One'")
    assert "First" not in titles("?preview=1") and "First" in titles("")    # nothing on it plays: gone from the grid too
    db.execute("UPDATE tracks SET duration=61.5 WHERE title='Intro'")
    db.execute("UPDATE tracks SET duration=3725 WHERE title='Long One'")

    first = next(p for p in client.get("/api/feed?q=intro").json())
    assert (first["title"], first["embed"], first["track_count"], first["duration"]) == ("First", "album=111", 2, 3786.5)

    r = client.get("/api/tracks.csv?sort=duration&dir=desc")
    lines = r.content.decode("utf-8-sig").splitlines()
    assert lines[0] == "artist,label,release,track,duration,year,released,posted,site,url"
    assert lines[1] == "Guest Person,Deep Label,First,Long One,1:02:05,2025,2025-03-07,2025-03-05,Bandcamp,https://nightferry.bandcamp.com/track/long-one"
    assert len(lines) == 7 and "attachment" in r.headers["content-disposition"]


def test_reading_tracklists():
    from app import lookup
    header = ["Track #", "Artist", "Title", "Crate", "Category / Series", "Title", "Track", "Time"]
    rows = [header, ["1", "Idpa & Saša Delimar", "State I", "245", "Fresh Produce", "Flummoxed", "1", "0:00"],
            ["2", "Masot", "02", "245", "Fresh Produce", "Flummoxed", "2", "5:08"], ["", "", "", "", "", "", "", ""]]
    want = [{"n": 1, "artist": "Idpa & Saša Delimar", "title": "State I"}, {"n": 2, "artist": "Masot", "title": "02"}]
    as_text = lambda sep: "\r\n".join(sep.join(r) for r in rows).encode("utf-8")
    assert lookup.pick_tracks(lookup.read_table(as_text("\t"))) == want          # pasted from a spreadsheet
    assert lookup.pick_tracks(lookup.read_table(b"\xef\xbb\xbf" + as_text(","))) == want   # CSV file
    import io, openpyxl
    book = openpyxl.Workbook(); [book.active.append([int(r[0]) if r[0].isdigit() else r[0]] + r[1:]) for r in rows]
    data = io.BytesIO(); book.save(data)
    assert lookup.pick_tracks(lookup.read_table(data.getvalue())) == want          # Excel file
    assert lookup.pick_tracks(lookup.read_table(b"1\tMasot\t02\n2\tIdpa\tState I")) == [
        {"n": 1, "artist": "Masot", "title": "02"}, {"n": 2, "artist": "Idpa", "title": "State I"}]   # no header
    assert lookup.pick_tracks(lookup.read_table("1. Masot \u2013 02\nIdpa - State I".encode()))[0] == {"n": 1, "artist": "Masot", "title": "02"}
    for bad in (b"", b"Artist\tAlbum\nMasot\tFlora"):
        with pytest.raises(lookup.TracklistError):
            lookup.pick_tracks(lookup.read_table(bad))
    assert lookup.norm("Especies Endémicas (Original Mix)") == "especies endemicas"
    assert lookup.artist_parts("Masot") == ["masot"] and lookup.artist_parts("Idpa & Saša Delimar")[1:] == ["idpa", "sasa delimar"]


def test_artist_named_only_in_release_title_or_address():
    """A vinyl LP bought from a label: 'DNO021 - Kercha - Open The Door LP' by DNO Records."""
    from app import db, lookup
    page = {"url": "https://dnorecords.bandcamp.com/album/dno021-kercha-open-the-door-lp", "title": "Open The Door LP",
            "artist": "DNO Records", "year": "2026", "release": "Open The Door LP", "release_url": "",
            "tracks": [{"title": "A Path Into Unknown", "artist": ""}, {"title": "Trigger Activation", "artist": ""}]}
    assert lookup.match_in_release("Kercha", "Trigger Activation", page) == ("bandcamp", "Trigger Activation")
    assert lookup.match_in_release("Kerch", "Trigger Activation", page)[0] == ""          # whole words only
    assert lookup.match_in_release("Someone Else", "Trigger Activation", page)[0] == ""
    assert lookup.match_in_release("Kercha", "Something Not On It", page)[0] == ""
    db.execute("INSERT INTO collection_items(key, fan, title, band, url, kind) VALUES('a999','x',?,?,?,'a')",
               ("DNO021 - Kercha - Open The Door LP", "DNO Records", page["url"]))
    try:
        lib = lookup.Library()
        assert [i["key"] for i in lib.owned_by("Kercha")] == ["a999"] and lib.owned_by("Erch") == []
        assert lib.owns(page)
    finally:
        db.execute("DELETE FROM collection_items WHERE key='a999'")


def test_track_names_written_in_the_description(client):
    from app import db, lookup
    from app.sources.bandcamp import listed_titles, parse_release
    assert listed_titles(["Intro text\r\n1. Serpentine\r\n2. High Priest - 4:10\n3) The Message (6:02)\nThanks!\n1. only one"]) == [
        "Serpentine", "High Priest", "The Message"]
    assert listed_titles(["A1. Disclosed\nA2. Forbidden Truth\nB1 - Open The Door"]) == ["Disclosed", "Forbidden Truth", "Open The Door"]
    assert listed_titles(["3. starts mid-way\n4. so not a track list", "Released 2026\n1. lonely line", "1. Go to https://x.y\n2. Click"]) == []
    page = parse_release(PAGES[("quantumsoul.bandcamp.com", "/album/evolution-part-1")], "https://quantumsoul.bandcamp.com/album/evolution-part-1")
    assert [t.title for t in page["tracks"]] == ["Evolution Part 1 - Mini Mix"]            # the feed still shows what is playable
    assert page["listed_tracks"] == ["Serpentine", "High Priest", "The Message", "Otherside", "Dema Dub", "Raw Signal", "Unite The Tribes"]

    # As saved by an earlier version: one playable track, no written list. It is read again.
    url = "https://quantumsoul.bandcamp.com/album/evolution-part-1"
    lookup.cache_put("release", url, {"url": url, "title": "Evolution Part 1", "artist": "Quantum Soul", "year": "2026",
                                      "tracks": [{"title": "Evolution Part 1 - Mini Mix", "artist": ""}]})
    j = client.post("/api/lookup", content="Artist\tTitle\nQuantum Soul\tUnite The Tribes\nQuantum Soul\tRaw Signal".encode()).json()
    for _ in range(100):
        time.sleep(0.02)
        got = client.get(f"/api/lookup/{j['id']}").json()
        if got["finished"]:
            break
    assert [(r["status"], r["url"], r["year"]) for r in got["rows"]] == [("bandcamp", url, "2026")] * 2
    assert got["rows"][1]["found"] == "Quantum Soul \u2014 Raw Signal"


def test_tracklist_lookup(client):
    def run(text, fresh=False):
        job = client.post("/api/lookup" + ("?fresh=true" if fresh else ""), content=text.encode()).json()
        for _ in range(200):
            time.sleep(0.02)
            got = client.get(f"/api/lookup/{job['id']}").json()
            if got["finished"]:
                return got
        raise AssertionError("lookup never finished")
    listing = "\n".join(["#\tArtist\tTitle\tCrate\tTitle", "1\tNight Ferry\tintro\t245\tMix A",
                         "2\tMasot\tEspecies Endémicas\t245\tMix A", "3\tIdpa & Saša Delimar\tState I\t245\tMix A",
                         "4\tNobody Known\tZzz\t245\tMix A", "5\tGhost Person\tQqq Rrr\t245\tMix A",
                         "6\tOther Guy\tEspecies Endémicas\t245\tMix A", "7\tNight Ferry\tUnreleased Thing\t245\tMix A"])
    got = run(listing)
    assert got["done"] == got["total"] == 7 and not got["warning"]
    r = {row["n"]: row for row in got["rows"]}
    # 1: already in the feed -> its album, no searching
    assert (r[1]["status"], r[1]["url"], r[1]["year"], r[1]["release"]) == ("bandcamp", "https://nightferry.bandcamp.com/album/first", "2025", "First")
    assert not any("intro" in q.lower() for q in searched)
    # 2: found by search; the link is the album the track is on, with its year
    assert (r[2]["status"], r[2]["url"], r[2]["year"], r[2]["release"]) == ("bandcamp", "https://masot.bandcamp.com/album/flora", "2023", "Flora")
    # 3: two artists, track titled "Artist - Title" on a label page; no album page -> the track's own link
    assert (r[3]["status"], r[3]["url"], r[3]["year"]) == ("bandcamp", "https://somelabel.bandcamp.com/track/state-i", "2021")
    # 4: not on Bandcamp -> the SoundCloud profile whose name matches, not the most followed one
    assert (r[4]["status"], r[4]["url"], r[4]["year"]) == ("soundcloud", "https://soundcloud.com/nobodyknown", "")
    # 5: nowhere -> a SoundCloud search link, flagged as such
    assert r[5]["status"] == "soundcloud_search" and r[5]["url"].endswith("people?q=Ghost%20Person")
    # 6: same title by someone else is not accepted as a match
    assert r[6]["status"] == "soundcloud_search"
    # 7: the artist has a Bandcamp page but the track isn't on it -> that page, labelled as such
    assert (r[7]["status"], r[7]["url"]) == ("bandcamp_artist", "https://nightferry.bandcamp.com")
    assert all(row["steps"] for row in got["rows"])       # every row says what was tried

    lines = client.get(f"/api/lookup/{got['id']}.csv").content.decode("utf-8-sig").splitlines()
    assert lines[0] == "#,artist,title,year,link,needs review,artist (your spelling),title (your spelling),link note,site,release,result,found as,note,steps"
    assert lines[2].startswith("2,Masot,Especies Endémicas,2023,https://masot.bandcamp.com/album/flora,,,,,Bandcamp,Flora,Bandcamp release,Masot — Especies Endémicas,Bandcamp search,")

    count = len(searched)
    again = run(listing)                                  # remembered: no new searches
    assert len(searched) == count and [x["url"] for x in again["rows"]] == [x["url"] for x in got["rows"]]
    run(listing, fresh=True)
    assert len(searched) > count
    assert client.post("/api/lookup", content=b"Artist\tAlbum\nMasot\tFlora").status_code == 400
    assert client.get("/api/lookup/nope").status_code == 404


def test_lookup_uses_purchases_and_artist_pages(client):
    """The situations in a real list: purchases, label releases, clumsy search, removed tracks."""
    def read_purchases():
        assert client.post("/api/collection", json={"fan": "https://bandcamp.com/divided_eye"}).json()["fan"] == "divided_eye"
        for _ in range(200):
            time.sleep(0.02)
            status = client.get("/api/collection").json()
            if not status["syncing"]:
                return status
        raise AssertionError("still reading")

    # An outage part-way: what was read is kept, and the next read carries on from there.
    calls["collection_fail"] = [500]
    status = read_purchases()
    assert (status["items"], status["complete"]) == (2, False) and "2 of 4 purchases are saved" in status["error"]
    # Told to slow down twice (429): it waits and retries rather than giving up.
    calls["collection_fail"] = [429, 429]
    status = read_purchases()
    assert (status["items"], status["tracks"], status["error"], status["complete"]) == (4, 4, "", True)
    assert calls["collection_fail"] == []

    # Bandcamp's collection names only one track per purchase. Reading the full track lists opens each
    # purchased release once; after that, any track on a release you own is answered without searching.
    assert (status["releases"], status["releases_read"]) == (4, 0)
    client.post("/api/collection/tracklists")
    for _ in range(200):
        time.sleep(0.02)
        status = client.get("/api/collection").json()
        if not status["deep_running"]:
            break
    assert (status["releases"], status["releases_read"]) == (4, 4)
    made, asked_before = calls["requests"], len(searched)
    j = client.post("/api/lookup?fresh=true", content="Artist\tTitle\nBlocklab\tGargantua\ncontactghost\tSpelling Mitsake".encode()).json()
    time.sleep(0.4)
    owned = client.get(f"/api/lookup/{j['id']}").json()
    assert owned["finished"] and [(x["status"], x["note"], x["year"]) for x in owned["rows"]] == [
        ("bandcamp", "In your purchases", "2025"), ("bandcamp", "In your purchases", "2026")]
    assert calls["requests"] == made and len(searched) == asked_before        # nothing fetched, nothing searched
    client.post("/api/collection/tracklists")                                   # nothing left to read
    time.sleep(0.2)
    assert calls["requests"] == made

    rows = [("Masot", "02"), ("Masot", "Especies Endémicas"), ("Blocklab", "Gargantua"), ("contactghost", "Spelling Mitsake"),
            ("Siadic", "Air"), ("Somah", "Gnosis"), ("Pugilist", "Asteroids"), ("Anecho", "UFO"),
            ("Allen Mock", "Dragon Language"), ("YDX", "What A World"), ("Aaaa & Bbbb Cccc", "Deep Thing"),
            ("Kercha", "Trigger Activation")]
    text = "Artist\tTitle\n" + "\n".join(f"{a}\t{t}" for a, t in rows)
    job = client.post("/api/lookup?fresh=true", content=text.encode()).json()
    for _ in range(300):
        time.sleep(0.02)
        got = client.get(f"/api/lookup/{job['id']}").json()
        if got["finished"]:
            break
    assert got["finished"] and not got["warning"]
    r = {(row["artist"], row["title"]): row for row in got["rows"]}
    want = {   # (status, link, year)
        # purchases: the track list that comes with the collection (a two-character title)
        ("Masot", "02"): ("bandcamp", "https://masot.bandcamp.com/album/archivos-pt-1", "2026"),
        # purchases: a release named after the track
        ("Masot", "Especies Endémicas"): ("bandcamp", "https://masot.bandcamp.com/album/especies-endemicas", "2026"),
        # purchases: on a label's page, track not in the collection's own list -> the release is opened
        ("Blocklab", "Gargantua"): ("bandcamp", "https://whitepeach.bandcamp.com/album/wpr091-phoenix", "2025"),
        ("contactghost", "Spelling Mitsake"): ("bandcamp", "https://artikalmusic.bandcamp.com/album/half-empty", "2026"),
        # search finds the track; the link given is the release it is on
        ("Siadic", "Air"): ("bandcamp", "https://siadic.bandcamp.com/album/traction-ep", "2024"),
        # search finds nothing; the artist's page (named differently) is scanned release by release
        ("Somah", "Gnosis"): ("bandcamp", "https://somahuk.bandcamp.com/album/gnosis-railroads", "2026"),
        # the artist's page exists but the track is gone -> the page itself
        ("Pugilist", "Asteroids"): ("bandcamp_artist", "https://pugilist.bandcamp.com", ""),
        # no Bandcamp presence -> SoundCloud profile, matched on its name
        ("Anecho", "UFO"): ("soundcloud", "https://soundcloud.com/anechoicchamber", ""),
        # only findable as an album, on a label's page; the same title by someone else is ignored
        ("Allen Mock", "Dragon Language"): ("bandcamp", "https://observatory-records.bandcamp.com/album/dragon-language", "2026"),
        # only findable by title alone, listed as "Artist - Title" on a label compilation
        ("YDX", "What A World"): ("bandcamp", "https://dubdenrecords.bandcamp.com/album/dubs-from-the-den-vol-7", "2026"),
        # credited to the label; the artist is only named in the album's address
        ("Kercha", "Trigger Activation"): ("bandcamp", "https://dnorecords.bandcamp.com/album/dno021-kercha-open-the-door-lp", "2026"),
        # two artists: found on the second artist's own page
        ("Aaaa & Bbbb Cccc", "Deep Thing"): ("bandcamp", "https://ccc.bandcamp.com/album/conduit", "2026"),
    }
    for key, expected in want.items():
        assert (r[key]["status"], r[key]["url"], r[key]["year"]) == expected, (key, r[key])
    assert r[("Masot", "02")]["note"] == "In your purchases" and r[("Somah", "Gnosis")]["note"] == "Found on the artist's page"
    assert "Other" not in " ".join(r[("Somah", "Gnosis")]["steps"]) and any("somahuk" in s for s in r[("Somah", "Gnosis")]["steps"])

    # Correcting a result by hand: saved, shown in lookups on screen, and kept even when searching again.
    fix = client.put("/api/lookup/link", json={"artist": "Pugilist", "title": "Asteroids", "url": "pugilist.bandcamp.com/album/blue"}).json()
    assert (fix["status"], fix["url"], fix["year"], fix["release"]) == ("manual", "https://pugilist.bandcamp.com/album/blue", "2026", "Blue EP")
    assert next(x for x in client.get(f"/api/lookup/{job['id']}").json()["rows"] if x["artist"] == "Pugilist")["status"] == "manual"
    assert "Link you set" in client.get(f"/api/lookup/{job['id']}.csv").content.decode("utf-8-sig")
    def one(fresh):
        j = client.post("/api/lookup" + ("?fresh=true" if fresh else ""), content="Artist\tTitle\npugilist\tASTEROIDS".encode()).json()
        time.sleep(0.4)
        return client.get(f"/api/lookup/{j['id']}").json()["rows"][0]
    assert one(False)["url"] == one(True)["url"] == "https://pugilist.bandcamp.com/album/blue"
    other = client.put("/api/lookup/link", json={"artist": "Pugilist", "title": "Asteroids", "url": "https://example.org/x", "year": "1999"}).json()
    assert (other["site"], other["year"]) == ("example.org", "1999")
    assert client.put("/api/lookup/link", json={"artist": "Pugilist", "title": "Asteroids", "url": "not a link"}).status_code == 400
    assert client.put("/api/lookup/link", json={"artist": "Pugilist", "title": "Asteroids", "url": "x.com/a", "year": "soon"}).status_code == 400
    assert client.put("/api/lookup/link", json={"artist": "Pugilist", "title": "Asteroids", "url": ""}).json()["status"] == "none"
    assert one(False)["status"] == "bandcamp_artist"     # with your link removed, the searched answer is back

    # Every track of a release that was opened is saved: a different track from one of those releases,
    # asked for under a different artist credit, is answered without any new searching.
    asked = len(searched)
    j = client.post("/api/lookup", content="Artist\tTitle\nAaaa\tDissolution".encode()).json()
    time.sleep(0.4)
    hit = client.get(f"/api/lookup/{j['id']}").json()["rows"][0]
    assert (hit["status"], hit["url"], hit["note"]) == ("bandcamp", "https://ccc.bandcamp.com/album/conduit", "On a page read earlier")
    assert len(searched) == asked

    # Tracks answerable from purchases or pages already read cost no requests at all, even when
    # asked to search again, and are filled in before anything slow starts.
    made = calls["requests"]
    j = client.post("/api/lookup?fresh=true", content="Artist\tTitle\nMasot\t02\nBlocklab\tGargantua\nMasot\tEspecies Endémicas".encode()).json()
    time.sleep(0.4)
    quick = client.get(f"/api/lookup/{j['id']}").json()
    assert quick["finished"] and [x["status"] for x in quick["rows"]] == ["bandcamp"] * 3
    assert [x["year"] for x in quick["rows"]] == ["2026", "2025", "2026"] and calls["requests"] == made

    # A miss saved by an older version is tried again; a found release saved by an older version is kept.
    from app import db, lookup
    for a, t, status in (("Old Miss", "Zzz Qqq", "bandcamp_artist"), ("Old Hit", "Zzz Www", "bandcamp")):
        db.execute("INSERT OR REPLACE INTO lookup_cache VALUES(?,?,?)", (lookup._cache_key(a, t), json.dumps(
            {"status": status, "site": "Bandcamp", "url": "https://old.bandcamp.com", "steps": []}), "2026-10-10T00:00:00Z"))
    j = client.post("/api/lookup", content="Artist\tTitle\nOld Miss\tZzz Qqq\nOld Hit\tZzz Www".encode()).json()
    time.sleep(0.5)
    again = client.get(f"/api/lookup/{j['id']}").json()["rows"]
    assert again[0]["status"] == "soundcloud_search" and (again[1]["status"], again[1]["url"]) == ("bandcamp", "https://old.bandcamp.com")

    # Which artist link to give when the track isn't found is a setting; switching it re-points
    # saved and on-screen results without searching again.
    lookup.cache_put("artistpage", lookup.norm("Anecho"), [["https://anecho.bandcamp.com", "Anecho"]])
    lookup.cache_put("grid", "https://anecho.bandcamp.com", {"name": "Anecho", "items": []})
    def both(fresh=False):
        j = client.post("/api/lookup" + ("?fresh=true" if fresh else ""), content="Artist\tTitle\nAnecho\tUFO\nPugilist\tAsteroids".encode()).json()
        time.sleep(0.5)
        return j["id"], [(x["status"], x["url"]) for x in client.get(f"/api/lookup/{j['id']}").json()["rows"]]
    assert client.get("/api/lookup/settings").json() == {"fallback": "bandcamp"}
    job_id, rows = both(fresh=True)                      # Anecho has both; Pugilist only a Bandcamp page
    assert rows == [("bandcamp_artist", "https://anecho.bandcamp.com"), ("bandcamp_artist", "https://pugilist.bandcamp.com")]
    asked = calls["requests"]
    assert client.put("/api/lookup/settings", json={"fallback": "soundcloud"}).json() == {"fallback": "soundcloud"}
    on_screen = [(x["status"], x["url"]) for x in client.get(f"/api/lookup/{job_id}").json()["rows"]]
    assert on_screen == [("soundcloud", "https://soundcloud.com/anechoicchamber"), ("bandcamp_artist", "https://pugilist.bandcamp.com")]
    assert both()[1] == on_screen and calls["requests"] == asked          # saved answers follow, nothing fetched
    assert client.put("/api/lookup/settings", json={"fallback": "spotify"}).status_code == 400
    client.put("/api/lookup/settings", json={"fallback": "bandcamp"})
    assert both()[1] == rows

    before = len(searched)                              # pages read once are not fetched again
    client.post("/api/lookup?fresh=true", content="Artist\tTitle\nSomah\tRailroads".encode())
    time.sleep(0.5)
    assert client.get("/api/collection").json()["items"] == 4


def test_feed_stands_aside_for_lookups_and_pauses(client):
    import asyncio
    from app import db, pace
    checked = db.query("SELECT last_checked, last_error FROM accounts WHERE source='bandcamp'")

    async def background():
        await pace.wait(background=True)
    saved_lock, pace._state["lock"] = pace._state["lock"], None
    try:
        with pace.foreground():                        # a lookup is running
            with pytest.raises(pace.Busy):
                asyncio.run(background())
            client.post("/api/refresh")                # a feed refresh now touches nothing on Bandcamp...
            wait(client)
            assert db.query("SELECT last_checked, last_error FROM accounts WHERE source='bandcamp'") == checked
            assert client.get("/api/status").json()["bandcamp_on_hold"] is True
        pace._state["lock"] = None
        asyncio.run(background())                      # ...and is free to again afterwards
        pace.slow_down(30)                             # Bandcamp asked for a pause
        assert 25 < pace.paused_seconds() <= 30 and pace.on_hold()
        with pytest.raises(pace.Busy):
            asyncio.run(background())
    finally:
        pace._state.update(next=0.0, paused_until=0.0, lock=saved_lock)
    client.post("/api/refresh")
    wait(client)
    assert client.get("/api/status").json()["bandcamp_on_hold"] is False


def test_hiding_and_listened_to(client):
    feed = client.get("/api/feed").json()
    first = next(p for p in feed if p["title"] == "First")
    assert first["hidden"] == 0 and first["played"] == 0 and client.get("/api/facets").json()["hidden"] == 0
    rows = lambda q="": [(r["release"], r["track"], r["played"], r["hidden"]) for r in client.get("/api/tracks" + q).json()
                         if r["post_id"] == first["id"]]

    # Listened to: one track from the list, or the whole release from its card.
    intro = next(r for r in client.get("/api/tracks").json() if r["track"] == "Intro")
    assert intro["track_key"] == "1001"
    client.post(f"/api/posts/{first['id']}/played", json={"track": intro["track_key"]})
    assert [r for r in rows() if r[0] == "First"] == [("First", "Intro", 1, 0), ("First", "Long One", 0, 0)]
    assert next(p for p in client.get("/api/feed").json() if p["id"] == first["id"])["played"] == 1
    client.post(f"/api/posts/{first['id']}/played", json={"track": ""})
    assert all(r[2] == 1 for r in rows() if r[0] == "First")
    client.post("/api/refresh")                            # survives a refresh re-reading the release
    wait(client)
    assert all(r[2] == 1 for r in rows() if r[0] == "First")
    client.post(f"/api/posts/{first['id']}/played", json={"played": False})
    assert all(r[2] == 0 for r in rows() if r[0] == "First")

    # Hidden: gone from the grid, the list and the export, until asked for; then put back.
    assert client.post(f"/api/posts/{first['id']}/hidden", json={"hidden": True}).json() == {"hidden": 1}
    assert "First" not in [p["title"] for p in client.get("/api/feed").json()]
    assert not [r for r in rows() if r[0] == "First"]
    assert "Long One" not in client.get("/api/tracks.csv").content.decode("utf-8-sig")
    assert [r for r in rows("?hidden=1") if r[0] == "First"] == [("First", "Intro", 0, 1), ("First", "Long One", 0, 1)]
    assert next(p for p in client.get("/api/feed?hidden=1").json() if p["id"] == first["id"])["hidden"] == 1
    client.post("/api/refresh")                            # a refresh doesn't bring it back
    wait(client)
    assert "First" not in [p["title"] for p in client.get("/api/feed").json()]
    assert client.post(f"/api/posts/{first['id']}/hidden", json={"hidden": False}).json() == {"hidden": 0}
    assert [p["title"] for p in client.get("/api/feed").json()] == [p["title"] for p in feed]
    assert client.post("/api/posts/999999/hidden", json={"hidden": True}).status_code == 404
    assert client.post("/api/posts/999999/played", json={}).status_code == 404


def test_upgrade_from_first_version(client):
    """An older database gains the new columns, and its posts are re-read to fill them in."""
    import sqlite3
    from app import db
    old = sqlite3.connect(":memory:")
    old.execute("CREATE TABLE posts(id INTEGER PRIMARY KEY, account_id INTEGER, external_id TEXT, title TEXT,"
                " url TEXT, image TEXT, kind TEXT, author TEXT, published_at TEXT, first_seen TEXT)")
    old.execute("INSERT INTO posts(title) VALUES('kept')")
    db.migrate(old)
    db.migrate(old)                                    # safe to run twice
    assert old.execute("SELECT title, label, detailed FROM posts").fetchall() == [("kept", None, 0)]

    before = client.get("/api/tracks").json()
    db.execute("UPDATE posts SET detailed=2, label=NULL, embed=NULL, posted_at=NULL")   # what an upgraded database looks like
    db.execute("DELETE FROM tracks")
    assert all(r["track_id"] is None for r in client.get("/api/tracks").json())
    for _ in range(2):                                 # BANDCAMP_MAX_NEW=2, three releases
        client.post("/api/refresh")
        wait(client)
    strip = lambda rows: [{k: v for k, v in r.items() if k != "track_id"} for r in rows]
    assert strip(client.get("/api/tracks").json()) == strip(before)


def test_release_tracked_through_two_pages_shows_once(client):
    before = client.get("/api/tracks").json()
    label = client.post("/api/artists", json={"accounts": [{"source": "bandcamp", "handle": "deeplabel"}]}).json()["id"]
    wait(client)
    assert next(a for a in client.get("/api/artists").json() if a["id"] == label)["post_count"] == 1
    strip = lambda rows: [(r["release"], r["track"], r["label"]) for r in rows]
    assert strip(client.get("/api/tracks").json()) == strip(before)                 # no second copy
    assert [p["title"] for p in client.get("/api/feed").json()].count("First") == 1
    assert [r["track"] for r in client.get(f"/api/tracks?artist_id={label}").json()] == ["Intro", "Long One"]
    assert [p["title"] for p in client.get(f"/api/feed?artist_id={label}").json()] == ["First"]
    assert len(client.get("/api/tracks.csv").content.decode("utf-8-sig").splitlines()) == 7
    client.delete(f"/api/artists/{label}")
    assert strip(client.get("/api/tracks").json()) == strip(before)                 # the other copy is still there


def test_single_release_and_errors(client):
    assert client.post("/api/artists", json={"name": "Solo", "accounts": [{"source": "bandcamp", "handle": "solo"}]}).status_code == 201
    assert client.post("/api/artists", json={"accounts": [{"source": "bandcamp", "handle": "solo"}]}).status_code == 409
    assert client.post("/api/artists", json={"name": "x", "accounts": []}).status_code == 400
    r = client.post("/api/artists", json={"name": "Ghost", "accounts": [{"source": "soundcloud", "handle": "ghost"}]})
    wait(client)
    solo = client.get("/api/feed?artist_id=2").json()
    assert solo[0]["title"] == "Only One" and solo[0]["url"] == "https://solo.bandcamp.com/album/only-one"
    ghost = next(a for a in client.get("/api/artists").json() if a["id"] == r.json()["id"])
    assert "not found" in ghost["accounts"][0]["last_error"]


def test_expired_soundcloud_key_is_replaced(client):
    from app import db
    db.kv_set("soundcloud_client_id", "b" * 32)
    client.post("/api/refresh")
    wait(client)
    assert db.kv_get("soundcloud_client_id") == CID
    sc = next(a for a in client.get("/api/artists").json()[1]["accounts"] if a["source"] == "soundcloud")
    assert sc["last_error"] is None


def test_remove(client):
    artist_id = client.get("/api/artists").json()[1]["id"]
    assert client.delete(f"/api/artists/{artist_id}").status_code == 200
    assert all(p["artist_id"] != artist_id for p in client.get("/api/feed").json())
    assert client.delete(f"/api/artists/{artist_id}").status_code == 404


def test_table(client):
    client.put("/api/table", json={"rows": []})        # start from an empty list
    made = client.put("/api/table", json={"rows": [
        {"name": "", "cells": {"bandcamp": "nightferry", "soundcloud": "nightferry"}}]})
    assert made.json()["added"] == 2
    wait(client)
    client.post("/api/refresh")                        # second pass loads the third release
    wait(client)
    rows = client.get("/api/table").json()
    assert rows[0]["name"] == "Night Ferry" and rows[0]["cells"] == {"bandcamp": "nightferry", "soundcloud": "nightferry", "youtube": ""}
    before = len(client.get("/api/feed?source=bandcamp").json())
    nf = rows[0]["id"]

    # Invalid rows: nothing is saved, each problem is tied to its row.
    bad = client.put("/api/table", json={"rows": [
        {"id": nf, "name": "Renamed", "cells": {"bandcamp": "nightferry", "soundcloud": ""}},
        {"name": "Dup", "cells": {"bandcamp": "https://nightferry.bandcamp.com"}},
        {"name": "Bad", "cells": {"soundcloud": "not a profile!"}}]})
    assert bad.status_code == 400 and sorted(e["row"] for e in bad.json()["errors"]) == [1, 2]
    assert client.get("/api/table").json() == rows

    # Rename, drop one page, add a new artist with two pages in one cell, ignore a blank row.
    ok = client.put("/api/table", json={"rows": [
        {"id": nf, "name": "NF", "cells": {"bandcamp": "nightferry", "soundcloud": ""}},
        {"name": "", "cells": {}},
        {"name": "", "cells": {"bandcamp": "solo, nightferry2", "soundcloud": "soundcloud.com/nightferry"}}]})
    assert ok.status_code == 200 and ok.json()["added"] == 3
    wait(client)
    rows = client.get("/api/table").json()
    assert [r["name"] for r in rows] == ["NF", "Night Ferry"]      # blank name filled from the page
    assert rows[0]["cells"] == {"bandcamp": "nightferry", "soundcloud": "", "youtube": ""}
    assert rows[1]["cells"] == {"bandcamp": "solo, nightferry2", "soundcloud": "nightferry", "youtube": ""}
    assert any("not found" in p for p in rows[1]["problems"])      # nightferry2 does not exist
    assert len(client.get(f"/api/feed?artist_id={nf}&source=bandcamp").json()) == before  # untouched page keeps its posts
    assert client.get(f"/api/feed?artist_id={nf}&source=soundcloud").json() == []

    csv_text = client.get("/api/artists.csv").text.splitlines()
    assert csv_text[0] == "name,bandcamp,soundcloud,youtube" and csv_text[2] == 'Night Ferry,"solo, nightferry2",nightferry,'

    # Leaving a row out removes that artist; an empty table clears the list.
    assert client.put("/api/table", json={"rows": [{"id": nf, "name": "NF", "cells": {"bandcamp": "nightferry"}}]}).status_code == 200
    assert [r["name"] for r in client.get("/api/table").json()] == ["NF"]
    assert client.put("/api/table", json={"rows": []}).json()["rows"] == []
    assert client.get("/api/feed").json() == []


def test_edits_remixes_and_bootlegs_belong_to_whoever_made_them(client):
    from app import lookup
    credits = lookup.remix_credits
    assert credits("Temperature (River Dub Edit)") == ("River", "River Dub")          # the bare name first
    assert credits("Exotermic Reaction (Jah Beer Dub Refix)") == ("Jah Beer", "Jah Beer Dub")
    assert credits("Tune (Someone UKG Halftime Flip)") == ("Someone", "Someone UKG Halftime")
    assert credits("You Owe Me (Nightmare & Oni 140 Bootleg)") == ("Nightmare & Oni",)
    assert credits("Outside The Box [IOWLA flip]") == ("IOWLA",)
    for plain_version in ("State I (Original Mix)", "Blur (VIP)", "Endgame (Blame)", "X (Extended Dub Mix)", "Y (Instrumental)", "Z"):
        assert credits(plain_version) == ()
    assert lookup.remixer_names("You Owe Me (Nightmare & Oni 140 Bootleg)") == ("nightmare oni", "nightmare", "oni")

    edit = "Temperature (River Dub Edit)"
    verdict = lambda artist, title, cand: lookup.judge(*lookup.score(artist, title, cand))
    assert verdict("Sean Paul", edit, {"name": "Temperature", "artists": ["Sean Paul"]}) == ""              # the original isn't the edit
    assert verdict("Sean Paul", edit, {"name": "Temperature (Someone Else Remix)", "artists": ["Sean Paul"]}) == ""
    assert verdict("Sean Paul", edit, {"name": "Temperature (River Dub Edit)", "artists": ["River Dub"]}) == "bandcamp"
    assert verdict("Sean Paul", edit, {"name": "Sean Paul - Temperature [River Dub edit]", "artists": ["Some Label"]}) == "bandcamp"
    assert verdict("Dayzero", "Blur", {"name": "Blur (Someone Remix)", "artists": ["Dayzero"]}) == "bandcamp_check"
    assert verdict("Dayzero", "Blur", {"name": "Blur (Original Mix)", "artists": ["Dayzero"]}) == "bandcamp"
    # The page words it differently: "bootleg" outside the brackets, and the original artist restated in them
    page = {"name": "Oblivion bootleg (by Astor Piazzolla as performed by Miguel Fajardo)", "artists": ["Sentient"],
            "context": ["Oblivion bootleg (by Astor Piazzolla as performed by Miguel Fajardo)"]}
    asked = "Oblivion (as performed by Miguel Fajardo) (sentient bootleg)"
    assert verdict("Astor Piazzolla", asked, page) == "bandcamp"
    assert verdict("Astor Piazzolla", "Oblivion (as performed by Miguel Fajardo)", page) == "bandcamp_check"    # someone's version
    assert verdict("Astor Piazzolla", "Oblivion (as performed by Miguel Fajardo) (Other Guy bootleg)", page) == ""

    def run(rows):
        text = "Artist\tTitle\n" + "\n".join(f"{a}\t{t}" for a, t in rows)
        j = client.post("/api/lookup?fresh=true", content=text.encode()).json()
        for _ in range(300):
            time.sleep(0.02)
            got = client.get(f"/api/lookup/{j['id']}").json()
            if got["finished"]:
                return {(r["artist"], r["title"]): r for r in got["rows"]}
        raise AssertionError("lookup never finished")

    rows = [("Sean Paul", "Temperature (River Dub Edit)"), ("Nas", "You Owe Me (Nightmare & Oni 140 Bootleg)"),
            ("Omega Nebula", "Outside The Box (IOWLA Flip)"), ("Masot", "02 (Somebody Edit)")]
    client.put("/api/lookup/settings", json={"fallback": "soundcloud"})
    try:
        r = run(rows)
        # not on Bandcamp: the remixer's profile, not Sean Paul's page (which exists, and has the original)
        # ...and of the many profiles called "River", the one that uploaded this edit - not the most followed
        # "River", not somebody called "River Dub", and not a channel that reposted it
        sean = r[rows[0]]
        assert (sean["status"], sean["url"], sean["year"]) == (
            "soundcloud_track", "https://soundcloud.com/riverirvine/temperature-river-dub-edit", "2026"), sean   # the track itself
        assert sean["note"] == "Free download on the remixer's SoundCloud" and sean["found"].startswith("River \u2014 Sean Paul - Temperature")
        # found on the remixer's own Bandcamp page, where it is listed under the original artist's name
        nas = r[rows[1]]
        assert (nas["status"], nas["url"]) == ("bandcamp", "https://nightmare.bandcamp.com/album/bootlegs")
        # nothing anywhere for the remixer: falls back to the artist
        omega = r[rows[2]]
        assert (omega["status"], omega["url"]) == ("soundcloud", "https://soundcloud.com/omeganebula")
        assert any('no page found for "IOWLA"' in s for s in omega["steps"])
        # you own the original "02", but that is not Somebody's edit of it
        assert r[rows[3]]["status"] not in ("bandcamp", "bandcamp_check")

        # Preferring Bandcamp pages: the remixer still comes before the artist, on whichever site they are on.
        client.put("/api/lookup/settings", json={"fallback": "bandcamp"})
        assert client.get("/api/lookup/settings").json() == {"fallback": "bandcamp"}
        again = run(rows[:1])[rows[0]]
        assert (again["status"], again["url"]) == ("soundcloud_track", "https://soundcloud.com/riverirvine/temperature-river-dub-edit")

        client.put("/api/lookup/settings", json={"fallback": "soundcloud"})
        more = run([("Nas", "Unfindable (Nightmare & Oni 140 Bootleg)"), ("Common Name", "Some Tune"), ("Solo Guy", "Whatever")])
        # a duo with one profile: the profile named for both, not a bigger one named for one of them
        duo = more[("Nas", "Unfindable (Nightmare & Oni 140 Bootleg)")]
        assert (duo["status"], duo["url"]) == ("soundcloud", "https://soundcloud.com/nightmareandoni"), duo
        assert "matched by name only" in duo["note"]
        # an ordinary track: the artist's profile is the one that uploaded it
        tune = more[("Common Name", "Some Tune")]
        assert (tune["status"], tune["url"], tune["year"], tune["note"]) == (
            "soundcloud_track", "https://soundcloud.com/the-real-common-name/some-tune", "2024", "On the artist's SoundCloud")
        assert more[("Solo Guy", "Whatever")]["note"] == "Matched by name only"
    finally:
        client.put("/api/lookup/settings", json={"fallback": "bandcamp"})


def test_soundcloud_is_the_default_fallback():
    from app import db, lookup
    saved, env = db.kv_get("lookup_fallback"), os.environ.pop("LOOKUP_FALLBACK")
    try:
        db.execute("DELETE FROM kv WHERE key='lookup_fallback'")
        assert lookup.fallback_pref() == "soundcloud"
    finally:
        os.environ["LOOKUP_FALLBACK"] = env
        if saved:
            db.kv_set("lookup_fallback", saved)


def test_label_releases_and_unlisted_tracks(client):
    from app import lookup
    assert lookup.slugify("Hannibal Rex Manyothers") == "hannibal-rex-manyothers"
    assert lookup.slugify("Especies Endémicas (Live!)") == "especies-endemicas-live"
    # Release lists of pages you know, as the background reader saves them.
    lookup.cache_put("grid", "https://dungeonbeats.bandcamp.com", {"name": "Dungeon Beats", "items": [
        {"title": "Hannibal Rex - An Older One", "url": "https://dungeonbeats.bandcamp.com/track/hannibal-rex-an-older-one", "artist": ""},
        {"title": "Someone Else - Thing", "url": "https://dungeonbeats.bandcamp.com/track/someone-else-thing", "artist": ""}]})
    lookup.cache_put("grid", "https://labelx.bandcamp.com", {"name": "Label X", "items": [
        {"title": "LX001", "url": "https://labelx.bandcamp.com/album/lx001", "artist": "Somebody"}]})
    rows = [("Howman", "Strip Search"), ("Hannibal Rex", "Manyothers"), ("Somebody", "Catalogue Tune"), ("Hannibal Rex", "Never Existed")]
    j = client.post("/api/lookup?fresh=true", content=("Artist\tTitle\n" + "\n".join(f"{a}\t{t}" for a, t in rows)).encode()).json()
    for _ in range(300):
        time.sleep(0.02)
        got = client.get(f"/api/lookup/{j['id']}").json()
        if got["finished"]:
            break
    r = {(x["artist"], x["title"]): x for x in got["rows"]}
    # credited to the label; found by searching for the artist and opening the release that names them
    howman = r[rows[0]]
    assert (howman["status"], howman["url"], howman["year"]) == (
        "bandcamp", "https://dnorecords.bandcamp.com/album/dno022-howman-trap-eastwood-ep", "2026"), howman
    # not listed anywhere: found at the address it would have on a label page that carries the artist
    hannibal = r[rows[1]]
    assert (hannibal["status"], hannibal["url"], hannibal["year"]) == (
        "bandcamp", "https://dungeonbeats.bandcamp.com/track/hannibal-rex-manyothers", "2026"), hannibal
    assert "not publicly listed" in hannibal["note"]
    # listed on a label page you know, under the artist's name: opened and matched without any search hit
    known = r[rows[2]]
    assert (known["status"], known["url"], known["note"]) == ("bandcamp", "https://labelx.bandcamp.com/album/lx001", "On a label page you know")
    assert r[rows[3]]["status"] not in ("bandcamp", "bandcamp_check")
    status = client.get("/api/collection").json()
    assert status["labels"] >= 1 and 0 <= status["labels_read"] <= status["labels"]


def test_track_bought_on_its_own_links_to_its_album(client):
    from app import db, lookup
    from app.sources.bandcamp import parse_release
    url, album = "https://shakhtarecords.bandcamp.com/track/dead-angle", "https://shakhtarecords.bandcamp.com/album/shkht013-johney"
    assert parse_release(DEAD_ANGLE, url)["release_url"] == album
    db.execute("INSERT INTO collection_items(key, fan, title, band, url, kind) VALUES('t998','x','Dead Angle','JOHNEY',?,'t')", (url,))
    # the page as an older version saved it: no album
    lookup.cache_put("release", url, {"url": url, "asked": url, "title": "Dead Angle", "artist": "JOHNEY", "year": "2026",
                                      "release": "SHKHT013 - JOHNEY", "release_url": "", "label": "", "tracks": [], "v": 2})
    try:
        j = client.post("/api/lookup?fresh=true", content="Artist\tTitle\nJohney\tDead Angle".encode()).json()
        for _ in range(100):
            time.sleep(0.05)
            got = client.get(f"/api/lookup/{j['id']}").json()
            if got["finished"]:
                break
        row = got["rows"][0]
        assert (row["status"], row["url"], row["release"], row["year"]) == ("bandcamp", album, "SHKHT013 - JOHNEY", "2026"), row
    finally:
        db.execute("DELETE FROM collection_items WHERE key='t998'")


def test_vip_is_not_the_original(client):
    """Owning the EP with the original on it is not an answer for the VIP version."""
    from app import db, lookup
    url = "https://toomuchcollective.bandcamp.com/album/re-use-reduce-ep"
    db.execute("INSERT INTO collection_items(key, fan, title, band, url, kind) VALUES('a997','x','Re-use, Reduce EP','Anecho',?,'a')", (url,))
    db.execute("INSERT INTO collection_tracks(item_key, position, title, artist) VALUES('a997',1,'Re-use, Reduce','Anecho')")
    lookup.cache_put("release", url, {"url": url, "asked": url, "title": "Re-use, Reduce EP", "artist": "Anecho", "year": "2025",
                                      "release": "", "release_url": "", "label": "",
                                      "tracks": [{"title": "Re-use, Reduce", "artist": ""}, {"title": "Other One", "artist": ""}], "v": 3})
    lookup.page_index.cache_clear() if hasattr(lookup.page_index, "cache_clear") else None
    try:
        j = client.post("/api/lookup?fresh=true", content=(
            "Artist\tTitle\nAnecho\tRe-use, Reduce (Continental VIP)\nAnecho\tRe-use, Reduce").encode()).json()
        for _ in range(200):
            time.sleep(0.05)
            got = client.get(f"/api/lookup/{j['id']}").json()
            if got["finished"]:
                break
        vip, original = got["rows"]
        assert (original["status"], original["url"]) == ("bandcamp", url), original
        # not the EP: falls back to the artist (the tests run with the Bandcamp page preferred)
        assert vip["status"] in lookup.FALLBACK_STATUSES and vip["url"] != url, vip
        assert vip["options"]["soundcloud"]["url"] == "https://soundcloud.com/anechoicchamber", vip
    finally:
        db.execute("DELETE FROM collection_items WHERE key='a997'")
        db.execute("DELETE FROM collection_tracks WHERE item_key='a997'")


def test_missing_pages_are_suggested(client):
    def ask(name="", **cells):
        r = client.post("/api/table/suggest", json={"name": name, "cells": cells})
        assert r.status_code == 200, r.text
        return r.json()
    got = ask("Nightmare")
    assert got["cells"].get("bandcamp") == "nightmare" and got["name"] == ""
    got = ask("Solo Guy")
    assert got["cells"].get("soundcloud") == "sologuy-official" and "no Bandcamp page found" in got["note"]
    # two profiles share the name: the one whose address matches the Bandcamp page given
    assert ask("River", bandcamp="riverirvine")["cells"] == {"soundcloud": "riverirvine"}
    assert ask("River")["cells"].get("soundcloud") == "river"            # nothing to go on: the bigger one
    # only a link: the name comes from the page; cells already filled are left alone
    got = ask("", bandcamp="nightferry")
    assert got["name"] == "Night Ferry" and "bandcamp" not in got["cells"]
    assert ask("Nightmare", bandcamp="x", soundcloud="y")["cells"] == {}


def test_cleared_cells_and_rejected_guesses_are_remembered(client):
    ask = lambda **body: client.post("/api/table/suggest", json=body).json()
    assert ask(name="River", cells={}, skip=["soundcloud"])["cells"].get("soundcloud") is None     # left alone
    # a guess turned down is not offered again: the next best profile comes instead
    assert ask(name="River", cells={}, avoid={"soundcloud": ["river"]})["cells"]["soundcloud"] == "riverirvine"
    assert "bandcamp" not in ask(name="Nightmare", cells={}, avoid={"bandcamp": ["https://nightmare.bandcamp.com"]})["cells"]
    # saved with the table, by artist name, and dropped once the cell is filled or the row is gone
    rows = [{"id": r["id"], "name": r["name"], "cells": r["cells"]} for r in client.get("/api/table").json()]
    extra = {"id": None, "name": "Solo Guy", "cells": {"soundcloud": "sologuy-official"}, "skip": ["bandcamp"],
             "rejected": {"bandcamp": ["sologuy"]}}
    saved = client.put("/api/table", json={"rows": rows + [extra]}).json()["rows"]
    mine = next(r for r in saved if r["name"] == "Solo Guy")
    assert mine["skip"] == ["bandcamp"] and mine["rejected"] == {"bandcamp": ["sologuy"]}
    assert next(r for r in client.get("/api/table").json() if r["name"] == "Solo Guy")["skip"] == ["bandcamp"]
    client.put("/api/table", json={"rows": rows})
    assert all(r["name"] != "Solo Guy" and not r["skip"] for r in client.get("/api/table").json())


def test_numbers_in_titles_must_agree():
    """Allen Mock's "Hybrid Kit 6" is on Epoch Remixes; Epoch only has Hybrid Kit, 2 and 3."""
    from app import lookup
    page = lambda *names: {"url": "https://allenmock.bandcamp.com/album/x", "title": "X", "artist": "Allen Mock",
                           "tracks": [{"title": n, "artist": ""} for n in names]}
    epoch = page("Vim", "Hybrid Kit", "Hybrid Kit 2", "Hybrid Kit 3", "Beat 0538", "10,000h")
    remixes = page("Hybrid Kit 4", "Hybrid Kit 5", "Hybrid Kit 6", "Hybrid Kit (Cumulus Frisbee Remix)")
    for asked in ("Hybrid Kit 6", "Hybid Kit 6"):                      # the second as mistyped in a list
        assert lookup.match_in_release("Allen Mock", asked, epoch)[0] == ""
        assert lookup.match_in_release("Allen Mock", asked, remixes) == ("bandcamp", "Hybrid Kit 6")
    assert lookup.match_in_release("Allen Mock", "Hybrid Kit", epoch) == ("bandcamp", "Hybrid Kit")
    assert lookup.match_in_release("Allen Mock", "Beat 538", epoch)[0] == "bandcamp"
    assert lookup.match_in_release("Allen Mock", "10000h", epoch)[0] == "bandcamp"


def test_date_range_and_result_cap(client):
    client.post("/api/artists", json={"accounts": [
        {"source": "bandcamp", "handle": "nightferry"}, {"source": "soundcloud", "handle": "nightferry"}]})
    wait(client)
    every = client.get("/api/tracks?limit=1000").json()
    assert len(every) >= 3
    days = sorted({(r["posted"] or r["released"] or "")[:10] for r in every} - {""})
    mid = days[len(days) // 2]
    got = client.get(f"/api/tracks?limit=1000&since={mid}").json()
    assert got and len(got) < len(every) and all((r["posted"] or r["released"])[:10] >= mid for r in got)
    got = client.get(f"/api/tracks?limit=1000&until={days[0]}&date_by=released").json()
    assert all(r["released"][:10] <= days[0] for r in got)
    assert len(client.get(f"/api/feed?since={mid}").json()) < len(client.get("/api/feed").json())
    assert client.get("/api/tracks?since=not-a-date&limit=1000").json() == every          # ignored, not an error
    lines = client.get("/api/tracks.csv?max=2").content.decode("utf-8-sig").strip().splitlines()
    assert len(lines) == 3                                                               # header + 2


def test_wip_is_not_part_of_a_title():
    from app import lookup
    for written in ("Sunken (WIP)", "Sunken [WIP v3]", "Sunken WIP", "Sunken - wip", "Sunken (Work In Progress)"):
        assert lookup.norm(written) == "sunken", written
    assert lookup.norm("Swip Wip Song") == "swip wip song" and lookup.norm("Wipeout") == "wipeout"
    assert lookup.judge(*lookup.score("A", "Sunken WIP", {"name": "Sunken", "artists": ["A"]})) == "bandcamp"


def test_unreadable_releases_do_not_hold_up_a_back_catalogue(client):
    """Two release pages that always fail used to take both of a refresh's slots, every time."""
    import asyncio
    from app import db
    from app.sources import SOURCES
    grid = '<html><body><p id="band-name-location"><span class="title">Stuck Label</span></p><ol id="music-grid">' + "".join(
        f'<li data-item-id="album-{n}" class="music-grid-item"><a href="/album/r{n}"><div class="art"><img src="https://f4.bcbits.com/img/a{n}_2.jpg"></div>'
        f'<p class="title">R{n}</p></a></li>' for n in range(1, 6)) + "</ol></body></html>"

    def site(request):
        path = request.url.path
        if path == "/music":
            return httpx.Response(200, text=grid)
        if path in ("/album/r1", "/album/r2"):
            return httpx.Response(500)
        return httpx.Response(200, text=release(path[-2:].upper(), "07 Mar 2025 00:00:00 GMT"))

    account = {"id": 987654, "url": "https://stucklabel.bandcamp.com", "meta": {}}
    known, warnings = set(), []

    async def rounds():
        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as c:
            for _ in range(7):
                got = await SOURCES["bandcamp"].fetch(c, account, known)
                known.update(p.external_id for p in got.posts)
                warnings.append(got.warning)
    try:
        asyncio.run(rounds())
    finally:
        db.execute("DELETE FROM kv WHERE key='bandcamp_unread:987654'")
    assert {u.rsplit("/", 1)[1] for u in known} == {"r3", "r4", "r5"}, (known, warnings)   # 2 per round in the tests
    assert "2 release page(s) could not be read" in warnings[-1] and "still to load" not in warnings[-1]
    assert "still to load" in warnings[0]


def test_brackets_that_only_describe_the_format_are_not_part_of_a_title():
    """Bare Dubs sells "Voyage (Digital Download)" by Deapoh; a tracklist just says "Voyage"."""
    from app import lookup
    page = {"url": "https://baredubs.bandcamp.com/track/deapoh-voyage-digital-download", "title": "Voyage (Digital Download)",
            "artist": "Deapoh", "tracks": [{"title": "Voyage (Digital Download)", "artist": ""}]}
    assert lookup.match_in_release("Deapoh", "Voyage", page)[0] == "bandcamp"
    assert lookup.norm("Voyage [FREE DL]") == lookup.norm("Voyage (Out Now)") == "voyage"
    assert lookup.norm("Voyage (Part 2)") == "voyage part 2" and lookup.norm("Voyage (Night)") == "voyage night"
    assert lookup.norm("(Intro)") == "intro"                      # a title that is all bracket stays


def test_profile_learned_from_a_found_track_is_reused(client):
    """Two profiles are called River. A track of his found on SoundCloud settles which one is meant."""
    from app import lookup

    def run(text, fresh=True):
        j = client.post("/api/lookup" + ("?fresh=true" if fresh else ""), content=("Artist\tTitle\n" + text).encode()).json()
        for _ in range(200):
            time.sleep(0.05)
            got = client.get(f"/api/lookup/{j['id']}").json()
            if got["finished"]:
                return got["rows"]
        raise AssertionError("lookup never finished")

    sc = lambda row: (row.get("options") or {}).get("soundcloud") or row
    lookup.db.kv_set("soundcloud_profiles", "{}")
    before = sc(run("River\tBoogaloo")[0])
    assert before["url"] == "https://soundcloud.com/river" and "name only" in before["note"].lower()   # the bigger one: wrong
    run("Sean Paul\tTemperature (River Dub Edit)")                 # found on SoundCloud, uploaded by riverirvine
    assert lookup.known_profile(["river"]) == "https://soundcloud.com/riverirvine"
    # the saved wrong answer is corrected without searching again, and new rows use what was learned
    for row in run("River\tBoogaloo", fresh=False) + run("River\tBoogaloo VIP") + run("River\tSomething Else"):
        assert sc(row)["url"] == "https://soundcloud.com/riverirvine", row
    # a link you set by hand settles it too, and outweighs the rest
    client.put("/api/lookup/link", json={"artist": "Nobody Known", "title": "Whatever", "url": "https://soundcloud.com/the-real-nobody/whatever"})
    assert lookup.known_profile(["nobody known"]) == "https://soundcloud.com/the-real-nobody"


def test_saved_search_answers_review_flags_and_searching_one_row_again(client):
    from app import lookup

    def finish(j):
        for _ in range(200):
            time.sleep(0.05)
            got = client.get(f"/api/lookup/{j['id']}").json()
            if got["finished"]:
                return got
        raise AssertionError("lookup never finished")

    def run(text, query=""):
        return finish(client.post("/api/lookup" + query, content=("Artist\tTitle\n" + text).encode()).json())

    lookup.db.kv_set("soundcloud_profiles", "{}")
    job = run("River\tUnheard Of Tune", "?fresh=true")
    row = job["rows"][0]
    # picked by name alone: flagged for review, on screen and in the export
    assert "name alone" in row["review"] or "name alone" in lookup.review_reason(dict(row, **(row.get("options") or {}).get("soundcloud", {})))
    asked = len(searched)
    # the saved answer thrown away (as a new version does): searched again, but from Bandcamp's saved
    # search answers - nothing new is asked of its search
    lookup.db.execute("DELETE FROM lookup_cache WHERE key=?", (lookup._cache_key("River", "Unheard Of Tune"),))
    run("River\tUnheard Of Tune")
    assert len(searched) == asked
    run("River\tUnheard Of Tune", "?retry=missing")              # asked for: Bandcamp is searched anew
    assert len(searched) > asked
    asked = len(searched)
    again = client.post(f"/api/lookup/{job['id']}/retry", json={"n": 1})
    assert again.status_code == 200 and again.json()["finished"] is False and again.json()["rows"][0]["status"] == "pending"
    with pytest.raises(ValueError):                                # not while a lookup is still running
        lookup.retry_row({"finished": False, "rows": [{"n": 1, "artist": "a", "title": "b"}], "done": 0}, 1)
    assert client.post(f"/api/lookup/{job['id']}/retry", json={"n": 99}).status_code == 409
    done = finish(job)
    assert done["rows"][0]["status"] != "pending" and len(searched) > asked and done["done"] == done["total"]
    # a partial Bandcamp match is flagged too; a link you set is not
    assert lookup.review_reason({"status": "bandcamp_check"}) and not lookup.review_reason({"status": "manual"})
    csv_text = client.get(f"/api/lookup/{job['id']}.csv").content.decode("utf-8-sig")
    assert "needs review" in csv_text.splitlines()[0]


def test_each_version_of_a_title_has_its_own_saved_answer():
    from app import lookup
    k = lookup._cache_key
    assert k("Sean Paul", "Temperature") != k("Sean Paul", "Temperature (River Dub Edit)")
    assert k("River", "Boogaloo") != k("River", "Boogaloo VIP") == k("River", "Boogaloo (VIP)")
    assert k("Anecho", "Re-use, Reduce (Continental VIP)") != k("Anecho", "Re-use, Reduce VIP")
    # plain titles keep the key they always had, so nothing saved for them is lost
    assert k("Deapoh", "Voyage (Digital Download)") == k("deapoh", "VOYAGE") == "v2|deapoh|voyage"
    # a link set by hand before this change (stored under the plain title) is still honoured
    lookup.db.execute("INSERT OR REPLACE INTO lookup_cache(key, result, at) VALUES(?,?,?)",
                      ("manual|old one|tune", '{"status": "manual", "url": "https://x.bandcamp.com/track/tune-remix"}', lookup.db.now()))
    assert lookup._manual_for("Old One", "Tune (Someone Remix)")["url"].endswith("tune-remix")
    lookup.db.execute("DELETE FROM lookup_cache WHERE key='manual|old one|tune'")


def test_your_list_spellings_links_and_notes(client):
    from app import lookup
    assert lookup.split_link_cell("https://x.bandcamp.com/track/y (limited time only)") == (["https://x.bandcamp.com/track/y"], "limited time only")
    assert lookup.split_link_cell("(Patreon)") == ([], "Patreon")
    assert lookup.split_link_cell("soundcloud.com/someone/tune - free DL") == (["https://soundcloud.com/someone/tune"], "free DL")
    sheet = "\n".join(["Artist\tTitle\tLink\tNotes",
                        "MASOT\tEspecies Endémicas\thttps://old.example.com/dead-link (outdated)\t",
                        "Nobody Known\tListed Tune\thttps://nobodyknown.bandcamp.com/track/listed-tune (limited time only)\tvinyl only",
                        "Nobody Known\tSC Only Tune\tsoundcloud.com/nobodyknown/sc-only-tune (Patreon)\t",
                        "NOBODY KNOWN\tListed Tune\t\t",                      # a second spelling of the same track
                        "aNeCHo\t\t\t"])                                       # an artist's stylisation alone
    got = client.post("/api/known", content=sheet.encode()).json()
    assert (got["tracks"], got["artists"], got["with_link"], got["clashes"]) == (3, 3, 3, 2), got
    assert got["clash_examples"][0]["kept"] == "Nobody Known - Listed Tune" or got["clash_examples"][0]["what"] == "artist"

    def run(text, query="?fresh=true"):
        j = client.post("/api/lookup" + query, content=("Artist\tTitle\n" + text).encode()).json()
        for _ in range(200):
            time.sleep(0.05)
            now = client.get(f"/api/lookup/{j['id']}").json()
            if now["finished"]:
                return now
        raise AssertionError("lookup never finished")

    asked = len(searched)
    job = run("masot\tespecies endemicas\nnobody known\tlisted tune\nNobody Known\tSC Only Tune\nAnecho\tUFO")
    found, listed, sc_only, other = job["rows"]
    # what the lookup finds itself beats the list's link; the spelling still comes from the list
    assert found["status"] == "bandcamp" and "masot.bandcamp.com" in found["url"]
    assert (found["list_artist"], found["list_title"], found["list_note"]) == ("MASOT", "Especies Endémicas", "outdated")
    # a Bandcamp release link from the list answers the row without a search
    assert (listed["status"], listed["url"]) == ("imported", "https://nobodyknown.bandcamp.com/track/listed-tune")
    assert "limited time only" in listed["note"] and "vinyl only" in listed["list_note"]
    # a SoundCloud link from the list: Bandcamp is searched first, and the list's link used when that finds nothing
    assert (sc_only["status"], sc_only["url"]) == ("imported", "https://soundcloud.com/nobodyknown/sc-only-tune")
    assert sc_only["list_note"] == "Patreon" and len(searched) > asked
    assert other["list_artist"] == "aNeCHo" and other["list_title"] == ""
    csv_lines = client.get(f"/api/lookup/{job['id']}.csv").content.decode("utf-8-sig").splitlines()
    assert "MASOT,Especies Endémicas,outdated" in csv_lines[1]

    saved_all = client.get("/api/saved?limit=1000").json()
    by_key = {r["key"]: r for r in saved_all["rows"]}
    assert by_key["masot|especies endemicas"]["title"].lower().startswith("especies") and by_key["masot|especies endemicas"]["list_title"] == "Especies Endémicas"
    assert by_key["nobody known|listed tune"]["status"] == "imported"
    assert saved_all["counts"]["list"] == 3 and saved_all["known_tracks"] == 3
    only = client.get("/api/saved?q=nobody listed").json()
    assert only["total"] == 1 and only["rows"][0]["list_note"] == "limited time only; vinyl only"
    assert "artist (your spelling)" in client.get("/api/saved.csv").content.decode("utf-8-sig").splitlines()[0]
    assert client.delete("/api/saved", params={"key": "masot|especies endemicas"}).json()["removed"] >= 1
    assert "masot|especies endemicas" in {r["key"] for r in client.get("/api/saved?limit=1000&filter=unsearched").json()["rows"]}
    assert client.delete("/api/known").json() == {"known_tracks": 0, "known_artists": 0}


def test_your_list_years_repeated_rows_versions_and_stray_columns(client):
    from app import lookup
    sheet = "\n".join([
        "Artist,Track,Year,Link,,,,",
        "ØZ,AK-47,2022,https://soundcloud.com/ozdub,,,,",                              # the same track three times:
        "ØZ,AK-47,2022,https://ozdub.bandcamp.com/album/d-blk002,,,,",                   # the release link wins
        "Øz,AK-47,,,,,,",
        "baron,Eski,2021,https://baron.bandcamp.com/album/spear,,,,https://elsewhere.example.com/not-this-row",
        "baron,Eski [alternate version],2024,https://widdfam.bandcamp.com/album/cosmic (limited time),,,,",
        "Töki,Zimmy (forth. DoD),2026,forthcoming https://soundcloud.com/toki/zimmy,,,,",
        "Darkai,011 Dub,2024,Label Patreon exclusive (eventually available at https://www.patreon.com/label/shop),,,,",
        "Somebody,Gone Tune,2019,sold out USB001; no link yet,,,,",
        "Hippoflip,???,2020,https://soundcloud.com/hippoflip,,,,"])
    got = client.post("/api/known", content=sheet.encode("cp1252")).json()         # as Excel saves a plain CSV
    assert (got["tracks"], got["with_link"], got["release_links"], got["skipped"]) == (6, 5, 3, 1), got
    assert (got["link_clashes"], got["spelling_clashes"]) == (1, 2), got
    oz = lookup.known_for("oz", "ak 47")                                           # typed without the special letter
    assert (oz["artist"], oz["title"], oz["year"], oz["url"]) == ("ØZ", "AK-47", "2022", "https://ozdub.bandcamp.com/album/d-blk002")
    plain_one, other = lookup.known_for("Baron", "Eski"), lookup.known_for("Baron", "eski (alternate version)")
    assert (plain_one["url"], plain_one["others"], plain_one["year"]) == ("https://baron.bandcamp.com/album/spear", "", "2021")
    assert (other["title"], other["url"], other["note"], other["year"]) == ("Eski [alternate version]", "https://widdfam.bandcamp.com/album/cosmic", "limited time", "2024")
    zimmy = lookup.known_for("Toki", "Zimmy")
    assert (zimmy["title"], zimmy["url"], zimmy["note"]) == ("Zimmy (forth. DoD)", "https://soundcloud.com/toki/zimmy", "forthcoming")
    assert lookup.known_for("darkai", "011 dub")["note"] == "Label Patreon exclusive; eventually available at"
    gone = lookup.known_for("somebody", "gone tune")
    assert (gone["url"], gone["note"]) == ("", "sold out USB001; no link yet")
    j = client.post("/api/lookup", content="Artist\tTitle\noz\tak-47\nbaron\teski [alternate version]".encode()).json()
    for _ in range(200):
        time.sleep(0.05)
        now = client.get(f"/api/lookup/{j['id']}").json()
        if now["finished"]:
            break
    first, second = now["rows"]
    assert (first["status"], first["year"], first["list_artist"]) == ("imported", "2022", "ØZ")
    assert (second["url"], second["year"]) == ("https://widdfam.bandcamp.com/album/cosmic", "2024")
    assert ",2022,https://ozdub.bandcamp.com/album/d-blk002," in client.get(f"/api/lookup/{j['id']}.csv").content.decode("utf-8-sig")
    rows = client.get("/api/saved?q=baron eski&limit=50").json()["rows"]
    assert sorted(r["list_title"] for r in rows) == ["Eski", "Eski [alternate version]"]
    assert client.delete("/api/known").json() == {"known_tracks": 0, "known_artists": 0}


def test_letters_like_o_with_a_stroke_and_answers_saved_under_the_old_tidying():
    from app import db, lookup
    assert lookup.norm("Nøcase") == "nocase" and lookup.norm("KØDE-1") == "kode 1" and lookup.norm("Łódź") == "lodz"
    db.execute("INSERT OR REPLACE INTO lookup_cache(key, result, at) VALUES(?,?,?)",
               ("manual|n case|old tune", json.dumps({"status": "manual", "url": "https://x.bandcamp.com/track/old-tune"}), db.now()))
    assert lookup._manual_for("Nøcase", "Old Tune")["url"] == "https://x.bandcamp.com/track/old-tune"
    assert lookup._manual_for("Nocase", "Old Tune") is None
    db.execute("DELETE FROM lookup_cache WHERE key=?", ("manual|n case|old tune",))


def test_titles_in_other_alphabets_are_kept_when_tidied(client):
    from app import lookup
    assert lookup.norm("Сердце Темноты (ft. Talabun)") == "сердце темноты" and lookup.norm("Tiger 白虎") == "tiger 白虎"
    assert lookup.norm("Сердце (Original Mix)") == "сердце" and lookup.norm("Too_Hot") == "too hot" and lookup.norm("???") == ""
    sheet = "Artist,Track,Year,Link\nDuckem,Сердце Темноты,2015,https://soundcloud.com/x/serdtse\nDuckem,Другая Песня,2016,\n"
    got = client.post("/api/known", content=sheet.encode("utf-8-sig")).json()
    assert (got["tracks"], got["skipped"]) == (2, 0), got
    assert lookup.known_for("duckem", "сердце темноты")["url"] == "https://soundcloud.com/x/serdtse"
    assert lookup.known_for("duckem", "другая песня")["year"] == "2016"
    assert client.delete("/api/known").json() == {"known_tracks": 0, "known_artists": 0}


def test_answers_saved_under_old_keys_are_moved_and_can_be_edited(client):
    from app import db, lookup
    put = lambda key, **r: db.execute("INSERT OR REPLACE INTO lookup_cache(key, result, at) VALUES(?,?,?)", (key, json.dumps(r), db.now()))
    put("v2|river|oldaloo vip", status="soundcloud", url="https://soundcloud.com/wrong-river", found="wrong-river")
    put("v2|emalkay|when i look at old", status="soundcloud_track", url="https://soundcloud.com/kwizma/old",
        found="Kwizma — Emalkay - When I Look At Old (Kwizma Bootleg)")
    put("v2|plainman|plain old tune", status="bandcamp", url="https://plainman.bandcamp.com/track/plain-old-tune", found="Plainman — Plain Old Tune")
    assert lookup.rekey_old_answers() == 2 and lookup.rekey_old_answers() == 0
    keys = {r["key"] for r in db.query("SELECT key FROM lookup_cache")}
    assert {"v2|river|oldaloo#vip", "v2|emalkay|when i look at old#kwizma", "v2|plainman|plain old tune"} <= keys
    assert lookup._saved_for("River", "Oldaloo VIP")["url"] == "https://soundcloud.com/wrong-river"
    row = client.get("/api/saved?q=river oldaloo").json()["rows"][0]
    assert (row["key"], row["version"], row["title"]) == ("river|oldaloo#vip", "vip", "")
    got = client.put("/api/lookup/link", json={"artist": "river", "title": "oldaloo (VIP)", "url": "https://soundcloud.com/riverirvine", "key": row["key"]}).json()
    assert got["status"] == "manual"
    rows = client.get("/api/saved?q=river oldaloo").json()["rows"]
    assert len(rows) == 1 and (rows[0]["status"], rows[0]["url"]) == ("manual", "https://soundcloud.com/riverirvine")
    assert lookup._manual_for("River", "Oldaloo VIP")["url"] == "https://soundcloud.com/riverirvine"
    names = lambda which: {r["key"] for r in client.get(f"/api/saved?limit=1000&filter={which}").json()["rows"]}
    assert "emalkay|when i look at old#kwizma" in names("found") and "emalkay|when i look at old#kwizma" not in names("missing")
    put("v2|nobody|only a profile", status="soundcloud", url="https://soundcloud.com/nobody", title="Only A Profile", artist="Nobody")
    assert "nobody|only a profile" in names("missing") and "nobody|only a profile" not in names("found")
    client.delete("/api/saved", params={"key": "nobody|only a profile"})
    for k in ("river|oldaloo#vip", "emalkay|when i look at old#kwizma", "plainman|plain old tune"):
        client.delete("/api/saved", params={"key": k})


def test_remix_typed_without_brackets_falls_back_to_the_remixers_soundcloud(client):
    from app import db, lookup
    assert lookup.bare_remix_readings("Outside The Box IOWLA Flip") == ["Outside The Box (IOWLA Flip)"]
    assert lookup.bare_remix_readings("Tune Kahn & Neek Remix") == ["Tune (Kahn & Neek Remix)"]
    for plain_title in ("Radio Edit", "Tune Radio Edit", "Coin Flip", "The Final Edit", "Thing (X Remix)", "Boogaloo VIP"):
        assert lookup.bare_remix_readings(plain_title) == [], plain_title
    client.post("/api/known", content="Artist,Track,Link\nNobody Known,,\nGhost Person,Unfindable Zzz (Nobody Known Flip),\n".encode())
    assert lookup.bare_remix_readings("Unfindable Zzz Nobody Known Flip")[0] == "Unfindable Zzz (Nobody Known Flip)"
    assert lookup.known_for("ghost person", "unfindable zzz nobody known flip")["title"] == "Unfindable Zzz (Nobody Known Flip)"
    db.kv_set("lookup_fallback", "bandcamp")
    try:
        j = client.post("/api/lookup?fresh=true", content="Artist\tTitle\nGhost Person\tUnfindable Zzz Nobody Known Flip".encode()).json()
        for _ in range(200):
            time.sleep(0.05)
            now = client.get(f"/api/lookup/{j['id']}").json()
            if now["finished"]:
                break
        row = now["rows"][0]
        assert (row["status"], row["url"]) == ("soundcloud", "https://soundcloud.com/nobodyknown"), row
        assert row["note"].startswith("Remixer's profile") and any("read as" in s for s in row["steps"])
        assert row["list_title"] == "Unfindable Zzz (Nobody Known Flip)"
    finally:
        db.kv_set("lookup_fallback", "soundcloud")
        client.delete("/api/known")


def test_saved_results_can_be_sorted(client):
    from app import db
    put = lambda key, at, **r: db.execute("INSERT OR REPLACE INTO lookup_cache(key, result, at) VALUES(?,?,?)", (key, json.dumps(r), at))
    put("v2|sortb|zebra", "2026-01-03T00:00:00+00:00", status="bandcamp", url="https://b.bandcamp.com/track/zebra", year="2021", artist="SortB", title="Zebra")
    put("v2|sorta|mango", "2026-01-01T00:00:00+00:00", status="soundcloud", url="https://soundcloud.com/a", year="", artist="SortA", title="Mango")
    put("v2|sortc|apple", "2026-01-02T00:00:00+00:00", status="bandcamp", url="https://a.bandcamp.com/track/apple", year="2024", artist="SortC", title="Apple")
    def order(extra=""):
        return [r["tidy_title"] for r in client.get("/api/saved?q=sort&limit=50" + extra).json()["rows"]]
    assert order() == ["zebra", "apple", "mango"]                                  # newest first, as before
    assert order("&sort=artist") == ["mango", "zebra", "apple"]
    assert order("&sort=title&dir=desc") == ["zebra", "mango", "apple"]
    assert order("&sort=year&dir=desc") == ["apple", "zebra", "mango"]             # no year: last either way
    assert order("&sort=year") == ["zebra", "apple", "mango"]
    assert order("&sort=link") == ["apple", "zebra", "mango"]
    assert order("&sort=saved") == ["mango", "apple", "zebra"]
    lines = client.get("/api/saved.csv?q=sort&sort=artist").content.decode("utf-8-sig").splitlines()
    assert [l.split(",")[0] for l in lines[1:]] == ["sorta", "sortb", "sortc"]
    for k in ("sortb|zebra", "sorta|mango", "sortc|apple"):
        client.delete("/api/saved", params={"key": k})


def test_symbols_written_into_a_name_are_kept():
    from app import db, lookup
    assert [lookup.norm(t) for t in ("∑1", "Myca$h", "Mor£ (Instrumental)", "$uicideboy$")] == ["∑1", "myca$h", "mor£", "$uicideboy$"]
    assert [lookup.norm(t) for t in ("★ Tune ★", "Tune™", "A + B", "100% Dub", "Who's There", "1 $ Tune")] == \
        ["tune", "tune", "a b", "100 dub", "who s there", "1 tune"]
    assert lookup.track_key("∑1", "Wake Up Lisa") == "∑1|wake up lisa" != lookup.track_key("1", "Wake Up Lisa")
    db.execute("INSERT OR REPLACE INTO lookup_cache(key, result, at) VALUES(?,?,?)",          # saved before symbols were kept
               ("manual|myca h|old tune", json.dumps({"status": "manual", "url": "https://x.bandcamp.com/track/m"}), db.now()))
    assert lookup._manual_for("Myca$h", "Old Tune")["url"] == "https://x.bandcamp.com/track/m"
    db.execute("DELETE FROM lookup_cache WHERE key=?", ("manual|myca h|old tune",))
    db.execute("INSERT OR REPLACE INTO known_tracks(key, variant, artist, title, at) VALUES(?,?,?,?,?)", ("1|wake up lisa", "wake up lisa", "∑1", "Wake Up Lisa", db.now()))
    db.execute("INSERT OR REPLACE INTO known_artists(key, name, at) VALUES(?,?,?)", ("1", "∑1", db.now()))
    assert lookup.rekey_known() == 2 and lookup.rekey_known() == 0
    assert lookup.known_for("∑1", "wake up lisa")["title"] == "Wake Up Lisa" and lookup.known_for("1", "wake up lisa")["title"] == ""
    db.execute("DELETE FROM known_tracks"); db.execute("DELETE FROM known_artists"); lookup._known_cache.clear()


def test_tracked_artist_with_other_names(client):
    from app import lookup
    assert lookup.split_names('Zed Alias One, "Zed, With Comma", ZAO, zao ,') == ["Zed Alias One", "Zed, With Comma", "ZAO"]
    assert lookup.join_names(["A", "B, C"]) == 'A, "B, C"'
    before = client.get("/api/table").json()
    keep = [{"id": r["id"], "name": r["name"], "cells": r["cells"]} for r in before]
    made = client.put("/api/table", json={"rows": keep + [
        {"name": 'Zed Alias One, "Zed, With Comma", ZAO', "cells": {"soundcloud": "zed-alias-one"}}]})
    assert made.status_code == 200, made.text
    try:
        row = next(r for r in made.json()["rows"] if r["name"].startswith("Zed Alias One"))
        assert row["name"] == 'Zed Alias One, "Zed, With Comma", ZAO'
        listed = next(a for a in client.get("/api/artists").json() if a["name"] == "Zed Alias One")     # the feed shows the first name
        assert listed["aliases"] == '"Zed, With Comma", ZAO'
        assert "zed alias one" in lookup.artist_parts("ZAO") and "zao" in lookup.artist_parts("Zed Alias One & Somebody")
        assert lookup.artist_parts("Unrelated Person") == ["unrelated person"]
        assert lookup.tracked_page("soundcloud", lookup.artist_parts("zed with comma"))[0] == "https://soundcloud.com/zed-alias-one"
        assert lookup.score("ZAO", "Some Tune", {"name": "Some Tune", "artists": ["Zed Alias One"]})[1] == 1.0
        j = client.post("/api/lookup?fresh=true", content="Artist\tTitle\nZAO\tUnfindable Qqq Www".encode()).json()
        for _ in range(200):
            time.sleep(0.05)
            now = client.get(f"/api/lookup/{j['id']}").json()
            if now["finished"]:
                break
        found = now["rows"][0]
        assert (found["url"], found["note"]) == ("https://soundcloud.com/zed-alias-one", "A page you track"), found
        # taking a name away again stops it matching
        again = client.put("/api/table", json={"rows": keep + [{"id": row["id"], "name": "Zed Alias One", "cells": row["cells"]}]})
        assert again.status_code == 200 and lookup.artist_parts("ZAO") == ["zao"]
    finally:
        client.put("/api/table", json={"rows": keep})
        client.delete("/api/saved", params={"key": "zao|unfindable qqq www"})


def test_refresh_button_reports_and_queues(client):
    import asyncio
    from app import refresh
    assert client.post("/api/refresh").json()["state"] == "started"
    for _ in range(100):
        time.sleep(0.05)
        s = client.get("/api/status").json()
        if not s["refreshing"]:
            break
    result = s["last_result"]
    assert result and result["pages"] >= 1 and result["checked"] + result["errors"] + result["held"] == result["pages"], s
    refresh.state["refreshing"] += 1                    # as if a few new pages were still being read
    try:
        assert client.post("/api/refresh").json()["state"] == "queued"
        assert client.post("/api/refresh").json()["state"] == "queued" and client.get("/api/status").json()["queued"]
    finally:
        refresh.state["refreshing"] -= 1
    for _ in range(100):
        time.sleep(0.05)
        if not client.get("/api/status").json()["queued"]:
            break
    assert not client.get("/api/status").json()["queued"]


def test_skipping_a_row_while_it_is_searched_or_before(client, monkeypatch):
    import asyncio
    from app import lookup
    started = []

    async def slow_find(net, lib, artist, title, state):
        started.append(title)
        await asyncio.sleep(60)
        return {"status": "none"}, True
    monkeypatch.setattr(lookup, "find", slow_find)
    j = client.post("/api/lookup?fresh=true", content="Artist\tTitle\nSlow One\tStuck Tune Aaa\nSlow Two\tNext Tune Bbb\nSlow Three\tLast Tune Ccc".encode()).json()
    for _ in range(100):
        time.sleep(0.05)
        now = client.get(f"/api/lookup/{j['id']}").json()
        if now.get("current") == 1:
            break
    assert client.post(f"/api/lookup/{j['id']}/skip", json={"n": 3}).status_code == 200       # before its turn
    assert client.post(f"/api/lookup/{j['id']}/skip", json={"n": 1}).json()["skipping"] == [1, 3]
    for _ in range(100):
        time.sleep(0.05)
        now = client.get(f"/api/lookup/{j['id']}").json()
        if now.get("current") == 2:
            break
    assert now["rows"][0]["status"] == "skipped" and now["rows"][2]["status"] == "skipped"
    # knowing the link answers the row being searched
    client.put("/api/lookup/link", json={"artist": "Slow Two", "title": "Next Tune Bbb", "url": "https://slowtwo.bandcamp.com/track/next"})
    for _ in range(100):
        time.sleep(0.05)
        now = client.get(f"/api/lookup/{j['id']}").json()
        if now["finished"]:
            break
    assert now["finished"] and started == ["Stuck Tune Aaa", "Next Tune Bbb"]
    assert [r["status"] for r in now["rows"]] == ["skipped", "manual", "skipped"]
    assert lookup._saved_for("Slow One", "Stuck Tune Aaa") is None                                  # nothing saved for a skip
    assert client.post(f"/api/lookup/{j['id']}/skip", json={"n": 1}).status_code == 409
    client.delete("/api/saved", params={"key": "slow two|next tune bbb"})


def test_saved_results_closest_list_entry_and_editing_a_spelling(client):
    from app import db, lookup
    put = lambda key, **r: db.execute("INSERT OR REPLACE INTO lookup_cache(key, result, at) VALUES(?,?,?)", (key, json.dumps(r), db.now()))
    client.post("/api/known", content=("Artist,Track,Link\nNaz,You Owe Me (Nitemare & Onee 140 Bootleg),https://soundcloud.com/no\n"
                                       "Shaun Pal,Warmth (Rivver Dub Edit),https://soundcloud.com/rivver\n").encode())
    put("manual|naz|you owe me nitemare onee 140 bootleg", status="manual", url="https://soundcloud.com/no")
    put("manual|shaun pal|warmth", status="manual", url="https://soundcloud.com/rivver")
    put("manual|nobody|unlisted", status="manual", url="https://soundcloud.com/x")
    rows = {r["key"]: r for q in ("naz", "shaun", "nobody") for r in client.get(f"/api/saved?limit=1000&q={q}").json()["rows"]}
    assert rows["naz|you owe me nitemare onee 140 bootleg"]["list_title"] == "You Owe Me (Nitemare & Onee 140 Bootleg)"
    assert rows["shaun pal|warmth"]["list_title"] == "Warmth (Rivver Dub Edit)" and rows["shaun pal|warmth"]["list_guess"]
    assert rows["nobody|unlisted"]["list_title"] == ""
    guess = rows["shaun pal|warmth"]
    client.put("/api/known/entry", json={"key": guess["key"], "artist": "Shaun Pal", "title": "Warmth (Rivver Dub Edit)",
                                         "like_key": guess["list_key"], "like_variant": guess["list_variant"]})
    mine = next(r for r in client.get("/api/saved?limit=1000&q=shaun").json()["rows"] if r["key"] == "shaun pal|warmth")
    assert not mine["list_guess"] and mine["list_link"] == "https://soundcloud.com/rivver"
    # typing a spelling for a track that has none
    client.put("/api/known/entry", json={"key": "nobody|unlisted", "artist": "NoBody", "title": "UnListed"})
    row = next(r for r in client.get("/api/saved?limit=1000&q=nobody").json()["rows"] if r["key"] == "nobody|unlisted")
    assert (row["list_artist"], row["list_title"], row["list_guess"]) == ("NoBody", "UnListed", False)
    # correcting it keeps one entry; removing it takes it out
    client.put("/api/known/entry", json={"key": "nobody|unlisted", "artist": "NoBody", "title": "Unlisted!", "variant": row["list_variant"]})
    assert [r["title"] for r in db.query("SELECT title FROM known_tracks WHERE key='nobody|unlisted'")] == ["Unlisted!"]
    client.put("/api/known/entry", json={"key": "nobody|unlisted", "variant": "unlisted"})
    assert db.query("SELECT * FROM known_tracks WHERE key='nobody|unlisted'") == []
    for k in ("naz|you owe me nitemare onee 140 bootleg", "shaun pal|warmth", "nobody|unlisted"):
        client.delete("/api/saved", params={"key": k})
    client.delete("/api/known")


def test_forgetting_a_track_from_your_list(client):
    from app import db
    client.post("/api/known", content=b"Artist,Track,Link\nListOnly,Never Searched,https://soundcloud.com/lo\nListOnly,Other Tune,\n")
    row = next(r for r in client.get("/api/saved?limit=1000&q=listonly").json()["rows"] if r["list_title"] == "Never Searched")
    assert not row["searched"] and row["list_key"]
    client.delete("/api/known/entry", params={"key": row["list_key"], "variant": row["list_variant"]})
    titles = [r["list_title"] for r in client.get("/api/saved?limit=1000&q=listonly").json()["rows"]]
    assert titles == ["Other Tune"]
    assert len(db.query("SELECT * FROM known_tracks WHERE artist='ListOnly'")) == 1
    client.delete("/api/known")


def test_youtube_channel_addresses():
    from app.sources.youtube import YouTube, parse_feed
    yt = YouTube()
    assert yt.normalize("@TubeBand") == ("@tubeband", "https://www.youtube.com/@tubeband")
    assert yt.normalize("https://www.youtube.com/@TubeBand/videos")[0] == "@tubeband"
    assert yt.normalize("youtube.com/channel/UC1234567890123456789012") == (
        "UC1234567890123456789012", "https://www.youtube.com/channel/UC1234567890123456789012")
    assert yt.normalize("https://youtube.com/c/OldName")[0] == "c/OldName"
    assert yt.normalize("https://www.youtube.com/user/older")[0] == "user/older"
    with pytest.raises(Exception):
        yt.normalize("https://www.youtube.com/watch?v=abc")
    name, posts = parse_feed(YT_FEED)
    assert name == "Tube Band" and [p.external_id for p in posts] == ["vid00000001", "vid00000002"]
    assert posts[0].embed == "vid00000001" and posts[0].published_at == "2026-10-02T12:00:00Z"
    assert posts[1].image == "https://i.ytimg.com/vi/vid00000002/hqdefault.jpg"


def test_youtube_in_the_feed_and_switching_a_site_off(client):
    from app import db
    r = client.post("/api/artists", json={"name": "Tube Band", "accounts": [{"source": "youtube", "handle": "@TubeBand"}]})
    assert r.status_code == 201, r.text
    wait(client)
    tube = next(a for a in client.get("/api/artists").json() if a["name"] == "Tube Band")
    rows = client.get(f"/api/feed?artist_id={tube['id']}").json()
    assert [p["title"] for p in rows] == ["Tube Band - Glass River", "Studio Diary"] and rows[0]["source"] == "youtube"
    assert "youtube" in [s["key"] for s in client.get("/api/sources").json()]
    # switched off: gone from the sources, the table and the feed; its pages are kept through a table save
    s = client.put("/api/settings", json={"sites": {"youtube": False}}).json()
    assert not next(x for x in s["sites"] if x["key"] == "youtube")["on"]
    assert "youtube" not in [x["key"] for x in client.get("/api/sources").json()]
    assert client.get(f"/api/feed?artist_id={tube['id']}").json() == []
    table = client.get("/api/table").json()
    assert all("youtube" not in r["cells"] for r in table)
    assert client.put("/api/table", json={"rows": table}).status_code == 200
    assert db.query("SELECT COUNT(*) n FROM accounts WHERE source='youtube'")[0]["n"] == 1
    youtube_calls.clear()
    client.post("/api/refresh"); wait(client)
    assert youtube_calls == []                                   # not checked while off
    assert client.put("/api/settings", json={"sites": {"bandcamp": False, "soundcloud": False}}).status_code == 400
    client.put("/api/settings", json={"sites": {"youtube": True, "bandcamp": True, "soundcloud": True}})
    assert len(client.get(f"/api/feed?artist_id={tube['id']}").json()) == 2


def test_link_finder_youtube_is_off_until_ticked_and_only_for_ticked_artists(client):
    from app import lookup, sites

    def run(text, query="?fresh=true"):
        j = client.post("/api/lookup" + query, content=("Artist\tTitle\n" + text).encode()).json()
        for _ in range(300):
            time.sleep(0.05)
            got = client.get(f"/api/lookup/{j['id']}").json()
            if got["finished"]:
                return got["rows"]
        raise AssertionError("lookup never finished")

    assert client.get("/api/settings").json()["lookup_sites"] == ["bandcamp", "soundcloud"]   # YouTube off by default
    youtube_calls.clear()
    assert run("Tube Band\tHidden Lake")[0]["status"] != "youtube" and not any("/results" in u for u in youtube_calls)
    client.put("/api/settings", json={"lookup_sites": ["bandcamp", "soundcloud", "youtube"]})
    # ticked in Tracked Artists: a video on its channel is found with no search, others by YouTube search
    table = client.get("/api/table").json()
    for row in table:
        row["youtube_lookup"] = row["name"] == "Tube Band"
    assert client.put("/api/table", json={"rows": table}).status_code == 200
    assert sites.youtube_names() == {"tube band"}
    assert next(r for r in client.get("/api/table").json() if r["name"] == "Tube Band")["youtube_lookup"]
    youtube_calls.clear()
    on_channel = run("Tube Band\tGlass River")[0]
    assert on_channel["status"] == "youtube" and on_channel["url"] == "https://www.youtube.com/watch?v=vid00000001", on_channel
    assert not any("/results" in u for u in youtube_calls)
    searched = run("Tube Band\tHidden Lake")[0]
    assert searched["status"] == "youtube" and searched["url"].endswith("vid00000010"), searched
    # an artist not ticked is never searched on YouTube
    youtube_calls.clear()
    other = run("Unticked Artist\tLost Song")[0]
    assert other["status"] != "youtube" and not any("/results" in u for u in youtube_calls)
    # SoundCloud's own find comes before YouTube
    assert run("Sean Paul\tTemperature (River Dub Edit)")[0]["status"] == "soundcloud_track"
    # Saved results: the tracks searched in the latest lookup, until the next one
    run("Tube Band\tHidden Lake\nTube Band\tGlass River")
    got = client.get("/api/saved?filter=lastrun&limit=1000").json()
    assert {r["key"] for r in got["rows"]} == {"tube band|hidden lake", "tube band|glass river"}, got["rows"]
    run("Tube Band\tGlass River", query="")                      # answered from what is saved: nothing searched
    assert client.get("/api/saved?filter=lastrun").json()["rows"] == []
    client.put("/api/settings", json={"lookup_sites": ["bandcamp", "soundcloud"]})
    # switched off for Link Finder: a saved YouTube answer gives way to the other links
    again = run("Tube Band\tHidden Lake", query="")[0]
    assert again["status"] != "youtube"


def test_a_saved_miss_only_gets_youtube_added_when_youtube_is_switched_on(client):
    from app import lookup
    table = client.get("/api/table").json()
    for row in table:
        row["youtube_lookup"] = row["name"] == "Tube Band"
    client.put("/api/table", json={"rows": table})
    miss = {"status": "none", "note": "", "steps": ["searched Bandcamp"], "options": {}, "sites": ["bandcamp", "soundcloud"], "engine": lookup.ENGINE}
    lookup._remember(lookup._cache_key("Tube Band", "Hidden Lake"), miss, "Tube Band", "Hidden Lake")
    client.put("/api/settings", json={"lookup_sites": ["bandcamp", "soundcloud", "youtube"]})
    before = len(searched)
    j = client.post("/api/lookup", content="Artist\tTitle\nTube Band\tHidden Lake".encode()).json()
    for _ in range(200):
        time.sleep(0.05)
        got = client.get(f"/api/lookup/{j['id']}").json()
        if got["finished"]:
            break
    row = got["rows"][0]
    assert row["status"] == "youtube" and len(searched) == before, row     # Bandcamp wasn't searched again
    assert "youtube" in lookup._saved_for("Tube Band", "Hidden Lake")["sites"]
    client.put("/api/settings", json={"lookup_sites": ["bandcamp", "soundcloud"]})


def test_hiding_one_track_from_the_list_leaves_the_rest_of_the_release(client):
    from app import db
    rows = client.get("/api/tracks?limit=1000").json()
    multi = next(r for r in rows if sum(1 for x in rows if x["post_id"] == r["post_id"]) > 1)
    pid, key = multi["post_id"], multi["track_key"]
    siblings = [r["track_key"] for r in rows if r["post_id"] == pid]
    before = client.get("/api/facets").json()["hidden"]
    r = client.post(f"/api/posts/{pid}/hidden", json={"hidden": True, "track": key})
    assert r.status_code == 200 and r.json()["hidden"] == before + 1
    left = [x["track_key"] for x in client.get("/api/tracks?limit=1000").json() if x["post_id"] == pid]
    assert key not in left and set(left) == set(siblings) - {key}
    assert any(p["id"] == pid for p in client.get("/api/feed?limit=200").json())        # the release stays in the grid
    shown = [x for x in client.get("/api/tracks?limit=1000&hidden=1").json() if x["post_id"] == pid and x["track_key"] == key]
    assert shown and shown[0]["hidden"] and shown[0]["track_hidden"] and not shown[0]["release_hidden"]
    client.post(f"/api/posts/{pid}/hidden", json={"hidden": False, "track": key})
    assert len([x for x in client.get("/api/tracks?limit=1000").json() if x["post_id"] == pid]) == len(siblings)
    assert db.query("SELECT COUNT(*) n FROM hidden_tracks")[0]["n"] == 0


def test_two_rows_for_one_artist_are_refused_until_merged(client):
    table = client.get("/api/table").json()
    first = next(r for r in table if r["name"] == "Tube Band")
    dup = {"id": None, "name": "tube band", "cells": {"soundcloud": "tubebandsc"}}
    r = client.put("/api/table", json={"rows": table + [dup]})
    assert r.status_code == 400
    errors = r.json()["errors"]
    new_at, old_at = len(table), table.index(first)
    assert any(e["row"] == new_at and f"row {old_at + 1}" in e["message"] for e in errors)
    assert any(e["row"] == old_at and f"row {new_at + 1}" in e["message"] for e in errors)
    # another name in the Artist cell counts too
    alias = {"id": None, "name": "Someone New, Tube Band", "cells": {"soundcloud": "someonenew"}}
    assert client.put("/api/table", json={"rows": table + [alias]}).status_code == 400
    assert client.get("/api/table").json() == table                     # nothing was saved


def test_rows_sharing_a_name_can_be_kept_as_different_artists(client):
    table = client.get("/api/table").json()
    dup = {"id": None, "name": "Tube Band", "cells": {"soundcloud": "othertubeband"}}
    r = client.put("/api/table", json={"rows": table + [dup]})
    assert r.status_code == 400 and all(e.get("kind") == "same_name" for e in r.json()["errors"])
    r = client.put("/api/table", json={"rows": table + [dict(dup, keep_separate=True)]})
    assert r.status_code == 200, r.text
    rows = [x for x in r.json()["rows"] if x["name"] == "Tube Band"]
    assert len(rows) == 2 and all(x["same_name"] for x in rows)
    # remembered: saving again needs no override; once the clash is gone, so is the note
    assert client.put("/api/table", json={"rows": r.json()["rows"]}).status_code == 200
    left = [x for x in r.json()["rows"] if not (x["name"] == "Tube Band" and x["cells"].get("soundcloud") == "othertubeband")]
    after = client.put("/api/table", json={"rows": left}).json()["rows"]
    assert not next(x for x in after if x["name"] == "Tube Band")["same_name"]


def test_favicon(client):
    r = client.get("/favicon")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert '<link rel="icon" href="/favicon">' in client.get("/").text


def test_followed_soundcloud_profiles_are_preferred(client):
    ask = lambda name: client.post("/api/table/suggest", json={"name": name, "cells": {}}).json()
    assert ask("River")["cells"]["soundcloud"] == "river"                  # nothing set: the most followed
    assert client.post("/api/soundcloud/follows", json={"user": "not a name!"}).status_code == 400
    st = client.post("/api/soundcloud/follows", json={"user": "https://soundcloud.com/MeFan/likes"}).json()
    assert (st["user"], st["count"], st["error"]) == ("mefan", 3, "")     # both pages read
    assert client.get("/api/settings").json()["soundcloud"]["count"] == 3
    assert ask("River")["cells"]["soundcloud"] == "riverirvine"            # the one you follow
    assert ask("Hidden Act")["cells"]["soundcloud"] == "hidden-act-music"  # followed, though search doesn't list it
    got = client.post("/api/table/suggest", json={"name": "River", "cells": {}, "avoid": {"soundcloud": ["riverirvine"]}}).json()
    assert got["cells"]["soundcloud"] == "river"                           # turned down: the next best
    # Link Finder: a track not found falls back to the followed profile, though search doesn't list it
    job = client.post("/api/lookup?fresh=true", content="Artist\tTitle\nHidden Act\tNothing Like This".encode()).json()
    for _ in range(200):
        time.sleep(0.02)
        got = client.get(f"/api/lookup/{job['id']}").json()
        if got["finished"]:
            break
    r = got["rows"][0]
    assert (r["status"], r["url"], r["note"]) == ("soundcloud", "https://soundcloud.com/hidden-act-music", "A profile you follow")
    st = client.post("/api/soundcloud/follows", json={"user": "nobody-here"}).json()
    assert st["count"] == 0 and "nobody-here" in st["error"]
    assert ask("River")["cells"]["soundcloud"] == "river"
    client.post("/api/soundcloud/follows", json={"user": ""})


def test_new_purchases_to_download(client):
    def read_purchases():
        client.post("/api/collection", json={"fan": "divided_eye"})
        for _ in range(300):
            time.sleep(0.02)
            if not client.get("/api/collection").json()["syncing"]:
                return
        raise AssertionError("still reading")
    calls.pop("collection_fail", None)
    assert client.get("/api/settings").json()["downloads"]["on"] is False      # off until switched on
    client.put("/api/settings", json={"downloads": True})
    read_purchases()
    if not client.get("/api/collection").json()["complete"]:
        read_purchases()
    client.post("/api/downloads/clear")
    assert client.get("/api/status").json()["downloads"] == {"on": True,
        "fan": "divided_eye", "url": "https://bandcamp.com/divided_eye", "count": 0, "items": []}
    blob = json.loads(json.dumps(FAN_BLOB))
    blob["collection_data"]["sequence"] = ["a5", "a6", "a1", "a2"]
    blob["item_cache"]["collection"]["a5"] = {"tralbum_type": "a", "tralbum_id": 5, "item_title": "Fresh One", "band_name": "Masot",
                                              "item_url": "https://masot.bandcamp.com/album/fresh-one"}
    blob["item_cache"]["collection"]["a6"] = {"tralbum_type": "a", "tralbum_id": 6, "item_title": "Coming Soon", "band_name": "Masot",
                                              "item_url": "https://masot.bandcamp.com/album/coming-soon", "is_preorder": True}
    old = PAGES[("bandcamp.com", "/divided_eye")]
    PAGES[("bandcamp.com", "/divided_eye")] = '<div id="pagedata" data-blob="' + html_lib.escape(json.dumps(blob)) + '"></div>'
    PAGES[("masot.bandcamp.com", "/album/coming-soon")] = release("Coming Soon", "01 Jan 2099 00:00:00 GMT")
    try:
        d = client.post("/api/downloads/check").json()
        # the new purchase is ready to download; the pre-order isn't out yet
        assert [(x["title"], x["reason"]) for x in d["items"]] == [("Fresh One", "new")]
        assert client.get("/api/status").json()["downloads"]["items"] == ["Masot — Fresh One"]
        assert client.post("/api/downloads/clear").json()["count"] == 0
        assert client.post("/api/downloads/check").json()["count"] == 0      # shown once only
        # its release date comes: ready to download
        db.execute("UPDATE collection_items SET release_date='2020-01-01' WHERE title='Coming Soon'")
        d = client.post("/api/downloads/check").json()
        assert [(x["title"], x["reason"]) for x in d["items"]] == [("Coming Soon", "released")]
        client.post("/api/downloads/clear")
        # a pre-order Bandcamp itself shows as out is ready too
        blob["item_cache"]["collection"]["a6"]["is_preorder"] = False
        db.execute("UPDATE collection_items SET preorder=1 WHERE title='Coming Soon'")
        PAGES[("bandcamp.com", "/divided_eye")] = '<div id="pagedata" data-blob="' + html_lib.escape(json.dumps(blob)) + '"></div>'
        assert [x["title"] for x in client.post("/api/downloads/check").json()["items"]] == ["Coming Soon"]
        # switched off in Settings: the list is dropped, nothing is watched, and the button goes
        st = client.put("/api/settings", json={"downloads": False}).json()["downloads"]
        assert st == {"on": False, "fan": "divided_eye"}
        assert client.get("/api/status").json()["downloads"]["on"] is False
        assert client.get("/api/downloads").json()["count"] == 0
        assert client.post("/api/downloads/check").status_code == 409
        db.execute("UPDATE collection_items SET preorder=1, release_date='2020-01-01' WHERE title='Coming Soon'")
        import asyncio
        from app import lookup
        asyncio.run(lookup.check_purchases())                    # the background check does nothing
        assert db.query("SELECT preorder FROM collection_items WHERE title='Coming Soon'")[0]["preorder"] == 1
        client.put("/api/settings", json={"downloads": True})
        assert client.get("/api/settings").json()["downloads"]["on"] is True
        client.post("/api/downloads/clear")
    finally:
        PAGES[("bandcamp.com", "/divided_eye")] = old


def test_refresh_progress_estimate(client):
    assert refresh.progress() is None                                   # nothing running
    db.kv_set("refresh_pace", json.dumps({"bandcamp": 3}))
    run = {"pages": 120, "done": 22, "started": time.monotonic() - 40,
           "by": {"soundcloud": {"pages": 100, "done": 20}, "bandcamp": {"pages": 20, "done": 2}}}
    refresh._runs.append(run)
    try:
        # SoundCloud: 20 pages in 40 s, 80 left = 160 s; Bandcamp: last refresh's 3 s a page, 18 left = 54 s
        assert refresh.progress() == {"pages": 120, "done": 22, "seconds_left": 160, "new": 0,
                                      "by": {"soundcloud": {"pages": 100, "done": 20}, "bandcamp": {"pages": 20, "done": 2}}}
        # the quick pages are done: what's left is the slow site, at its own pace (10 pages in 40 s)
        run["by"] = {"soundcloud": {"pages": 100, "done": 100}, "bandcamp": {"pages": 20, "done": 10}}
        run["done"] = 110
        assert refresh.progress()["seconds_left"] == 40
        assert client.get("/api/status").json()["progress"]["done"] == 110
    finally:
        refresh._runs.remove(run)
        db.kv_set("refresh_pace", "")


def test_focus_feed_and_marked_rows(client):
    client.put("/api/table", json={"rows": []})
    client.put("/api/table", json={"rows": [
        {"name": "Night Ferry", "cells": {"bandcamp": "nightferry"}, "focus": True},     # the older form still works
        {"name": "Solo", "cells": {"bandcamp": "solo"}}]})
    wait(client)
    rows = {r["name"]: r for r in client.get("/api/table").json()}
    assert rows["Night Ferry"]["groups"] == ["focus"] and rows["Solo"]["groups"] == []
    assert [a["name"] for a in client.get("/api/artists").json() if a["focus"]] == ["Night Ferry"]
    everyone = client.get("/api/feed").json()
    focused = client.get("/api/feed?focus=1").json()
    assert focused and len(focused) < len(everyone) and {p["artist_id"] for p in focused} == {rows["Night Ferry"]["id"]}
    assert client.get("/api/feed?workspace=focus").json() == focused
    # nobody ticked: the Focus Feed is empty, not everyone
    client.put("/api/table", json={"rows": [dict(r, groups=[]) for r in client.get("/api/table").json()]})
    assert client.get("/api/feed?focus=1").json() == []
    # a row marked for removal (with errors elsewhere still pointing at the right row numbers)
    rows = client.get("/api/table").json()
    bad = client.put("/api/table", json={"rows": [dict(rows[0], remove=True), {"name": "X", "cells": {"soundcloud": "not a profile!"}}]})
    assert [e["row"] for e in bad.json()["errors"]] == [1]
    ok = client.put("/api/table", json={"rows": [dict(rows[0], remove=True), rows[1]]})
    assert [r["name"] for r in ok.json()["rows"]] == [rows[1]["name"]]


def test_soundcloud_profile_with_an_empty_tracks_list(client):
    import asyncio
    track = {"kind": "track", "id": 5001, "title": "Meus Olhos", "permalink_url": "https://soundcloud.com/oxo/meus-olhos",
             "created_at": "2022-05-01T10:00:00Z", "user": {"username": "Oxo"}}
    db.kv_set("soundcloud_client_id", "c" * 32)

    def site(request):
        path, cursor = request.url.path, request.url.params.get("cursor")
        if path == "/users/1/tracks":                       # empty page that points to another, then a page with tracks
            return httpx.Response(200, json={"collection": [track] if cursor else [],
                                             "next_href": None if cursor else "https://api-v2.soundcloud.com/users/1/tracks?cursor=2"})
        if path in ("/users/2/tracks", "/users/3/tracks"):
            return httpx.Response(200, json={"collection": []})
        if path == "/stream/users/2":                       # the All tab: a repost and its own track
            return httpx.Response(200, json={"collection": [{"type": "track-repost", "track": dict(track, id=1)},
                                                            {"type": "track", "track": track}]})
        if path == "/users/7/toptracks":                    # only the Popular tracks tab lists them
            return httpx.Response(200, json={"collection": [dict(track, id=7001)]})
        if path.endswith("/toptracks") or path == "/stream/users/7":
            return httpx.Response(200, json={"collection": []})
        if path == "/users/7/tracks":
            return httpx.Response(200, json={"collection": []})
        if path == "/users/6/tracks":                       # six empty pages, then the tracks
            n = int(cursor or 0)
            return httpx.Response(200, json={"collection": [dict(track, id=6100)] if n == 6 else [],
                                             "next_href": None if n == 6 else f"https://api-v2.soundcloud.com/users/6/tracks?cursor={n + 1}&offset=2025-01-0{n + 1}"})
        if path == "/stream/users/6":                       # a repost of its own track counts too
            return httpx.Response(200, json={"collection": [{"type": "track-repost", "track": dict(track, id=6200, user={"id": 6})},
                                                            {"type": "track-repost", "track": dict(track, id=6300, user={"id": 99})}]})
        if path == "/users/5/tracks":                       # one full track, two given only as numbers
            return httpx.Response(200, json={"collection": [track, {"id": 6001, "kind": "track", "policy": "ALLOW"},
                                                            {"id": 6002, "kind": "track"}]})
        if path == "/tracks":
            ids = request.url.params["ids"].split(",")
            return httpx.Response(200, json=[dict(track, id=int(i), title=f"Old {i}",
                                                  permalink_url=f"https://soundcloud.com/oxo/old-{i}") for i in ids])
        if path in ("/stream/users/1", "/stream/users/3"):
            return httpx.Response(200, json={"collection": []})
        if path == "/users/4/tracks":                       # a short page (others left out), then nothing
            return httpx.Response(200, json={"collection": [] if cursor else [track],
                                             "next_href": None if cursor else "https://api-v2.soundcloud.com/users/4/tracks?cursor=2"})
        if path in ("/users/1", "/users/6"):
            return httpx.Response(200, json={"track_count": 1 if path.endswith("1") else 2})
        if path == "/users/4":
            return httpx.Response(200, json={"permalink": "oxo4", "track_count": 3})
        if path == "/stream/users/4":                       # the All tab: the same track and two more, over two pages
            if cursor:
                return httpx.Response(200, json={"collection": [{"type": "track", "track": dict(track, id=5003)}]})
            return httpx.Response(200, json={"collection": [{"type": "track", "track": track},
                                                            {"type": "track", "track": dict(track, id=5002)}],
                                             "next_href": "https://api-v2.soundcloud.com/stream/users/4?cursor=2"})
        if path == "/users/3":
            return httpx.Response(200, json={"permalink": "oxo3", "track_count": 37})
        return httpx.Response(404)

    async def read(uid, known=frozenset()):
        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as c:
            return await SOURCES["soundcloud"].fetch(c, {"id": 0, "url": "x", "meta": {"user_id": uid}}, set(known))
    again = asyncio.run(read(6, {"6100"}))                 # already saved: no looking further
    assert [p.external_id for p in again.posts] == ["6100"] and any("already saved" in t for t in again.trace)
    first, second, third, fourth, fifth, sixth, seventh = (asyncio.run(read(u)) for u in (1, 2, 3, 4, 5, 6, 7))
    assert [p.external_id for p in sixth.posts] == ["6100", "6200"]
    assert any("next from" in t and "offset=2025-01-01" in t for t in sixth.trace)
    assert [p.external_id for p in seventh.posts] == ["7001"]
    assert [p.title for p in fifth.posts] == ["Meus Olhos", "Old 6001", "Old 6002"]
    assert any("2 listed without details" in t for t in fifth.trace)
    assert [p.external_id for p in fourth.posts] == ["5001", "5002", "5003"]
    assert [p.title for p in first.posts] == ["Meus Olhos"] and not first.warning
    assert [p.external_id for p in second.posts] == ["5001"]
    assert third.posts == [] and "37 tracks" in third.warning and any("37" in t for t in third.trace)


def test_check_now_reads_one_artist(client):
    nf = client.post("/api/artists", json={"name": "Night Ferry", "accounts": [
        {"source": "soundcloud", "handle": "nightferry"}]}).json()["id"]
    from app import db as d
    d.execute("DELETE FROM posts")
    d.execute("UPDATE accounts SET last_checked=NULL")
    row = client.post(f"/api/artists/{nf}/check").json()
    assert row["id"] == nf and row["post_count"] == 2 and row["problems"] == []
    assert client.post("/api/artists/99999/check").status_code == 404


def test_related_tracks_on_request(client):
    first = client.get("/api/related/900").json()
    assert first["total"] == 10 and [t["track"] for t in first["tracks"]] == ["Rel 0", "Rel 1", "Rel 2"]
    assert first["tracks"][0]["artist"] == "Pub Artist" and first["tracks"][1]["artist"] == "Uploader 1"
    assert first["tracks"][0]["duration"] == 181 and first["tracks"][0]["posted"].startswith("2025-01-01")
    more = client.get("/api/related/900?offset=3&limit=5").json()
    assert [t["track"] for t in more["tracks"]] == ["Rel 3", "Rel 4", "Rel 5", "Rel 6", "Rel 7"]
    assert len(related_calls) == 1                         # read once, then handed out from the saved copy
    assert client.get("/api/related/abc").status_code == 400


def test_beatport_and_discogs_in_link_finder(client):
    from app import lookup, sites
    lookup.BEATPORT_GAP = lookup.DISCOGS_GAP = 0
    st = client.get("/api/settings").json()
    assert st["lookup_order"] == ["bandcamp", "beatport", "soundcloud", "youtube", "discogs"]
    assert "beatport" not in st["lookup_sites"] and "discogs" not in st["lookup_sites"]          # off until ticked
    assert st["lookup_info"]["discogs"] == {"label": "Discogs", "color": "#c9b98f", "on": False, "lookup_only": True}
    extra = {x["key"]: x for x in st["sites"] if x["lookup_only"]}
    assert set(extra) == {"beatport", "discogs"} and not any(x["on"] for x in extra.values())   # off by default
    assert "beatport" not in [x["key"] for x in client.get("/api/sources").json()]                # no feed, no chip
    client.put("/api/settings", json={"lookup_sites": ["bandcamp", "soundcloud", "beatport"]})
    assert "beatport" not in sites.lookup_sites()                   # ticked, but the site is switched off
    st = client.put("/api/settings", json={"sites": {"beatport": True, "discogs": True}}).json()
    assert st["lookup_info"]["beatport"]["on"] and st["lookup_info"]["discogs"]["on"]
    assert "beatport" in sites.lookup_sites()
    from app import db
    db.execute("DELETE FROM kv WHERE key = 'extra_sites_on'")
    client.put("/api/settings", json={"lookup_sites": ["bandcamp", "soundcloud", "discogs"]})
    sites.upgrade()                                                 # from v76: ticked means switched on
    assert sites.is_on("discogs") and not sites.is_on("beatport")
    client.put("/api/settings", json={"sites": {"beatport": True}})

    def run(text):
        j = client.post("/api/lookup?fresh=true", content=("Artist\tTitle\n" + text).encode()).json()
        for _ in range(300):
            time.sleep(0.02)
            got = client.get(f"/api/lookup/{j['id']}").json()
            if got["finished"]:
                return {r["title"]: r for r in got["rows"]}
        raise AssertionError("lookup never finished")
    try:
        client.put("/api/settings", json={"lookup_sites": ["bandcamp", "soundcloud", "beatport", "discogs"]})
        rows = run("Beat Artist\tDeep Groove\nWax Only\tPressed Tune")
        bp = rows["Deep Groove"]
        assert (bp["status"], bp["site"], bp["url"], bp["release"], bp["year"]) == (
            "beatport", "Beatport", "https://www.beatport.com/track/deep-groove/501", "Deep Groove EP", "2024")
        assert rows["Pressed Tune"]["status"] == "soundcloud_search"          # just a SoundCloud search link
        assert any("add a Discogs access token" in x for x in rows["Pressed Tune"]["steps"])
        client.put("/api/settings", json={"discogs_token": "dg-secret"})
        assert client.get("/api/settings").json()["discogs"]["token"] is True
        dg = run("Wax Only\tPressed Tune")["Pressed Tune"]
        assert (dg["status"], dg["url"], dg["release"], dg["year"]) == (
            "discogs", "https://www.discogs.com/master/700", "Pressed EP", "2019")
        assert "Vinyl" in dg["note"]
        # Discogs unticked: the saved answer falls back to the SoundCloud link without searching again
        client.put("/api/settings", json={"lookup_sites": ["bandcamp", "soundcloud"]})
        before = calls["requests"]
        j = client.post("/api/lookup", content=b"Artist\tTitle\nWax Only\tPressed Tune").json()
        for _ in range(100):
            time.sleep(0.02)
            got = client.get(f"/api/lookup/{j['id']}").json()
            if got["finished"]:
                break
        assert got["rows"][0]["status"] == "soundcloud_search" and calls["requests"] == before
        # ticked later: a saved not-found answer is looked up on Beatport alone
        def plain_run(text):
            j = client.post("/api/lookup", content=("Artist\tTitle\n" + text).encode()).json()
            for _ in range(300):
                time.sleep(0.02)
                got = client.get(f"/api/lookup/{j['id']}").json()
                if got["finished"]:
                    return got["rows"][0]
        assert plain_run("Beat Artist\tLate Tune")["status"] == "soundcloud_search"
        client.put("/api/settings", json={"lookup_sites": ["bandcamp", "soundcloud", "beatport"]})
        late = plain_run("Beat Artist\tLate Tune")
        assert (late["status"], late["url"]) == ("beatport", "https://www.beatport.com/track/late-tune/502")
    finally:
        client.put("/api/settings", json={"lookup_sites": ["bandcamp", "soundcloud"], "discogs_token": ""})


def test_a_release_sold_from_another_page_links_to_that_page(client):
    """herbalistekmusic.bandcamp.com/album/52-blue shows a release with no tracks and nothing to buy;
    its head links Observatory Records' page, which sells it."""
    import asyncio
    from app import db, lookup
    from app.sources import SOURCES
    real_url = "https://observatory-records.bandcamp.com/album/52-blue"
    shell = release("52 Blue", "20 Jan 2026 00:00:00 GMT", ld_extra={"byArtist": {"name": "Herbalistek"}},
                    head=f'<link rel="canonical" href="{real_url}">')
    tralbum = {"id": 777, "item_type": "album", "artist": "Herbalistek",
               "trackinfo": [{"title": "52 Blue", "duration": 300, "track_id": 7001},
                             {"title": "Deep Water", "duration": 280, "track_id": 7002}]}
    real = release("52 Blue", "20 Jan 2026 00:00:00 GMT", ld_extra={"byArtist": {"name": "Herbalistek"},
                                                                     "publisher": {"name": "Observatory Records"}}).replace(
        "</head>", "</head><body><script data-tralbum='" + json.dumps(tralbum) + "'></script></body>").replace(
        "<head>", '<head><meta name="bc-page-properties" content=\'{"item_type":"a","item_id":777}\'>')
    grid = ('<html><body><p id="band-name-location"><span class="title">Herbalistek</span></p><ol id="music-grid">'
            '<li><a href="/album/52-blue"><p class="title">52 Blue</p></a></li></ol></body></html>')
    plain = release("Other", "01 Jan 2026 00:00:00 GMT", head='<link rel="canonical" href="https://someone.bandcamp.com/album/other">')

    def site(request):
        host, path = request.url.host, request.url.path
        if host == "herbalistekmusic.bandcamp.com":
            return httpx.Response(200, text={"/music": grid, "/album/52-blue": shell}.get(path, ""))
        if host == "observatory-records.bandcamp.com" and path == "/album/52-blue":
            return httpx.Response(200, text=real)
        return httpx.Response(404)

    assert parse_release(shell, "https://herbalistekmusic.bandcamp.com/album/52-blue")["sold_at"] == real_url
    assert parse_release(real, real_url)["sold_at"] is None                    # has tracks: it is the real page
    assert parse_release(plain, "https://someone.bandcamp.com/album/other")["sold_at"] is None   # its own address

    account = {"id": 987655, "url": "https://herbalistekmusic.bandcamp.com", "meta": {}}

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as c:
            got = await SOURCES["bandcamp"].fetch(c, account, set())
            page = await lookup.get_release(lookup.Net(c), "https://herbalistekmusic.bandcamp.com/album/52-blue")
            return got, page
    try:
        got, page = asyncio.run(go())
    finally:
        db.execute("DELETE FROM kv WHERE key='bandcamp_unread:987655'")
        db.execute("DELETE FROM web_cache WHERE kind='release' AND key LIKE '%52-blue'")
    post, = got.posts
    assert post.external_id == "https://herbalistekmusic.bandcamp.com/album/52-blue"   # still known by where it is listed
    assert post.url == real_url and post.embed == "album=777"
    assert [t.title for t in post.tracks] == ["52 Blue", "Deep Water"] and post.label == "Observatory Records"
    # Link Finder gives the page that sells it
    assert page["url"] == real_url and [t["title"] for t in page["tracks"]] == ["52 Blue", "Deep Water"]
    assert lookup.release_result(page, "bandcamp", "", "")["url"] == real_url


def test_a_release_with_nothing_to_buy_and_no_link_is_searched_for_once_then_noted(client):
    """A page showing a release with no tracks, nothing to buy and no link to where it is sold."""
    import asyncio
    from app import db, lookup
    from app.sources import SOURCES
    from app.sources.bandcamp import NOT_FOR_SALE
    by = {"byArtist": {"name": "Herbalistek"}}
    zero = '<meta property="og:description" content="0 track album">'
    found_shell = release("52 Blue", "20 Jan 2026 00:00:00 GMT", ld_extra=by, head=zero)
    lost_shell = release("Lost One", "20 Jan 2026 00:00:00 GMT", ld_extra=by, head=zero)
    tralbum = {"id": 778, "item_type": "album", "trackinfo": [{"title": "52 Blue", "duration": 300, "track_id": 7101}]}
    real = release("52 Blue", "20 Jan 2026 00:00:00 GMT", ld_extra={**by, "publisher": {"name": "Observatory Records"}}).replace(
        "</head>", "</head><body><script data-tralbum='" + json.dumps(tralbum) + "'></script></body>")
    grid = ('<html><body><p id="band-name-location"><span class="title">Herbalistek</span></p><ol id="music-grid">'
            '<li><a href="/album/52-blue"><p class="title">52 Blue</p></a></li>'
            '<li><a href="/album/lost-one"><p class="title">Lost One</p></a></li></ol></body></html>')
    searches = []

    def site(request):
        host, path = request.url.host, request.url.path
        if host == "herbalist.bandcamp.com":
            return httpx.Response(200, text={"/music": grid, "/album/52-blue": found_shell, "/album/lost-one": lost_shell}.get(path, ""))
        if host == "bandcamp.com" and path == "/search":
            searches.append(request.url.params["q"])
            if "52 Blue" in request.url.params["q"]:
                return httpx.Response(200, text="<ul>" + result("52 Blue", "https://herbalist.bandcamp.com/album/52-blue", "by Herbalistek", kind="ALBUM")
                                      + result("52 Blue", "https://observatory.bandcamp.com/album/52-blue", "by Herbalistek", kind="ALBUM") + "</ul>")
            return httpx.Response(200, text="<ul></ul>")
        if host == "observatory.bandcamp.com" and path == "/album/52-blue":
            return httpx.Response(200, text=real)
        return httpx.Response(404)

    assert parse_release(found_shell, "https://herbalist.bandcamp.com/album/52-blue")["placeholder"]
    assert not parse_release(real, "https://observatory.bandcamp.com/album/52-blue")["placeholder"]
    assert not parse_release(release("No List", "20 Jan 2026 00:00:00 GMT"), "https://x.bandcamp.com/album/no-list")["placeholder"]   # unknown: not marked
    account = {"id": 987656, "url": "https://herbalist.bandcamp.com", "meta": {}}

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as c:
            got = await SOURCES["bandcamp"].fetch(c, account, set())
            page = await lookup.get_release(lookup.Net(c), "https://herbalist.bandcamp.com/album/lost-one")
            return got, page
    try:
        got, page = asyncio.run(go())
    finally:
        db.execute("DELETE FROM kv WHERE key='bandcamp_unread:987656'")
        db.execute("DELETE FROM web_cache WHERE key LIKE '%herbalist%'")
    blue, lost = got.posts
    assert searches == ["Herbalistek 52 Blue", "Herbalistek Lost One"]                  # once each
    assert blue.url == "https://observatory.bandcamp.com/album/52-blue" and blue.note is None
    assert [t.title for t in blue.tracks] == ["52 Blue"] and blue.label == "Observatory Records"
    assert lost.url == "https://herbalist.bandcamp.com/album/lost-one" and lost.note == NOT_FOR_SALE
    # Link Finder: such a page is only a fallback to check, and says why
    assert page["placeholder"]
    assert lookup.match_in_release("Herbalistek", "Lost One", page)[0] == "bandcamp_check"
    assert "nothing to buy" in lookup.release_result(page, "bandcamp_check", "", "Found on the artist's page")["note"]


def test_workspaces(client):
    """Named groups of artists, each its own filter; Focus is the first."""
    from app import sites
    client.put("/api/table", json={"rows": []})
    assert client.get("/api/workspaces").json() == [{"id": "focus", "name": "Focus", "count": 0}]
    spaces = [{"id": "focus", "name": "Focus"}, {"id": "w1", "name": "Deep dubstep"}, {"id": "w2", "name": "Labels"}]
    r = client.put("/api/table", json={"workspaces": spaces, "rows": [
        {"name": "Night Ferry", "cells": {"bandcamp": "nightferry"}, "groups": ["w1", "focus"]},
        {"name": "Solo", "cells": {"bandcamp": "solo"}, "groups": ["w1", "w2", "gone"]}]})
    assert r.status_code == 200 and [w["name"] for w in r.json()["workspaces"]] == ["Focus", "Deep dubstep", "Labels"]
    wait(client)
    rows = {x["name"]: x for x in client.get("/api/table").json()}
    assert rows["Night Ferry"]["groups"] == ["focus", "w1"] and rows["Solo"]["groups"] == ["w1", "w2"]   # unknown ids dropped
    assert [w["count"] for w in client.get("/api/workspaces").json()] == [1, 2, 1]
    assert sites.focus_names() == {"night ferry"}                    # kept for older versions reading this database
    ids = lambda q: {p["artist_id"] for p in client.get("/api/feed?" + q).json()}
    assert ids("workspace=w1") == {rows["Night Ferry"]["id"], rows["Solo"]["id"]}
    assert ids("workspace=w2") == {rows["Solo"]["id"]}
    assert {t["artist_id"] for t in client.get("/api/tracks?workspace=w2").json()} == {rows["Solo"]["id"]}
    assert client.get("/api/feed?workspace=nope").json() == []
    # renamed and one removed: members follow the id; leaving workspaces out keeps them as they are
    table = client.get("/api/table").json()
    client.put("/api/table", json={"workspaces": [{"id": "w2", "name": "Label pages"}, {"id": "focus", "name": "Focus"}], "rows": table})
    assert [(w["name"], w["count"]) for w in client.get("/api/workspaces").json()] == [("Label pages", 1), ("Focus", 1)]
    client.put("/api/table", json={"rows": client.get("/api/table").json()})
    assert [w["name"] for w in client.get("/api/workspaces").json()] == ["Label pages", "Focus"]
    # names must be there and differ
    bad = client.put("/api/table", json={"workspaces": [{"id": "a", "name": "X"}, {"id": "b", "name": " x "}], "rows": table})
    assert bad.status_code == 400 and "Two workspaces" in bad.json()["detail"]
    assert client.put("/api/table", json={"workspaces": [{"id": "a", "name": " "}], "rows": table}).status_code == 400
    client.put("/api/table", json={"workspaces": [{"id": "focus", "name": "Focus"}], "rows": []})


def test_reading_follows(client):
    """Who a SoundCloud account or a Bandcamp fan follows, marked when already tracked."""
    client.put("/api/table", json={"rows": [{"name": "Night Ferry", "cells": {"bandcamp": "nightferry", "soundcloud": "riverirvine"}}]})
    blob = {"fan_data": {"fan_id": 55}, "following_bands_data": {"sequence": ["1", "2"], "last_token": "t1"},
            "item_cache": {"following_bands": {
                "1": {"band_id": 1, "name": "Night Ferry", "url_hints": {"subdomain": "nightferry", "custom_domain": None}},
                "2": {"band_id": 2, "name": "Deep Label", "url_hints": {"subdomain": "deeplabel"}}}}}
    asked = []

    def site(request):
        host, path = request.url.host, request.url.path
        if host == "bandcamp.com" and path == "/fanperson":
            return httpx.Response(200, text=f"<html><div id='pagedata' data-blob='{json.dumps(blob)}'></div></html>")
        if host == "bandcamp.com" and path == "/nobody":
            return httpx.Response(404)
        if host == "bandcamp.com" and path == "/api/fancollection/1/following_bands":
            body = json.loads(request.content)
            asked.append(body["older_than_token"])
            assert body["fan_id"] == 55
            if body["older_than_token"] == "t1":
                return httpx.Response(200, json={"followeers": [
                    {"name": "Custom Domain Act", "url_hints": {"subdomain": "cda", "custom_domain": "music.cda.net"}},
                    {"name": "Deep Label", "url_hints": {"subdomain": "deeplabel"}}], "more_available": True, "last_token": "t2"})
            return httpx.Response(200, json={"followeers": [], "more_available": False, "last_token": None})
        return handler(request)

    before = refresh.make_client
    refresh.make_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(site), follow_redirects=True)
    try:
        sc = client.post("/api/follows", json={"site": "soundcloud", "user": "https://soundcloud.com/MeFan"}).json()
        bc = client.post("/api/follows", json={"site": "bandcamp", "user": "fanperson"}).json()
        missing = client.post("/api/follows", json={"site": "bandcamp", "user": "nobody"})
        bad = client.post("/api/follows", json={"site": "youtube", "user": "x"})
    finally:
        refresh.make_client = before
    assert [(f["name"], f["handle"], f["tracked"]) for f in sc["items"]] == [
        ("River", "riverirvine", True), ("Someone Else", "someone", False), ("Hidden Act", "hidden-act-music", False)]
    assert [(f["name"], f["handle"], f["tracked"]) for f in bc["items"]] == [
        ("Night Ferry", "nightferry.bandcamp.com", True), ("Deep Label", "deeplabel.bandcamp.com", False),
        ("Custom Domain Act", "music.cda.net", False)]                      # each once, read to the end
    assert asked == ["t1", "t2"]
    assert missing.status_code == 400 and "No Bandcamp fan page" in missing.json()["detail"]
    assert bad.status_code == 400
    assert set(client.get("/api/follows").json()) == {"soundcloud", "bandcamp"}
    client.put("/api/table", json={"rows": []})


def test_quick_sites_do_not_wait_behind_bandcamp(monkeypatch):
    """Each site has its own queue: SoundCloud pages are all read while Bandcamp's slow pages are
    still being read, and the new posts found so far are counted while the refresh runs."""
    import asyncio
    from app import refresh, sites
    from app.sources.base import FetchResult, Post
    finished, seen_new = [], []

    class Fake:
        def __init__(self, key, seconds):
            self.key, self.label, self.seconds = key, key, seconds

        async def fetch(self, client, account, known):
            await asyncio.sleep(self.seconds)
            finished.append(account["source"])
            if self.key == "soundcloud" and len([f for f in finished if f == "soundcloud"]) == 3:
                seen_new.append(refresh.progress()["new"])
            return FetchResult(posts=[Post(f"x{account['id']}", "t", "https://x/t", None, "track", None, None, None, None)])

    accounts = [{"id": i, "source": "bandcamp" if i < 4 else "soundcloud", "url": f"https://x/{i}"} for i in range(7)]
    monkeypatch.setattr(refresh.db, "list_accounts", lambda account_ids=None: accounts)
    monkeypatch.setattr(refresh.db, "known_ids", lambda account_id: set())
    monkeypatch.setattr(refresh.db, "save_result", lambda account, result: None)
    monkeypatch.setattr(sites, "is_on", lambda key: True)
    monkeypatch.setattr(refresh, "SOURCES", {"bandcamp": Fake("bandcamp", 0.3), "soundcloud": Fake("soundcloud", 0.01)})
    asyncio.run(refresh.refresh([a["id"] for a in accounts]))
    assert finished[:3] == ["soundcloud"] * 3, finished          # not held up by the 4 slow Bandcamp pages
    assert seen_new == [2]                                       # counted as each page finishes, while Bandcamp still runs


def test_a_new_bandcamp_page_reads_its_newest_releases_first(client, monkeypatch):
    import asyncio
    from app.sources import SOURCES, bandcamp
    monkeypatch.setattr(bandcamp, "FIRST_READ", 1)
    account = {"id": 99999, "url": "https://nightferry.bandcamp.com"}
    first = asyncio.run(_fetch_with(SOURCES["bandcamp"], account, set()))
    assert [p.external_id.rsplit("/", 1)[-1] for p in first.posts] == ["first"] and "still to load" in first.warning
    later = asyncio.run(_fetch_with(SOURCES["bandcamp"], account, {p.external_id for p in first.posts}))
    assert len(later.posts) == 2                                  # then MAX_NEW (2 in the tests) at a time


async def _fetch_with(source, account, known):
    from app import refresh
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as c:
        return await source.fetch(c, account, known)


def test_quiet_bandcamp_pages_rest_between_checks(monkeypatch):
    """A Bandcamp page with no release in QUIET_DAYS is checked at most every QUIET_HOURS; recent, new,
    still-loading and other sites' pages are always checked."""
    from datetime import datetime, timedelta, timezone
    from app import refresh
    ago = lambda **kw: (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")
    newest = {1: ago(days=400), 2: ago(days=10), 3: ago(days=400), 4: ago(days=400), 5: ago(days=400), 6: ago(days=400)}
    monkeypatch.setattr(refresh.db, "query", lambda sql, args=(): [{"account_id": k, "newest": v} for k, v in newest.items()])
    acc = lambda i, source="bandcamp", checked=ago(hours=1), error=None: {
        "id": i, "source": source, "last_checked": checked, "last_error": error}
    accounts = [acc(1),                                    # quiet, checked an hour ago: rests
                acc(2),                                    # released 10 days ago: checked
                acc(3, checked=ago(hours=7)),              # quiet, but last checked 7 hours ago: due
                acc(4, checked=None),                      # never read: checked
                acc(5, error="3 older release(s) still to load"),   # catalogue still loading: checked
                acc(6, source="soundcloud"),               # other sites are quick: always checked
                acc(7)]                                    # nothing saved yet: checked
    assert refresh._resting(accounts) == {1}
    monkeypatch.setattr(refresh, "QUIET_HOURS", 0)
    assert refresh._resting(accounts) == set()              # switched off: every page, every time



def test_check_them_now_reads_the_quiet_pages_too(monkeypatch):
    """A normal refresh leaves quiet Bandcamp pages for later and says how many; "Check them now"
    (everything) reads them as well. The summary counts the pages checked on each site."""
    import asyncio
    from app import refresh, sites
    from app.sources.base import FetchResult
    read = []

    class Fake:
        def __init__(self, key):
            self.key = self.label = key

        async def fetch(self, client, account, known):
            read.append(account["id"])
            return FetchResult()

    accounts = [{"id": 1, "source": "bandcamp"}, {"id": 2, "source": "bandcamp"}, {"id": 3, "source": "soundcloud"}]
    monkeypatch.setattr(refresh.db, "list_accounts", lambda account_ids=None: accounts)
    monkeypatch.setattr(refresh.db, "known_ids", lambda account_id: set())
    monkeypatch.setattr(refresh.db, "save_result", lambda account, result: None)
    monkeypatch.setattr(refresh.db, "kv_set", lambda k, v: None)
    monkeypatch.setattr(sites, "is_on", lambda key: True)
    monkeypatch.setattr(refresh, "SOURCES", {"bandcamp": Fake("bandcamp"), "soundcloud": Fake("soundcloud")})
    monkeypatch.setattr(refresh, "_resting", lambda accs: {1})
    saved = dict(refresh.state)
    try:
        asyncio.run(refresh.refresh())
        assert sorted(read) == [2, 3]
        assert refresh.state["last_result"]["resting"] == 1
        assert refresh.state["last_result"]["sites"] == {"bandcamp": 1, "soundcloud": 1}
        read.clear()
        asyncio.run(refresh.refresh(everything=True))
        assert sorted(read) == [1, 2, 3] and refresh.state["last_result"]["resting"] == 0
        assert refresh.state["last_result"]["sites"] == {"bandcamp": 2, "soundcloud": 1}
    finally:
        refresh.state.update(saved)



def test_find_missing_links_remembers_what_it_searched(client):
    """Each search is remembered per artist and site; the table says which sites were searched, so
    "Skip rows already searched" can leave those rows alone. Sites skipped aren't counted as searched."""
    from app import db
    keep = client.get("/api/table").json()
    try:
        made = client.put("/api/table", json={"rows": keep + [{"name": "Remembered Act", "cells": {"youtube": ""}}]})
        assert made.status_code == 200, made.text
        row = next(r for r in made.json()["rows"] if r["name"] == "Remembered Act")
        assert row["searched"] == []
        got = client.post("/api/table/suggest", json={"name": "Remembered Act", "cells": {}, "skip": ["soundcloud"]}).json()
        assert got["searched"] == ["bandcamp"]                     # SoundCloud was left out, so it isn't marked
        row = next(r for r in client.get("/api/table").json() if r["name"] == "Remembered Act")
        assert row["searched"] == ["bandcamp"]
        client.post("/api/table/suggest", json={"name": "Remembered Act", "cells": {}})
        row = next(r for r in client.get("/api/table").json() if r["name"] == "Remembered Act")
        assert row["searched"] == ["bandcamp", "soundcloud"]
        got = client.post("/api/table/suggest", json={"name": "Remembered Act", "cells": {}, "skip": ["bandcamp", "soundcloud"]}).json()
        assert got["searched"] == []                               # nothing looked for: nothing recorded
    finally:
        client.put("/api/table", json={"rows": keep})
        db.kv_set("suggest_searched", "{}")
