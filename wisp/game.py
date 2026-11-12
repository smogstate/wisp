"""What the overlay knows about the game: the addon's snapshot, the tools the
model can call over it, and the WISP1 messages that go back into the game."""
import base64
import json
import re
import statistics
import time
from pathlib import Path

from .assets import parse_money
from .crafting import Crafting, coins
from .farming import Farming
from .gathering import Gathering
from .graph import ItemGraph
from .history import diff
from .i18n import num, t, tn

SNAPSHOT_VERSION = 1


def find_saved_file(wow_dir: Path) -> Path | None:
    """The newest Wisp.lua under WTF/Account/*/SavedVariables, or None."""
    files = list(wow_dir.glob("WTF/Account/*/SavedVariables/Wisp.lua"))
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def read_snapshot(saved_file: Path) -> dict:
    """The addon stores the snapshot as base64 of JSON precisely so that this
    needs no Lua parsing. Raises ValueError with a readable reason."""
    try:
        text = saved_file.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise ValueError(f"cannot read {saved_file.name}: {e.strerror}") from e
    m = re.search(r'^WispSnapshot = "([A-Za-z0-9+/=]+)"', text, re.M)
    if not m:
        raise ValueError("the addon has not saved a snapshot yet; log out of the game once")
    try:
        snapshot = json.loads(base64.b64decode(m.group(1)).decode("utf-8", errors="replace"))
    except ValueError as e:
        raise ValueError("the saved snapshot is damaged") from e
    if not isinstance(snapshot, dict) or snapshot.get("v") != SNAPSHOT_VERSION:
        raise ValueError("the snapshot was written by a different version of the addon")
    return snapshot


def encode_wisp1(cmds: list[dict]) -> str:
    """The message the player pastes into the addon's inbox."""
    payload = json.dumps({"cmds": cmds}, ensure_ascii=False, separators=(",", ":"))
    return "WISP1:" + base64.b64encode(payload.encode("utf-8")).decode("ascii")


# WoW's inline codes: colours, textures, links, line breaks
_WOW_CODES = re.compile(r"\|c[0-9a-fA-F]{8}|\|r|\|T[^|]*\|t|\|H[^|]*\|h|\|h|\|n")


def _clean(text, limit: int) -> str:
    """Game text is shown in the UI and sent back into the game: drop WoW's
    inline codes, any stray `|` and control characters, and cap the length."""
    text = _WOW_CODES.sub("", str(text or ""))
    return re.sub(r"[\x00-\x1f\x7f|]", "", text)[:limit].strip()


def _as_list(value) -> list:
    # the game's JSON encoder writes an empty table as {} or [], and we accept both
    return value if isinstance(value, list) else []


