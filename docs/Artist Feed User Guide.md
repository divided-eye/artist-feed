# Artist Feed: User Guide

Oct 11, 2026

## About this guide

This guide describes Artist Feed version 39. The version number is shown in small grey text beside the title at the top of the app.

The guide is meant to be edited. Change anything here that you want described differently. When a new version of the guide is requested, the current text is read first, your edits are kept, and only the parts affected by changes to the app are updated.

To download a copy of this guide, click the guide's name at the top of the page and choose **Export**, then pick Word, PDF or Markdown. In the phone apps, Export is under Share. A download is a snapshot: later edits made here are not carried into it.

One thing applies throughout: the app was built without access to the real Bandcamp and SoundCloud sites. Its logic is tested against stand-in copies of their pages, and corrected from reports of how it behaves on the real ones. Parts that have only been tested that way are noted under Known limits.

## What the app does

Artist Feed is a small web app that runs on your own computer and does two jobs.

- **The feed.** It checks the Bandcamp and SoundCloud pages of the artists and labels you track and shows their newest releases and uploads on one page, newest first. You can browse as a grid of covers or as a sortable list of tracks, filter and search, listen without leaving the page, and export the list.
- **Link Finder.** You give it a tracklist (artist and title per row). For each track it returns a link to the Bandcamp release the track is on, with the release year. When the track is not on Bandcamp it returns the track on SoundCloud, or failing that an artist page.

What it does not do:

- Instagram is not included. The app is built so that another site can be added as one file, but none has been.
- It has no login. Anyone who can reach it on your network can use and change it.
- It does not download or store audio. Listening uses each site's own embedded player.

## Installing, running and updating

The app runs in Docker, so the only thing to install is Docker Desktop (free for personal use). On Windows, accept the default WSL 2 option during setup.

### First run

1. Start Docker Desktop and wait until it says the engine is running.
2. Unzip the app, and open the folder that directly contains `docker-compose.yml`.
3. Open a terminal in that folder and run `docker compose up -d --build`.
4. Open http://localhost:8080 in a browser.

### Updating to a new version

1. Unzip the new version to a **new** folder. Do not unzip over the old one.
2. Check that the folder you are in directly contains `docker-compose.yml`. "Extract all" on Windows often adds an extra outer folder.
3. Copy the `data` folder from the old version's folder into the new one. This carries over your artists, feed, purchases and saved lookups.
4. Run `docker compose up -d --build` in the new folder.
5. Reload the page and check the version number beside the title.

If the version number has not changed, the terminal was in a folder with old files. In the build output, the line `COPY app ./app` should not say `CACHED`.

### Stopping and starting

- `docker compose stop` stops the app. `docker compose start` starts it again.
- Quitting Docker Desktop also stops it. Opening Docker Desktop again restarts the app by itself.
- Nothing is lost while it is off. It catches up when it starts.

### After editing a file yourself

The running app uses its own copy of the files. After changing any file in the folder, run `docker compose up -d --build` again and hard-refresh the page (Ctrl+Shift+R).

## Using the feed

The feed shows everything collected from your tracked artists, newest first. It refreshes itself every hour while the app is running. **Refresh Feed** checks immediately.

### Views

- **Grid** shows one card per release or upload, with cover, artist, label, track count, length and age.
- **List** shows one row per track: artist, track, duration, year, release, posted date, release date, label and site. It opens sorted by Posted, newest first.
- The app remembers which view you last used.

### List view

- Click a column heading to sort by it. Click again to reverse.
- Drag the right edge of a heading to resize the column. Double-click the edge to reset it. **Reset column widths** resets all of them.
- The headings stay in place while the list scrolls. Drag a heading onto another to move that column. The Columns button (List view only) opens a menu to tick columns on or off and reorder them with arrows; Reset to default restores the original order, visibility and widths. The layout is remembered in this browser.
- **Export list** downloads a CSV of every row that matches the current filters, in the current sort order, not just the rows on screen.
- **Released** is the release date the artist set. **Posted** is when the item went up on the site. Posted is blank when the site does not give it.

### Filters and search

