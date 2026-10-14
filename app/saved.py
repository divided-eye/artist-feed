"""Everything Link Finder has saved: one row per track ever looked up, plus the tracks in the list
you imported that haven't been looked up yet."""
import csv
import io
import json

from . import db, lookup

FILTERS = ("", "review", "missing", "found", "manual", "list", "nospelling", "unsearched")


def _entry(rest: str) -> dict:
    tidy_artist, _, tkey = rest.partition("|")
    base, _, version = tkey.partition("#")
    return {"key": rest, "tidy_artist": tidy_artist, "tidy_title": base, "version": version.replace("+", ", "),
            "artist": "", "title": "", "status": "", "url": "", "site": "", "year": "", "release": "", "found": "",
            "note": "", "at": "", "searched": False, "list_artist": "", "list_title": "", "list_link": "", "list_note": ""}


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
    for row in out.values():
        if not row["list_artist"]:
            row["list_artist"] = artists.get(row["tidy_artist"], "")
        row["review"] = lookup.review_reason(row)
        row["result"] = lookup.STATUS_TEXT.get(row["status"], row["status"]) if row["status"] else "Not looked up yet"
    return sorted(out.values(), key=lambda r: (not r["searched"], -_stamp(r["at"]), r["tidy_artist"], r["tidy_title"]))


def _from_list(row, t):
    row.update(list_artist=t["artist"], list_title=t["title"], list_link=t["url"], list_note=t["note"],
               year=row["year"] or t["year"])


def _stamp(at: str) -> int:
    digits = "".join(ch for ch in at if ch.isdigit())
    return int(digits or 0)


def _keep(row, which: str) -> bool:
    status = row["status"]
    return {"": True, "review": bool(row["review"]), "missing": status in lookup.FALLBACK_STATUSES or status == "error",
            "found": status in ("bandcamp", "soundcloud_track", "bandcamp_check"), "manual": status == "manual",
            "list": bool(row["list_title"] or row["list_link"]), "nospelling": not (row["list_title"] or row["list_artist"]),
            "unsearched": not row["searched"]}.get(which, True)


def listing(q="", which="", limit=200, offset=0) -> dict:
    rows = all_rows()
    counts = {name: sum(1 for r in rows if _keep(r, name)) for name in FILTERS}
    words = [w for w in lookup._fold(q or "").split() if w]
    if words:
        def text(r):
            return lookup._fold(" ".join(str(r[k]) for k in ("tidy_artist", "tidy_title", "version", "artist", "title", "list_artist",
                                                             "list_title", "url", "note", "list_note", "found", "release")))
        rows = [r for r in rows if all(w in text(r) for w in words)]
    rows = [r for r in rows if _keep(r, which)]
    return {"total": len(rows), "counts": counts, "rows": rows[offset:offset + limit] if limit else rows, **lookup.known_counts()}


def to_csv(q="", which="") -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["artist (tidied)", "title (tidied)", "version", "artist (as typed)", "title (as typed)",
                     "artist (your spelling)", "title (your spelling)", "year", "link", "needs review", "link note",
                     "your list's link", "site", "release", "result", "found as", "note", "saved"])
    for r in listing(q, which, limit=0)["rows"]:
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