class Game:
    """One snapshot, plus the tools over it. Rebuilt whenever the file changes."""

    def __init__(self, snapshot: dict | None, problem: str | None = None, names: dict | None = None):
        self.snapshot = snapshot or {}
        # names in the player's language from Blizzard's data, where Wisp has fetched them: {"item": {id: name}, "creature": {id: name}}
        self.local = names or {}
        self.problem = problem            # why there is no snapshot, in words
        self.places: dict[str, dict] = {}  # ref -> {map, zone, x, y, title}
        self.quests = [self._quest(q) for q in _as_list(self.snapshot.get("quests")) if isinstance(q, dict)]
        self.dungeons = [self._dungeon(i, d) for i, d in enumerate(_as_list(self.snapshot.get("dungeons")))
                         if isinstance(d, dict)]
        self.available = [self._available(q) for q in _as_list(self.snapshot.get("available")) if isinstance(q, dict)]
        self.crafting = Crafting(self.snapshot)
        self.gathering = Gathering(self.snapshot)
        self.farming = Farming(self.snapshot, self.crafting, self.gathering)
        self.routes: dict[str, dict] = {}  # ref -> a walk through several places, made by the gathering tool
        self.graph = ItemGraph(self)       # how items tie recipes, quests, bosses, vendors and drops together
        self.cards: list[dict] = []        # action cards made during the current answer
        self.history = None                # the snapshot history, when the app has one
        self.market = None                 # the auction prices kept over time, when the app has them
        self.assets = None                 # the player's investments, when the app has a place for them
        self.live = None                   # the auction house as Blizzard publishes it, when the app can reach it
        self.recipes_read = False          # whether the addon has read every recipe in the game
        self.web_seen: dict[str, str] = {}  # url -> title of every web page this conversation was given
        self.notes_dir: Path | None = None  # where research reports are kept, when the app has a place
        self.saved_file: Path | None = None  # the addon's saved file: Questie's whole quest list is kept there too
        self._questdb = None

    @classmethod
    def load(cls, wow_dir: Path | None, names: dict | None = None) -> "Game":
        if wow_dir is None:
            return cls(None, t("no game folder was given"))
        saved = find_saved_file(wow_dir)
        if saved is None:
            return cls(None, t("no Wisp snapshot found: enable the Wisp addon, log in, then log out once"))
        try:
            game = cls(read_snapshot(saved), names=names)
        except ValueError as e:
            return cls(None, str(e))
        game.saved_file = saved
        from .questdb import read_index
        game.graph.attach(read_index(saved, "WispItems", "items"))   # Questie's whole item list, when the addon has read it
        every = read_index(saved, "WispRecipes", "recipes")           # every recipe in the game, when the addon has read them
        game.recipes_read = every is not None
        game.graph.add_recipes(game.crafting.add_world(every))
        for item, name in game.graph.names.items():                   # a reagent nobody of the player's uses still has its name
            game.crafting.names.setdefault(item, name)
        return game

    def questdb(self):
        """Every quest the character could ever take, as the addon read them from Questie; None until it has."""
        if self._questdb is None and self.snapshot:
            from .questdb import QuestDB, read_index
            index = read_index(self.saved_file)
            self._questdb = QuestDB(index, self) if index else None
        return self._questdb

    # -- reading the snapshot ------------------------------------------------

    def age_minutes(self) -> int | None:
        taken = self.snapshot.get("takenAt")
        return max(0, int((time.time() - taken) / 60)) if isinstance(taken, (int, float)) else None

    @staticmethod
    def _reward(q: dict) -> dict:
        """What handing a quest in gives, where Questie could say."""
        out = {}
        if isinstance(q.get("xp"), int) and not isinstance(q.get("xp"), bool) and q["xp"] > 0:
            out["xp"] = q["xp"]
        if isinstance(q.get("money"), int) and not isinstance(q.get("money"), bool) and q["money"] > 0:
            out["money"] = coins(q["money"])
        return out

    def _share_of_level(self, xp: int) -> str | None:
        bar = (self.snapshot.get("character") or {}).get("xpMax")
        return t("{share} of a level", share=f"{xp / bar:.0%}") if isinstance(bar, int) and bar > 0 and xp else None

    def colour(self, level) -> str | None:
        """How hard a quest of this level is for the character, as the quest log colours it:
        red, orange, yellow, green, or grey for one that gives almost nothing any more."""
        me = self.snapshot.get("character") or {}
        mine = me.get("level")
        if not isinstance(level, int) or not isinstance(mine, int) or level <= 0:
            return None
        green = me.get("greenRange")
        if not isinstance(green, int):   # the game's own table, for when it did not say
            green = 4 if mine <= 5 else mine // 10 + 5 if mine <= 39 else mine // 5 + 1 if mine <= 59 else 9
        diff = level - mine
        return "red" if diff >= 5 else "orange" if diff >= 3 else "yellow" if diff >= -2 else "green" if -diff <= green else "grey"

    def _place(self, ref: str, place: dict, title: str) -> dict | None:
        """Register a location under a ref. The model only ever handles refs, so
        a coordinate can never be invented or mistyped on its way to the game."""
        try:
            map_id, x, y = int(place["map"]), float(place["x"]), float(place["y"])
        except (KeyError, TypeError, ValueError):
            return None
        if map_id <= 0 or not (0 <= x <= 100 and 0 <= y <= 100):
            return None
        entry = {"map": map_id, "zone": _clean(place.get("zone"), 60), "x": x, "y": y, "title": _clean(title, 60)}
        self.places[ref] = entry
        return {"ref": ref, "zone": entry["zone"], "x": x, "y": y}

    def creature_name(self, some, otherwise):
        """A creature's name in the player's language where Blizzard's is known, else as it came (Questie's, maybe English)."""
        return (self.local.get("creature") or {}).get(some) or otherwise

    def _who(self, prefix: str, people, role: str) -> list[dict]:
        out = []
        for i, person in enumerate(p for p in _as_list(people) if isinstance(p, dict)):
            name = _clean(self.creature_name(person.get("id"), person.get("name")) if person.get("kind") == "npc" else person.get("name"), 60) or "?"
            where = []
            for j, place in enumerate(p for p in _as_list(person.get("places")) if isinstance(p, dict)):
                seen = self._place(f"{prefix}.{i + 1}.{j + 1}", place, f"{name} ({role})")
                if seen:
                    where.append(seen)
            out.append({"name": name, "kind": _clean(person.get("kind"), 10), "where": where})
        return out

    def _quest(self, q: dict) -> dict:
        qid = q.get("id")
        title = _clean(q.get("title"), 80)
        out = {
            "id": qid, "title": title, "level": q.get("level"), "colour": self.colour(q.get("level")),
            "complete": bool(q.get("complete")), **self._reward(q),
            "objectives": [{"text": _clean(o.get("text"), 160), "done": bool(o.get("done"))}
                           for o in _as_list(q.get("objectives")) if isinstance(o, dict)],
            "starts": self._who(f"q{qid}.start", q.get("starts"), t("quest giver")),
            "ends": self._who(f"q{qid}.end", q.get("ends"), t("turn in")),
        }
        way = q.get("gameWaypoint")
        if isinstance(way, dict):
            out["game_pointer"] = self._place(f"q{qid}.pointer", way, t("{title} (next step)", title=title))
        nxt = q.get("next")
        if isinstance(nxt, dict):
            out["next_in_chain"] = {
                "id": nxt.get("id"), "title": _clean(nxt.get("title"), 80), "level": nxt.get("level"),
                "starts": self._who(f"q{qid}.next", nxt.get("starts"), t("next quest")),
            }
        return out

    def _available(self, q: dict) -> dict:
        """A quest the character could pick up, as Questie has it."""
        qid = q.get("id")
        title = _clean(q.get("title"), 80)
        starts = self._who(f"a{qid}.start", q.get("starts"), t("quest giver"))
        return {"id": qid, "title": title, "level": q.get("level") if isinstance(q.get("level"), int) else None,
                "colour": self.colour(q.get("level")), **self._reward(q),
                "requires_level": q.get("requiredLevel") if isinstance(q.get("requiredLevel"), int) else None,
                "kind": q.get("kind") if q.get("kind") in ("elite", "dungeon", "raid", "pvp") else "normal",
                "starts": starts, "zones": list(dict.fromkeys(w["zone"] for s in starts for w in s["where"] if w["zone"])),
                "needs": [_clean(n.get("name"), 80) for n in _as_list(q.get("needs")) if isinstance(n, dict) and _clean(n.get("name"), 80)]}

    # -- dungeons, from the DungeonJournal addon --------------------------------

    def _journal_place(self, ref: str, place, title: str) -> dict | None:
        if not isinstance(place, dict):
            return None
        seen = self._place(ref, place, title)
        return {**seen, "at": _clean(place.get("label"), 80)} if seen else None

    def _dungeon_quest(self, q: dict) -> dict:
        qid, name = q.get("id"), _clean(q.get("name"), 80)
        level = (self.snapshot.get("character") or {}).get("level")
        needs = []
        for step in (n for n in _as_list(q.get("needs")) if isinstance(n, dict)):
            step_name = _clean(step.get("name"), 80)
            needs.append({
                "id": step.get("id"), "name": step_name, "done": step.get("state") == "done",
                "in_log": step.get("state") == "log", "pickup": _clean(step.get("pickup"), 160) or None,
                "where": self._journal_place(f"dq{step.get('id')}.start", step.get("start"), t("{name} (quest start)", name=step_name)),
            })
        missing = [n for n in needs if not n["done"]]
        requires = q.get("requires")
        if q.get("state") == "done":
            status = "done"
        elif q.get("state") == "log":
            status = "in your quest log"
        elif missing:
            status = "locked: an earlier quest is not done"
        elif isinstance(requires, int) and isinstance(level, int) and level < requires:
            status = f"needs level {requires}"
        else:
            status = "available"
        out = {
            "id": qid, "name": name, "status": status, "level": q.get("level"), "requires_level": requires,
            "objective": _clean(q.get("objective"), 240), "pickup": _clean(q.get("pickup"), 200),
            "turn_in": _clean(q.get("turnin"), 200), "rewards": _clean(str(q.get("rewards") or "").replace("\n", "; "), 240),
            "pickup_where": self._journal_place(f"dq{qid}.start", q.get("start"), t("{name} (quest start)", name=name)),
        }
        if q.get("class"):
            out["class_only"] = _clean(q.get("class"), 40)
        if needs:
            out["needs_first"] = needs
            if missing:   # the place to go next is the first step not done, not the start of the chain
                out["next_step"] = missing[0]
        return out

    def _dungeon(self, index: int, d: dict) -> dict:
        name = _clean(d.get("name"), 80)
        bosses = []
        for b in (b for b in _as_list(d.get("bosses")) if isinstance(b, dict)):
            loot = [{"item": _clean(i[1], 80), "slot": _clean(i[2], 40), "quality": QUALITY.get(i[3], "?")}
                    for i in _as_list(b.get("loot")) if isinstance(i, list) and len(i) >= 4]
            bosses.append({"name": _clean(b.get("name"), 80), "rare": bool(b.get("rare")), "loot": loot})
        return {
            "name": name, "level": _clean(d.get("level"), 20), "location": _clean(d.get("location"), 120),
            "entrance": self._journal_place(f"d{index + 1}.entrance", d.get("entrance"), t("{name} entrance", name=name)),
            "entrance_hint": _clean(d.get("entranceHint"), 300) or None,
            "quests": [self._dungeon_quest(q) for q in _as_list(d.get("quests")) if isinstance(q, dict)],
            "bosses": bosses,
        }

    # -- tools ---------------------------------------------------------------

    def tool_character(self, _args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        completed = _as_list(self.snapshot.get("completed"))
        return {
            "character": self.snapshot.get("character"),
            "professions": self.snapshot.get("professions"),
            "client": self.snapshot.get("client"),
            "addons": self.snapshot.get("addons"),
            "quests_in_log": len(self.quests),
            "quests_completed": len(completed),
            "snapshot_age_minutes": self.age_minutes(),
            "note": "This is the state at the player's last logout. Anything done since is not in it.",
        }

    def tool_quests(self, _args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        note = "Locations come from the Questie addon. Use the 'ref' of a location with the waypoint tool."
        if not (self.snapshot.get("addons") or {}).get("questie"):
            note = "Questie was not available when the snapshot was taken, so most locations are missing."
        return {"quests": self.quests, "snapshot_age_minutes": self.age_minutes(), "note": note}

    def tool_available_quests(self, args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        if self.snapshot.get("available") is None:
            return {"error": "The list of quests the character can pick up comes from the Questie addon, and it was not there at "
                             "the last logout" + (": Questie was not running." if not (self.snapshot.get("addons") or {}).get("questie")
                                                  else ". This version of Questie may keep it elsewhere.")}
        me = self.snapshot.get("character") or {}
        kind, zone = args.get("kind", "any"), args.get("zone")
        if kind not in ("any", "normal", "elite", "dungeon", "raid", "pvp"):
            return {"error": "kind must be any, normal, elite, dungeon, raid or pvp"}
        limit = args.get("limit", 30)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 60:
            return {"error": "limit must be a whole number from 1 to 60"}
        found = [q for q in self.available if kind in ("any", q["kind"])]
        note = ("Quests Questie has down as open to this character at the last logout: level, class, race and earlier quests "
                "allow them, and they are not yet picked up. It follows Questie's own settings, so quests far below the "
                "character's level are left out unless Questie is set to show them. 'elite' is the game's tag for a quest "
                "meant for a group. colour is how the quest log would colour it for this character: grey gives almost no "
                "experience, green and yellow are comfortable, orange is hard, red is out of reach; worth_doing counts green, "
                "yellow and orange. Every quest giver's place has a ref for the waypoint tool. For what to do in a zone and "
                "in which order, call questing_route.")
        if isinstance(zone, str) and zone.strip():
            wanted = _clean(zone, 60).lower()
            if wanted in ("here", "this zone", "my zone"):
                wanted = str(me.get("zone") or "").lower()
            zones = sorted({z for q in self.available for z in q["zones"]})
            match = [z for z in zones if z.lower() == wanted] or [z for z in zones if wanted and wanted in z.lower()]
            if len(match) != 1:
                return {"error": f"no single zone with open quests is named like '{_clean(zone, 60)}'" if match or wanted else
                                 "the character's zone is not known", "zones_with_open_quests": zones[:60]}
            found = [q for q in found if match[0] in q["zones"]]
            here = me.get("zone") == match[0] and isinstance(me.get("x"), (int, float)) and isinstance(me.get("y"), (int, float))

            def distance(q):   # from where the character stands, when it is in that zone
                near = [((w["x"] - me["x"]) ** 2 + (w["y"] - me["y"]) ** 2) ** 0.5
                        for s in q["starts"] for w in s["where"] if w["zone"] == match[0]]
                return min(near) if near and here else 0
            found.sort(key=lambda q: (distance(q), q["level"] or 0, q["title"]))
            out = [{k: v for k, v in q.items() if k != "zones" and v not in ([], None)} for q in found[:limit]]
            return {"zone": match[0], "kind": kind, "open_quests": len(found), "quests": out, "character_level": me.get("level"),
                    "ordered": "nearest first" if here else "by level",
                    **({"left_out": len(found) - limit} if len(found) > limit else {}), "note": note}
        if kind == "any":   # no filter: the lie of the land, not three hundred quests
            zones = {}
            for q in found:
                for z in q["zones"] or ["(no place known)"]:
                    counts = zones.setdefault(z, {"zone": z, "open_quests": 0, "worth_doing": 0, "levels": []})
                    counts["open_quests"] += 1
                    if q["colour"] in ("green", "yellow", "orange"):   # not grey, and not out of reach
                        counts["worth_doing"] += 1
                        counts["xp_on_offer"] = counts.get("xp_on_offer", 0) + q.get("xp", 0)
                    if q["level"]:
                        counts["levels"].append(q["level"])
                    if q["kind"] != "normal":
                        counts[q["kind"]] = counts.get(q["kind"], 0) + 1
            for counts in zones.values():
                levels = counts.pop("levels")
                if levels:
                    counts["quest_levels"] = f"{min(levels)}-{max(levels)}" if min(levels) != max(levels) else str(levels[0])
            return {"open_quests": len(found), "by_zone": sorted(zones.values(), key=lambda z: (-z["worth_doing"], -z["open_quests"], z["zone"]))[:60],
                    "character_zone": me.get("zone"), "character_level": me.get("level"),
                    "note": note + " Ask with a zone (or zone 'here') or a kind for the quests themselves."}
        found.sort(key=lambda q: (q["level"] or 0, q["title"]))
        return {"kind": kind, "open_quests": len(found), "character_level": me.get("level"),
                "quests": [{k: v for k, v in q.items() if v not in ([], None)} for q in found[:limit]],
                **({"left_out": len(found) - limit} if len(found) > limit else {}), "note": note}

    def tool_leveling(self, args: dict) -> dict:
        """How fast the character levels, from what the addon measured: experience per hour,
        the time to the next level at that pace, the rested bonus, and day by day."""
        if not self.snapshot:
            return {"error": self.problem}
        me, record = self.snapshot.get("character") or {}, self.snapshot.get("xp")
        days_back = args.get("days", 14)
        if not isinstance(days_back, int) or isinstance(days_back, bool) or not 1 <= days_back <= 60:
            return {"error": "days must be a whole number from 1 to 60"}
        level, xp, bar = me.get("level"), me.get("xp"), me.get("xpMax")
        out = {"level": level}
        if isinstance(xp, int) and isinstance(bar, int) and bar > 0:
            out.update(experience=f"{xp:,} of {bar:,}", into_this_level=f"{xp / bar:.0%}", to_next_level=bar - xp)
        rested = record.get("rested") if isinstance(record, dict) else None
        if isinstance(rested, int) and rested > 0 and isinstance(bar, int) and bar > 0:
            out["rested"] = f"{rested:,} XP of rested bonus ({rested / bar:.0%} of a level): kills give double until it is used up"
        today = int(self.snapshot.get("takenAt", 0)) // 86400
        days = [d for d in (record.get("days") if isinstance(record, dict) else None) or []
                if isinstance(d, dict) and all(isinstance(d.get(k), int) and not isinstance(d.get(k), bool) for k in ("day", "gained", "secs"))
                and d["day"] > today - days_back and d["gained"] > 0]
        if not days:
            out["note"] = ("No experience has been measured yet. From now on Wisp's addon notes each gain and each quest handed in, "
                           "and they arrive here at logout.")
            return out
        from .market import date_of
        gained, secs = sum(d["gained"] for d in days), sum(d["secs"] for d in days)
        quest_xp, quests = sum(d.get("questXp") or 0 for d in days), sum(d.get("quests") or 0 for d in days)
        rows = []
        for d in sorted(days, key=lambda d: -d["day"]):
            rows.append({"date": date_of(d["day"]), "gained": d["gained"], "time_gaining": t("{h}h {m}m", h=d["secs"] // 3600, m=d["secs"] % 3600 // 60),
                         "per_hour": round(d["gained"] * 3600 / d["secs"]) if d["secs"] >= 600 else "too little time to say",
                         "quests_handed_in": d.get("quests") or 0, **({"levels_gained": d["levels"]} if d.get("levels") else {})})
        out.update(period_days=days_back, gained=gained, quests_handed_in=quests, days=rows,
                   from_quests=f"{quest_xp / gained:.0%}" if gained else "–")
        note = ("Measured by Wisp's addon from the character's own play. time_gaining is the time between one gain and the next "
                "when under five minutes, so long breaks are left out and per_hour is a little generous. ")
        if secs >= 600:
            rate = gained * 3600 / secs
            out["per_hour"] = round(rate)
            if isinstance(out.get("to_next_level"), int):
                hours = out["to_next_level"] / rate
                out["next_level_in"] = (t("about {h}h {m}m of play at this pace", h=int(hours), m=int(hours % 1 * 60)) if hours >= 1
                                        else t("about {m} minutes of play at this pace", m=max(1, round(hours * 60))))
        else:
            out["per_hour"] = "too little time measured to say"
            note += "A rate needs ten minutes of gaining behind it. "
        out["note"] = note + "For what to do next, questing_route says how much experience a zone's quests hold."
        if args.get("show") is True:
            headline = t("level {level} · {share} in", level=level, share=out.get("into_this_level", "?")) \
                + (f" · {out['next_level_in']}" if "next_level_in" in out else "")
            resting = (" " + t("{xp} XP of rested bonus ({share} of a level)", xp=num(rested), share=f"{rested / bar:.0%}") + "."
                       if "rested" in out else "")
            self.cards.append({"id": f"card{len(self.cards) + 1}", "lines": [], "heading": t("Leveling"), "table": {
                "title": t("{xp} XP an hour", xp=num(out["per_hour"])) if isinstance(out["per_hour"], int) else t("Your leveling"), "sub": headline,
                "columns": [t("Day"), t("Gained"), t("Time"), t("Per hour"), t("Quests")],
                "rows": [[r["date"][5:], num(r["gained"]), r["time_gaining"], num(r["per_hour"]) if isinstance(r["per_hour"], int) else "–",
                          r["quests_handed_in"]] for r in rows[:14]],
                "notes": [t("{share} of it from handing in quests.", share=out["from_quests"]) + resting,
                          t("Time between gains, breaks over five minutes left out.")]}})
            out["shown"] = "a table card with these figures is now shown to the player: do not repeat them in your text"
        return out

    def tool_questing_route(self, args: dict) -> dict:
        """What to do in a zone, in an order to walk it: hand in what is done, pick up what is
        open and worth doing, then go on with what is under way. Laid out and ordered in code."""
        if not self.snapshot:
            return {"error": self.problem}
        me = self.snapshot.get("character") or {}
        zone = _clean(args.get("zone"), 60) if isinstance(args.get("zone"), str) else ""
        if not zone or zone.lower() in ("here", "this zone", "my zone"):
            zone = str(me.get("zone") or "")
        known = sorted({p["zone"] for p in self.places.values() if p["zone"]})
        match = [z for z in known if z.lower() == zone.lower()] or [z for z in known if zone and zone.lower() in z.lower()]
        if len(match) != 1:
            return {"error": (f"no single zone with quests is named like '{zone}'" if zone else "the character's zone is not known"),
                    "zones_with_quests": known[:60]}
        zone = match[0]
        hubs, fields, counts = {}, [], {"hand_in": 0, "pick_up": 0, "go_on": 0}

        def first_in_zone(people):
            return next(((who["name"], w) for who in people for w in who["where"] if w["zone"] == zone), None)

        gains = {"now": 0, "later": 0}   # experience for handing in on this walk, and for what is picked up on it

        def hub(found, action, key):
            name, w = found
            stop = hubs.setdefault((round(w["x"]), round(w["y"])), {"who": name, "x": w["x"], "y": w["y"], "ref": w["ref"], "actions": []})
            stop["actions"].append(action)
            counts[key] += 1
        for q in self.quests:
            if q["complete"]:
                found = first_in_zone(q["ends"])
                if found:
                    hub(found, t("Hand in: {quest}", quest=q["title"]) + (t(" ({xp} XP)", xp=num(q["xp"])) if q.get("xp") else ""), "hand_in")
                    gains["now"] += q.get("xp", 0)
            elif isinstance(q.get("game_pointer"), dict) and q["game_pointer"]["zone"] == zone:
                todo = next((o["text"] for o in q["objectives"] if not o["done"]), "")
                w = q["game_pointer"]
                fields.append({"who": q["title"], "x": w["x"], "y": w["y"], "ref": w["ref"],
                               "actions": [t("Go on: {what}", what=todo or q["title"])
                                           + (t(" ({xp} XP when handed in)", xp=num(q["xp"])) if q.get("xp") else "")]})
                counts["go_on"] += 1
        skipped = {"grey": 0, "red": 0}
        for q in self.available:
            found = first_in_zone(q["starts"]) if zone in q["zones"] else None
            if not found:
                continue
            if q["colour"] in skipped:   # nothing to gain, or not yet possible
                skipped[q["colour"]] += 1
                continue
            kind = "" if q["kind"] == "normal" else ", " + t(q["kind"])
            hub(found, t("Pick up: {quest} (level {level}{kind}{xp})", quest=q["title"], level=q["level"], kind=kind,
                         xp=t(", {xp} XP", xp=num(q["xp"])) if q.get("xp") else ""), "pick_up")
            gains["later"] += q.get("xp", 0)
        if not hubs and not fields:
            return {"zone": zone, "nothing_to_do": "No quest in the log leads here and none worth doing is open here.",
                    "left_out": skipped, "try": "available_quests with no arguments shows which zones have quests worth doing."}
        here = me.get("zone") == zone and isinstance(me.get("x"), (int, float)) and isinstance(me.get("y"), (int, float))

        def walk(stops, start):   # each time the nearest one left
            ordered, at = [], start
            stops = list(stops)
            while stops:
                nxt = min(stops, key=lambda s: ((s["x"] - at[0]) ** 2 + (s["y"] - at[1]) ** 2, s["who"]))
                stops.remove(nxt)
                ordered.append(nxt)
                at = (nxt["x"], nxt["y"])
            return ordered, at
        start = (me["x"], me["y"]) if here else next(((s["x"], s["y"]) for s in list(hubs.values()) + fields), (50, 50))
        givers, last = walk(sorted(hubs.values(), key=lambda s: -len(s["actions"]))[:14], start)
        outings, _ = walk(fields[:10], last)
        stops = givers + outings
        steps = [{"text": s["who"], "detail": " · ".join(s["actions"]), "where": f"{s['x']}, {s['y']}"} for s in stops]
        said = [t("{n} to hand in", n=counts["hand_in"])] * bool(counts["hand_in"]) + [t("{n} to pick up", n=counts["pick_up"])] * bool(counts["pick_up"]) \
            + [t("{n} to go on with", n=counts["go_on"])] * bool(counts["go_on"])
        places = [self.places[s["ref"]] for s in stops]
        worth = [t("{xp} XP to hand in ({share})", xp=num(gains["now"]), share=self._share_of_level(gains["now"]))
                 if gains["now"] and self._share_of_level(gains["now"]) else "",
                 t("{xp} XP in the quests picked up ({share})", xp=num(gains["later"]), share=self._share_of_level(gains["later"]))
                 if gains["later"] and self._share_of_level(gains["later"]) else ""]
        worth = [w for w in worth if w]
        self.cards.append({
            "id": f"card{len(self.cards) + 1}", "lines": [],
            "route": {"title": t("Questing in {zone}", zone=zone),
                      "sub": t("level {level}", level=me.get("level", "?")) + " · " + ", ".join(p for p in said if p),
                      "steps": steps,
                      "notes": worth + [t("As of your last logout. Quest givers first, then the quests under way, each time the nearest.")
                                + (t(" Left out: {grey} grey", grey=skipped["grey"]) + (t(" and {red} red", red=skipped["red"]) if skipped["red"] else "") + "."
                                   if skipped["grey"] or skipped["red"] else "")]},
            "hint": t("Waypoints for TomTom: Copy, then in the game Ctrl+V twice"),
            "wisp": encode_wisp1([{"do": "tomtom.waypoint", "map": p["map"], "x": s["x"], "y": s["y"],
                                   "title": f"{i + 1}. {_clean(s['who'], 50)}"} for i, (s, p) in enumerate(zip(stops, places))]),
        })
        return {"zone": zone, "shown": "the route, as a card with its numbered stops and a Copy button for the waypoints",
                "stops": [{"stop": i + 1, **s} for i, s in enumerate(steps)], "left_out": skipped,
                "experience": {"for_handing_in_now": gains["now"], "in_quests_picked_up": gains["later"],
                               "share_of_a_level_now": self._share_of_level(gains["now"])},
                "starts": "from where the character stands" if here else "at the first quest giver",
                "note": "The player now sees the route. Do not repeat its stops: say in a sentence or two what it achieves or what "
                        "to watch for (an elite quest, a quest that is orange). Nothing has happened in the game yet."}

    def _holdings(self) -> tuple[list[dict], dict]:
        """Every investment still held or once held, valued at today's listings, and the totals."""
        from .market import market_key, summarise
        rows, today = [], int(time.time()) // 86400
        for p in self.assets.positions(market_key(self.snapshot)).values():
            now = self.crafting.prices.get(p["item"], (None,))[0]
            entry = {"item": p["name"], "held": p["held"], "paid_each": coins(p["average"]) if p["held"] else "–",
                     "invested": coins(p["average"] * p["held"]), "bought": p["bought"], "sold": p["sold"],
                     "days_held": max(0, today - p["since"]), "made_on_sales": self._signed(p["realised"]) if p["sold"] else "–",
                     "_item": p["item"], "_invested": p["average"] * p["held"], "_realised": p["realised"], "_worth": None, "_average": p["average"]}
            if now and p["held"]:
                gain = (now - p["average"]) * p["held"]
                entry.update(now_each=coins(now), worth_now=coins(now * p["held"]), unsold_gain=self._signed(gain),
                             unsold_gain_percent=f"{(now - p['average']) / p['average']:+.0%}" if p["average"] else "–",
                             _worth=now * p["held"], _gain=gain)
            elif p["held"]:
                entry["now_each"] = t("no listing known")
            past = self.market.series(self.snapshot, p["item"], max(2, today - p["since"] + 1)) if self.market and p["held"] else []
            if len(past) >= 2:   # how the price has behaved since it was bought
                lows = [low for _, low, _, _ in past]
                entry["since_bought"] = {"lowest": coins(min(lows)), "highest": coins(max(lows)), "scan_days": len(past)}
                slope = summarise(past, today)["slope"]
                if slope is not None and p["average"]:
                    weekly = slope * 7 / p["average"]
                    entry["since_bought"]["direction"] = "steady" if abs(weekly) < 0.03 else f"{'rising' if weekly > 0 else 'falling'} about {abs(weekly):.0%} a week"
            carried = self.crafting.bags.get(p["item"], 0) + self.crafting.bank.get(p["item"], 0)
            if p["held"] and carried < p["held"]:   # the rest is at auction, already sold, or in a bank Wisp has not seen
                entry["bags_and_bank_hold" if self.crafting.bank_seen else "bags_hold"] = carried
            rows.append(entry)
        rows.sort(key=lambda r: (-(r["_worth"] or r["_invested"]), r["item"]))
        invested = sum(r["_invested"] for r in rows)
        worth = sum(r["_worth"] if r["_worth"] is not None else r["_invested"] for r in rows)
        realised = sum(r["_realised"] for r in rows)
        totals = {"invested_in_what_is_held": coins(invested), "worth_now": coins(worth), "unsold_gain": self._signed(worth - invested),
                  "made_on_sales": self._signed(realised), "made_in_all": self._signed(worth - invested + realised)}
        return rows, totals

    @staticmethod
    def _signed(copper) -> str:
        return ("-" if copper < -0.5 else "+" if copper > 0.5 else "") + coins(abs(copper))

    @staticmethod
    def _listed_below(steps: list, price: int) -> int:
        """How many are listed for less than a price, from a ladder. A step holds everything up to
        its price, so this counts the steps that end below the price: never too many."""
        return max((listed for up_to, listed in steps if up_to < price), default=0)

    def tool_live_auctions(self, args: dict) -> dict:
        """Every listing of one item in the player's auction house, straight from Blizzard: no scan needed."""
        if not self.snapshot:
            return {"error": self.problem}
        setup = ("Blizzard publishes every auction of every WoW Forever auction house, refreshed about hourly, and Wisp can read "
                 "it with the player's own free developer client: create one at develop.battle.net (API Access, Create Client, any "
                 "unique name), then paste its Client ID and secret into Wisp's Settings under Blizzard auction data.")
        name = _clean(args.get("item"), 80) if isinstance(args.get("item"), str) else ""
        if not name:
            return {"error": "item must be an item name"}
        if self.live is None:
            return {"error": "Live auction data is not available in this run of Wisp. " + setup}
        found = self.graph.find(name)
        if not found:
            return {"error": f"Wisp knows no item named like '{name}'. Names are as the player's game writes them."}
        if len(found) > 1:
            return {"several_items_match": [self.graph.names[i] for i in found], "note": "Ask again with one of these names exactly."}
        seen = self.live.item(found[0], self.snapshot)
        if seen.get("error") == "not_set_up":
            return {"error": "The player has not set this up yet. " + setup + " Until then, prices come from the player's own "
                             "Auctionator scans (auction_prices, market_depth)."}
        if seen.get("error") == "house_unknown":
            return {"error": "Blizzard's data is reachable, but which of its auction houses is this character's is not known yet. "
                             "Wisp finds it by the character's own auctions: the player puts one auction up, looks at the Auctions "
                             "tab, logs out, and asks again. Until then use auction_prices and market_depth."}
        if "error" in seen:
            return {"error": "Blizzard's auction data could not be read: " + seen["error"]}
        out = {"item": self.graph.names[found[0]], "auction_house": seen["house"], "data_minutes_old": seen["minutes_old"],
               "auctions": seen["auctions"], "listed_in_all": seen["listed"],
               "note": "Straight from Blizzard, as of its last refresh (about hourly) plus data_minutes_old: every buyout listing of "
                       "this item in the player's auction house, the player's own included. Listings, not sales. Prices are for one."}
        if not seen["steps"]:
            out["nothing_listed"] = "No buyout listing of this item right now."
            return out
        below = 0
        ladder = []
        for each, qty in seen["steps"][:12]:
            below += qty
            ladder.append({"each": coins(each), "listed": qty, "listed_up_to_here": below})
        out.update(lowest=coins(seen["steps"][0][0]), price_ladder=ladder)
        if len(seen["steps"]) > 12:
            out["dearer_steps_left_out"] = len(seen["steps"]) - 12
        mine = self.crafting.bags.get(found[0], 0) + self.crafting.bank.get(found[0], 0)
        if mine:
            out["you_hold"] = mine
        return out

    def tool_market_depth(self, args: dict) -> dict:
        """How deep an item's market is: how many are listed at each price above the lowest."""
        problem = self._no_market()
        if problem and not (self.market and isinstance(args.get("item"), str)):
            return problem
        name = _clean(args.get("item"), 80) if isinstance(args.get("item"), str) else ""
        found = self.graph.find(name) if name else []
        if len(found) != 1:
            return {"error": f"no single item is named like '{name}'" if name else "name the item",
                    **({"several_items_match": [self.graph.names[i] for i in found]} if found else {})}
        item, ladder = found[0], self.market.depth(self.snapshot, found[0]) if self.market else None
        if ladder is None:
            return {"error": f"Wisp has no price ladder for {self.graph.names[item]}. It reads one after a full scan with Auctionator, "
                             "or when the item is searched for at the auction house; then the player logs out."}
        steps, lowest = ladder["steps"], ladder["steps"][0][0]
        total = steps[-1][1]
        age = max(0, int(self.snapshot.get("takenAt", 0)) - ladder["at"])
        out = {"item": self.graph.names[item], "lowest": coins(lowest), "listed_in_all": total, "auctions": ladder["auctions"],
               "seen": t("in the last session") if age < 6 * 3600 else t("{days} days before the last logout", days=round(age / 86400, 1)),
               "ladder": [{"up_to": coins(price), "listed": listed, "above_the_lowest": f"{(price - lowest) / lowest:+.0%}"} for price, listed in steps]}
        if ladder["partial"]:
            out["partial"] = "only the first page of listings was loaded: there are more, at higher prices"
        block = max(((b[1] - a[1], -b[0]) for a, b in zip([[0, 0]] + steps, steps)), default=None)   # of equal blocks, the cheaper
        block = block and (block[0], -block[1])
        if block and len(steps) > 1:
            out["biggest_block"] = f"{block[0]} listed at or just under {coins(block[1])}"
        if ladder["mine"]:
            out["yours"] = {"listed": ladder["mine"], "from": coins(ladder["mine_at"]),
                            "listed_below_yours": self._listed_below(steps, ladder["mine_at"])}
        quantity = args.get("quantity")
        if quantity is not None:
            if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 10**7:
                return {"error": "quantity must be a whole number from 1"}
            cost, got = 0, 0
            for price, listed in steps:   # each step is priced at its top: what it would cost at most
                take = min(quantity, listed) - got
                if take > 0:
                    cost, got = cost + take * price, got + take
            out["to_buy"] = {"quantity": quantity, **({"at_most": coins(cost), "each_on_average": coins(cost / got), "last_ones_at": coins(next(p for p, l in steps if l >= got))}
                                                      if got == quantity else {"not_enough_listed": f"only {got} are listed"})}
        if isinstance(args.get("price"), str):
            mine = parse_money(args["price"])
            if mine is None:
                return {"error": "the price must be money with its units: '3s 50c'"}
            out["at_your_price"] = {"price": coins(mine), "listed_for_less": self._listed_below(steps, mine),
                                    "against_the_lowest": f"{(mine - lowest) / lowest:+.0%}"}
        out["note"] = ("Every auction of this item as it stood when a full scan or a search last showed it. Each line is how many "
                       "are listed up to that price, counted from the lowest; to_buy is priced at the top of each step, so it is "
                       "the most it would cost. Listings change by the minute: this is that moment, not now.")
        if args.get("show") is True:
            self.cards.append({"id": f"card{len(self.cards) + 1}", "lines": [], "heading": t("Market depth"), "table": {
                "title": out["item"], "sub": t("{total} listed in {auctions} · from {lowest} · seen {seen}", total=total,
                                               auctions=tn(ladder["auctions"], "{n} auction", "{n} auctions"), lowest=out["lowest"], seen=out["seen"]),
                "columns": [t("Up to"), t("Listed"), t("Above the lowest")],
                "rows": [[r["up_to"], r["listed"], r["above_the_lowest"]] for r in out["ladder"]],
                "notes": [t("How many are listed up to each price, counted from the cheapest.")
                          + (t(" Yours: {mine} from {price}, with {less} listed for less.", mine=ladder["mine"], price=coins(ladder["mine_at"]),
                               less=out["yours"]["listed_below_yours"]) if ladder["mine"] else "")]}})
            out["shown"] = "a table card of the ladder is now shown to the player: do not repeat its rows"
        return out

    def tool_my_auctions(self, args: dict) -> dict:
        """What the character has up for sale, set against the lowest listing Auctionator saw."""
        if not self.snapshot:
            return {"error": self.problem}
        record = self.snapshot.get("ownAuctions")
        if not isinstance(record, dict) or not isinstance(record.get("list"), list):
            return {"error": "Wisp has not seen the character's auctions yet. It reads them whenever the Auctions tab of the "
                             "auction house is open: the player opens that tab once, then logs out."}
        seen, taken = record.get("seenAt"), self.snapshot.get("takenAt")
        since = max(0, taken - seen) if isinstance(seen, (int, float)) and isinstance(taken, (int, float)) else 0
        craft, rows, listed, waiting, undercut = self.crafting, [], 0, 0, 0
        for a in record["list"]:
            if not isinstance(a, dict) or not isinstance(a.get("item"), int) or not isinstance(a.get("buyout"), (int, float)) or a["buyout"] <= 0:
                continue
            qty = a["quantity"] if isinstance(a.get("quantity"), int) and a["quantity"] > 0 else 1
            each, name = int(a["buyout"]), _clean(a.get("name"), 80) or craft.name(a["item"])
            entry = {"item": name, "quantity": qty, "each": coins(each), "total": coins(each * qty), "_total": each * qty}
            lowest = craft.prices.get(a["item"], (None,))[0]
            if a.get("sold"):
                entry["status"], entry["_short"] = "sold: the money is in the mail", t("sold")
                waiting += each * qty
            else:
                left = (a["secondsLeft"] - since) if isinstance(a.get("secondsLeft"), (int, float)) else None
                entry["status"] = ("time left unknown" if left is None else "has run out by now" if left <= 0
                                   else f"about {max(1, round(left / 3600))}h left" if left >= 3600 else "under an hour left")
                entry["_short"] = (t("time left unknown") if left is None else t("run out") if left <= 0
                                   else t("{h}h left", h=max(1, round(left / 3600))) if left >= 3600 else t("under an hour left"))
                if a.get("bidder"):
                    entry["status"] += ", has a bid"
                    entry["_short"] += t(", has a bid")
                listed += each * qty
                if not lowest:
                    entry["against_the_market"] = "no listing seen to compare with"
                elif each <= lowest:
                    entry["against_the_market"], entry["_lowest"] = "the lowest, or level with it", True
                else:
                    entry.update(lowest_seen=coins(lowest), against_the_market=f"undercut: {(each - lowest) / each:.0%} dearer than the lowest")
                    undercut += 1
                ladder = self.market.depth(self.snapshot, a["item"]) if self.market else None
                if ladder:   # not only whether somebody is cheaper, but how many stand in front
                    entry["listed_for_less"] = self._listed_below(ladder["steps"], each)
            rows.append(entry)
        rows.sort(key=lambda r: (-r["_total"], r["item"]))
        out = {"auctions": [{k: v for k, v in r.items() if k[0] != "_"} for r in rows[:60]], "listed_for": coins(listed),
               "sold_and_waiting_in_the_mail": coins(waiting), "undercut": undercut,
               "seen": f"{round(since / 3600, 1)} hours before the last logout" if since >= 1800 else "just before the last logout",
               "note": "The character's own auctions as the Auctions tab last showed them, and the lowest listing from the player's "
                       "own Auctionator scans, which may be older or newer than that look and includes the player's own listing. "
                       "The price is for one. An auction may have sold, been bid on or run out since."}
        if args.get("show") is True and rows:
            self.cards.append({"id": f"card{len(self.cards) + 1}", "lines": [], "heading": t("Your auctions"), "table": {
                "title": tn(len(rows), "{n} auction up", "{n} auctions up"), "sub": t("listed for {sum}", sum=out["listed_for"])
                + (t(" · {n} undercut", n=undercut) if undercut else "")
                + (t(" · {sum} sold, in the mail", sum=out["sold_and_waiting_in_the_mail"]) if waiting else ""),
                "columns": [t("Item"), t("Qty"), t("Each"), t("Lowest"), t("Status")],
                "rows": [[r["item"], r["quantity"], r["each"], r.get("lowest_seen", t("you") if r.get("_lowest") else "–"), r["_short"]]
                         for r in rows[:20]],
                "notes": [t("As your Auctions tab last showed them. Lowest is from your own scans and may be older.")]}})
            out["shown"] = "a table card with these rows is now shown to the player: do not repeat them in your text"
        return out

    def _deal(self, d: dict) -> dict:
        """One auction mail in figures. A sale brings its price less the house's cut; the deposit
        is the player's own money coming back, so it is no part of what was made."""
        item = d["item"] if d["item"] else next((i for i, n in self.graph.names.items() if n.lower() == d["name"].lower()), None)
        qty = d["count"] * d["n"]
        net = (d["bid"] - d["cut"]) * d["n"] if d["kind"] == "sell" else d["bid"] * d["n"]
        return {"mail": d["id"], "kind": d["kind"], "item": self.graph.names.get(item, d["name"]) if item else d["name"], "_item": item,
                "quantity": qty, "each": coins(d["bid"] / d["count"]), "_total": d["bid"] * d["n"], "_cut": d["cut"] * d["n"], "_net": net,
                "_each_net": net / qty, "days_ago": max(0, (int(time.time()) - d["sent"]) // 86400), "_day": d["sent"] // 86400,
                "_ref": f"{d['who']}#{d['id']}", "_auctions": d["n"], "tracked": d["trade"] is not None}

    def tool_auction_ledger(self, args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        if self.assets is None:
            return {"error": "the ledger is not available in this run"}
        from .market import market_key
        view, days = args.get("view", "summary"), args.get("days", 30)
        if view not in ("summary", "sales", "purchases"):
            return {"error": "view must be summary, sales or purchases"}
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 3650:
            return {"error": "days must be a whole number from 1 to 3650"}
        deals = [self._deal(d) for d in self.assets.deals(market_key(self.snapshot), int(time.time()) - days * 86400)]
        if isinstance(args.get("item"), str) and args["item"].strip():
            wanted = _clean(args["item"], 80).lower()
            deals = [d for d in deals if wanted in d["item"].lower()]
        note = ("What the auction house's mail said, noted by Wisp's addon whenever the mailbox was open: real purchases and real "
                "sales, not listings. A sale's 'received' is its price less the house's cut. An auction whose mail was emptied "
                "before the mailbox was opened with Wisp running is not here, and neither are auctions that ran out unsold.")
        if not deals:
            return {"deals": [], "note": "No auction mail has been noted in that period. " + note}
        sold, bought = [d for d in deals if d["kind"] == "sell"], [d for d in deals if d["kind"] == "buy"]

        def rows(some):
            return [{"mail": d["mail"], "item": d["item"], "quantity": d["quantity"], "each": d["each"],
                     **({"sold_for": coins(d["_total"]), "received": coins(d["_net"])} if d["kind"] == "sell" else {"paid": coins(d["_total"])}),
                     "days_ago": d["days_ago"], **({"tracked_as_investment": True} if d["tracked"] else {})} for d in some[:40]]
        if view == "summary":
            def by_item(some, top=6):
                items = {}
                for d in some:
                    entry = items.setdefault(d["item"], {"item": d["item"], "quantity": 0, "_money": 0})
                    entry["quantity"] += d["quantity"]
                    entry["_money"] += d["_net"]
                ranked = sorted(items.values(), key=lambda e: -e["_money"])[:top]
                return [{"item": e["item"], "quantity": e["quantity"], "total": coins(e["_money"]), "each_on_average": coins(e["_money"] / e["quantity"])}
                        for e in ranked]
            gross, cut, spent = sum(d["_total"] for d in sold), sum(d["_cut"] for d in sold), sum(d["_total"] for d in bought)
            out = {"period_days": days,
                   "sold": {"auctions": sum(d["_auctions"] for d in sold), "for": coins(gross), "auction_house_cut": coins(cut), "received": coins(gross - cut),
                            **({"cut_rate": f"{cut / gross:.1%}"} if gross else {}), "most_money_from": by_item(sold)},
                   "bought": {"auctions": sum(d["_auctions"] for d in bought), "paid": coins(spent), "most_money_on": by_item(bought)},
                   "balance": self._signed(gross - cut - spent), "note": note}
            table = {"title": t("Auction house, in and out"),
                     "sub": t("last {days} days · received {received} · paid {paid} · balance {balance}", days=days,
                              received=coins(gross - cut), paid=coins(spent), balance=out["balance"]),
                     "columns": [t("Item"), t("Sold"), t("Received"), t("Each")],
                     "rows": [[e["item"], e["quantity"], e["total"], e["each_on_average"]] for e in out["sold"]["most_money_from"]],
                     "notes": [t("Real sales from your auction mail, after the house's cut{rate}.", rate=f" ({cut / gross:.1%})" if gross else "")]}
        else:
            some = sold if view == "sales" else bought
            out = {"view": view, "period_days": days, "deals": rows(some), **({"left_out": len(some) - 40} if len(some) > 40 else {}), "note": note}
            table = {"title": t("What you sold") if view == "sales" else t("What you bought"),
                     "sub": t("last {days} days, from your auction mail", days=days),
                     "columns": [t("Item"), t("Qty"), t("Each"), t("Received") if view == "sales" else t("Paid"), t("Days ago")],
                     "rows": [[d["item"], d["quantity"], d["each"], coins(d["_net"] if view == "sales" else d["_total"]), d["days_ago"]] for d in some[:20]],
                     "notes": [t("Received is the sale price less the auction house's cut.") if view == "sales" else t("What each auction really cost.")]}
        if args.get("show") is True and table["rows"]:
            self.cards.append({"id": f"card{len(self.cards) + 1}", "lines": [], "heading": t("Auction house"), "table": table})
            out["shown"] = "a table card with these rows is now shown to the player: do not repeat them in your text"
        return out

    def tool_investments(self, args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        if self.assets is None:
            return {"error": "investments are not available in this run"}
        from .market import market_key
        action, market = args.get("action", "list"), market_key(self.snapshot)
        if action not in ("list", "buy", "sell", "remove"):
            return {"error": "action must be list, buy, sell or remove"}
        caveat = ("Worth and gain are at the lowest listing from the player's own Auctionator scans: what the items are listed "
                  "for, before the auction house's cut, not what they will sell for. made_on_sales uses the prices the player "
                  "reported. Wisp only knows the trades it was told about.")
        item = name = deal = None
        if args.get("from_mail") is not None:   # the trade as the auction house's mail has it: nothing to type, nothing to get wrong
            if action not in ("buy", "sell"):
                return {"error": "from_mail goes with buy or sell"}
            deal = next((d for d in map(self._deal, self.assets.deals(market)) if d["mail"] == args["from_mail"]), None)
            if deal is None or deal["kind"] != action:
                return {"error": "no auction mail with that number is a " + ("purchase" if action == "buy" else "sale")}
            if deal["tracked"]:
                return {"error": "that mail is already recorded as an investment trade"}
            if not deal["_item"]:
                return {"error": f"Wisp does not know the item '{deal['item']}' yet: it learns items the character carries or that "
                                 "Auctionator has scanned"}
            item, name = deal["_item"], deal["item"]
        elif action != "list" or isinstance(args.get("item"), str) and args["item"].strip():
            asked = _clean(args.get("item"), 80) if isinstance(args.get("item"), str) else ""
            if not asked and not (action == "remove" and isinstance(args.get("trade"), int)):
                return {"error": "name the item"}
            if asked:
                held = {p["name"].lower(): p["item"] for p in self.assets.positions(market).values()}
                found = [held[asked.lower()]] if asked.lower() in held else self.graph.find(asked)
                if len(found) != 1:
                    return {"error": f"no single item is named like '{asked}'" + (". Wisp knows the items the character carries, crafts or has a "
                                     "price for; scan the auction house so it learns this one." if not found else ""),
                            **({"several_items_match": [self.graph.names[i] for i in found]} if found else {})}
                item, name = found[0], self.graph.names.get(found[0], asked)
        if deal is not None:
            qty, price, assumed = deal["quantity"], max(1, round(deal["_each_net"])), False
            position = self.assets.positions(market).get(item)
            if action == "sell" and (position is None or position["held"] < qty):
                return {"error": f"only {position['held'] if position else 0} {name} are recorded as held: record the purchase first"}
            trade = self.assets.add(market, item, name, action, qty, price, deal["_day"], False,
                                    "from the auction house's mail" + (", after its cut" if action == "sell" else ""), mail=deal["_ref"])
        elif action in ("buy", "sell"):
            qty = args.get("quantity")
            if not isinstance(qty, int) or isinstance(qty, bool) or not 1 <= qty <= 1_000_000:
                return {"error": "quantity must be a whole number from 1 to 1000000"}
            each, total = args.get("price_each"), args.get("total")
            if each is not None and total is not None:
                return {"error": "give price_each or total, not both"}
            assumed = each is None and total is None
            if assumed:   # the player gave no price: today's lowest listing, and it is marked as assumed
                price = self.crafting.prices.get(item, (None,))[0]
                if not price:
                    return {"error": f"there is no listing known for {name}: ask the player what they paid" if action == "buy"
                                     else f"there is no listing known for {name}: ask the player what they sold it for"}
            else:
                money = parse_money(each if each is not None else total)
                if money is None:
                    return {"error": "the price must be money with its units, as the player gave it: '3s 50c', '1g 20s', '45c'"}
                price = money if each is not None else round(money / qty)
                if price < 1:
                    return {"error": "that comes to less than a copper each"}
            days_ago = args.get("days_ago", 0)
            if not isinstance(days_ago, int) or isinstance(days_ago, bool) or not 0 <= days_ago <= 730:
                return {"error": "days_ago must be a whole number from 0 to 730"}
            position = self.assets.positions(market).get(item)
            if action == "sell" and (position is None or position["held"] < qty):
                return {"error": f"only {position['held'] if position else 0} {name} are recorded as held: record the purchase first, or sell fewer"}
            trade = self.assets.add(market, item, name, action, qty, price, int(time.time()) // 86400 - days_ago, assumed,
                                    _clean(args.get("note"), 200))
        elif action == "remove":
            by_number = isinstance(args.get("trade"), int) and not isinstance(args.get("trade"), bool)
            if by_number:   # a purchase that a later sale rests on cannot go before the sale does
                held = {}
                for row in self.assets.trades(market):
                    if row["id"] != args["trade"]:
                        held[row["item"]] = held.get(row["item"], 0) + (row["qty"] if row["kind"] == "buy" else -row["qty"])
                        if held[row["item"]] < 0:
                            return {"error": "a later sale rests on that purchase: remove the sale first"}
            removed = self.assets.remove(market, trade=args["trade"]) if by_number else self.assets.remove(market, item=item)
            if not removed:
                return {"error": "nothing recorded matches that"}
        rows, totals = self._holdings()
        # what the mailbox knows that the record does not: purchases not tracked, and sales of things that are
        recent = [d for d in map(self._deal, self.assets.deals(market, int(time.time()) - 30 * 86400)) if not d["tracked"]]
        held = {r["_item"] for r in rows if r["held"]}
        mailbox = {"purchases_not_tracked": [{"mail": d["mail"], "item": d["item"], "quantity": d["quantity"], "each": d["each"], "days_ago": d["days_ago"]}
                                             for d in recent if d["kind"] == "buy" and d["_item"]][:8],
                   "sales_of_tracked_items_not_recorded": [{"mail": d["mail"], "item": d["item"], "quantity": d["quantity"], "each": d["each"],
                                                            "days_ago": d["days_ago"]} for d in recent if d["kind"] == "sell" and d["_item"] in held][:8]}
        mailbox = {k: v for k, v in mailbox.items() if v}
        offer = ("from_the_mailbox lists real trades the record does not have. Offer them to the player once, in a line each, and "
                 "record one with from_mail only when they say yes: not every purchase is an investment.")
        if not rows:
            return {"investments": [], **({"from_the_mailbox": mailbox, "what_to_do": offer} if mailbox else {}),
                    "note": "Nothing is recorded as an investment yet. When the player says they bought something "
                            "to sell later, record it with action buy: the item, how many, and what they paid."}
        out = {"totals": totals, "investments": [{k: v for k, v in r.items() if k[0] != "_"} for r in rows], "note": caveat,
               **({"from_the_mailbox": mailbox, "what_to_do": offer} if mailbox and action == "list" else {})}
        if action in ("buy", "sell"):
            out["recorded"] = {"trade": trade, "action": action, "item": name, "quantity": qty, "each": coins(price),
                               **({"price_assumed": "the player gave no price: today's lowest listing was taken. Say so."} if assumed else {})}
        chosen = next((r for r in rows if r["_item"] == item), None) if item else None
        if chosen and action == "list":   # one investment in depth: its trades, and its price since it was bought
            position = self.assets.positions(market)[item]
            out = {"investment": {k: v for k, v in chosen.items() if k[0] != "_"}, "note": caveat,
                   "trades": [{"trade": t["id"], "action": t["kind"], "quantity": t["qty"], "each": coins(t["each"]),
                               "days_ago": int(time.time()) // 86400 - t["day"], **({"price_assumed": True} if t["assumed"] else {}),
                               **({"note": t["note"]} if t["note"] else {})} for t in position["trades"]]}
            past = self.market.series(self.snapshot, item, max(14, int(time.time()) // 86400 - position["since"] + 8)) if self.market else []
            if args.get("show") is True and len(past) >= 2:
                self.cards.append({"id": f"card{len(self.cards) + 1}", "lines": [], "heading": t("Investment"), "chart": {
                    "title": chosen["item"], "sub": t("{held} held · bought {days} days ago", held=chosen["held"], days=chosen["days_held"]),
                    "points": [list(row) for row in past[-180:]], "usual": round(position["average"]), "line": t("paid"), "estimate": None,
                    "stats": [[t("Bought at"), chosen["paid_each"]], [t("Now"), chosen.get("now_each", "–")],
                              [t("Gain"), chosen.get("unsold_gain_percent", "–")], [t("Unsold gain"), chosen.get("unsold_gain", "–")]],
                    "notes": [t("The line is the day's lowest listing from your own scans; the level marked is what you paid. Listed worth, before the auction house's cut.")]}})
                out["shown"] = "a chart card of its price against what was paid is now shown to the player"
            return out
        if args.get("show") is True or action in ("buy", "sell", "remove"):
            self.cards.append({"id": f"card{len(self.cards) + 1}", "lines": [], "heading": t("Investments"), "table": {
                "title": t("Your investments"),
                "sub": t("paid {paid} · worth {worth} · made {made} in all", paid=totals["invested_in_what_is_held"],
                         worth=totals["worth_now"], made=totals["made_in_all"]),
                "columns": [t("Item"), t("Held"), t("Bought at"), t("Now"), t("Profit")],
                "rows": [[r["item"], r["held"], r["paid_each"], r.get("now_each", "–"),
                          r.get("unsold_gain", r["made_on_sales"]) if r["held"] else t("sold: {made}", made=r["made_on_sales"])] for r in rows[:20]],
                "notes": [t("At the lowest listing from your own scans, before the auction house's cut. \"Made in all\" adds what your sales brought.")]}})
            out["shown"] = "a table card of the investments is now shown to the player: do not repeat its rows"
        return out

    def tool_gold(self, args: dict) -> dict:
        """What the character's own things would fetch: bags to sell, recipes to craft for profit."""
        if not self.snapshot:
            return {"error": self.problem}
        view, craft = args.get("view"), self.crafting
        if view not in ("bags", "crafts"):
            return {"error": "view must be bags or crafts"}
        if not craft.prices:
            return self.tool_auction_prices({"items": ["x"]})   # says why there are no prices
        caveat = ("Lowest listings from the player's own Auctionator scans: what things are listed for, before the auction "
                  "house's cut, and not a promise that they sell. Selling many at once pushes a price down.")

        def usual(item):
            past = self.market.series(self.snapshot, item, 30) if self.market else []
            return statistics.median(low for _, low, _, _ in past) if len(past) >= 3 else None
        if view == "bags":
            rows, bound, unpriced = [], 0, 0
            stock = {item: craft.bags.get(item, 0) + craft.bank.get(item, 0) for item in {*craft.bags, *craft.bank}}
            for item, count in stock.items():
                price, vendor = craft.prices.get(item, (0,))[0], self.farming.vendor.get(item, 0)
                if item in craft.bound:
                    bound += 1
                    continue
                if not price and not vendor:
                    unpriced += 1
                    continue
                level = usual(item) if price else None
                if not price:
                    advice = "nobody lists it: sell to a vendor"
                elif vendor >= price:
                    advice = "a vendor pays as much or more: sell to a vendor"
                elif level and price >= level * 1.15:
                    advice = f"{(price - level) / level:+.0%} against its usual price: a good time to sell"
                elif level and price <= level * 0.85:
                    advice = f"{(price - level) / level:+.0%} against its usual price: hold if you can"
                else:
                    advice = "at its usual price" if level else "no price history yet"
                rows.append({"item": craft.name(item), "have": count, **({"of_them_in_bank": craft.bank[item]} if craft.bank.get(item) else {}),
                             "each": coins(price) if price else "–",
                             "worth": coins(max(price, vendor) * count), "vendor_pays": coins(vendor * count) if vendor else "–",
                             "advice": advice, "_worth": max(price, vendor) * count})
            rows.sort(key=lambda r: (-r["_worth"], r["item"]))
            total = sum(r["_worth"] for r in rows)
            out = {"view": "bags", "sellable_worth": coins(total), "items": [{k: v for k, v in r.items() if k[0] != "_"} for r in rows[:40]],
                   "left_out": {"bound_to_the_character": bound, "no_price_known": unpriced},
                   "note": caveat + (" The bank is counted as it was when last opened." if craft.bank_seen
                                     else " The bank is not known: it is counted once the player has opened it.")}
            table = {"title": t("What your bags and bank would fetch") if craft.bank else t("What your bags would fetch"),
                     "sub": t("about {sum} in all, at auction or from a vendor", sum=coins(total)),
                     "columns": [t("Item"), t("Have"), t("Each"), t("Worth")], "rows": [[r["item"], r["have"], r["each"], r["worth"]] for r in rows[:15]],
                     "notes": [t("Lowest listings from your own scans, or what a vendor pays. Listed worth, not sales.")]}
        else:
            rows, unknown = [], 0
            for recipe in craft.recipes.values():
                sells = craft.prices.get(recipe["makes"], (0,))[0] * recipe["makes_count"] if recipe["makes"] else 0
                costs = [craft.prices.get(item, (0,))[0] * count for item, count in recipe["reagents"]]
                if not recipe["learned"] or not sells or not costs or not all(costs):
                    unknown += recipe["learned"]
                    continue
                profit = sells - sum(costs)
                rows.append({"recipe_id": recipe["id"], "recipe": recipe["name"], "profession": recipe["profession"],
                             **({"crafter": recipe["owner"]} if recipe["owner"] else {}),
                             "materials": coins(sum(costs)), "sells_for": coins(sells),
                             "profit": ("-" if profit < 0 else "") + coins(abs(profit)), "return": f"{profit / sum(costs):+.0%}",
                             "can_make_from_bags": min(craft.bags.get(item, 0) // count for item, count in recipe["reagents"]),
                             "_profit": profit})
            rows.sort(key=lambda r: (-r["_profit"], r["recipe"]))
            out = {"view": "crafts", "recipes": [{k: v for k, v in r.items() if k[0] != "_"} for r in rows[:30]],
                   "recipes_without_full_prices": unknown,
                   "note": caveat + " Profit is the result's listing minus every material bought at auction, per craft. Only the "
                           "character's own learned recipes, and only those where the result and every material have a price."}
            table = {"title": t("Crafting for profit"), "sub": t("your recipes, at your auction prices"),
                     "columns": [t("Recipe"), t("Materials"), t("Sells for"), t("Profit")],
                     "rows": [[r["recipe"], r["materials"], r["sells_for"], r["profit"]] for r in rows[:15]],
                     "notes": [t("Per craft, materials bought at auction. Listed prices, before the auction house's cut.")]}
        if args.get("show") is True and table["rows"]:
            self.cards.append({"id": f"card{len(self.cards) + 1}", "lines": [], "table": table})
            out["shown"] = "a table card with these rows is now shown to the player: do not repeat them in your text"
        return out

    def tool_quest_search(self, args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        db = self.questdb()
        if db is None:
            return {"error": "Wisp's addon has not read Questie's quest list yet. It does that by itself in the background about "
                             "a minute after login, with Questie running; the player then logs out once and it is here for good."
                             + ("" if (self.snapshot.get("addons") or {}).get("questie") else " Questie was not running at the last logout.")}
        query, qid, zone, kind = args.get("name"), args.get("id"), args.get("zone"), args.get("kind", "any")
        if qid is not None and (not isinstance(qid, int) or isinstance(qid, bool)):
            return {"error": "id must be a whole number"}
        if kind not in ("any", "normal", "elite", "dungeon", "raid", "pvp"):
            return {"error": "kind must be any, normal, elite, dungeon, raid or pvp"}
        low, high = args.get("min_level", 0), args.get("max_level", 100)
        if not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100 for v in (low, high)):
            return {"error": "min_level and max_level must be whole numbers from 0 to 100"}
        limit = args.get("limit", 15)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 40:
            return {"error": "limit must be a whole number from 1 to 40"}
        note = ("From Questie's whole quest database: every quest this character's race and class could ever take, not only "
                "those in reach. state: done, in the log, open now (Questie shows it on the map), open by its requirements "
                "(level and earlier quests allow it, though Questie may hide it, e.g. too low for the character), not yet "
                "(why_not_open says what is missing), closed (another quest was chosen instead). colour is how the quest log "
                "would colour it. Every place has a ref for the waypoint tool. Never add a quest or a fact from memory.")
        if qid is not None:
            if qid not in db.quests:
                return {"error": f"no quest {qid} in Questie's list for this character: it may be for another race or class, "
                                 "or hidden by Questie"}
            return {"quest": db.detail(qid), "character_level": db.level, "note": note}
        if not any(isinstance(v, str) and v.strip() for v in (query, zone)) and kind == "any" and (low, high) == (0, 100):
            return {"error": "give a name, an id, or at least a zone, a kind or a level range"}
        found = db.find(query) if isinstance(query, str) and query.strip() else sorted(db.quests)
        if isinstance(zone, str) and zone.strip():
            wanted = _clean(zone, 60).lower()
            if wanted in ("here", "this zone", "my zone"):
                wanted = str((self.snapshot.get("character") or {}).get("zone") or "").lower()
            zones = db.zones()
            match = [z for z in zones if z.lower() == wanted] or [z for z in zones if wanted and wanted in z.lower()]
            if len(match) != 1:
                return {"error": f"no single zone is named like '{_clean(zone, 60)}'", "zones": zones[:80]}
            found = [i for i in found if db.quests[i].get("zone") == match[0]]
        found = [i for i in found if kind == "any" or (db.quests[i].get("kind") or "normal") == kind]
        found = [i for i in found if low <= (db.quests[i].get("level") or 0) <= high]
        if args.get("hide_done") is True:
            found = [i for i in found if i not in db.done]
        if not found:
            return {"found": 0, "note": "Nothing in Questie's list for this character matches. Quests for another race or class "
                                        "are not in it.", "character_level": db.level}
        if len(found) <= 3:
            return {"found": len(found), "quests": [db.detail(i) for i in found], "character_level": db.level, "note": note}
        if not (isinstance(query, str) and query.strip()):
            found.sort(key=lambda i: (db.quests[i].get("level") or 0, db.quests[i]["name"]))
        return {"found": len(found), "quests": [db.row(i) for i in found[:limit]],
                **({"left_out": len(found) - limit} if len(found) > limit else {}), "character_level": db.level,
                "note": note + " Several match: call again with id for one quest's givers, places, requirements and chain."}

    def tool_quest_done(self, args: dict) -> dict:
        ids = args.get("quest_ids")
        if not isinstance(ids, list) or not ids or len(ids) > 50 or not all(isinstance(i, int) for i in ids):
            return {"error": "quest_ids must be a list of 1 to 50 whole numbers"}
        completed = set(_as_list(self.snapshot.get("completed")))
        return {"completed": {str(i): i in completed for i in ids}}

    def _no_journal(self) -> dict | None:
        if not self.snapshot:
            return {"error": self.problem}
        if not self.dungeons:
            return {"error": "There is no dungeon data: the DungeonJournal addon was not found when the snapshot was "
                             "taken. With it installed, Wisp knows every dungeon's quests, what they need first, and the loot."}
        return None

    def tool_dungeons(self, _args: dict) -> dict:
        missing = self._no_journal()
        if missing:
            return missing
        rows = []
        for d in self.dungeons:
            statuses = [q["status"] for q in d["quests"]]
            rows.append({"name": d["name"], "level": d["level"], "location": d["location"], "quests": len(statuses),
                         "done": statuses.count("done"), "in_log": statuses.count("in your quest log"),
                         "available_now": statuses.count("available"), "bosses": len(d["bosses"])})
        return {"dungeons": rows, "snapshot_age_minutes": self.age_minutes(),
                "note": "Quest counts are for this character's faction. Use the dungeon tool with a name for the details."}

    def tool_dungeon(self, args: dict) -> dict:
        missing = self._no_journal()
        if missing:
            return missing
        wanted = args.get("name")
        if not isinstance(wanted, str) or not wanted.strip():
            return {"error": "name must be a dungeon's name"}
        key = wanted.strip().casefold()
        found = [d for d in self.dungeons if d["name"].casefold() == key] or \
                [d for d in self.dungeons if key in d["name"].casefold() or d["name"].casefold().removeprefix("the ") in key]
        if len(found) > 1:
            return {"error": "More than one dungeon matches. Ask for one of: " + ", ".join(d["name"] for d in found)}
        if not found:
            return {"error": "No dungeon is called that. The dungeons are: " + ", ".join(d["name"] for d in self.dungeons)}
        return {"dungeon": found[0], "snapshot_age_minutes": self.age_minutes(),
                "note": "From the DungeonJournal addon. Quest status is as of the player's last logout. "
                        "Every 'ref' can go to the waypoint tool."}

    def tool_changes(self, args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        days = args.get("days")
        if days is not None and (isinstance(days, bool) or not isinstance(days, (int, float)) or not 0 < days <= 3650):
            return {"error": "days must be a number above 0"}
        older = self.history.earlier(self.snapshot, (days or 0) * 86400) if self.history else None
        if older is None:
            return {"error": "There is no earlier snapshot to compare with" + (" from that long ago" if days else "")
                             + ". Wisp keeps one each time the player logs out, starting from when it was first run."}
        return {"changes": diff(older, self.snapshot),
                "note": "Worked out from two saved snapshots: 'from' and 'to' are when each was taken (Unix time). "
                        "Only quests that were in the quest log at some point have names; the count is exact."}

    @staticmethod
    def _slug(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    def _gather_route(self, name: str, map_id: int):
        """Lay a walk through one kind of node in one zone and register it under a ref the waypoint tool takes."""
        gather, me = self.gathering, self.snapshot.get("character") or {}
        place = gather.nodes[name]["zones"][map_id]
        start = (me["x"], me["y"]) if me.get("map") == map_id and isinstance(me.get("x"), (int, float)) and isinstance(me.get("y"), (int, float)) else None
        stops = gather.route(name, map_id, start)
        ref = f"g{map_id}." + self._slug(name)
        self.routes[ref] = {"title": t("{name}, {stops}", name=name, stops=tn(len(stops), "{n} stop", "{n} stops")),
                            "detail": t("{zone} · {nodes}", zone=place["zone"], nodes=tn(len(place["at"]), "{n} known node", "{n} known nodes")),
                            "stops": [{"map": map_id, "zone": place["zone"], "x": s["x"], "y": s["y"], "title": f"{name} {i + 1}"}
                                      for i, s in enumerate(stops)]}
        return ref, stops, start

    def _revive(self, ref: str):
        """A ref from an earlier answer. The snapshot is read afresh for every question, and some places are only
        registered when a tool looks them up (any item's sellers, any quest's givers, a gathering route): look
        the same thing up again, so that a ref a tool once gave stays good."""
        try:
            if m := re.fullmatch(r"w(\d+)\.(vendor|drop|object)\.\d+\.\d+", ref):
                self.graph._from_world(int(m.group(1)))
            elif m := re.fullmatch(r"qdb(\d+)\.(start|end|kill|use)\.\d+", ref):
                db = self.questdb()
                if db is not None and int(m.group(1)) in db.quests:
                    db.detail(int(m.group(1)))
            elif m := re.fullmatch(r"g(\d+)\.([a-z0-9-]+)", ref):
                for name, node in self.gathering.nodes.items():
                    if self._slug(name) == m.group(2) and int(m.group(1)) in node["zones"]:
                        self._gather_route(name, int(m.group(1)))
        except Exception:   # whatever is wrong with an old ref, it is simply unknown
            pass

    def tool_waypoint(self, args: dict) -> dict:
        refs = args.get("refs")
        if not isinstance(refs, list) or not refs or len(refs) > 20 or not all(isinstance(r, str) for r in refs):
            return {"error": "refs must be a list of 1 to 20 location refs from the quests or dungeon tools"}
        for ref in refs:
            if ref not in self.places and ref not in self.routes:
                self._revive(ref)
        unknown = [r for r in refs if r not in self.places and r not in self.routes]
        if unknown:
            return {"error": f"unknown refs: {', '.join(unknown[:5])}. Use refs exactly as a tool returned them; if it came from an "
                             "earlier answer and is not known any more, call that tool again and use the ref it gives now."}
        shown, points = [], []
        for ref in dict.fromkeys(refs):
            if ref in self.routes:   # a route is one line on the card and a waypoint for each of its stops
                route = self.routes[ref]
                shown.append({"addon": "TomTom", "text": t("Route: {title}", title=route["title"]), "detail": route["detail"]})
                points += route["stops"]
            else:
                p = self.places[ref]
                shown.append({"addon": "TomTom", "text": t("Waypoint: {title}", title=p["title"]), "detail": f"{p['zone']}  {p['x']}, {p['y']}"})
                points.append(p)
        if len(points) > 50:
            return {"error": "that is more than 50 waypoints at once: ask for fewer routes"}
        card = {
            "id": f"card{len(self.cards) + 1}",
            "lines": shown,
            "wisp": encode_wisp1([{"do": "tomtom.waypoint", "map": p["map"], "x": p["x"], "y": p["y"], "title": p["title"]}
                                  for p in points]),
        }
        self.cards.append(card)
        lines = shown
        return {"shown": len(lines), "note": "A card with a Copy button is now shown to the player. Nothing has happened "
                "in the game yet: the player must copy it and paste it into the game, and you cannot see whether they did."}

    def tool_recipes(self, args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        books = self.crafting.books
        if not books:
            return {"error": "No recipes have been read yet. The game only shows a profession's recipes while its window "
                             "is open: the player opens the profession window once, logs out, and asks again.",
                    "professions": self.snapshot.get("professions")}
        wanted, show, search = args.get("profession"), args.get("show", "skill_up"), args.get("search")
        if show not in ("skill_up", "all"):
            return {"error": "show must be skill_up or all"}
        if isinstance(search, str) and search.strip():   # one recipe by name, learned or not, and how to come by it
            words = _clean(search, 80).lower().split()
            hits = [r for r in [r for b in books for r in b["recipes"]] + self.crafting.world_recipes   # and every other recipe in the game
                    if all(w in (r["name"] + " " + (self.crafting.name(r["makes"]) if r["makes"] else "")).lower() for w in words)]
            if not hits:
                return {"found": 0, "note": "No recipe of the professions read so far is named like that ("
                                            + ", ".join(b["profession"] for b in books) + "). The game lists a profession's recipes, "
                                            "learned or not, while its window is open. For the recipe item itself, try item_links."}
            hits.sort(key=lambda r: (bool(r.get("world")), bool(r["owner"]), not r["learned"]))   # this character's own first, then an alt's, then the rest
            return {"found": len(hits), "recipes": [
                {**self.crafting.describe(r), "profession": r["profession"],
                 **({} if r["learned"] else {"how_to_learn": self.graph.learn_from(r) or "not known: a trainer may teach it"})}
                for r in hits[:12]],
                "note": "how_to_learn is for recipes not learned yet: the_game_says is the game's own text; recipe_items are the "
                        "items that teach it, from the Questie addon, with who sells or drops each and places that have refs "
                        "for the waypoint tool. Vendors of the other faction are left out."}
        if isinstance(wanted, str) and wanted.strip():
            books = [b for b in books if b["profession"].lower() == wanted.strip().lower()]
            if not books:
                return {"error": f"no recipes read for {wanted.strip()}. Read so far: "
                                 + ", ".join(b["profession"] for b in self.crafting.books)}
        out = []
        for book in books:
            listed = [r for r in book["recipes"] if show == "all" or (r["learned"] and r["colour"] != "grey")]
            listed.sort(key=lambda r: ("orange", "yellow", "green", None, "grey").index(r["colour"]))
            entry = {"profession": book["profession"], **({"character": book["owner"]} if book["owner"] else {}),
                     "skill": book["skill"], "max_skill": book["max"],
                     "recipes_known_to_wisp": len(book["recipes"]), "recipes": [self.crafting.describe(r) for r in listed[:80]]}
            if isinstance(book["seen_at"], (int, float)):
                entry["list_read_hours_ago"] = round(max(0, time.time() - book["seen_at"]) / 3600, 1)
            if len(listed) > 80:
                entry["left_out"] = len(listed) - 80
            out.append(entry)
        return {"professions": out,
                "note": "Read from the game while the profession window was open, on whichever of the player's characters has the "
                        "profession: a book with 'character' belongs to another character of theirs (crafter on a recipe), who "
                        "must do the crafting; bags and bank counted are the last-played character's. A recipe gives skill on every craft "
                        "while orange, less often while yellow or green, never once grey. Where colour_read_at_skill is "
                        "given the colour is older than the current skill and may have dropped. Prices are from the "
                        "player's own Auctionator scans; in_bags does not count the bank. result_listed_for is the "
                        "lowest price the crafted item was listed at and profit_per_craft what is left after buying "
                        "every material at auction: before the auction house's cut, and a listing is not a sale. For "
                        "any totals, call craft_plan."}

    def tool_auction_prices(self, args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        names = args.get("items")
        if not isinstance(names, list) or not 1 <= len(names) <= 20 or not all(isinstance(n, str) for n in names):
            return {"error": "items must be a list of 1 to 20 item names"}
        craft = self.crafting
        if not craft.prices:
            return {"error": "There are no auction prices. They come from the Auctionator addon's own scans: "
                             + ("the player scans the auction house with it, then logs out." if craft.has_auctionator
                                else "it was not installed at the last logout.")}
        out = []
        for name in names:
            found = craft.find(name)
            entry = {"asked": _clean(name, 80), "found": []}
            for item in found:
                line = {"item": craft.name(item), "lowest_price": coins(craft.prices[item][0]), "seen": craft.seen(item)}
                if craft.bags.get(item):
                    line["in_bags"] = craft.bags[item]
                    line["bags_worth"] = coins(craft.prices[item][0] * craft.bags[item])
                past = self.market.series(self.snapshot, item, 30) if self.market else []
                if len(past) >= 3:
                    usual = statistics.median(low for _, low, _, _ in past)
                    line["usual_price_30_days"] = coins(usual)
                    line["now_against_usual"] = f"{(craft.prices[item][0] - usual) / usual:+.0%}"
                    line["days_with_a_scan"] = len(past)
                entry["found"].append(line)
            if not found:
                entry["note"] = "no price under that name"
            out.append(entry)
        return {"prices": out, "items_with_a_price": len(craft.prices),
                "note": "From the player's own Auctionator scans: the lowest price it saw listed, not a sale, and before "
                        "the auction house's cut. Names are as the player's game writes them. 'seen' is counted from "
                        "the last logout. An item with no price was not at the auction house when Auctionator looked, "
                        "or is spelled differently. For how a price has moved and where it is heading, call price_history."}

    def _no_market(self) -> dict | None:
        if not self.snapshot:
            return {"error": self.problem}
        if self.market is None or not self.market.size(self.snapshot)["rows"]:
            return {"error": "Wisp has no price history yet. It is built from the Auctionator addon's scans: "
                             + ("the player scans the auction house, logs out, and every scan day from then on is kept."
                                if self.crafting.has_auctionator else "the addon was not installed at the last logout.")}
        return None

    def tool_price_history(self, args: dict) -> dict:
        problem = self._no_market()
        if problem:
            return problem
        name, days = args.get("item"), args.get("days", 30)
        if not isinstance(name, str) or not name.strip():
            return {"error": "item must be an item name"}
        if not isinstance(days, int) or isinstance(days, bool) or not 2 <= days <= 3650:
            return {"error": "days must be a whole number from 2 to 3650"}
        found = self.market.find(_clean(name, 80))
        if not found:
            return {"error": f"no item named like '{_clean(name, 80)}' has been scanned. Names are as the player's game writes them."}
        if len(found) > 1:
            return {"several_items_match": [n for _, n in found], "note": "Ask again with one of these names exactly."}
        item, item_name = found[0]
        out = self.market.history(self.snapshot, item, item_name, days)
        chart = out.pop("_chart", None)
        if args.get("show") is True and chart:
            self.cards.append({"id": f"card{len(self.cards) + 1}", "lines": [], "chart": chart})
            out["shown"] = "a chart card with these figures is now shown to the player: do not repeat them in your text"
        if self.crafting.bags.get(item):
            out["in_bags"] = self.crafting.bags[item]
        out["kept_by_wisp"] = self.market.size(self.snapshot)
        out["note"] = ("Lowest listed price per scan day, from the player's own Auctionator scans; a day without a scan has "
                       "no entry. A listing is not a sale and the auction house takes a cut. next_week_estimate is a plain "
                       "statistical estimate: give it as a range, with its confidence, never as a promise.")
        return out

    def tool_market_query(self, args: dict) -> dict:
        problem = self._no_market()
        if problem:
            return problem
        sql = args.get("sql")
        if not isinstance(sql, str) or not sql.strip() or len(sql) > 4000:
            return {"error": "sql must be one SELECT statement of at most 4000 characters"}
        out = self.market.query(self.snapshot, sql)
        if "error" not in out:
            out["note"] = ("Prices are in copper unless you used coins(). Lowest listings on the player's scan days: a "
                           "listing is not a sale. State results as they are; do not do further arithmetic on them yourself.")
        return out

    def tool_market_scan(self, args: dict) -> dict:
        problem = self._no_market()
        if problem:
            return problem
        kind, days, limit = args.get("find"), args.get("days", 30), args.get("limit", 15)
        if kind not in SCANS:
            return {"error": "find must be one of: " + ", ".join(SCANS)}
        if not isinstance(days, int) or isinstance(days, bool) or not 5 <= days <= 3650:
            return {"error": "days must be a whole number from 5 to 3650"}
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 40:
            return {"error": "limit must be a whole number from 1 to 40"}
        only, craft = None, self.crafting
        among = args.get("among", "everything")
        if among == "my_bags":
            only = set(craft.bags)
        elif among == "my_materials":
            only = {item for r in craft.recipes.values() for item, _ in r["reagents"]}
        elif among == "what_i_craft":
            only = {r["makes"] for r in craft.recipes.values() if r["makes"] and r["learned"]}
        elif among != "everything":
            return {"error": "among must be everything, my_bags, my_materials or what_i_craft"}
        name = _clean(args.get("name_contains"), 60) if isinstance(args.get("name_contains"), str) else ""
        found = self.market.scan(self.snapshot, kind, days, limit, name, only)
        for entry in found:
            item = next((i for i, n in craft.names.items() if n == entry["item"]), None)
            if item and craft.bags.get(item):
                entry["in_bags"] = craft.bags[item]
        shown = {}
        if args.get("show") is True and found:
            self.cards.append({"id": f"card{len(self.cards) + 1}", "lines": [], "table": {
                "title": t(SCAN_TITLES[kind]), "sub": t("last {days} days", days=days) + ({"my_bags": t(" · my bags"), "my_materials": t(" · my materials"), "what_i_craft": t(" · what i craft")}[among] if only is not None else ""),
                "columns": [t("Item"), t("Now"), t("Usual"), t("Against usual"), t("Listed")],
                "rows": [[e["item"], e["now"], e["usual"], e["now_against_usual"], e.get("usually_listed", "–")] for e in found],
                "notes": [t("Lowest listings from your own Auctionator scans. A listing is not a sale.")]}})
            shown = {"shown": "a table card with these rows is now shown to the player: do not repeat them in your text"}
        return {"find": kind, "what_it_means": SCANS[kind], "items": found, "kept_by_wisp": self.market.size(self.snapshot), **shown,
                "note": "Ranked in code over every item with at least 4 scan days in the period, seen in the last 3 days and "
                        "usually worth 1s or more. Lowest listed prices from the player's own scans: a listing is not a "
                        "sale, and thinly traded items can show big moves that mean nothing. For one item in depth, call "
                        "price_history." + ("" if found else " Nothing stood out; with only a few scan days that is normal.")}

    def tool_item_links(self, args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        name = args.get("item")
        if not isinstance(name, str) or not name.strip():
            return {"error": "item must be an item name"}
        found = self.graph.find(_clean(name, 80))
        if not found:
            return {"error": f"Wisp knows no item named like '{_clean(name, 80)}'. Names are as the player's game writes them."
                             + ("" if self.graph.world else " So far it knows only the items the character carries, crafts with, crafts, "
                                "is asked for by quests or has a price for: Questie's whole item list is read by Wisp's addon in the "
                                "background about a minute after login, and is here after the next logout.")}
        if len(found) > 1:
            return {"several_items_match": [self.graph.names[i] for i in found], "note": "Ask again with one of these names exactly."}
        out = self.graph.around(found[0], self.crafting)
        if len(out) == 1:
            out["nothing_known"] = "Wisp has the name but no links for this item."
        addons = self.snapshot.get("addons") or {}
        out["note"] = ("One step in every direction from this item. Follow a link with the tool for that kind of thing: "
                       "recipes and craft_plan for a recipe, quests or dungeon for a quest or a boss, price_history for the "
                       "price, item_links again for another item. Every place has a ref for the waypoint tool. Recipes are "
                       "only those of the character's own professions; sellers, drops and quest needs come from the Questie "
                       "addon, the first few of each, and never the other faction's vendors"
                       + ("." if addons.get("questie") else "; Questie was not available at the last logout, so those are missing."))
        return out

    def tool_report(self, args: dict) -> dict:
        """The end of a research run: a structured report, shown as a card and kept as a note.
        Its sources are checked here: only pages this conversation was really given can be cited."""
        title, summary = _clean(args.get("title"), 80), _clean(args.get("summary"), 500)
        sections, steps = args.get("sections"), args.get("next_steps") or []
        if not title or not summary:
            return {"error": "the report needs a title and a summary"}
        if not isinstance(sections, list) or not 1 <= len(sections) <= 8 or not isinstance(steps, list) or len(steps) > 8:
            return {"error": "the report needs 1 to 8 sections and at most 8 next steps"}
        order, dropped, laid = {}, [], []
        for section in sections:
            points = section.get("points") if isinstance(section, dict) else None
            heading = _clean(section.get("heading"), 80) if isinstance(section, dict) else ""
            if not heading or not isinstance(points, list) or not 1 <= len(points) <= 10:
                return {"error": "every section needs a heading and 1 to 10 points"}
            out = []
            for point in points:
                text = _clean(point.get("text"), 500) if isinstance(point, dict) else ""
                urls = point.get("sources") or [] if isinstance(point, dict) else None
                if not text or not isinstance(urls, list) or len(urls) > 6:
                    return {"error": "every point needs text, and at most 6 source URLs"}
                refs = []
                for url in urls:
                    if isinstance(url, str) and url in self.web_seen:
                        refs.append(order.setdefault(url, len(order) + 1))
                    else:
                        dropped.append(str(url)[:200])
                out.append({"text": text, "refs": sorted(set(refs))})
            laid.append({"heading": heading, "points": out})
        steps = [_clean(step, 300) for step in steps if isinstance(step, str) and _clean(step, 300)]
        sources = [{"n": n, "title": _clean(self.web_seen[url], 120) or url, "url": url, "site": url.split("//", 1)[-1].split("/", 1)[0].removeprefix("www.")}
                   for url, n in order.items()]
        report = {"title": title, "summary": summary, "sections": laid, "next_steps": steps, "sources": sources,
                  "basis": t("Your own game data") + (t(" and {pages}", pages=tn(len(sources), "{n} web page", "{n} web pages")) if sources
                                                      else t(", no web pages"))}
        saved = None
        if self.notes_dir is not None:
            lines = [f"# {title}", "", time.strftime("%Y-%m-%d %H:%M") + " · " + report["basis"], "", summary, ""]
            for section in laid:
                lines += [f"## {section['heading']}", ""]
                lines += ["- " + p["text"] + "".join(f" [{n}]" for n in p["refs"]) for p in section["points"]] + [""]
            if steps:
                lines += ["## Next steps", ""] + [f"{i + 1}. {step}" for i, step in enumerate(steps)] + [""]
            if sources:
                lines += ["## Sources", ""] + [f"{src['n']}. {src['title']} — {src['url']}" for src in sources] + [""]
            try:
                self.notes_dir.mkdir(parents=True, exist_ok=True)
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "report"
                saved = self.notes_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.md"
                saved.write_text("\n".join(lines), encoding="utf-8")
                report["saved"] = t("Kept in your notes")
            except OSError:
                saved = None
        self.cards.append({"id": f"card{len(self.cards) + 1}", "lines": [], "report": report})
        out = {"shown": "the report, as a card" + (", and kept as a note" if saved else ""),
               "note": "The player now sees the report. Do not repeat it: add one short sentence at most."}
        if dropped:
            out["sources_left_out"] = dropped[:10]
            out["why"] = "These were not among the pages the web tools returned in this conversation, so they are not cited."
        return out

    def tool_notes(self, args: dict) -> dict:
        folder = self.notes_dir
        files = sorted(folder.glob("*.md"), reverse=True) if folder is not None and folder.is_dir() else []
        if not files:
            return {"notes": [], "note": "No research has been kept yet. A /research run ends in a report that is kept here."}

        def heading(file):
            try:
                return file.read_text(encoding="utf-8").split("\n", 1)[0].lstrip("# ").strip()[:80]
            except OSError:
                return file.stem
        wanted = args.get("read")
        if isinstance(wanted, str) and wanted.strip():
            wanted = wanted.strip().lower()
            for file in files:
                if wanted == file.stem.lower() or wanted in heading(file).lower():
                    return {"note": file.stem, "text": file.read_text(encoding="utf-8")[:8000],
                            "remember": "This is an earlier report. Its prices and advice are as old as its date."}
            return {"error": "no kept note matches that", "notes": [heading(f) for f in files[:20]]}
        return {"notes": [{"note": f.stem, "title": heading(f), "kept": f.stem[:8]} for f in files[:30]],
                "note": "Read one with read set to its title or its note name."}

    def tool_gathering(self, args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        gather = self.gathering
        if not gather.nodes:
            return {"error": "No gathering nodes are known. They come from the GatherMate2 addon: "
                             + ("it is installed but has nothing on record yet. It notes every herb, vein and pool the "
                                "player gathers from; its companion GatherMate2_Data can import a ready-made set."
                                if gather.installed else "it was not installed at the last logout.")}
        what = _clean(args.get("what"), 80) if isinstance(args.get("what"), str) else ""
        zone = _clean(args.get("zone"), 60) if isinstance(args.get("zone"), str) else ""
        note = ("Places the player's GatherMate2 addon has on record, found by the player or imported: where a node can "
                "appear, not whether it is there now. Zones the player has not gathered in look empty.")
        names = gather.find(what) if what else []
        if what and not names:
            return {"error": f"no node named like '{what}' is on record. Known: " + ", ".join(sorted(gather.nodes)[:40])}
        if len(names) > 1:
            return {"several_nodes_match": names, "note": "Ask again with one of these names exactly."}
        maps = gather.zone(zone) if zone else []
        if zone and not maps:
            return {"error": f"no nodes are on record in a zone named like '{zone}'"}
        if len(maps) > 1:
            return {"several_zones_match": [gather.zone_name(m) for m in maps], "note": "Ask again with one of these zones exactly."}
        if not names and not maps:   # the lie of the land
            kinds = {}
            for name, node in gather.nodes.items():
                kinds.setdefault(node["kind"], []).append({"node": name, "known_nodes": sum(len(z["at"]) for z in node["zones"].values()),
                                                           "zones": len(node["zones"])})
            return {"known_nodes": gather.total, "by_profession": {k: sorted(v, key=lambda n: -n["known_nodes"])[:60] for k, v in kinds.items()},
                    "note": note + " Ask with what for the zones a node is in, with zone for what a zone holds."}
        if not names:
            return {"zone": gather.zone_name(maps[0]), "gatherable": gather.in_zone(maps[0]),
                    "note": note + " Ask again with what as well for a route."}
        name = names[0]
        zones = gather.where(name)
        if not maps:
            return {"node": name, "profession": gather.nodes[name]["kind"], "zones": [{k: v for k, v in z.items() if k != "map"} for z in zones[:12]],
                    "note": note + " Ask again with a zone for a route through its nodes."}
        map_id = maps[0]
        if map_id not in gather.nodes[name]["zones"]:
            return {"error": f"no {name} is on record in that zone", "zones_with_it": [z["zone"] for z in zones[:8]]}
        place = gather.nodes[name]["zones"][map_id]
        ref, stops, start = self._gather_route(name, map_id)
        return {"node": name, "zone": place["zone"], "known_nodes": len(place["at"]),
                "route": {"ref": ref, "stops": len(stops), "starts": "from where the character stands" if start else "at the richest spot",
                          "first_stops": [{"x": s["x"], "y": s["y"], "nodes": s["nodes"]} for s in stops[:5]]},
                "note": note + " Give the route's ref to the waypoint tool to show the whole route as one card: nodes close "
                               "together are one stop, and each stop follows the nearest one before it."}

    def tool_farming(self, args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        farm, view, days = self.farming, args.get("view"), args.get("days", 30)
        if view not in ("spots", "sources", "gathering"):
            return {"error": "view must be spots, sources or gathering"}
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 60:
            return {"error": "days must be a whole number from 1 to 60"}
        priced = ("Worth is the lowest auction listing where there is one, else what a vendor pays: what the loot is listed "
                  "for, not what it sold for, and selling a lot at once pushes a price down.")
        zone = _clean(args.get("zone"), 60) if isinstance(args.get("zone"), str) else ""
        table = None
        if view == "gathering":
            if not self.gathering.nodes:
                return self.tool_gathering({})
            maps = self.gathering.zone(zone) if zone else []
            if zone and len(maps) != 1:
                return {"error": f"no single zone with nodes on record is named like '{zone}'"}
            ranked, unknown = farm.gathering_worth(maps[0] if maps else None)
            out = {"view": "gathering", "worth": [{k: v for k, v in e.items() if k[0] != "_"} for e in ranked[:25]],
                   "nodes_without_a_price": unknown[:30],
                   "note": "The nodes GatherMate2 has on record, priced. worth_all is every known node of a zone gathered once: "
                           "a way to compare zones, not an hourly rate, since it says nothing of respawns, competition or the "
                           "walking. " + priced + " For the route through one, call gathering with what and zone."}
            table = {"title": t("What known nodes are worth"), "sub": maps and self.gathering.zone_name(maps[0]) or t("every zone on record"),
                     "columns": [t("Node"), t("Zone"), t("Nodes"), t("Each"), t("All")],
                     "rows": [[e["node"], e["zone"], e["known_nodes"], e["worth_each"], e["worth_all"]] for e in ranked[:15]],
                     "notes": [t("Every known node gathered once, at your auction prices. Not an hourly rate.")]}
        else:
            if not farm.records:
                return {"error": "No looting has been measured yet. From now on Wisp's addon notes what the character loots "
                                 "(what, from what, where, and the time it took) and it arrives here at each logout. There are "
                                 "no drop rates for ordinary loot anywhere else: for places the player has not farmed, say so; "
                                 "a research run can look for what other players report."}
            found = farm.zone(zone) if zone else []
            if zone and len(found) != 1:
                return {"error": f"no single zone the character has looted in is named like '{zone}'",
                        "zones": sorted({r["zone"] for r in farm.records})}
            if view == "spots":
                spots = farm.spots(days)
                out = {"view": "spots", "spots": [{k: v for k, v in e.items() if k[0] != "_"} for e in spots],
                       "note": "Measured from the character's own looting in the last " + f"{days} days. time_looting is the time "
                               "between one loot and the next when under five minutes, so travel and long fights are partly "
                               "left out and per_hour is a little generous. " + priced}
                table = {"title": t("Your farming spots"), "sub": t("last {days} days, measured from your own loot", days=days),
                         "columns": [t("Zone"), t("Time"), t("Looted"), t("Total"), t("Per hour")],
                         "rows": [[e["zone"], e["time_looting"], e["looted"], e["total"], e["per_hour"]] for e in spots[:15]],
                         "notes": [t("Coins plus loot at your auction prices, or what a vendor pays. Listed worth, not sales.")]}
            else:
                sources = farm.sources(days, found[0][0] if found else None)
                out = {"view": "sources", "sources": [{k: v for k, v in e.items() if k[0] != "_"} for e in sources[:30]],
                       "note": "Measured from the character's own looting: per_loot is how many of the item came per corpse or "
                               "node looted. With few looted it is rough: say how many it rests on. " + priced}
                table = {"title": t("What you looted from what"),
                         "sub": t("{where}, last {days} days", where=found[0][1] if found else t("everywhere"), days=days),
                         "columns": [t("Source"), t("Zone"), t("Looted"), t("Per loot")],
                         "rows": [[e["source"], e["zone"], e["looted"], e["worth_per_loot"]] for e in sources[:15]],
                         "notes": [t("Worth per corpse or node looted, at your auction prices.")]}
        if args.get("show") is True and table and table["rows"]:
            self.cards.append({"id": f"card{len(self.cards) + 1}", "lines": [], "table": table})
            out["shown"] = "a table card with these rows is now shown to the player: do not repeat them in your text"
        return out

    def _one_item(self, args: dict):
        """The item a tool was asked about, or the answer to give instead."""
        name = _clean(args.get("item"), 80) if isinstance(args.get("item"), str) else ""
        if not name:
            return None, {"error": "item must be an item name"}
        found = self.graph.find(name)
        if not found:
            return None, {"error": f"Wisp knows no item named like '{name}'. Names are as the player's game writes them."}
        if len(found) > 1:
            return None, {"several_items_match": [self.graph.names[i] for i in found], "note": "Ask again with one of these names exactly."}
        return found[0], None

    def _recipes_note(self) -> str:
        return ("Recipes are every recipe in the game, read from the client by Wisp's addon, whatever professions the player has."
                if self.recipes_read else
                "Only the recipes of the player's own characters' professions are known so far: Wisp's addon reads every recipe in "
                "the game in the background about a minute after login, and they are here after the next /wisp sync or logout.")

    def tool_craft_tree(self, args: dict) -> dict:
        """Down the graph: a product broken into its raw materials through every recipe on the way."""
        if not self.snapshot:
            return {"error": self.problem}
        item, instead = self._one_item(args)
        if instead:
            return instead
        quantity = args.get("quantity", 1)
        if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 10000:
            return {"error": "quantity must be a whole number from 1 to 10000"}
        from .tree import breakdown
        out = breakdown(self, item, quantity)
        if out.get("error") == "no recipe":
            return {"error": f"No recipe known to Wisp makes {self.graph.names[item]}: it is a raw material, or its recipe is not known. "
                             + self._recipes_note() + " For where it comes from, use item_links."}
        card, buy = out.pop("_card"), out.pop("_buy")
        if args.get("show") is True:
            shown = {"id": f"card{len(self.cards) + 1}", "lines": [], "heading": t("Shopping list, down to raw materials"), "table": card}
            if buy:   # the same card creates the shopping list for exactly what is still missing
                shown["wisp"] = encode_wisp1([{"do": "auctionator.shopping_list", "name": _clean(out["product"], 40).replace(";", "") or "Wisp",
                                               "items": [{"name": name, "quantity": count, "id": item} for item, name, count in buy[:60]]}])
                shown["hint"] = t("Shopping list for Auctionator: Copy, then in the game Ctrl+V twice")
            self.cards.append(shown)
            out["shown"] = ("a card is now shown to the player: every material with what is needed, what is already held and what to buy, "
                            "the order of crafts, and" + (" a button that creates the shopping list for the rest in Auctionator" if buy
                                                          else " no button, since nothing needs buying") + ". Do not repeat its rows in your text")
        out["note"] = ("Worked out in code. What the player already holds is taken first, parts included (a held intermediate needs "
                       "neither making nor buying): their bags and bank, the bank their characters share, then each other character's "
                       "bags and bank, as each was at its own last logout; already_held says where. to_buy is what is left. who_can is "
                       "information only: another player can do a craft for them, so a missing profession is no obstacle. Prices "
                       "are the lowest listings from the player's own scans. A craft that makes several at once may leave some over. "
                       + self._recipes_note())
        return out

    def tool_item_uses(self, args: dict) -> dict:
        """Up the graph: everything an item goes into, directly and through what is made from it."""
        if not self.snapshot:
            return {"error": self.problem}
        item, instead = self._one_item(args)
        if instead:
            return instead
        from .tree import uses
        out = uses(self, item)
        card = out.pop("_card")
        if args.get("show") is True and card["rows"]:
            self.cards.append({"id": f"card{len(self.cards) + 1}", "lines": [], "heading": t("Where it is used"), "table": card})
            out["shown"] = "a card with the products is now shown to the player: do not repeat them in your text"
        out["note"] = ("Worked out in code. direct_recipes use the item itself; end_products are everything it ends up in, through "
                       "what is made from it, dearest first, with how many of the item one product takes in all. To judge what the "
                       "item is worth, set its price against what its products are listed for and what else they take, how many "
                       "recipes and professions want it, and how easily it is come by (a vendor selling it caps its price; a drop "
                       "from few creatures keeps it scarce); price_history and market_depth say how its market has behaved. "
                       "Listings are not sales. " + self._recipes_note())
        return out

    def tool_craft_plan(self, args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        plan = self.crafting.plan(args.get("crafts"))
        plan.pop("_buy", None)
        plan.pop("_card", None)
        return plan

    def tool_shopping_list(self, args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        crafts, items = args.get("crafts"), args.get("items")
        if (crafts is None) == (items is None):
            return {"error": "give either crafts (to buy what a plan lacks) or items, not both"}
        craft, unchecked, plan = self.crafting, [], None
        if crafts is not None:
            plan = craft.plan(crafts)
            if "error" in plan:
                return plan
            buy = [(item, craft.name(item), count) for item, count in plan["_buy"]]
        else:
            if not isinstance(items, list) or not 1 <= len(items) <= 60:
                return {"error": "items must be a list of 1 to 60 {name, quantity}"}
            known = {name.lower(): item for item, name in craft.names.items()}
            buy = []
            for entry in items:
                name = _clean(entry.get("name"), 80).replace(";", "") if isinstance(entry, dict) else ""
                count = entry.get("quantity") if isinstance(entry, dict) else None
                if not name or not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 100000:
                    return {"error": "every item needs a name and a whole quantity from 1 to 100000"}
                item = known.get(name.lower())
                if item is None:
                    unchecked.append(name)
                buy.append((item, craft.name(item) if item else name, count))
        title = _clean(args.get("name"), 40).replace(";", "") or "Wisp"
        total = sum(craft.prices[item][0] * count for item, _, count in buy if item in craft.prices)
        priced = sum(1 for item, _, _ in buy if item in craft.prices)
        about = (t("about {sum}", sum=coins(total)) + ("" if priced == len(buy) else t(" · {n} without a price", n=len(buy) - priced))
                 if priced else t("prices unknown"))
        header = {"addon": "Auctionator", "text": t("Shopping list: {title}", title=title),
                  "detail": t("{items} · {about}", items=tn(len(buy), "{n} item", "{n} items"), about=about)}
        card = {"id": f"card{len(self.cards) + 1}", "lines": []}
        if plan:   # the whole plan, laid out: the steps, a table of materials, the total
            card["plan"] = {"title": title, **plan["_card"]}
            card["hint"] = t("Shopping list for Auctionator: Copy, then in the game Ctrl+V twice")
        else:
            card["lines"] = [header] + [{"text": f"{count} × {name}",
                                         "detail": t("{price} each", price=coins(craft.prices[item][0])) if item in craft.prices else ""}
                                        for item, name, count in buy]
        if buy:
            card["wisp"] = encode_wisp1([{"do": "auctionator.shopping_list", "name": title,
                                          "items": [{"name": name, "quantity": count, **({"id": item} if item else {})}
                                                    for item, name, count in buy]}])
        self.cards.append(card)
        if not buy:
            return {"shown": "the plan, as a card with its steps and materials", "list": [],
                    "note": "Nothing needs buying: the bags and the plan's own crafts cover every material, so the card "
                            "has no Copy button. Do not repeat the card's steps and numbers in your text."}
        out = {"shown": "the plan as a card: steps, a materials table, the cost, and a Copy button" if plan else len(buy),
               "list": [f"{count} × {name}" for _, name, count in buy],
               "note": "A card with a Copy button is now shown to the player. Nothing has happened in the game yet: the "
                       "player copies it, pastes it into the game, and the list appears in Auctionator's Shopping tab."
                       + (" Do not repeat the card's steps and numbers in your text." if plan else "")}
        if unchecked:
            out["names_wisp_could_not_check"] = unchecked
        if not craft.has_auctionator:
            out["warning"] = "The Auctionator addon was not installed at the last logout. The list needs it."
        return out


SCANS = {
    "below_usual": "priced well under their own usual level now: candidates to buy",
    "above_usual": "priced well over their own usual level now: candidates to sell",
    "rising": "prices climbing over the last two weeks",
    "falling": "prices dropping over the last two weeks",
    "swings": "prices that move a lot between a low and a high: candidates to buy low and sell high",
    "most_listed": "the items with the most listings: the busiest markets",
}

SCAN_TITLES = {"below_usual": "Cheaper than usual", "above_usual": "Dearer than usual", "rising": "Rising", "falling": "Falling",
               "swings": "Biggest swings", "most_listed": "Most listed"}

QUALITY = {0: "poor", 1: "common", 2: "uncommon", 3: "rare", 4: "epic", 5: "legendary"}

TOOLS = [
    {
        "name": "character",
        "description": "The player's character as of their last logout: class, race, level, faction, zone and "
                       "position, gold, game version, and how old this snapshot is. Call it when the answer depends "
                       "on who the character is.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "quests",
        "description": "The player's quest log as of their last logout. For each quest: objectives and their "
                       "progress, who starts it and who it is handed in to with their locations, the game's own "
                       "pointer to the next step, and the next quest in the chain. Every location has a 'ref'. "
                       "This is the only source of quest facts and locations: never state a location, an NPC or a "
                       "quest id from memory.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "available_quests",
        "description": "The quests the character could pick up now and has not, from the Questie addon: level, kind "
                       "(normal, elite for group quests, dungeon, raid, pvp), who gives each and where, the items it "
                       "asks for. With nothing: how many are open in each zone. With zone (a name, or 'here' for "
                       "where the character stands): that zone's quests, nearest first. With kind: only that kind. "
                       "Use it for 'what quests are there here', 'what elite quests can I do', 'where should I "
                       "quest next'. For one particular quest, or quests out of reach, use quest_search. "
                       "Never name a quest from memory.",
        "input_schema": {
            "type": "object",
            "properties": {"zone": {"type": "string"},
                           "kind": {"type": "string", "enum": ["any", "normal", "elite", "dungeon", "raid", "pvp"]},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 60}},
            "additionalProperties": False,
        },
    },
    {
        "name": "leveling",
        "description": "How fast the character levels, measured from their own play: experience per hour, the time "
                       "to the next level at that pace, the rested bonus, what share came from quests, and day by "
                       "day. Use it for 'how fast am I leveling', 'when will I reach the next level', 'is questing "
                       "or grinding better for me'.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 60, "description": "How far back. Default 14."},
                           "show": {"type": "boolean", "description": "true to also show the player a table card."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "questing_route",
        "description": "Show the player what to do in a zone, as a route card: the quests to hand in, the open quests "
                       "worth picking up (not grey, not red), and the quests under way, with the stops ordered to "
                       "walk them and a Copy button that sets a waypoint for each. Leave zone out for the zone the "
                       "character is in. Use it for 'what should I do now', 'what next here', 'give me a route'.",
        "input_schema": {
            "type": "object",
            "properties": {"zone": {"type": "string", "description": "A zone name. Leave out for where the character stands."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "gold",
        "description": "What the character's own things would fetch at auction. bags: every item carried that can be "
                       "sold, with its listing, its worth, what a vendor pays, and whether it is above or below its "
                       "usual price. crafts: the character's recipes ranked by profit per craft at current listings. "
                       "Use it for 'what should I sell', 'what is my stuff worth', 'what can I craft for money'.",
        "input_schema": {
            "type": "object",
            "properties": {"view": {"type": "string", "enum": ["bags", "crafts"]},
                           "show": {"type": "boolean", "description": "true to also show the player the result as a table card."}},
            "required": ["view"], "additionalProperties": False,
        },
    },
    {
        "name": "investments",
        "description": "The player's investments: things bought to sell later. list (the default) shows every "
                       "position: how many are held, what was paid, what they are worth at today's listing, the "
                       "gain, what sales have brought, and how the price has behaved since. With an item, that one "
                       "in depth with its trades. buy and sell record a trade: the item, how many, and the price "
                       "exactly as the player gave it, as price_each or as total for the lot; leave both out only "
                       "if the player gave no price, and today's listing is taken and marked as assumed. remove "
                       "deletes one trade by its number, or an item's whole record. Record a trade only when the "
                       "player says they made it. Never work out gains yourself: this does.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "buy", "sell", "remove"]},
                "item": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 1, "maximum": 1000000},
                "price_each": {"type": "string", "description": "For one, with units, as the player said it: '3s 50c'."},
                "total": {"type": "string", "description": "For the whole lot, with units: '6g'."},
                "days_ago": {"type": "integer", "minimum": 0, "maximum": 730, "description": "When it happened. Default 0, today."},
                "note": {"type": "string"},
                "trade": {"type": "integer", "description": "For remove: the trade's number."},
                "from_mail": {"type": "integer", "description": "For buy or sell: the number of an auction mail. The item, the quantity "
                                                                "and the price are then taken from the mail, and nothing else is needed."},
                "show": {"type": "boolean", "description": "true to also show the player a card: the table, or with an item its chart."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "live_auctions",
        "description": "Every listing of one item in the player's auction house right now, straight from Blizzard's own "
                       "published auction data (refreshed about hourly): the lowest price, how many are listed, and the "
                       "price ladder. Needs no scan in the game. It works once the player has entered their own free "
                       "Blizzard developer client in Settings; if they have not, it says how, and you pass that on. "
                       "Prefer it over auction_prices for 'what does X cost now' when it works; use it too when the "
                       "player asks about the Blizzard API or live auction data.",
        "input_schema": {
            "type": "object", "properties": {"item": {"type": "string"}},
            "required": ["item"], "additionalProperties": False,
        },
    },
    {
        "name": "market_depth",
        "description": "How deep an item's market is: how many are listed at each price above the lowest, the "
                       "biggest block of listings, and where the player's own auctions stand. With quantity: what "
                       "buying that many would cost at most. With price: how many are listed for less than it. "
                       "Use it for 'how many are cheaper than mine', 'can I buy 200 without moving the price', "
                       "'where should I list', 'is it worth buying out the cheap ones'. Known after a full "
                       "Auctionator scan or a search for the item.",
        "input_schema": {
            "type": "object",
            "properties": {"item": {"type": "string"},
                           "quantity": {"type": "integer", "minimum": 1, "description": "How many the player wants to buy."},
                           "price": {"type": "string", "description": "A price for one, with units, to count the listings under it: '4s 20c'."},
                           "show": {"type": "boolean", "description": "true to also show the player the ladder as a table card."}},
            "required": ["item"], "additionalProperties": False,
        },
    },
    {
        "name": "my_auctions",
        "description": "What the character has up for sale at the auction house right now: each auction's quantity, "
                       "price for one, time left, whether it has a bid or has sold, and whether it is still the lowest "
                       "or has been undercut, against the lowest listing Auctionator saw. Use it for 'what do I have "
                       "listed', 'have I been undercut', 'should I repost'.",
        "input_schema": {
            "type": "object",
            "properties": {"show": {"type": "boolean", "description": "true to also show the player a table card."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "auction_ledger",
        "description": "What the player really bought and sold at the auction house, from its mail: summary (money "
                       "received and paid, the house's cut, the balance, the items that brought and cost the most), "
                       "sales, or purchases, each with the mail's number. Real prices, not listings. Use it for 'what "
                       "did I sell', 'how much did I make at the auction house', 'what did I pay for that'.",
        "input_schema": {
            "type": "object",
            "properties": {"view": {"type": "string", "enum": ["summary", "sales", "purchases"]},
                           "days": {"type": "integer", "minimum": 1, "maximum": 3650, "description": "How far back. Default 30."},
                           "item": {"type": "string", "description": "Only deals in items whose name contains this."},
                           "show": {"type": "boolean", "description": "true to also show the player the result as a table card."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "quest_search",
        "description": "Any quest at all, from Questie's whole quest database, not only those in the log or open now: by "
                       "name (as the game writes it; part of a name works) or id, or every quest of a zone, a kind or a level "
                       "range. For each: level and the level it needs, kind (elite = group quest, dungeon, raid, pvp), whether "
                       "this character has done it, has it, can take it now, and if not what is missing (level, earlier "
                       "quests); who gives it and takes it back and where (refs for waypoint), what it asks for (creatures "
                       "with places, things, items), and the whole chain around it. Use it for 'where does quest X start', "
                       "'what do I need before X', 'what elite quests are there in Westfall', 'which part of the chain am I on'.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "id": {"type": "integer"}, "zone": {"type": "string"},
                           "kind": {"type": "string", "enum": ["any", "normal", "elite", "dungeon", "raid", "pvp"]},
                           "min_level": {"type": "integer", "minimum": 0, "maximum": 100},
                           "max_level": {"type": "integer", "minimum": 0, "maximum": 100},
                           "hide_done": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 40}},
            "additionalProperties": False,
        },
    },
    {
        "name": "quest_done",
        "description": "Whether the player has completed given quests, by quest id.",
        "input_schema": {
            "type": "object",
            "properties": {"quest_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 50}},
            "required": ["quest_ids"], "additionalProperties": False,
        },
    },
    {
        "name": "changes",
        "description": "What changed for this character between two of its saved snapshots: level, money, quests "
                       "finished, picked up and dropped, profession skill, zone. With no argument it compares the latest "
                       "snapshot with the one before it (one play session). With days, it compares with the snapshot "
                       "from at least that many days ago. Use it for 'what did I do last time' or 'how far have I come "
                       "this week'.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "number", "description": "Look back at least this many days"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "dungeons",
        "description": "Every dungeon in the game with its level range, where it is, and how many of its quests this "
                       "character has done, has in the log, or can pick up now. Use it to choose or compare dungeons.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "dungeon",
        "description": "One dungeon in full: its entrance, each of its quests with this character's status (done, in "
                       "the log, available, locked behind an earlier quest, or needing a level), who gives it and where, "
                       "the quests that must be done first and the next step to take, the rewards, and every boss with "
                       "its loot. This is the source for 'what am I missing for this dungeon' and 'what drops there'.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The dungeon's name, or part of it"}},
            "required": ["name"], "additionalProperties": False,
        },
    },
    {
        "name": "recipes",
        "description": "The recipes of the player's professions, read from the game: for each, its skill-up colour "
                       "(orange, yellow, green, grey), the skill at which it turns grey, what it makes, and its "
                       "materials with how many are in the bags and the auction price of each. By default only the "
                       "recipes that can still give skill; show=all lists every recipe. With search (part of a "
                       "recipe's name or of what it makes): that recipe, learned or not, and for one not learned yet "
                       "how to come by it: the recipe item that teaches it and who sells or drops that item, with "
                       "places. Use search for 'where do I buy the recipe for X', 'can I learn X'. This is the only "
                       "source of recipes and materials: never state them from memory.",
        "input_schema": {
            "type": "object",
            "properties": {"profession": {"type": "string", "description": "Leave out for every profession read so far."},
                           "show": {"type": "string", "enum": ["skill_up", "all"]}, "search": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "auction_prices",
        "description": "The auction-house price of items by name, from the player's own Auctionator scans: the "
                       "lowest price seen, when it was seen, earlier prices where Wisp has kept them, and what the "
                       "player's own stock of it is worth. A name that is not exact returns the closest matches. "
                       "This is the only source of prices: never quote one from memory.",
        "input_schema": {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20}},
            "required": ["items"], "additionalProperties": False,
        },
    },
    {
        "name": "price_history",
        "description": "One item's auction price over time, from every scan Wisp has kept: the price per scan day "
                       "with how many were listed, its usual level, its lowest and highest with dates, its direction, "
                       "the cheapest and dearest weekday once there is enough data, and an estimate of next week's "
                       "price with its confidence. Use it for 'is this a good price', 'when should I sell', 'where is "
                       "this heading'.",
        "input_schema": {
            "type": "object",
            "properties": {"item": {"type": "string"},
                           "days": {"type": "integer", "minimum": 2, "maximum": 3650, "description": "How far back. Default 30."},
                           "show": {"type": "boolean", "description": "true to also show the player a chart card of it."}},
            "required": ["item"], "additionalProperties": False,
        },
    },
    {
        "name": "market_scan",
        "description": "Search the whole kept price history for the items that stand out, ranked in code: below_usual "
                       "(cheap now, to buy), above_usual (dear now, to sell), rising, falling, swings (buy low, sell "
                       "high), most_listed. Narrow it to the player's bags, the materials of their recipes or what "
                       "they can craft, or to names containing a word. This is how to look through thousands of "
                       "items: never guess which items are worth looking at.",
        "input_schema": {
            "type": "object",
            "properties": {
                "find": {"type": "string", "enum": ["below_usual", "above_usual", "rising", "falling", "swings", "most_listed"]},
                "among": {"type": "string", "enum": ["everything", "my_bags", "my_materials", "what_i_craft"]},
                "name_contains": {"type": "string"},
                "days": {"type": "integer", "minimum": 5, "maximum": 3650, "description": "The period judged. Default 30."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 40},
                "show": {"type": "boolean", "description": "true to also show the player this ranking as a table card."},
            },
            "required": ["find"], "additionalProperties": False,
        },
    },
    {
        "name": "market_query",
        "description": "Ask the kept price history anything, with one read-only SQL SELECT (SQLite). One table, p, "
                       "shared by all the player's characters: item (id), name, date ('2026-11-03'), days_ago (0 = the "
                       "last scan day known), low and high (the day's lowest and highest listing, in copper), listed "
                       "(how many were listed; may be null). One row per item and day scanned; days without a scan "
                       "have no row. coins(copper) writes money as '2g 16s'. Use it when market_scan and "
                       "price_history do not fit: price bands, listings thresholds, comparing dates, weekdays "
                       "(strftime('%w', date)), counts, anything aggregated. Do the arithmetic in the SQL, not in "
                       "your head. At most 200 rows come back, so aggregate and LIMIT.",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "One SELECT, e.g. SELECT name, coins(low) AS now, listed "
                                                                    "FROM p WHERE days_ago = 0 AND low BETWEEN 10000 AND 50000 "
                                                                    "ORDER BY listed DESC LIMIT 20"}},
            "required": ["sql"], "additionalProperties": False,
        },
    },
    {
        "name": "item_links",
        "description": "Everything one step from an item, to follow how things connect: its auction price and the "
                       "count in the bags, the character's recipes that use it and those that make it, the quests "
                       "that ask for it (in the log or for a dungeon), the dungeon bosses that drop it, who sells "
                       "it, what creatures drop it and what objects hold it, each with places that have refs, for "
                       "any item in the game, recipe items included (and which recipe one teaches). Use "
                       "it for 'what is this for', 'where do I get this', 'is it worth keeping', and walk on from "
                       "its answer one item at a time. Never state a use or a source of an item from memory.",
        "input_schema": {
            "type": "object", "properties": {"item": {"type": "string"}},
            "required": ["item"], "additionalProperties": False,
        },
    },
    {
        "name": "gathering",
        "description": "Where herbs, ore veins, fishing pools and chests are, from the player's GatherMate2 addon. "
                       "With nothing: what is on record, by profession. With what: the zones a node is known in, "
                       "richest first. With zone: what that zone holds. With both: a route through that zone's "
                       "nodes, as one ref for the waypoint tool. The only source of gathering places: never name a "
                       "zone or a spot for a herb or an ore from memory.",
        "input_schema": {
            "type": "object",
            "properties": {"what": {"type": "string", "description": "A node as the game names it: Peacebloom, Copper Vein."},
                           "zone": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "farming",
        "description": "What farming is worth, measured. spots: the zones the character has farmed, with time spent "
                       "looting, coins, loot worth and gold per hour. sources: each kind of creature and node the "
                       "character looted, with the measured rate of every item and the worth per loot. gathering: "
                       "the gathering nodes on record priced, by zone. The rates here are the player's own "
                       "measurements and the only drop rates there are: never quote a drop rate from memory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "view": {"type": "string", "enum": ["spots", "sources", "gathering"]},
                "zone": {"type": "string", "description": "Narrow to one zone."},
                "days": {"type": "integer", "minimum": 1, "maximum": 60, "description": "How far back. Default 30."},
                "show": {"type": "boolean", "description": "true to also show the player the result as a table card."},
            },
            "required": ["view"], "additionalProperties": False,
        },
    },
    {
        "name": "craft_tree",
        "description": "A craftable item broken all the way down to raw materials, through every recipe on the way, "
                       "whatever profession each needs, and checked against everything the player holds: their bags "
                       "and bank and those of their other characters. For every material: how many are needed, how "
                       "many are already held and where, how many to buy and what that costs; the crafts in order; "
                       "what the product is listed for; intermediates cheaper to buy than to make. Use it for 'what "
                       "do I need to make X', 'make me a shopping list for X', 'what does X cost to craft'. With show "
                       "true the player sees it as a card whose button creates the shopping list for exactly what is "
                       "missing in Auctionator: use that whenever they ask for a list.",
        "input_schema": {
            "type": "object",
            "properties": {"item": {"type": "string"}, "quantity": {"type": "integer", "minimum": 1, "maximum": 10000},
                           "show": {"type": "boolean"}},
            "required": ["item"], "additionalProperties": False,
        },
    },
    {
        "name": "item_uses",
        "description": "Everything an item goes into, up the whole graph: the recipes that use it directly (any "
                       "profession, the player's or not) and every product it ends up in through what is made from "
                       "it, with how many of the item one product takes, what the products are listed for, who can "
                       "craft them, and where the item itself comes from (vendors, drops, recipes). Use it for 'what "
                       "is X used for', 'is X worth keeping or buying', 'what should I invest in': reason from what "
                       "it becomes and how scarce it is. With show true the player sees the products as a card.",
        "input_schema": {
            "type": "object",
            "properties": {"item": {"type": "string"}, "show": {"type": "boolean"}},
            "required": ["item"], "additionalProperties": False,
        },
    },
    {
        "name": "craft_plan",
        "description": "The arithmetic of a crafting plan, done in code: for each recipe and how many times to craft "
                       "it, the total materials, what the plan's own crafts supply (a bolt made in one step and used "
                       "in the next), what the bags cover, what is left to buy and what that costs. Always use this "
                       "for quantities and costs; never add them up yourself.",
        "input_schema": {
            "type": "object",
            "properties": {"crafts": {"type": "array", "minItems": 1, "maxItems": 30, "items": {
                "type": "object",
                "properties": {"recipe_id": {"type": "integer"}, "times": {"type": "integer", "minimum": 1, "maximum": 5000}},
                "required": ["recipe_id", "times"], "additionalProperties": False}}},
            "required": ["crafts"], "additionalProperties": False,
        },
    },
    {
        "name": "shopping_list",
        "description": "Show the player a card. With crafts (the same as for craft_plan) it is the whole crafting "
                       "plan laid out: the steps in the order given, a table of materials with what the bags cover "
                       "and what to buy, the cost, and a Copy button that creates the shopping list for exactly the "
                       "remainder in the Auctionator addon. Put the crafts in the order the player should do them. "
                       "With items it is a plain shopping list by name. The list is created in the game only when "
                       "the player pastes the card.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "A short name for the list, such as 'Tailoring 150-185'."},
                "crafts": {"type": "array", "minItems": 1, "maxItems": 30, "items": {
                    "type": "object",
                    "properties": {"recipe_id": {"type": "integer"}, "times": {"type": "integer", "minimum": 1, "maximum": 5000}},
                    "required": ["recipe_id", "times"], "additionalProperties": False}},
                "items": {"type": "array", "minItems": 1, "maxItems": 60, "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "quantity": {"type": "integer", "minimum": 1, "maximum": 100000}},
                    "required": ["name", "quantity"], "additionalProperties": False}},
            },
            "required": ["name"], "additionalProperties": False,
        },
    },
    {
        "name": "notes",
        "description": "The research reports kept from earlier /research runs: list them, or read one. Look here "
                       "before researching a question again, and when the player refers to earlier research.",
        "input_schema": {
            "type": "object",
            "properties": {"read": {"type": "string", "description": "A note's title or name, to read it. Leave out to list."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "waypoint",
        "description": "Offer the player waypoints for the game. Pass location refs from the quests or dungeon tools. This shows "
                       "a card the player can copy and paste into the game, where TomTom sets the waypoints. It does "
                       "not act in the game by itself. Use it when the player asks where something is or wants to go "
                       "there. A ref can also be a route from the gathering tool: one ref, a waypoint for each stop.",
        "input_schema": {
            "type": "object",
            "properties": {"refs": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20}},
            "required": ["refs"], "additionalProperties": False,
        },
    },
]

# Offered only in a research run.
RESEARCH_TOOLS = [
    {
        "name": "report",
        "description": "End a research run: show the player a structured report and keep it as a note. Short "
                       "points, grouped under headings. A point that rests on the web lists the URLs of the pages "
                       "it rests on, exactly as the web tools gave them; a URL they did not give is left out. "
                       "Call it once, when the research is done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string", "description": "Two sentences: the answer, and how sure it is."},
                "sections": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
                    "type": "object",
                    "properties": {
                        "heading": {"type": "string"},
                        "points": {"type": "array", "minItems": 1, "maxItems": 10, "items": {
                            "type": "object",
                            "properties": {"text": {"type": "string"},
                                           "sources": {"type": "array", "items": {"type": "string"}, "maxItems": 6}},
                            "required": ["text"], "additionalProperties": False}}},
                    "required": ["heading", "points"], "additionalProperties": False}},
                "next_steps": {"type": "array", "maxItems": 8, "items": {"type": "string"},
                               "description": "What the player can do today, most useful first."},
            },
            "required": ["title", "summary", "sections"], "additionalProperties": False,
        },
    },
]

SYSTEM = """You are Wisp, an assistant for a World of Warcraft: Forever player. You run in a small window beside the game.

What you know about the game comes only from your tools. They read a snapshot the player's addon saved at their last \
logout, so it can be out of date: say so when it matters, and if the player tells you something changed, believe them. \
Quest locations come from the Questie addon and everything about dungeons from the DungeonJournal addon; when one of \
them was not installed, the tool says so, and you tell the player which addon would help.

Quests the character has not picked up come from available_quests: what is open to them, by zone and by kind (elite \
group quests, dungeon, raid, PvP), nearest first, each with the colour the quest log would give it. For what to do \
now, call questing_route: it lays the zone's hand-ins, pick-ups and quests under way out as a route card. For where to \
level next, compare zones by their quests worth doing, the experience on offer there and their quest levels against \
the character's level. How fast they level, and when the next level comes at that pace, is in leveling. Any \
item can be followed through the whole game's recipes: item_uses goes up (everything it becomes, any profession) and \
craft_tree goes down (a product to its raw materials, with the cost). When asked what an item is worth, whether to keep, \
buy or invest in it, look at both its market (price_history, market_depth) and its place in that graph, and say which \
facts your judgement rests on. Any particular quest, or quests out of reach (a chain's later parts, a zone's elite quests at any level), is in quest_search: \
where it starts, what must come first, and whether this character can take it yet.

For earning gold at the auction house, start from the player's own things: gold shows what their bags would fetch \
and which of their recipes turn a profit; market_scan and price_history show what is cheap or dear against its usual \
price. Say plainly that a listing is not a sale.

When the player says they bought something to sell later, or sold some of it, record it with investments, using the \
quantity and the price exactly as they gave them; if a number is missing, ask rather than guess. For how their \
investments are doing, call investments and show the card: every figure there is worked out in code. What they really \
bought and sold at the auction house is in auction_ledger, from the house's own mail; where investments lists such \
mail that is not recorded, offer it once and record it with from_mail only on a yes.

Never state a quest id, an NPC, a location or coordinates from memory. WoW Forever differs from every earlier version \
of the game, so what you remember is unreliable. If the tools do not have something, say that plainly.

For professions, recipes and their materials come only from the recipes tool, and every quantity and cost from \
craft_plan: choose what to craft and how many times, and let it do the sums. Once you have chosen, always call \
shopping_list with those crafts: it shows the player the plan as a card with the steps, the materials, what the bags \
cover, what to buy and the cost. Your text then adds at most two short sentences the card cannot say, such as why this \
order or what is uncertain; never repeat its steps or numbers. Prices are the player's own last auction \
scans and skill is as of the last logout, so say how old they are when it matters. Wisp only knows the recipes the \
character's profession window listed; for a path beyond those, say what is missing rather than fill it in from memory.

The price of anything at the auction house comes only from live_auctions, auction_prices, price_history, market_scan and \
market_query, never from memory. live_auctions is Blizzard's own published auction data, read with the player's own \
developer client: if the player asks about the Blizzard API, live prices, or prices without scanning, call it; when it is \
not set up, pass on what it says about setting it up instead of saying you have no access. Prices are the lowest the player's Auctionator addon saw listed on the days it scanned: a listing, not a sale, \
and before the auction house's cut. Wisp keeps every scan day, so for questions about earning gold, good prices or \
where a price is heading, look there first; say how many scan days an answer rests on, and give any estimate of a \
future price as a range with its confidence, never as a certainty. When your answer rests on a price_history or \
market_scan result, call it with show set to true for the one result you are answering with: the player then sees a \
chart or a table, and your text adds only what it means for them, in a sentence or two.

How things connect goes through items: item_links gives, for one item, the recipes that use and make it, the quests \
that ask for it, the bosses, creatures and objects that drop it, who sells it, and its price. For a question that \
spans several of these, follow the links one item at a time rather than guessing the chain. When a crafting plan has \
materials with no auction price, look them up there: a vendor may sell them, and the plan's card can be followed by a \
waypoint to that vendor.

What to farm for gold comes from the farming tool: what the character's own looting has measured (gold per hour by \
zone, how often each item came from each creature or node) and what the known gathering nodes are worth. Nobody has \
drop rates for ordinary loot, so the player's measurements are the only ones: for a place they have not farmed, say \
that there is no measurement, and offer a /research run to see what other players report. When you answer from it, \
call it with show set to true and keep your text to what it means.

Where to gather comes only from the gathering tool: the places the player's GatherMate2 addon has on record. Item \
links name the node an item is gathered from; from there, ask gathering for its zones and then for a route.

You cannot act in the game. The waypoint and shopping_list tools only show a card that the player copies and pastes \
into the game. After calling one, tell the player in one short sentence to press Copy and then Ctrl+V twice in the game.

Answer in the player's language. Be brief: this is read in a narrow window between fights. Never a wall of text: when \
there are steps or several items, put each on its own short line, numbered when the order matters. Plain text only: no \
markdown symbols and no tables."""


def run_tool(game: Game, name: str, args) -> dict:
    """Dispatch one tool call. The arguments are model output: check them."""
    handler = getattr(game, f"tool_{name}", None) if name in {t["name"] for t in TOOLS + RESEARCH_TOOLS} else None
    if handler is None:
        return {"error": f"there is no tool named {name}"}
    if not isinstance(args, dict):
        return {"error": "the arguments must be an object"}
    return handler(args)
