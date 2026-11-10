"""Wisp overlay (MVP).

Run it with no arguments: the window opens, and Settings takes the API key
and the game folder and installs the addon.

    Wisp.exe                      (or: python -m wisp)
    Wisp.exe "D:\\Games\\World of Warcraft"

    --fake        a scripted model and a sample character: look at the interface with no key and no game
    --browser     show the interface in the normal browser instead of its own window
    --check       test the setup in the terminal and exit
    --smoke FILE  open the window, drive it through one question, write the result to FILE, exit
"""
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import local
from .game import Game, find_saved_file
from .history import History, ago_words, diff, recap
from .market import Market
from . import update
from .llm import CHATS, PROVIDERS, FakeChat, answer, list_models
from .sessions import Sessions, record

UI = Path(__file__).with_name("ui.html")
SIZE = (430, 680)   # the open window
ORB = 64            # the collapsed one: a small square holding the wisp
THEMES = ("auto", "dark", "light")
NO_FOLDER = ("That folder has no Interface\\AddOns inside. Choose the World of Warcraft folder "
             "or its _forever_ folder.")


def check(wow_dir: Path | None, call_model: bool = True, provider: str = "anthropic",
          model: str | None = None) -> list[tuple[str, str, str]]:
    """Everything a first run depends on, as (what, "ok" | "FAIL" | "note", detail).
    A "note" is something optional that is missing. Makes one small real model call."""
    out = []

    def add(what, ok, good="", bad="", optional=False):
        out.append((what, "ok" if ok else "note" if optional else "FAIL", good if ok else bad))

    add("Game folder", wow_dir is not None, str(wow_dir), "not chosen yet")
    if wow_dir is not None:
        addons = wow_dir / "Interface" / "AddOns"
        state = local.addon_state(wow_dir)
        add("Wisp addon installed", state == "installed", bad="not installed" if state == "missing" else
            "an older version is installed: press Install addon")
        add("Questie installed", (addons / "Questie").is_dir(), bad="without it Wisp knows almost no locations")
        add("TomTom installed", (addons / "TomTom").is_dir(), bad="without it the built-in map pin is used", optional=True)
        add("DungeonJournal installed", (addons / "ForeverDungeonJournal").is_dir(),
            bad="without it Wisp knows nothing about dungeons: their quests, what those need first, the loot", optional=True)
        saved = find_saved_file(wow_dir)
        if saved is None:
            # say which step is missing, from what the game has written so far
            seen = local.addon_seen_by_game(wow_dir) if state != "missing" else "missing"
            add("Snapshot file", False, bad={
                "missing": "install the addon first",
                "unseen": "the game has not noticed the addon yet: close the game completely, start it again, and on the "
                          "character screen open AddOns and tick Wisp",
                "disabled": "Wisp is switched off in the game: on the character screen open AddOns and tick Wisp",
                "enabled": "the game has the addon but has not saved yet: log in with your character, then log out to "
                           "the character screen (the game only writes addon data at logout)",
                "unknown": "restart the game so it sees the addon, log in with your character, then log out to the "
                           "character screen (the game only writes addon data at logout)",
            }[seen])
            return _key_checks(out, add, call_model, provider, model)
        add("Snapshot file", True, str(saved))
        game = Game.load(wow_dir)
        if game.problem:
            add("Snapshot readable", False, bad=game.problem)
        else:
            add("Snapshot readable", True, f"{len(game.quests)} quests, saved {game.age_minutes()} minutes ago")
            add("Questie data in the snapshot", (game.snapshot.get("addons") or {}).get("questie"),
                bad="Questie was not ready at logout; play a little, then log out again")
            kept = History(local.APP_DIR / "snapshots").count(game.snapshot)
            add("Snapshots kept for this character", True, f"{kept}: one per logout Wisp has seen")
            if (addons / "ForeverDungeonJournal").is_dir():
                add("Dungeon data in the snapshot", bool(game.dungeons), f"{len(game.dungeons)} dungeons",
                    "not yet: log in and out once more with the new Wisp addon")
            books = game.crafting.books
            add("Recipes read from the game", bool(books), ", ".join(f"{b['profession']} {len(b['recipes'])}" for b in books),
                "none yet: open your profession window in the game once, then log out", optional=True)
            if (addons / "Auctionator").is_dir():
                named = sum(1 for item in game.crafting.prices if item in game.crafting.names)
                add("Auction prices in the snapshot", bool(game.crafting.prices),
                    f"for {len(game.crafting.prices)} items, {named} of them with a name so far",
                    "none yet: scan the auction house with Auctionator, then log out", optional=True)
                kept = Market(local.APP_DIR / "market.sqlite").size(game.snapshot)
                add("Price history kept by Wisp", kept["rows"] > 0,
                    f"{kept['rows']} prices of {kept['items']} items, from {kept['first_day']} to {kept['last_day']}",
                    "none yet: it grows with every day you scan the auction house", optional=True)
            if (addons / "GatherMate2").is_dir():
                add("Gathering nodes in the snapshot", game.gathering.total > 0,
                    f"{game.gathering.total} places of {len(game.gathering.nodes)} kinds",
                    "none yet: GatherMate2 notes each herb, vein and pool as you gather; log out after gathering",
                    optional=True)
            looted = sum(s["looted"] for r in game.farming.records for s in r["sources"])
            add("Looting measured by Wisp", looted > 0, f"{looted} corpses and nodes over {len({r['day'] for r in game.farming.records})} days",
                "none yet: it starts with the first thing you loot after this update", optional=True)
            places = sum(1 for ref in game.places if ref.startswith("q") and (".start." in ref or ".end." in ref))
            add("Quest locations found", places > 0, f"{places} for quest givers and turn-ins", "none: is your quest log empty?")
    return _key_checks(out, add, call_model, provider, model)


