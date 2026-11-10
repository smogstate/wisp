"""The whole MVP loop without the game or a model key:

  addon (in a Lua VM, faked game + Questie) saves a snapshot
    -> the overlay reads it and answers a question with a scripted model
    -> the action card's message is pasted back into the addon
    -> TomTom receives the waypoint.

    npm install && python3 tests/test_mvp.py
    WISP_LUA=/path/to/lua5.1 python3 tests/test_mvp.py     (no Node needed)
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wisp import local  # noqa: E402
from wisp.__main__ import App, check, serve  # noqa: E402
from wisp.game import Game, encode_wisp1, read_snapshot, run_tool  # noqa: E402
from wisp.llm import FakeChat, answer  # noqa: E402


# The addon runs under real Lua 5.1 (what the game uses) when WISP_LUA points
# at one, and under the fengari VM (Lua 5.3) through Node otherwise.
LUA = [os.environ["WISP_LUA"], "tests/wow_stub.lua"] if os.environ.get("WISP_LUA") else ["node", "tests/wow_stub.js"]


def stub(*args, flags=""):
    out = subprocess.run([*LUA, *args], cwd=ROOT, capture_output=True, text=True, env={**os.environ, "WISP_STUB": flags})
    assert out.returncode == 0, out.stderr + out.stdout
    return out.stdout.splitlines()


def make_wow_dir(tmp: Path, flags="") -> Path:
    """A fake game folder holding what the addon would have saved at logout."""
    lines = stub("snapshot", flags=flags)
    snapshot = next(l for l in lines if l.startswith("SNAPSHOT ")).split(" ", 1)[1]
    assert any("snapshot built: 2 quests" in l for l in lines), lines
    saved = tmp / "WTF/Account/TEST/SavedVariables"
    saved.mkdir(parents=True)
    # the game writes saved variables as Lua assignments
    (saved / "Wisp.lua").write_text(f'\nWispSnapshot = "{snapshot}"\n', encoding="utf-8")
    return tmp


def test_snapshot_and_tools(wow: Path):
    game = Game.load(wow)
    assert game.problem is None, game.problem
    snap = game.snapshot
    assert snap["character"]["class"] == "MAGE" and snap["character"]["level"] == 12
    assert snap["character"]["gold"] == 15 and snap["character"]["x"] == 42.1
    assert snap["addons"] == {"questie": True, "tomtom": True}
    assert [q["id"] for q in game.quests] == [33, 76], "header and hidden entries are skipped"

    wolves, mine = game.quests
    assert wolves["complete"] and wolves["objectives"] == [{"text": "8/8 Tough Wolf Meat", "done": True}]
    assert wolves["ends"][0]["name"] == "Eagan Peltskinner", "colour codes are stripped from game text"
    assert wolves["ends"][0]["where"] == [{"ref": "q33.end.1.1", "zone": "Elwynn Forest", "x": 48.9, "y": 40.2}]
    assert wolves["next_in_chain"]["title"] == "An Unwelcome Guest"
    assert wolves["next_in_chain"]["starts"][0]["kind"] == "object"
    assert wolves["next_in_chain"]["starts"][0]["where"][0]["ref"] == "q33.next.1.1"
    assert mine["game_pointer"] == {"ref": "q76.pointer", "zone": "Elwynn Forest", "x": 61.5, "y": 53.8}
    assert len(mine["starts"][0]["where"]) == 1, "an in-instance spawn (-1, -1) is not a place"

    assert run_tool(game, "quest_done", {"quest_ids": [7, 33]}) == {"completed": {"7": True, "33": False}}
    assert "error" in run_tool(game, "quest_done", {"quest_ids": "7"})
    assert "error" in run_tool(game, "waypoint", {"refs": ["q33.end.1.1", "made.up"]}), "an invented ref is refused"
    assert "error" in run_tool(game, "waypoint", {"refs": []})
    assert "error" in run_tool(game, "delete_everything", {})
    assert "error" in run_tool(game, "waypoint", "not an object")
    assert not game.cards, "a refused call makes no card"
    assert run_tool(game, "character", {})["quests_completed"] == 5


def test_question_to_waypoint(wow: Path):
    """Ask -> the scripted model reads the quests -> asks for a waypoint -> the card is pasted into the game."""
    game = Game.load(wow)
    events = list(answer(FakeChat(), game, "where do I hand in the wolf quest?"))
    kinds = [e["type"] for e in events]
    assert kinds[-1] == "done" and events[-1]["reason"] == "end", kinds
    assert [e["name"] for e in events if e["type"] == "tool"] == ["quests", "waypoint"]
    cards = [e["card"] for e in events if e["type"] == "card"]
    assert len(cards) == 1
    card = cards[0]
    assert card["lines"] == [{"addon": "TomTom", "text": "Waypoint: Eagan Peltskinner (turn in)",
                              "detail": "Elwynn Forest  48.9, 40.2"}]
    message = json.loads(base64.b64decode(card["wisp"][6:]))
    assert message == {"cmds": [{"do": "tomtom.waypoint", "map": 1429, "x": 48.9, "y": 40.2,
                                 "title": "Eagan Peltskinner (turn in)"}]}

    usage = [e["usage"] for e in events if e["type"] == "usage"]
    assert [u["requests"] for u in usage] == [1, 2, 3] and usage[-1]["output"] == 360 and usage[-1]["cost"] > 0

    from wisp.llm import add_usage, new_usage
    total = new_usage()
    add_usage(total, "claude-opus-5-5", input_tokens=1_000_000, output_tokens=500_000, cache_read=1_000_000)
    assert total["cost"] == 4 + 10 + 0.20 and total["priced"]
    add_usage(total, "some-other-model", input_tokens=10)
    assert total["input"] == 1_000_010 and not total["priced"], "an unknown model is counted but not priced"

    lines = stub("paste", card["wisp"])
    assert "WAY tomtom 1429 0.489 0.402 Eagan Peltskinner (turn in)" in lines, lines
    assert "CHAT Wisp waypoint: Eagan Peltskinner (turn in)" in lines, lines
    assert "BOX []" in lines, "the inbox closes itself after a paste"


def test_inbox_is_strict():
    def paste(text, flags=""):
        return stub("paste", text, flags=flags)

    assert "CHAT Wisp that is not a Wisp message." in paste("/run print('hi')")
    assert "CHAT Wisp could not read that message." in paste("WISP1:not-base64-json")
    assert "CHAT Wisp could not read that message." in paste("WISP1:" + base64.b64encode(b'{"cmds": 5}').decode())

    mixed = paste(encode_wisp1([
        {"do": "run.lua", "code": "os.exit()"},
        {"do": "tomtom.waypoint", "map": 1429, "x": 500, "y": 10, "title": "off the map"},
        {"do": "tomtom.waypoint", "map": 1429, "x": 10, "y": 20, "title": "ok |TInterface\\Icons\\x:0|t"},
    ]))
    assert any("skipped unknown command run.lua" in l for l in mixed), mixed
    assert any("failed bad waypoint" in l for l in mixed), mixed
    ways = [l for l in mixed if l.startswith("WAY ")]
    assert ways == ["WAY tomtom 1429 0.1 0.2 ok TInterface\\Icons\\x:0t"], "one bad command does not stop the rest; | is stripped"

    pin = paste(encode_wisp1([{"do": "tomtom.waypoint", "map": 1429, "x": 10, "y": 20, "title": "x"}]), flags="notomtom")
    assert "WAY pin 1429 0.1 0.2" in pin, "without TomTom the built-in map pin is used"


def test_without_questie(tmp: Path):
    game = Game.load(make_wow_dir(tmp, flags="noquestie"))
    assert game.snapshot["addons"]["questie"] is False
    assert game.quests[0]["ends"] == [] and "q76.pointer" in game.places, "the game's own pointer still works"
    assert "Questie was not available" in run_tool(game, "quests", {})["note"]


def test_bad_files(tmp: Path):
    assert "no Wisp snapshot found" in Game.load(tmp).problem
    saved = tmp / "WTF/Account/A/SavedVariables"
    saved.mkdir(parents=True)
    (saved / "Wisp.lua").write_text("WispSnapshot = nil\n")
    assert "log out of the game once" in Game.load(tmp).problem
    (saved / "Wisp.lua").write_text('WispSnapshot = "AAAA"\n')
    assert "damaged" in Game.load(tmp).problem
    (saved / "Wisp.lua").write_text('WispSnapshot = "%s"\n' % base64.b64encode(b'{"v": 99}').decode())
    assert "different version" in Game.load(tmp).problem
    assert "error" in run_tool(Game.load(tmp), "quests", {})
    try:
        read_snapshot(saved / "missing.lua")
        raise AssertionError("expected a ValueError")
    except ValueError:
        pass


def test_server(wow: Path):
    app = App(wow, fake=True)
    app.new_chat()
    server, url = serve(app)
    base, token = url.split("/?t=")

    def call(path, body=None, headers=None):
        h = {"X-Wisp-Token": token, "Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    try:
        assert call("/?t=" + token)[0] == 200
        assert call("/?t=wrong")[0] == 403 and call("/")[0] == 403, "the page needs the token"
        state = json.loads(call("/api/state")[1])
        assert state["quests"] == 2 and state["level"] == 12 and state["questie"] is True
        assert call("/api/state", headers={"X-Wisp-Token": "nope"})[0] == 403
        assert call("/api/state", headers={"Host": "evil.example"})[0] == 403, "DNS rebinding"
        assert call("/api/ask", {"text": "hi"}, headers={"Origin": "https://evil.example"})[0] == 403, "another site"
        assert call("/api/ask", {"text": "   "})[0] == 400
        assert call("/api/nothing", {})[0] == 404

        status, body = call("/api/ask", {"text": "where is the wolf quest?"})
        events = [json.loads(line) for line in body.splitlines()]
        assert status == 200 and events[-1] == {"type": "done", "reason": "end"}
        assert sum(e["type"] == "card" for e in events) == 1
        assert "".join(e["text"] for e in events if e["type"] == "text").startswith("Let me look at your quest log.")
        assert call("/api/new", {})[0] == 200
    finally:
        server.shutdown()


def test_this_pc(tmp: Path):
    """Choosing the game folder, installing the addon, the sample folder, the setup check."""
    root = tmp / "World of Warcraft"
    forever = root / "_forever_"
    (forever / "Interface" / "AddOns").mkdir(parents=True)
    assert local.game_folder(root) == forever, "the World of Warcraft folder is accepted"
    assert local.game_folder(str(forever)) == forever and local.game_folder(f'"{forever}"') == forever
    assert local.game_folder(tmp) is None and local.game_folder("") is None and local.game_folder(None) is None

    assert local.addon_state(forever) == "missing" and local.addon_state(None) == "missing"
    assert local.install_addon(forever) is None
    installed = forever / "Interface" / "AddOns" / "Wisp"
    assert sorted(f.name for f in installed.iterdir()) == ["Wisp.lua", "Wisp.toc"]
    assert local.addon_state(forever) == "installed"
    (installed / "Wisp.lua").write_text("-- an old version")
    assert local.addon_state(forever) == "outdated"

    sample = local.sample_game_folder()
    game = Game.load(sample)
    assert game.problem is None and len(game.quests) == 2 and "q33.end.1.1" in game.places

    results = {what: status for what, status, _ in check(sample, call_model=False)}
    assert results["Game folder"] == "ok" and results["Snapshot readable"] == "ok"
    assert results["Wisp addon installed"] == "FAIL" and results["TomTom installed"] == "note"
    assert "A real call to the model" not in results, "no model call when asked not to"
    assert check(None, call_model=False)[0] == ("Game folder", "FAIL", "not chosen yet")

    # no snapshot yet: the check says which step is missing, from what the game has written
    def why(folder):
        return next(detail for what, _, detail in check(folder, call_model=False) if what == "Snapshot file")

    local.install_addon(forever)
    assert "restart the game" in why(forever), "no character folders at all: the general advice"
    character = forever / "WTF" / "Account" / "ACC" / "Realm" / "Hero"
    character.mkdir(parents=True)
    (character / "AddOns.txt").write_text("Questie: enabled\nTomTom: enabled\n")
    assert local.addon_seen_by_game(forever) == "unseen" and "has not noticed the addon" in why(forever)
    (character / "AddOns.txt").write_text("Questie: enabled\nWisp: disabled\n")
    assert local.addon_seen_by_game(forever) == "disabled" and "switched off" in why(forever)
    (character / "AddOns.txt").write_text("Questie: enabled\nWisp: enabled\n")
    assert local.addon_seen_by_game(forever) == "enabled" and "has not saved yet" in why(forever)
    (installed / "Wisp.toc").unlink()
    assert "install the addon first" in why(forever)

    app = App(None, fake=True)   # persist is off: real settings and keys are never touched
    assert "error" in app.save_settings({"wow_dir": str(tmp)})
    assert local.addon_state(forever) == "missing"
    saved = app.save_settings({"wow_dir": str(root), "key": "sk-ant-would-be-secret"})
    assert saved["wow_dir"] == str(forever) and app.wow_dir == forever
    assert saved["addon"] == "installed" and "installed its addon" in saved["addon_note"], "one package: no button needed"
    (installed / "Wisp.lua").write_text("-- an old version")
    app.ensure_addon()
    assert local.addon_state(forever) == "installed" and "updated its addon" in app.addon_note
    assert "sk-ant" not in json.dumps(app.settings()), "the key is never sent back"
    assert "error" in App(None, fake=True).install_addon()


def test_settings_api(wow: Path):
    app = App(wow, fake=True)
    app.new_chat()
    server, url = serve(app)
    base, token = url.split("/?t=")

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Wisp-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    try:
        (wow / "Interface" / "AddOns").mkdir(parents=True, exist_ok=True)
        settings = call("/api/settings")
        assert settings["wow_dir"] == str(wow) and settings["addon"] == "missing" and settings["can_browse"] is False
        assert "error" in call("/api/settings", {"wow_dir": str(wow / "nowhere")})
        assert call("/api/install-addon", {})["addon"] == "installed"
        results = call("/api/check", {})["results"]
        assert {"what": "Wisp addon installed", "status": "ok", "detail": ""} in results
        assert call("/api/copy", {"text": "anything else"}) == {"copied": False}, "only Wisp messages go to the clipboard"
        assert "error" in call("/api/browse", {})

        # appearance: kept, and kept within bounds
        state = call("/api/state")
        assert state["theme"] == "auto" and state["opacity"] == 85
        assert state["window"] == {"own": False, "glass": False, "fade": False, "collapsed": False, "hotkey": None}
        saved = call("/api/settings", {"theme": "dark", "opacity": 5, "plain_window": True})
        assert (saved["theme"], saved["opacity"], saved["plain_window"]) == ("dark", 30, True), "never clearer than 30"
        assert call("/api/settings", {"theme": "neon", "opacity": "70"})["theme"] == "dark", "nonsense is ignored"
        assert call("/api/window", {"collapsed": True}) == {"collapsed": True}
        assert call("/api/state")["window"]["collapsed"] is True
        assert call("/api/window", {"collapsed": False}) == {"collapsed": False}
        assert call("/api/window", {"active": False}) == {"active": False}, "the page reports when it is not in use"
    finally:
        server.shutdown()


def test_sessions(wow: Path, tmp: Path):
    """Conversations are saved, listed, searched, reopened and forked."""
    from wisp.sessions import Sessions

    def ask(app, question):
        return list(app.ask(question))

    app = App(wow, fake=True)
    app.sessions = Sessions(tmp / "sessions")
    app.new_chat()
    assert app.fork(None) == {"error": "There is nothing to fork yet."}
    ask(app, "where do I hand in the wolf quest?")
    ask(app, "and the mine?")
    original = app.session["id"]
    assert (tmp / "sessions" / f"{original}.json").is_file()
    view = app.view()
    assert view["title"] == "where do I hand in the wolf quest?" and len(view["turns"]) == 2
    kinds = [item["type"] for item in view["turns"][0]]
    assert kinds == ["user", "wisp", "trace", "trace", "card", "wisp"], kinds
    assert view["turns"][0][1]["text"].startswith("Let me look at your quest log.")
    assert "messages" not in view, "the provider's history never goes to the window"
    assert view["usage"]["requests"] == 6

    listed = app.sessions.list()
    assert [s["id"] for s in listed] == [original] and listed[0]["questions"] == 2 and not listed[0]["forked"]
    assert len(app.sessions.list("WAYPOINT")) == 1, "search covers what was said, in any case"
    assert app.sessions.list("dragons") == []

    # fork after the first question: a copy of that much, and the original is untouched
    first_end = app.session["turns"][0]["end"]
    forked = app.fork(0)
    assert forked["id"] != original and forked["forked"] and len(forked["turns"]) == 1
    assert forked["title"].endswith("(fork)") and forked["usage"]["requests"] == 0
    assert len(app.chat.messages) == first_end, "the fork carries the history up to that turn only"
    ask(app, "what about the next quest?")
    assert len(app.view()["turns"]) == 2 and len(app.sessions.load(original)["turns"]) == 2
    assert len(app.sessions.list()) == 2 and app.sessions.list()[0]["id"] == forked["id"], "newest first"
    assert app.fork(5) == {"error": "There is nothing to fork yet."}
    assert app.fork(True) == {"error": "There is nothing to fork yet."}

    # a different run of the program finds them and can continue one
    later = App(wow, fake=True)
    later.sessions = Sessions(tmp / "sessions")
    reopened = later.open_session(original)
    assert len(reopened["turns"]) == 2 and len(later.chat.messages) == later.session["turns"][1]["end"]
    ask(later, "one more")
    assert len(later.sessions.load(original)["turns"]) == 3
    assert "error" in later.open_session("../../etc/passwd") and "error" in later.open_session(None)
    assert later.sessions.load("20261110-000000-zzzzzz") is None

    # a fresh conversation with nothing in it leaves no file behind
    later.new_chat()
    later.sessions.save(later.session)
    assert len(list((tmp / "sessions").glob("*.json"))) == 2

    server, url = serve(later)
    base, token = url.split("/?t=")

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Wisp-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    try:
        assert call("/api/session")["turns"] == []
        assert len(call("/api/sessions")["sessions"]) == 2 and len(call("/api/sessions?q=one%20more")["sessions"]) == 1
        assert len(call("/api/sessions/peek", {"id": original})["turns"]) == 3
        assert call("/api/session")["turns"] == [], "peeking does not switch"
        assert "error" in call("/api/sessions/peek", {"id": "nope"})
        assert len(call("/api/sessions/open", {"id": original})["turns"]) == 3
        assert len(call("/api/fork", {"turn": 1})["turns"]) == 2
        assert len(call("/api/fork", {})["turns"]) == 2, "no turn given: fork all of it"
    finally:
        server.shutdown()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, tempfile.TemporaryDirectory() as c:
        wow = make_wow_dir(Path(a))
        test_snapshot_and_tools(wow)
        test_question_to_waypoint(wow)
        test_inbox_is_strict()
        test_without_questie(Path(b))
        test_bad_files(Path(c))
        test_server(wow)
        test_settings_api(wow)
        with tempfile.TemporaryDirectory() as e:
            test_sessions(wow, Path(e))
    with tempfile.TemporaryDirectory() as d:
        test_this_pc(Path(d))
    print("mvp: OK (%s)" % next(l for l in stub("snapshot") if l.startswith("LUA ")))
