# Artist Feed: User Guide

Oct 21, 2026

## About this guide

This guide describes Artist Feed version 81. The version number is shown in small grey text beside the title at the top of the app.

Artist Feed is free to use, copy, change and share under the MIT License (the `LICENSE` file in the app's folder).

The guide is meant to be edited. Change anything here that you want described differently. When a new version of the guide is requested, the current text is read first, your edits are kept, and only the parts affected by changes to the app are updated.

To download a copy of this guide, click the guide's name at the top of the page and choose **Export**, then pick Word, PDF or Markdown. In the phone apps, Export is under Share. A download is a snapshot: later edits made here are not carried into it.

One thing applies throughout: the app was built without access to the real Bandcamp, SoundCloud and YouTube sites. Its logic is tested against stand-in copies of their pages, and corrected from reports of how it behaves on the real ones. Parts that have only been tested that way are noted under Known limits.

## What the app does

Artist Feed is a small web app that runs on your own computer and does two jobs.

- **The feed.** It checks the Bandcamp, SoundCloud and YouTube pages of the artists and labels you track and shows their newest releases and uploads on one page, newest first. You can browse as a grid of covers or as a sortable list of tracks, filter and search, listen without leaving the page, and export the list.
- **Link Finder.** You give it a tracklist (artist and title per row). For each track it returns a link to the Bandcamp release the track is on, with the release year. When the track is not on Bandcamp it returns the track on SoundCloud, or failing that an artist page.

What it does not do:

- Instagram is not included. The app is built so that another site can be added as one file, but none has been.
- It has no login. Anyone who can reach it on your network can use and change it.
- It does not download or store audio. Listening uses each site's own embedded player.

## Installing, running and updating

There are two ways to run the app. **Docker** is the recommended one: Docker Desktop is a free program that runs the app in its own sealed-off space, with everything it needs included, and starts it again whenever the computer starts. You don't need to learn Docker to use it; the few commands below are all there is. If you would rather not install Docker, see **Running without Docker** further down. The app runs on Windows and on a Mac; the steps below are written for Windows, and **On a Mac** lists what differs.

Whichever way you choose, the app runs on your own computer, and you use it in your web browser at http://localhost:8080. Other devices on your home network can open it at `http://<your computer's address>:8080`.

### Installing Docker Desktop (once)

1. Download Docker Desktop from docker.com/products/docker-desktop and run the installer. On Windows, keep the default option **Use WSL 2** ticked. If the installer says WSL needs updating, let it, or run `wsl --update` in PowerShell.
2. Restart the computer if asked, then open Docker Desktop. Accept the terms; you can skip signing in.
3. Wait until the bottom-left corner of Docker Desktop says the engine is running (a green indicator).

Docker Desktop is free for personal use. It needs a 64-bit Windows 10 or 11, or a recent Mac, and about 4 GB of memory to spare.

### First run

1. Get the app's files. Either download them from GitHub with Git (`git clone https://github.com/divided-eye/artist-feed.git`, see Keeping it in Git), or unzip the zip you were given. Put the folder somewhere permanent, such as `Documents\artist-feed`.
2. Open that folder in File Explorer and check it directly contains `docker-compose.yml`. ("Extract all" on Windows often adds an extra outer folder; if so, go one level in.)
3. Open a terminal in that folder: in File Explorer, click the address bar, type `powershell` and press Enter. On a Mac, open Terminal, type `cd` and a space, drag the folder onto the Terminal window and press Enter.
4. Make sure Docker Desktop is running, then type `docker compose up -d --build` and press Enter. The first time takes a few minutes while it downloads and builds; later starts take seconds. When it finishes, it prints that the container is started.
5. Open http://localhost:8080 in your browser. Click **Tracked Artists**, add a few artists, and press **Save changes**.

From then on the app starts by itself whenever Docker Desktop starts. To make Docker Desktop start with the computer, tick **Start Docker Desktop when you sign in** in its Settings.

If the page doesn't open: check Docker Desktop is running and the app is listed under **Containers** with a green dot. `docker compose logs --tail 50`, run in the app's folder, shows its latest messages. If another program already uses port 8080, change `8080:8000` in `docker-compose.yml` to, say, `8090:8000`, run `docker compose up -d` again and open http://localhost:8090.

### Updating to a new version

1. (Without Git; with Git, see Keeping it in Git below.) Unzip the new version to a **new** folder. Do not unzip over the old one.
2. Check that the folder you are in directly contains `docker-compose.yml`. "Extract all" on Windows often adds an extra outer folder.
3. Copy the `data` folder from the old version's folder into the new one. This carries over your artists, feed, purchases and saved lookups.
4. Run `docker compose up -d --build` in the new folder.
5. Reload the page and check the version number beside the title.

If the version number has not changed, the terminal was in a folder with old files. In the build output, the line `COPY app ./app` should not say `CACHED`.

### Keeping it in Git

The app's folder is a Git repository: every version since 3 is saved as a commit, tagged with its number (`v3` … `v58`), with that version's Version log entry as its description. Git keeps a history of the code only. The `data` folder is left out on purpose, so your artists, feed and saved lookups are never committed and never overwritten by an update.

**Setting it up (once).** Install Git for Windows from git-scm.com (the default options are fine). Stop the app, unzip `artist-feed-git.zip` to a new folder, move your `data` folder into it, and start the app from there with `docker compose up -d --build`. After that, updates no longer need a new folder each time.

**Everyday commands**, typed in PowerShell inside the app's folder:

| Command | What it does |
| --- | --- |
| `git log --oneline` | Lists every version and what changed in it. |
| `git status` | Shows the files you have edited since the last commit. |
| `git diff` | Shows exactly what you changed. |
| `git add -A` then `git commit -m "what I changed"` | Saves your own edits (a colour, some wording) as a commit. Do this before installing an update. |
| `git checkout v55` | Switches to an older version, to compare or go back. `git checkout main` returns to the newest. Rebuild with `docker compose up -d --build` after switching. |

**Installing an update.** Commit any edits of your own first. If the repository is on GitHub, run `git pull`; if the update came as a file such as `artist-feed-v59.bundle`, run `git pull artist-feed-v59.bundle main`. Then run `docker compose up -d --build` and check the version number. Git merges your edits with the update. Where both changed the same lines, it says there is a conflict and marks those lines in the file with `<<<<<<<` and `>>>>>>>`: keep the version you want, delete the markers, then `git add -A` and `git commit`.

The file `.gitattributes` keeps line endings as Linux expects them, so files edited on Windows still work inside Docker. Leave it in place.

### Stopping and starting

- `docker compose stop` stops the app. `docker compose start` starts it again.
- Quitting Docker Desktop also stops it. Opening Docker Desktop again restarts the app by itself.
- Nothing is lost while it is off. It catches up when it starts.

### After editing a file yourself

The running app uses its own copy of the files. After changing any file in the folder, run `docker compose up -d --build` again and hard-refresh the page (Ctrl+Shift+R).

### Running without Docker

The app is an ordinary Python program, so it can run without Docker. The trade-off: it only runs while its window is open, it doesn't start by itself when the computer starts, and you install Python and the app's libraries yourself.

**Installing (once), on Windows:**

1. Install Python 3.12 from python.org/downloads. On the installer's first screen, tick **Add python.exe to PATH**.
2. Open PowerShell in the app's folder (the one containing `requirements.txt`) and run these three lines one at a time:

```
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The first line makes a private Python environment for the app in a `.venv` folder, the second switches to it, and the third installs the libraries the app needs. If PowerShell refuses the second line because running scripts is disabled, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, answer Y, and try again.

On a Mac the steps differ slightly; see **On a Mac** below.

**Starting it**, each time, in PowerShell in the app's folder:

```
.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Then open http://localhost:8080. Leave the window open while you use the app; closing it, or pressing Ctrl+C in it, stops the app. Use `--host 127.0.0.1` instead of `0.0.0.0` to keep it to this computer only.

**Where things are.** Your data is saved in the `data` folder inside the app's folder, the same as with Docker, so you can switch between the two ways and keep everything. The settings in `docker-compose.yml` don't apply; set them in PowerShell before starting instead, for example `$env:REFRESH_MINUTES = "30"`.

**Updating** works as with Docker (`git pull`, or a new folder with your `data` folder copied in), then run `pip install -r requirements.txt` again in case the libraries changed, and start the app as above.

### On a Mac

Artist Feed runs on a Mac the same way it runs on Windows, on both Apple silicon (M1 and later) and Intel Macs. Everything above applies, with these differences.

**Docker Desktop.** On docker.com, pick the download that matches your Mac: **Apple silicon** or **Intel chip**. Apple menu → **About This Mac** shows which, next to Chip or Processor. Open the downloaded `.dmg`, drag Docker into Applications, open it from there and allow the permissions it asks for. There is no WSL step; that is Windows only. Docker Desktop supports the current version of macOS and the two before it.

**Getting the files.** Either download the zip from the GitHub page (**Code** → **Download ZIP**; Safari unzips it into Downloads by itself) and move the folder somewhere permanent such as `Documents/artist-feed`, or use Git: the first time you type `git` in Terminal, macOS offers to install Apple's command line tools, which include it. Then run `git clone https://github.com/divided-eye/artist-feed.git` in the folder you want it in.

**Opening a terminal in the app's folder.** Open Terminal (in Applications → Utilities, or press Cmd+Space and type Terminal). Type `cd` and a space, drag the app's folder from Finder onto the Terminal window to fill in its path, and press Enter. Every command in this guide, `docker compose …` and `git …`, is typed there exactly as written. Where the guide says PowerShell, read Terminal.

**Starting with the computer.** In Docker Desktop's Settings, under General, tick **Start Docker Desktop when you sign in to your computer**.

**Other devices on your network.** The first time, macOS may ask whether Docker may accept incoming network connections. Allow it if you want to open the app from a phone or another computer. Your Mac's address is in System Settings → Wi-Fi → **Details** beside the connected network (or System Settings → Network for a cable).

**Sleep.** The app only checks for new posts while the Mac is awake. When it wakes, the app catches up, as after any time switched off.

**Keys.** For a hard refresh, use Cmd+Shift+R in Chrome or Firefox, or Cmd+Option+R in Safari, instead of Ctrl+Shift+R. To stop the app in a Terminal window, press Control+C (Control, not Command).

**Running without Docker.** Install Python 3.12 from python.org/downloads (the macOS installer); the Python that comes with Apple's command line tools is too old to rely on. Then, in Terminal in the app's folder, run once:

```
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

and to start it each time:

```
source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Settings are set with `export` before starting, instead of `$env:`, for example `export REFRESH_MINUTES=30`.

These are the standard Docker and Python steps for macOS, and everything the app uses is available for both kinds of Mac, but the app itself hasn't been tried on a Mac yet.

## Using the feed

The feed shows everything collected from your tracked artists, newest first. It refreshes itself every hour while the app is running. **Refresh Feed** checks immediately. If a refresh is already under way (for example for pages just added), a full one follows as soon as it ends. When it finishes, the line beside the buttons says what it found: how many new posts, how many Bandcamp pages were skipped because a Link Finder lookup was running (they are checked on the next refresh), and how many pages couldn't be read. The reasons for those are in the Status column of Tracked Artists and in the app's log (docker compose logs).

### Views

- **Grid** shows one card per release or upload, with cover, artist, label, track count, length and age.
- **List** shows one row per track: artist, track, duration, year, release, posted date, release date, label and site. It opens sorted by Posted, newest first.
- The app remembers which view you last used.

### Focus Feed

The **Focus Feed** button, beside Grid | List, shows only the artists ticked in the **Focus** column of Tracked Artists: a short list of the ones you want to follow most closely, out of everyone you track. Click it again (or Clear filters) to show everyone. The other filters still apply on top of it. Tick or untick Focus in Tracked Artists, then Save changes.

### List view

- Click a column heading to sort by it. Click again to reverse.
- Drag the right edge of a heading to resize the column. Double-click the edge to reset it. **Reset column widths** resets all of them.
- The headings stay in place while the list scrolls. Drag a heading onto another to move that column. The Edit Columns button (List view only) opens a menu to tick columns on or off and reorder them with arrows; Reset to default restores the original order, visibility and widths. The layout is remembered in this browser.
- **Export Feed** (List view only, beside Refresh Feed at the start of the filter row) downloads a CSV of every row that matches the current filters, in the current sort order, not just the rows on screen.
- **Related tracks** (SoundCloud rows only): click **+** beside a SoundCloud track to open, under it, the first three tracks SoundCloud lists as related to it, with their artist, track, duration and posted date. **Show 5 more** adds five more below them each time; − closes the list. Each track plays in the player with ▶, and its name opens it on SoundCloud. They are read from SoundCloud only when you click +, never during a refresh, and kept for a day.
- **Released** is the release date the artist set. **Posted** is when the item went up on the site. Posted is blank when the site does not give it.

### Filters and search

| Control | What it does |
| --- | --- |
| All / Bandcamp / SoundCloud | Show one site or both |
| All artists | Show one tracked artist |
| Search | Matches artist, label, release and track names, a moment after you stop typing (Enter searches at once). The ✕ at its right end empties it; the filter box in Tracked Artists and the search box for saved results have one too. If the feed can't be loaded, a red message beside the status line says so. |
| Year, Label, Type, Length | Narrow by release year, label (or no label), albums and EPs versus single tracks, and track length |
| Hide no-preview tracks | Leaves out Bandcamp tracks that cannot be played yet |
| Show hidden | Shows releases you have hidden, dimmed, so you can restore them |
| Clear filters | Resets everything above except Hide no-preview tracks |
| Page width | Standard, Wide or Full window |

Filters apply to both views and to the export. The date menu sits next to the length menu. It keeps the last 7, 30, 90 days or 12 months, or a range you pick with two dates; a second menu chooses whether that goes by posted date (the release date is used where a post has none) or by release date. Show, at the left of the line below the filters (with Page width, Reset column widths and Edit Columns on the right), limits the page to the first 25 to 500 results in the current sort order, and the export is limited the same way.

### Listening

Press play on a card or a list row. The site's own player opens in a bar at the bottom and keeps playing while you scroll, filter or switch views.

- SoundCloud tracks start on their own.
- Bandcamp's player does not allow that, so press play inside it once.
- In List view, playing a row from an album opens the album at that track.
- A hollow triangle in a dashed grey ring means a Bandcamp track with no stream yet, usually an unreleased track on a pre-order. Bandcamp's player then shows whichever track the release does have.
- An arrow instead of a play button means the release's details are not loaded yet, or it cannot be embedded. The arrow opens it on the site.

### Hiding and listened-to

- The ✕ on a card (shown when you point at it) hides that whole release. The ✕ in the list's Hide column hides just that track; the rest of the release stays, in the list and as a card. A release of a single track is hidden whole either way.
- Hidden releases and tracks stay hidden through refreshes. Tick **Show hidden** and use ↺ to bring one back. In the list, ↺ on a track of a release hidden from the grid brings back the whole release.
- Anything you press play on is dimmed afterwards. Playing a card marks the whole release. Playing a list row marks that track.
- There is no button to mark something as unheard.

### Downloads

The **Downloads** button (top right, beside Tracked Artists) shows how many of your Bandcamp purchases are ready to download that you haven't been shown yet. It needs your Bandcamp username, set in the Link Finder panel, and is off until you tick **Show the Downloads button**, right beside the username box in Link Finder. While it is off the button stays in place, greyed out, and hovering over it says how to turn it on; purchases aren't watched for downloads and any waiting count is dropped, so switching it on starts fresh. Link Finder still uses your purchases either way.

- Two things are counted: a purchase made since the app first read your collection, and a pre-order whose release date has come. A pre-order is not counted when you buy it, only when it comes out.
- Point at the button to see the list of releases. Click it to open your Bandcamp collection in a new tab, where the download links are; the count goes back to zero.
- Your purchases are checked as often as the feed refreshes (every hour unless you changed it), and whenever Link Finder starts a lookup.
- The app can't download the files itself. Bandcamp's download links need you to be signed in to your account, and the app only reads your public collection page.
- Purchases you made before setting your username are not counted, and a collection set to private can't be read.

## Tracked Artists

The **Tracked Artists** button opens a table with one row per artist or label and one column per site. This is the list the feed is built from. The Artist column is always wide enough to show the longest name in full, and the site columns share what is left; if the window is too narrow the table scrolls sideways. Drag the edge of the Artist, site or Status heading to set a width yourself, and double-click the edge to return it to automatic.

- A cell takes a short name (`artistname`) or a full page address. Leave it empty if the artist is not on that site.
- For several pages on one site, separate them with commas in the same cell.
- Leave the artist name empty and it is filled in from the page. If an artist goes by other names, add them after the first, separated by commas: "Night Ferry, NF, Nightferry". The first name is the one shown in the feed. Link Finder treats all of them as the same artist, so a track listed under any of the names matches their releases and falls back to their pages. Put a name that contains a comma in double quotes. Type other names in the table itself; in the paste box, commas separate the name from the links.
- Copy rows from Excel or Google Sheets and paste them into any cell. They fill the table from that cell onward, in the order name, Bandcamp, SoundCloud.
- Nothing changes until you press **Save changes**. If any row has a problem, nothing is saved and that row shows the reason.
- The ✕ at the end of a row marks it for removal: the row turns a dark red-grey, its text is struck through, and an Undo button takes the ✕'s place. Nothing is removed until you Save, which stops tracking those artists and removes their posts after asking you to confirm. A new row that was never saved goes at once. The Focus column, left of the artist's name, picks the artists shown by the Focus Feed button.
- **Export CSV** downloads the saved list. **Import CSV** adds a file's rows to the table for you to review and save.
- After a save, rows are sorted by artist name, or by the column you sorted on.

The Status column shows how many posts each artist has, and any problem reading one of their pages. Click **Check now** beside the count to read that artist's pages straight away, without waiting for the next refresh or refreshing everyone; the count and any problems update when it finishes.

To find rows quickly, type in **Filter rows** (it matches names, pages and status messages) or pick from the menu beside it: **Needs attention** shows rows with a problem (a page that couldn't be read, or a save error), and there are also Still being checked, No posts yet, New, not saved yet, Ticked for YouTube search, Ticked for Focus, and Marked for removal. Each option shows how many rows it holds. Click a column heading to sort by it, again to reverse, and a third time to go back to the usual order; sorting by Status puts rows with problems first, then those with the fewest posts. Filtering and sorting only change what is shown: saving still saves every row, and pasting a block of cells fills the rows on screen, leaving filtered-out rows alone.

Each artist belongs on one row. Save is refused, with a message on every row involved, when two rows have the same page, or share a name (any of the names in their Artist cells). Both usually happen after **Import CSV**, which adds the file's rows as new rows rather than updating the ones you have: move the new pages into the existing row and delete the extra one. Two rows for one artist would leave Link Finder using whichever it finds first. If two rows really are different artists who share a name (in different genres, say), press **Different artists, keep both** on one of them and save. That is remembered, and both rows then say "Shares its name with another row". Link Finder still can't tell them apart by name, so give the right link with Edit when it picks the wrong one. Renaming one of them ends it.

Labels work the same way as artists: add the label's Bandcamp page as a row. A release that reaches the feed through both an artist's page and a label's page is shown once. Some artists' pages show a release that is sold from another page, usually the label's: that copy has no tracks and nothing to buy. The app links and plays the page that sells it instead, whether or not you track that page, and Link Finder gives that page too.

### Adding many at once

The box under the table takes a pasted list, one artist or label per line. A line can be just a name, just a Bandcamp or SoundCloud link, or name, Bandcamp, SoundCloud separated by tabs or commas. **Add to table** appends the lines as new rows; nothing is saved until you press Save changes.

### Find missing links

**Find missing links** goes through every row that has an empty cell and looks for what is missing: the Bandcamp page, the SoundCloud profile, or the name when only a link was given. It works one row at a time at Bandcamp's pace, and the button reads Stop while it runs. As in a Link Finder lookup, the panel glows while it works, rows still to be searched have a blue tint, and the row being searched pulses.

- Filled cells are highlighted in light blue italics. They are guesses matched on the name, so check them, especially for common names.
- When a row already has one site's link, a page with the same address on the other site is preferred.
- When your SoundCloud username is set in Settings, a profile you follow is preferred over others with the same name.
- Editing a highlighted cell removes its highlight. Save changes saves whatever is in the table at that moment and clears all highlights.
- A cell is left empty when nothing fitting is found. A cell you clear is remembered and shows "left empty". Later runs leave it alone, and this is saved with the table.

### Rescan

Each row has a **Rescan** checkbox. Tick it and press Find missing links to search that row's empty cells again, including ones you cleared. A guess you removed is not offered again for that cell, so a rescan returns the next best match or nothing. The box unticks itself after the run. Typing a link into a cleared cell also ends its "left empty" state.

### Clear All

**Clear All** empties the table after asking you to confirm. Nothing is removed from the feed until you press Save changes, which asks again; Discard brings the rows back.

## Settings

The **Settings** button (top right, beside Tracked Artists and Link Finder) opens the display choices, two lists of checkboxes and a box for your SoundCloud username. Nothing changes until you press **Save** at the top of the panel. The button lights up when there is something to save, and the words beside it say **Unsaved changes**, then **Saved** once it is done. Pressing **Close** (or Escape) with changes not yet saved asks before dropping them.

**Display.** How the app looks: **Text size** makes everything larger or smaller (90% to 150%), and **Colour scheme** picks **Dark** (the default), **Light**, **High contrast** (black and white with yellow highlights and thicker outlines) or **Match this device**, which follows the light or dark setting of the computer or phone. They take effect when you press Save. Each browser or device keeps its own choice, so a phone can be set differently from a computer, and they are not part of the saved data.

**Sites.** Switch a site off and it is gone from the app: no button for it at the top, no column in Tracked Artists, no checks for new posts, its posts left out of the feed, and Link Finder doesn't use it. This goes further than the site buttons at the top, which only narrow what the feed shows. The pages you track there are kept (saving Tracked Artists leaves them alone), so switching the site back on brings everything back. At least one of Bandcamp, SoundCloud and YouTube has to stay on. **Beatport** and **Discogs** are listed here too, marked Link Finder only and experimental: they have no feed, start switched off, and switching one on also ticks it for Link Finder (see Beatport and Discogs below).

**Link Finder searches.** Tick the sites Link Finder looks on. A track found on a site higher in the list is preferred: Bandcamp first, then Beatport, then SoundCloud, then YouTube, then Discogs. YouTube, Beatport and Discogs start unticked. Saved answers stay as they were, except that a link on a site you have unticked gives way to the next best one. When you tick a site, rows saved as not found are searched again the next time they are looked up; when the newly ticked sites are only YouTube, Beatport or Discogs, only those are looked at for them (YouTube only for the artists ticked for it).

**Your SoundCloud account.** Many SoundCloud profiles share a name. Enter your SoundCloud username and press **Save**: the profiles your account follows are read straight away and are then preferred whenever a profile has to be picked by its name, by Find missing links in Tracked Artists and by Link Finder when a track isn't on SoundCloud. A followed profile is found even when SoundCloud's search doesn't list it. The line beside the box says how many profiles were read and when. The list is read again every 12 hours, so a profile you follow later is picked up within half a day (press **Read follows now** to read it at once). Your follows must be public. Clear the box and press Save to stop using it.

### YouTube

Put a channel in the YouTube column of Tracked Artists as its @handle or its address (youtube.com/@name, /channel/UC…, or an old /c/ or /user/ address). Its newest uploads appear in the feed like any other post and play in the player at the bottom.

Link Finder only looks on YouTube for the rows ticked in the **YouTube search** column of Tracked Artists, so it doesn't search YouTube for every artist just because their other links aren't set:

- For a ticked artist, it first looks through the uploads of the YouTube channels you track for ticked rows, then runs a YouTube search for the artist and title.
- For a ticked label, it looks through the uploads of the label's channel, which needs to be in the label's YouTube cell. Labels aren't searched by name, because a tracklist doesn't say which label a track is on.
- YouTube comes after Bandcamp, Beatport and SoundCloud: it is only looked at when the track wasn't found on SoundCloud.

### Beatport and Discogs

Both are optional extra places for Link Finder to look, and both start switched off: switch them on under **Sites** in Settings, which also ticks them under **Link Finder searches** (untick them there to keep the site on but stop searching it), then press Save. Neither has a feed or a column in Tracked Artists.

**Both are experimental.** They were built without access to the real Beatport and Discogs sites and tested only against stand-in copies, so their results may be unreliable: a track that is there may not be found, a match may be the wrong track or release, and either site can stop answering if it changes how it works. Check their links before relying on them, and treat a Beatport · check match or Discogs result as a lead rather than an answer.

- **Beatport** is searched for any track that wasn't found on Bandcamp, before SoundCloud, since it is a place to buy the track. An exact match there is the answer (shown as Beatport track) and beats a partial match on Bandcamp; a partial match is kept as Beatport · check match, behind a track found on SoundCloud. The link opens the track's Beatport page.
- **Discogs** is the last resort: it is only searched when the track itself wasn't found on Bandcamp, Beatport, SoundCloud or YouTube. It can turn up releases that exist only on vinyl. Link Finder asks Discogs for releases by the artist with that track, opens the track lists of up to three of them, and links that release's main page on Discogs, the one that gathers all its pressings and versions (a release with no main page is linked directly; the note says its formats, such as Vinyl). A release found there comes before an artist page. Discogs only answers signed-in apps, so it needs a free personal access token: on discogs.com go to Settings → Developers → **Generate new token**, then paste it into the box that appears in Settings when Discogs is switched on and press Save (or set `DISCOGS_TOKEN` in `docker-compose.yml`). Remove token, beside the box, takes a saved token out.

**How long they add (estimates).** Beatport: one search per track not found on Bandcamp, at most one request every 1.5 seconds, so roughly 1.5 to 3 seconds per track, about 3 to 5 minutes for 100 tracks that aren't on Bandcamp. Discogs: one search plus up to three track lists, at most one request a second (Discogs allows 60 a minute), so roughly 2 to 5 seconds per track, and only for tracks found nowhere else. Tracks answered from your purchases, your feed or saved results cost nothing either way, searches are kept for a week and Discogs track lists for 30 days. These are estimates, not measurements.

Beatport has no public search for apps, so Link Finder reads its search page the way a browser does. If Beatport blocks that or changes the page, the step says "Beatport search unavailable" and the lookup carries on without it.

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
| YouTube video | Not found on Bandcamp or SoundCloud, but on YouTube: among the uploads of a channel you track, or by a YouTube search for an artist ticked for it. Only when YouTube is ticked for Link Finder in Settings. |
| Beatport track | Not on Bandcamp, but on Beatport with the title and artist both matching. The link is the track's Beatport page. Only when Beatport is ticked for Link Finder in Settings. |
| Beatport · check match | A partial match on Beatport. Worth a click before you trust it. |
| Discogs release | Not found on any other site, but on a release listed on Discogs (often vinyl-only). The link is the release's main page; the note gives its formats. Only when Discogs is switched on and a token is set. Experimental: may be wrong. |
| SoundCloud artist page | The track was not found. This is the artist's (or remixer's) profile. |
| Bandcamp artist page | The track was not found. This is the artist's (or remixer's) Bandcamp page. |
| SoundCloud search | No profile matched. The link opens a SoundCloud search. |
| YouTube channel | The track was not found, and the artist has neither a Bandcamp page nor a SoundCloud profile to link. This is the YouTube channel you track for them. |
| Link you set | You entered this link yourself. |
| Error | The search could not finish. Run the list again to retry. |

