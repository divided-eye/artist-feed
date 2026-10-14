# Artist Feed

One page showing the newest releases and uploads from the artists you follow on
Bandcamp and SoundCloud. You manage the artist list in the app itself.

## Run it

```
docker compose up -d --build
```

Open http://localhost:8080 (or `http://<that machine's address>:8080` from another
device). Click **Artists**, fill in the table, and press **Save changes**; the
posts appear a few seconds later. The app re-checks every hour; **Refresh**
checks immediately.

## Views, search and export

- **Grid** shows one card per release or upload. **List** shows one row per
  track: artist, label, release, track, duration, release year, release date
  and the date it was posted (uploaded) to the site. The posted date is blank
  when the site doesn't give one. Click a column
  heading to sort by it; click again to reverse.
- **Search** matches artist, label, release and track names, in both views.
- **Filters** (the row under the title bar) narrow both views by year, label,
  type (albums & EPs, or single tracks) and track length. They combine with
  the site, artist and search filters; **Clear filters** resets all of them.
- **Column widths** in List view: drag the right edge of a column heading.
  Double-click the edge to reset that column, or use **Reset column widths**.
  Widths are remembered in the browser you set them in. The page widens with
  the columns, so sideways scrolling only starts once the list is wider than
  the window.
- **Page width** (right end of the filter row): Standard, Wide or Full window,
  for both views.
- **Export list** (in List view) downloads a CSV of every row that matches the
  current filters, in the current sort order.

Label and release come from what each site publishes. On Bandcamp the label is
filled in when a release sits on a label's page rather than the artist's own;
on SoundCloud both are only present when the uploader entered them.