def provider_key(provider: str) -> tuple[str | None, str | None]:
    return local.get_key(provider, PROVIDERS[provider]["env"])


def _key_checks(out, add, call_model, provider: str = "anthropic", model: str | None = None):
    for name, info in PROVIDERS.items():
        key, source = provider_key(name)
        add(f"{info['company']} key", key is not None, f"from {source}" if source == "environment" else "saved on this PC",
            "not entered yet", optional=name != provider)
    key, _ = provider_key(provider)
    if not call_model or key is None:
        return out
    model = model or PROVIDERS[provider]["model"]
    try:
        chat = CHATS[provider](key, model=model)
    except ImportError as e:
        add("A real call to the model", False, bad=f"a package is missing: {e.name}")
        return out
    # the same code path a question takes, with one plain question and no tools to call
    chat.user("Reply with the single word: ready")
    text, problem = "", None
    try:
        for event in chat.stream(lambda: False):
            if event["type"] == "text":
                text += event["text"]
            elif event["type"] == "error":
                problem = event["message"]
    except Exception as e:   # no network
        problem = str(e)[:200]
    add("A real call to the model", bool(text.strip()) and not problem, f"{model} answered: {text.strip()[:40]}",
        problem or "the model sent no text")
    return out


class App:
    def __init__(self, wow_dir: Path | None, fake: bool, persist: bool = False):
        self.wow_dir, self.fake = wow_dir, fake
        self.persist = persist                   # False in tests and --fake: never touch real settings or keys
        self.token = secrets.token_urlsafe(16)   # only our own page may call the API
        self.busy = threading.Lock()             # one question at a time
        self.stop = threading.Event()
        self.chat = None
        self.problem = None                      # why the model cannot be used, in words
        self.window = None                       # the pywebview window, when there is one
        self.frameless = False                   # Wisp draws its own window: no title bar, see-through
        self.handle = None                       # the Windows handle of that window
        self.active = True                       # the mouse is over it or it has the keyboard
        self.collapsed = False
        self.hotkey = None                       # the global show/hide key, when Windows granted it
        saved = local.load_config() if persist else {}
        models = saved.get("models") if isinstance(saved.get("models"), dict) else {}
        # which provider answers, and which of its models; a model is remembered per provider
        self.provider = saved.get("provider") if saved.get("provider") in PROVIDERS else "anthropic"
        self.models = {name: models.get(name) if isinstance(models.get(name), str) else info["model"]
                       for name, info in PROVIDERS.items()}
        self.prefs = {"theme": saved.get("theme") if saved.get("theme") in THEMES else "auto",
                      "opacity": saved.get("opacity") if isinstance(saved.get("opacity"), int) else 85,
                      "plain_window": bool(saved.get("plain_window")),
                      "auto_update": saved.get("auto_update") is not False}
        # this copy's own version, and where the newer one stands: off (run from source, or switched off),
        # current, downloading, ready (downloaded and checked: a restart away), failed
        mine = update.current()
        self.exe = update.exe()
        self.update = {"version": mine["commit"][:7] if mine else None, "built": (mine or {}).get("built"),
                       "state": "off" if not mine or self.exe is None else "current", "new": None}
        # conversations are files on this PC; a throwaway folder when nothing real may be touched
        self.sessions = Sessions(local.APP_DIR / "sessions" if persist else Path(tempfile.mkdtemp(prefix="wisp-sessions-")))
        self.session = self.sessions.new()
        self.history = History(local.APP_DIR / "snapshots" if persist else Path(tempfile.mkdtemp(prefix="wisp-snapshots-")))
        self.market = Market((local.APP_DIR if persist else Path(tempfile.mkdtemp(prefix="wisp-market-"))) / "market.sqlite")
        self.notes = (local.APP_DIR if persist else Path(tempfile.mkdtemp(prefix="wisp-notes-"))) / "notes"   # research reports
        self.addon_note = None                   # what Wisp just did to the addon, for Settings to show

    def check_update(self, base: str = update.RELEASE) -> str:
        """Look for a newer Wisp.exe and fetch it beside this one. Returns the state it left."""
        mine, state = update.current(), self.update
        if not mine or self.exe is None or not self.prefs["auto_update"]:
            state["state"] = "off"
            return "off"
        info = update.latest(base)
        if info is None or info["commit"] == mine["commit"]:
            state["state"] = "current"
            return "current"
        state["new"] = info["commit"][:7]
        if update.ready(self.exe, info["sha256"]) is None:
            state["state"] = "downloading"
            if not update.download(info, self.exe, base):
                state["state"] = "failed"   # no connection, no room, or a folder Wisp may not write to
                return "failed"
        if self.persist:
            local.save_config({**local.load_config(), "update_sha256": info["sha256"]})
        self.update_sha256 = info["sha256"]
        state["state"] = "ready"
        return "ready"

    def start_updates(self):
        """Look now, and every six hours while Wisp stays open."""
        def loop():
            while True:
                try:
                    self.check_update()
                except Exception:   # an update must never take Wisp down
                    self.update["state"] = "failed"
                time.sleep(6 * 3600)
        if self.exe is not None and self.persist:
            threading.Thread(target=loop, daemon=True).start()

    def restart_updated(self) -> dict:
        """Swap in the downloaded exe and start it. The window goes with this process."""
        new = update.ready(self.exe, getattr(self, "update_sha256", None)) if self.exe is not None else None
        if new is None:
            return {"error": "There is no downloaded update to restart into."}
        try:
            update.swap(self.exe, new)
            update.relaunch(self.exe, [])
        except OSError as e:
            return {"error": f"Could not update: {e.strerror or e}. Wisp will try again when it next starts."}
        threading.Timer(0.3, self.close_window).start()   # after this answer has gone out
        return {"restarting": True}

    def ensure_addon(self):
        """One package: Wisp carries its addon and keeps the game's copy current,
        without being asked. Only ever writes its own Interface\\AddOns\\Wisp folder."""
        if self.wow_dir is None:
            return
        before = local.addon_state(self.wow_dir)
        if before == "installed":
            return
        problem = local.install_addon(self.wow_dir)
        if problem:
            self.addon_note = problem
        elif before == "missing":
            self.addon_note = "Wisp installed its addon into the game. Restart the game so it loads."
        else:
            self.addon_note = "Wisp updated its addon in the game. Restart the game so the new version loads."

    def new_chat(self, session: dict | None = None):
        """Start a fresh conversation, or continue a saved one. A saved one stays with
        the provider it was held with: each keeps the history in its own format."""
        self.session = session or self.sessions.new()
        history = {"messages": self.session["messages"], "usage": self.session["usage"]}
        self.chat, self.problem = None, None
        if self.fake:
            self.chat = FakeChat(**history)
            return
        provider = self.session.get("provider") if self.session.get("provider") in PROVIDERS else self.provider
        model = self.session.get("model") if self.session.get("provider") == provider and self.session["turns"] else self.models[provider]
        info = PROVIDERS[provider]
        key, _ = provider_key(provider)
        if key is None:
            self.problem = f"No {info['company']} key yet. Open Settings and paste one."
            return
        try:
            self.chat = CHATS[provider](key, model=model, **history)
        except ImportError as e:
            self.problem = f"A package is missing: {e.name}"
        except Exception as e:
            self.problem = f"Cannot start the model: {e}"

    def load_game(self) -> Game:
        """The current snapshot, filed into the history the first time it is seen."""
        game = Game.load(self.wow_dir)
        if game.snapshot:
            self.history.ingest(game.snapshot)
            game.history = self.history
            game.notes_dir = self.notes
            try:
                self.market.ingest(game.snapshot)
                game.market = self.market
            except Exception:   # a locked or damaged price file must not cost the rest of Wisp
                game.market = None
        return game

    def recap(self, game: Game) -> dict | None:
        """What changed since the snapshot before this one, as short lines. No model involved."""
        older = self.history.earlier(game.snapshot) if game.snapshot else None
        if older is None:
            return None
        changes = diff(older, game.snapshot)
        lines = recap(changes)
        return {"since": ago_words(changes["hours_between"]), "lines": lines} if lines else None

    def state(self) -> dict:
        game = self.load_game()
        character = game.snapshot.get("character") or {}
        return {
            "recap": self.recap(game),
            "snapshots": self.history.count(game.snapshot) if game.snapshot else 0,
            "model": "scripted test model" if self.fake or self.chat is None else
                     f"{PROVIDERS[self.chat.provider]['label']} · {self.chat.model}",
            "model_problem": self.problem,
            "snapshot_problem": game.problem,
            "snapshot_age_minutes": game.age_minutes(),
            "quests": len(game.quests),
            "level": character.get("level"),
            "questie": (game.snapshot.get("addons") or {}).get("questie"),
            "needs_setup": self.wow_dir is None or (self.chat is None and not self.fake),
            "theme": self.prefs["theme"],
            "opacity": self.prefs["opacity"],
            # two ways to be see-through: the page's own background (where the system blends single pixels),
            # or the whole window's opacity (Windows), which the page drives by reporting when it is in use
            "window": {"own": self.window is not None, "glass": self.frameless and os.name != "nt",
                       "fade": self.frameless and os.name == "nt", "collapsed": self.collapsed, "hotkey": self.hotkey},
            "usage": dict(self.chat.usage) if self.chat is not None else None,
            "update": dict(self.update),
        }

    def settings(self) -> dict:
        return {
            "wow_dir": str(self.wow_dir) if self.wow_dir else "",
            "guesses": [str(p) for p in local.guess_game_folders()],
            # per provider: its name, what a key looks like, where its key is from (never the key), its model
            "providers": [{"id": name, "label": info["label"], "company": info["company"], "hint": info["hint"],
                           "key": "scripted model, no key needed" if self.fake else provider_key(name)[1],
                           "model": self.models[name]} for name, info in PROVIDERS.items()],
            "provider": self.provider,
            "addon": local.addon_state(self.wow_dir),
            "addon_note": self.addon_note,
            "can_browse": self.window is not None,
            "theme": self.prefs["theme"],
            "opacity": self.prefs["opacity"],
            "plain_window": self.prefs["plain_window"],
            "auto_update": self.prefs["auto_update"],
            "update": dict(self.update),
            "glass": self.frameless,   # either way of being see-through: the slider applies
        }

    def save_settings(self, body: dict) -> dict:
        """Returns {"error": ...} or the new settings."""
        if isinstance(body.get("wow_dir"), str) and body["wow_dir"].strip():
            folder = local.game_folder(body["wow_dir"])
            if folder is None:
                return {"error": NO_FOLDER}
            self.wow_dir = folder
            if self.persist:
                local.save_config({**local.load_config(), "wow_dir": str(folder)})
            self.ensure_addon()
        if body.get("theme") in THEMES:
            self.prefs["theme"] = body["theme"]
        if isinstance(body.get("opacity"), int) and not isinstance(body["opacity"], bool):
            self.prefs["opacity"] = min(100, max(30, body["opacity"]))   # never so clear that it cannot be found
        if isinstance(body.get("plain_window"), bool):
            self.prefs["plain_window"] = body["plain_window"]
        if isinstance(body.get("auto_update"), bool):
            self.prefs["auto_update"] = body["auto_update"]
        if self.persist:
            local.save_config({**local.load_config(), **self.prefs})
        self.apply_alpha()
        restart = False
        keys = body.get("keys") if isinstance(body.get("keys"), dict) else {}
        for name, key in keys.items():
            if name in PROVIDERS and isinstance(key, str) and key.strip() and self.persist:
                problem = local.set_key(key.strip(), name)
                if problem:
                    return {"error": problem}
                restart = restart or self.chat is None or name == self.provider
        chosen = body.get("provider")
        if chosen in PROVIDERS and chosen != self.provider:
            self.provider, restart = chosen, True
        model = body.get("model")
        if isinstance(model, str) and 0 < len(model.strip()) <= 100:
            self.models[self.provider] = model.strip()
            if self.chat is not None and self.chat.provider == self.provider:
                self.chat.model = model.strip()   # the same provider: the conversation simply continues on it
        if self.persist:
            local.save_config({**local.load_config(), "provider": self.provider, "models": self.models})
        result = self.settings()
        if restart and not self.fake:
            # another provider cannot read this one's history: its answers start in a new conversation
            moved = bool(self.session["turns"]) and self.chat is not None and self.chat.provider != self.provider
            if self.chat is None or self.chat.provider != self.provider or not self.session["turns"]:
                self.new_chat()
            result["new_conversation"] = moved
        return result

    def model_choices(self, provider) -> dict:
        """The provider's own list of models, for the picker. Its default is always offered."""
        if provider not in PROVIDERS:
            return {"error": "no such provider"}
        key, _ = provider_key(provider)
        models = list_models(provider, key) if key and not self.fake else []
        current = self.models[provider]
        return {"models": list(dict.fromkeys([current, PROVIDERS[provider]["model"], *models])), "current": current,
                "has_key": key is not None}

    def apply_alpha(self):
        """Windows: solid while in use and while it is the wisp, as see-through as Settings says otherwise."""
        solid = self.active or self.collapsed
        local.set_window_alpha(self.handle, 1.0 if solid else self.prefs["opacity"] / 100)

    def set_active(self, active: bool) -> dict:
        self.active = bool(active)
        self.apply_alpha()
        return {"active": self.active}

    def window_shown(self):
        """Once the window exists: its real size, rounded corners, and its starting opacity."""
        if not self.frameless:
            return
        from webview.window import FixPoint
        self.window.resize(*SIZE, fix_point=FixPoint.NORTH | FixPoint.EAST)   # a borderless window starts a little small
        self.handle = local.window_handle(self.window)
        local.round_window_corners(self.handle)
        self.apply_alpha()

    def set_collapsed(self, collapsed: bool) -> dict:
        """Shrink the window to the wisp, or bring it back, keeping its top-right corner where it is."""
        self.collapsed = bool(collapsed)
        if self.window is not None:
            if self.frameless:
                from webview.window import FixPoint
                width, height = (ORB, ORB) if self.collapsed else SIZE
                if not self.collapsed:
                    local.set_window_round(self.handle, False)
                self.window.resize(width, height, fix_point=FixPoint.NORTH | FixPoint.EAST)
                if self.collapsed:
                    time.sleep(0.15)   # let the resize land before the shape is cut from the new size
                    local.set_window_round(self.handle, True)
                self.apply_alpha()
            elif self.collapsed:
                self.window.minimize()   # an ordinary window just goes to the taskbar
            else:
                self.window.restore()
        return {"collapsed": self.collapsed}

    def toggle_from_hotkey(self):
        if self.window is None:
            return
        opening = self.collapsed
        self.window.evaluate_js("window.toggleCollapsed && toggleCollapsed()")
        if opening:   # take the keyboard, so you can type at once
            local.bring_to_front(self.window)

    def remember_position(self):
        """Where the window's top-right corner is, so it comes back to the same place."""
        if self.window is None or not self.persist:
            return
        try:
            corner = {"right": self.window.x + self.window.width, "top": self.window.y}
            local.save_config({**local.load_config(), "corner": corner})
        except Exception:
            pass

    def close_window(self) -> dict:
        self.remember_position()
        if self.window is not None:
            self.window.destroy()
        return {}

    def install_addon(self) -> dict:
        if self.wow_dir is None:
            return {"error": "Choose the game folder first."}
        problem = local.install_addon(self.wow_dir)
        if problem:
            return {"error": problem}
        self.addon_note = "The addon is installed. Restart the game so it loads."
        return self.settings()

    def browse(self) -> dict:
        if self.window is None:
            return {"error": "Type or paste the folder path."}
        import webview
        picked = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        return {"path": picked[0] if picked else ""}

    def open_outside(self, url) -> dict:
        """Open a source's page in the player's browser, or the notes folder. Only web
        addresses, and only from a real run of the app: never in tests or --fake."""
        if url == "notes":
            self.notes.mkdir(parents=True, exist_ok=True)
            target = str(self.notes)
        elif isinstance(url, str) and re.match(r"https?://[^\s\x00-\x1f]{4,2000}$", url):
            target = url
        else:
            return {"error": "not a web address"}
        if self.persist:
            try:
                if url == "notes" and hasattr(os, "startfile"):
                    os.startfile(target)   # the folder, in Explorer
                else:
                    import webbrowser
                    webbrowser.open(target)
            except OSError as e:
                return {"error": str(e)}
        return {"opened": target}

    def ask(self, question: str, research: bool = False):
        """Yields UI events for one question."""
        if self.chat is None:
            self.new_chat()
        if self.chat is None:
            yield {"type": "error", "message": self.problem}
            yield {"type": "done", "reason": "error"}
            return
        self.stop.clear()
        game = self.load_game()   # re-read: you may have logged out since the last question
        session, chat = self.session, self.chat
        turn = {"items": [{"type": "user", "text": question}], "end": 0}
        session["turns"].append(turn)
        session["title"] = session["title"] or question[:60]
        session["provider"], session["model"] = chat.provider, chat.model
        try:
            for event in answer(chat, game, question, self.stop.is_set, research=research):
                record(turn, event)
                yield event
        finally:   # also when the window went away mid-answer
            turn["end"] = len(chat.messages)
            session["messages"], session["usage"] = chat.messages, dict(chat.usage)
            self.sessions.save(session)

    def view(self, session: dict | None = None) -> dict:
        """A session as the window needs it: what was shown, never the provider's history."""
        session = session or self.session
        return {"id": session["id"], "title": session["title"], "forked": bool(session.get("forked_from")),
                "turns": [turn["items"] for turn in session["turns"]],
                "usage": session["usage"] if session is not self.session or self.chat is None else dict(self.chat.usage)}

    def open_session(self, session_id) -> dict:
        session = self.sessions.load(session_id)
        if session is None:
            return {"error": "That conversation could not be opened."}
        held_with = session.get("provider")
        if not self.fake and held_with in PROVIDERS and provider_key(held_with)[0] is None:
            return {"error": f"That conversation was held with {PROVIDERS[held_with]['label']}. "
                             f"Add a {PROVIDERS[held_with]['company']} key in Settings to continue it."}
        self.new_chat(session)
        return self.view()

    def fork(self, turn) -> dict:
        fork = self.sessions.fork(self.session, turn)
        if fork is None:
            return {"error": "There is nothing to fork yet."}
        self.new_chat(fork)
        return self.view()


