"""Every quest the character could ever take, for questions about any quest.

The addon reads Questie's whole quest database once, in the background after a
login, and keeps it in the saved file as `WispQuests` (base64 of JSON) beside the
snapshot. It is read again only when Questie's data, the game's language or the
character changes. Each quest: name, level, the level it needs, zone, kind (elite,
dungeon, raid, pvp), who gives it and who takes it back, what comes before it and
after it, and what it asks for. The people, things and items it names come in
lists of their own, each person and thing with one place.

What the character has done, has in the log, or could pick up now comes from the
snapshot. Whether a quest is open is worked out here, in code: level, earlier
quests, quests that close it.
"""
import base64
import difflib
import json
import re
from pathlib import Path

_read: dict = {}   # saved file -> ((mtime, size), index or None)


def read_index(saved: Path | None, name: str = "WispQuests", holds: str = "quests") -> dict | None:
    """An index the addon keeps in the saved file (the quests, or WispItems: the items), or None when it has not written it yet."""
    if saved is None:
        return None
    try:
        stat = saved.stat()
    except OSError:
        return None
    stamp = (stat.st_mtime_ns, stat.st_size)
    if (saved, name) in _read and _read[saved, name][0] == stamp:
        return _read[saved, name][1]
    index = None
    try:
        m = re.search(rf'^{name} = "([A-Za-z0-9+/=]+)"', saved.read_text(encoding="utf-8", errors="replace"), re.M)
        if m:
            # a letter cut in half by an older addon must not cost the whole list
            data = json.loads(base64.b64decode(m.group(1)).decode("utf-8", errors="replace"))
            if isinstance(data, dict) and data.get("v") == 1 and isinstance(data.get(holds), list):
                index = data
    except (OSError, ValueError):
        pass
    _read[saved, name] = (stamp, index)
    return index


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _ids(value) -> list[int]:
    return [v for v in value if _int(v) is not None] if isinstance(value, list) else []


def fold(text: str) -> str:
    """For matching names as people type them: case, ё/е, й/и and punctuation do not matter."""
    return " ".join(re.sub(r"[^\w]+", " ", str(text).casefold().replace("ё", "е").replace("й", "и")).split())


def stems(text: str) -> list[str]:
    """A name's words without their endings: 'тонкую нить' is 'Тонкая нить', 'рулоны шелка' is 'Рулон шелка', 'eggs' is 'Egg'.
    ponytail: the last letter or two cut off, no dictionary; a real stemmer if this ever mismatches."""
    return [w if len(w) <= 3 else w[:-1] if len(w) == 4 else w[:-2] for w in fold(text).split()]


def best(query: str, names) -> list:
    """The keys of the names a typed query means, best first; names is (key, name) pairs.
    The exact name alone; else every name holding the query's words, as written or by their stems
    (a player writes a name in whatever case their sentence needs), those of the query's own length
    first ('шелка' is 'Шелк' before it is 'Рулон шелка'), shorter names first."""
    wanted = fold(query)
    if not wanted:
        return []
    folded = [(key, fold(name)) for key, name in names]
    exact = [key for key, text in folded if text == wanted]
    if exact:
        return exact
    words, cut = wanted.split(), stems(query)
    near = [(key, text) for key, text in folded
            if all(w in text for w in words) or all(any(part.startswith(s) for part in text.split()) for s in cut)]
    same = [n for n in near if len(n[1].split()) == len(words)]
    return [key for key, text in sorted(same or near, key=lambda n: (len(n[1]), n[1], str(n[0])))]