The note under each result says where it came from, for example "In your purchases" or "Matched by name only". **What was tried** lists every step and what it found. While a lookup runs, rows not yet answered have a soft blue tint, and the row being searched pulses and reads "Searching now…". Each of those rows has **Skip** and **Add link**. Skip stops the search for that track at once (or before it starts) and moves on; it is marked Skipped and nothing is saved for it, so it is searched for next time unless you add a link. Add link sets the link you already know, and that track is not searched. A row whose link is a guess (a SoundCloud profile picked by name alone, or a partial Bandcamp, Beatport or Discogs match) is marked Needs review and tinted red. It stays that way after the lookup finishes, until you press Looks right, which keeps the link as one you set, or correct it with Edit.

### Options in the panel

- **Your Bandcamp username.** Lets the app read your public collection page. Your purchases are checked before anything is searched. Beside it, **Show the Downloads button** switches the Downloads button (top right) on or off; it takes effect straight away and starts off.
- **If a track isn't found, link to the artist's…** Chooses between SoundCloud profile first or Bandcamp page first. Changing it re-points results on screen and saved results without searching again.
- **Search again for saved results.** Normally a track looked up before is answered from the saved result. Tick this to run every step again, for example after a track has been released or bought. Search again for not-found rows only keeps the saved answers for tracks that were found and searches again just for the rows saved as not found. Each row of a finished lookup also has its own Search again button.