| Control | What it does |
| --- | --- |
| All / Bandcamp / SoundCloud | Show one site or both |
| All artists | Show one tracked artist |
| Search | Matches artist, label, release and track names |
| Year, Label, Type, Length | Narrow by release year, label (or no label), albums and EPs versus single tracks, and track length |
| Hide no-preview tracks | Leaves out Bandcamp tracks that cannot be played yet |
| Show hidden | Shows releases you have hidden, dimmed, so you can restore them |
| Clear filters | Resets everything above except Hide no-preview tracks |
| Page width | Standard, Wide or Full window |

Filters apply to both views and to the export. Two more controls sit in the same row. The date menu keeps the last 7, 30, 90 days or 12 months, or a range you pick with two dates; a second menu chooses whether that goes by posted date (the release date is used where a post has none) or by release date. Show limits the page to the first 25 to 500 results in the current sort order, and the export is limited the same way.

### Listening

Press play on a card or a list row. The site's own player opens in a bar at the bottom and keeps playing while you scroll, filter or switch views.

- SoundCloud tracks start on their own.
- Bandcamp's player does not allow that, so press play inside it once.
- In List view, playing a row from an album opens the album at that track.
- A hollow triangle in a dashed grey ring means a Bandcamp track with no stream yet, usually an unreleased track on a pre-order. Bandcamp's player then shows whichever track the release does have.
- An arrow instead of a play button means the release's details are not loaded yet, or it cannot be embedded. The arrow opens it on the site.

### Hiding and listened-to

- The ✕ on a card (shown when you point at it) or at the end of a list row hides that release. Hiding a row hides the whole release it belongs to.
- Hidden releases stay hidden through refreshes. Tick **Show hidden** and use ↺ to bring one back.
- Anything you press play on is dimmed afterwards. Playing a card marks the whole release. Playing a list row marks that track.
- There is no button to mark something as unheard.

## Tracked Artists

The **Tracked Artists** button opens a table with one row per artist or label and one column per site. This is the list the feed is built from. The Artist column is always wide enough to show the longest name in full, and the site columns share what is left; if the window is too narrow the table scrolls sideways. Drag the edge of the Artist, site or Status heading to set a width yourself, and double-click the edge to return it to automatic.

- A cell takes a short name (`artistname`) or a full page address. Leave it empty if the artist is not on that site.
- For several pages on one site, separate them with commas in the same cell.
- Leave the artist name empty and it is filled in from the page.
- Copy rows from Excel or Google Sheets and paste them into any cell. They fill the table from that cell onward, in the order name, Bandcamp, SoundCloud.
- Nothing changes until you press **Save changes**. If any row has a problem, nothing is saved and that row shows the reason.
- Deleting a row and saving stops tracking that artist and removes their posts. It asks you to confirm.
- **Export CSV** downloads the saved list. **Import CSV** adds a file's rows to the table for you to review and save.
- After a save, rows are sorted by artist name.

The Status column shows how many posts each artist has, and any problem reading one of their pages.

Labels work the same way as artists: add the label's Bandcamp page as a row. A release that reaches the feed through both an artist's page and a label's page is shown once.

### Adding many at once

The box under the table takes a pasted list, one artist or label per line. A line can be just a name, just a Bandcamp or SoundCloud link, or name, Bandcamp, SoundCloud separated by tabs or commas. **Add to table** appends the lines as new rows; nothing is saved until you press Save changes.

### Find missing links

**Find missing links** goes through every row that has an empty cell and looks for what is missing: the Bandcamp page, the SoundCloud profile, or the name when only a link was given. It works one row at a time at Bandcamp's pace, and the button reads Stop while it runs.

- Filled cells are highlighted in light blue italics. They are guesses matched on the name, so check them, especially for common names.
- When a row already has one site's link, a page with the same address on the other site is preferred.
- Editing a highlighted cell removes its highlight. Save changes saves whatever is in the table at that moment and clears all highlights.
- A cell is left empty when nothing fitting is found. A cell you clear is remembered and shows "left empty". Later runs leave it alone, and this is saved with the table.

### Rescan

Each row has a **Rescan** checkbox. Tick it and press Find missing links to search that row's empty cells again, including ones you cleared. A guess you removed is not offered again for that cell, so a rescan returns the next best match or nothing. The box unticks itself after the run. Typing a link into a cleared cell also ends its "left empty" state.

