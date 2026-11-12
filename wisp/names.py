"""Names in the player's language, from Blizzard's own data.

The game names what it has at hand in the player's language, but the lists read
from the Questie addon may be English: every item it knows, and every creature
(the game gives no creature's name by its number). Blizzard's API hands out the
names of all items and all creatures in every language; with the player's own
developer client Wisp fetches them once, in the background, and keeps them in
its data file beside the prices:

    name(kind, id, lang, name)          kind: item | creature
    name_progress(kind, lang, upto, done, at)

A fetch that is cut short carries on where it stopped. It is done again after a
month, for what a patch added. Nothing about the player is sent.
"""
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS name(kind TEXT, id INTEGER, lang TEXT, name TEXT, PRIMARY KEY (kind, id, lang)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS name_progress(kind TEXT, lang TEXT, upto INTEGER, done INTEGER, at INTEGER, PRIMARY KEY (kind, lang)) WITHOUT ROWID;
"""
KINDS = ("item", "creature")
LOCALE = {"ru": "ru_RU"}      # Wisp's language -> Blizzard's; English needs no fetching, Questie's names are English
AGAIN = 30 * 86400            # a finished list is fetched again after this long
MOST = 6000                   # requests for one list at the very most


class Names:
    def __init__(self, file: Path):
        self.file = file
        self.version = 0          # goes up when a list has been fetched: what was built with the old names is stale
        self._loaded = None       # (lang, version) -> the names, read once

    def _open(self) -> sqlite3.Connection:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.file, timeout=10)
        db.executescript(SCHEMA)
        return db

    def load(self, lang: str) -> dict[str, dict[int, str]]:
        """{"item": {id: name}, "creature": {id: name}} in that language; empty for a language not fetched."""
        if lang not in LOCALE:
            return {}
        if self._loaded and self._loaded[0] == (lang, self.version):
            return self._loaded[1]
        out = {kind: {} for kind in KINDS}
        try:
            db = self._open()
            try:
                for kind, some, name in db.execute("SELECT kind, id, name FROM name WHERE lang = ?", (lang,)):
                    if kind in out:
                        out[kind][some] = name
            finally:
                db.close()
        except sqlite3.Error:
            pass
        self._loaded = ((lang, self.version), out)
        return out

    def counts(self, lang: str) -> dict:
        """Per kind: how many names are kept, and whether the list is complete."""
        out = {}
        try:
            db = self._open()
            try:
                for kind in KINDS:
                    n = db.execute("SELECT count(*) FROM name WHERE kind = ? AND lang = ?", (kind, lang)).fetchone()[0]
                    row = db.execute("SELECT done FROM name_progress WHERE kind = ? AND lang = ?", (kind, lang)).fetchone()
                    out[kind] = {"names": n, "complete": bool(row and row[0])}
            finally:
                db.close()
        except sqlite3.Error:
            pass
        return out

    def wanted(self, lang: str) -> list[str]:
        """The lists still to fetch (or to fetch again) for this language."""
        if lang not in LOCALE:
            return []
        try:
            db = self._open()
            try:
                rows = {kind: (done, at) for kind, done, at in db.execute("SELECT kind, done, at FROM name_progress WHERE lang = ?", (lang,))}
            finally:
                db.close()
        except sqlite3.Error:
            return []
        now = time.time()
        return [kind for kind in KINDS if not rows.get(kind, (0, 0))[0] or now - rows[kind][1] > AGAIN]

    def sync(self, api, lang: str, should_stop=lambda: False, pause: float = 0.05) -> dict:
        """Fetch what is wanted. Returns {kind: names written}. `api` is a wisp.blizzard.Blizzard."""
        from .blizzard import BlizzardError
        written, locale = {}, LOCALE.get(lang)
        for kind in self.wanted(lang):
            db = self._open()
            try:
                row = db.execute("SELECT upto, done, at FROM name_progress WHERE kind = ? AND lang = ?", (kind, lang)).fetchone()
                start = 0 if not row or row[1] else row[0]   # a finished list starts over; an unfinished one carries on
                count, finished = 0, False
                for _ in range(MOST):
                    if should_stop():
                        break
                    try:   # in order of id, from the last one seen: whatever page size Blizzard allows, nothing is missed
                        page = api._get_ns(f"/data/wow/search/{kind}", api._static_namespace(), 60,
                                           {"orderby": "id", "_pageSize": 1000, "id": f"[{start + 1},]"})
                    except BlizzardError:
                        break
                    rows, was = [], start
                    for result in page.get("results") or []:
                        data = result.get("data") if isinstance(result, dict) else None
                        names = data.get("name") if isinstance(data, dict) else None
                        if isinstance(data.get("id") if isinstance(data, dict) else None, int) and isinstance(names, dict):
                            local = names.get(locale)
                            if isinstance(local, str) and local.strip():
                                rows.append((kind, data["id"], lang, local.strip()[:120]))
                            start = max(start, data["id"])
                    if not page.get("results") or start == was:   # nothing more, or nothing with a number: the end
                        finished = True
                        break
                    with db:
                        db.executemany("INSERT OR REPLACE INTO name VALUES (?, ?, ?, ?)", rows)
                        db.execute("INSERT OR REPLACE INTO name_progress VALUES (?, ?, ?, 0, ?)", (kind, lang, start, int(time.time())))
                    count += len(rows)
                    time.sleep(pause)
                if finished:
                    with db:
                        db.execute("INSERT OR REPLACE INTO name_progress VALUES (?, ?, ?, 1, ?)", (kind, lang, start, int(time.time())))
                written[kind] = count
            except sqlite3.Error:
                pass
            finally:
                db.close()
            if written.get(kind):
                self.version += 1
        return written