### Correcting a result

**Edit** under any link lets you paste the right link and, optionally, the year. Your link is saved for that artist and title and is used from then on, even with "Search again" ticked. **Remove my link** goes back to searching.

### Edits, remixes and bootlegs

A bracketed tag ending in remix, edit, bootleg, flip, refix, rework and similar names whoever made that version. For "Temperature (River Dub Edit)" by Sean Paul, the app searches as River, looks on River's pages, and falls back to River's profile, not Sean Paul's. The original artist is used only when nothing is found for the remixer.

- Words between the name and the kind of version are set aside: "River Dub Edit" is tried as River, then River Dub.
- A request that names a remixer never returns the plain original. When a remix, edit, flip or bootleg has no link of its own, the remixer's SoundCloud profile is given, whichever artist link you prefer otherwise. A remix typed without brackets, such as "Outside The Box IOWLA Flip", is first looked for as typed; if that finds no track, it is read as "Outside The Box (IOWLA Flip)" and looked for again. Brackets are still the reliable way to write one.
- Tags that only describe a version, such as "(Original Mix)", are ignored. A VIP is the exception: it is treated as a different recording, so a VIP request never returns the original, and it falls back to the artist's own page.

### Exporting

**Export results** downloads the list in its original order with year, link, a "needs review" column, your spelling of the artist and title and the link note (from your list, when you have imported one), site, release, result, and the steps tried.

