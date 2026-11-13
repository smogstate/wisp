"""The character as a sim site reads it.

The sims for this game (wowsims.com/forever and the sites built on it) import the text that the
WoWSims exporter addon writes. Wisp writes the same text from what its own addon saw: class, race,
level, professions, what is worn, the talents. The player copies it and pastes it into the site.
Wisp sends nothing anywhere, and the character's name and realm are not in the text.

Talents are the one part that needs the sim: it reads them as digits in the order of its own trees.
That order comes from the sim's public source, one small file for the class, kept for a day.
"""
import json
import time
import urllib.request
from pathlib import Path

from .i18n import t
from .questdb import PROFESSIONS

SITE = "https://www.wowsims.com/forever/"
TREES = "https://raw.githubusercontent.com/wowsims/forever/master/ui/sim/talents/trees/{cls}.json"
RELEASE = "https://api.github.com/repos/wowsims/exporter/releases/latest"
FORMAT = "v3.2.7"   # the exporter release whose text this follows; the site only warns when it is not the latest
DAY = 86400

# the slots in the order the sim lists them, as the addon numbers them
SLOTS = ("Head", "Neck", "Shoulders", "Back", "Chest", "Wrists", "Hands", "Waist", "Legs", "Feet",
         "Ring 1", "Ring 2", "Trinket 1", "Trinket 2", "Main hand", "Off hand", "Ranged")
CLASSES = ("druid", "hunter", "mage", "paladin", "priest", "rogue", "shaman", "warlock", "warrior")
RACES = {"human", "dwarf", "gnome", "nightelf", "orc", "tauren", "troll", "undead", "bloodelf",
         "highorderskyborne", "windshaperskyborne"}
TRADES = {"Alchemy", "Blacksmithing", "Enchanting", "Engineering", "Herbalism", "Leatherworking", "Mining", "Skinning", "Tailoring"}
# the sim's page for the tree with the most points in it
PAGES = {"druid": ("balance", "feralcat", "restoration"), "hunter": ("dps",) * 3, "mage": ("dps",) * 3,
         "paladin": ("holy", "protection", "retribution"), "priest": ("healer", "healer", "dps"), "rogue": ("dps",) * 3,
         "shaman": ("elemental", "enhancement", "restoration"), "warlock": ("dps",) * 3, "warrior": ("dps", "dps", "protection")}
THICK_HIDE, SHREDDING_ATTACKS = 134403, 134406   # a druid with the first and without the second is the sim's bear


def _get(url: str):
    ask = urllib.request.Request(url, headers={"User-Agent": "Wisp", "Accept": "application/json"})
    with urllib.request.urlopen(ask, timeout=10) as reply:
        return json.loads(reply.read(2_000_000))


def kept(url: str, file: Path | None, get=_get):
    """A public file, asked for at most once a day; yesterday's copy when the network is away. No folder: nothing is asked."""
    if file is None:
        return None
    def old():
        try:
            return json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    try:
        if time.time() - file.stat().st_mtime < DAY and old() is not None:
            return old()
    except OSError:
        pass
    try:
        fresh = get(url)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(fresh), encoding="utf-8")
        return fresh
    except Exception:   # no network, a busy server, not JSON: the old copy, or none
        return old()


def trees(cls: str, folder: Path | None, get=_get) -> list | None:
    """The class's three trees as the sim lists them: [{name, talents: [(the game's number, name, most points)]}]."""
    if cls not in CLASSES or folder is None:
        return None
    raw = kept(TREES.format(cls=cls), folder / f"trees-{cls}.json", get)
    try:
        out = [{"name": str(tree["name"]), "talents": [(int(x["definitionId"]), str(x.get("fancyName") or ""), int(x.get("maxPoints") or 5))
                                                       for x in tree["talents"]]} for tree in raw]
    except (TypeError, KeyError, ValueError):
        return None
    return out if len(out) == 3 else None


def version(folder: Path | None, get=_get) -> str:
    got = kept(RELEASE, folder / "exporter.json" if folder else None, get)
    tag = got.get("tag_name") if isinstance(got, dict) else None
    return tag if isinstance(tag, str) and 0 < len(tag) < 20 and tag.isascii() and tag.isprintable() else FORMAT