### Clear All

**Clear All** empties the table after asking you to confirm. Nothing is removed from the feed until you press Save changes, which asks again; Discard brings the rows back.

## Link Finder

The **Link Finder** button opens a panel that takes a tracklist and returns one link per track.

### Giving it a list

- Upload an Excel (.xlsx) or CSV file, or paste rows copied from a spreadsheet.
- Each row needs two things: the artist and the track title. That is all a search uses. No header row is needed; artist then title, separated by a tab, is enough.
- With a header row, the Artist column and the first Title column are used. Other columns are ignored.
- One "Artist - Title" per line also works. Up to 500 tracks at a time.

### What each result means

| Result | Meaning |
| --- | --- |
| Bandcamp release | Title and artist both matched. The link is the release the track is on. |
| Bandcamp · check match | A partial match. Worth a click before you trust it. |
| SoundCloud track | Not on Bandcamp, but found on SoundCloud under a profile whose name fits. The link is the track itself. |
| SoundCloud artist page | The track was not found. This is the artist's (or remixer's) profile. |
| Bandcamp artist page | The track was not found. This is the artist's (or remixer's) Bandcamp page. |
| SoundCloud search | No profile matched. The link opens a SoundCloud search. |
| Link you set | You entered this link yourself. |
| Error | The search could not finish. Run the list again to retry. |

The note under each result says where it came from, for example "In your purchases" or "Matched by name only". **What was tried** lists every step and what it found. While a lookup runs, rows not yet answered have a soft blue tint, and the row being searched pulses and reads "Searching now…". A row whose link is a guess (a SoundCloud profile picked by name alone, or a partial Bandcamp match) is marked Needs review and tinted red. It stays that way after the lookup finishes, until you press Looks right, which keeps the link as one you set, or correct it with Edit.

### Options in the panel

- **Your Bandcamp username.** Lets the app read your public collection page. Your purchases are checked before anything is searched.
- **If a track isn't found, link to the artist's…** Chooses between SoundCloud profile first or Bandcamp page first. Changing it re-points results on screen and saved results without searching again.
- **Search again for saved results.** Normally a track looked up before is answered from the saved result. Tick this to run every step again, for example after a track has been released or bought. Search again for not-found rows only keeps the saved answers for tracks that were found and searches again just for the rows saved as not found. Each row of a finished lookup also has its own Search again button.

### Correcting a result

**Edit** under any link lets you paste the right link and, optionally, the year. Your link is saved for that artist and title and is used from then on, even with "Search again" ticked. **Remove my link** goes back to searching.

### Edits, remixes and bootlegs

A bracketed tag ending in remix, edit, bootleg, flip, refix, rework and similar names whoever made that version. For "Temperature (River Dub Edit)" by Sean Paul, the app searches as River, looks on River's pages, and falls back to River's profile, not Sean Paul's. The original artist is used only when nothing is found for the remixer.

- Words between the name and the kind of version are set aside: "River Dub Edit" is tried as River, then River Dub.
- A request that names a remixer never returns the plain original.
- Tags that only describe a version, such as "(Original Mix)", are ignored. A VIP is the exception: it is treated as a different recording, so a VIP request never returns the original, and it falls back to the artist's own page.

### Exporting

**Export results** downloads the list in its original order with year, link, a "needs review" column, site, release, result, and the steps tried.

## How it works

Neither site offers an official way to do what the app does, so it reads their public pages the way a browser would.

### Reading Bandcamp

Bandcamp has no public API or artist feed. For each tracked page the app reads the `/music` page, which lists the releases, then opens each release it has not read before to get the release date, posting date, label, track list, durations and the id its player needs. At most 6 new releases are opened per artist per refresh, so a long back catalogue fills in over several visits. Between the hourly refreshes, pages that still have older releases to load are visited again every 10 minutes (CATCH_UP_MINUTES in docker-compose.yml), 6 releases at a time. A release page that cannot be read is retried after the unread ones, so it cannot hold up the rest.

### Reading SoundCloud

SoundCloud's RSS feeds are empty for most musicians, so the app uses the same data endpoints the soundcloud.com website calls. They need a public key that is embedded in the site's own code. The app finds that key itself and replaces it when it expires. Only an artist's own uploads are read, not reposts.