### Saved results

The **Saved results** tab in Link Finder lists every track looked up so far, newest first, with its link, year, result and the date it was saved. Search it, or filter to rows that need review, have no link to the track itself (only an artist page, a profile or a search), have the track found on Bandcamp or SoundCloud, have a link you set, are in your list, or have no spelling on file. Click a column heading to sort the whole table by it, again to reverse the order, and a third time to return to newest first. Rows with nothing in that column go last. Export downloads whatever the search and filter show, in the order shown.

The filter **Searched in the last lookup** shows only the tracks the latest lookup had to search for (not the ones answered from what was already saved), and keeps showing them after the lookup finishes, so you have one list of new links to review. It starts again each time you press a Look up button; Search again on a row adds that row to it.

Each track is listed under the tidied form of its name: lower case, no accents or punctuation (ø is read as o; letters of other alphabets, such as Cyrillic or Chinese, are kept as they are, and so is a symbol written into a word, as in "∑1", "Myca$h" or "Mor£"), and no describing tags or status notes such as "(WIP)" or "(forth. Label)". That is the form tracks are matched by. A version keeps its own entry, shown in square brackets, so "Temperature" and "Temperature (River Dub Edit)" are saved separately. What you typed is shown beneath when it differs; tracks saved before version 40 only have the tidied form. Your spelling of the artist and of the title each have their own column. A track saved under a name that doesn't match your list exactly (saved before versions had entries of their own, or typed without brackets) shows the closest entry, marked "closest in your list": the one with the same words, or the only version of that title. **Edit spelling** (or Add spelling) under the title lets you type how the artist and title should be written; it is saved to your list for that track, and Remove takes the track out of your list.