def make_handler(app: App, port: int):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed(self) -> bool:
            # Host guards against DNS rebinding, Origin against other pages in the
            # browser, the token against everything else on this computer.
            if self.headers.get("Host") not in hosts:
                return False
            origin = self.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] not in hosts:
                return False
            return secrets.compare_digest(self.headers.get("X-Wisp-Token", ""), app.token)

        def send_json(self, code: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/" and self.headers.get("Host") in hosts and query == f"t={app.token}":
                page = UI.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(page)
            elif path == "/api/state" and self.allowed():
                self.send_json(200, app.state())
            elif path == "/api/settings" and self.allowed():
                self.send_json(200, app.settings())
            elif path == "/api/session" and self.allowed():
                self.send_json(200, app.view())
            elif path == "/api/models" and self.allowed():
                self.send_json(200, app.model_choices(urllib.parse.parse_qs(query).get("provider", [""])[0]))
            elif path == "/api/sessions" and self.allowed():
                search = urllib.parse.parse_qs(query).get("q", [""])[0][:200]
                self.send_json(200, {"sessions": app.sessions.list(search)})
            else:
                self.send_json(403, {"error": "forbidden"})

        def do_POST(self):
            if not self.allowed():
                return self.send_json(403, {"error": "forbidden"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > 100_000:
                return self.send_json(413, {"error": "too long"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self.send_json(400, {"error": "bad json"})
            if not isinstance(body, dict):
                return self.send_json(400, {"error": "bad json"})
            if self.path == "/api/stop":
                app.stop.set()
                return self.send_json(200, {})
            if self.path == "/api/update":
                return self.send_json(200, app.restart_updated())
            if self.path == "/api/open":
                return self.send_json(200, app.open_outside(body.get("url")))
            if self.path == "/api/copy":
                text = body.get("text")
                # only Wisp's own messages go to the clipboard this way
                ok = isinstance(text, str) and text.startswith("WISP1:") and len(text) < 100_000 and text.isascii()
                return self.send_json(200, {"copied": bool(ok and local.copy_to_clipboard(text))})
            if self.path == "/api/window":   # allowed mid-answer: the answer carries on while collapsed
                if body.get("close") is True:
                    return self.send_json(200, app.close_window())
                if isinstance(body.get("collapsed"), bool):
                    return self.send_json(200, app.set_collapsed(body["collapsed"]))
                if isinstance(body.get("active"), bool):
                    return self.send_json(200, app.set_active(body["active"]))
                return self.send_json(400, {"error": "nothing to do"})
            if self.path == "/api/sessions/peek":   # look without switching
                session = app.sessions.load(body.get("id"))
                return self.send_json(200, app.view(session) if session else {"error": "That conversation could not be opened."})
            if self.path in ("/api/new", "/api/settings", "/api/install-addon", "/api/browse", "/api/check",
                             "/api/sessions/open", "/api/fork"):
                if app.busy.locked():
                    return self.send_json(409, {"error": "Wait for the answer to finish."})
                if self.path == "/api/new":
                    app.new_chat()
                    return self.send_json(200, {})
                if self.path == "/api/sessions/open":
                    return self.send_json(200, app.open_session(body.get("id")))
                if self.path == "/api/fork":
                    return self.send_json(200, app.fork(body.get("turn")))
                if self.path == "/api/settings":
                    return self.send_json(200, app.save_settings(body))
                if self.path == "/api/install-addon":
                    return self.send_json(200, app.install_addon())
                if self.path == "/api/browse":
                    return self.send_json(200, app.browse())
                results = check(app.wow_dir, call_model=not app.fake, provider=app.provider, model=app.models[app.provider])
                return self.send_json(200, {"results": [{"what": w, "status": s, "detail": d} for w, s, d in results]})
            if self.path != "/api/ask":
                return self.send_json(404, {"error": "not found"})
            question = body.get("text")
            if not isinstance(question, str) or not question.strip() or len(question) > 4000:
                return self.send_json(400, {"error": "ask something, up to 4000 characters"})
            if not app.busy.acquire(blocking=False):
                return self.send_json(409, {"error": "busy"})
            try:
                # one JSON event per line; the connection closing ends the answer
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    for event in app.ask(question.strip(), research=body.get("research") is True):
                        self.wfile.write(json.dumps(event, ensure_ascii=False).encode() + b"\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    app.stop.set()   # the window went away: stop spending tokens
            finally:
                app.busy.release()

    return Handler


def serve(app: App, port: int = 0):
    """Start the server on a free local port. Returns (server, url)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    port = server.server_address[1]
    server.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}/?t={app.token}"


def grab(prefix: str | None, name: str):
    """A screenshot of the screen, when Pillow is there (the build uses it to show what Windows drew)."""
    if not prefix:
        return
    try:
        from PIL import ImageGrab
        time.sleep(0.8)
        ImageGrab.grab().save(f"{prefix}-{name}.png")
    except Exception:
        pass


def smoke(window, result: dict, shots: str | None = None):
    """Drive the real window through one question with the scripted model."""
    try:
        js = window.evaluate_js

        def wait(condition, seconds=30):
            deadline = time.time() + seconds
            while time.time() < deadline:
                if js(condition):
                    return True
                time.sleep(0.2)
            return False

        result["loaded"] = wait("!!document.getElementById('status') && document.getElementById('status').textContent.includes('level')")
        result["status"] = js("document.getElementById('status') ? document.getElementById('status').textContent : null")
        result["engine"] = js("navigator.userAgent")
        js("document.getElementById('input').value = 'where do I hand in the wolf quest?'; "
           "document.getElementById('form').requestSubmit(); true")
        result["answered"] = wait("!!document.querySelector('.card') && document.getElementById('send').dataset.state === 'send'")
        result["card"] = js("document.querySelector('.card li') ? document.querySelector('.card li').textContent : null")
        result["answer"] = js("[...document.querySelectorAll('.msg.wisp')].map(e => e.textContent).filter(Boolean).join(' | ')")
        js("document.querySelector('.card button') && document.querySelector('.card button').click(); true")
        wait("document.querySelector('.card button') && !!document.querySelector('.card button').dataset.state", 10)
        result["copy_button"] = js("document.querySelector('.card button') ? document.querySelector('.card button').dataset.state : null")
        # collapse to the wisp and back, in the real window
        result["glass"] = js("document.body.classList.contains('glass')")
        result["fade"] = js("fade")
        result["size_open"] = [window.width, window.height]
        js("post('/api/window', { active: true }); true")
        grab(shots, "open")
        js("post('/api/settings', { opacity: 50 }).then(() => post('/api/window', { active: false })); true")
        time.sleep(1)
        grab(shots, "idle")
        js("post('/api/window', { active: true }); true")
        js("setCollapsed(true); true")
        wait("document.body.classList.contains('collapsed')", 10)
        time.sleep(1.5)
        result["size_collapsed"] = [window.width, window.height]
        result["orb"] = js("document.getElementById('orb').dataset.state")
        grab(shots, "collapsed")
        js("setCollapsed(false); true")
        wait("!document.body.classList.contains('collapsed')", 10)
        time.sleep(1.5)
        result["size_reopened"] = [window.width, window.height]
        result["key_store"] = local.key_store_problem() or "works"
        result["version"] = (update.current() or {}).get("commit")   # the build checks it is the commit it built
        # saving the key is part of the first run on Windows; elsewhere the environment variable does
        key_store_ok = result["key_store"] == "works" or os.name != "nt"
        result["ok"] = bool(result["loaded"] and result["answered"] and result["card"] and key_store_ok
                            and result["size_collapsed"][0] < result["size_open"][0])
    except Exception as e:
        result["ok"], result["error"] = False, repr(e)
    finally:
        window.destroy()


def update_at_start(args: list[str]):
    """Before anything else: if a newer exe was downloaded and checked last time, become it."""
    app = update.exe()
    if app is None:
        return
    if "--apply-update" in args:   # the build's own test of the swap, on a real Windows machine
        at = args.index("--apply-update")
        update.swap(app, Path(args[at + 1]).resolve())
        if "--then" in args:
            update.relaunch(app, args[args.index("--then") + 1:])
        sys.exit(0)
    if "--fake" in args or "--smoke" in args or "--check" in args:
        return
    update.tidy(app)
    config = local.load_config()
    new = update.ready(app, config.get("update_sha256")) if config.get("auto_update") is not False else None
    if new is not None:
        try:
            update.swap(app, new)
            update.relaunch(app, args)
        except OSError:
            return   # a folder Wisp may not write to, or the file is held: carry on as the old one
        sys.exit(0)


def main():
    args = sys.argv[1:]
    update_at_start(args)
    fake, in_browser = "--fake" in args, "--browser" in args
    smoke_file = args[args.index("--smoke") + 1] if "--smoke" in args and len(args) > args.index("--smoke") + 1 else None
    paths = [a for a in args if not a.startswith("--") and a != smoke_file]
    fake = fake or smoke_file is not None

    wow_dir = None
    if paths:
        wow_dir = local.game_folder(paths[0])
        if wow_dir is None:
            sys.exit(f"{paths[0]}: {NO_FOLDER}")
    elif fake:
        wow_dir = local.sample_game_folder()
    else:
        wow_dir = local.game_folder(local.load_config().get("wow_dir"))
        if wow_dir is None:
            guesses = local.guess_game_folders()
            wow_dir = guesses[0] if guesses else None

    if "--check" in args:
        chosen = local.load_config().get("provider")
        results = check(wow_dir, provider=chosen if chosen in PROVIDERS else "anthropic")
        for what, status, detail in results:
            print(f"  {status:<4}  {what}{': ' + detail if detail else ''}")
        failed = sum(status == "FAIL" for _, status, _ in results)
        sys.exit(f"\n{failed} thing(s) to fix." if failed else 0)

    app = App(wow_dir, fake, persist=not fake)
    if not fake:
        app.ensure_addon()   # a newer Wisp.exe brings a newer addon
        app.start_updates()
    app.new_chat()
    server, url = serve(app)
    try:
        import webview
    except ImportError:
        webview = None
    if smoke_file and webview is None:
        sys.exit("--smoke needs pywebview")
    if webview and not in_browser:
        plain = "--plain" in args or app.prefs["plain_window"]
        options = {"width": SIZE[0], "height": SIZE[1], "on_top": True}
        if plain:
            options["min_size"] = (320, 400)
        else:   # Wisp's own window: no title bar, see-through, collapsible to a small square
            options.update(frameless=True, easy_drag=False, transparent=True, min_size=(ORB, ORB))
        corner = local.load_config().get("corner") if app.persist else None
        if isinstance(corner, dict) and isinstance(corner.get("right"), int) and isinstance(corner.get("top"), int):
            x, y = corner["right"] - SIZE[0], corner["top"]
            # only if that spot is still on a screen
            if any(s.x <= x and x + SIZE[0] <= s.x + s.width and s.y <= y and y + 80 <= s.y + s.height for s in webview.screens):
                options.update(x=x, y=y)
        window = webview.create_window("Wisp", url, **options)
        app.window, app.frameless = window, not plain
        window.events.closing += app.remember_position
        window.events.shown += app.window_shown
        if not fake and local.start_hotkey(app.toggle_from_hotkey):
            app.hotkey = local.HOTKEY_LABEL
        if smoke_file:
            result = {}
            threading.Timer(120, lambda: (result.setdefault("error", "timed out"), window.destroy())).start()
            webview.start(smoke, (window, result, str(Path(smoke_file).with_suffix(""))))
            Path(smoke_file).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            os._exit(0 if result.get("ok") else 1)   # the timer thread must not keep the process alive
        webview.start()
    else:
        print(f"Wisp is running. If no page opened, go to:\n{url}\nPress Ctrl+C to quit.")
        webbrowser.open(url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    server.shutdown()


def run():
    """Entry point for the packaged app, which has no console: failures go to a log file."""
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        local.APP_DIR.mkdir(parents=True, exist_ok=True)
        (local.APP_DIR / "wisp.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    run()
