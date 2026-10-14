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
os.environ["DEEP_READ_EXTRA_SECONDS"] = "0"
os.environ["BANDCAMP_BACKOFF_SECONDS"] = "0"

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
PAGES = {
    ("bandcamp.com", "/divided_eye"): '<div id="pagedata" data-blob="' + html_lib.escape(json.dumps(FAN_BLOB)) + '"></div>',
    ("masot.bandcamp.com", "/album/archivos-pt-1"): album("Archivos Pt. 1", "Masot", "01", "02"),
    ("masot.bandcamp.com", "/album/especies-endemicas"): album("Especies Endémicas", "Masot", "Intro Piece", "Especies Endémicas"),
    ("whitepeach.bandcamp.com", "/album/wpr091-phoenix"): album("WPR091 - Phoenix", "Blocklab", "Phoenix", "Gargantua", date="01 Feb 2025 00:00:00 GMT"),
    ("artikalmusic.bandcamp.com", "/album/half-empty"): album("Half Empty", "contactghost", "Half Empty", "Spelling Mitsake"),
    ("siadic.bandcamp.com", "/track/air"): release("Air", "09 Sep 2024 00:00:00 GMT", "MusicRecording", {
        "byArtist": {"name": "Siadic"}, "inAlbum": {"name": "Traction EP", "@id": "https://siadic.bandcamp.com/album/traction-ep"}}),
    ("dubdenrecords.bandcamp.com", "/track/ydx-what-a-world"): release("YDX - What A World", "01 Jan 2026 00:00:00 GMT", "MusicRecording", {
        "byArtist": {"name": "Dub Den Records"}, "inAlbum": {"name": "Dubs From The Den Vol. 7", "@id": "https://dubdenrecords.bandcamp.com/album/dubs-from-the-den-vol-7"}}),
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


def handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    calls["requests"] = calls.get("requests", 0) + 1
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
        users = {"Anecho": [{"username": "ANECHO", "permalink": "anechoicchamber", "permalink_url": "https://soundcloud.com/anechoicchamber"}],
                 "Nobody Known": [{"username": "nobody known fan", "permalink_url": "https://soundcloud.com/fan", "followers_count": 9000},
                                  {"username": "Nobody Known", "permalink_url": "https://soundcloud.com/nobodyknown", "followers_count": 12}]}
        return httpx.Response(200, json={"collection": users.get(request.url.params["q"], [])})
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
    assert client.get("/api/facets").json() == {"years": ["2026", "2025", "2024"], "labels": ["Deep Label", "SC Label"]}
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
    assert lines[0] == "#,artist,title,link,site,release,year,result,found as,note,steps"
    assert lines[2].startswith("2,Masot,Especies Endémicas,https://masot.bandcamp.com/album/flora,Bandcamp,Flora,2023,Bandcamp release,Masot — Especies Endémicas,Bandcamp search,")

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
            ("Allen Mock", "Dragon Language"), ("YDX", "What A World"), ("Aaaa & Bbbb Cccc", "Deep Thing")]
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