Each row has Edit, Search again and Forget, plus Looks right on rows marked for review. Forget opens a choice of what to forget: Saved answer (the track is searched for next time), List entry (takes that track out of your imported list; on a row marked "closest in your list" it is called Closest list entry), or Both. Only the choices that apply to the row are shown.

### Your list of spellings and links

**Import your list** takes a spreadsheet or CSV of how artists and titles should be written. It needs an Artist column and, for tracks, a Title column. A row with only an artist gives that artist's spelling for all their tracks. Upload the .xlsx file itself, or save as "CSV UTF-8": a plain CSV saved from Excel loses letters outside Western European alphabets, which arrive as question marks.

- Your spelling is shown beside the tidied form here, under the names in lookup results, and as its own columns in both exports. The tidied form is kept.
- The list may include links, in columns named Link, URL, Bandcamp or SoundCloud, or Buy. When the list has a header row, columns without a heading are ignored. Without a header row, links are read wherever a web address appears. Text written beside a link, such as "(limited time only)" or "(Patreon)", is kept separately as a note and shown and exported in its own column.
- A list may repeat tracks: rows for the same track are merged. The spelling used most often is kept. When rows give different links, a Bandcamp release or SoundCloud track link is preferred over a shop page, a label's front page or a profile, then the link given most often. The import reports how many tracks had either difference. Versions that tidy to the same title, such as "Eski" and "Eski \[alternate version\]", stay separate entries, each with its own spelling, link and note. A Year column is kept and used when the lookup has no year of its own. Rows whose title has no letters or digits, such as "???", are left out.
- Importing again updates the list. Remove my list deletes it; saved results and links you set are kept.

