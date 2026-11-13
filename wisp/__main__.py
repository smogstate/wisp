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
from .assets import Assets
from .market import Market
from . import i18n, inbox, update
from .i18n import t
from .llm import CHATS, PROVIDERS, FakeChat, answer, list_models
from .sessions import Sessions, record

UI = Path(__file__).with_name("ui.html")
SIZE = (430, 680)   # the open window, until the player drags it to another size
SMALLEST, LARGEST = (340, 420), (1000, 1400)


def sized(value) -> tuple[int, int] | None:
    """A window size as asked for (by the page's grip, or kept in the config), held within what makes sense."""
    if not (isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in value)):
        return None
    return tuple(max(low, min(high, v)) for v, low, high in zip(value, SMALLEST, LARGEST))
ORB = 64            # the collapsed one: a small square holding the wisp
THEMES = ("auto", "dark", "light")
BATTLENET = "battlenet"   # the credential store's entry for the player's Blizzard developer client: "id:secret"
DAILY_CAP = 5.0   # dollars a day unless the player sets another limit: a cap from the first day
NO_FOLDER = "That folder has no Interface\\AddOns inside. Choose the World of Warcraft folder or its _forever_ folder."


def check(wow_dir: Path | None, call_model: bool = True, provider: str = "anthropic",
          model: str | None = None, battlenet=None, found_house=None, spend=None) -> list[tuple[str, str, str]]:
    """Everything a first run depends on, as (what, "ok" | "FAIL" | "note", detail).
    A "note" is something optional that is missing. Makes one small real model call, whose cost goes to `spend`."""
    out = []

    def add(what, ok, good="", bad="", optional=False):
        out.append((what, "ok" if ok else "note" if optional else "FAIL", good if ok else bad))

    add(t("Game folder"), wow_dir is not None, str(wow_dir), t("not chosen yet"))
    if wow_dir is not None:
        addons = wow_dir / "Interface" / "AddOns"
        state = local.addon_state(wow_dir)
        add(t("Wisp addon installed"), state == "installed", bad=t("not installed") if state == "missing" else
            t("an older version is installed: press Update addon"))
        add(t("Questie installed"), (addons / "Questie").is_dir(), bad=t("without it Wisp knows almost no locations"))
        add(t("TomTom installed"), (addons / "TomTom").is_dir(), bad=t("without it the built-in map pin is used"), optional=True)
        add(t("DungeonJournal installed"), (addons / "ForeverDungeonJournal").is_dir(),
            bad=t("without it Wisp knows nothing about dungeons: their quests, what those need first, the loot"), optional=True)
        saved = find_saved_file(wow_dir)
        if saved is None:
            # say which step is missing, from what the game has written so far
            seen = local.addon_seen_by_game(wow_dir) if state != "missing" else "missing"
            add(t("Snapshot file"), False, bad={
                "missing": t("install the addon first"),
                "unseen": t("the game has not noticed the addon yet: close the game completely, start it again, and on the character screen open AddOns and tick Wisp"),
                "disabled": t("Wisp is switched off in the game: on the character screen open AddOns and tick Wisp"),
                "enabled": t("the game has the addon but has not saved yet: log in with your character, then log out to the character screen (the game only writes addon data at logout)"),
                "unknown": t("restart the game so it sees the addon, log in with your character, then log out to the character screen (the game only writes addon data at logout)"),
            }[seen])
            return _key_checks(out, add, call_model, provider, model, spend)
        add(t("Snapshot file"), True, str(saved))
        game = Game.load(wow_dir)
        if game.problem:
            add(t("Snapshot readable"), False, bad=game.problem)
        else:
            add(t("Snapshot readable"), True, t("{quests} quests, saved {minutes} minutes ago", quests=len(game.quests), minutes=game.age_minutes()))
            add(t("Questie data in the snapshot"), (game.snapshot.get("addons") or {}).get("questie"),
                bad=t("Questie was not ready at logout; play a little, then log out again"))
            add(t("Open quests in the snapshot"), bool(game.available), t("{n} the character could pick up", n=len(game.available)),
                t("none: Questie's list of available quests was not found; tell Wisp's author which Questie version this is"),
                optional=True)
            kept = History(local.APP_DIR / "snapshots").count(game.snapshot)
            add(t("Snapshots kept for this character"), True, t("{n}: one per logout Wisp has seen", n=kept))
            if (addons / "ForeverDungeonJournal").is_dir():
                add(t("Dungeon data in the snapshot"), bool(game.dungeons), t("{n} dungeons", n=len(game.dungeons)),
                    t("not yet: log in and out once more with the new Wisp addon"))
            books = game.crafting.books
            add(t("Recipes read from the game"), bool(books), ", ".join(f"{b['profession']} {len(b['recipes'])}" for b in books),
                t("none yet: open your profession window in the game once, then log out"), optional=True)
            if (addons / "Auctionator").is_dir():
                named = sum(1 for item in game.crafting.prices if item in game.crafting.names)
                add(t("Auction prices in the snapshot"), bool(game.crafting.prices),
                    t("for {items} items, {named} of them with a name so far", items=len(game.crafting.prices), named=named),
                    t("none yet: scan the auction house with Auctionator, then log out"), optional=True)
                kept = Market(local.APP_DIR / "market.sqlite").size(game.snapshot)
                add(t("Price history kept by Wisp"), kept["rows"] > 0,
                    t("{rows} prices of {items} items, from {first} to {last}", rows=kept["rows"], items=kept["items"],
                      first=kept["first_day"], last=kept["last_day"]),
                    t("none yet: it grows with every day you scan the auction house"), optional=True)
            from .deaths import Deaths   # the combat log, read by the addon: whether this game lets it
            fallen = len(Deaths(game.snapshot).records)
            add(t("Deaths on record"), fallen > 0, str(fallen),
                t("this game does not let addons read the combat log: deaths cannot be recorded") if game.snapshot.get("combatLog") is False
                else t("none yet: the addon files the last half minute of a fight when the character dies"), optional=True)
            if (addons / "GatherMate2").is_dir():
                add(t("Gathering nodes in the snapshot"), game.gathering.total > 0,
                    t("{places} places of {kinds} kinds", places=game.gathering.total, kinds=len(game.gathering.nodes)),
                    t("none yet: GatherMate2 notes each herb, vein and pool as you gather; log out after gathering"),
                    optional=True)
            gains = [d for d in ((game.snapshot.get("xp") or {}).get("days") or []) if isinstance(d, dict)]
            add(t("Experience measured by Wisp"), bool(gains), t("{n} days of play", n=len(gains)), t("none yet: it starts with your next gain"),
                optional=True)
            ladders = game.snapshot.get("listings")
            add(t("Auction listings in the snapshot"), bool(ladders), t("the price ladders of {n} items, from the last session", n=len(ladders or [])),
                t("none from the last session: they come from a full Auctionator scan or a search for an item"), optional=True)
            add(t("Bank contents in the snapshot"), game.crafting.bank_seen is not None, t("{n} kinds of item", n=len(game.crafting.bank)),
                t("not yet: open your bank once, then log out"), optional=True)
            mine = (game.snapshot.get("ownAuctions") or {}).get("list")
            add(t("Your auctions in the snapshot"), mine is not None, t("{n} as the Auctions tab last showed them", n=len(mine or [])),
                t("not yet: look at the Auctions tab at the auction house, then log out"), optional=True)
            mails = game.snapshot.get("auctionMail")
            add(t("Auction mail noted by Wisp"), bool(mails), t("{n} purchases and sales in the last two months", n=len(mails or [])),
                t("none yet: open a mailbox that holds auction mail, then log out"), optional=True)
            looted = sum(s["looted"] for r in game.farming.records for s in r["sources"])
            add(t("Looting measured by Wisp"), looted > 0,
                t("{looted} corpses and nodes over {days} days", looted=looted, days=len({r["day"] for r in game.farming.records})),
                t("none yet: it starts with the first thing you loot after this update"), optional=True)
            if (addons / "Questie").is_dir():
                every = game.questdb()
                failed = game.snapshot.get("indexFailed") if isinstance(game.snapshot.get("indexFailed"), dict) else {}

                def not_yet(name):   # why the game could not read a list, when it said; else how it comes
                    return (t("the game could not read it: {why}", why=str(failed[name])[:300]) if failed.get(name) else
                            t("not yet: Wisp's addon reads them in the background about a minute after login; then /wisp sync"))
                add(t("Every quest from Questie"), every is not None, t("{n} quests this character could take", n=len(every.quests) if every else 0),
                    not_yet("quests"), optional=True)
            add(t("Every recipe in the game"), bool(game.crafting.world_recipes) or game.recipes_read and bool(game.crafting.recipes),
                t("{n} recipes beyond your own professions", n=len(game.crafting.world_recipes)),
                t("the game gave none: tell Wisp's author") if game.recipes_read else
                t("not yet: Wisp's addon reads them in the background about a minute after login; then /wisp sync"), optional=True)
            add(t("One-click cards"), bool((game.snapshot.get("addons") or {}).get("inbox")), t("the game reads Wisp's inbox"),
                t("not yet: restart the game once, keep its sound enabled (the volume may be zero), then /wisp sync"), optional=True)
            if (addons / "Questie").is_dir():
                add(t("Every item from Questie"), bool(game.graph.world), t("{n} items with who sells or drops them", n=len(game.graph.world)),
                    not_yet("items"), optional=True)
            places = sum(1 for ref in game.places if ref.startswith("q") and (".start." in ref or ".end." in ref))
            add(t("Quest locations found"), places > 0, t("{n} for quest givers and turn-ins", n=places), t("none: is your quest log empty?"))
            probe = game.snapshot.get("nameProbe") if isinstance(game.snapshot.get("nameProbe"), dict) else None
            if probe:   # whose names Wisp shows: Questie's, next to the game's own, for a few of the character's quests
                said = lambda v: f"«{v}»" if isinstance(v, str) and v else "–"
                for kind, label in (("quests", t("Names of quests")), ("people", t("Names of people"))):
                    rows = [r for r in probe.get(kind) or [] if isinstance(r, dict)][:3]
                    if rows:
                        add(label, True, "; ".join(f"Questie {said(r.get('questie'))}, {t('the game')} {said(r.get('game') or r.get('log'))}" for r in rows))
                add(t("Languages"), True, f"{t('the game')}: {probe.get('locale') or '?'} · Questie: {probe.get('questieLocale') or '?'} · "
                    + t("the game can be asked for a quest's name: {a}, for a person's: {b}",
                        a=t("yes") if probe.get("canAskTitle") else t("no"), b=t("yes") if probe.get("canAskPerson") else t("no")))
            if battlenet == "missing":
                add(t("Blizzard API"), False, bad=t("not set up: optional, in Settings"), optional=True)
            elif battlenet is not None:   # the player's own developer client: try Blizzard's data for real
                from .blizzard import probe
                for what, worked, detail in probe(battlenet, game.snapshot, remember=found_house):
                    add(what, worked, detail, detail)
    return _key_checks(out, add, call_model, provider, model, spend)


