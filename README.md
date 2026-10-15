# Artist Feed

One page showing the newest releases and uploads from the artists and labels you
follow on Bandcamp, SoundCloud and YouTube, plus **Link Finder**, which finds a
Bandcamp or SoundCloud link for every track in a tracklist.

It runs on your own computer, and you use it in your web browser. Everything it
saves stays in the `data` folder next to these files.

The full user guide, covering every feature, is in
[`docs/Artist Feed User Guide.md`](docs/Artist%20Feed%20User%20Guide.md) (also as a
Word file in the same folder).

---

## Getting it running

There are two ways. **Docker** is recommended: it needs one program installed,
and the app then starts by itself whenever your computer does. If you'd rather not
install Docker, see [Without Docker](#without-docker).

### With Docker

Docker Desktop is a free program that runs the app in its own sealed-off space with
everything it needs included. You don't need to learn Docker; the commands below
are all you'll use.

**1. Install Docker Desktop (once).**

- Download it from <https://www.docker.com/products/docker-desktop/> and run the
  installer. On Windows, keep **Use WSL 2** ticked. If it says WSL needs updating,
  let it, or run `wsl --update` in PowerShell.
- Restart if asked, open Docker Desktop, and accept the terms (signing in is
  optional).
- Wait until it shows the engine is running (green, bottom left).

It needs 64-bit Windows 10/11 or a recent Mac, and is free for personal use.

**2. Get the app's files.** Either:

- with Git: `git clone https://github.com/divided-eye/artist-feed.git`, or
- download the zip (on GitHub: **Code → Download ZIP**) and unzip it.

Put the folder somewhere permanent, such as `Documents\artist-feed`. Check that the
folder you open directly contains `docker-compose.yml`. Windows' "Extract all" often
adds an extra outer folder.

**3. Start it.** Open a terminal in that folder: in File Explorer, click the address
bar, type `powershell` and press Enter. (Mac: open Terminal and `cd` to the folder.)
Then run:

```
docker compose up -d --build
```

The first time takes a few minutes; it finishes by saying the container is started.

**4. Open <http://localhost:8080>** in your browser. Click **Tracked Artists**, add
some artists, and press **Save changes**. Their posts appear within a minute.

From then on it starts whenever Docker Desktop starts. To have Docker Desktop start
with your computer, tick **Start Docker Desktop when you sign in** in its Settings.

**Everyday Docker commands** (run in the app's folder):

| Command | What it does |
|---|---|
| `docker compose up -d --build` | Start the app, or apply an update |
| `docker compose stop` / `docker compose start` | Stop it / start it again |
| `docker compose logs --tail 50` | Show its latest messages, if something's wrong |

**If the page won't open:** check Docker Desktop is running and the app has a green
dot under **Containers**. If something else already uses port 8080, change
`"8080:8000"` in `docker-compose.yml` to `"8090:8000"`, run
`docker compose up -d`, and open <http://localhost:8090>.

### Without Docker

The app is an ordinary Python program. Without Docker it only runs while its window
is open, and doesn't start by itself with the computer.

**1. Install Python 3.12 (once)** from <https://www.python.org/downloads/>. On the
installer's first screen, tick **Add python.exe to PATH**.

**2. Get the app's files** as in step 2 above.

**3. Install the app's libraries (once).** Open PowerShell in the app's folder and
run, one line at a time:

```
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

This makes a private Python environment for the app (the `.venv` folder) and
installs what it needs into it. If PowerShell says running scripts is disabled, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, answer `Y`, and retry.
On a Mac: `python3 -m venv .venv`, `source .venv/bin/activate`,
`pip install -r requirements.txt`.

**4. Start it**, each time, in PowerShell in the app's folder:

```
.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Open <http://localhost:8080>. Keep the window open while using the app; closing it
or pressing Ctrl+C stops it. Use `--host 127.0.0.1` to keep it to this computer only.

The settings in `docker-compose.yml` don't apply here; set them before starting
instead, e.g. `$env:REFRESH_MINUTES = "30"`. Data goes to the same `data` folder, so
you can switch between Docker and this way at any time.

---

## Updating

With Git (recommended):

```
git pull
docker compose up -d --build
```

Without Docker, run `pip install -r requirements.txt` after `git pull`, then start
the app as usual. Without Git, unzip the new version to a new folder, copy your
`data` folder into it, and start it from there.

The version number is shown beside the title, and at
<http://localhost:8080/api/status>. Every version's changes are listed in the
guide's Version log, and in `git log --oneline`.

## Your data

Everything (your artist list, the feed, hidden and played items, Link Finder's saved
results) is in `data/feed.db`. Back up the `data` folder to keep it all. Git ignores
it, so it's never uploaded or overwritten by an update.

There's no login: anyone who can reach port 8080 can use and change the app. Keep it
on your home network, or change the port line in `docker-compose.yml` to
`"127.0.0.1:8080:8000"` to limit it to this computer.

## Settings

The common ones are in **Settings** inside the app (which sites are used, which ones
Link Finder searches) and in the Link Finder panel. The rest are environment
settings in `docker-compose.yml`, described in the guide under "What to personalise".

## For developers

- `app/sources/`: one file per site (Bandcamp, SoundCloud, YouTube). To add a site,
  copy one, implement `normalize()` and `fetch()` (see `base.py`), and register it in
  `app/sources/__init__.py`.
- `app/lookup.py`: Link Finder. `app/static/index.html`: the whole page.
- Tests: `pip install pytest` then `pytest`. They run the app against stand-in copies
  of the sites, so they check the app's logic, not whether the real sites changed.
- Check what the app can read from a page:
  `docker compose run --rm artist-feed python -m app.check bandcamp artistname`

## License

Free to use, copy, change and share, under the [MIT License](LICENSE).
