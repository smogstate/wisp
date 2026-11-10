"""The auction house over time. The Auctionator addon keeps about three weeks of
its scans; every logout hands Wisp the new days, and Wisp keeps them for good
in one SQLite file on this PC:

    price(market, item, day, low, high, listed)   one row per item and day scanned
    item(id, name)

`market` is one for every character (Denis: "shared among all chars"). `day` counts days since 1970. Everything said
about a price (its usual level, its direction, a forecast) is computed here
from those rows, in code; the model only words it.
"""
import datetime
import json
import re
import sqlite3
import statistics
from pathlib import Path

from .crafting import coins
from .i18n import t, tn

SCHEMA = """
CREATE TABLE IF NOT EXISTS price(market TEXT, item INTEGER, day INTEGER, low INTEGER, high INTEGER, listed INTEGER,
                                 PRIMARY KEY (market, item, day)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS item(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS handed(market TEXT, taken INTEGER, PRIMARY KEY (market, taken)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS depth(market TEXT, item INTEGER, day INTEGER, at INTEGER, auctions INTEGER, mine INTEGER, mine_at INTEGER,
                                 partial INTEGER, steps TEXT, PRIMARY KEY (market, item, day)) WITHOUT ROWID;
"""
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


SHARED = "all"


def market_key(snapshot: dict) -> str:
    """Every character shares one market: prices scanned on any of them, investments, auction mail."""
    return SHARED


def share_markets(db: sqlite3.Connection):
    """Fold what earlier versions kept per realm and faction into the one shared market. Where two
    held the same item on the same day, the row already shared is kept."""
    for table, keyed in (("price", True), ("handed", True), ("depth", True), ("trade", False), ("mail", False)):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone():
            continue
        if not db.execute(f"SELECT 1 FROM {table} WHERE market != ? LIMIT 1", (SHARED,)).fetchone():
            continue
        with db:
            db.execute(f"UPDATE {'OR IGNORE ' if keyed else ''}{table} SET market = ? WHERE market != ?", (SHARED, SHARED))
            db.execute(f"DELETE FROM {table} WHERE market != ?", (SHARED,))


