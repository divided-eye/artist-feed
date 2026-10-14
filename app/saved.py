"""Everything Link Finder has saved: one row per track ever looked up, plus the tracks in the list
you imported that haven't been looked up yet."""
import csv
import io
import json

from . import db, lookup

# No link to the track itself: an artist's page, a profile, a search, or nothing. (A track found on
# SoundCloud is found, even though the lookup still checks Bandcamp for it again later.)
NOT_FOUND = ("bandcamp_artist", "soundcloud", "soundcloud_search", "none", "error")
FILTERS = ("", "review", "missing", "found", "manual", "list", "nospelling", "unsearched")


def _entry(rest: str) -> dict:
    tidy_artist, _, tkey = rest.partition("|")
    base, _, version = tkey.partition("#")
    return {"key": rest, "tidy_artist": tidy_artist, "tidy_title": base, "version": version.replace("+", ", "),
            "artist": "", "title": "", "status": "", "url": "", "site": "", "year": "", "release": "", "found": "",
            "note": "", "at": "", "searched": False, "list_artist": "", "list_title": "", "list_link": "", "list_note": "", "list_key": "", "list_variant": "", "list_guess": False}


def all_rows() -> list:
    tracks, artists = lookup._known()
    out, manual = {}, set()
    for r in db.query("SELECT key, result, at FROM lookup_cache"):
        kind, _, rest = r["key"].partition("|")
        if kind not in (lookup.CACHE_VERSION, "manual") or rest in manual:
            continue                                        # a link you set outranks the saved search
        try:
            saved = json.loads(r["result"])
        except ValueError:
            continue
        if kind == "manual":
            manual.add(rest)
        else:
            saved = lookup.with_current_fallback(saved) or saved
        row = out[rest] = _entry(rest)
        row.update({k: saved.get(k) or "" for k in ("artist", "title", "status", "url", "site", "year", "release", "found", "note")})
        row.update(at=r["at"], searched=True)
    for key, entries in tracks.items():
        row, shown = out.get(key), None
        if row is not None:                                 # looked up: the entry written the way it was typed
            shown = (lookup.known_entry(row["artist"], row["title"]) if row["title"] else None) or entries[0]
            if shown["url"] and row["status"] not in lookup.STRONG:
                row.update({k: v for k, v in lookup.from_list(shown).items() if k != "steps"})
            _from_list(row, shown)
        for t in entries:                                   # in your list, never looked up
            if t is shown:
                continue
            extra = out[key if key not in out else f"{key}~{t['variant']}"] = _entry(key)
            if t["url"]:
                extra.update({k: v for k, v in lookup.from_list(t).items() if k != "steps"}, at=t["at"])
            _from_list(extra, t)
    by_artist = {}
    for key, entries in tracks.items():
        by_artist.setdefault(key.partition("|")[0], []).extend(entries)
    for row in out.values():
        if not (row["list_title"] or row["list_link"]):        # nothing under this exact entry: the closest one
            guess = _closest(by_artist.get(row["tidy_artist"], []), row)
            if guess:
                _from_list(row, guess)
                row["list_guess"] = True
        if not row["list_artist"]:
            row["list_artist"] = artists.get(row["tidy_artist"], "")
        row["review"] = lookup.review_reason(row)
        row["result"] = lookup.STATUS_TEXT.get(row["status"], row["status"]) if row["status"] else "Not looked up yet"
    return sorted(out.values(), key=lambda r: (not r["searched"], -_stamp(r["at"]), r["tidy_artist"], r["tidy_title"]))


def _from_list(row, t):
    row.update(list_artist=t["artist"], list_title=t["title"], list_link=t["url"], list_note=t["note"],
               year=row["year"] or t["year"], list_key=t["key"], list_variant=t["variant"])


def _closest(entries, row):
    """Your list's entry for a saved track that doesn't match one exactly - typically saved before
    versions had entries of their own, or typed without brackets: the entry with the same words
    ("you owe me nightmare oni 140 bootleg" is "You Owe Me (Nightmare & Oni 140 Bootleg)"), else the
    only version of that title in your list ("temperature" and "Temperature (River Dub Edit)")."""
    words = lookup.plain(" ".join(filter(None, [row["tidy_title"], row["version"]])))
    same = [t for t in entries if t["variant"] == words]
    if same:
        return same[0]
    base = row["tidy_title"]
    versions = [t for t in entries if t["key"].partition("|")[2].partition("#")[0] == base]
    return versions[0] if len(versions) == 1 else None


