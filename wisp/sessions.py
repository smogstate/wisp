"""Conversations kept on this PC, one JSON file each. Nothing is uploaded.

A session is:
    id, title, created, updated, forked_from
    turns     one per question: {"items": what the window showed, "end": how many
              provider messages existed once the turn was over}
    messages  the conversation in the provider's own format, so it can be continued
    usage     tokens and cost so far

"end" is what makes a fork possible at any turn: the first N turns go with the
first turns[N-1]["end"] messages.
"""
import copy
import json
import os
import re
import secrets
import time
from pathlib import Path

ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")   # also keeps an id from ever being a path


class Sessions:
    def __init__(self, folder: Path):
        self.folder = folder

    def new(self) -> dict:
        now = time.time()
        return {"id": time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3), "title": "",
                "created": now, "updated": now, "forked_from": None, "turns": [], "messages": [], "usage": None}

    def save(self, session: dict):
        if not session["turns"]:
            return   # an empty conversation is not worth a file
        session["updated"] = time.time()
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / (session["id"] + ".json")
        partial = target.with_suffix(".part")
        partial.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
        os.replace(partial, target)   # never leave a half-written conversation

    def load(self, session_id) -> dict | None:
        if not isinstance(session_id, str) or not ID.match(session_id):
            return None
        try:
            session = json.loads((self.folder / (session_id + ".json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        ok = isinstance(session, dict) and isinstance(session.get("turns"), list) and isinstance(session.get("messages"), list)
        return session if ok and session.get("id") == session_id else None

    def list(self, query: str = "") -> list[dict]:
        """Summaries, newest first. The query matches the title or anything said."""
        query = query.strip().lower()
        found = []
        for file in self.folder.glob("*.json"):
            session = self.load(file.stem)
            if session is None:
                continue
            said = " ".join(str(item.get("text", "")) for turn in session["turns"] for item in turn.get("items", []))
            if query and query not in (session.get("title", "") + " " + said).lower():
                continue
            found.append({"id": session["id"], "title": session.get("title") or "Untitled",
                          "updated": session.get("updated", 0), "questions": len(session["turns"]),
                          "forked": bool(session.get("forked_from"))})
        return sorted(found, key=lambda s: s["updated"], reverse=True)

    def fork(self, session: dict, upto: int | None = None) -> dict | None:
        """A new session holding the first upto+1 turns (all of them by default).
        The original is untouched. None if there is nothing to fork there."""
        turns = session["turns"]
        if upto is None:
            upto = len(turns) - 1
        if not isinstance(upto, int) or isinstance(upto, bool) or not 0 <= upto < len(turns):
            return None
        fork = self.new()
        fork["title"] = (session.get("title") or "Untitled")[:54] + " (fork)"
        fork["forked_from"] = session["id"]
        fork["turns"] = copy.deepcopy(turns[:upto + 1])
        fork["messages"] = copy.deepcopy(session["messages"][:turns[upto]["end"]])
        # the tokens were spent in the original; the fork starts its own count
        fork["usage"] = None
        self.save(fork)
        return fork


def record(turn: dict, event: dict):
    """Add one interface event to a turn, in the shape the window draws it."""
    items, kind = turn["items"], event["type"]
    if kind == "text":
        if items[-1]["type"] == "wisp":
            items[-1]["text"] += event["text"]
        else:
            items.append({"type": "wisp", "text": event["text"]})
    elif kind == "tool":
        items.append({"type": "trace", "name": event["name"], "ok": event["ok"]})
    elif kind == "card":
        items.append({"type": "card", "card": event["card"]})
    elif kind == "error":
        items.append({"type": "error", "text": event["message"]})
    elif kind == "done" and event["reason"] == "stopped":
        items.append({"type": "note", "text": "stopped"})