def _whole(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def date_of(day: int) -> str:
    return (datetime.date(1970, 1, 1) + datetime.timedelta(days=day)).isoformat()


def _quantile(values: list, q: float) -> float:
    ordered = sorted(values)
    at = (len(ordered) - 1) * q
    low = int(at)
    return ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (at - low)


def _slope(points: list[tuple[int, float]]) -> float:
    """Copper per day, as the median of the slopes between every two days: one
    freak price does not bend it (Theil-Sen)."""
    slopes = [(b[1] - a[1]) / (b[0] - a[0]) for i, a in enumerate(points) for b in points[i + 1:] if b[0] != a[0]]
    return statistics.median(slopes) if slopes else 0.0


def summarise(rows: list[tuple[int, int, int, int | None]], today: int) -> dict:
    """What a series of (day, low, high, listed) says, as numbers in copper."""
    lows = [low for _, low, _, _ in rows]
    listed = [n for _, _, _, n in rows if n is not None]
    usual = statistics.median(lows)
    out = {"days": len(rows), "now": lows[-1], "seen_days_ago": today - rows[-1][0], "usual": usual,
           "lowest": min(rows, key=lambda r: r[1]), "highest": max(rows, key=lambda r: r[1]),
           "listed": statistics.median(listed) if listed else None,
           "against_usual": (lows[-1] - usual) / usual if usual else 0.0}
    recent = rows[-14:]
    out["slope"] = _slope([(day, low) for day, low, _, _ in recent]) if len(recent) >= 4 else None
    return out


def forecast(rows: list, summary: dict) -> dict | None:
    """Where the price is likely to be over the next week: the recent level moved
    along the recent direction, with the spread the price has shown. A plain
    statistical estimate from the player's own scans, and it says how much data
    is behind it."""
    # ponytail: level + robust trend + spread; a proper model once there are months of daily scans
    if len(rows) < 5:
        return None
    recent = [low for _, low, _, _ in rows[-14:]]
    level = statistics.median(recent[-7:])
    drift = (summary["slope"] or 0.0) * 3.5   # the middle of the coming week
    drift = max(-0.4 * level, min(0.4 * level, drift))   # a trend from a few points is not carried far
    low, high = _quantile(recent, 0.2) + drift, _quantile(recent, 0.8) + drift
    span = rows[-1][0] - rows[0][0] + 1
    return {"expected": max(1, level + drift), "low": max(1, low), "high": max(1, high),
            "based_on": t("{n} days of scans over {span} days", n=len(rows), span=span),
            "confidence": t("weak" if len(rows) < 10 else "moderate" if len(rows) < 30 else "fair")}


class Market:
    def __init__(self, file: Path):
        self.file = file

    def _open(self) -> sqlite3.Connection:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.file, timeout=10)   # one connection per use: the app answers from several threads
        db.executescript(SCHEMA)
        share_markets(db)
        return db

    def ingest(self, snapshot: dict) -> int:
        """File what this snapshot knows about prices. Returns the rows written.
        Handing over the same snapshot twice writes nothing."""
        taken = snapshot.get("takenAt")
        prices, record = snapshot.get("prices"), snapshot.get("market")
        if not isinstance(taken, (int, float)) or not isinstance(prices, list) or not prices:
            return 0
        market, taken, written = market_key(snapshot), int(taken), 0
        record = record if isinstance(record, dict) else {}
        day0, their_today = record.get("day0"), record.get("today")
        dated = _whole(day0, 0, 2**40) and _whole(their_today, 0, 10**6)

        def our_day(theirs: int) -> int:   # the addon counts days from its own day 0, in the player's time zone
            return (day0 + theirs * 86400 + 43200) // 86400

        db = self._open()
        try:
            with db:
                if db.execute("SELECT 1 FROM handed WHERE market = ? AND taken = ?", (market, taken)).fetchone():
                    return 0
                db.execute("INSERT INTO handed VALUES (?, ?)", (market, taken))
                names, current = [], []
                for row in prices:   # [item id, copper, days since seen or -1, name]
                    if not (isinstance(row, list) and len(row) >= 4 and _whole(row[0], 1, 10**9) and _whole(row[1], 1, 10**11)):
                        continue
                    if isinstance(row[3], str) and row[3].strip():
                        names.append((row[0], row[3].strip()[:80]))
                    if _whole(row[2], 0, 10000):
                        day = our_day(their_today - row[2]) if dated else taken // 86400 - row[2]
                        current.append((market, row[0], day, row[1], row[1], None))
                db.executemany("INSERT INTO item VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name = excluded.name", names)
                days = []
                for entry in record.get("days") if dated and isinstance(record.get("days"), list) else []:
                    rows = entry.get("rows") if isinstance(entry, dict) else None
                    if not (_whole(entry.get("day") if isinstance(entry, dict) else None, 0, 10**6) and isinstance(rows, list)):
                        continue
                    day = our_day(entry["day"])
                    for i in range(0, len(rows) - 3, 4):
                        item, low, high, listed = rows[i:i + 4]
                        if _whole(item, 1, 10**9) and _whole(low, 1, 10**11) and _whole(high, 1, 10**11):
                            days.append((market, item, day, min(low, high), max(low, high), listed if _whole(listed, 0, 10**9) else None))
                # the day-by-day record is the fuller one and replaces; the current price only fills a day with no record
                written += db.executemany("INSERT OR REPLACE INTO price VALUES (?, ?, ?, ?, ?, ?)", days).rowcount
                written += db.executemany("INSERT OR IGNORE INTO price VALUES (?, ?, ?, ?, ?, ?)", current).rowcount
                # how deep each market was: the ladder of prices, where a scan or a search showed it
                ladders = []
                for row in snapshot.get("listings") if isinstance(snapshot.get("listings"), list) else []:
                    if not (isinstance(row, list) and len(row) >= 8 and len(row) % 2 == 0 and all(_whole(v, 0, 10**12) for v in row)):
                        continue
                    steps = [[row[i], row[i + 1]] for i in range(6, len(row), 2)]
                    if row[0] and all(a[0] < b[0] and a[1] < b[1] for a, b in zip(steps, steps[1:])) and steps[0][0] > 0:
                        ladders.append((market, row[0], row[1] // 86400, row[1], row[2], row[3], row[4], row[5], json.dumps(steps)))
                db.executemany("INSERT OR REPLACE INTO depth VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", ladders)
        finally:
            db.close()
        return written

    # -- reading --------------------------------------------------------------

    def today(self, snapshot: dict) -> int:
        record = snapshot.get("market") if isinstance(snapshot.get("market"), dict) else {}
        if _whole(record.get("day0"), 0, 2**40) and _whole(record.get("today"), 0, 10**6):
            return (record["day0"] + record["today"] * 86400 + 43200) // 86400
        return int(snapshot.get("takenAt", 0)) // 86400

    def find(self, query: str) -> list[tuple[int, str]]:
        """Items by name: the exact one, else those containing the words, shortest name first."""
        query = query.strip().lower()
        if not query:
            return []
        db = self._open()
        try:
            exact = db.execute("SELECT id, name FROM item WHERE lower(name) = ?", (query,)).fetchall()
            if exact:
                return exact[:1]
            like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            return db.execute("SELECT id, name FROM item WHERE lower(name) LIKE ? ESCAPE '\\' "
                              "ORDER BY length(name), name LIMIT 8", (like,)).fetchall()
        finally:
            db.close()

    def series(self, snapshot: dict, item: int, days: int) -> list[tuple[int, int, int, int | None]]:
        today = self.today(snapshot)
        db = self._open()
        try:
            return db.execute("SELECT day, low, high, listed FROM price WHERE market = ? AND item = ? AND day > ? AND day <= ? "
                              "ORDER BY day", (market_key(snapshot), item, today - days, today)).fetchall()
        finally:
            db.close()

    def depth(self, snapshot: dict, item: int) -> dict | None:
        """The latest ladder of an item: {"at", "auctions", "mine", "mine_at", "partial", "steps": [[price, listed up to it], ...]}."""
        db = self._open()
        try:
            row = db.execute("SELECT at, auctions, mine, mine_at, partial, steps FROM depth WHERE market = ? AND item = ? "
                             "ORDER BY at DESC LIMIT 1", (market_key(snapshot), item)).fetchone()
        finally:
            db.close()
        if row is None:
            return None
        return {"at": row[0], "auctions": row[1], "mine": row[2], "mine_at": row[3], "partial": bool(row[4]), "steps": json.loads(row[5])}

    def size(self, snapshot: dict) -> dict:
        db = self._open()
        try:
            rows, items, first, last = db.execute("SELECT count(*), count(DISTINCT item), min(day), max(day) FROM price "
                                                  "WHERE market = ?", (market_key(snapshot),)).fetchone()
        finally:
            db.close()
        return {"rows": rows, "items": items, "first_day": date_of(first) if first is not None else None,
                "last_day": date_of(last) if last is not None else None,
                "days_covered": last - first + 1 if first is not None else 0}

    def query(self, snapshot: dict, sql: str, limit: int = 200) -> dict:
        """Run one read-only SELECT the model wrote. The file is opened read-only,
        an authorizer refuses everything but reading, and a query that runs long
        is stopped. `p` is the shared market's prices with names and dates."""
        self._open().close()   # make sure the file and its tables exist
        db = sqlite3.connect(f"file:{self.file.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            db.create_function("coins", 1, lambda copper: coins(copper) if isinstance(copper, (int, float)) else None)
            today, market = self.today(snapshot), market_key(snapshot).replace("'", "''")
            db.execute(f"CREATE TEMP VIEW p AS SELECT price.item, item.name, date(price.day * 86400, 'unixepoch') AS date, "
                       f"{today} - price.day AS days_ago, price.low, price.high, price.listed "
                       f"FROM price JOIN item ON item.id = price.item WHERE price.market = '{market}'")
            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
            db.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
            steps = [0]

            def tick():   # called every 100000 steps of the query: about two seconds' worth, then stop
                steps[0] += 1
                return steps[0] > 200
            db.set_progress_handler(tick, 100000)
            cursor = db.execute(sql)
            rows = cursor.fetchmany(limit + 1)
            return {"columns": [c[0] for c in cursor.description or []], "rows": [list(r) for r in rows[:limit]],
                    **({"cut_off_at": limit} if len(rows) > limit else {})}
        except sqlite3.Error as e:
            return {"error": f"the query failed: {str(e)[:200]}"}
        finally:
            db.close()

    def history(self, snapshot: dict, item: int, name: str, days: int) -> dict:
        """One item over time, ready for the model: the series, what it shows, a forecast."""
        rows, today = self.series(snapshot, item, days), self.today(snapshot)
        if not rows:
            return {"item": name, "note": "no scans of this item in that period"}
        s = summarise(rows, today)
        out = {"item": name, "days_with_a_scan": s["days"], "now": coins(s["now"]),
               "last_seen": "today" if not s["seen_days_ago"] else f"{s['seen_days_ago']} days ago",
               "usual_price": coins(s["usual"]), "now_against_usual": f"{s['against_usual']:+.0%}",
               "lowest": f"{coins(s['lowest'][1])} on {date_of(s['lowest'][0])}",
               "highest": f"{coins(s['highest'][1])} on {date_of(s['highest'][0])}"}
        if s["listed"] is not None:
            out["usually_listed"] = int(s["listed"])
        if s["slope"] is not None and s["usual"]:
            per_week = s["slope"] * 7 / s["usual"]
            out["direction"] = "steady" if abs(per_week) < 0.03 else f"{'rising' if per_week > 0 else 'falling'} about {abs(per_week):.0%} a week"
        by_weekday = {}
        for day, low, _, _ in rows:
            by_weekday.setdefault(datetime.date.fromordinal(day + 719163).weekday(), []).append(low)
        if len(rows) >= 14 and len(by_weekday) >= 5:   # only worth saying once every weekday has been seen a few times
            best = min(by_weekday, key=lambda d: statistics.median(by_weekday[d]))
            worst = max(by_weekday, key=lambda d: statistics.median(by_weekday[d]))
            out["cheapest_weekday"] = f"{WEEKDAYS[best]} ({coins(statistics.median(by_weekday[best]))})"
            out["dearest_weekday"] = f"{WEEKDAYS[worst]} ({coins(statistics.median(by_weekday[worst]))})"
        guess = forecast(rows, s)
        if guess:
            out["next_week_estimate"] = {"around": coins(guess["expected"]), "between": f"{coins(guess['low'])} and {coins(guess['high'])}",
                                         "based_on": guess["based_on"], "confidence": guess["confidence"]}
        shown = rows if len(rows) <= 45 else rows[-45:]
        out["series"] = [{"date": date_of(day), "low": coins(low), **({"high": coins(high)} if high != low else {}),
                          **({"listed": listed} if listed is not None else {})} for day, low, high, listed in shown]
        if len(rows) > 45:
            out["series_note"] = f"the last 45 of {len(rows)} days are listed; the figures above use all of them"
        # the same thing for the player's eyes: a chart card, drawn by the window from these numbers
        out["_chart"] = {
            "title": name, "sub": t("last {days} days · {scans}", days=days, scans=tn(len(rows), "{n} scan day", "{n} scan days")),
            "points": [list(row) for row in rows[-180:]], "usual": round(s["usual"]),
            "estimate": {k: round(guess[k]) for k in ("low", "high", "expected")} if guess else None,
            "stats": [[t("Now"), out["now"]], [t("Usual"), out["usual_price"]], [t("Against usual"), out["now_against_usual"]]]
                     + ([[t("Next week"), f"{coins(guess['low'])} – {coins(guess['high'])}"]] if guess else []),
            "notes": [t("Lowest listing on each day you scanned, from your own Auctionator scans. A listing is not a sale.")
                      + (t(" The estimate rests on {based}: confidence {confidence}.", based=guess["based_on"], confidence=guess["confidence"])
                         if guess else "")]}
        return out

    def scan(self, snapshot: dict, kind: str, days: int, limit: int, name: str = "", only: set | None = None,
             min_price: int = 100) -> list[dict]:
        """The whole market ranked one way: the items whose price now stands out."""
        today, market = self.today(snapshot), market_key(snapshot)
        db = self._open()
        try:
            # ponytail: every row of the period is read and grouped here; fine for a few hundred thousand rows,
            # SQL window functions when a market grows past that
            rows = db.execute("SELECT p.item, i.name, p.day, p.low, p.high, p.listed FROM price p JOIN item i ON i.id = p.item "
                              "WHERE p.market = ? AND p.day > ? AND p.day <= ? ORDER BY p.item, p.day",
                              (market, today - days, today)).fetchall()
        finally:
            db.close()
        series, names = {}, {}
        for item, item_name, day, low, high, listed in rows:
            series.setdefault(item, []).append((day, low, high, listed))
            names[item] = item_name
        found = []
        for item, points in series.items():
            if only is not None and item not in only:
                continue
            if name and name.lower() not in names[item].lower():
                continue
            if len(points) < 4 or today - points[-1][0] > 3:   # too little to judge, or not seen lately
                continue
            s = summarise(points, today)
            if s["usual"] < min_price:
                continue
            lows = [p[1] for p in points]
            spread = (_quantile(lows, 0.8) - _quantile(lows, 0.2)) / s["usual"]
            weekly = (s["slope"] or 0.0) * 7 / s["usual"]
            score = {"below_usual": -s["against_usual"], "above_usual": s["against_usual"], "rising": weekly,
                     "falling": -weekly, "swings": spread, "most_listed": s["listed"] or 0}[kind]
            if score <= (0.1 if kind in ("below_usual", "above_usual", "swings") else 0.03 if kind in ("rising", "falling") else 0):
                continue
            found.append((score, {"item": names[item], "now": coins(s["now"]), "usual": coins(s["usual"]),
                                  "now_against_usual": f"{s['against_usual']:+.0%}", "a_week_moves_it": f"{weekly:+.0%}",
                                  "swings_within": f"{spread:.0%}", "days_with_a_scan": s["days"],
                                  **({"usually_listed": int(s["listed"])} if s["listed"] is not None else {})}))
        found.sort(key=lambda f: -f[0])
        return [entry for _, entry in found[:limit]]