def _stamp(at: str) -> int:
    digits = "".join(ch for ch in at if ch.isdigit())
    return int(digits or 0)


def _keep(row, which: str) -> bool:
    status = row["status"]
    return {"": True, "review": bool(row["review"]), "missing": status in NOT_FOUND,
            "found": status in ("bandcamp", "soundcloud_track", "bandcamp_check"), "manual": status == "manual",
            "list": bool(row["list_title"] or row["list_link"]), "nospelling": not (row["list_title"] or row["list_artist"]),
            "unsearched": not row["searched"]}.get(which, True)


SORTS = {"artist": lambda r: (r["tidy_artist"], r["tidy_title"], r["version"]),
         "title": lambda r: (r["tidy_title"], r["version"], r["tidy_artist"]),
         "list_artist": lambda r: lookup._fold(r["list_artist"]),
         "list_title": lambda r: lookup._fold(r["list_title"]),
         "link": lambda r: r["url"].split("://")[-1].removeprefix("www.").casefold(),
         "year": lambda r: r["year"],
         "result": lambda r: (not r["review"], r["result"].casefold()),      # rows to review lead
         "saved": lambda r: r["at"] if r["searched"] else ""}


def _sorted(rows, sort, direction):
    """Rows in the order asked for. Rows with nothing in that column go last either way, and rows
    that tie keep the usual order (newest first)."""
    key = SORTS.get(sort)
    if not key:
        return rows
    blank = lambda r: key(r) in ("", (True, ""), (False, ""))
    filled = sorted((r for r in rows if not blank(r)), key=key, reverse=direction == "desc")
    return filled + [r for r in rows if blank(r)]


def listing(q="", which="", limit=200, offset=0, sort="", direction="asc") -> dict:
    rows = all_rows()
    counts = {name: sum(1 for r in rows if _keep(r, name)) for name in FILTERS}
    words = [w for w in lookup._fold(q or "").split() if w]
    if words:
        def text(r):
            return lookup._fold(" ".join(str(r[k]) for k in ("tidy_artist", "tidy_title", "version", "artist", "title", "list_artist",
                                                             "list_title", "url", "note", "list_note", "found", "release")))
        rows = [r for r in rows if all(w in text(r) for w in words)]
    rows = _sorted([r for r in rows if _keep(r, which)], sort, direction)
    return {"total": len(rows), "counts": counts, "rows": rows[offset:offset + limit] if limit else rows, **lookup.known_counts()}


def to_csv(q="", which="", sort="", direction="asc") -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["artist (tidied)", "title (tidied)", "version", "artist (as typed)", "title (as typed)",
                     "artist (your spelling)", "title (your spelling)", "year", "link", "needs review", "link note",
                     "your list's link", "site", "release", "result", "found as", "note", "saved"])
    for r in listing(q, which, limit=0, sort=sort, direction=direction)["rows"]:
        writer.writerow([r["tidy_artist"], r["tidy_title"], r["version"], r["artist"], r["title"], r["list_artist"], r["list_title"],
                         r["year"], r["url"], ("REVIEW: " + r["review"]) if r["review"] else "", r["list_note"], r["list_link"],
                         r["site"], r["release"], r["result"], r["found"], r["note"], r["at"]])
    return out.getvalue().encode("utf-8-sig")


def forget(rest: str) -> int:
    """Drop what is saved for one track: the search's answer and any link you set. Your list is kept."""
    before = db.query("SELECT COUNT(*) AS n FROM lookup_cache WHERE key IN (?,?)",
                      (f"{lookup.CACHE_VERSION}|{rest}", f"manual|{rest}"))[0]["n"]
    db.execute("DELETE FROM lookup_cache WHERE key IN (?,?)", (f"{lookup.CACHE_VERSION}|{rest}", f"manual|{rest}"))
    return before