A link from your list is used only when the lookup has nothing better:

1. A link you set in the app with Edit or Looks right always comes first.
2. Then a Bandcamp release or SoundCloud track the lookup found itself. Lists go out of date, for example when a track that only had a SoundCloud page has since been released.
3. Then the link from your list. A Bandcamp release link answers the row without searching. Any other link is used only after a Bandcamp search finds nothing.
4. Last, the lookup's own fallback: an artist page or a partial match.

When your list's link is not the one shown, it still appears beneath as "your list had".

## How it works

Neither site offers an official way to do what the app does, so it reads their public pages the way a browser would.

### Reading Bandcamp

Bandcamp has no public API or artist feed. For each tracked page the app reads the `/music` page, which lists the releases, then opens each release it has not read before to get the release date, posting date, label, track list, durations and the id its player needs. At most 6 new releases are opened per artist per refresh, so a long back catalogue fills in over several visits. Between the hourly refreshes, pages that still have older releases to load are visited again every 10 minutes (CATCH\_UP\_MINUTES in docker-compose.yml), 6 releases at a time. A release page that cannot be read is retried after the unread ones, so it cannot hold up the rest.

### Reading SoundCloud

SoundCloud's RSS feeds are empty for most musicians, so the app uses the same data endpoints the soundcloud.com website calls. They need a public key that is embedded in the site's own code. The app finds that key itself and replaces it when it expires. Only an artist's own uploads are read, not reposts. SoundCloud's track list sometimes leaves tracks out, coming back short or empty for a profile that has more. When that happens the app reads further pages of the list (up to 25 when they come back empty), then the profile's All tab and its Popular tracks tab, keeping only the artist's own uploads, until it has 30. This extra reading happens only for profiles whose list comes back short: it stops as soon as it reaches tracks already saved, and the All and Popular tabs are skipped when the whole list was read and holds every track the profile shows. SoundCloud also sometimes lists a track as just its number, with no title or link (most often older tracks); the app looks those up so they aren't dropped. If that comes back empty too while SoundCloud says the profile has tracks, the row's Status says so, and the app tries again on the next refresh. To see what SoundCloud returns for one profile, run docker compose exec artist-feed python -m app.check soundcloud username: it lists the tracks found and what each request returned.

### Reading YouTube

YouTube publishes a feed of every channel's 15 newest videos, which needs no key. A channel given by its @handle (or an old address) is opened once to read its id, which is then remembered. The feed gives no running times, so YouTube posts have no duration, and it does not mark Shorts apart from other videos. Link Finder's YouTube search reads YouTube's own results page, and keeps each answer for a week.

### Pacing

Every request to Bandcamp goes through one queue, two seconds apart, whether it comes from the feed, the purchases reader or the Link Finder.

- If Bandcamp answers "slow down" (error 429), everything waits as long as it asks, or 30 seconds doubling each time, and then carries on with a wider gap. The status line shows the pause.
- While a lookup or a purchases read is running, the feed's own Bandcamp checks stand aside and resume on the next refresh.

While the feed refreshes, the status line counts the pages checked and estimates the time left, for example "Checking for new posts… 118 of 452 pages · about 49 min left". Each site is timed separately, since the refresh ends when its slowest site does: a site's pace comes from the last full refresh at first, then from this refresh once five of its pages are done. Bandcamp's two-second gap and any pauses it asks for make it a rough guide: SoundCloud and YouTube pages are quick, Bandcamp pages slow.

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
| Your Bandcamp username | Link Finder panel, or `BANDCAMP_FAN` in `docker-compose.yml` | Lets Link Finder check your purchases first, and the Downloads button count what you have to download. Your collection must be public. Enter your own username in the Link Finder panel, or set it in the file. |
| Your SoundCloud username | Settings, or `SOUNDCLOUD_USER` in `docker-compose.yml` | Find missing links and Link Finder prefer the SoundCloud profiles you follow when several share a name. Your follows must be public. |
| Who can reach the app | `ports` line in `docker-compose.yml` | `"8080:8000"` lets any device on your network open it. `"127.0.0.1:8080:8000"` limits it to this computer. |

### Preferences in the app

These are remembered without editing any file.

