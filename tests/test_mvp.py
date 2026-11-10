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
from wisp.market import Market  # noqa: E402
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
    (saved / "Wisp.lua").write_text(f'\nWispData = {{\n["recipes"] = {{\n}},\n}}\nWispSnapshot = "{snapshot}"\n', encoding="utf-8")
    return tmp


def test_snapshot_and_tools(wow: Path):
    game = Game.load(wow)
    assert game.problem is None, game.problem
    snap = game.snapshot
    assert snap["character"]["class"] == "MAGE" and snap["character"]["level"] == 12
    assert snap["character"]["gold"] == 15 and snap["character"]["x"] == 42.1
    assert snap["addons"] == {"questie": True, "tomtom": True, "dungeonjournal": True, "auctionator": True, "gathermate": True}
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


def test_dungeons(wow: Path, tmp: Path):
    """DungeonJournal's data, as this character sees it: an Alliance mage, level 12."""
    game = Game.load(wow)
    listed = run_tool(game, "dungeons", {})["dungeons"]
    assert [d["name"] for d in listed] == ["Hall of Thanes", "The Deadmines"]
    assert listed[0]["quests"] == 1, "the other faction's quest and the hidden step are left out"
    assert listed[1] == {"name": "The Deadmines", "level": "17-26", "location": "Moonbrook, Westfall", "quests": 4,
                         "done": 1, "in_log": 1, "available_now": 0, "bosses": 2}

    mines = run_tool(game, "dungeon", {"name": "deadmines"})["dungeon"]
    assert mines["entrance"] == {"ref": "d2.entrance", "zone": "Westfall", "x": 42.5, "y": 71.7, "at": "The Deadmines Entrance"}
    assert mines["entrance_hint"] == "Dungeon entrance in Moonbrook"
    by_name = {q["name"]: q for q in mines["quests"]}
    bandanas = by_name["Red Silk Bandanas"]
    assert bandanas["status"] == "locked: an earlier quest is not done"
    assert [(n["id"], n["done"]) for n in bandanas["needs_first"]] == [(65, True), (132, True), (155, False)]
    assert bandanas["next_step"]["id"] == 155, "the next step is the first one not done, not the start of the chain"
    assert bandanas["next_step"]["pickup"] == "Defias Traitor, Sentinel Hill"
    assert bandanas["next_step"]["where"] == {"ref": "dq155.start", "zone": "Westfall", "x": 55.7, "y": 47.5, "at": "Defias Traitor"}
    assert bandanas["pickup_where"]["ref"] == "dq214.start" and bandanas["rewards"] == "7s"
    assert by_name["Collecting Memories"]["status"] == "needs level 14" and by_name["Collecting Memories"]["class_only"] == "Warrior"
    assert by_name["Collecting Memories"]["pickup_where"]["zone"] == "Stormwind City"
    assert by_name["The Jasperlode Mine"]["status"] == "in your quest log" and by_name["Already Done"]["status"] == "done"
    assert mines["bosses"][0] == {"name": "Rhahk'Zor", "rare": False, "loot": [
        {"item": "Rockslicer", "slot": "Two-Hand, Axe", "quality": "rare"},
        {"item": "Rhahk'Zor's Hammer", "slot": "Two-Hand, Mace", "quality": "rare"}]}
    assert mines["bosses"][1]["rare"] is True
    thanes = run_tool(game, "dungeon", {"name": "Hall of Thanes"})["dungeon"]
    assert thanes["bosses"][0]["loot"] == [{"item": "Catacomb Cloak", "slot": "Back", "quality": "rare"}], "a note entry is not loot"
    assert thanes["quests"][0]["status"] == "available" and thanes["quests"][0]["pickup_where"] is None

    assert "No dungeon is called that" in run_tool(game, "dungeon", {"name": "Karazhan"})["error"]
    assert run_tool(game, "dungeon", {"name": "es"})["error"] == "More than one dungeon matches. Ask for one of: Hall of Thanes, The Deadmines"
    assert "error" in run_tool(game, "dungeon", {"name": ""}) and "error" in run_tool(game, "dungeon", {})

    # a waypoint to the entrance and to the next quest step, pasted into the game
    result = run_tool(game, "waypoint", {"refs": ["d2.entrance", "dq155.start"]})
    assert result["shown"] == 2
    lines = stub("paste", game.cards[-1]["wisp"])
    assert "WAY tomtom 1436 0.425 0.717 The Deadmines entrance" in lines, lines
    assert "WAY tomtom 1436 0.557 0.475 The Defias Brotherhood (quest start)" in lines, lines

    # without the DungeonJournal addon the tools say so, and the rest still works
    plain = Game.load(make_wow_dir(tmp, flags="nojournal"))
    assert plain.snapshot["addons"]["dungeonjournal"] is False and len(plain.quests) == 2
    assert "DungeonJournal addon was not found" in run_tool(plain, "dungeons", {})["error"]
    assert "DungeonJournal addon was not found" in run_tool(plain, "dungeon", {"name": "x"})["error"]


