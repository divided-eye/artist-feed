"""SQLite storage. One small file on disk; no server needed."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "data/feed.db")

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS artists(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY,
  artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  handle TEXT NOT NULL,
  url TEXT NOT NULL,
  display_name TEXT,
  avatar TEXT,
  meta TEXT NOT NULL DEFAULT '{}',
  last_checked TEXT,
  last_error TEXT,
  UNIQUE(source, handle)
);
CREATE TABLE IF NOT EXISTS posts(
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  external_id TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  image TEXT,
  kind TEXT,
  author TEXT,
  published_at TEXT,
  first_seen TEXT NOT NULL,
  label TEXT,
  release TEXT,
  embed TEXT,
  detailed INTEGER NOT NULL DEFAULT 0,
  hidden INTEGER NOT NULL DEFAULT 0,
  posted_at TEXT,
  UNIQUE(account_id, external_id)
);
CREATE TABLE IF NOT EXISTS tracks(
  id INTEGER PRIMARY KEY,
  post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  title TEXT NOT NULL,
  artist TEXT,
  duration REAL,
  ext_id TEXT,
  url TEXT
);
CREATE INDEX IF NOT EXISTS tracks_post ON tracks(post_id, position);
CREATE INDEX IF NOT EXISTS posts_url ON posts(url);
CREATE INDEX IF NOT EXISTS posts_when ON posts(COALESCE(published_at, first_seen) DESC);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
-- What you have listened to: track '' means the release as a whole (played from its card).
CREATE TABLE IF NOT EXISTS played(post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  track TEXT NOT NULL DEFAULT '', at TEXT NOT NULL, PRIMARY KEY(post_id, track));
CREATE TABLE IF NOT EXISTS lookup_cache(key TEXT PRIMARY KEY, result TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS web_cache(kind TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY(kind, key));
CREATE TABLE IF NOT EXISTS collection_items(key TEXT PRIMARY KEY, fan TEXT NOT NULL, title TEXT NOT NULL,
  band TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collection_tracks(item_key TEXT NOT NULL, position INTEGER, title TEXT NOT NULL, artist TEXT);
CREATE INDEX IF NOT EXISTS collection_tracks_item ON collection_tracks(item_key);
-- Your own list of tracks: how each artist and title should be written, and optionally a link.
CREATE TABLE IF NOT EXISTS known_tracks(key TEXT PRIMARY KEY, artist TEXT NOT NULL, title TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '', others TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS known_artists(key TEXT PRIMARY KEY, name TEXT NOT NULL, at TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            folder = os.path.dirname(DB_PATH)
            if folder:
                os.makedirs(folder, exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
            migrate(_conn)
            _conn.execute("CREATE INDEX IF NOT EXISTS posts_embed ON posts(embed)")
        return _conn


# Columns added after the first version. A database made by an older version
# gets them here; its existing posts keep detailed=0, which makes the sources
# re-read them (a few per refresh) to fill in labels, track lists and durations.
ADDED_POST_COLUMNS = {
    "label": "TEXT", "release": "TEXT", "embed": "TEXT",
    "detailed": "INTEGER NOT NULL DEFAULT 0", "posted_at": "TEXT",
    "hidden": "INTEGER NOT NULL DEFAULT 0",
}
# Raise this when the sources start collecting something new: posts stored with
# a lower number are re-read (a few per refresh) to pick it up.
DETAIL_VERSION = 3


def migrate(c):
    have = {r[1] for r in c.execute("PRAGMA table_info(posts)")}
    for name, kind in ADDED_POST_COLUMNS.items():
        if name not in have:
            c.execute(f"ALTER TABLE posts ADD COLUMN {name} {kind}")
    c.commit()


def reset():
    """Close the connection (used by tests when DB_PATH changes)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