### Pacing

Every request to Bandcamp goes through one queue, two seconds apart, whether it comes from the feed, the purchases reader or the Link Finder.

- If Bandcamp answers "slow down" (error 429), everything waits as long as it asks, or 30 seconds doubling each time, and then carries on with a wider gap. The status line shows the pause.
- While a lookup or a purchases read is running, the feed's own Bandcamp checks stand aside and resume on the next refresh.

### Background reading

After your purchases are read, the app keeps working in the background, one page every few seconds, pausing whenever a lookup is running.

1. **Full track lists.** Bandcamp's collection page names only one track per purchase, so each purchased release is opened once and its whole track list saved.
2. **Label pages.** The release list of every Bandcamp page you track or have bought from is saved, and refreshed every two weeks.

The Link Finder panel shows progress as "full track lists: N of M releases" and "label pages: N of M". Lookups improve as these fill in.

### How a lookup runs

A lookup works in three passes so that known answers appear at once.

1. **Saved data, no requests.** Links you set, earlier results, your feed, your purchases and every track list already saved.
2. **Release years** for purchase matches whose page has not been read yet. One request each.
3. **Searching** for whatever is left. This is the slow part.

The search pass tries these in order and stops at the first confident match:

1. Your purchases, by opening releases you own by that artist.
2. Bandcamp search: artist and title as a track, each artist of a pair separately, the title alone, and as a release. A result with the right title but an unrecognised artist is opened to check.
3. Bandcamp search for the artist's name alone, opening the releases that name them.
4. Label pages you know: releases there that name the artist are opened. If none carries the track, the address the track would have on those pages is tried.
5. The artist's own Bandcamp page (the remixer's first, for an edit): every release is opened and its track list read, up to 40 per lookup.
6. SoundCloud: the track itself, accepted when the uploader's name fits the artist or remixer.
7. Fallback: an artist profile or page, chosen by the preference in the panel.

### How a match is judged

- Titles and names are compared without accents, capitals, punctuation, "feat." credits or version tags.
- A match needs both the title and the artist. Short or common titles such as "Air" or "02" never match on title alone.
- The artist counts as matching when named in the track's credit, an "Artist - Title" track name, the release title, or the page address. Labels often credit a release to themselves and name the artist only there.
- Track names written as a numbered list in a release's description count as tracks of that release.
- When the same track is on a release you own and on another, the one you own is returned. Numbers in a title must agree exactly, so "Hybrid Kit 6" does not match "Hybrid Kit 2". A WIP marker ("Title WIP", "Title (WIP)") is a status, not part of the title, and is ignored. So is a bracket that only says how a track is sold or presented, such as "(Digital Download)", "(Free DL)" or "(Out Now)". A track page is swapped for the album it belongs to.

## What to personalise

Most personal settings are made in the app itself. A few live in `docker-compose.yml`, and wording and colours live in one page file.

### Set these first

| Setting | Where | Why |
| --- | --- | --- |
| Your tracked artists and labels | Tracked Artists table | Nothing appears in the feed until at least one is saved |
| Your Bandcamp username | Link Finder panel, or `BANDCAMP_FAN` in `docker-compose.yml` | Lets Link Finder check your purchases first. Your collection must be public. Enter your own username in the Link Finder panel, or set it in the file. |
| Who can reach the app | `ports` line in `docker-compose.yml` | `"8080:8000"` lets any device on your network open it. `"127.0.0.1:8080:8000"` limits it to this computer. |

### Preferences in the app

These are remembered without editing any file.

| Preference | Where | Saved in |
| --- | --- | --- |
| Grid or List view | Title bar | This browser |
| Column widths | Drag in List view | This browser |
| Page width | Filter row | This browser |
| Hide no-preview tracks | Filter row | This browser |
| Artist link when a track isn't found | Link Finder panel | The app's data |
| Hidden releases and listened-to marks | Feed | The app's data |
| Links you set by hand | Link Finder results | The app's data |

Things saved in the browser do not follow you to another browser or device. Things saved in the app's data do.

### Settings in docker-compose.yml