def test_history(wow: Path, tmp: Path):
    """Every snapshot is kept per character, and what changed between two is worked out in code."""
    import copy
    from wisp.history import History, character_key, diff, recap

    base = Game.load(wow).snapshot
    assert base["who"] == {"name": "Тестер", "realm": "Test Realm"}
    assert base["professions"] == [{"name": "Tailoring", "skill": 150, "max": 225}, {"name": "Fishing", "skill": 31, "max": 75}]
    assert base["character"]["money"] == 153422 and base["character"]["xp"] == 4200
    told = json.dumps(run_tool(Game(base), "character", {}), ensure_ascii=False)
    assert "Тестер" not in told and "Test Realm" not in told, "who the character is stays out of what the model is given"
    assert "Tailoring" in told

    store = History(tmp / "snapshots")
    assert store.ingest(base) is True and store.ingest(base) is False
    folder = tmp / "snapshots" / "Test Realm-Тестер"
    filed = list(folder.glob("*.json"))
    assert len(filed) == 1 and filed[0].stem == str(base["takenAt"])
    assert "dungeons" not in json.loads(filed[0].read_text(encoding="utf-8")), "reference data is not kept per snapshot"
    assert store.earlier(base) is None, "nothing before the first one"

    same = copy.deepcopy(base)
    same["takenAt"] += 600
    assert store.ingest(same) is False, "logging in and straight out changes nothing and is not filed twice"

    later = copy.deepcopy(base)
    later["takenAt"] += 7200
    later["character"].update(level=14, money=153422 + 32050, zone="Westfall")
    later["completed"] = base["completed"] + [33, 9001]
    later["quests"] = [q for q in base["quests"] if q["id"] != 33] + [{"id": 500, "title": "A New Errand"}]
    later["professions"][0]["skill"] = 185
    later["professions"].append({"name": "Cooking", "skill": 1, "max": 75})
    assert store.ingest(later) is True and store.count(later) == 2
    assert store.earlier(later)["takenAt"] == base["takenAt"]
    assert store.earlier(later, at_least_seconds=86400) is None

    changes = diff(base, later)
    assert changes["hours_between"] == 2.0 and changes["level"] == {"from": 12, "to": 14}
    assert changes["quests_finished"] == 2 and changes["quests_finished_names"] == ["Wolves Across the Border"], \
        "the count is exact; a quest that was never in the log has no name"
    assert changes["quests_picked_up"] == ["A New Errand"] and changes["quests_abandoned"] == []
    assert changes["money_copper"]["change"] == 32050 and changes["zone"] == {"from": "Elwynn Forest", "to": "Westfall"}
    assert changes["professions"] == [{"name": "Tailoring", "from": 150, "to": 185}, {"name": "Cooking", "learned": True, "to": 1}]
    assert recap(changes) == ["Level 12 → 14", "2 quests finished: Wolves Across the Border and 1 more", "Picked up: A New Errand",
                              "Tailoring 150 → 185", "Cooking: learned", "Money +3g 20s", "Now in Westfall"]
    assert recap(diff(base, same)) == []

    other = copy.deepcopy(later)
    other["who"] = {"name": "Alt/One", "realm": "Test Realm"}
    assert character_key(other) == "Test Realm-Alt_One" and store.ingest(other) is True
    assert store.earlier(other) is None, "each character has its own history"

    game = Game(later)
    assert "no earlier snapshot" in run_tool(game, "changes", {})["error"], "without a history there is nothing to compare"
    game.history = store
    assert run_tool(game, "changes", {})["changes"]["level"] == {"from": 12, "to": 14}
    assert "from that long ago" in run_tool(game, "changes", {"days": 3})["error"]
    assert "error" in run_tool(game, "changes", {"days": -1}) and "error" in run_tool(game, "changes", {"days": True})

    # the app files the snapshot it sees and offers the recap, with no model involved
    app = App(wow, fake=True)
    app.history = History(tmp / "app")
    two_days_ago = copy.deepcopy(base)
    two_days_ago["takenAt"] -= 2 * 86400
    two_days_ago["character"]["level"] = 10
    app.history.ingest(two_days_ago)
    state = app.state()
    assert state["snapshots"] == 2 and state["recap"] == {"since": "2 days", "lines": ["Level 10 → 12"]}
    events = list(app.ask("what changed?"))
    assert events[-1]["type"] == "done" and app.history.count(base) == 2, "asking again does not file it again"
    assert App(wow, fake=True).state()["recap"] is None, "a first run has nothing to compare with"