class QuestDB:
    def __init__(self, index: dict, game):
        from .game import _clean   # game text: no colour codes or links, and not too long
        self.clean = lambda text, limit=80: _clean(text, limit)
        self.game = game
        self.quests: dict[int, dict] = {}
        for q in index.get("quests") or []:
            if isinstance(q, dict) and _int(q.get("id")) and isinstance(q.get("name"), str):
                self.quests[q["id"]] = q

        def table(rows):
            return {r["id"]: r for r in rows or [] if isinstance(r, dict) and _int(r.get("id")) and isinstance(r.get("name"), str)}
        self.npcs, self.objects, self.items = table(index.get("npcs")), table(index.get("objects")), table(index.get("items"))
        self.maps = {m["id"]: m.get("name") for m in index.get("maps") or [] if isinstance(m, dict) and _int(m.get("id"))}
        snap = game.snapshot
        self.done = set(_ids(snap.get("completed")))
        self.log = {q["id"] for q in game.quests}
        self.open = {q["id"] for q in game.available}
        self.level = (snap.get("character") or {}).get("level")
        self.folded = {qid: fold(q["name"]) for qid, q in self.quests.items()}

    # -- finding -----------------------------------------------------------------

    def find(self, query: str) -> list[int]:
        """Quest ids whose name matches: the exact name, else every word in it, else the closest names."""
        wanted = fold(query)
        if not wanted:
            return []
        exact = [qid for qid, name in self.folded.items() if name == wanted]
        if exact:
            return sorted(exact)
        words = wanted.split()
        within = [qid for qid, name in self.folded.items() if all(w in name for w in words)]
        if within:
            return sorted(within, key=lambda qid: (len(self.folded[qid]), qid))
        close = set(difflib.get_close_matches(wanted, set(self.folded.values()), n=5, cutoff=0.72))
        return sorted(qid for qid, name in self.folded.items() if name in close)

    def zones(self) -> list[str]:
        return sorted({q["zone"] for q in self.quests.values() if isinstance(q.get("zone"), str)})

    # -- what it is to this character ------------------------------------------------

    def name(self, qid: int) -> str:
        return self.quests[qid]["name"] if qid in self.quests else f"quest {qid}"

    def state(self, qid: int) -> tuple[str, list[str]]:
        """done | in the log | open now | closed | not yet, and why it is not open."""
        q = self.quests[qid]
        if qid in self.done:
            return "done", []
        if qid in self.log:
            return "in the log", []
        if qid in self.open:
            return "open now", []
        closed = [qid2 for qid2 in _ids(q.get("excl")) if qid2 in self.done]
        if closed:
            return "closed", [f"closed for good: {self.name(c)} (id {c}) was done instead" for c in closed]
        why = []
        req = _int(q.get("req"))
        if req and isinstance(self.level, int) and self.level < req:
            why.append(f"needs level {req}")
        before = _ids(q.get("pre"))
        if before and not any(b in self.done for b in before):
            why.append("first: " + " or ".join(f"{self.name(b)} (id {b})" for b in before))
        for b in _ids(q.get("preAll")):
            if b not in self.done:
                why.append(f"first: {self.name(b)} (id {b})")
        if why:
            return "not yet", why
        return "open by its requirements", []

    def _who(self, qid: int, role: str, npcs, objects, items=()) -> list[dict]:
        out = []
        for kind, table, ids in (("npc", self.npcs, npcs), ("object", self.objects, objects), ("item", self.items, items)):
            for some in _ids(ids):
                entry = table.get(some)
                if not entry:
                    continue
                person = {"name": self.clean(entry["name"]), "kind": kind}
                if kind != "item":
                    seen = self.game._place(f"qdb{qid}.{role}.{len(out) + 1}",
                                            {"map": entry.get("map"), "zone": self.maps.get(entry.get("map")), "x": entry.get("x"),
                                             "y": entry.get("y")}, f"{person['name']}")
                    person["where"] = seen or (f"inside {self.clean(entry['at'], 60)}: an instance, so no point on the map"
                                               if isinstance(entry.get("at"), str) else "no place known: Questie has none")
                out.append(person)
                if len(out) >= 4:
                    return out
        return out

    def chain(self, qid: int) -> list[dict]:
        """The quests linked to this one by 'next in chain' and single earlier quests, in order."""
        back, at, seen = [], qid, {qid}
        while len(back) < 15:
            before = [b for b in _ids(self.quests.get(at, {}).get("pre")) if b in self.quests]
            leading = [qid2 for qid2, q in self.quests.items() if q.get("next") == at]
            step = (before or leading)[:1]
            if not step or step[0] in seen:
                break
            at = step[0]
            seen.add(at)
            back.append(at)
        ahead, at = [], qid
        while len(ahead) < 15:
            nxt = _int(self.quests.get(at, {}).get("next"))
            if not nxt or nxt in seen or nxt not in self.quests:
                break
            seen.add(nxt)
            ahead.append(nxt)
            at = nxt
        ids = list(reversed(back)) + [qid] + ahead
        return [{"id": i, "name": self.name(i), "level": self.quests[i].get("level"), "state": self.state(i)[0],
                 **({"this_one": True} if i == qid else {})} for i in ids] if len(ids) > 1 else []

    def row(self, qid: int) -> dict:
        q = self.quests[qid]
        state, _ = self.state(qid)
        return {k: v for k, v in {"id": qid, "name": q["name"], "level": q.get("level"), "required_level": q.get("req"),
                                  "zone": q.get("zone"), "kind": q.get("kind"), "state": state,
                                  "colour": self.game.colour(q.get("level")),
                                  **self.game.rewards.get(qid, {})}.items() if v is not None}

    def detail(self, qid: int) -> dict:
        q = self.quests[qid]
        state, why = self.state(qid)
        out = self.row(qid)
        if why:
            out["why_not_open"] = why
        if q.get("repeatable"):
            out["repeatable"] = True
        if isinstance(q.get("text"), str):
            out["objective"] = self.clean(q["text"], 240)
        out["starts"] = self._who(qid, "start", q.get("sn"), q.get("so"), q.get("si"))
        out["ends"] = self._who(qid, "end", q.get("en"), q.get("eo"))
        asks = {"kill": self._who(qid, "kill", q.get("kill"), ()), "use": self._who(qid, "use", (), q.get("use")),
                "bring": [self.clean(self.items[i]["name"]) for i in _ids(q.get("items")) if i in self.items]}
        out.update({k: v for k, v in asks.items() if v})
        chain = self.chain(qid)
        if chain:
            out["chain"] = chain
        return out