def provider_key(provider: str) -> tuple[str | None, str | None]:
    return local.get_key(provider, PROVIDERS[provider]["env"])


def _key_checks(out, add, call_model, provider: str = "anthropic", model: str | None = None, spend=None):
    for name, info in PROVIDERS.items():
        key, source = provider_key(name)
        add(t("{company} key", company=info["company"]), key is not None, t("from environment") if source == "environment" else t("saved on this PC"),
            t("not entered yet"), optional=name != provider)
    key, _ = provider_key(provider)
    if not call_model or key is None:
        return out
    model = model or PROVIDERS[provider]["model"]
    try:
        chat = CHATS[provider](key, model=model)
    except ImportError as e:
        add(t("A real call to the model"), False, bad=t("a package is missing: {name}", name=e.name))
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
    if spend:
        spend(chat.usage.get("counted", chat.usage["cost"]))
    add(t("A real call to the model"), bool(text.strip()) and not problem, t("{model} answered: {reply}", model=model, reply=text.strip()[:40]),
        problem or t("the model sent no text"))
    return out


class App:
    def __init__(self, wow_dir: Path | None, fake: bool, persist: bool = False):
        self.wow_dir, self.fake = wow_dir, fake
        self.persist = persist                   # False in tests and --fake: never touch real settings or keys
        self.token = secrets.token_urlsafe(16)   # only our own page may call the API
        self.busy = threading.Lock()             # one question at a time
        self.updating = threading.Lock()         # one look for a newer version at a time
        self._game = None                        # (what the game's file looked like, the Game built from it)
        self.stop = threading.Event()
        self.chat = None
        self.problem = None                      # why the model cannot be used, in words
        self.window = None                       # the pywebview window, when there is one
        self.frameless = False                   # Wisp draws its own window: no title bar, see-through
        self.handle = None                       # the Windows handle of that window
        self.active = True                       # the mouse is over it or it has the keyboard
        self.collapsed = False
        self.round = False                       # whether the collapsed window really is a disc
        self.size = (sized(local.load_config().get("size")) if persist else None) or SIZE   # the open window, as last dragged
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
                      "auto_update": saved.get("auto_update") is not False,
                      # auto: the language of the game client, as the addon reported it; else Windows' own
                      "language": saved.get("language") if saved.get("language") in i18n.LANGUAGES else "auto",
                      "bnet_region": saved.get("bnet_region") if saved.get("bnet_region") in ("eu", "us", "kr", "tw") else "eu",
                      # dollars a day across every conversation; 0 is "no limit", which the player must choose
                      "daily_cap": saved["daily_cap"] if isinstance(saved.get("daily_cap"), (int, float))
                                   and not isinstance(saved.get("daily_cap"), bool) and 0 <= saved["daily_cap"] <= 1000 else DAILY_CAP}
        self.spend_file = (local.APP_DIR if persist else Path(tempfile.mkdtemp(prefix="wisp-spend-"))) / "spend.json"
        self._spent_here: dict[str, float] = {}   # today's total as this run counted it: stands when the file cannot
        # this copy's own version, and where the newer one stands: off (run from source, or switched off),
        # current, downloading, ready (downloaded and checked: a restart away), failed
        mine = update.current()
        self.exe = update.exe()
        self.code_dir = (local.APP_DIR if persist else Path(tempfile.mkdtemp(prefix="wisp-code-"))) / "code"      # downloaded code
        self.stage_dir = (local.APP_DIR if persist else Path(tempfile.mkdtemp(prefix="wisp-stage-"))) / "update"  # a downloaded exe
        self.try_code = lambda folder: update.try_code(self.exe, folder)
        self.update = {"version": mine["commit"][:7] if mine else None, "built": (mine or {}).get("built"),
                       "state": "off" if not mine or self.exe is None else "current", "new": None}
        # conversations are files on this PC; a throwaway folder when nothing real may be touched
        self.sessions = Sessions(local.APP_DIR / "sessions" if persist else Path(tempfile.mkdtemp(prefix="wisp-sessions-")))
        self.session = self.sessions.new()
        self.history = History(local.APP_DIR / "snapshots" if persist else Path(tempfile.mkdtemp(prefix="wisp-snapshots-")))
        self.market = Market((local.APP_DIR if persist else Path(tempfile.mkdtemp(prefix="wisp-market-"))) / "market.sqlite")
        self.assets = Assets(self.market.file)   # investments live in the same file as the prices
        from .blizzard import Live   # the auction house as Blizzard publishes it, when the player has set a client up

        def remember_house(where):
            if self.persist:
                local.save_config({**local.load_config(), "bnet_house": where})
        self.live = Live(lambda: None if self.battlenet() in (None, "missing") else self.battlenet(), saved.get("bnet_house"), remember_house)
        self.notes = (local.APP_DIR if persist else Path(tempfile.mkdtemp(prefix="wisp-notes-"))) / "notes"   # research reports
        self.addon_note = None                   # what Wisp just did to the addon, for Settings to show

    def check_update(self, base: str = update.RELEASE, asked: bool = False) -> str:
        """Look for a newer build and fetch it. Returns the state it left.
        Nearly always that is Wisp's code alone, into Wisp's own folder; the exe is
        fetched only when the newer build needs a different one. `asked`: the player
        pressed the button, so it looks even when updating by itself is switched off."""
        if not self.updating.acquire(blocking=False):   # the look every six hours and the button, never both at once
            return self.update["state"]
        try:
            return self._check_update(base, asked)
        finally:
            self.updating.release()

    def _check_update(self, base: str, asked: bool) -> str:
        mine, state = update.current(), self.update
        if not mine or self.exe is None or not (asked or self.prefs["auto_update"]):
            state["state"] = "off"
            return "off"
        info = update.latest(base)
        if info is None or info["commit"] == mine["commit"]:
            state["state"] = "current"
            return "current"
        state["new"], state["kind"] = info["commit"][:7], None
        same_exe = "code_sha256" in info and info.get("runtime") is not None and info.get("runtime") == mine.get("runtime")
        if same_exe:
            if update.running(self.code_dir) != info["commit"]:
                state["state"] = "downloading"
                if not update.install_code(info, self.code_dir, base, tried=self.try_code):
                    state["state"] = "failed"
                    return "failed"
                update.tidy_code(self.code_dir, {mine["commit"], info["commit"]})
            state["kind"] = "code"
        else:
            if update.ready(self.stage_dir, info["sha256"]) is None:
                state["state"] = "downloading"
                if not update.fetch(base + "Wisp.exe", update.pending(self.stage_dir), info["size"], info["sha256"]):
                    state["state"] = "failed"   # no connection, or no room
                    return "failed"
            if self.persist:
                local.save_config({**local.load_config(), "update_sha256": info["sha256"]})
            self.update_sha256 = info["sha256"]
            state["kind"] = "exe"
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
        """Start again as the new version. The window goes with this process."""
        if self.exe is None or self.update["state"] != "ready":
            return {"error": t("There is no downloaded update to restart into.")}
        try:
            if self.update.get("kind") == "exe":
                new = update.ready(self.stage_dir, getattr(self, "update_sha256", None))
                if new is None:
                    return {"error": t("The downloaded update is no longer there. Wisp will fetch it again.")}
                update.swap(self.exe, new, self.stage_dir)
            update.relaunch(self.exe, [])   # the launcher picks the new code up by itself
        except OSError as e:
            return {"error": t("Could not update: {why}. Wisp will try again when it next starts.", why=e.strerror or e)}
        threading.Timer(0.3, self.close_window).start()   # after this answer has gone out
        return {"restarting": True}

    # -- what today has cost ----------------------------------------------------
    # One number per day, in dollars, across every conversation: {"2026-11-10": 0.42}.
    # Every model response is counted: at its price where Wisp knows it, at the dearest price it knows otherwise
    # (llm.add_usage, "counted"). The day's total is kept in a file and in memory, whichever is higher, so a file
    # that cannot be read or written does not open the limit while Wisp runs.

    def _spend(self) -> dict:
        try:
            ledger = json.loads(self.spend_file.read_text(encoding="utf-8"))
            return ledger if isinstance(ledger, dict) else {}
        except (OSError, ValueError):
            return {}

    def spent_today(self) -> float:
        today = time.strftime("%Y-%m-%d")
        value = self._spend().get(today)
        kept = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
        return max(kept, self._spent_here.get(today, 0.0))

    def add_spend(self, dollars: float):
        if dollars <= 0:
            return
        ledger, today = self._spend(), time.strftime("%Y-%m-%d")
        ledger[today] = round(self.spent_today() + dollars, 6)
        self._spent_here = {today: ledger[today]}
        for day in sorted(ledger)[:-60]:   # two months is plenty
            del ledger[day]
        try:
            self.spend_file.parent.mkdir(parents=True, exist_ok=True)
            partial = self.spend_file.with_suffix(".part")
            partial.write_text(json.dumps(ledger), encoding="utf-8")
            os.replace(partial, self.spend_file)
        except OSError:
            pass

    def spend(self) -> dict:
        return {"today": round(self.spent_today(), 4), "cap": self.prefs["daily_cap"]}

    def language(self, snapshot: dict | None = None) -> str:
        """The language Wisp writes in: the one chosen, else the game client's, else this PC's."""
        if self.prefs["language"] in i18n.LANGUAGES:
            return self.prefs["language"]
        game_locale = ((snapshot or {}).get("client") or {}).get("locale")
        return i18n.language_of(game_locale) if game_locale else local.system_language()

    def ensure_addon(self):
        """One package: Wisp carries its addon and keeps the game's copy current,
        without being asked. Only ever writes its own Interface\\AddOns\\Wisp folder."""
        if self.wow_dir is None:
            return
        before = local.addon_state(self.wow_dir)
        if before != "installed":
            problem = local.install_addon(self.wow_dir)
            if problem:
                self.addon_note = problem
                return
            self.addon_note = (t("Wisp installed its addon into the game. Restart the game so it loads.") if before == "missing"
                               else t("Wisp updated its addon in the game. Restart the game so the new version loads."))
        try:   # the one-click inbox: its own folders beside the addon; the game finds new files only when it starts
            if inbox.install(self.wow_dir / "Interface" / "AddOns") and not self.addon_note:
                self.addon_note = t("Wisp added what one-click cards need. Restart the game once so it sees the new files.")
        except OSError:
            pass   # cards still go by copy and paste

    def can_deliver(self, snapshot: dict) -> bool:
        """Whether a card can be sent to the game at a click: the files are there, and the game said at its last save that it reads them."""
        return (self.wow_dir is not None and bool((snapshot.get("addons") or {}).get("inbox"))
                and inbox.installed(self.wow_dir / "Interface" / "AddOns"))

    def item_card(self, name) -> dict:
        """A link on a card: that item's own card, straight from the graph. No model is asked, so it costs nothing."""
        game = self.load_game()
        item, instead = game._one_item({"item": name})
        if instead:
            return {"error": instead.get("error") or t("Nothing can be shown for that item.")}
        craftable = any(r["reagents"] for r in game.graph.made_by.get(item, []))
        game.cards = []
        (game.tool_craft_tree if craftable else game.tool_item_uses)({"item": game.graph.names[item], "show": True})
        if not game.cards:
            return {"error": t("Nothing can be shown for that item.")}
        card = game.cards[-1]
        card["id"] = f"link{int(time.time() * 1000)}"
        if self.session["turns"]:   # kept with the conversation it was opened in
            self.session["turns"][-1]["items"].append({"type": "card", "card": card})
            self.sessions.save(self.session)
        return {"card": card}

    def deliver(self, text) -> dict:
        if self.wow_dir is None:
            return {"error": t("Choose the game folder first.")}
        problem = inbox.deliver(self.wow_dir / "Interface" / "AddOns", text)
        return {"error": t("Could not send it to the game: {why}", why=problem)} if problem else {"sent": True}

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
            self.problem = t("No {company} key yet. Open Settings and paste one.", company=info["company"])
            return
        try:
            self.chat = CHATS[provider](key, model=model, **history)
        except ImportError as e:
            self.problem = t("A package is missing: {name}", name=e.name)
        except Exception as e:
            self.problem = t("Cannot start the model: {why}", why=e)

    def _stamp(self):
        """What the game last wrote, by which a change is told: the saved file, when it was written, its size."""
        try:
            saved = find_saved_file(self.wow_dir) if self.wow_dir else None
            stat = saved.stat() if saved else None
            return (str(saved), stat.st_mtime_ns, stat.st_size) if stat else None
        except OSError:
            return None

    def load_game(self) -> Game:
        """The game as the addon last saved it, with everything built on it: quests, items, recipes and how they
        hang together. Built once and kept; built again only when the game has written its file anew (a logout,
        a /wisp sync) or Wisp's language has changed. A new snapshot is filed into the history as it is first seen."""
        stamp = self._stamp()
        kept = self._game
        if kept is not None and stamp is not None and kept[0] == stamp and self.language(kept[1].snapshot) == i18n.LANG:
            return kept[1]
        game = Game.load(self.wow_dir)
        wanted = self.language(game.snapshot)
        if wanted != i18n.LANG:
            i18n.set_language(wanted)
            game = Game.load(self.wow_dir)   # the names it gives to places are written in that language too
        self._game = (stamp, game) if game.snapshot and stamp is not None else None
        if game.snapshot:
            self.history.ingest(game.snapshot)
            game.history = self.history
            game.notes_dir = self.notes
            game.sim_dir = local.APP_DIR / "sim"
            game.assets = self.assets
            game.live = self.live
            try:   # what the auction house's mail said, kept beyond the addon's sixty days
                from .history import character_key
                from .market import market_key
                self.assets.ingest_mail(market_key(game.snapshot), character_key(game.snapshot), game.snapshot.get("auctionMail"))
            except Exception:
                pass
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

    def addon_behind(self, game) -> str | None:
        """What to tell the player when the game's data was written by an earlier addon than the one Wisp has
        installed: after an update the game keeps running the old addon until its interface loads again, and the
        data it saves is the old addon's. None when the two match, or when this Wisp has no version (run from source)."""
        mine = self.update.get("version")
        if not mine or not game.snapshot or game.snapshot.get("addonBuild") == mine:
            return None
        return t("This game data was saved by an earlier version of Wisp's addon, so what is new is not in it yet. In the game type /wisp sync, and once the interface has reloaded, /wisp sync again.")

    def state(self) -> dict:
        game = self.load_game()
        character = game.snapshot.get("character") or {}
        return {
            "recap": self.recap(game),
            "snapshots": self.history.count(game.snapshot) if game.snapshot else 0,
            "model": t("scripted test model") if self.fake or self.chat is None else
                     f"{PROVIDERS[self.chat.provider]['label']} · {self.chat.model}",
            "model_problem": self.problem,
            "snapshot_problem": game.problem,
            "addon_behind": self.addon_behind(game),
            "snapshot_age_minutes": game.age_minutes(),
            "quests": len(game.quests),
            "level": character.get("level"),
            "questie": (game.snapshot.get("addons") or {}).get("questie"),
            "needs_setup": self.wow_dir is None or (self.chat is None and not self.fake),
            "inbox": self.can_deliver(game.snapshot),
            "theme": self.prefs["theme"],
            "opacity": self.prefs["opacity"],
            # the page asks for the names and their rarity (/api/item-colours) only when this changes
            "item_colours": f"{len(game.item_colours())}:{game.snapshot.get('takenAt', 0)}" if game.snapshot else "0",
            # two ways to be see-through: the page's own background (where the system blends single pixels),
            # or the whole window's opacity (Windows), which the page drives by reporting when it is in use
            "window": {"own": self.window is not None, "glass": self.frameless and os.name != "nt",
                       "fade": self.frameless and os.name == "nt", "collapsed": self.collapsed, "hotkey": self.hotkey},
            "usage": dict(self.chat.usage) if self.chat is not None else None,
            "update": dict(self.update),
            "spend": self.spend(),
            "lang": i18n.LANG,
        }

    def settings(self) -> dict:
        return {
            "wow_dir": str(self.wow_dir) if self.wow_dir else "",
            "guesses": [str(p) for p in local.guess_game_folders()],
            # per provider: its name, what a key looks like, where its key is from (never the key), its model
            "providers": [{"id": name, "label": info["label"], "company": info["company"], "hint": info["hint"],
                           "key": t("scripted model, no key needed") if self.fake else provider_key(name)[1],
                           "model": self.models[name]} for name, info in PROVIDERS.items()],
            "provider": self.provider,
            "addon": local.addon_state(self.wow_dir),
            "addon_note": self.addon_note,
            "can_browse": self.window is not None,
            "theme": self.prefs["theme"],
            "opacity": self.prefs["opacity"],
            "plain_window": self.prefs["plain_window"],
            "auto_update": self.prefs["auto_update"],
            "language": self.prefs["language"],
            "battlenet": local.get_key(BATTLENET, "WISP_BATTLENET")[1] if self.persist else None, "bnet_region": self.prefs["bnet_region"],
            "update": dict(self.update),
            "spend": self.spend(),
            "glass": self.frameless,   # either way of being see-through: the slider applies
        }

    def save_settings(self, body: dict) -> dict:
        """Returns {"error": ...} or the new settings."""
        if isinstance(body.get("wow_dir"), str) and body["wow_dir"].strip():
            folder = local.game_folder(body["wow_dir"])
            if folder is None:
                return {"error": t(NO_FOLDER)}
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
        if body.get("language") in ("auto", *i18n.LANGUAGES):
            self.prefs["language"] = body["language"]
        if body.get("bnet_region") in ("eu", "us", "kr", "tw"):
            self.prefs["bnet_region"] = body["bnet_region"]
        client = body.get("battlenet") if isinstance(body.get("battlenet"), dict) else {}
        if all(isinstance(client.get(k), str) and 0 < len(client[k].strip()) <= 200 and ":" not in client[k] for k in ("id", "secret")) and self.persist:
            problem = local.set_key(f"{client['id'].strip()}:{client['secret'].strip()}", BATTLENET)
            if problem:
                return {"error": problem}
        cap = body.get("daily_cap")
        if isinstance(cap, (int, float)) and not isinstance(cap, bool) and 0 <= cap <= 1000:
            self.prefs["daily_cap"] = round(float(cap), 2)
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

    def battlenet(self):
        """The player's Blizzard developer client for Check setup: a client, "missing", or None in tests and --fake."""
        if self.fake or not self.persist:
            return None
        from .blizzard import Blizzard
        return Blizzard.from_saved(local.get_key(BATTLENET, "WISP_BATTLENET")[0], self.prefs["bnet_region"]) or "missing"

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
        update.started_fine(local.APP_DIR / "code")   # tells the launcher this copy of the code came up
        if not self.frameless:
            return
        from webview.window import FixPoint
        self.window.resize(*self.size, fix_point=FixPoint.NORTH | FixPoint.EAST)   # a borderless window starts a little small
        self.handle = local.window_handle(self.window)
        local.round_window_corners(self.handle)
        self.apply_alpha()

    def set_collapsed(self, collapsed: bool) -> dict:
        """Shrink the window to the wisp, or bring it back, keeping its top-right corner where it is."""
        self.collapsed = bool(collapsed)
        self.round = os.name != "nt"   # elsewhere the window is see-through and the page draws the disc itself
        if self.window is not None:
            if self.frameless:
                from webview.window import FixPoint
                width, height = (ORB, ORB) if self.collapsed else self.size
                self.handle = local.window_handle(self.window) or self.handle   # asked for anew: Windows may have made the window again
                if not self.collapsed:
                    local.set_window_round(self.handle, False)
                self.window.resize(width, height, fix_point=FixPoint.NORTH | FixPoint.EAST)
                if self.collapsed:
                    time.sleep(0.15)   # let the resize land before the shape is cut from the new size
                    self.round = local.set_window_round(self.handle, True) or self.round
                self.apply_alpha()
            elif self.collapsed:
                self.window.minimize()   # an ordinary window just goes to the taskbar
            else:
                self.window.restore()
        return {"collapsed": self.collapsed, "round": self.round}   # not round: the page draws a square tile instead

    def set_size(self, size, keep: bool = False) -> dict:
        """The page's grip was dragged: Wisp's own window follows, its top-right corner staying where it is."""
        size = sized(size)
        if size is not None and self.window is not None and self.frameless and not self.collapsed:
            from webview.window import FixPoint
            self.size = size
            self.window.resize(*size, fix_point=FixPoint.NORTH | FixPoint.EAST)
            if keep and self.persist:
                local.save_config({**local.load_config(), "size": list(size)})
        return {"size": list(self.size)}

    def window_resized(self, *_):
        """Whatever resizes the collapsed window afterwards (a change of screen or scaling) leaves the old cut: cut it again."""
        if self.collapsed and self.frameless and os.name == "nt":
            self.round = local.set_window_round(local.window_handle(self.window) or self.handle, True)

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
            return {"error": t("Choose the game folder first.")}
        problem = local.install_addon(self.wow_dir)
        if problem:
            return {"error": problem}
        self.addon_note = t("The addon is installed. Restart the game so it loads.")
        return self.settings()

    def browse(self) -> dict:
        if self.window is None:
            return {"error": t("Type or paste the folder path.")}
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
        cap = self.prefs["daily_cap"]
        if cap and self.spent_today() >= cap:   # fail closed: not one more request once the day's limit is reached
            yield {"type": "error", "message": t("Today's spending limit of ${cap} is reached (${spent} spent). It starts again tomorrow; the limit is in Settings.",
                                                 cap=f"{cap:.2f}", spent=f"{self.spent_today():.2f}")}
            yield {"type": "done", "reason": "error"}
            return
        self.stop.clear()
        game = self.load_game()   # re-read: you may have logged out since the last question
        session, chat = self.session, self.chat
        spent_so_far = lambda usage: usage.get("counted", usage["cost"])   # a conversation kept by an older Wisp has no "counted"
        counted, over = spent_so_far(chat.usage), [False]
        turn = {"items": [{"type": "user", "text": question}], "end": 0}
        session["turns"].append(turn)
        session["title"] = session["title"] or question[:60]
        session["provider"], session["model"] = chat.provider, chat.model
        try:
            for event in answer(chat, game, question, lambda: self.stop.is_set() or over[0], research=research):
                if event["type"] == "usage":   # after every model response: add what it cost to the day
                    self.add_spend(spent_so_far(event["usage"]) - counted)
                    counted = spent_so_far(event["usage"])
                    over[0] = bool(cap) and self.spent_today() >= cap
                if event["type"] == "done" and event["reason"] == "stopped" and over[0] and not self.stop.is_set():
                    said = {"type": "error", "message": t("Stopped here: today's spending limit of ${cap} is reached. It starts again tomorrow; the limit is in Settings.",
                                                          cap=f"{cap:.2f}")}
                    record(turn, said)
                    yield said
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
            return {"error": t("That conversation could not be opened.")}
        held_with = session.get("provider")
        if not self.fake and held_with in PROVIDERS and provider_key(held_with)[0] is None:
            return {"error": t("That conversation was held with {label}. Add a {company} key in Settings to continue it.",
                               label=PROVIDERS[held_with]["label"], company=PROVIDERS[held_with]["company"])}
        self.new_chat(session)
        return self.view()

    def fork(self, turn) -> dict:
        fork = self.sessions.fork(self.session, turn)
        if fork is None:
            return {"error": t("There is nothing to fork yet.")}
        self.new_chat(fork)
        return self.view()


