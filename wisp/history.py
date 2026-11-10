"""Snapshot history: every snapshot the addon saved is kept, per character, so
Wisp knows not only what the character is but how it got there.

The addon holds only the latest snapshot. Each time the overlay sees a new one
it files a copy here, one JSON file per logout, never overwritten:

    snapshots/<realm>-<character>/<time taken>.json

Differences are worked out here in code, not by the model.
"""
import hashlib
import json
import re
from pathlib import Path

REFERENCE = ("dungeons", "recipes", "prices")   # large and much the same every time: not kept per snapshot


def character_key(snapshot: dict) -> str:
    who = snapshot.get("who") if isinstance(snapshot.get("who"), dict) else {}
    name = f"{who.get('realm') or 'realm'}-{who.get('name') or 'character'}"
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)[:80]   # safe as a folder name on Windows


def _fingerprint(snapshot: dict) -> str:
    body = {k: v for k, v in snapshot.items() if k not in ("takenAt", *REFERENCE)}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


class History:
    def __init__(self, folder: Path):
        self.folder = folder
        self._seen: dict[str, float] = {}   # character -> the newest time already filed

    def _files(self, key: str) -> list[Path]:
        files = [f for f in (self.folder / key).glob("*.json") if f.stem.isdigit()]
        return sorted(files, key=lambda f: int(f.stem))

    def _read(self, file: Path) -> dict | None:
        try:
            snapshot = json.loads(file.read_text(encoding="utf-8"))
            return snapshot if isinstance(snapshot, dict) else None
        except (OSError, ValueError):
            return None

    def ingest(self, snapshot: dict) -> bool:
        """File the snapshot if it is new. True when a file was written."""
        taken = snapshot.get("takenAt")
        if not isinstance(taken, (int, float)):
            return False
        key, taken = character_key(snapshot), int(taken)
        if self._seen.get(key, 0) >= taken:
            return False
        files = self._files(key)
        self._seen[key] = taken
        if files and int(files[-1].stem) >= taken:
            self._seen[key] = int(files[-1].stem)
            return False
        if files:   # logging in and straight out again changes nothing: do not file it twice
            last = self._read(files[-1])
            if last is not None and _fingerprint(last) == _fingerprint(snapshot):
                return False
        folder = self.folder / key
        folder.mkdir(parents=True, exist_ok=True)
        kept = {k: v for k, v in snapshot.items() if k not in REFERENCE}
        (folder / f"{taken}.json").write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
        return True

    def earlier(self, snapshot: dict, at_least_seconds: float = 0) -> dict | None:
        """The newest filed snapshot of this character that is older than the given
        one by at least that long. With 0: the one before it."""
        taken = snapshot.get("takenAt")
        if not isinstance(taken, (int, float)):
            return None
        for file in reversed(self._files(character_key(snapshot))):
            if int(file.stem) <= taken - max(at_least_seconds, 1):
                return self._read(file)
        return None

    def count(self, snapshot: dict) -> int:
        return len(self._files(character_key(snapshot)))


def _quest_names(*snapshots: dict) -> dict:
    """Quest id -> name, from every place a snapshot mentions one."""
    names = {}
    for snapshot in snapshots:
        for q in snapshot.get("quests") or []:
            if isinstance(q, dict) and q.get("id") is not None:
                names[q["id"]] = q.get("title")
                if isinstance(q.get("next"), dict):
                    names.setdefault(q["next"].get("id"), q["next"].get("title"))
        for d in snapshot.get("dungeons") or []:
            for q in (d.get("quests") or []) if isinstance(d, dict) else []:
                names.setdefault(q.get("id"), q.get("name"))
                for step in q.get("needs") or []:
                    names.setdefault(step.get("id"), step.get("name"))
    return {k: v for k, v in names.items() if v}


def _money(snapshot: dict) -> int | None:
    """Copper, from the exact figure when the addon saved one, else from whole gold."""
    c = snapshot.get("character") or {}
    if isinstance(c.get("money"), int):
        return c["money"]
    return c["gold"] * 10000 if isinstance(c.get("gold"), int) else None


