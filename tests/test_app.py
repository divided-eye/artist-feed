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

from fastapi.testclient import TestClient  # noqa: E402

from app import refresh  # noqa: E402
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
TRALBUM = {"id": 111, "item_type": "album", "artist": "Night Ferry", "current": {"id": 111, "type": "album"},
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


calls = {"expired_once": False}


def handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "nightferry.bandcamp.com":
        pages = {"/music": GRID,
                 "/album/first": FIRST,
                 "/track/second": SECOND,
                 "/album/third": release("Third", "15 Jan 2024 00:00:00 GMT")}
        return httpx.Response(200, text=pages[path]) if path in pages else httpx.Response(404)
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
        if path == "/resolve":
            if not request.url.params["url"].endswith("/nightferry"):
                return httpx.Response(404)
            return httpx.Response(200, json={"kind": "user", "id": 42, "username": "Night Ferry",
                                             "avatar_url": "https://i1.sndcdn.com/avatars-1-large.jpg"})
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
    return httpx.Response(404)


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
    assert [p["title"] for p in feed] == ["Demo", "Second", "No Art", "First"]
    assert feed[0]["image"].endswith("artworks-9-t500x500.jpg")
    assert feed[2]["image"].endswith("avatars-1-t500x500.jpg")

    client.post("/api/refresh")                        # second pass picks up the rest, no duplicates
    wait(client)
    feed = client.get("/api/feed").json()
    assert [p["title"] for p in feed] == ["Demo", "Second", "No Art", "First", "Third"]
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

    assert [r["track"] for r in get("?sort=duration&dir=desc")][:2] == ["Long One", "Second"]
    assert get("?sort=duration&dir=asc")[-1]["release"] == "Third"            # unknown durations last
    assert [r["track"] for r in get("?sort=track&dir=asc")] == ["Demo", "Intro", "Long One", "No Art", "Second", ""]
    assert [r["track"] for r in get("?q=guest")] == ["Long One"]
    assert [r["track"] for r in get("?q=deep+intro")] == ["Intro"]
    assert [r["track"] for r in get("?source=soundcloud&limit=1&offset=1")] == ["No Art"]
    assert get("?q=100%25") == []

    first = next(p for p in client.get("/api/feed?q=intro").json())
    assert (first["title"], first["embed"], first["track_count"], first["duration"]) == ("First", "album=111", 2, 3786.5)

    r = client.get("/api/tracks.csv?sort=duration&dir=desc")
    lines = r.content.decode("utf-8-sig").splitlines()
    assert lines[0] == "artist,label,release,track,duration,year,released,site,url"
    assert lines[1] == "Guest Person,Deep Label,First,Long One,1:02:05,2025,2025-03-07,Bandcamp,https://nightferry.bandcamp.com/track/long-one"
    assert len(lines) == 7 and "attachment" in r.headers["content-disposition"]


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
    db.execute("UPDATE posts SET detailed=0, label=NULL, embed=NULL")   # what an upgraded database looks like
    db.execute("DELETE FROM tracks")
    assert all(r["track_id"] is None for r in client.get("/api/tracks").json())
    for _ in range(2):                                 # BANDCAMP_MAX_NEW=2, three releases
        client.post("/api/refresh")
        wait(client)
    strip = lambda rows: [{k: v for k, v in r.items() if k != "track_id"} for r in rows]
    assert strip(client.get("/api/tracks").json()) == strip(before)


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
    assert rows[0]["name"] == "Night Ferry" and rows[0]["cells"] == {"bandcamp": "nightferry", "soundcloud": "nightferry"}
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
    assert rows[0]["cells"] == {"bandcamp": "nightferry", "soundcloud": ""}
    assert rows[1]["cells"] == {"bandcamp": "solo, nightferry2", "soundcloud": "nightferry"}
    assert any("not found" in p for p in rows[1]["problems"])      # nightferry2 does not exist
    assert len(client.get(f"/api/feed?artist_id={nf}&source=bandcamp").json()) == before  # untouched page keeps its posts
    assert client.get(f"/api/feed?artist_id={nf}&source=soundcloud").json() == []

    csv_text = client.get("/api/artists.csv").text.splitlines()
    assert csv_text[0] == "name,bandcamp,soundcloud" and csv_text[2] == 'Night Ferry,"solo, nightferry2",nightferry'

    # Leaving a row out removes that artist; an empty table clears the list.
    assert client.put("/api/table", json={"rows": [{"id": nf, "name": "NF", "cells": {"bandcamp": "nightferry"}}]}).status_code == 200
    assert [r["name"] for r in client.get("/api/table").json()] == ["NF"]
    assert client.put("/api/table", json={"rows": []}).json()["rows"] == []
    assert client.get("/api/feed").json() == []
