"""Investments: things bought to sell later at a better price.

The player says what they bought, how many and for how much; Wisp keeps every
buy and sell as a trade and works out the rest: what is still held, what it
cost on average, what it is worth at today's listings, what has been made on
what was sold and what would be made on the rest. All of it in code.

Kept for good in Wisp's data file, beside the price history, in the one market
every character shares:

    trade(id, market, item, name, kind, qty, each, day, at, assumed, note)

`each` is copper for one. `assumed` marks a price the player did not give, where
the day's lowest listing was taken instead. `mail` names the auction mail a trade
was recorded from, so that no mail is recorded twice.

The same file keeps what the auction house's mail said was really bought and sold:

    mail(who, id, market, kind, item, name, count, n, bid, deposit, cut, sent)

One row per auction, or per `n` auctions that said exactly the same and arrived
together. `bid` is what one of them went for, `cut` what the house kept of it.
"""
import re
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trade(id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT, item INTEGER, name TEXT, kind TEXT,
                                 qty INTEGER, each INTEGER, day INTEGER, at INTEGER, assumed INTEGER, note TEXT, mail TEXT);
CREATE TABLE IF NOT EXISTS mail(who TEXT, id INTEGER, market TEXT, kind TEXT, item INTEGER, name TEXT, count INTEGER, n INTEGER,
                                bid INTEGER, deposit INTEGER, cut INTEGER, sent INTEGER, PRIMARY KEY (who, id)) WITHOUT ROWID;
"""
UNITS = {"g": 10000, "gold": 10000, "s": 100, "silver": 100, "c": 1, "copper": 1,
         # as a Russian player writes them: 3с 50м, 2 золотых 5 серебра
         "з": 10000, "зол": 10000, "золото": 10000, "золота": 10000, "золотых": 10000, "золотой": 10000,
         "с": 100, "сер": 100, "серебро": 100, "серебра": 100, "серебряных": 100,
         "м": 1, "мед": 1, "медь": 1, "меди": 1, "медных": 1}


def parse_money(text) -> int | None:
    """'3s 50c', '1g', '2 gold 5 silver', '1.5g' -> copper. None when it is not money with its units:
    a bare number could be gold or copper, and guessing would be a hundredfold mistake."""
    if not isinstance(text, str):
        return None
    amount = r"(\d+(?:\.\d+)?)\s*(" + "|".join(sorted(UNITS, key=len, reverse=True)) + r")\b\.?"
    parts = re.findall(amount, text.lower())
    if not parts or re.sub(amount + r"|\s|\band\b|\bи\b", "", text.lower()):   # anything left over, a thousands comma included: not sure enough
        return None
    total = round(sum(float(number) * UNITS[unit] for number, unit in parts))
    return total if 0 < total <= 10**11 else None


class Assets:
    def __init__(self, file: Path):
        self.file = file

    def _open(self) -> sqlite3.Connection:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.file, timeout=10)
        db.executescript(SCHEMA)
        if "mail" not in [row[1] for row in db.execute("PRAGMA table_info(trade)")]:   # a file from before trades knew their mail
            db.execute("ALTER TABLE trade ADD COLUMN mail TEXT")
        from .market import share_markets
        share_markets(db)
        return db

    def add(self, market: str, item: int, name: str, kind: str, qty: int, each: int, day: int, assumed: bool, note: str,
            mail: str | None = None) -> int:
        db = self._open()
        try:
            with db:
                return db.execute("INSERT INTO trade(market, item, name, kind, qty, each, day, at, assumed, note, mail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                  (market, item, name, kind, qty, each, day, int(time.time()), int(assumed), note, mail)).lastrowid
        finally:
            db.close()

    def remove(self, market: str, trade: int | None = None, item: int | None = None) -> int:
        db = self._open()
        try:
            with db:
                if trade is not None:
                    return db.execute("DELETE FROM trade WHERE market = ? AND id = ?", (market, trade)).rowcount
                return db.execute("DELETE FROM trade WHERE market = ? AND item = ?", (market, item)).rowcount
        finally:
            db.close()

    def trades(self, market: str) -> list[dict]:
        db = self._open()
        try:
            rows = db.execute("SELECT id, item, name, kind, qty, each, day, assumed, note FROM trade WHERE market = ? ORDER BY day, id",
                              (market,)).fetchall()
        finally:
            db.close()
        return [dict(zip(("id", "item", "name", "kind", "qty", "each", "day", "assumed", "note"), row)) for row in rows]

    # -- what the auction house's mail said ------------------------------------

    def ingest_mail(self, market: str, who: str, notes) -> int:
        """File the addon's notes of auction mails. A note seen again replaces itself: its count can only have grown."""
        rows = []
        for m in notes if isinstance(notes, list) else []:
            if not isinstance(m, dict) or m.get("kind") not in ("buy", "sell") or not isinstance(m.get("name"), str):
                continue
            number = {k: m.get(k) if isinstance(m.get(k), int) and not isinstance(m.get(k), bool) else None
                      for k in ("id", "item", "count", "n", "bid", "deposit", "cut", "sent")}
            if number["id"] is None or not number["bid"] or number["bid"] < 0 or not number["sent"]:
                continue
            rows.append((who, number["id"], market, m["kind"], number["item"], m["name"].strip()[:80], max(1, number["count"] or 1),
                         max(1, number["n"] or 1), number["bid"], max(0, number["deposit"] or 0), max(0, number["cut"] or 0), number["sent"]))
        if not rows:
            return 0
        db = self._open()
        try:
            with db:
                return db.executemany("INSERT OR REPLACE INTO mail VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows).rowcount
        finally:
            db.close()

    def deals(self, market: str, since: int = 0) -> list[dict]:
        """Auctions bought and sold, newest first, each with the trade it was recorded as, if any."""
        db = self._open()
        try:
            rows = db.execute("SELECT m.who, m.id, m.kind, m.item, m.name, m.count, m.n, m.bid, m.deposit, m.cut, m.sent, "
                              "(SELECT t.id FROM trade t WHERE t.mail = m.who || '#' || m.id) FROM mail m "
                              "WHERE m.market = ? AND m.sent >= ? ORDER BY m.sent DESC, m.id DESC", (market, since)).fetchall()
        finally:
            db.close()
        keys = ("who", "id", "kind", "item", "name", "count", "n", "bid", "deposit", "cut", "sent", "trade")
        return [dict(zip(keys, row)) for row in rows]

    def positions(self, market: str) -> dict[int, dict]:
        """Per item, from its trades in order: how many are held, what they cost on average, and
        what was made on those sold. A sale is set against the average cost of what was held then."""
        held = {}
        for t in self.trades(market):
            p = held.setdefault(t["item"], {"item": t["item"], "name": t["name"], "held": 0, "average": 0.0, "realised": 0.0,
                                            "bought": 0, "sold": 0, "spent": 0, "received": 0, "since": t["day"], "trades": []})
            if t["kind"] == "buy":
                p["average"] = (p["average"] * p["held"] + t["each"] * t["qty"]) / (p["held"] + t["qty"])
                p["held"] += t["qty"]
                p["bought"] += t["qty"]
                p["spent"] += t["each"] * t["qty"]
            else:
                p["realised"] += t["qty"] * (t["each"] - p["average"])
                p["held"] -= t["qty"]
                p["sold"] += t["qty"]
                p["received"] += t["each"] * t["qty"]
            p["trades"].append(t)
        return held