def make_handler(app: App, port: int):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        timeout = 60   # a connection that sends nothing does not hold a thread for ever

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
            if path == "/" and self.headers.get("Host") in hosts and secrets.compare_digest(query, f"t={app.token}"):
                page = UI.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(page)
            elif path == "/api/item-colours" and self.allowed():
                game = app.load_game()
                self.send_json(200, {"colours": game.item_colours() if game.snapshot else {}})
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
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= 100_000:   # a negative length would read until the other side hangs up
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
            if self.path == "/api/check-update":   # the button in Settings: look now, fetch what there is
                app.check_update(asked=True)
                return self.send_json(200, {"update": dict(app.update)})
            if self.path == "/api/open":
                return self.send_json(200, app.open_outside(body.get("url")))
            if self.path == "/api/deliver":   # a card's Send button: into the running game, no paste
                return self.send_json(200, app.deliver(body.get("text")))
            if self.path == "/api/copy":
                text = body.get("text")
                # only Wisp's own messages go to the clipboard this way
                ok = isinstance(text, str) and len(text) < 100_000 and text.isascii() and (
                    text.startswith("WISP1:") or text.startswith('{"version":') and '"flavor":"forever"' in text[:60])   # or its text for a sim
                return self.send_json(200, {"copied": bool(ok and local.copy_to_clipboard(text))})
            if self.path == "/api/window":   # allowed mid-answer: the answer carries on while collapsed
                if body.get("close") is True:
                    return self.send_json(200, app.close_window())
                if isinstance(body.get("collapsed"), bool):
                    return self.send_json(200, app.set_collapsed(body["collapsed"]))
                if "size" in body:
                    return self.send_json(200, app.set_size(body.get("size"), body.get("keep") is True))
                if isinstance(body.get("active"), bool):
                    return self.send_json(200, app.set_active(body["active"]))
                return self.send_json(400, {"error": "nothing to do"})
            if self.path == "/api/sessions/peek":   # look without switching
                session = app.sessions.load(body.get("id"))
                return self.send_json(200, app.view(session) if session else {"error": t("That conversation could not be opened.")})
            if self.path in ("/api/new", "/api/settings", "/api/install-addon", "/api/browse", "/api/check",
                             "/api/sessions/open", "/api/fork", "/api/item-card"):
                if app.busy.locked():
                    return self.send_json(409, {"error": t("Wait for the answer to finish.")})
                if self.path == "/api/new":
                    app.new_chat()
                    return self.send_json(200, {})
                if self.path == "/api/sessions/open":
                    return self.send_json(200, app.open_session(body.get("id")))
                if self.path == "/api/fork":
                    return self.send_json(200, app.fork(body.get("turn")))
                if self.path == "/api/item-card":
                    return self.send_json(200, app.item_card(body.get("item")))
                if self.path == "/api/settings":
                    return self.send_json(200, app.save_settings(body))
                if self.path == "/api/install-addon":
                    return self.send_json(200, app.install_addon())
                if self.path == "/api/browse":
                    return self.send_json(200, app.browse())
                within = not app.prefs["daily_cap"] or app.spent_today() < app.prefs["daily_cap"]   # the limit holds here too
                results = check(app.wow_dir, call_model=not app.fake and within, provider=app.provider, model=app.models[app.provider],
                                battlenet=app.battlenet(), found_house=app.live.found, spend=app.add_spend)
                return self.send_json(200, {"results": [{"what": w, "status": s, "detail": d} for w, s, d in results]})
            if self.path != "/api/ask":
                return self.send_json(404, {"error": "not found"})
            question = body.get("text")
            if not isinstance(question, str) or not question.strip() or len(question) > 4000:
                return self.send_json(400, {"error": t("Ask something, up to 4000 characters.")})
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
    """Before anything else: if a build that needs a different exe was downloaded and checked
    last time, become it. (Code alone needs nothing here: the launcher already chose it.)"""
    app = update.exe()
    if app is None:
        return
    stage = local.APP_DIR / "update"
    if "--apply-update" in args:   # the build's own test of the swap, on a real Windows machine
        at = args.index("--apply-update")
        update.swap(app, Path(args[at + 1]).resolve(), stage)
        if "--then" in args:
            update.relaunch(app, args[args.index("--then") + 1:])
        sys.exit(0)
    if "--fake" in args or "--smoke" in args or "--check" in args:
        return
    update.tidy(app, stage)
    config = local.load_config()
    new = update.ready(stage, config.get("update_sha256")) if config.get("auto_update") is not False else None
    if new is not None:
        try:
            update.swap(app, new, stage)
            update.relaunch(app, args)
        except OSError:
            return   # the file is held, or a folder Wisp may not write to: carry on as the old one
        sys.exit(0)