def test_crafting(wow: Path, tmp: Path):
    """Recipes read from the profession window, sums done in code, a list for Auctionator."""
    game = Game.load(wow)
    assert game.snapshot["addons"]["auctionator"] is True
    assert any("read 4 Tailoring recipes" in l for l in stub("snapshot")), "said once, and headers are not recipes"
    book = run_tool(game, "recipes", {})["professions"][0]
    assert (book["profession"], book["skill"], book["max_skill"], book["recipes_known_to_wisp"]) == ("Tailoring", 150, 225, 4)
    hood, bolt = book["recipes"]
    assert [r["name"] for r in book["recipes"]] == ["Azure Silk Hood", "Bolt of Silk Cloth"], "what still gives skill, orange first"
    assert (hood["colour"], hood["turns_grey_at"], hood["makes"]) == ("orange", 185, "1 × Azure Silk Hood")
    assert hood["materials"] == [
        {"item": "Bolt of Silk Cloth", "per_craft": 2, "in_bags": 0, "price_each": "21s"},
        {"item": "Blue Dye", "per_craft": 2, "in_bags": 0, "price_each": None},
        {"item": "Fine Thread", "per_craft": 1, "in_bags": 3, "price_each": None}], "an optional finishing reagent is not a material"
    assert hood["materials_cost_per_craft"] is None and hood["no_auction_price_for"] == ["Blue Dye", "Fine Thread"]
    assert bolt["materials_cost_per_craft"] == "18s" and bolt["can_make_from_bags"] == 8, "32 cloth in two stacks, 4 a bolt"
    everything = run_tool(game, "recipes", {"profession": "tailoring", "show": "all"})["professions"][0]["recipes"]
    assert [(r["name"], r["colour"]) for r in everything][2:] == [("Not Learned Yet", None), ("Small Silk Pack", "grey")]
    assert everything[2]["learned"] is False and everything[3]["materials"][1]["item"] == "item 4234", "a name the game had not loaded"
    assert "no recipes read for Alchemy" in run_tool(game, "recipes", {"profession": "Alchemy"})["error"]

    crafts = [{"recipe_id": 3839, "times": 20}, {"recipe_id": 8760, "times": 10}]
    plan = run_tool(game, "craft_plan", {"crafts": crafts})
    assert plan["steps"] == [{"recipe": "Bolt of Silk Cloth", "times": 20, "colour": "yellow", "makes": "20 × Bolt of Silk Cloth"},
                             {"recipe": "Azure Silk Hood", "times": 10, "colour": "orange", "makes": "10 × Azure Silk Hood"}]
    assert plan["materials"] == [
        {"item": "Silk Cloth", "needed": 80, "made_in_this_plan": 0, "from_bags": 32, "to_buy": 48, "price_each": "4s 50c", "cost": "2g 16s"},
        {"item": "Bolt of Silk Cloth", "needed": 20, "made_in_this_plan": 20, "from_bags": 0, "to_buy": 0},
        {"item": "Blue Dye", "needed": 20, "made_in_this_plan": 0, "from_bags": 0, "to_buy": 20},
        {"item": "Fine Thread", "needed": 10, "made_in_this_plan": 0, "from_bags": 3, "to_buy": 7}], plan["materials"]
    assert plan["to_buy_cost"].startswith("2g 16s plus") and plan["no_auction_price_for"] == ["Blue Dye", "Fine Thread"]
    assert "seen today" in plan["prices"] and "_buy" not in plan and "_card" not in plan
    late = run_tool(game, "craft_plan", {"crafts": [{"recipe_id": 3839, "times": 25}, {"recipe_id": 3813, "times": 1}]})
    assert "at most the first 20 crafts" in late["steps"][0]["warning"] and "grey" in late["steps"][1]["warning"]
    for bad in ([], [{"recipe_id": 1, "times": 1}], [{"recipe_id": 3839, "times": 0}], [{"recipe_id": 3839, "times": 1.5}], "x"):
        assert "error" in run_tool(game, "craft_plan", {"crafts": bad}), bad

    made = run_tool(game, "shopping_list", {"name": "Tailoring 150-170", "crafts": crafts})
    assert made["list"] == ["48 × Silk Cloth", "20 × Blue Dye", "7 × Fine Thread"] and "warning" not in made
    card = game.cards[-1]
    assert card["lines"] == [] and "Auctionator" in card["hint"]
    # the plan itself is laid out on the card, not left to the model's prose
    laid = card["plan"]
    assert (laid["title"], laid["sub"]) == ("Tailoring 150-170", "Tailoring, skill 150")
    assert laid["steps"] == [
        {"text": "20 × Bolt of Silk Cloth", "colour": "yellow", "each": "4 Silk Cloth", "warning": ""},
        {"text": "10 × Azure Silk Hood", "colour": "orange", "each": "2 Bolt of Silk Cloth, 2 Blue Dye, 1 Fine Thread", "warning": ""}]
    assert laid["materials"] == [
        {"item": "Silk Cloth", "need": 80, "bags": 32, "buy": 48, "cost": "2g 16s", "unpriced": False},
        {"item": "Blue Dye", "need": 20, "bags": 0, "buy": 20, "cost": "", "unpriced": True},
        {"item": "Fine Thread", "need": 10, "bags": 3, "buy": 7, "cost": "", "unpriced": True}], "bolts the plan makes for itself are a step, not a material"
    assert laid["total"] == "To buy: about 2g 16s · 2 without an auction price" and "Only orange" in laid["notes"][0]
    assert "Do not repeat" in made["note"]
    pasted = stub("paste", card["wisp"])
    assert 'LIST Tailoring 150-170 = "Silk Cloth";48 + "Blue Dye";20 + "Fine Thread";7' in pasted, pasted
    assert any("shopping list \"Tailoring 150-170\" with 3 items" in l for l in pasted)

    named = run_tool(game, "shopping_list", {"name": "x", "items": [{"name": "silk cloth", "quantity": 5}, {"name": "Mystery; Dust|", "quantity": 2}]})
    assert named["list"] == ["5 × Silk Cloth", "2 × Mystery Dust"] and named["names_wisp_could_not_check"] == ["Mystery Dust"]
    assert "plan" not in game.cards[-1] and game.cards[-1]["lines"] == [
        {"addon": "Auctionator", "text": "Shopping list: x", "detail": "2 items · about 22s 50c · 1 without a price"},
        {"text": "5 × Silk Cloth", "detail": "4s 50c each"}, {"text": "2 × Mystery Dust", "detail": ""}]
    assert "error" in run_tool(game, "shopping_list", {"name": "x"})
    assert "error" in run_tool(game, "shopping_list", {"name": "x", "crafts": crafts, "items": [{"name": "a", "quantity": 1}]})
    assert "error" in run_tool(game, "shopping_list", {"name": "x", "items": [{"name": "a", "quantity": True}]})
    covered = run_tool(game, "shopping_list", {"name": "x", "crafts": [{"recipe_id": 3839, "times": 8}]})
    assert covered["list"] == [] and "no Copy button" in covered["note"]
    assert "wisp" not in game.cards[-1] and game.cards[-1]["lines"] == [] and game.cards[-1]["plan"]["total"] == "Nothing to buy"

    # prices of anything Auctionator has scanned, not only what this character carries or crafts
    assert hood["result_listed_for"] == "90s" and "profit_per_craft" not in hood, "no profit while a material has no price"
    assert (bolt["result_listed_for"], bolt["profit_per_craft"]) == ("21s", "3s"), "a 21s bolt from 18s of cloth"
    asked = run_tool(game, "auction_prices", {"items": ["linen cloth", "silk", "Old Thing", "nothing like this"]})
    linen, silk, old, none = asked["prices"]
    assert linen["found"] == [{"item": "Linen Cloth", "lowest_price": "1s 20c", "seen": "2 days ago"}]
    assert [f["item"] for f in silk["found"]] == ["Silk Cloth", "Azure Silk Hood", "Bolt of Silk Cloth"], "closest names, shortest first"
    assert silk["found"][0]["in_bags"] == 32 and silk["found"][0]["bags_worth"] == "1g 44s"
    assert old["found"][0]["seen"] == "over three weeks ago" and none == {"asked": "nothing like this", "found": [], "note": "no price under that name"}
    assert asked["items_with_a_price"] == 8 and "error" in run_tool(game, "auction_prices", {"items": []})
    assert not Game.load(wow).crafting.find("Bolt of Linen Cloth"), "an item the game had not loaded has no name yet"
    learned = Game.load(make_wow_dir(tmp / "c", flags="visitauction"))
    assert run_tool(learned, "auction_prices", {"items": ["Bolt of Linen Cloth"]})["prices"][0]["found"][0]["lowest_price"] == "3s", \
        "its name is learned after a visit to the auction house, and kept"

    # prices over time: every scan day Auctionator hands over is kept in one SQLite file
    market = Market(tmp / "market.sqlite")
    assert "no price history yet" in run_tool(game, "price_history", {"item": "Silk Cloth"})["error"]
    assert market.ingest(game.snapshot) == 9 and market.ingest(game.snapshot) == 0, "the same logout is filed once"
    game.market = market
    assert market.size(game.snapshot)["items"] == 7 and market.size(game.snapshot)["days_covered"] == 6
    silk = run_tool(game, "price_history", {"item": "silk cloth"})
    assert [(p["low"], p.get("high"), p["listed"]) for p in silk["series"]] == [("3s 80c", "4s", 200), ("4s 30c", None, 80), ("4s 50c", "4s 70c", 120)]
    assert (silk["now"], silk["usual_price"], silk["now_against_usual"], silk["usually_listed"], silk["in_bags"]) == ("4s 50c", "4s 30c", "+5%", 120, 32)
    assert "next_week_estimate" not in silk, "three scan days are not enough to estimate from"
    assert run_tool(game, "price_history", {"item": "silk"})["several_items_match"] == ["Silk Cloth", "Azure Silk Hood", "Bolt of Silk Cloth"]
    assert "error" in run_tool(game, "price_history", {"item": "no such thing"})
    priced = run_tool(game, "auction_prices", {"items": ["Silk Cloth"]})["prices"][0]["found"][0]
    assert (priced["usual_price_30_days"], priced["days_with_a_scan"]) == ("4s 30c", 3)
    def handed(flags):
        lines = stub("snapshot", flags=flags)
        snap = json.loads(base64.b64decode(next(l for l in lines if l.startswith("SNAPSHOT ")).split()[1]))
        return [d["day"] - snap["market"]["today"] for d in snap["market"]["days"]], next(l for l in lines if l.startswith("SENT "))
    assert handed("") == ([-5, -3, -2, -1, 0], "SENT Test Realm-Alliance 5"), "the first time: everything Auctionator holds"
    assert handed("handedover")[0] == [-5, -3, -2, -1, 0], "then the days since, two for overlap, and older days not yet handed over"
    assert handed("handedback") == ([-5, -2, -1, 0], "SENT Test Realm-Alliance 5"), "what was handed over is not sent again"
    def days_of(flags):
        line = next(l for l in stub("snapshot", flags=flags) if l.startswith("DAYS "))
        return {int(d.split(":")[0]): int(d.split(":")[1]) for d in line.split()[1:]}
    first = days_of("bigmarket")
    assert sorted(first) == list(range(-13, 1)) and 56000 <= sum(first.values()) <= 60000, "a big market: 60000 rows at most, newest first"
    after = days_of("bigmarket,handedback")   # days -4 to 0 were handed over at the last logout
    assert sorted(after) == list(range(-15, -4)) + [-2, -1, 0] and sum(after.values()) <= 60000, \
        "the next logout: the new days, then on into the backlog from where the last one stopped"

    # a month of scans: a usual level, a direction, a weekday pattern, an estimate, and the market ranked
    def month(today):
        days = []
        for d in range(today - 29, today + 1):
            steady = 700 if d == today else 1000 + (40 if d % 2 else -40)     # cheap today against a level of 1000
            rising = 2000 + (d - today + 29) * 50                             # up 50 copper a day
            weekly = 500 if d % 7 == 3 else 900                               # dips once a week
            days.append({"day": d, "rows": [1, steady, steady, 50, 2, rising, rising + 100, 5, 3, weekly, weekly, 300, 4, 20, 20, 9000]})
        return {"takenAt": today * 86400 + 3600, "who": {"realm": "R"}, "character": {"faction": "Horde"},
                "prices": [[1, 700, 0, "Steady Ore"], [2, 3450, 0, "Rising Gem"], [3, 900, 0, "Weekly Herb"], [4, 20, 0, "Junk"]],
                "market": {"day0": 0, "today": today, "days": days}}
    far = Game(month(20000))
    far.market = market
    assert market.ingest(far.snapshot) == 120
    ore = run_tool(far, "price_history", {"item": "Steady Ore"})
    assert (ore["days_with_a_scan"], ore["now"], ore["usual_price"], ore["now_against_usual"]) == (30, "7s", "10s", "-30%")
    assert ore["next_week_estimate"]["confidence"] == "fair" and len(ore["series"]) == 30 and ore["kept_by_wisp"]["rows"] == 120
    gem = run_tool(far, "price_history", {"item": "Rising Gem", "days": 60})
    assert gem["direction"] == "rising about 13% a week", gem["direction"]
    assert gem["next_week_estimate"]["around"] == "34s 75c", "the recent level carried half a week along its direction"
    herb = run_tool(far, "price_history", {"item": "Weekly Herb"})
    assert herb["cheapest_weekday"].endswith("(5s)") and herb["dearest_weekday"].endswith("(9s)")
    cheap = run_tool(far, "market_scan", {"find": "below_usual"})
    assert [(i["item"], i["now_against_usual"]) for i in cheap["items"]] == [("Steady Ore", "-30%")], cheap["items"]
    assert [i["item"] for i in run_tool(far, "market_scan", {"find": "rising"})["items"]] == ["Rising Gem"]
    assert [i["item"] for i in run_tool(far, "market_scan", {"find": "swings"})["items"]][0] == "Rising Gem"
    busy = run_tool(far, "market_scan", {"find": "most_listed", "limit": 2})["items"]
    assert [i["item"] for i in busy] == ["Weekly Herb", "Steady Ore"], "junk worth under a silver is never listed"
    assert run_tool(far, "market_scan", {"find": "most_listed", "name_contains": "gem"})["items"][0]["item"] == "Rising Gem"
    assert run_tool(far, "market_scan", {"find": "most_listed", "among": "my_bags"})["items"] == []
    # anything else, asked in SQL: read-only, this market only, stopped if it runs long
    asked = run_tool(far, "market_query", {"sql": "SELECT name, coins(low) AS now, listed FROM p WHERE days_ago = 0 AND low >= 100 ORDER BY low DESC"})
    assert asked["columns"] == ["name", "now", "listed"] and asked["rows"] == [["Rising Gem", "34s 50c", 5], ["Weekly Herb", "9s", 300], ["Steady Ore", "7s", 50]]
    assert run_tool(far, "market_query", {"sql": "SELECT count(*), min(date), max(days_ago) FROM p"})["rows"] == [[120, "2024-09-05", 29]]
    assert run_tool(far, "market_query", {"sql": "SELECT count(*) FROM price"})["rows"] == [[129]], "the raw table can be read too"
    for refused in ("DELETE FROM price", "UPDATE item SET name = 'x'", "DROP TABLE price", "ATTACH DATABASE 'x.db' AS x",
                    "PRAGMA journal_mode = DELETE", "INSERT INTO item VALUES (9, 'x')", "SELECT 1; DELETE FROM price", "not sql", ""):
        assert "error" in run_tool(far, "market_query", {"sql": refused}), refused
    slow = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n"
    assert "failed" in run_tool(far, "market_query", {"sql": slow})["error"], "a query that never ends is stopped"
    many = run_tool(far, "market_query", {"sql": "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 500) SELECT i FROM n"})
    assert len(many["rows"]) == 200 and many["cut_off_at"] == 200
    assert market.size(far.snapshot)["rows"] == 120, "nothing was changed"

    # shown to the player as cards on request: a chart for one item, a table for a ranking
    before = len(far.cards)
    assert "shown" in run_tool(far, "price_history", {"item": "Steady Ore", "show": True})
    chart = far.cards[-1]["chart"]
    assert (chart["title"], chart["sub"], chart["usual"], len(chart["points"])) == ("Steady Ore", "last 30 days · 30 scan days", 1000, 30)
    assert chart["points"][-1] == [20000, 700, 700, 50] and chart["estimate"]["low"] <= chart["estimate"]["expected"] <= chart["estimate"]["high"]
    assert [s[0] for s in chart["stats"]] == ["Now", "Usual", "Against usual", "Next week"] and "confidence fair" in chart["notes"][0]
    assert "shown" in run_tool(far, "market_scan", {"find": "below_usual", "show": True})
    assert far.cards[-1]["table"]["rows"] == [["Steady Ore", "7s", "10s", "-30%", 50]] and far.cards[-1]["table"]["title"] == "Cheaper than usual"
    assert len(far.cards) == before + 2 and "wisp" not in far.cards[-1], "nothing to paste: these cards only show"
    for bad in ({"find": "x"}, {"find": "rising", "days": 1}, {"find": "rising", "limit": 0}, {"find": "rising", "among": "x"}):
        assert "error" in run_tool(far, "market_scan", bad), bad
    assert market.size(game.snapshot)["rows"] == 9, "each realm and faction is its own market"

    # the addon checks a pasted list as strictly as a waypoint
    def paste(cmd, flags=""):
        return stub("paste", encode_wisp1([cmd]), flags=flags)
    for bad in ({"items": []}, {"items": [{"name": "a", "quantity": 0}]}, {"items": [{"quantity": 3}]}, {"items": "x"}):
        assert any("failed bad shopping list" in l for l in paste({"do": "auctionator.shopping_list", "name": "n", **bad})), bad
    odd = paste({"do": "auctionator.shopping_list", "name": "a|b;c", "items": [{"id": 4306, "name": "ignored", "quantity": 2.9},
                                                                              {"id": 424242, "name": "By; name|", "quantity": 1}]})
    assert 'LIST abc = "Silk Cloth";2 + "By name";1' in odd, "the game's own name where it knows the item; ; and | never pass"
    gone = paste({"do": "auctionator.shopping_list", "name": "n", "items": [{"name": "a", "quantity": 1}]}, flags="noauctionator")
    assert any("failed Auctionator is not installed" in l for l in gone)

    without = Game.load(make_wow_dir(tmp / "a", flags="noauctionator"))
    assert without.snapshot["addons"]["auctionator"] is False and not without.snapshot.get("prices")
    assert "not installed" in run_tool(without, "auction_prices", {"items": ["Silk Cloth"]})["error"]
    assert "not installed" in run_tool(without, "shopping_list", {"name": "x", "crafts": crafts})["warning"]
    assert "not installed" in run_tool(without, "craft_plan", {"crafts": crafts})["prices"]
    closed = Game.load(make_wow_dir(tmp / "b", flags="noprofession"))
    assert "window" in run_tool(closed, "recipes", {})["error"] and closed.snapshot["bags"], "bags are read without the window"