Add or change lines under `environment:`, then run `docker compose up -d --build`. Only the first two are set in the file by default; the third is there as a commented-out line.

| Setting | Default | Meaning |
| --- | --- | --- |
| `REFRESH_MINUTES` | 60 | How often the feed checks every artist (minimum 5) |
| `BANDCAMP_FAN` | empty | Bandcamp account whose purchases Link Finder reads |
| `LOOKUP_FALLBACK` | soundcloud | Artist link when a track isn't found: `soundcloud` or `bandcamp`. The menu in the panel overrides it. |
| `BANDCAMP_GAP_SECONDS` | 2 | Gap between any two requests to Bandcamp. Raise it if you see many pauses. |
| `BANDCAMP_BACKOFF_SECONDS` | 30 | First pause after Bandcamp says "slow down"; doubles each time |
| `BANDCAMP_MAX_NEW` | 6 | New Bandcamp releases opened per artist per feed refresh |
| `SOUNDCLOUD_LIMIT` | 30 | Most recent SoundCloud uploads read per artist |
| `LOOKUP_SCAN_RELEASES` | 40 | Releases opened on an artist's page per lookup row |
| `LOOKUP_OWNED_RELEASES` | 15 | Purchases by an artist opened per lookup row |
| `LOOKUP_MAX_ROWS` | 500 | Largest tracklist accepted |
| `DEEP_READ_EXTRA_SECONDS` | 1 | Extra gap between background page reads |
| `SOUNDCLOUD_CLIENT_ID` | unset | Supply your own SoundCloud key instead of the automatic one |

### Wording and colours

These are in `app/static/index.html`. Search the file for the text shown.

| To change | Search for |
| --- | --- |
| Button names | `Refresh Feed`, `Link Finder`, `Tracked Artists` |
| The hint text in the Link Finder panel | `Headers not needed` |
| No-preview play button colour | `rowplay.nostream` (currently `#6d6d6d`, hover triangle `#474747`) |
| The yellow accent colour used across the app | `--accent: #d9f25c` |
| The Link Finder panel's blue glow | `lookup-glow` (colour `125, 200, 255`, speed `5s`) |
| Background and text colours | The `--bg`, `--panel`, `--text` and `--muted` values near the top |

Change only the text between `>` and `<`, or the colour values. Leave `id="..."` parts alone or buttons stop working.

A new version of the app replaces this file. Edits made here are lost unless they are also reported so they can be built in, which is how the current button names, hint text and grey no-preview colour got there.

## What is saved

Everything the app keeps is in one file, `data/feed.db`, inside the app's folder. Copy the `data` folder to move or back up everything.

| What | Kept for |
| --- | --- |
| Tracked artists, the feed, hidden releases, listened-to marks | Until you remove them |
| Your purchases | Permanently; new purchases are added once a day |
| Links you set by hand | Permanently; they outrank any search |
| The result for each artist and title looked up | Permanently, with one exception below |
| Every release page opened, with its full track list and year | 6 months |
| Which Bandcamp page belongs to an artist | 30 days |
| The release list of an artist's or label's page | 3 days for lookups; label pages you know are refreshed every 2 weeks |

How saved results behave:

- A saved result is reused instantly unless **Search again for saved results** is ticked.
- Results that ended in an error or a rate limit are not saved, so they are retried next time.
- When a new version improves the search, results that were **not** a Bandcamp release are searched again once. Found releases and links you set are kept. Bandcamp's search answers are themselves kept for 7 days (LOOKUP_SEARCH_DAYS in docker-compose.yml), so a repeat search like this asks Bandcamp almost nothing. Pressing Search again, or ticking either search-again box, asks Bandcamp anew.
- When a track isn't found, both the artist's Bandcamp page and SoundCloud profile are saved, so changing the preference needs no new search.

View, column widths, page width and Hide no-preview tracks are stored in the browser, not in `data`.

## Known limits