| Preference | Where | Saved in |
| --- | --- | --- |
| Grid or List view | Title bar | This browser |
| Column widths | Drag in List view | This browser |
| Page width | Line below the filters | This browser |
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
| `SOUNDCLOUD_USER` | unset | Your SoundCloud username, whose followed profiles are preferred (also set in Settings, which wins) |
| `SOUNDCLOUD_CLIENT_ID` | unset | Supply your own SoundCloud key instead of the automatic one |
| `DISCOGS_TOKEN` | unset | Discogs personal access token for Link Finder (also set in Settings, which wins) |
| `BEATPORT_GAP_SECONDS` | 1.5 | Gap between Link Finder's requests to Beatport |
| `DISCOGS_GAP_SECONDS` | 1.1 | Gap between Link Finder's requests to Discogs (Discogs allows 60 a minute) |

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

### App icon

The icon in the browser tab is a file in `app/static`. The app uses the first of these it finds: `favicon.png`, `favicon.ico`, `favicon.svg`. It comes with `favicon.svg`, a headphones emoji.

To use your own, save a square image (64×64 pixels or larger) as `app/static/favicon.png`. Then run `docker compose up -d --build`. Browsers hold on to old icons, so close the tab and open the page again if the old one still shows. Delete `favicon.png` to go back to the emoji.

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
- When a new version improves the search, results that were **not** a Bandcamp release are searched again once. Found releases and links you set are kept. Bandcamp's search answers are themselves kept for 7 days (LOOKUP\_SEARCH\_DAYS in docker-compose.yml), so a repeat search like this asks Bandcamp almost nothing. Pressing Search again, or ticking either search-again box, asks Bandcamp anew.
- When a track isn't found, both the artist's Bandcamp page and SoundCloud profile are saved, so changing the preference needs no new search.

View, column widths, page width and Hide no-preview tracks are stored in the browser, not in `data`.

## Known limits

- **Neither site is an official integration.** If Bandcamp or SoundCloud changes its pages, that part stops updating until the app is fixed. Everything already collected stays.
- **Built without access to the real sites.** The app's logic is tested on stand-in pages. How it behaves on the real sites is known only from real use. Reading purchases, the feed, the players and most lookups have been seen working; SoundCloud track search and the address guess for unlisted tracks have not been confirmed.
- **Beatport and Discogs lookups are experimental.** Neither has been tried against the real site. Beatport is read the way a browser reads its search page and may be blocked; Discogs answers through its API but which release is picked is untested. Results from either may be missing or wrong.
- **Bandcamp limits how fast it can be read.** The first read of a large purchase history takes hours, and a lookup of a track that is nowhere can take two to three minutes.
- **Bandcamp's player cannot be started or controlled by the app.** One extra click is needed, and there is no shared volume control.
- **Pre-orders.** Only the released tracks of a pre-order can be played. Bandcamp's player shows one of those whichever track you pick.
- **Common artist names.** When a track is not on SoundCloud, a profile is picked by name alone and marked "matched by name only". For a name like River this can be the wrong person. The app learns the right profile for a name when it finds one of their tracks on SoundCloud, or when you set a SoundCloud link with Edit, and uses it from then on, including for answers already saved. Setting your SoundCloud username in Settings also helps: a profile you follow is picked before one matched by name alone, and its note reads "A profile you follow".
- **Subscriber-only and unlisted tracks** are found only if the label's page is one you track or have bought from and it carries the artist. One known miss: Hannibal Rex, Manyothers on Dungeon Beats. This was left as is by choice.
- **Labels that never name the artist.** A label release whose title, address and track list do not name the artist is not matched, because accepting title-only matches would give wrong links for common titles.
- **Label is often blank in the feed.** On Bandcamp it is filled in when a release sits on a label's page. On SoundCloud it appears only when the uploader entered it.
- **Excel may store a title such as "02" as the number 2.** Format the cell as text if a numeric title is not matching.
- **No Instagram, no login, no "mark as unheard".**
- YouTube has only been tested against stand-in copies of its pages. Its channel feed holds the 15 newest videos, has no running times and includes Shorts. Its search page is not an official interface and may change, or ask for a sign-in or a cookie choice in some countries; if it stops answering, YouTube search steps say so and the rest of the lookup carries on.

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

**Why does a lookup take so long, or seem stuck?** Bandcamp limits how fast it can be read, so requests go two seconds apart and pause when Bandcamp asks. A track that is not in your purchases can need several searches and dozens of page reads. The status line names the track being searched and shows any pause. Tracks already known are filled in first. If you know a track can't be found (an unreleased tune, say) or already have its link, press Skip or Add link on its row.

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

Versions 1 and 2 were delivered before the app showed a version number; the numbers are given here for reference. Versions 1 to 9 date from 9 October 2026, versions 10 to 33 from 10 October 2026, versions 34 to 40 from 11 October 2026.