def test_item_graph(wow: Path, tmp: Path):
    """One item, and everything a step away: recipes, quests, bosses, sellers, drops, price."""
    game = Game.load(wow)

    def links(name):
        out = run_tool(game, "item_links", {"item": name})
        out.pop("note", None)
        return out
    silk = links("silk cloth")
    assert silk["auction_price"] == {"lowest": "4s 50c", "seen": "today"} and silk["in_bags"] == 32
    assert silk["used_in_your_recipes"] == [{"recipe_id": 3839, "recipe": "Bolt of Silk Cloth", "profession": "Tailoring",
                                             "needs_each": 4, "colour": "yellow", "makes": "Bolt of Silk Cloth"}]
    assert len(silk["dropped_by"]) == 5 and silk["dropped_by_in_all"] == 7, "the first few sources, and how many there are"
    assert silk["found_in"][0]["name"] == "Silk Crate" and "found_in_in_all" not in silk
    bolt = links("Bolt of Silk Cloth")   # a step on: what the bolt is made from and what it goes into
    assert bolt["made_by_your_recipes"][0]["from"] == "4 Silk Cloth"
    assert [r["recipe"] for r in bolt["used_in_your_recipes"]] == ["Small Silk Pack", "Azure Silk Hood", "Not Learned Yet"]
    assert bolt["used_in_your_recipes"][2]["learned"] is False
    thread = links("Fine Thread")   # no auction price, but a vendor: the gap in a crafting plan
    seller = thread["sold_by"][0]
    assert "auction_price" not in thread and seller["name"] == "Alexandra Bolero" and seller["where"][0]["zone"] == "Stormwind City"
    assert run_tool(game, "waypoint", {"refs": [seller["where"][0]["ref"]]})["shown"] == 1
    assert game.cards[-1]["lines"][0]["text"] == "Waypoint: Alexandra Bolero (sells Fine Thread)"
    meat = links("Tough Wolf Meat")
    assert meat["asked_for_by_quests"] == [{"quest": "Wolves Across the Border", "where": "in your quest log", "status": "ready to hand in"}]
    assert meat["dropped_by"][0]["name"] == "Prowler"
    assert links("Rockslicer") == {"item": "Rockslicer", "dropped_by_bosses": [{"boss": "Rhahk'Zor", "dungeon": "The Deadmines"}]}
    assert links("Red Silk Bandana")["asked_for_by_quests"][0] == {
        "quest": "Red Silk Bandanas", "where": "dungeon quest: The Deadmines", "status": "locked: an earlier quest is not done"}
    assert links("Linen Cloth") == {"item": "Linen Cloth", "auction_price": {"lowest": "1s 20c", "seen": "2 days ago"}}, \
        "priced but nothing to do with this character: no sources were asked of Questie"
    assert links("silk")["several_items_match"][0] == "Silk Cloth" and "error" in links("no such item") and "error" in links("")
    bare = Game.load(make_wow_dir(tmp, flags="noquestie"))
    out = run_tool(bare, "item_links", {"item": "Fine Thread"})
    assert "sold_by" not in out and "Questie was not available" in out["note"] and out["used_in_your_recipes"]


