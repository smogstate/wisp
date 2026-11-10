"""What the overlay knows about the game: the addon's snapshot, the tools the
model can call over it, and the WISP1 messages that go back into the game."""
import base64
import json
import re
import statistics
import time
from pathlib import Path

from .crafting import Crafting, coins
from .farming import Farming
from .gathering import Gathering
from .graph import ItemGraph
from .history import diff

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
        snapshot = json.loads(base64.b64decode(m.group(1)))
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

    def __init__(self, snapshot: dict | None, problem: str | None = None):
        self.snapshot = snapshot or {}
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
        self.web_seen: dict[str, str] = {}  # url -> title of every web page this conversation was given
        self.notes_dir: Path | None = None  # where research reports are kept, when the app has a place

    @classmethod
    def load(cls, wow_dir: Path | None) -> "Game":
        if wow_dir is None:
            return cls(None, "no game folder was given")
        saved = find_saved_file(wow_dir)
        if saved is None:
            return cls(None, "no Wisp snapshot found: enable the Wisp addon, log in, then log out once")
        try:
            return cls(read_snapshot(saved))
        except ValueError as e:
            return cls(None, str(e))

    # -- reading the snapshot ------------------------------------------------

    def age_minutes(self) -> int | None:
        taken = self.snapshot.get("takenAt")
        return max(0, int((time.time() - taken) / 60)) if isinstance(taken, (int, float)) else None

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

    def _who(self, prefix: str, people, role: str) -> list[dict]:
        out = []
        for i, person in enumerate(p for p in _as_list(people) if isinstance(p, dict)):
            name = _clean(person.get("name"), 60) or "?"
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
            "id": qid, "title": title, "level": q.get("level"), "complete": bool(q.get("complete")),
            "objectives": [{"text": _clean(o.get("text"), 160), "done": bool(o.get("done"))}
                           for o in _as_list(q.get("objectives")) if isinstance(o, dict)],
            "starts": self._who(f"q{qid}.start", q.get("starts"), "quest giver"),
            "ends": self._who(f"q{qid}.end", q.get("ends"), "turn in"),
        }
        way = q.get("gameWaypoint")
        if isinstance(way, dict):
            out["game_pointer"] = self._place(f"q{qid}.pointer", way, f"{title} (next step)")
        nxt = q.get("next")
        if isinstance(nxt, dict):
            out["next_in_chain"] = {
                "id": nxt.get("id"), "title": _clean(nxt.get("title"), 80), "level": nxt.get("level"),
                "starts": self._who(f"q{qid}.next", nxt.get("starts"), "next quest"),
            }
        return out

    def _available(self, q: dict) -> dict:
        """A quest the character could pick up, as Questie has it."""
        qid = q.get("id")
        title = _clean(q.get("title"), 80)
        starts = self._who(f"a{qid}.start", q.get("starts"), "quest giver")
        return {"id": qid, "title": title, "level": q.get("level") if isinstance(q.get("level"), int) else None,
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
                "where": self._journal_place(f"dq{step.get('id')}.start", step.get("start"), f"{step_name} (quest start)"),
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
            "pickup_where": self._journal_place(f"dq{qid}.start", q.get("start"), f"{name} (quest start)"),
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
            "entrance": self._journal_place(f"d{index + 1}.entrance", d.get("entrance"), f"{name} entrance"),
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
                "meant for a group. Every quest giver's place has a ref for the waypoint tool.")
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
                    counts = zones.setdefault(z, {"zone": z, "open_quests": 0})
                    counts["open_quests"] += 1
                    if q["kind"] != "normal":
                        counts[q["kind"]] = counts.get(q["kind"], 0) + 1
            return {"open_quests": len(found), "by_zone": sorted(zones.values(), key=lambda z: (-z["open_quests"], z["zone"]))[:60],
                    "character_zone": me.get("zone"), "character_level": me.get("level"),
                    "note": note + " Ask with a zone (or zone 'here') or a kind for the quests themselves."}
        found.sort(key=lambda q: (q["level"] or 0, q["title"]))
        return {"kind": kind, "open_quests": len(found), "character_level": me.get("level"),
                "quests": [{k: v for k, v in q.items() if v not in ([], None)} for q in found[:limit]],
                **({"left_out": len(found) - limit} if len(found) > limit else {}), "note": note}

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

    def tool_waypoint(self, args: dict) -> dict:
        refs = args.get("refs")
        if not isinstance(refs, list) or not refs or len(refs) > 20 or not all(isinstance(r, str) for r in refs):
            return {"error": "refs must be a list of 1 to 20 location refs from the quests or dungeon tools"}
        unknown = [r for r in refs if r not in self.places and r not in self.routes]
        if unknown:
            return {"error": f"unknown refs: {', '.join(unknown[:5])}. Use refs exactly as a tool returned them."}
        shown, points = [], []
        for ref in dict.fromkeys(refs):
            if ref in self.routes:   # a route is one line on the card and a waypoint for each of its stops
                route = self.routes[ref]
                shown.append({"addon": "TomTom", "text": f"Route: {route['title']}", "detail": route["detail"]})
                points += route["stops"]
            else:
                p = self.places[ref]
                shown.append({"addon": "TomTom", "text": f"Waypoint: {p['title']}", "detail": f"{p['zone']}  {p['x']}, {p['y']}"})
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
        wanted, show = args.get("profession"), args.get("show", "skill_up")
        if show not in ("skill_up", "all"):
            return {"error": "show must be skill_up or all"}
        if isinstance(wanted, str) and wanted.strip():
            books = [b for b in books if b["profession"].lower() == wanted.strip().lower()]
            if not books:
                return {"error": f"no recipes read for {wanted.strip()}. Read so far: "
                                 + ", ".join(b["profession"] for b in self.crafting.books)}
        out = []
        for book in books:
            listed = [r for r in book["recipes"] if show == "all" or (r["learned"] and r["colour"] != "grey")]
            listed.sort(key=lambda r: ("orange", "yellow", "green", None, "grey").index(r["colour"]))
            entry = {"profession": book["profession"], "skill": book["skill"], "max_skill": book["max"],
                     "recipes_known_to_wisp": len(book["recipes"]), "recipes": [self.crafting.describe(r) for r in listed[:80]]}
            if isinstance(book["seen_at"], (int, float)):
                entry["list_read_hours_ago"] = round(max(0, time.time() - book["seen_at"]) / 3600, 1)
            if len(listed) > 80:
                entry["left_out"] = len(listed) - 80
            out.append(entry)
        return {"professions": out,
                "note": "Read from the game while the profession window was open. A recipe gives skill on every craft "
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
                "title": SCAN_TITLES[kind], "sub": f"last {days} days" + (f" · {among.replace('_', ' ')}" if only is not None else ""),
                "columns": ["Item", "Now", "Usual", "Against usual", "Listed"],
                "rows": [[e["item"], e["now"], e["usual"], e["now_against_usual"], e.get("usually_listed", "–")] for e in found],
                "notes": ["Lowest listings from your own Auctionator scans. A listing is not a sale."]}})
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
            return {"error": f"Wisp knows no item named like '{_clean(name, 80)}'. It knows the items the character carries, "
                             "crafts with, crafts, is asked for by quests, sees drop from dungeon bosses, or has a price for. "
                             "Names are as the player's game writes them."}
        if len(found) > 1:
            return {"several_items_match": [self.graph.names[i] for i in found], "note": "Ask again with one of these names exactly."}
        out = self.graph.around(found[0], self.crafting)
        if len(out) == 1:
            out["nothing_known"] = "Wisp has the name but no links for this item."
        addons = self.snapshot.get("addons") or {}
        out["note"] = ("One step in every direction from this item. Follow a link with the tool for that kind of thing: "
                       "recipes and craft_plan for a recipe, quests or dungeon for a quest or a boss, price_history for the "
                       "price, item_links again for another item. Every place has a ref for the waypoint tool. Recipes are "
                       "only the character's own; sellers, drops and quest needs come from the Questie addon and are listed "
                       "only for items that matter to this character, the first few of each"
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
                  "basis": "Your own game data" + (f" and {len(sources)} web page{'s' if len(sources) != 1 else ''}" if sources else ", no web pages")}
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
                report["saved"] = "Kept in your notes"
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
        me = self.snapshot.get("character") or {}
        start = (me["x"], me["y"]) if me.get("map") == map_id and isinstance(me.get("x"), (int, float)) and isinstance(me.get("y"), (int, float)) else None
        stops = gather.route(name, map_id, start)
        ref = f"g{map_id}." + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        self.routes[ref] = {"title": f"{name}, {len(stops)} stop{'s' if len(stops) != 1 else ''}",
                            "detail": f"{place['zone']} · {len(place['at'])} known node{'s' if len(place['at']) != 1 else ''}",
                            "stops": [{"map": map_id, "zone": place["zone"], "x": s["x"], "y": s["y"], "title": f"{name} {i + 1}"}
                                      for i, s in enumerate(stops)]}
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
            table = {"title": "What known nodes are worth", "sub": maps and self.gathering.zone_name(maps[0]) or "every zone on record",
                     "columns": ["Node", "Zone", "Nodes", "Each", "All"],
                     "rows": [[e["node"], e["zone"], e["known_nodes"], e["worth_each"], e["worth_all"]] for e in ranked[:15]],
                     "notes": ["Every known node gathered once, at your auction prices. Not an hourly rate."]}
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
                table = {"title": "Your farming spots", "sub": f"last {days} days, measured from your own loot",
                         "columns": ["Zone", "Time", "Looted", "Total", "Per hour"],
                         "rows": [[e["zone"], e["time_looting"], e["looted"], e["total"], e["per_hour"]] for e in spots[:15]],
                         "notes": ["Coins plus loot at your auction prices, or what a vendor pays. Listed worth, not sales."]}
            else:
                sources = farm.sources(days, found[0][0] if found else None)
                out = {"view": "sources", "sources": [{k: v for k, v in e.items() if k[0] != "_"} for e in sources[:30]],
                       "note": "Measured from the character's own looting: per_loot is how many of the item came per corpse or "
                               "node looted. With few looted it is rough: say how many it rests on. " + priced}
                table = {"title": "What you looted from what", "sub": (found[0][1] if found else "everywhere") + f", last {days} days",
                         "columns": ["Source", "Zone", "Looted", "Per loot"],
                         "rows": [[e["source"], e["zone"], e["looted"], e["worth_per_loot"]] for e in sources[:15]],
                         "notes": ["Worth per corpse or node looted, at your auction prices."]}
        if args.get("show") is True and table and table["rows"]:
            self.cards.append({"id": f"card{len(self.cards) + 1}", "lines": [], "table": table})
            out["shown"] = "a table card with these rows is now shown to the player: do not repeat them in your text"
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
        about = (f"about {coins(total)}" + ("" if priced == len(buy) else f" · {len(buy) - priced} without a price")) if priced else "prices unknown"
        header = {"addon": "Auctionator", "text": f"Shopping list: {title}",
                  "detail": f"{len(buy)} item{'s' if len(buy) != 1 else ''} · {about}"}
        card = {"id": f"card{len(self.cards) + 1}", "lines": []}
        if plan:   # the whole plan, laid out: the steps, a table of materials, the total
            card["plan"] = {"title": title, **plan["_card"]}
            card["hint"] = "Shopping list for Auctionator: Copy, then in the game Ctrl+V twice"
        else:
            card["lines"] = [header] + [{"text": f"{count} × {name}",
                                         "detail": f"{coins(craft.prices[item][0])} each" if item in craft.prices else ""}
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
                       "quest next'. The only source of quests outside the log: never name one from memory.",
        "input_schema": {
            "type": "object",
            "properties": {"zone": {"type": "string"},
                           "kind": {"type": "string", "enum": ["any", "normal", "elite", "dungeon", "raid", "pvp"]},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 60}},
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
                       "recipes that can still give skill; show=all lists every recipe. This is the only source of "
                       "recipes and materials: never state them from memory.",
        "input_schema": {
            "type": "object",
            "properties": {"profession": {"type": "string", "description": "Leave out for every profession read so far."},
                           "show": {"type": "string", "enum": ["skill_up", "all"]}},
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
                       "for the player's realm and faction: item (id), name, date ('2026-11-03'), days_ago (0 = the "
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
                       "it, what creatures drop it and what objects hold it, each with places that have refs. Use "
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
group quests, dungeon, raid, PvP), nearest first. Use it for what to do next and where.

Never state a quest id, an NPC, a location or coordinates from memory. WoW Forever differs from every earlier version \
of the game, so what you remember is unreliable. If the tools do not have something, say that plainly.

For professions, recipes and their materials come only from the recipes tool, and every quantity and cost from \
craft_plan: choose what to craft and how many times, and let it do the sums. Once you have chosen, always call \
shopping_list with those crafts: it shows the player the plan as a card with the steps, the materials, what the bags \
cover, what to buy and the cost. Your text then adds at most two short sentences the card cannot say, such as why this \
order or what is uncertain; never repeat its steps or numbers. Prices are the player's own last auction \
scans and skill is as of the last logout, so say how old they are when it matters. Wisp only knows the recipes the \
character's profession window listed; for a path beyond those, say what is missing rather than fill it in from memory.

The price of anything at the auction house comes only from auction_prices, price_history, market_scan and \
market_query, never from memory. Prices are the lowest the player's Auctionator addon saw listed on the days it scanned: a listing, not a sale, \
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