def query(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def execute(sql, args=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


# --- key/value (small bits of source state, e.g. a cached API key) ---

def kv_get(key, default=None):
    rows = query("SELECT value FROM kv WHERE key=?", (key,))
    return rows[0]["value"] if rows else default


def kv_set(key, value):
    execute("INSERT INTO kv(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# --- artists and accounts ---

def _account_row(r):
    r["meta"] = json.loads(r.get("meta") or "{}")
    return r


def list_accounts(account_ids=None, artist_id=None):
    sql, args = "SELECT * FROM accounts", []
    if account_ids is not None:
        sql += " WHERE id IN (%s)" % ",".join("?" * len(account_ids))
        args = list(account_ids)
    elif artist_id is not None:
        sql += " WHERE artist_id=?"
        args = [artist_id]
    return [_account_row(r) for r in query(sql + " ORDER BY id", args)]


def list_artists():
    artists = query("SELECT * FROM artists ORDER BY name COLLATE NOCASE")
    by_artist = {}
    for acc in list_accounts():
        acc.pop("meta")
        by_artist.setdefault(acc["artist_id"], []).append(acc)
    counts = {r["artist_id"]: r["n"] for r in query(
        "SELECT a.artist_id, COUNT(*) n FROM posts p JOIN accounts a ON a.id=p.account_id "
        "GROUP BY a.artist_id")}
    for a in artists:
        a["accounts"] = by_artist.get(a["id"], [])
        a["post_count"] = counts.get(a["id"], 0)
    return artists


def add_artist(name):
    return execute("INSERT INTO artists(name, created_at) VALUES(?,?)", (name, now()))


def add_account(artist_id, source, handle, url):
    return execute("INSERT INTO accounts(artist_id, source, handle, url) VALUES(?,?,?,?)",
                   (artist_id, source, handle, url))


def apply_table(keep_ids, remove_account_ids, renames, new_accounts, new_artists):
    """Make the artist list match an edited table, as one all-or-nothing change.

    keep_ids: artist ids that stay (every other artist is removed)
    remove_account_ids: pages to stop tracking
    renames: [(artist_id, name)]
    new_accounts: [(artist_id, source, handle, url)]
    new_artists: [(name, [(source, handle, url), ...])]
    Returns the ids of the pages that were added.
    """
    added = []
    with _lock:
        c = conn()
        try:
            marks = ",".join("?" * len(keep_ids))
            c.execute(f"DELETE FROM artists WHERE id NOT IN ({marks})", list(keep_ids))
            for account_id in remove_account_ids:
                c.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            for artist_id, name in renames:
                c.execute("UPDATE artists SET name=? WHERE id=?", (name, artist_id))
            insert = "INSERT INTO accounts(artist_id, source, handle, url) VALUES(?,?,?,?)"
            for artist_id, source, handle, url in new_accounts:
                added.append(c.execute(insert, (artist_id, source, handle, url)).lastrowid)
            for name, pages in new_artists:
                artist_id = c.execute("INSERT INTO artists(name, created_at) VALUES(?,?)",
                                      (name, now())).lastrowid
                for source, handle, url in pages:
                    added.append(c.execute(insert, (artist_id, source, handle, url)).lastrowid)
            c.commit()
        except Exception:
            c.rollback()
            raise
    return added


def known_ids(account_id):
    return {r["external_id"] for r in query(
        "SELECT external_id FROM posts WHERE account_id=? AND detailed>=?",
        (account_id, DETAIL_VERSION))}


def save_result(account, result):
    """Store what a source returned for one account."""
    seen = now()
    with _lock:
        c = conn()
        for p in result.posts:
            c.execute(
                "INSERT INTO posts(account_id, external_id, title, url, image, kind, author,"
                " published_at, first_seen, label, release, embed, posted_at, detailed)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(account_id, external_id) DO UPDATE SET title=excluded.title,"
                " image=COALESCE(excluded.image, image),"
                " author=COALESCE(excluded.author, author),"
                " published_at=COALESCE(excluded.published_at, published_at),"
                " label=COALESCE(excluded.label, label),"
                " release=COALESCE(excluded.release, release),"
                " embed=COALESCE(excluded.embed, embed),"
                " posted_at=COALESCE(excluded.posted_at, posted_at), detailed=excluded.detailed",
                (account["id"], p.external_id, p.title, p.url, p.image, p.kind, p.author,
                 p.published_at, seen, p.label, p.release, p.embed, p.posted_at, DETAIL_VERSION))
            if p.tracks is not None:
                post_id = c.execute("SELECT id FROM posts WHERE account_id=? AND external_id=?",
                                    (account["id"], p.external_id)).fetchone()[0]
                c.execute("DELETE FROM tracks WHERE post_id=?", (post_id,))
                c.executemany(
                    "INSERT INTO tracks(post_id, position, title, artist, duration, ext_id, url)"
                    " VALUES(?,?,?,?,?,?,?)",
                    [(post_id, i + 1, t.title, t.artist, t.duration, t.ext_id, t.url)
                     for i, t in enumerate(p.tracks)])
        c.execute(
            "UPDATE accounts SET display_name=COALESCE(?, display_name),"
            " avatar=COALESCE(?, avatar), meta=?, last_checked=?, last_error=? WHERE id=?",
            (result.display_name, result.avatar, json.dumps(account["meta"]), seen,
             result.warning, account["id"]))
        # An artist added without a name takes the name the site reports.
        if result.display_name:
            c.execute("UPDATE artists SET name=? WHERE id=? AND name IN "
                      "(SELECT handle FROM accounts WHERE artist_id=?)",
                      (result.display_name, account["artist_id"], account["artist_id"]))
        c.commit()


def save_error(account_id, message):
    execute("UPDATE accounts SET last_checked=?, last_error=? WHERE id=?",
            (now(), message[:300], account_id))


WHEN = "COALESCE(p.published_at, p.first_seen)"
POSTED = "p.posted_at"   # blank when the site doesn't give a posting date
JOINS = ("FROM posts p JOIN accounts a ON a.id=p.account_id "
         "JOIN artists ar ON ar.id=a.artist_id ")
# One row per track; a post whose tracks are not known yet still gets one row.
T_ARTIST = "COALESCE(NULLIF(t.artist,''), NULLIF(p.author,''), ar.name)"
T_LABEL = "COALESCE(p.label,'')"
T_RELEASE = "COALESCE(NULLIF(p.release,''), CASE WHEN p.kind='album' THEN p.title ELSE '' END)"
T_TRACK = "COALESCE(t.title, CASE WHEN p.kind='album' THEN '' ELSE p.title END)"
TRACK_SORTS = {"artist": T_ARTIST, "label": T_LABEL, "release": T_RELEASE, "track": T_TRACK}


def _like(q):
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


LENGTHS = {            # seconds: at least, under
    "short": (0, 180), "medium": (180, 360), "long": (360, 600), "extended": (600, None),
}


def _filters(source, artist_id, q, fields, more=None, per_track=False):
    """WHERE clause shared by the grid, the list and the export.

    more: optional {"year", "label", "kind", "length"}; label "-" means "no label".
    """
    more = more or {}
    where, args = [], []
    if not more.get("hidden"):                 # hidden items only appear when asked for
        where.append("p.hidden=0")
    # The same release can reach the feed through two tracked pages (an artist's
    # and their label's, say). Show it once: the copy with the most detail.
    same = ("SELECT p2.id FROM posts p2 JOIN accounts a2 ON a2.id=p2.account_id "
            "WHERE a2.source=a.source AND (p2.url=p.url OR p2.embed=p.embed)")
    if artist_id:      # when looking at one artist, only that artist's copies compete
        same += " AND a2.artist_id=?"
        args.append(artist_id)
    where.append(f"p.id=({same} ORDER BY (COALESCE(p2.label,'')='') ASC,"
                 " p2.detailed DESC, p2.id ASC LIMIT 1)")
    if source:
        where.append("a.source=?")
        args.append(source)
    if artist_id:
        where.append("a.artist_id=?")
        args.append(artist_id)
    if more.get("year"):
        where.append(f"substr({WHEN},1,4)=?")
        args.append(str(more["year"]))
    if more.get("label") == "-":
        where.append("COALESCE(p.label,'')=''")
    elif more.get("label"):
        where.append("p.label=?")
        args.append(more["label"])
    if more.get("since") or more.get("until"):
        when = WHEN if more.get("date_by") == "released" else f"COALESCE({POSTED}, {WHEN})"
        if more.get("since"):
            where.append(f"substr({when},1,10)>=?")
            args.append(more["since"])
        if more.get("until"):
            where.append(f"substr({when},1,10)<=?")
            args.append(more["until"])
    if more.get("kind") in ("album", "track"):
        where.append("p.kind=?")
        args.append(more["kind"])
    if more.get("preview"):                    # leave out what can't be listened to yet
        # A Bandcamp track with no length has no stream (the unreleased tracks of a pre-order).
        if per_track:
            where.append("NOT (a.source='bandcamp' AND t.id IS NOT NULL AND t.duration IS NULL)")
        else:   # a release goes only when none of its tracks can be played
            where.append("NOT (a.source='bandcamp' AND EXISTS(SELECT 1 FROM tracks WHERE post_id=p.id)"
                         " AND NOT EXISTS(SELECT 1 FROM tracks WHERE post_id=p.id AND duration IS NOT NULL))")
    if more.get("length") in LENGTHS:
        low, high = LENGTHS[more["length"]]
        test = "{d}>=?" + (" AND {d}<?" if high is not None else "")
        if per_track:
            where.append("(" + test.format(d="t.duration") + ")")
        else:   # a release matches when any of its tracks does
            where.append("EXISTS(SELECT 1 FROM tracks WHERE post_id=p.id AND "
                         + test.format(d="duration") + ")")
        args += [low] + ([high] if high is not None else [])
    for word in (q or "").split():
        where.append("(" + " OR ".join(f"{f} LIKE ? ESCAPE '\\'" for f in fields) + ")")
        args += [_like(word)] * len(fields)
    return ("WHERE " + " AND ".join(where) + " " if where else ""), args


def facets():
    """The years and labels present, for the filter menus."""
    years = [r["y"] for r in query(
        f"SELECT DISTINCT substr({WHEN},1,4) AS y FROM posts p ORDER BY y DESC") if r["y"]]
    labels = [r["label"] for r in query(
        "SELECT DISTINCT label FROM posts WHERE COALESCE(label,'')<>'' "
        "ORDER BY label COLLATE NOCASE")]
    hidden = query("SELECT COUNT(DISTINCT COALESCE(embed, url)) AS n FROM posts WHERE hidden=1")[0]["n"]
    return {"years": years, "labels": labels, "hidden": hidden}


def set_hidden(post_id, hidden):
    """Hide a release from the feed (or put it back). Copies of the same release that arrived
    through another tracked page are hidden with it."""
    rows = query("SELECT p.url, p.embed, a.source FROM posts p JOIN accounts a ON a.id=p.account_id "
                 "WHERE p.id=?", (post_id,))
    if not rows:
        return False
    r = rows[0]
    execute("UPDATE posts SET hidden=? WHERE id IN (SELECT p.id FROM posts p JOIN accounts a "
            "ON a.id=p.account_id WHERE a.source=? AND (p.url=? OR (p.embed IS NOT NULL AND p.embed=?)))",
            (1 if hidden else 0, r["source"], r["url"], r["embed"]))
    return True


def set_played(post_id, track, played=True):
    if not query("SELECT 1 FROM posts WHERE id=?", (post_id,)):
        return False
    if played:
        execute("INSERT OR IGNORE INTO played(post_id, track, at) VALUES(?,?,?)", (post_id, track or "", now()))
    elif track:
        execute("DELETE FROM played WHERE post_id=? AND track=?", (post_id, track))
    else:
        execute("DELETE FROM played WHERE post_id=?", (post_id,))
    return True


def feed(source=None, artist_id=None, limit=60, offset=0, q=None, more=None):
    """One row per post (release or upload), newest first."""
    where, args = _filters(source, artist_id, q, [
        "p.title", "COALESCE(p.author,'')", "ar.name", "COALESCE(p.label,'')",
        "COALESCE(p.release,'')",
        "(SELECT COALESCE(GROUP_CONCAT(title, ' '), '') FROM tracks WHERE post_id=p.id)"], more)
    sql = (
        "SELECT p.id, p.title, p.url, p.image, p.kind, p.author, p.published_at, p.first_seen,"
        " p.label, p.release, p.embed, p.hidden, a.source, a.avatar, a.display_name,"
        " EXISTS(SELECT 1 FROM played WHERE post_id=p.id) AS played,"
        " ar.id AS artist_id, ar.name AS artist,"
        " (SELECT COUNT(*) FROM tracks WHERE post_id=p.id) AS track_count,"
        " (SELECT SUM(duration) FROM tracks WHERE post_id=p.id) AS duration "
        + JOINS + where
        + f"ORDER BY COALESCE({POSTED}, {WHEN}) DESC, p.id DESC LIMIT ? OFFSET ?")
    return query(sql, args + [limit, offset])


def tracks(source=None, artist_id=None, q=None, sort="date", direction="desc",
           limit=None, offset=0, more=None):
    """One row per track, for the list view and its export."""
    where, args = _filters(source, artist_id, q, [
        T_ARTIST, T_LABEL, T_RELEASE, T_TRACK, "p.title", "ar.name"], more, per_track=True)
    way = "ASC" if direction == "asc" else "DESC"
    newest = f"{WHEN} DESC, p.id DESC, t.position ASC"
    if sort in TRACK_SORTS:      # blanks last, then newest first within equal values
        expr = TRACK_SORTS[sort]
        order = f"({expr}='') ASC, {expr} COLLATE NOCASE {way}, {newest}"
    elif sort == "posted":
        order = f"({POSTED} IS NULL) ASC, {POSTED} {way}, {newest}"
    elif sort == "duration":
        order = f"(t.duration IS NULL) ASC, t.duration {way}, {newest}"
    else:
        order = f"{WHEN} {way}, p.id {way}, t.position ASC"
    sql = (
        f"SELECT p.id AS post_id, t.id AS track_id, {T_ARTIST} AS artist, {T_LABEL} AS label,"
        f" {T_RELEASE} AS release, {T_TRACK} AS track, t.duration, t.position,"
        f" t.ext_id AS track_ext_id, {WHEN} AS released, p.published_at, {POSTED} AS posted,"
        " COALESCE(t.url, p.url) AS url, p.url AS post_url, p.embed, p.image, p.kind, p.hidden,"
        " COALESCE(t.ext_id, t.title, '') AS track_key,"
        " EXISTS(SELECT 1 FROM played pl WHERE pl.post_id=p.id"
        "        AND pl.track IN ('', COALESCE(t.ext_id, t.title, ''))) AS played,"
        " p.title AS post_title, a.source, ar.id AS artist_id, ar.name AS tracked_artist "
        + JOINS + "LEFT JOIN tracks t ON t.post_id=p.id " + where + "ORDER BY " + order)
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args = args + [limit, offset]
    return query(sql, args)


def executemany(sql, rows):
    with _lock:
        c = conn()
        c.executemany(sql, rows)
        c.commit()