def test_gathering(wow: Path, tmp: Path):
    """GatherMate2's nodes: which zones have the most, a route through one, and the way in from an item."""
    game = Game.load(wow)
    assert game.snapshot["gather"]["total"] == 16

    def gather(**args):
        out = run_tool(game, "gathering", args)
        out.pop("note", None)
        return out
    known = gather()
    assert known["known_nodes"] == 16 and known["by_profession"]["Herb Gathering"][0] == {"node": "Peacebloom", "known_nodes": 9, "zones": 2}
    assert gather(what="peacebloom")["zones"] == [{"zone": "Elwynn Forest", "known_nodes": 7}, {"zone": "Westfall", "known_nodes": 2}]
    assert gather(zone="elwynn forest")["gatherable"] == [
        {"node": "Peacebloom", "kind": "Herb Gathering", "known_nodes": 7}, {"node": "Copper Vein", "kind": "Mining", "known_nodes": 5},
        {"node": "Silverleaf", "kind": "Herb Gathering", "known_nodes": 2}]

    # a route: three flowers in one patch are one stop; it starts where the character stands (42, 66) and walks on
    walk = gather(what="Peacebloom", zone="Elwynn")
    assert (walk["known_nodes"], walk["route"]["stops"], walk["route"]["starts"]) == (7, 4, "from where the character stands")
    assert walk["route"]["first_stops"] == [{"x": 45.0, "y": 62.0, "nodes": 1}, {"x": 30.5, "y": 60.5, "nodes": 3},
                                            {"x": 60.5, "y": 40.5, "nodes": 2}, {"x": 75.0, "y": 70.0, "nodes": 1}], walk["route"]["first_stops"]
    assert gather(what="Peacebloom", zone="Westfall")["route"]["starts"] == "at the richest spot"
    shown = run_tool(game, "waypoint", {"refs": [walk["route"]["ref"], "q33.end.1.1"]})
    assert shown["shown"] == 2 and game.cards[-1]["lines"][0] == {
        "addon": "TomTom", "text": "Route: Peacebloom, 4 stops", "detail": "Elwynn Forest · 7 known nodes"}
    ways = [l for l in stub("paste", game.cards[-1]["wisp"]) if l.startswith("WAY ")]
    assert len(ways) == 5 and ways[0] == "WAY tomtom 1429 0.45 0.62 Peacebloom 1" and ways[-1].endswith("Eagan Peltskinner (turn in)")

    # from an item: a herb's node has the herb's name, an ore's vein is the object that holds it
    ore = run_tool(game, "item_links", {"item": "Copper Ore"})
    assert ore["gathered_from"] == [{"node": "Copper Vein", "profession": "Mining", "known_nodes": 5, "richest_zones": ["Elwynn Forest (5)"]}]
    assert "gathered_from" not in run_tool(game, "item_links", {"item": "Silk Cloth"})

    assert gather(what="leaf") == {"node": "Silverleaf", "profession": "Herb Gathering", "zones": [{"zone": "Elwynn Forest", "known_nodes": 2}]}
    assert gather(what="e")["several_nodes_match"] == ["Peacebloom", "Silverleaf", "Copper Vein"]
    assert "error" in gather(what="Mithril") and "error" in gather(zone="Nowhere") and "zones_with_it" in gather(what="Silverleaf", zone="Westfall")
    without = Game.load(make_wow_dir(tmp, flags="nogathermate"))
    assert without.snapshot["addons"]["gathermate"] is False and "not installed" in run_tool(without, "gathering", {})["error"]