- **Neither site is an official integration.** If Bandcamp or SoundCloud changes its pages, that part stops updating until the app is fixed. Everything already collected stays.
- **Built without access to the real sites.** The app's logic is tested on stand-in pages. How it behaves on the real sites is known only from real use. Reading purchases, the feed, the players and most lookups have been seen working; SoundCloud track search and the address guess for unlisted tracks have not been confirmed.
- **Bandcamp limits how fast it can be read.** The first read of a large purchase history takes hours, and a lookup of a track that is nowhere can take two to three minutes.
- **Bandcamp's player cannot be started or controlled by the app.** One extra click is needed, and there is no shared volume control.
- **Pre-orders.** Only the released tracks of a pre-order can be played. Bandcamp's player shows one of those whichever track you pick.
- **Common artist names.** When a track is not on SoundCloud, a profile is picked by name alone and marked "matched by name only". For a name like River this can be the wrong person. The app learns the right profile for a name when it finds one of their tracks on SoundCloud, or when you set a SoundCloud link with Edit, and uses it from then on, including for answers already saved.
- **Subscriber-only and unlisted tracks** are found only if the label's page is one you track or have bought from and it carries the artist. One known miss: Hannibal Rex, Manyothers on Dungeon Beats. This was left as is by choice.
- **Labels that never name the artist.** A label release whose title, address and track list do not name the artist is not matched, because accepting title-only matches would give wrong links for common titles.
- **Label is often blank in the feed.** On Bandcamp it is filled in when a release sits on a label's page. On SoundCloud it appears only when the uploader entered it.
- **Excel may store a title such as "02" as the number 2.** Format the cell as text if a numeric title is not matching.
- **No Instagram, no login, no "mark as unheard".**

## FAQ

These are the questions that came up while the app was being built.

### Setup and updates

**I haven't used Docker before. What do I need to install?** Only Docker Desktop, which is free for personal use. It includes everything the commands need. On Windows, accept the default WSL 2 option.

**What happens if I don't run it all the time?** Nothing is lost in normal use. The app only checks while it is running, but each time it starts it does a full check. Bandcamp pages list all of an artist's releases, so nothing is missed. SoundCloud reads each artist's 30 most recent uploads, so you would only miss something if an artist posted more than 30 tracks while it was off.

**I installed a new version but the app looks the same. Why?** Docker built from a folder that still had the old files. Unzip to a new folder, make sure you are in the folder that directly contains `docker-compose.yml`, copy `data` across, and run `docker compose up -d --build`. In the output, `COPY app ./app` should not say `CACHED`.

**Where does it show the version?** In small grey text directly after the "artist/feed" title. The address http://localhost:8080/api/status shows it too.

**Can I change button names or colours by editing a file instead of getting a new version?** Yes. They are in `app/static/index.html`; see What to personalise. Rebuild afterwards, and report the change so the next version keeps it.

### The feed

**Why is an item showing an arrow instead of a play button?** The app has no player details for that release yet. Either its details have not been loaded (a few refreshes fix that), or the page could not be read. The arrow opens it on the site.

**Why was I seeing the same release twice?** It reached the feed through two pages you track, usually an artist's page and a label's. Since version 7 a release is shown once.

**Why are some durations blank?** Those are tracks Bandcamp is not streaming yet, usually the unreleased tracks of a pre-order. They get the dashed grey play button and can be hidden with Hide no-preview tracks.

**Why does Bandcamp's player play the first track when I pick a different one?** On a pre-order only the released tracks can be played, and Bandcamp's player falls back to one of those. The app does ask for the track you picked.

**Can there be one volume slider for both sites, or can each site's own slider be shown?** Not with the sites' own players. Neither embedded player shows a volume control, and a page cannot add one inside them. SoundCloud's player accepts volume commands, Bandcamp's accepts none. One slider for both would mean the app playing Bandcamp audio itself, which is unofficial and was not built. On Windows, the volume mixer can lower the browser on its own.

**What is the difference between Released and Posted?** Released is the date the artist gave the release. Posted is when it went up on the site. Posted is blank when the site does not say.

**Can I change the colour of the no-preview icons?** Yes. Search `app/static/index.html` for `rowplay.nostream` and change the colour values. They are currently `#6d6d6d`.

**The Status column keeps saying "N older release(s) still to load". Is it stuck?** Usually not. A page's back catalogue loads 6 releases per visit, only while the app is running, and visits are skipped while a lookup or purchases read is running or Bandcamp has asked for a pause. The number should fall each time you look. Up to version 34 it could stall: release pages that failed to load were retried first on every visit and could use up all 6 places. Since version 35 those are retried last and reported separately as "could not be read", and backlog pages are visited every 10 minutes.