def diff(old: dict, new: dict) -> dict:
    """What changed between two snapshots of one character, as plain facts."""
    a, b = old.get("character") or {}, new.get("character") or {}
    names = _quest_names(old, new)
    done_before, done_now = set(old.get("completed") or []), set(new.get("completed") or [])
    log_before = {q["id"] for q in old.get("quests") or [] if isinstance(q, dict) and "id" in q}
    log_now = {q["id"] for q in new.get("quests") or [] if isinstance(q, dict) and "id" in q}
    finished = sorted(done_now - done_before)
    out = {
        "from": old.get("takenAt"), "to": new.get("takenAt"),
        "hours_between": round((new.get("takenAt", 0) - old.get("takenAt", 0)) / 3600, 1),
        "quests_finished": len(finished),
        "quests_finished_names": [names[i] for i in finished if i in names][:30],
        "quests_picked_up": [names.get(i, f"quest {i}") for i in sorted(log_now - log_before - done_before)][:30],
        "quests_abandoned": [names.get(i, f"quest {i}") for i in sorted(log_before - log_now - done_now)][:30],
    }
    if a.get("level") != b.get("level") and isinstance(a.get("level"), int) and isinstance(b.get("level"), int):
        out["level"] = {"from": a["level"], "to": b["level"]}
    before, now = _money(old), _money(new)
    if before is not None and now is not None and before != now:
        out["money_copper"] = {"from": before, "to": now, "change": now - before}
    if a.get("zone") and b.get("zone") and a.get("zone") != b.get("zone"):
        out["zone"] = {"from": a["zone"], "to": b["zone"]}
    skills_before = {p.get("name"): p for p in old.get("professions") or [] if isinstance(p, dict)}
    professions = []
    for p in new.get("professions") or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        was = skills_before.get(p["name"])
        if was is None:
            professions.append({"name": p["name"], "learned": True, "to": p.get("skill")})
        elif was.get("skill") != p.get("skill"):
            professions.append({"name": p["name"], "from": was.get("skill"), "to": p.get("skill")})
    if professions:
        out["professions"] = professions
    return out


def money_words(copper: int) -> str:
    sign, copper = ("-" if copper < 0 else "+"), abs(copper)
    gold, silver, rest = copper // 10000, copper % 10000 // 100, copper % 100
    parts = [f"{gold}g"] * bool(gold) + [f"{silver}s"] * bool(silver) + [f"{rest}c"] * bool(rest and not gold)
    return sign + " ".join(parts or ["0"])


def ago_words(hours: float) -> str:
    if hours < 1:
        return "less than an hour"
    if hours < 36:
        return f"{round(hours)} hour{'s' if round(hours) != 1 else ''}"
    return f"{round(hours / 24)} days"


def recap(changes: dict) -> list[str]:
    """The same facts as short lines for the window. Empty when nothing changed."""
    lines = []
    if "level" in changes:
        lines.append(f"Level {changes['level']['from']} → {changes['level']['to']}")
    if changes["quests_finished"]:
        named = changes["quests_finished_names"]
        more = changes["quests_finished"] - len(named[:4])
        text = f"{changes['quests_finished']} quest{'s' if changes['quests_finished'] != 1 else ''} finished"
        if named:
            text += ": " + ", ".join(named[:4]) + (f" and {more} more" if more > 0 else "")
        lines.append(text)
    if changes["quests_picked_up"]:
        lines.append("Picked up: " + ", ".join(changes["quests_picked_up"][:4])
                     + (f" and {len(changes['quests_picked_up']) - 4} more" if len(changes["quests_picked_up"]) > 4 else ""))
    if changes["quests_abandoned"]:
        lines.append("Dropped: " + ", ".join(changes["quests_abandoned"][:4]))
    for p in changes.get("professions", []):
        lines.append(f"{p['name']}: learned" if p.get("learned") else f"{p['name']} {p['from']} → {p['to']}")
    if "money_copper" in changes:
        lines.append("Money " + money_words(changes["money_copper"]["change"]))
    if "zone" in changes:
        lines.append(f"Now in {changes['zone']['to']}")
    return lines