class Build:
    """What the addon saw of the character's gear and talents, and the text a sim imports."""

    def __init__(self, snapshot: dict, order: list | None, version: str = FORMAT):
        me = snapshot.get("character") if isinstance(snapshot.get("character"), dict) else {}
        self.cls = str(me.get("class") or "").lower()
        self.level = me.get("level") if isinstance(me.get("level"), int) else None
        race = str(me.get("raceFile") or "").replace("Scourge", "Undead")
        self.race = race if race.lower().replace(" ", "") in RACES else None
        self.version = version
        self.read = isinstance(snapshot.get("gear"), (list, dict))   # an addon from before this has neither
        self.gear = {}   # place in SLOTS -> {id, name, enchant, suffix}
        for item in snapshot.get("gear") if isinstance(snapshot.get("gear"), list) else []:
            whole = lambda v: isinstance(v, int) and not isinstance(v, bool)
            if isinstance(item, dict) and whole(item.get("slot")) and 1 <= item["slot"] <= len(SLOTS) and whole(item.get("id")) and item["id"] > 0:
                self.gear[item["slot"]] = {"id": item["id"], "name": str(item.get("name") or f"#{item['id']}")[:80],
                                           **{k: item[k] for k in ("enchant", "suffix") if whole(item.get(k)) and item[k]}}
        self.talents_read = snapshot.get("talentsRead") is True
        self.ranks, self.names = {}, {}
        for row in snapshot.get("talents") if isinstance(snapshot.get("talents"), list) else []:
            if isinstance(row, list) and len(row) >= 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in row[:2]) and 0 < row[1] <= 9:
                self.ranks[row[0]] = row[1]
                if len(row) > 2 and isinstance(row[2], str):
                    self.names[row[0]] = row[2][:60]
        self.order = order
        self.trades = []
        english = {name.lower(): names[0] for names in PROFESSIONS.values() for name in names}
        for trade in snapshot.get("professions") if isinstance(snapshot.get("professions"), list) else []:
            name = english.get(str(trade.get("name") or "").lower()) if isinstance(trade, dict) else None
            if name in TRADES and isinstance(trade.get("skill"), int):
                self.trades.append({"name": name, "level": trade["skill"]})

    def points(self) -> list | None:
        """Points in each of the three trees, when the sim's order is at hand."""
        if not self.order:
            return None
        return [sum(self.ranks.get(number, 0) for number, _, _ in tree["talents"]) for tree in self.order]

    def unknown(self) -> int:
        """Talents the character has that the sim's trees do not list: the sim is behind the game, or ahead of it."""
        listed = {number for tree in self.order or [] for number, _, _ in tree["talents"]}
        return len([number for number in self.ranks if number not in listed]) if self.order else 0

    def talent_string(self) -> str | None:
        """As the sim writes it: a digit a talent, tree by tree, '0500032-05-3', empty ends cut off."""
        if not self.order or not self.talents_read:
            return None
        each = ["".join(str(self.ranks.get(number, 0)) for number, _, _ in tree["talents"]).rstrip("0") for tree in self.order]
        return "-".join(each).rstrip("-")

    def page(self) -> str:
        """The sim's page for this class and build; the front page when the build does not say."""
        points = self.points()
        if self.cls not in PAGES or not points or not sum(points):
            return SITE
        page = PAGES[self.cls][points.index(max(points))]
        if page == "feralcat" and self.ranks.get(THICK_HIDE) and not self.ranks.get(SHREDDING_ATTACKS):
            page = "feralbear"
        return f"{SITE}{self.cls}/{page}/"

    def export(self) -> dict:
        """The exporter addon's own layout, without the name, the realm and the character's id."""
        out = {"version": self.version, "flavor": "forever", "class": self.cls, "level": self.level}
        if self.race:
            out["race"] = self.race
        talents = self.talent_string()
        if talents:
            out["talents"] = talents
        if self.trades:
            out["professions"] = self.trades
        if self.gear:
            item = lambda g: {"id": g["id"], **({"enchant": g["enchant"]} if "enchant" in g else {}),
                              **({"random_suffix": g["suffix"]} if "suffix" in g else {})}
            out["gear"] = {"items": [item(self.gear[place]) if place in self.gear else None for place in range(1, len(SLOTS) + 1)]}
        return out

    def text(self) -> str:
        return json.dumps(self.export(), separators=(",", ":"))

    def taken(self) -> list:
        """The talents with points in them, for the model: by the game's own name when it gave one, the sim's otherwise."""
        if self.order:
            return [{"tree": tree["name"], "talent": self.names.get(number) or name or f"#{number}", "points": self.ranks[number], "of": most}
                    for tree in self.order for number, name, most in tree["talents"] if number in self.ranks]
        return [{"talent": self.names.get(number) or f"#{number}", "points": rank} for number, rank in sorted(self.ranks.items())]

    def card(self) -> dict:
        points = self.points()
        build = (t("talents {build}", build="/".join(str(n) for n in points)) if points
                 else t("{n} talent points", n=sum(self.ranks.values())) if self.ranks else None)
        # the window is narrow: the name, which may wrap, goes first
        rows = [[g["name"], t(SLOTS[place - 1]), t("yes") if "enchant" in g else "–"] for place, g in sorted(self.gear.items())]
        notes = [t("The text holds class, race, level, professions, gear and talents; not the character's name or realm.")]
        if self.talent_string() is None:
            notes.append(t("Talents are not in the text: the site keeps the ones set there."))
        elif self.unknown():
            notes.append(t("{n} of the talents are not in the sim's trees and were left out.", n=self.unknown()))
        return {"title": t("What the character wears"), "sub": " · ".join(str(x) for x in (t("level {n}", n=self.level) if self.level else None, build) if x),
                "columns": [t("Item"), t("Slot"), t("Enchanted")], "rows": rows, "notes": notes}
