"""What the overlay knows about the game: the addon's snapshot, the tools the
model can call over it, and the WISP1 messages that go back into the game."""
import base64
import json
import re
import time
from pathlib import Path

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
        self.cards: list[dict] = []        # action cards made during the current answer

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

    # -- tools ---------------------------------------------------------------

    def tool_character(self, _args: dict) -> dict:
        if not self.snapshot:
            return {"error": self.problem}
        completed = _as_list(self.snapshot.get("completed"))
        return {
            "character": self.snapshot.get("character"),
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

    def tool_quest_done(self, args: dict) -> dict:
        ids = args.get("quest_ids")
        if not isinstance(ids, list) or not ids or len(ids) > 50 or not all(isinstance(i, int) for i in ids):
            return {"error": "quest_ids must be a list of 1 to 50 whole numbers"}
        completed = set(_as_list(self.snapshot.get("completed")))
        return {"completed": {str(i): i in completed for i in ids}}

    def tool_waypoint(self, args: dict) -> dict:
        refs = args.get("refs")
        if not isinstance(refs, list) or not refs or len(refs) > 20 or not all(isinstance(r, str) for r in refs):
            return {"error": "refs must be a list of 1 to 20 location refs from the quests tool"}
        unknown = [r for r in refs if r not in self.places]
        if unknown:
            return {"error": f"unknown refs: {', '.join(unknown[:5])}. Use refs exactly as the quests tool returned them."}
        lines = [self.places[r] for r in dict.fromkeys(refs)]
        card = {
            "id": f"card{len(self.cards) + 1}",
            "lines": [{"addon": "TomTom", "text": f"Waypoint: {p['title']}", "detail": f"{p['zone']}  {p['x']}, {p['y']}"}
                      for p in lines],
            "wisp": encode_wisp1([{"do": "tomtom.waypoint", "map": p["map"], "x": p["x"], "y": p["y"], "title": p["title"]}
                                  for p in lines]),
        }
        self.cards.append(card)
        return {"shown": len(lines), "note": "A card with a Copy button is now shown to the player. Nothing has happened "
                "in the game yet: the player must copy it and paste it into the game, and you cannot see whether they did."}


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
        "name": "quest_done",
        "description": "Whether the player has completed given quests, by quest id.",
        "input_schema": {
            "type": "object",
            "properties": {"quest_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 50}},
            "required": ["quest_ids"], "additionalProperties": False,
        },
    },
    {
        "name": "waypoint",
        "description": "Offer the player waypoints for the game. Pass location refs from the quests tool. This shows "
                       "a card the player can copy and paste into the game, where TomTom sets the waypoints. It does "
                       "not act in the game by itself. Use it when the player asks where something is or wants to go "
                       "there.",
        "input_schema": {
            "type": "object",
            "properties": {"refs": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20}},
            "required": ["refs"], "additionalProperties": False,
        },
    },
]

SYSTEM = """You are Wisp, an assistant for a World of Warcraft: Forever player. You run in a small window beside the game.

What you know about the game comes only from your tools. They read a snapshot the player's addon saved at their last \
logout, so it can be out of date: say so when it matters, and if the player tells you something changed, believe them.

Never state a quest id, an NPC, a location or coordinates from memory. WoW Forever differs from every earlier version \
of the game, so what you remember is unreliable. If the tools do not have something, say that plainly.

You cannot act in the game. The waypoint tool only shows a card that the player copies and pastes into the game. After \
calling it, tell the player in one short sentence to press Copy and then Ctrl+V twice in the game.

Answer in the player's language. Be brief: this is read in a narrow window between fights. Plain text, no markdown, no \
tables."""


def run_tool(game: Game, name: str, args) -> dict:
    """Dispatch one tool call. The arguments are model output: check them."""
    handler = getattr(game, f"tool_{name}", None) if name in {t["name"] for t in TOOLS} else None
    if handler is None:
        return {"error": f"there is no tool named {name}"}
    if not isinstance(args, dict):
        return {"error": "the arguments must be an object"}
    return handler(args)