### Link Finder

**What does "Search again for saved results" do?** Unticked, any track looked up before gets its saved answer at once. Ticked, saved answers are ignored and every step runs again, still reusing saved pages. Use it when a track saved as not found has since been released or bought.

**Does the app keep what it learns, or scan from scratch each time?** It keeps it. Purchases, every release page it opens, artist pages and final results are all saved. See What is saved.

**Does it save every track on a release it opens, or only the one it was looking for?** Every track. Later lookups check all saved track lists before searching anything.

**Can I correct a result and have the right link remembered?** Yes. Use Edit under the link. Your link is used for that artist and title from then on.

**Why does a lookup take so long, or seem stuck?** Bandcamp limits how fast it can be read, so requests go two seconds apart and pause when Bandcamp asks. A track that is not in your purchases can need several searches and dozens of page reads. The status line names the track being searched and shows any pause. Tracks already known are filled in first.

**What does "Bandcamp answered 429" mean?** Bandcamp is saying the app asked too fast. The app waits and carries on by itself. A purchases read that is interrupted continues from where it stopped.

**Why did it take hours after reading my purchases?** Bandcamp's collection page names only one track per purchase. The app opens each purchased release once to save its whole track list. That happens once; afterwards only new purchases are read.

**Why wasn't a track found when I own the release?** The usual cause was a label release credited to the label, with the artist named only in the title or address, such as "DNO021 - Kercha - Open The Door LP" by DNO Records. Since version 17 the artist appearing there counts as a match. Buying the vinyl was not the cause.

**Why weren't tracks found that are listed on the release page?** Some releases have one playable item, such as a preview mix, and name the real tracks only as text in the description. Since version 18 a numbered list in the description is read as track names.

**Can SoundCloud profile links be preferred over Bandcamp artist pages?** Yes, and since version 21 that is the default. The menu in the Link Finder panel switches the order.

**For a remix or bootleg, whose page does it link to?** The remixer's. The original artist is used only when nothing is found for the remixer.

**Why did it return the wrong SoundCloud profile?** Profiles were being picked by name, and many share a name. Since version 22 the app looks for the track on SoundCloud and uses whoever uploaded it. A profile picked by name alone is labelled "matched by name only".

**The track is on the artist's SoundCloud. Why did I get the profile?** That was the behaviour up to version 22. Since version 23 the link goes straight to the track.

**Why wasn't a subscriber-only track found?** Those are not in Bandcamp's search or public lists. The app can find one only when the label's page is one you know and carries the artist. Otherwise set the link by hand with Edit.

## Version log

Versions 1 and 2 were delivered before the app showed a version number; the numbers are given here for reference. Versions 1 to 9 date from 9 October 2026, versions 10 to 33 from 10 October 2026, versions 34 to 39 from 11 October 2026.