def selfcheck() -> bool:
    """Does this copy of the code load and work with the libraries in this exe? Run in a
    process of its own before a downloaded copy is trusted."""
    try:
        import sqlite3   # noqa: F401
        import anthropic   # noqa: F401
        import keyring   # noqa: F401
        import openai   # noqa: F401
        import webview   # noqa: F401
        from .game import TOOLS, run_tool
        from .llm import CHATS
        assert UI.read_text(encoding="utf-8").lstrip().startswith("<") and local.ADDON_SOURCE.is_dir()
        game = Game.load(local.sample_game_folder())
        assert game.problem is None and "error" not in run_tool(game, "quests", {}) and len(TOOLS) > 5
        Market(Path(tempfile.mkdtemp(prefix="wisp-check-")) / "m.sqlite").size(game.snapshot)
        for chat in CHATS.values():
            chat("selfcheck-not-a-key")   # builds the provider's client: its library is whole
        return True
    except Exception:
        return False


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
        options = {"width": app.size[0], "height": app.size[1], "on_top": True}
        if plain:
            options["min_size"] = (320, 400)
        else:   # Wisp's own window: no title bar, see-through, collapsible to a small square
            options.update(frameless=True, easy_drag=False, transparent=True, min_size=(ORB, ORB))
        corner = local.load_config().get("corner") if app.persist else None
        if isinstance(corner, dict) and isinstance(corner.get("right"), int) and isinstance(corner.get("top"), int):
            x, y = corner["right"] - app.size[0], corner["top"]
            # only if that spot is still on a screen
            if any(s.x <= x and x + app.size[0] <= s.x + s.width and s.y <= y and y + 80 <= s.y + s.height for s in webview.screens):
                options.update(x=x, y=y)
        window = webview.create_window("Wisp", url, **options)
        app.window, app.frameless = window, not plain
        window.events.closing += app.remember_position
        window.events.shown += app.window_shown
        window.events.resized += app.window_resized
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