A release that reaches the feed through two tracked pages (an artist's page and
their label's, for example) is shown once.

Pre-orders appear with their future release date. Tracks that are not out yet
have no duration, and a pre-order with nothing streamable yet has no play
button.

## Tracklist lookup

The **Tracklist** button takes a list of tracks and returns a link for each one:
the Bandcamp release the track is on, plus its release year. When the track
isn't on Bandcamp, you get the artist's Bandcamp page if they have one, and
otherwise their SoundCloud page.

- Upload an Excel (.xlsx) or CSV file, or paste rows copied from a spreadsheet.
  With a header row, the **Artist** column and the first **Title** column are
  used; other columns (crate, series, a second "Title" for the mix, times) are
  ignored. Without a header it expects number, artist, title, or artist, title,
  or one "Artist - Title" per line.
- **Your Bandcamp username** (in the panel, or `BANDCAMP_FAN` in
  `docker-compose.yml`) lets the lookup read your public collection page. Your
  purchases are checked before anything is searched. They are read on the
  first lookup, then topped up with new purchases once a day; **Read
  purchases** re-reads everything. The collection must be public.
- Bandcamp's collection page only names one track per purchase, so after
  reading your purchases the app goes on to open each purchased release and
  save its whole track list. This runs by itself in the background, a page
  every few seconds, so a large collection takes a few hours the first time.
  It waits while a lookup is running, carries on after a restart, and from
  then on only new purchases need reading. The panel shows how far it has got
  ("full track lists: N of M releases"). Tracks on releases already read are
  answered instantly; the rest are still found the slow way until it finishes.
- A lookup works in three passes, quickest first. Everything it can answer
  from saved data (your feed, your purchases, pages read before, earlier
  results) is filled in within a second or two, with no requests to Bandcamp.
  Then it reads release years for those answers where it doesn't have them
  yet. Only then does it search Bandcamp for the tracks that are left, which
  is the slow part; the status line names the track it is on.
- Every release page the lookup opens is saved with its full track list, not
  just the track being looked for. Later lookups check those saved track lists
  before searching anything, so the more it has read, the more it answers
  instantly.
- The app paces itself: every request to Bandcamp goes through one queue, two
  seconds apart. If Bandcamp answers "slow down" (429), everything waits as
  long as it asks and then carries on more slowly; the status line says how
  long the pause is. While a lookup or a purchases read is running, the feed's
  own Bandcamp checks stand aside and resume on the next refresh. A purchases
  read that gets interrupted keeps what it has and continues from there next
  time.
- Each track is looked for, in order, in: your feed; your purchases; Bandcamp
  search (as a track, by title alone, and as a release); and the artist's own
  Bandcamp page, where every release is opened and its track list read, since
  Bandcamp's search often misses tracks. Easy tracks take a second or two;
  ones that need an artist's page scanned can take up to a minute the first
  time. Pages read once are remembered.
- The **Result** column says what each link is: "Bandcamp release" (title and
  artist both matched), "Bandcamp · check match" (partial match, worth a
  look), "Bandcamp artist page" (the artist is on Bandcamp but the track isn't
  on any release there now), "SoundCloud artist page", or "SoundCloud search"
  (no profile matched; the link opens a search).
- Labels often credit a release to themselves and name the artist only in the
  title or the page address ("DNO021 - Kercha - Open The Door LP" by DNO
  Records). The artist appearing there as a whole word counts as a match, and
  a search result with the right title but an unrecognised artist is opened
  to check before it is passed over.
- **What was tried** under each result lists every step and what it found.
  It is also in the export, in the "steps" column.
- **Edit** under any link lets you replace it with the correct one (and set
  the year). Your link is saved for that artist and title and is used from
  then on, even when you tick "Search again for saved results". **Remove my
  link** goes back to searching.
- **Export results** downloads the list with links, release, year and result.
- Looked-up tracks are remembered, so repeating a list is instant. Tick
  "Search again for saved results" to redo them. Up to 500 tracks at a time.

## Listening

Press play on a card or a list row and the site's own player opens in a bar at
the bottom of the page; it keeps playing while you scroll, filter or switch
views. SoundCloud tracks start on their own. Bandcamp's player does not allow
that, so press play inside it once. In List view, playing a row from an album
opens the album at that track.

A row or card with an arrow instead of a play button has not had its details
loaded yet, or cannot be embedded; the arrow opens it on the site.

## Updating from an earlier version

Replace the files, keep your `data` folder, and run
`docker compose up -d --build`. The version number beside the title (and at
http://localhost:8080/api/status) tells you which version is running; this one
is v17. Labels, track lists and durations for Bandcamp
releases already in your feed are filled in over the next refreshes (6 per
artist each time; press **Refresh** to speed it up). SoundCloud fills in on the
first refresh.

## The artist table

One row per artist, one column per site.

- A cell takes a name (`artistname`) or a full address. Leave it empty if the
  artist isn't on that site. For several pages on one site, separate them with
  commas.
- Leave the artist name empty and it is filled in from the page.
- Copy rows from Excel or Google Sheets and paste them into any cell; they fill
  the table from that cell onward. Column order: name, Bandcamp, SoundCloud.
- Nothing changes until you press **Save changes**. If any row has a problem,
  nothing is saved and the row is marked with the reason.
- Deleting a row and saving stops tracking that artist and removes their posts.
- **Export CSV** downloads the saved list; **Import CSV** adds the rows of a
  CSV file to the table for you to review and save. A header row
  (`name,bandcamp,soundcloud`, in any order) is optional.

Your artist list and feed history are in `./data/feed.db`. Back up that one file
to keep everything.

There is no login. Anyone who can reach port 8080 can edit the list, so keep it
on your home network, or change the port line in `docker-compose.yml` to
`127.0.0.1:8080:8000` to limit it to the machine it runs on.

## Check a page before adding it

```
docker compose run --rm artist-feed python -m app.check bandcamp artistname
docker compose run --rm artist-feed python -m app.check soundcloud username
```

This prints what the app can read from that page. It is also the fastest way to
tell whether one of the sites has changed something.

## How each site is read

**Bandcamp** has no public API or artist feed. The app reads the artist's public
`/music` page, then opens each release it has not seen before once to get the
release date. At most 6 new releases are opened per artist per refresh, so a
long back catalogue fills in over a few refreshes. Custom domains work: paste
the full address. Future-dated releases (pre-orders) show at the top as "out <date>".

**SoundCloud** is read through the same JSON endpoints the soundcloud.com site
uses, because SoundCloud's RSS feeds are empty for most musicians. Those
endpoints need a public key embedded in the site's JavaScript; the app finds it
automatically and replaces it when it expires. Only the artist's own uploads are
listed, not reposts.

Neither method is an official integration. If a site changes its pages, that
source stops updating and the reason appears in yellow beside the page in the
Artists panel; everything already collected stays in the feed.

## Settings (docker-compose.yml)

| Setting | Default | Meaning |
|---|---|---|
| `REFRESH_MINUTES` | 60 | How often to check every artist (minimum 5) |
| `BANDCAMP_MAX_NEW` | 6 | New Bandcamp releases opened per artist per refresh |
| `SOUNDCLOUD_LIMIT` | 30 | Most recent SoundCloud uploads read per artist |
| `SOUNDCLOUD_CLIENT_ID` | unset | Supply your own key instead of the automatic one |
| `BANDCAMP_FAN` | unset | Bandcamp username whose purchases the tracklist lookup checks |
| `BANDCAMP_GAP_SECONDS` | 2 | Gap between any two requests to Bandcamp, app-wide |
| `BANDCAMP_BACKOFF_SECONDS` | 30 | First pause when Bandcamp says "slow down"; doubles each time |

## Adding Instagram (or another site) later

Each site is one file in `app/sources/`. Copy `soundcloud.py`, implement
`normalize()` and `fetch()` (see `base.py`), and add it to the list in
`app/sources/__init__.py`. The input field, filter chip and badge appear in the
UI automatically.

## Without Docker

```
pip install -r requirements.txt
uvicorn app.main:app --port 8080
```

## Tests

```
pip install pytest && pytest
```

The tests run the whole app against stand-in copies of the two sites. They check
the app's logic, not whether the real sites still look the same.