def test_farming(wow: Path, tmp: Path):
    """What the character really looted, turned into worth per loot, per hour and per gathering node."""
    game = Game.load(wow)
    day = game.snapshot["loot"][0]
    assert (day["zone"], day["secs"], day["copper"]) == ("Elwynn Forest", 200, 155), "a 13-minute break is not looting time"
    by_key = {s["key"]: s for s in day["sources"]}
    assert by_key["n118"]["n"] == 2 and by_key["n118"]["name"] == "Prowler" and by_key["n118"]["items"] == [
        [750, 1, "Tough Wolf Meat"], [2589, 2, "Linen Cloth"]], "a corpse opened twice counts once"
    assert by_key["n100"] == {"key": "n100", "name": "Defias Weaver 100", "n": 2, "items": [[4306, 3, "Silk Cloth"]]}, \
        "three looted at once are told apart; a name not seen while looting comes from Questie"
    assert by_key["o1731"]["name"] == "Copper Vein" and by_key["o1731"]["items"] == [[2770, 5, "Copper Ore"], [2835, 1, "Rough Stone"]]
    assert game.snapshot["vendor"] == [[750, 6], [2835, 2], [4306, 100]]

    def farm(**args):
        out = run_tool(game, "farming", args)
        out.pop("note", None)
        return out
    spot = farm(view="spots")["spots"][0]
    assert (spot["coins"], spot["loot_worth"], spot["total"], spot["looted"]) == ("1s 55c", "19s 98c", "21s 53c", 6)
    assert spot["per_hour"] == "too little time to say" and spot["main_loot"][0] == "3 × Silk Cloth"
    weaver, vein, wolf = farm(view="sources")["sources"]
    assert (weaver["source"], weaver["worth_per_loot"], weaver["drops"]) == (
        "Defias Weaver 100", "6s 75c", [{"item": "Silk Cloth", "per_loot": 1.5, "worth_each": "4s 50c"}]), "the auction price, not the vendor's"
    assert vein["kind"] == "node" and vein["drops"][0] == {"item": "Copper Ore", "per_loot": 2.5, "worth_each": "80c"}
    assert wolf["drops"][1] == {"item": "Tough Wolf Meat", "per_loot": 0.5, "worth_each": "6c"}, "nobody lists it: what a vendor pays"

    worth = farm(view="gathering", show=True)
    assert worth["worth"][0] == {"node": "Copper Vein", "profession": "Mining", "zone": "Elwynn Forest", "known_nodes": 5,
                                 "worth_each": "2s 1c", "worth_all": "10s 5c", "basis": "measured over 2 of your gathers"}
    assert worth["worth"][1]["basis"].startswith("assumed one per node") and worth["nodes_without_a_price"] == ["Silverleaf"]
    assert game.cards[-1]["table"]["rows"][0] == ["Copper Vein", "Elwynn Forest", 5, "2s 1c", "10s 5c"]
    assert [w["zone"] for w in farm(view="gathering", zone="Westfall")["worth"]] == ["Westfall"]

    # with enough time behind it, a per-hour figure; zones ranked by it; old days fall out of the period
    snapshot = dict(game.snapshot, loot=[
        {"day": day["day"], "map": 1429, "zone": "Elwynn Forest", "secs": 3600, "copper": 5000,
         "sources": [{"key": "n100", "name": "Defias Weaver", "n": 100, "items": [[4306, 150, "Silk Cloth"]]}]},
        {"day": day["day"] - 1, "map": 1436, "zone": "Westfall", "secs": 1800, "copper": 0,
         "sources": [{"key": "n7", "name": "Harvest Golem", "n": 40, "items": [[2589, 400, "Linen Cloth"]]}]},
        {"day": day["day"] - 40, "map": 1436, "zone": "Westfall", "secs": 9999, "copper": 999999, "sources": []}])
    long = Game(snapshot)
    spots = run_tool(long, "farming", {"view": "spots", "show": True})["spots"]
    assert [(s["zone"], s["per_hour"], s["time_looting"]) for s in spots] == [("Westfall", "9g 60s", "0h 30m"), ("Elwynn Forest", "7g 25s", "1h 0m")]
    assert long.cards[-1]["table"]["rows"][0] == ["Westfall", "0h 30m", 40, "4g 80s", "9g 60s"]
    assert run_tool(long, "farming", {"view": "spots", "days": 60})["spots"][0]["days_farmed"] == 2
    assert [s["source"] for s in run_tool(long, "farming", {"view": "sources", "zone": "westfall"})["sources"]] == ["Harvest Golem"]
    for bad in ({"view": "x"}, {"view": "spots", "days": 0}, {"view": "spots", "zone": "Nowhere"}):
        assert "error" in run_tool(long, "farming", bad), bad
    quiet = Game.load(make_wow_dir(tmp, flags="noloot"))
    assert "No looting has been measured yet" in run_tool(quiet, "farming", {"view": "spots"})["error"]
    assert run_tool(quiet, "farming", {"view": "gathering"})["worth"][0]["node"] == "Peacebloom", "without loot, a herb is priced by its own name"


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

    # the window helpers do nothing without a window, and the global key can be registered on Windows
    local.set_window_alpha(None, 0.5)
    local.set_window_round(None, True)
    local.round_window_corners(None)
    assert local.window_handle(object()) is None
    pressed = []
    registered = local.start_hotkey(lambda: pressed.append(1))
    assert registered is (os.name == "nt"), f"the show/hide key: registered={registered}"

    app = App(None, fake=True)   # persist is off: real settings and keys are never touched
    assert "error" in app.save_settings({"wow_dir": str(tmp)})
    assert local.addon_state(forever) == "missing"
    saved = app.save_settings({"wow_dir": str(root), "keys": {"anthropic": "sk-ant-would-be-secret"}})
    assert saved["wow_dir"] == str(forever) and app.wow_dir == forever
    assert saved["addon"] == "installed" and "installed its addon" in saved["addon_note"], "one package: no button needed"
    (installed / "Wisp.lua").write_text("-- an old version")
    app.ensure_addon()
    assert local.addon_state(forever) == "installed" and "updated its addon" in app.addon_note
    assert "would-be-secret" not in json.dumps(app.settings()), "a key is never sent back"
    assert [(p["id"], p["label"], p["model"]) for p in app.settings()["providers"]] == [
        ("anthropic", "Claude", "claude-opus-5-5"), ("moonshot", "Kimi", "kimi-k3.1-pro"),
        ("openrouter", "OpenRouter", "anthropic/claude-opus-5.5")]
    assert app.save_settings({"provider": "moonshot", "model": "kimi-k3"})["provider"] == "moonshot"
    assert app.models == {"anthropic": "claude-opus-5-5", "moonshot": "kimi-k3",
                          "openrouter": "anthropic/claude-opus-5.5"}, "a model is remembered per provider"
    assert app.save_settings({"provider": "nope", "model": ""})["provider"] == "moonshot", "nonsense is ignored"
    assert app.model_choices("moonshot")["models"] == ["kimi-k3", "kimi-k3.1-pro"] and "error" in app.model_choices("x")
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
        with tempfile.TemporaryDirectory() as f:
            test_dungeons(wow, Path(f))
        with tempfile.TemporaryDirectory() as g:
            test_history(wow, Path(g))
        test_inbox_is_strict()
        with tempfile.TemporaryDirectory() as k:
            test_farming(wow, Path(k))
        with tempfile.TemporaryDirectory() as j:
            test_gathering(wow, Path(j))
        with tempfile.TemporaryDirectory() as i:
            test_item_graph(wow, Path(i))
        with tempfile.TemporaryDirectory() as h:
            test_crafting(wow, Path(h))
        test_without_questie(Path(b))
        test_bad_files(Path(c))
        test_server(wow)
        test_settings_api(wow)
        with tempfile.TemporaryDirectory() as e:
            test_sessions(wow, Path(e))
    with tempfile.TemporaryDirectory() as d:
        test_this_pc(Path(d))
    print("mvp: OK (%s)" % next(l for l in stub("snapshot") if l.startswith("LUA ")))