| Version | What changed |
| --- | --- |
| 81 | A release shown on an artist's Bandcamp page but sold from another page (a label's), with no tracks and nothing to buy, now links and plays the page that sells it, in the feed and in Link Finder. Releases already saved without tracks are read again once. |
| 80 | Display settings: text size (90% to 150%) and colour scheme (Dark, Light, High contrast or Match this device), kept per browser or device. |
| 79 | Show the Downloads button moved from Settings to Link Finder, beside your Bandcamp username, and takes effect straight away. |
| 78 | Beatport and Discogs are listed under Sites in Settings (Link Finder only, experimental), both off by default; switching one on also ticks it for Link Finder. Discogs results link the release's main page. Notes that both lookups may be unreliable. |
| 77 | Settings has a Save button: changes take effect when you press it, the words beside it say Unsaved changes or Saved, and Close asks before dropping unsaved changes. The SoundCloud username and Discogs token are saved with it too; Read follows now and Remove token replace their own save buttons. |
| 76 | Link Finder can also search Beatport (after Bandcamp) and Discogs (last resort, finds vinyl-only releases; needs a token), both off until ticked in Settings. The Downloads button is off by default and shows greyed out, with a tooltip, until switched on. |
| 75 | The Downloads button is optional: Settings has a Show the Downloads button switch. Off, purchases aren't watched for downloads. |
| 74 | List view: a + beside each SoundCloud track opens its related tracks underneath (three at first, Show 5 more adds five at a time), read only when clicked. |
| 73 | SoundCloud's extra reading stops at tracks already saved, and skips the All and Popular tabs when the track list was complete, so later refreshes of quiet profiles take a few requests instead of twenty. |
| 72 | SoundCloud: empty track-list pages are followed further, reposts of an artist's own tracks count, and the Popular tracks tab is read as a last resort. app.check shows where each next page starts and what the All tab held. |
| 71 | SoundCloud tracks listed only by number (no title or link) are looked up instead of dropped; app.check reports how many there were. |
| 70 | SoundCloud profiles whose track list comes back short (not only empty) are filled in from further pages and the All tab, up to the 30 most recent uploads. |
| 69 | Check now in the Status column of Tracked Artists reads one artist's pages straight away. The Focus, YouTube search and Rescan checkboxes sit centred in their rows. |
| 68 | SoundCloud profiles whose track list comes back empty are read from their All tab instead, and the Status column says when SoundCloud lists none for a profile that has tracks. app.check shows what each SoundCloud request returned. |
| 67 | Focus column in Tracked Artists and a Focus Feed button showing only those artists. Removing a row now marks it (dark red-grey, with Undo) until you Save. |
| 66 | Refresh estimate timed per site, so it no longer climbs as the quick SoundCloud and YouTube pages finish. A ✕ in the search and filter boxes empties them. Tracked Artists button has a hover description. |
| 65 | Refresh progress: the status line shows pages checked so far and a rough estimate of the time left. |
| 64 | Downloads button: counts new Bandcamp purchases and pre-orders that have come out; clicking it opens your Bandcamp collection and clears the count. |
| 63 | Your SoundCloud username in Settings: the profiles it follows are preferred when Find missing links or Link Finder picks a SoundCloud profile by name. SOUNDCLOUD\_USER setting. |
| 62 | App icon: a headphones emoji in the browser tab, replaceable by saving favicon.png (or .ico or .svg) in app/static. |
| 61 | Filter row: the date menu sits next to the length menu. Show and its results menu moved to the left of the line below the filters, beside Page width, Reset column widths and Edit Columns. |
| 60 | Title bar: Tracked Artists, Link Finder and Settings on the right of the second line; Refresh Feed and Export list at the start of the filter row. |
| 59 | Title bar: Tracked Artists, Link Finder and Settings sit beside Grid \| List; Export list sits beside Refresh Feed. The Columns button is now Edit Columns. Setup instructions rewritten for newcomers to Docker, with a way to run the app without Docker. |
| 58 | Tracked Artists: two rows sharing a name are refused on Save, like two rows with the same page, unless you mark them as different artists with "Different artists, keep both". |
| 57 | List view: the hide column is titled Hide, and its ✕ hides only that track instead of the whole release. Cards still hide whole releases. |
| 56 | Tracked Artists: Filter rows box, a menu of row types (Needs attention, Still being checked, No posts yet, New, Ticked for YouTube search) with counts, and sortable columns. |
| 55 | YouTube as a third site: channels in the feed and player, a YouTube button at the top, a YouTube column and a YouTube search column in Tracked Artists. A new Settings button switches sites off completely and chooses which sites Link Finder searches (Bandcamp, then SoundCloud, then YouTube; YouTube off by default). Saved results has a "Searched in the last lookup" filter. |
| 54 | Feed search: only the newest search is shown when answers arrive out of order, Enter searches at once, and a failed load is reported instead of silently leaving the old list. |
| 53 | Forget in Saved results opens a choice: Saved answer, List entry (including the closest list entry), or Both. |
| 52 | Forget is now on every row in Saved results, including tracks that are only in your imported list, and it can take a track out of your list. |
| 51 | Saved results: tracks whose saved name doesn't match your list exactly show the closest list entry, marked as such. Edit spelling / Add spelling sets a track's artist and title spelling in place. |
| 50 | Link Finder: Skip and Add link on rows still waiting during a lookup, including the one being searched. Skip stops that track's search and moves on; Add link sets the link you know and the track is not searched. |
| 49 | Refresh Feed: no longer ignored while a smaller refresh is running (a full one is queued behind it). After a refresh, the status line reports new posts, Bandcamp pages skipped during a lookup, and pages that couldn't be read; each refresh is also summarised in the app's log. |
| 48 | Tracked Artists: an artist can have other names, typed after the first and separated by commas; Link Finder matches any of them and falls back to that artist's tracked Bandcamp page or SoundCloud profile. Find missing links shows the same glow and row highlighting as a Link Finder lookup. |
| 47 | Names: a symbol written into a word is kept when a name is tidied ("∑1" was becoming "1", and "Myca$h" "myca h"). Punctuation and symbols standing alone are still dropped. An imported list is re-filed under the new tidied names at start-up, so it does not need importing again. |
| 46 | Saved results: click a column heading to sort by it. The sort covers the whole table, not only the rows loaded, and the export follows it. |
| 45 | Link Finder: a remix, edit, flip or bootleg typed without brackets is read as one when the title as typed finds no track, so it falls back to the remixer's SoundCloud profile and matches your list's bracketed spelling. For any remix with no link of its own, the remixer's SoundCloud profile is used even when Bandcamp is the preferred artist link. |
| 44 | Saved results: the "Not found" filter no longer includes tracks found on SoundCloud and is renamed "Track not found (artist page only)". Remix answers saved before version 40 whose SoundCloud title repeats the artist are also moved to the remix's own entry. |
| 43 | Saved results: fixed Edit and Looks right doing nothing on VIP and remix tracks saved before version 40; those answers are moved to their version's own entry at start-up, so they also pick up the title spelling from your list. Your spelling is now two columns, artist and title. |
| 42 | Names: letters of other alphabets (Cyrillic, Chinese and so on) are kept when a name is tidied instead of being dropped, so titles written in them can be matched, saved and imported from your list. |
| 41 | Your list: a Year column is read and used when the lookup has no year. Repeated rows for a track are merged (commonest spelling; best link, then commonest). Versions that tidy to the same title are kept as separate entries. Columns without a heading are ignored. Names: ø, æ, ł and similar letters are now read as o, ae, l, so "Nocase" matches "Nøcase"; "(forth. Label)" and "(forthcoming)" are treated like WIP. |
| 40 | Link Finder: Saved results tab listing every track looked up, with search, filters, row actions and export. Import your own list of artist and title spellings, with optional links and link notes; spellings appear as separate columns and list links are used when the lookup has nothing better. Each version of a title (a remix, a VIP) now has its own saved answer instead of sharing the plain title's. |
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