| Version | What changed |
| --- | --- |
| 39 | Link Finder: uncertain links are marked Needs review with a red tint that stays until confirmed (Looks right) or corrected, and the export has a "needs review" column. Bandcamp search answers are saved for 7 days, so repeat searches after an update are nearly free. Search again button on each row, and a tick box to search again for not-found rows only. Hint text states that a row needs artist and track title. |
| 38 | Link Finder remembers which SoundCloud profile belongs to a name, from a track of theirs found there or a link you set, and uses it wherever a profile would otherwise be picked by name alone. Saved name-only answers are corrected without a new search. |
| 37 | Link Finder: rows still waiting are tinted while a lookup runs, and the row being searched pulses. Brackets that only describe format or availability, such as "(Digital Download)", are ignored when matching titles; earlier misses are searched again once. |
| 36 | Default list order swaps Release and Released: Artist, Track, Duration, Year, Release, Posted, Released, Label, Site. Link Finder export puts year before the link. |
| 35 | Tracked Artists table: resizable columns, and the Artist column always fits the full name. Back catalogues load faster: catch-up visits every 10 minutes, and unreadable release pages no longer block the rest. |
| 34 | List columns can be dragged to reorder and shown or hidden from a Columns menu; new default order Artist, Track, Duration, Year, Released, Posted, Release, Label, Site. Date filter (last N days or a date range, by posted or release date). Show-first-N limit on results and export. WIP markers ignored when matching titles. Guide included in the zip under docs. |
| 33 | Numbers in titles must agree: "Hybrid Kit 6" no longer matches "Hybrid Kit 2" or "Hybrid Kit". Saved results for titles containing a number are looked up again once. |
| 32 | Tracked Artists: cleared cells are remembered and skipped by Find missing links; removed guesses are never offered again; per-row Rescan checkbox to search a row's cleared cells again. |
| 31 | Tracked Artists: Find missing links fills empty Bandcamp, SoundCloud and name cells with highlighted guesses that clear on save. Paste-a-list box under the table. Clear All button with confirmation. |
| 30 | A VIP version is no longer matched to the original track; when the VIP itself isn't found, the artist's page is returned. Link Finder glow pulses only while a lookup is running and is steady otherwise. List view opens sorted by Posted, newest first; Grid view is ordered by posted date, falling back to release date. |
| 29 | A track bought on its own now links to the album it is on, not the single-track page; track links from earlier lookups are corrected the next time they come up. Bandcamp username in docker-compose.yml ships empty. |
| 28 | No-preview button in grey (`#6d6d6d`). "Hide no-preview tracks" filter. Refresh renamed Refresh Feed and moved left of Link Finder. Links Lookup renamed Link Finder. |
| 27 | No-preview play button made clearly different: hollow triangle in a dashed ring. |
| 26 | List headings stay in place while scrolling. Bandcamp tracks with no stream are marked, with a hint in the player bar. |
| 25 | Hide releases from the feed, with Show hidden to restore. Items you have played are dimmed. Pulsing blue glow on the lookup panel. |
| 24 | Lookup searches Bandcamp for the artist's name and opens releases naming them. Release lists of label pages you know are saved and searched. Unlisted tracks tried at their expected address. |
| 23 | A track found on SoundCloud links straight to the track, with year and a free-download note. |
| 22 | SoundCloud profile chosen by who uploaded the track, not by name alone. Bare remixer name tried first. A profile named for a whole act beats one named for a member. |
| 21 | Edits, remixes, bootlegs and flips link to the remixer. SoundCloud profile became the default fallback. A remix request never returns the plain original. |
| 20 | Buttons renamed Links Lookup and Tracked Artists, grouped on the right, no permanent yellow. |
| 19 | Setting for which artist link to prefer when a track isn't found. Both links saved so switching needs no new search. |
| 18 | Track names written as a numbered list in a release's description are read. |
| 17 | Artist named only in a release's title or address counts as a match. Title-matching search results are opened to check. Old misses retried automatically. |
| 16 | Background read of every purchased release's full track list. Track purchases link to their album. |
| 15 | Lookup runs in three passes, so saved answers appear at once. |
| 14 | Feed stands aside while a lookup or purchases read runs. Pauses shown in the status line. Gap raised to 2 seconds; feed opens 6 releases per artist per refresh instead of 12. |
| 13 | Every saved track list is checked before searching. Releases you own preferred. |
| 12 | Edit button to set and save your own link for a track. |
| 11 | One shared, self-slowing queue for all Bandcamp requests. Purchases read resumes after an interruption. |
| 10 | Lookup reworked: reads your Bandcamp purchases, searches several ways, scans the artist's page, and records the steps tried. |
| 9 | Tracklist lookup added: upload or paste a list, get Bandcamp release links with year, SoundCloud fallback. |
| 8 | Separate Released and Posted date columns. Posted no longer borrows the release date. |
| 7 | A release tracked through two pages is shown once. |
| 6 | Posted date collected and shown in List view and the export. |
| 5 | Page widens with the list columns. Page width setting. |
| 4 | Resizable list columns. Filters for year, label, type and length. |
| 3 | Grid and List views, search, per-track list with export, in-page player. Version number shown in the app. Fixed Docker project name. |
| 2 | Artist list became an editable table with paste and CSV import and export. |
| 1 | First build: Bandcamp and SoundCloud feed, artists added one at a time, hourly refresh, Docker setup. |
