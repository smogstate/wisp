"""Investments: things bought to sell later at a better price.

The player says what they bought, how many and for how much; Wisp keeps every
buy and sell as a trade and works out the rest: what is still held, what it
cost on average, what it is worth at today's listings, what has been made on
what was sold and what would be made on the rest. All of it in code.

Kept for good in Wisp's data file, beside the price history, per market (realm
and faction):

    trade(id, market, item, name, kind, qty, each, day, at, assumed, note)

`each` is copper for one. `assumed` marks a price the player did not give, where
the day's lowest listing was taken instead.
"""
import re
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trade(id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT, item INTEGER, name TEXT, kind TEXT,
                                 qty INTEGER, each INTEGER, day INTEGER, at INTEGER, assumed INTEGER, note TEXT);
"""
UNITS = {"g": 10000, "gold": 10000, "s": 100, "silver": 100, "c": 1, "copper": 1}


def parse_money(text) -> int | None:
    """'3s 50c', '1g', '2 gold 5 silver', '1.5g' -> copper. None when it is not money with its units:
    a bare number could be gold or copper, and guessing would be a hundredfold mistake."""
    if not isinstance(text, str):
        return None
    amount = r"(\d+(?:\.\d+)?)\s*(gold|silver|copper|g|s|c)\b"
    parts = re.findall(amount, text.lower())
    if not parts or re.sub(amount + r"|\s|\band\b", "", text.lower()):   # anything left over, a thousands comma included: not sure enough
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
        return db

    def add(self, market: str, item: int, name: str, kind: str, qty: int, each: int, day: int, assumed: bool, note: str) -> int:
        db = self._open()
        try:
            with db:
                return db.execute("INSERT INTO trade(market, item, name, kind, qty, each, day, at, assumed, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                  (market, item, name, kind, qty, each, day, int(time.time()), int(assumed), note)).lastrowid
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
