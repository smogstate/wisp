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
import time
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
    out = subprocess.run([*LUA, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "WISP_STUB": flags})
    assert out.returncode == 0, out.stderr + out.stdout
    return out.stdout.splitlines()


def make_wow_dir(tmp: Path, flags="") -> Path:
    """A fake game folder holding what the addon would have saved at logout."""
    lines = stub("snapshot", flags=flags)
    snapshot = next(l for l in lines if l.startswith("SNAPSHOT ")).split(" ", 1)[1]
    assert any("snapshot built: 2 quests" in l or "снимок собран: заданий: 2, Questie найден" in l for l in lines), lines
    saved = tmp / "WTF/Account/TEST/SavedVariables"
    saved.mkdir(parents=True)
    # the game writes saved variables as Lua assignments
    quests = next((l.split(" ") for l in lines if l.startswith("QUESTS ")), None)
    kept = f'WispQuests = "{quests[1]}"\nWispQuestsKey = "{quests[2]}"\n' if quests else ""
    items = next((l.split(" ") for l in lines if l.startswith("ITEMS ")), None)
    kept += f'WispItems = "{items[1]}"\nWispItemsKey = "{items[2]}"\n' if items else ""
    (saved / "Wisp.lua").write_text(f'\nWispData = {{\n["recipes"] = {{\n}},\n}}\nWispSnapshot = "{snapshot}"\n{kept}', encoding="utf-8")
    return tmp


def test_snapshot_and_tools(wow: Path):
    game = Game.load(wow)
    assert game.problem is None, game.problem
    snap = game.snapshot
    assert snap["character"]["class"] == "MAGE" and snap["character"]["level"] == 12
    assert snap["character"]["gold"] == 15 and snap["character"]["x"] == 42.1
    assert snap["addons"] == {"inbox": False, "questie": True, "tomtom": True, "dungeonjournal": True, "auctionator": True, "gathermate": True}
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
        {"item": "Silk Cloth", "need": 80, "bags": 32, "bank": 0, "buy": 48, "cost": "2g 16s", "unpriced": False},
        {"item": "Blue Dye", "need": 20, "bags": 0, "bank": 0, "buy": 20, "cost": "", "unpriced": True},
        {"item": "Fine Thread", "need": 10, "bags": 3, "bank": 0, "buy": 7, "cost": "", "unpriced": True}], "bolts the plan makes for itself are a step, not a material"
    assert "the bank is not known" in laid["notes"][0]
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
    fixture_market, market = market, Market(tmp / "month.sqlite")   # made-up dates of their own: kept apart from the fixture's
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
    assert run_tool(far, "market_query", {"sql": "SELECT count(*) FROM price"})["rows"] == [[120]], "the raw table can be read too"
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
    alt = dict(game.snapshot, who={"name": "Alt", "realm": "Elsewhere"}, character={"faction": "Horde"})
    assert fixture_market.size(game.snapshot)["rows"] == fixture_market.size(alt)["rows"] == 9, "every character shares one market"

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


def test_available_quests(wow: Path, tmp: Path):
    """The quests Questie has down as open to the character: by zone, by kind, nearest first."""
    game = Game.load(wow)
    assert [q["id"] for q in game.snapshot["available"]] == [34, 40, 84, 176, 314, 386], "one Questie has no record of is left out"

    def quests(**args):
        out = run_tool(game, "available_quests", args)
        out.pop("note", None)
        return out
    everywhere = quests()
    assert everywhere["open_quests"] == 6 and everywhere["character_zone"] == "Elwynn Forest"
    assert everywhere["by_zone"] == [
        {"zone": "Elwynn Forest", "open_quests": 5, "worth_doing": 3, "xp_on_offer": 2060, "elite": 1, "dungeon": 1, "quest_levels": "5-25"},
        {"zone": "Dun Morogh", "open_quests": 1, "worth_doing": 1, "xp_on_offer": 800, "elite": 1, "quest_levels": "12"}], \
        "worth doing: not grey (level 5 at 12) and not red (level 25)"

    # the example asked for: which elite quests are open around here
    here = quests(zone="here", kind="elite")
    assert (here["zone"], here["open_quests"], here["ordered"]) == ("Elwynn Forest", 1, "nearest first")
    hogger = here["quests"][0]
    assert (hogger["title"], hogger["level"], hogger["kind"], hogger["needs"]) == ("Wanted: Hogger", 11, "elite", ["Huge Gnoll Claw"])
    assert hogger["starts"][0]["name"] == "Wanted Poster" and hogger["starts"][0]["where"][0]["ref"] == "a176.start.1.1"
    assert run_tool(game, "waypoint", {"refs": ["a176.start.1.1"]})["shown"] == 1
    assert game.cards[-1]["lines"][0]["text"] == "Waypoint: Wanted Poster (quest giver)"
    assert [q["title"] for q in quests(kind="elite")["quests"]] == ["Wanted: Hogger", "Protecting the Herd"], "everywhere: by level"
    near = [q["title"] for q in quests(zone="elwynn")["quests"]]
    assert near == ["A Fishy Peril", "What Comes Around", "An Unwelcome Guest", "Wanted: Hogger", "Back to Billy"], \
        "from where the character stands, at the marshal's feet: his two first, then the poster's"
    colours = {q["title"]: q["colour"] for q in quests(zone="elwynn")["quests"]}
    assert colours == {"A Fishy Peril": "yellow", "What Comes Around": "red", "An Unwelcome Guest": "green",
                       "Wanted: Hogger": "yellow", "Back to Billy": "grey"}, "as the quest log would colour them at level 12"
    assert Game({"v": 1, "character": {"level": 45}}).colour(35) == "green" and Game({"v": 1, "character": {"level": 45}}).colour(34) == "grey", \
        "without the game's own number, its table"
    assert quests(zone="Dun Morogh")["ordered"] == "by level" and quests(kind="raid")["quests"] == []
    assert "zones_with_open_quests" in quests(zone="Nowhere") and "error" in quests(kind="x") and "error" in quests(limit=0)
    claw = run_tool(game, "item_links", {"item": "Huge Gnoll Claw"})["asked_for_by_quests"]
    assert claw == [{"quest": "Wanted: Hogger", "where": "open to you, not picked up", "status": "level 11, elite"}]

    older = Game.load(make_wow_dir(tmp / "a", flags="oldquestie"))
    assert "may keep it elsewhere" in run_tool(older, "available_quests", {})["error"]
    without = Game.load(make_wow_dir(tmp / "b", flags="noquestie"))
    assert "Questie was not running" in run_tool(without, "available_quests", {})["error"]


def test_questing_route(wow: Path):
    """What to do in the zone, in an order to walk it, as one card."""
    game = Game.load(wow)
    out = run_tool(game, "questing_route", {})
    assert out["zone"] == "Elwynn Forest" and out["starts"] == "from where the character stands"
    assert [(s["text"], s["detail"]) for s in out["stops"]] == [
        ("Marshal Dughan", "Pick up: A Fishy Peril (level 10, 620 XP)"),
        ("Wanted Poster", "Pick up: An Unwelcome Guest (level 9, 540 XP) · Pick up: Wanted: Hogger (level 11, elite, 900 XP)"),
        ("Eagan Peltskinner", "Hand in: Wolves Across the Border (450 XP)"),
        ("The Jasperlode Mine", "Go on: Scout through the Jasperlode Mine (700 XP when handed in)")], \
        "quest givers first, the nearest each time, two quests at one giver as one stop; then the quest under way"
    assert out["left_out"] == {"grey": 1, "red": 1}, "a level 5 quest gives nothing at 12, a level 25 one is out of reach"
    card = game.cards[-1]
    assert card["route"]["title"] == "Questing in Elwynn Forest" and card["route"]["sub"] == "level 12 · 1 to hand in, 3 to pick up, 1 to go on with"
    assert card["route"]["notes"][:2] == ["450 XP to hand in (4% of a level)", "2,060 XP in the quests picked up (21% of a level)"]
    assert "Left out: 1 grey and 1 red." in card["route"]["notes"][2] and "TomTom" in card["hint"]
    assert out["experience"] == {"for_handing_in_now": 450, "in_quests_picked_up": 2060, "share_of_a_level_now": "4% of a level"}
    ways = [l for l in stub("paste", card["wisp"]) if l.startswith("WAY ")]
    assert ways == ["WAY tomtom 1429 0.421 0.659 1. Marshal Dughan", "WAY tomtom 1429 0.246 0.747 2. Wanted Poster",
                    "WAY tomtom 1429 0.489 0.402 3. Eagan Peltskinner", "WAY tomtom 1429 0.615 0.538 4. The Jasperlode Mine"], ways
    far = run_tool(game, "questing_route", {"zone": "dun morogh"})
    assert [s["text"] for s in far["stops"]] == ["Rudra Amberstill"] and far["starts"] == "at the first quest giver"
    assert "zones_with_quests" in run_tool(game, "questing_route", {"zone": "Nowhere"})
    assert "nothing_to_do" in run_tool(game, "questing_route", {"zone": "Stormwind"}), "a zone known only for a vendor"


def test_leveling(wow: Path):
    """How fast the character levels, from the gains the addon measured."""
    game = Game.load(wow)
    day = game.snapshot["xp"]["days"][0]
    assert (day["gained"], day["secs"], day["quests"], day["questXp"], day["questMoney"], day["levels"]) == (1200, 200, 1, 450, 300, 0), \
        "two kills, a quest handed in, and a kill after a break that is not counted as time"
    assert game.snapshot["xp"]["rested"] == 2500
    lines = stub("snapshot", flags="levelup")
    up = json.loads(base64.b64decode(next(l for l in lines if l.startswith("SNAPSHOT ")).split()[1]))["xp"]["days"][0]
    assert (up["gained"], up["levels"]) == (7500, 1), "the rest of the old bar and the new one so far"
    short = run_tool(game, "leveling", {})
    assert (short["experience"], short["into_this_level"], short["to_next_level"]) == ("4,200 of 10,000", "42%", 5800)
    assert short["per_hour"] == "too little time measured to say" and "next_level_in" not in short and short["from_quests"] == "38%"
    assert short["rested"].startswith("2,500 XP of rested bonus (25% of a level)")
    assert game.quests[0]["xp"] == 450 and game.available[3]["money"] == "15s", "what Questie reckons a quest gives"

    today = game.snapshot["takenAt"] // 86400
    played = Game(dict(game.snapshot, xp={"rested": 0, "days": [
        {"day": today, "gained": 4000, "secs": 2400, "quests": 3, "questXp": 2000, "questMoney": 0, "levels": 0},
        {"day": today - 1, "gained": 2000, "secs": 1200, "quests": 0, "questXp": 0, "questMoney": 0, "levels": 1},
        {"day": today - 20, "gained": 90000, "secs": 60, "quests": 0, "questXp": 0, "questMoney": 0, "levels": 0}]}))
    out = run_tool(played, "leveling", {"show": True})
    assert (out["per_hour"], out["gained"], out["from_quests"], out["next_level_in"]) == (6000, 6000, "33%", "about 58 minutes of play at this pace")
    assert [(d["gained"], d["per_hour"]) for d in out["days"]] == [(4000, 6000), (2000, 6000)] and out["days"][1]["levels_gained"] == 1
    assert "rested" not in out and len(run_tool(played, "leveling", {"days": 30})["days"]) == 3
    table = played.cards[-1]["table"]
    assert table["title"] == "6,000 XP an hour" and table["sub"] == "level 12 · 42% in · about 58 minutes of play at this pace"
    assert table["rows"][0][1:] == ["4,000", "0h 40m", "6,000", 3]
    quiet = Game.load(make_wow_dir(Path(tempfile.mkdtemp()), flags="noxp"))
    assert "No experience has been measured yet" in run_tool(quiet, "leveling", {})["note"] and "error" in run_tool(quiet, "leveling", {"days": 0})


def test_gold(wow: Path, tmp: Path):
    """What the character's own things would fetch: bags to sell, recipes that turn a profit."""
    game = Game.load(wow)
    bags = run_tool(game, "gold", {"view": "bags", "show": True})
    assert bags["sellable_worth"] == "1g 47s 26c" or bags["sellable_worth"] == "1g 47s"
    assert bags["items"][0] == {"item": "Silk Cloth", "have": 32, "each": "4s 50c", "worth": "1g 44s", "vendor_pays": "32s",
                                "advice": "no price history yet"}
    assert [i["item"] for i in bags["items"]] == ["Silk Cloth", "Copper Ore"], "what can be sold, the most valuable first"
    assert bags["left_out"] == {"bound_to_the_character": 1, "no_price_known": 1}, "the hearthstone; thread nobody lists or buys"
    assert game.cards[-1]["table"]["rows"][0] == ["Silk Cloth", 32, "4s 50c", "1g 44s"]

    # with a price history behind it, each item is set against its usual price
    market = Market(tmp / "market.sqlite")
    today = game.snapshot["market"]["today"]
    past = dict(game.snapshot, takenAt=game.snapshot["takenAt"] - 10, prices=[[4306, 300, 0, "Silk Cloth"]],
                market={"day0": game.snapshot["market"]["day0"], "today": today,
                        "days": [{"day": today - d, "rows": [4306, 300, 300, 50, 2770, 200, 200, 50]} for d in (2, 3, 4, 6, 7)]})
    market.ingest(past)
    market.ingest(game.snapshot)
    game.market = market
    advice = {i["item"]: i["advice"] for i in run_tool(game, "gold", {"view": "bags"})["items"]}
    assert advice == {"Silk Cloth": "+50% against its usual price: a good time to sell",
                      "Copper Ore": "-60% against its usual price: hold if you can"}, advice

    crafts = run_tool(game, "gold", {"view": "crafts", "show": True})
    assert crafts["recipes"] == [{"recipe_id": 3839, "recipe": "Bolt of Silk Cloth", "profession": "Tailoring", "materials": "18s",
                                  "sells_for": "21s", "profit": "3s", "return": "+17%", "can_make_from_bags": 8}]
    assert crafts["recipes_without_full_prices"] == 2 and game.cards[-1]["table"]["title"] == "Crafting for profit"
    assert "error" in run_tool(game, "gold", {"view": "x"})
    without = Game.load(make_wow_dir(tmp / "a", flags="noauctionator"))
    assert "not installed" in run_tool(without, "gold", {"view": "bags"})["error"]


def test_investments(wow: Path, tmp: Path):
    """Things bought to sell later: the trades the player reports, and what they come to."""
    from wisp.assets import Assets, parse_money
    for text, copper in (("3s 50c", 350), ("1g", 10000), ("2 gold 5 silver", 20500), ("1.5g", 15000), ("45c", 45), ("1g and 20s", 12000)):
        assert parse_money(text) == copper, text
    for text in ("350", "3", "about 3s", "1,200g", "", "0c", None, 5):
        assert parse_money(text) is None, f"{text!r}: no units or not sure enough, so not guessed"

    game = Game.load(wow)
    game.market = Market(tmp / "wisp.sqlite")
    game.market.ingest(game.snapshot)
    game.assets = Assets(tmp / "wisp.sqlite")

    def invest(**args):
        out = run_tool(game, "investments", args)
        out.pop("note", None)
        return out
    assert invest()["investments"] == []

    # bought five days ago at 3s; listed at 4s 50c today
    bought = invest(action="buy", item="silk cloth", quantity=200, price_each="3s", days_ago=5)
    assert bought["recorded"] == {"trade": 1, "action": "buy", "item": "Silk Cloth", "quantity": 200, "each": "3s"}
    silk = bought["investments"][0]
    assert (silk["held"], silk["paid_each"], silk["invested"], silk["now_each"], silk["worth_now"]) == (200, "3s", "6g", "4s 50c", "9g")
    assert (silk["unsold_gain"], silk["unsold_gain_percent"], silk["days_held"], silk["bags_hold"]) == ("+3g", "+50%", 5, 32)
    assert silk["since_bought"] == {"lowest": "3s 80c", "highest": "4s 50c", "scan_days": 3}, "how the price has behaved since"
    assert bought["totals"] == {"invested_in_what_is_held": "6g", "worth_now": "9g", "unsold_gain": "+3g", "made_on_sales": "0c", "made_in_all": "+3g"}
    table = game.cards[-1]["table"]
    assert table["rows"] == [["Silk Cloth", 200, "3s", "4s 50c", "+3g"]] and table["sub"] == "paid 6g · worth 9g · made +3g in all"

    # more at a higher price, for the lot: the cost is averaged; then a sale against that average
    invest(action="buy", item="Silk Cloth", quantity=100, total="5g")
    assert invest()["investments"][0]["paid_each"] == "3s 67c", "200 at 3s and 100 at 5s"
    sold = invest(action="sell", item="Silk Cloth", quantity=150, price_each="5s")
    silk = sold["investments"][0]
    assert (silk["held"], silk["invested"], silk["worth_now"], silk["unsold_gain"], silk["made_on_sales"]) == (150, "5g 50s", "6g 75s", "+1g 25s", "+2g")
    assert sold["totals"]["made_in_all"] == "+3g 25s", "what the sale made and what the rest would"

    # what must not slip through
    assert "only 150" in invest(action="sell", item="Silk Cloth", quantity=151, price_each="5s")["error"]
    assert "with its units" in invest(action="buy", item="Silk Cloth", quantity=10, price_each="350")["error"]
    assert "not both" in invest(action="buy", item="Silk Cloth", quantity=10, price_each="3s", total="30s")["error"]
    assert "less than a copper" in invest(action="buy", item="Silk Cloth", quantity=1000, total="5c")["error"]
    assert "error" in invest(action="buy", item="Silk Cloth", quantity=0, price_each="3s") and "error" in invest(action="buy", quantity=5, price_each="3s")
    assert invest(action="buy", item="silk", quantity=5, price_each="3s")["several_items_match"][0] == "Silk Cloth"
    assert "no single item" in invest(action="buy", item="Dragon Scale", quantity=5, price_each="3s")["error"]
    assert len(game.assets.trades("all")) == 3, "none of those was recorded"

    # no price given: today's listing, marked as assumed; an item with no listing needs the player's word
    ore = invest(action="buy", item="Copper Ore", quantity=40)
    assert ore["recorded"]["each"] == "80c" and "price_assumed" in ore["recorded"]
    assert "ask the player" in invest(action="buy", item="Fine Thread", quantity=5)["error"]

    # one investment in depth, with its chart: the price against what was paid
    detail = invest(item="Silk Cloth", show=True)
    assert [(t["action"], t["quantity"], t["each"], t["days_ago"]) for t in detail["trades"]] == [
        ("buy", 200, "3s", 5), ("buy", 100, "5s", 0), ("sell", 150, "5s", 0)]
    chart = game.cards[-1]["chart"]
    assert (chart["title"], chart["line"], chart["usual"], chart["estimate"]) == ("Silk Cloth", "paid", 367, None)
    assert [s[0] for s in chart["stats"]] == ["Bought at", "Now", "Gain", "Unsold gain"] and len(chart["points"]) == 3

    # a mistake undone; a whole record forgotten; and every character shares the book
    assert "remove the sale first" in invest(action="remove", trade=1)["error"], "the 150 sold cannot rest on the 100 left"
    invest(action="remove", trade=2)
    assert invest()["investments"][0]["paid_each"] in ("3s", "–") and "error" in invest(action="remove", trade=999)
    invest(action="remove", item="Copper Ore")
    assert [i["item"] for i in invest()["investments"]] == ["Silk Cloth"]
    elsewhere = Game(dict(game.snapshot, who={"name": "Other", "realm": "Other Realm"}))
    elsewhere.assets = game.assets
    assert [i["item"] for i in run_tool(elsewhere, "investments", {})["investments"]] == ["Silk Cloth"], "an alt on another realm sees it too"
    assert "not available" in run_tool(Game.load(wow), "investments", {})["error"]


def test_auction_mail(wow: Path, tmp: Path):
    """What was really bought and sold, from the auction house's mail, and investments recorded from it."""
    from wisp.assets import Assets
    app = App(wow, fake=True)
    game = app.load_game()   # the app files the addon's notes as it reads the snapshot
    notes = {m["name"]: m for m in game.snapshot["auctionMail"]}
    assert set(notes) == {"Silk Cloth", "Bolt of Silk Cloth", "Strange Dust"}, "a letter and a 'money on its way' notice are not deals"
    assert (notes["Silk Cloth"]["n"], notes["Silk Cloth"]["count"], notes["Silk Cloth"]["bid"]) == (3, 20, 6000), \
        "three stacks bought in one go, still three after one mail was emptied"
    assert "Bob" not in json.dumps(game.snapshot["auctionMail"]) and "Ann" not in json.dumps(game.snapshot["auctionMail"]), \
        "the other player's name stays in the game's own file"

    def ledger(**args):
        out = run_tool(game, "auction_ledger", args)
        out.pop("note", None)
        return out
    summary = ledger(show=True)
    assert summary["sold"] == {"auctions": 1, "for": "1g", "auction_house_cut": "5s", "received": "95s", "cut_rate": "5.0%",
                               "most_money_from": [{"item": "Bolt of Silk Cloth", "quantity": 5, "total": "95s", "each_on_average": "19s"}]}
    assert (summary["bought"]["auctions"], summary["bought"]["paid"], summary["balance"]) == (4, "2g", "-1g 5s")
    assert game.cards[-1]["table"]["sub"] == "last 30 days · received 95s · paid 2g · balance -1g 5s"
    bought = ledger(view="purchases")["deals"]
    assert [(d["item"], d["quantity"], d["each"], d["paid"]) for d in bought] == [("Silk Cloth", 60, "3s", "1g 80s"), ("Strange Dust", 10, "2s", "20s")]
    assert ledger(view="sales")["deals"][0]["received"] == "95s" and ledger(view="sales", item="dust")["deals"] == []
    assert ledger(days=1, view="purchases")["deals"][0]["item"] == "Silk Cloth" and len(ledger(days=1, view="purchases")["deals"]) == 1
    assert "error" in ledger(view="x") and "error" in ledger(days=0)

    # the mailbox offers what the record does not have; a trade is taken from a mail exactly as it was
    def invest(**args):
        out = run_tool(game, "investments", args)
        out.pop("note", None)
        return out
    offered = invest()["from_the_mailbox"]["purchases_not_tracked"]
    assert [(o["item"], o["quantity"], o["each"]) for o in offered] == [("Silk Cloth", 60, "3s"), ("Strange Dust", 10, "2s")]
    silk_mail = offered[0]["mail"]
    took = invest(action="buy", from_mail=silk_mail)
    assert took["recorded"]["quantity"] == 60 and took["recorded"]["each"] == "3s" and took["investments"][0]["paid_each"] == "3s"
    assert "already recorded" in invest(action="buy", from_mail=silk_mail)["error"] and "error" in invest(action="sell", from_mail=silk_mail)
    assert "error" in invest(action="buy", from_mail=99999) and "error" in invest(action="list", from_mail=silk_mail)
    assert ledger(view="purchases")["deals"][0]["tracked_as_investment"] is True
    assert [o["item"] for o in invest()["from_the_mailbox"]["purchases_not_tracked"]] == ["Strange Dust"]

    # a sale of something tracked: offered, and recorded at what really came in, after the house's cut
    invest(action="buy", item="Bolt of Silk Cloth", quantity=10, price_each="15s")
    sale = invest()["from_the_mailbox"]["sales_of_tracked_items_not_recorded"][0]
    assert (sale["item"], sale["quantity"], sale["each"]) == ("Bolt of Silk Cloth", 5, "20s")
    sold = invest(action="sell", from_mail=sale["mail"])
    assert sold["recorded"]["each"] == "19s", "20s each, less the 5% the house kept"
    bolt = next(i for i in sold["investments"] if i["item"] == "Bolt of Silk Cloth")
    assert (bolt["held"], bolt["made_on_sales"]) == (5, "+20s") and "sales_of_tracked_items_not_recorded" not in invest().get("from_the_mailbox", {})

    # a note seen again replaces itself; an older file gains the new column
    assert app.assets.ingest_mail("all", "Test Realm-Тестер", game.snapshot["auctionMail"]) == 3
    assert len(app.assets.deals("all")) == 3
    import sqlite3
    old = tmp / "old.sqlite"
    db = sqlite3.connect(old)
    db.execute("CREATE TABLE trade(id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT, item INTEGER, name TEXT, kind TEXT, "
               "qty INTEGER, each INTEGER, day INTEGER, at INTEGER, assumed INTEGER, note TEXT)")
    db.execute("INSERT INTO trade(market, item, name, kind, qty, each, day, at, assumed, note) VALUES ('m', 1, 'x', 'buy', 2, 3, 4, 5, 0, '')")
    db.commit()
    db.close()
    assert Assets(old).positions("all")[1]["held"] == 2 and Assets(old).deals("all") == [], "an old file: its trades join the shared market"
    quiet = Game.load(make_wow_dir(tmp / "a", flags="nomail"))
    quiet.assets = Assets(tmp / "quiet.sqlite")
    assert run_tool(quiet, "auction_ledger", {})["deals"] == []


def test_my_auctions(wow: Path, tmp: Path):
    """What the character has up for sale, and whether it is still the lowest."""
    game = Game.load(wow)
    out = run_tool(game, "my_auctions", {"show": True})
    by_item = {a["item"]: a for a in out["auctions"]}
    assert by_item["Silk Cloth"] == {"item": "Silk Cloth", "quantity": 40, "each": "5s", "total": "2g", "status": "about 8h left",
                                     "lowest_seen": "4s 50c", "against_the_market": "undercut: 10% dearer than the lowest"}
    assert by_item["Bolt of Silk Cloth"]["against_the_market"] == "the lowest, or level with it" and "has a bid" in by_item["Bolt of Silk Cloth"]["status"]
    assert by_item["Azure Silk Hood"]["status"].startswith("sold") and "against_the_market" not in by_item["Azure Silk Hood"]
    assert by_item["Fine Thread"]["against_the_market"] == "no listing seen to compare with"
    assert (out["listed_for"], out["sold_and_waiting_in_the_mail"], out["undercut"]) == ("3g 5s", "1g 20s", 1)
    table = game.cards[-1]["table"]
    assert table["sub"] == "listed for 3g 5s · 1 undercut · 1g 20s sold, in the mail" and table["rows"][0] == ["Silk Cloth", 40, "5s", "4s 50c", "8h left"]
    assert "Someone" not in json.dumps(game.snapshot["ownAuctions"]), "who bid stays in the game"
    # seen long before the logout: the time left is counted down, and what has run out says so
    later = Game(dict(game.snapshot, takenAt=game.snapshot["takenAt"] + 3 * 3600))
    gone = {a["item"]: a["status"] for a in run_tool(later, "my_auctions", {})["auctions"]}
    assert gone["Bolt of Silk Cloth"] == "has run out by now, has a bid" and gone["Silk Cloth"] == "about 5h left"
    assert run_tool(later, "my_auctions", {})["seen"].startswith("3.") and "hours before the last logout" in run_tool(later, "my_auctions", {})["seen"]
    unseen = Game.load(make_wow_dir(tmp, flags="noauctions"))
    assert "Auctions tab" in run_tool(unseen, "my_auctions", {})["error"]


def test_bank(tmp: Path):
    """The bank, read while its window was open: counted in plans, in what to sell, and for investments."""
    game = Game.load(make_wow_dir(tmp, flags="bank"))
    banked = game.snapshot["bank"]["items"]
    assert [(i["name"], i["count"], i.get("shared"), i.get("bound")) for i in banked] == [
        ("Silk Cloth", 20, None, None), ("Blue Dye", 15, None, None), ("Hearthstone", 1, None, True),
        ("Silk Cloth", 10, True, None), ("Copper Ore", 30, True, None)], "the character's tab and the account's; nothing read once the window is shut"
    assert game.crafting.bank == {4306: 30, 6260: 15, 6948: 1, 2770: 30}

    # a plan takes from the bags first, then the bank, and buys only the rest
    crafts = [{"recipe_id": 3839, "times": 20}, {"recipe_id": 8760, "times": 10}]
    plan = run_tool(game, "craft_plan", {"crafts": crafts})
    assert plan["materials"][0] == {"item": "Silk Cloth", "needed": 80, "made_in_this_plan": 0, "from_bags": 32, "from_bank": 30, "to_buy": 18,
                                    "price_each": "4s 50c", "cost": "81s"}
    assert plan["materials"][2] == {"item": "Blue Dye", "needed": 20, "made_in_this_plan": 0, "from_bags": 0, "from_bank": 15, "to_buy": 5}
    made = run_tool(game, "shopping_list", {"name": "x", "crafts": crafts})
    assert made["list"] == ["18 × Silk Cloth", "5 × Blue Dye", "7 × Fine Thread"]
    laid = game.cards[-1]["plan"]
    assert laid["materials"][0]["bank"] == 30 and "the bank as it was when you last opened it" in laid["notes"][0]
    hood = run_tool(game, "recipes", {})["professions"][0]["recipes"][0]
    assert hood["materials"][1] == {"item": "Blue Dye", "per_craft": 2, "in_bags": 0, "in_bank": 15, "price_each": None}

    # what to sell counts both; a bound item stays out; an item only in the bank is there too
    sell = run_tool(game, "gold", {"view": "bags", "show": True})
    by_item = {i["item"]: i for i in sell["items"]}
    assert (by_item["Silk Cloth"]["have"], by_item["Silk Cloth"]["of_them_in_bank"], by_item["Silk Cloth"]["worth"]) == (62, 30, "2g 79s")
    assert by_item["Copper Ore"]["have"] == 34 and "Hearthstone" not in by_item and "Blue Dye" not in by_item, "dye: no price anywhere"
    assert game.cards[-1]["table"]["title"] == "What your bags and bank would fetch"
    assert run_tool(game, "item_links", {"item": "Silk Cloth"})["in_bank"] == 30

    # an investment: what is held is looked for in bags and bank together
    from wisp.assets import Assets
    game.assets = Assets(tmp / "a.sqlite")
    held = run_tool(game, "investments", {"action": "buy", "item": "Silk Cloth", "quantity": 100, "price_each": "3s"})["investments"][0]
    assert held["bags_and_bank_hold"] == 62 and "bags_hold" not in held
    assert "bags_and_bank_hold" not in run_tool(game, "investments", {"action": "buy", "item": "Copper Ore", "quantity": 30, "price_each": "50c"})["investments"][1]
    plain = Game.load(make_wow_dir(tmp / "plain"))
    assert plain.crafting.bank == {} and plain.crafting.bank_seen is None and plain.snapshot.get("bank") is None


def test_market_depth(tmp: Path):
    """Not only the lowest price: how many are listed at each price above it."""
    game = Game.load(make_wow_dir(tmp, flags="listings"))
    rows = {r[0]: r for r in game.snapshot["listings"]}
    assert rows[4306][2:] == [7, 0, 0, 0, 450, 40, 455, 50, 470, 70, 500, 75, 700, 115, 2000, 116], \
        "seven auctions from the full scan: how many are listed up to each price, counted from the lowest"
    assert rows[2589][2:] == [2, 0, 0, 0, 120, 20, 130, 40] and rows[4305][2:] == [3, 2, 2050, 0, 2000, 5, 2050, 8, 2600, 20], \
        "a search for one item gives its ladder and where the player's own auctions stand; an auction with no buyout is not a price"
    assert "error" in run_tool(game, "market_depth", {"item": "Silk Cloth"}), "no history file yet"
    game.market = Market(tmp / "m.sqlite")
    game.market.ingest(game.snapshot)

    def depth(**args):
        out = run_tool(game, "market_depth", args)
        out.pop("note", None)
        return out
    silk = depth(item="silk cloth", quantity=60, price="4s 80c", show=True)
    assert (silk["lowest"], silk["listed_in_all"], silk["auctions"], silk["seen"]) == ("4s 50c", 116, 7, "in the last session")
    assert silk["ladder"][:3] == [{"up_to": "4s 50c", "listed": 40, "above_the_lowest": "+0%"}, {"up_to": "4s 55c", "listed": 50, "above_the_lowest": "+1%"},
                                  {"up_to": "4s 70c", "listed": 70, "above_the_lowest": "+4%"}]
    assert silk["biggest_block"] == "40 listed at or just under 4s 50c"
    assert silk["to_buy"] == {"quantity": 60, "at_most": "2g 72s", "each_on_average": "4s 54c", "last_ones_at": "4s 70c"}, \
        "forty at the lowest, then ten and ten dearer"
    assert silk["at_your_price"] == {"price": "4s 80c", "listed_for_less": 70, "against_the_lowest": "+7%"}
    assert game.cards[-1]["table"]["sub"] == "116 listed in 7 auctions · from 4s 50c · seen in the last session"
    assert depth(item="Silk Cloth", quantity=500)["to_buy"] == {"quantity": 500, "not_enough_listed": "only 116 are listed"}
    bolt = depth(item="Bolt of Silk Cloth")
    assert bolt["yours"] == {"listed": 2, "from": "20s 50c", "listed_below_yours": 5}, "five cheaper ones stand in front of the player's own"
    assert "error" in depth(item="Silk Cloth", price="480") and "error" in depth(item="Silk Cloth", quantity=0) and "error" in depth(item="silk")
    assert "no price ladder" in depth(item="Fine Thread")["error"]

    # the player's own auctions: not only undercut or not, but how many stand in front
    mine = {a["item"]: a for a in run_tool(game, "my_auctions", {})["auctions"]}
    assert mine["Silk Cloth"]["listed_for_less"] == 70 and mine["Bolt of Silk Cloth"]["listed_for_less"] == 0 and "listed_for_less" not in mine["Fine Thread"]

    # a ladder that does not climb is not kept; a later look at the same item replaces the earlier one
    bad = dict(game.snapshot, takenAt=game.snapshot["takenAt"] + 5, listings=[[4306, game.snapshot["takenAt"], 2, 0, 0, 0, 500, 10, 400, 20],
                                                                               [2589, game.snapshot["takenAt"] + 60, 1, 0, 0, 0, 90, 7]])
    game.market.ingest(bad)
    assert depth(item="Silk Cloth")["lowest"] == "4s 50c" and depth(item="Linen Cloth")["ladder"] == [{"up_to": "90c", "listed": 7, "above_the_lowest": "+0%"}]
    plain = Game.load(make_wow_dir(tmp / "plain"))
    assert plain.snapshot.get("listings") is None


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


def test_spending_cap(wow: Path):
    """A limit on what a day may cost, across every conversation: on from the first day, and it fails closed."""
    app = App(wow, fake=True)
    assert app.spend() == {"today": 0.0, "cap": 5.0}, "a limit without being asked for one"
    app.new_chat()
    events = list(app.ask("where do I hand in the wolf quest?"))
    assert events[-1] == {"type": "done", "reason": "end"}
    cost = app.chat.usage["cost"]
    assert cost > 0 and abs(app.spent_today() - cost) < 1e-9, "every model response is added to the day"

    # a limit the next answer will cross: it stops between two requests, says why, and sends nothing more
    assert app.save_settings({"daily_cap": round(cost + 0.01, 2)})["spend"]["cap"] == round(cost + 0.01, 2)
    before = app.chat.usage["requests"]
    events = list(app.ask("and again?"))
    assert events[-1] == {"type": "done", "reason": "stopped"}, events[-1]
    assert [e["message"] for e in events if e["type"] == "error"][0].startswith("Stopped here: today's spending limit of $")
    assert app.chat.usage["requests"] == before + 2 and app.spent_today() >= app.prefs["daily_cap"], "it had three to make"
    assert app.chat.messages[-1]["role"] == "assistant", "the conversation is left in a state it can continue from"

    # reached: not one request more, in this conversation or a new one
    spent, asked = app.spent_today(), app.chat.usage["requests"]
    refused = list(app.ask("one more?"))
    assert refused[0]["type"] == "error" and "is reached" in refused[0]["message"] and refused[-1] == {"type": "done", "reason": "error"}
    assert app.chat.usage["requests"] == asked and app.spent_today() == spent and len(app.session["turns"]) == 2
    app.new_chat()
    assert list(app.ask("in a new conversation?"))[0]["type"] == "error"

    # raised, or switched off by choice, it answers again; nonsense is ignored
    app.save_settings({"daily_cap": 0})
    assert list(app.ask("now?"))[-1] == {"type": "done", "reason": "end"} and app.spend()["cap"] == 0
    for nonsense in (-1, 5000, "5", True, None):
        app.save_settings({"daily_cap": nonsense})
    assert app.prefs["daily_cap"] == 0

    # another day starts from nothing; old days are kept two months
    ledger = {f"2026-{m:02d}-{d:02d}": 1.0 for m in (1, 2, 3) for d in range(1, 29)}
    app.spend_file.write_text(json.dumps(ledger))
    assert app.spent_today() == 0.0
    app.add_spend(0.25)
    kept = json.loads(app.spend_file.read_text())
    assert len(kept) == 60 and kept[time.strftime("%Y-%m-%d")] == 0.25 and "2026-01-01" not in kept
    app.spend_file.write_text("not json")
    assert app.spent_today() == 0.0, "a damaged file is an empty one"


def test_localization(tmp: Path):
    """What Wisp writes itself is in the player's language: the game client's, unless Settings says otherwise."""
    import re
    from wisp import i18n
    from wisp.game import RESEARCH_TOOLS, TOOLS
    # every text marked for translation has its Russian, in the code and in the window
    missing = []
    for file in (ROOT / "wisp").glob("*.py"):
        source = file.read_text(encoding="utf-8")
        for key in re.findall(r'(?<![\w.])t\(\s*"((?:[^"\\]|\\.)*)"', source):
            key = key.encode().decode("unicode_escape").encode("latin-1").decode("utf-8") if "\\" in key else key
            if key not in i18n.RU:
                missing.append(f"{file.name}: {key}")
        for key in re.findall(r'(?<![\w.])tn\([^"]*"(?:[^"\\]|\\.)*",\s*"((?:[^"\\]|\\.)*)"', source):
            if key not in i18n.RU_COUNTED:
                missing.append(f"{file.name}: counted: {key}")
    page = (ROOT / "wisp" / "ui.html").read_text(encoding="utf-8")
    window = page[page.index("const RU = {"):page.index("const T = ")]
    script = page[page.index("<script>"):]
    said = re.findall(r'(?<![\w.])T\("((?:[^"\\]|\\.)*)"', script) + re.findall(r'\bhelp: "([^"]*)"', script)
    # the window's fixed texts: what the markup itself says, and what it says in tooltips and to screen readers
    markup = page[page.index("<body>"):page.index("<script>")]
    said += [text.strip() for text in re.findall(r">([^<>]+)<", markup) if text.strip()]
    said += re.findall(r'(?:title|aria-label|placeholder)="([^"]+)"', markup)
    said += [tool["name"] for tool in TOOLS + RESEARCH_TOOLS] + ["the web", "a web page", "ok", "FAIL", "note"]
    own = ("Wisp", "Language / Язык", "Same as the game / Как в игре", "Русский", "English", "EU", "US", "KR", "TW", "…\\World of Warcraft\\_forever_")
    for key in dict.fromkeys(said):
        if f'"{key}":' not in window and key not in own:
            missing.append(f"ui.html: {key}")
    counted = page[page.index("const RU_COUNTED = {"):page.index("const fill = ")]
    for key in re.findall(r'(?<![\w.])TN\([^"]*"(?:[^"\\]|\\.)*",\s*"((?:[^"\\]|\\.)*)"', script):
        if f'"{key}":' not in counted:
            missing.append(f"ui.html: counted: {key}")
    assert not missing, "no Russian for: " + "; ".join(missing[:12])
    assert all(len(forms) == 3 for forms in i18n.RU_COUNTED.values())

    try:
        app = App(make_wow_dir(tmp / "ru", flags="russian"), fake=True)
        game = app.load_game()
        assert i18n.LANG == "ru" and app.state()["lang"] == "ru", "a Russian game client: Russian, without being asked"
        run_tool(game, "questing_route", {})
        route = game.cards[-1]["route"]
        assert route["title"] == "Задания: Elwynn Forest" and route["sub"] == "уровень 12 · сдать: 1, взять: 3, продолжить: 1"
        assert route["steps"][0]["detail"] == "Взять: A Fishy Peril (10 ур., 620 опыта)"
        assert route["steps"][2]["detail"] == "Сдать: Wolves Across the Border (450 опыта)"
        assert route["notes"][1] == "2\u00a0060 опыта во взятых заданиях (21% уровня)" and "Пропущено: серых — 1, красных — 1." in route["notes"][2]
        assert game.cards[-1]["hint"] == "Метки для TomTom: «Копировать», затем в игре дважды Ctrl+V"
        assert game.places["q33.end.1.1"]["title"] == "Eagan Peltskinner (принимает задание)", "the waypoint's name in the game too"
        crafts = [{"recipe_id": 3839, "times": 25}, {"recipe_id": 8760, "times": 10}]
        run_tool(game, "shopping_list", {"name": "Портняжное дело 150-170", "crafts": crafts})
        plan = game.cards[-1]["plan"]
        assert plan["sub"] == "Tailoring, навык 150" and plan["total"] == "Купить: около 3з 6с · без цены на аукционе: 2"
        assert plan["steps"][0]["warning"] == "становится серым на навыке 170: навык дадут не больше первых 20 изготовлений"
        assert plan["materials"][0]["cost"] == "3з 6с", "money in the game's own letters"
        assert [i18n.tn(n, "{n} stop", "{n} stops") for n in (1, 2, 5, 11, 21, 104)] == [
            "1 остановка", "2 остановки", "5 остановок", "11 остановок", "21 остановка", "104 остановки"]
        # every card kind, in Russian: nothing of Wisp's own left in English
        for name, args in (("leveling", {"show": True}), ("gold", {"view": "bags", "show": True}), ("gold", {"view": "crafts", "show": True}),
                           ("my_auctions", {"show": True}), ("farming", {"view": "spots", "show": True}), ("farming", {"view": "sources", "show": True}),
                           ("farming", {"view": "gathering", "show": True}),
                           ("price_history", {"item": "Silk Cloth", "show": True}),
                           ("investments", {"action": "buy", "item": "Silk Cloth", "quantity": 10, "price_each": "3с 50м"}),
                           ("investments", {"item": "Silk Cloth", "show": True}), ("auction_ledger", {"show": True}),
                           ("waypoint", {"refs": ["q33.end.1.1"]})):
            before = len(game.cards)
            out = run_tool(game, name, args)
            assert "error" not in out and len(game.cards) == before + 1, (name, args, out.get("error"))
        names = set(game.crafting.names.values()) | {"TomTom", "Auctionator", "XP", "PvP", "Eagan Peltskinner", "Scout through the Jasperlode Mine"}

        def from_the_game(value):   # names the game itself gave: the fixture's are English, a Russian client's are not
            if isinstance(value, str):
                names.add(value)
            for inner in value.values() if isinstance(value, dict) else value if isinstance(value, list) else ():
                from_the_game(inner)
        from_the_game(game.snapshot)
        for card in game.cards:
            text = json.dumps({k: v for k, v in card.items() if k not in ("wisp", "id")}, ensure_ascii=False)
            for known in sorted((n for n in names if len(n) >= 4 or n in ("XP", "PvP")), key=len, reverse=True):
                text = text.replace(known, "")
            text = text.replace("Ctrl+V", "")
            english = re.findall(r"[A-Za-z]{3,}", re.sub(r'"(points|usual|estimate|low|high|expected|title|sub|columns|rows|notes|stats|line|lines|'
                                                         r'heading|table|chart|plan|route|steps|text|detail|where|hint|addon|materials|item|need|bags|bank|'
                                                         r'buy|cost|unpriced|colour|each|warning|total|null|false|true|orange|yellow|green|grey)"', "", text))
            english = [word for word in english if word not in ("false", "true", "null", "Wisp")]
            assert not english, f"left in English on a card: {english[:8]} in {text[:300]}"
        assert run_tool(game, "investments", {})["investments"][0]["paid_each"] == "3с 50м", "and a price given in Russian is understood"

        # the rest of what Wisp says: since last time, Check setup, what went wrong, the addon's own chat lines
        from wisp.history import ago_words, recap
        changes = {"level": {"from": 11, "to": 12}, "quests_finished": 3, "quests_finished_names": ["A", "B", "C"], "quests_picked_up": ["D"],
                   "quests_abandoned": [], "money_copper": {"change": 12345}, "zone": {"to": "Westfall"}}
        assert recap(changes) == ["Уровень 11 → 12", "Выполнено 3 задания: A, B, C", "Взято: D", "Деньги +1з 23с", "Сейчас: Westfall"]
        assert [ago_words(h) for h in (0.5, 1, 3, 30, 72)] == ["меньше часа", "1 час", "3 часа", "30 часов", "3 дня"]
        checked = {what: (status, detail) for what, status, detail in check(app.wow_dir, call_model=False)}
        assert checked["Папка игры"][0] == "ok" and checked["Снимок читается"][1].startswith("заданий: 2, сохранён ")
        assert checked["Аддон Wisp установлен"] == ("FAIL", "не установлен"), "the marks stay as they are: the window words them"
        assert not [what for what in checked if re.search(r"[A-Za-z]{4,}", what.replace("Wisp", "").replace("Questie", "").replace("TomTom", "")
                                                           .replace("DungeonJournal", "").replace("Anthropic", "").replace("Moonshot", "").replace("OpenRouter", ""))], checked.keys()
        app.prefs["daily_cap"] = 0.01
        app.add_spend(0.02)
        assert next(app.ask("привет"))["message"].startswith("Сегодняшний лимит расходов $0.01 достигнут (потрачено $0.02).")
        assert Game.load(None).problem == "папка игры не указана"
        assert any("это не сообщение Wisp." in line for line in stub("paste", "/run print('hi')", flags="russian"))

        app.save_settings({"language": "en"})
        app.load_game()
        assert i18n.LANG == "en" and app.settings()["language"] == "en", "Settings can say otherwise"
        app.save_settings({"language": "nonsense"})
        assert app.prefs["language"] == "en"
        english = App(make_wow_dir(tmp / "en"), fake=True)
        english.load_game()
        assert i18n.LANG == "en"
    finally:
        i18n.set_language("en")


def test_update(tmp: Path):
    """Wisp keeping itself current: the code alone into its own folder, the exe untouched;
    the launcher choosing what to run; and the rare case of a build that needs another exe."""
    import hashlib
    import importlib.util
    import threading
    import zipfile
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from wisp import update
    from wisp.__main__ import selfcheck
    assert update.current() is None and update.exe() is None, "a copy run from source never updates"
    try:   # the libraries the exe carries; where they are all installed, the check must pass
        import anthropic, keyring, openai, webview   # noqa: F401,E401
        assert selfcheck() is True, "what a downloaded copy is asked before it is trusted"
    except ImportError:
        assert selfcheck() is False, "a library missing: exactly what the check is for"

    site, here, root, stage = tmp / "site", tmp / "install", tmp / "appdata" / "code", tmp / "appdata" / "update"
    site.mkdir(), here.mkdir()
    old, new = "a" * 40, "b" * 40

    def code_zip(commit, runtime="r1", built="2026-11-11T10:00:00Z", extra=()):
        with zipfile.ZipFile(site / "wisp-code.zip", "w") as z:
            z.writestr("wisp/version.json", json.dumps({"commit": commit, "built": built, "runtime": runtime}))
            z.writestr("wisp/__main__.py", "# the new code")
            z.writestr("addon/Wisp/Wisp.lua", "-- the new addon")
            for name in extra:
                z.writestr(name, "x")
        data = (site / "wisp-code.zip").read_bytes()
        return {"code_sha256": hashlib.sha256(data).hexdigest(), "code_size": len(data)}
    exe_bytes = b"MZ new exe " * 200_000
    (site / "Wisp.exe").write_bytes(exe_bytes)
    published = {"commit": new, "built": "2026-11-11T10:00:00Z", "runtime": "r1", "sha256": hashlib.sha256(exe_bytes).hexdigest(),
                 "size": len(exe_bytes), **code_zip(new)}

    def publish(info):
        (site / "version.json").write_text(json.dumps(info))
    publish(published)
    handler = type("Quiet", (SimpleHTTPRequestHandler,), {"log_message": lambda *a: None})
    server = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: handler(*a, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        assert update.latest(base) == published
        exe = here / "Wisp.exe"
        exe.write_bytes(b"MZ old exe")
        app = App(None, fake=True)
        app.exe, app.code_dir, app.stage_dir, real = exe, root, stage, update.current
        tried = []
        app.try_code = lambda folder: tried.append(folder.name) or True
        try:
            update.current = lambda: {"commit": new, "runtime": "r1"}
            assert app.check_update(base) == "current" and not root.exists()
            update.current = lambda: {"commit": old, "runtime": "r1"}
            app.prefs["auto_update"] = False
            assert app.check_update(base) == "off", "switched off in Settings: it does not even look"
            app.updating.acquire()
            assert app.check_update(base, asked=True) == "off", "a look already under way: this one changes nothing"
            app.updating.release()
            assert app.check_update(base, asked=True) == "ready", "but the button in Settings looks all the same"
            import shutil
            shutil.rmtree(root)
            tried.clear()
            app.prefs["auto_update"] = True

            # the usual update: the code alone, into Wisp's own folder, tried before it is trusted
            assert app.check_update(base) == "ready" and app.update["kind"] == "code" and app.update["new"] == "bbbbbbb"
            assert (root / new / "wisp" / "__main__.py").read_text() == "# the new code" and update.running(root) == new
            assert tried == [new + ".new"] and exe.read_bytes() == b"MZ old exe" and sorted(p.name for p in here.iterdir()) == ["Wisp.exe"], \
                "the exe is not touched and nothing appears beside it"
            assert not (root / "code.zip").exists() and not stage.exists()
            assert app.check_update(base) == "ready" and tried == [new + ".new"], "already there: not fetched again"

            # a copy that does not start in this exe is not kept, and neither is one that is not what was announced
            update.current = lambda: {"commit": "c" * 40, "runtime": "r1"}
            app.try_code = lambda folder: False
            (root / "current.json").unlink()
            assert app.check_update(base) == "failed" and update.running(root) is None and not (root / (new + ".new")).exists()
            app.try_code = lambda folder: True
            publish({**published, **code_zip("d" * 40)})
            assert app.check_update(base) == "failed", "the zip holds another commit than the one announced"
            for sneaky in ("../outside.py", "wisp/../../outside.py", "elsewhere/x.py", "wisp/a:b.py"):
                publish({**published, **code_zip(new, extra=[sneaky])})
                assert app.check_update(base) == "failed" and not (tmp / "appdata" / "outside.py").exists(), sneaky
            publish({**published, **code_zip(new), "code_size": 5})
            assert update.latest(base) is None, "a size that cannot be right: the whole announcement is ignored"

            # a build that needs a different exe: the exe is fetched into Wisp's folder, not beside the running one
            publish({**published, **code_zip(new, runtime="r2"), "runtime": "r2"})
            assert app.check_update(base) == "ready" and app.update["kind"] == "exe"
            assert update.pending(stage).read_bytes() == exe_bytes and sorted(p.name for p in here.iterdir()) == ["Wisp.exe"]
            assert update.ready(stage, "f" * 64) is None and update.ready(stage, None) is None
            update.swap(exe, update.ready(stage, published["sha256"]), stage)
            assert exe.read_bytes() == exe_bytes and (stage / "Wisp.old.exe").read_bytes() == b"MZ old exe"
            assert sorted(p.name for p in here.iterdir()) == ["Wisp.exe"], "the old exe is put away out of sight"
            update.tidy(exe, stage)
            assert not (stage / "Wisp.old.exe").exists() and exe.exists()
            try:
                update.swap(exe, stage / "missing.exe", stage)
                raise AssertionError("a swap with nothing to swap in must fail")
            except OSError:
                assert exe.read_bytes() == exe_bytes, "and the exe that was there is back in its place"
            for wrong in ({"sha256": "0" * 64}, {"size": len(exe_bytes) - 1}, {"size": len(exe_bytes) + 1}):
                info = {**published, **wrong}
                assert update.fetch(base + "Wisp.exe", update.pending(stage), info["size"], info["sha256"]) is False
                assert not update.pending(stage).exists() and not (stage / "Wisp.new.exe.part").exists(), wrong
        finally:
            update.current = real
    finally:
        server.shutdown()

    # the launcher: which copy of the code the exe runs
    spec = importlib.util.spec_from_file_location("wisp_app", ROOT / "wisp_app.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    own, home = tmp / "inside", tmp / "home"

    def put(folder, commit, built, runtime="r1"):
        (folder / "wisp").mkdir(parents=True, exist_ok=True)
        (folder / "wisp" / "__main__.py").write_text("")
        (folder / "wisp" / "version.json").write_text(json.dumps({"commit": commit, "built": built, "runtime": runtime}))
    put(own, old, "2026-11-10T10:00:00Z")
    assert launcher.choose(own, home) == own, "nothing downloaded: the copy inside the exe"
    put(home / new, new, "2026-11-11T10:00:00Z")
    (home / "current.json").write_text(json.dumps({"commit": new}))
    assert launcher.choose(own, home) == home / new and (home / "starting").read_text() == new
    update.started_fine(home)   # its window came up
    assert launcher.choose(own, home) == home / new
    assert launcher.choose(own, home) == own and not (home / "current.json").exists(), \
        "it never said its window came up: dropped, and the copy inside takes over"
    (home / "current.json").write_text(json.dumps({"commit": new}))
    put(own, "e" * 40, "2026-11-12T10:00:00Z")
    assert launcher.choose(own, home) == own, "a newer exe was installed by hand: its own code wins"
    put(own, old, "2026-11-10T10:00:00Z", runtime="r2")
    assert launcher.choose(own, home) == own, "code made for another exe is never run"
    for nonsense in ('{"commit": "../../x"}', "not json", '{"commit": "' + "f" * 40 + '"}'):
        (home / "current.json").write_text(nonsense)
        assert launcher.choose(own, home) == own


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


def test_quest_search(tmp: Path):
    """Any quest at all: the addon reads Questie's whole list once, in the background, and keeps it."""
    lines = stub("snapshot", flags="questindex")
    assert "QUESTSAGAIN KEPT" in lines, "the next login keeps what was read: it is read again only when Questie or the character changes"
    wow = make_wow_dir(tmp, flags="questindex")
    (wow / "Interface" / "AddOns" / "Questie").mkdir(parents=True)
    game = Game.load(wow)
    db = game.questdb()
    assert db is not None and 9001 not in db.quests and 9003 not in db.quests, "another race's and another class's quests are left out"
    assert 9002 not in db.quests and {7, 33, 155, 166, 386} <= set(db.quests), "and so are those Questie hides"

    both = run_tool(game, "quest_search", {"name": "defias"})
    assert both["found"] == 2 and [q["id"] for q in both["quests"]] == [155, 166]
    first, second = both["quests"]
    assert first["state"] == "open by its requirements" and first["zone"] == "Westfall", "its earlier quest (7) is done, and level 9 is reached"
    assert first["starts"][0]["name"] == "Gryan Stoutmantle" and first["starts"][0]["where"]["zone"] == "Westfall"
    assert second["state"] == "not yet" and second["why_not_open"] == ["needs level 14", "first: The Defias Brotherhood (id 155)"]
    assert second["kind"] == "dungeon" and second["colour"] == "red"
    assert second["kill"] == [{"name": "Edwin VanCleef", "kind": "npc", "where": "no place known: inside an instance, or Questie has none"}]
    assert [(c["id"], c["state"]) for c in second["chain"]] == [(7, "done"), (155, "open by its requirements"), (166, "not yet")] and second["chain"][2]["this_one"]

    wolves = run_tool(game, "quest_search", {"name": "Wolfs Across the Border"})["quests"]   # misspelt: the closest name
    assert len(wolves) == 1 and wolves[0]["state"] == "in the log" and wolves[0]["objective"].startswith("Bring 8 pieces")
    assert wolves[0]["bring"] == ["Tough Wolf Meat"] and wolves[0]["kill"][0]["name"] == "Prowler"
    assert wolves[0]["starts"][0]["name"] == "Eagan Peltskinner", "colour codes stripped"
    ref = wolves[0]["starts"][0]["where"]["ref"]
    assert "error" not in run_tool(game, "waypoint", {"refs": [ref]}), "every place is a ref the waypoint tool takes"
    assert [c["id"] for c in wolves[0]["chain"]] == [33, 34]
    assert run_tool(game, "quest_search", {"id": 7})["quest"]["state"] == "done"
    assert run_tool(game, "quest_search", {"id": 40})["quest"]["state"] == "open now"
    assert "another race or class" in run_tool(game, "quest_search", {"id": 9001})["error"]

    assert [q["id"] for q in run_tool(game, "quest_search", {"zone": "westfall", "kind": "dungeon"})["quests"]] == [166]
    elwynn = run_tool(game, "quest_search", {"zone": "here", "limit": 4})
    assert elwynn["found"] > 4 and len(elwynn["quests"]) == 4 and elwynn["left_out"] == elwynn["found"] - 4
    assert [q.get("level", 0) for q in elwynn["quests"]] == sorted(q.get("level", 0) for q in elwynn["quests"]), "by level"
    assert all(q["id"] != 7 for q in run_tool(game, "quest_search", {"zone": "here", "hide_done": True, "limit": 40})["quests"])
    assert "error" in run_tool(game, "quest_search", {}) and "error" in run_tool(game, "quest_search", {"zone": "Narnia"})
    assert run_tool(game, "quest_search", {"name": "no such quest anywhere"})["found"] == 0
    from wisp.questdb import fold
    assert fold("Ёжик, В Тумане!") == fold("ежик в тумане"), "as people type names: case, ё and punctuation do not matter"
    checked = {what: (status, detail) for what, status, detail in check(wow, call_model=False)}
    assert checked["Every quest from Questie"] == ("ok", f"{len(db.quests)} quests this character could take")

    # every item, too: who sells a recipe the character has never held
    assert sorted(game.graph.world) == [2321, 4306, 7090], "items that come from nowhere, or only from the other faction's vendors, are left out"
    found = run_tool(game, "recipes", {"search": "learned yet"})
    assert found["found"] == 1 and found["recipes"][0]["learned"] is False
    how = found["recipes"][0]["how_to_learn"]
    assert how["the_game_says"] == "Vendor: Alexandra Bolero", "the game's own text, its colour codes stripped"
    scroll = how["recipe_items"][0]
    assert scroll["item"] == "Pattern: Not Learned Yet" and [v["name"] for v in scroll["sold_by"]] == ["Alexandra Bolero"], "not the Horde's vendor"
    assert scroll["sold_by"][0]["where"][0]["zone"] == "Stormwind City"
    assert "error" not in run_tool(game, "waypoint", {"refs": [scroll["sold_by"][0]["where"][0]["ref"]]})
    assert "how_to_learn" not in run_tool(game, "recipes", {"search": "bolt of silk"})["recipes"][0], "a learned recipe needs none"
    assert run_tool(game, "recipes", {"search": "no such dish"})["found"] == 0
    link = run_tool(game, "item_links", {"item": "pattern not learned"})   # no colon typed: found all the same
    assert link["item"] == "Pattern: Not Learned Yet" and link["sold_by"][0]["name"] == "Alexandra Bolero"
    assert link["teaches"] == {"recipe_id": 8791, "recipe": "Not Learned Yet", "profession": "Tailoring", "already_learned": False}
    assert checked["Every item from Questie"] == ("ok", "3 items with who sells or drops them")

    plain = Game.load(make_wow_dir(tmp / "plain"))   # not read yet: says how it comes
    assert "about a minute after login" in run_tool(plain, "item_links", {"item": "Pattern: Not Learned Yet"})["error"]
    assert "how_to_learn" in run_tool(plain, "recipes", {"search": "learned yet"})["recipes"][0], "the game's own text is there without Questie's list"
    assert "about a minute after login" in run_tool(plain, "quest_search", {"name": "defias"})["error"]


def test_shared_market(tmp: Path):
    """Every character shares one market: what one scanned, another sees, whatever its realm or faction."""
    import sqlite3
    file = tmp / "market.sqlite"
    old = sqlite3.connect(file)   # as earlier versions kept it: per realm and faction
    old.executescript("CREATE TABLE price(market TEXT, item INTEGER, day INTEGER, low INTEGER, high INTEGER, listed INTEGER, PRIMARY KEY (market, item, day)) WITHOUT ROWID;"
                      "CREATE TABLE trade(id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT, item INTEGER, name TEXT, kind TEXT, qty INTEGER, each INTEGER, day INTEGER, at INTEGER, assumed INTEGER, note TEXT, mail TEXT);")
    old.executemany("INSERT INTO price VALUES (?, ?, ?, ?, ?, ?)", [("R-Alliance", 4306, 20000, 400, 450, 10), ("R-Horde", 4306, 20000, 380, 420, 5),
                                                                    ("R-Horde", 4306, 20001, 390, 390, 7), ("Other-Alliance", 2589, 20001, 100, 120, 3)])
    old.execute("INSERT INTO trade(market, item, name, kind, qty, each, day, at, assumed, note) VALUES ('R-Horde', 4306, 'Silk Cloth', 'buy', 5, 300, 20000, 0, 0, '')")
    old.commit(); old.close()
    market = Market(file)
    alliance = {"who": {"name": "A", "realm": "R"}, "character": {"faction": "Alliance"}, "takenAt": 20001 * 86400}
    horde = {"who": {"name": "H", "realm": "Other"}, "character": {"faction": "Horde"}, "takenAt": 20001 * 86400}
    assert market.size(alliance) == market.size(horde) and market.size(horde)["rows"] == 3, "folded into one: a clash keeps one row"
    assert [row[0] for row in market.series(horde, 4306, 30)] == [20000, 20001], "a Horde character on another realm sees the Alliance scans"
    from wisp.assets import Assets
    assert len(Assets(file).trades("all")) == 1, "investments are shared too"


def test_blizzard(wow: Path):
    """Check setup tries Blizzard's WoW Forever data with the player's own client, against a stand-in of the API."""
    import gzip
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse
    from wisp.blizzard import Blizzard, probe
    stack = {"on": False}   # whether the stand-in prices a listing for the whole stack
    recipes = {"on": True}   # whether the stand-in has professions at all: nobody knows yet whether Forever's does

    def house(number):
        if number == 110:
            return {"auctions": [{"id": 1, "item": {"id": 999}, "buyout": 10, "quantity": 1, "time_left": "LONG"}]}
        each = (lambda a: a["buyout"] * a["quantity"]) if stack["on"] else (lambda a: a["buyout"])
        mine = [{"item": 4306, "quantity": 40, "buyout": 500}, {"item": 4305, "quantity": 5, "buyout": 2000}]
        return {"auctions": [{"id": 10 + i, "item": {"id": a["item"]}, "buyout": each(a), "quantity": a["quantity"], "time_left": "LONG"}
                             for i, a in enumerate(mine)] + [{"id": 30, "item": {"id": 4306}, "buyout": 450, "quantity": 20, "time_left": "SHORT"}]}

    class StandIn(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, packed=False):
            data = json.dumps(body).encode()
            if packed:
                data = gzip.compress(data)
            self.send_response(code)
            if packed:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            good = "Basic " + base64.b64encode(b"good-id:good-secret").decode()
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path != "/token" or self.headers.get("Authorization") != good:
                return self.reply(401, {"error": "invalid_client"})
            self.reply(200, {"access_token": "tok", "expires_in": 86399})

        def do_GET(self):
            url = urlparse(self.path)
            ns = parse_qs(url.query).get("namespace", [""])[0]
            if self.headers.get("Authorization") != "Bearer tok":
                return self.reply(401, {})
            if url.path == "/data/wow/item/4306" and ns == "static-forever-eu":
                return self.reply(200, {"id": 4306, "name": "Silk Cloth"})
            if ns == "static-forever-eu" and recipes["on"]:   # every profession's recipes, as other versions of the game have them
                book = {"/data/wow/profession/index": {"professions": [{"id": 197, "name": "Tailoring"}, {"id": 185, "name": "Cooking"}]},
                        "/data/wow/profession/185": {"id": 185, "name": "Cooking", "skill_tiers": [{"id": 2548, "name": "Classic Cooking"}]},
                        "/data/wow/profession/185/skill-tier/2548": {"categories": [{"name": "Everyday", "recipes": [{"id": 8604, "name": "Herb Baked Egg"}, {"id": 2538, "name": "Charred Wolf Meat"}]}]},
                        "/data/wow/recipe/8604": {"id": 8604, "name": "Herb Baked Egg", "crafted_item": {"id": 6888},
                                                  "reagents": [{"reagent": {"id": 6889, "name": "Small Egg"}, "quantity": 1}, {"reagent": {"id": 2678, "name": "Mild Spices"}, "quantity": 1}]}}
                if url.path in book:
                    return self.reply(200, book[url.path])
            if ns != "dynamic-forever-eu":
                return self.reply(404, {})
            if url.path == "/data/wow/ruleset/index":
                return self.reply(200, {"rulesets": [{"id": 1, "name": "Normal"}, {"id": 15, "name": "Hardcore"}]})
            if url.path == "/data/wow/ruleset/1/auctions/":
                return self.reply(200, {"auctions": [{"id": 110, "name": "Alliance Auction House 1"}, {"id": 113, "name": "Horde Auction House 1"},
                                                     {"id": 111, "name": "Alliance Auction House 2"}]})
            if url.path == "/data/wow/ruleset/1/auctions/111/commodities":   # trade goods: priced for one
                return self.reply(200, {"auctions": [{"id": 50, "item": {"id": 2321}, "quantity": 10, "unit_price": 50, "time_left": "SHORT"}]})
            parts = url.path.split("/")
            if url.path.startswith("/data/wow/ruleset/1/auctions/") and len(parts) == 7 and parts[6].isdigit():
                return self.reply(200, house(int(parts[6])), packed=True)
            self.reply(404, {})

    server = ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        snapshot = Game.load(wow).snapshot

        def run(client_id="good-id", secret="good-secret", snap=snapshot):
            return {what: (ok, detail) for what, ok, detail in probe(Blizzard(client_id, secret, "eu", oauth=base + "/token", api=base), snap)}
        refused = run(secret="wrong")
        assert refused == {"Blizzard API: the client": (False, "Blizzard did not accept the client: HTTP 401")}
        lines = run()
        assert lines["Blizzard API: the client"] == (True, "accepted (EU)")
        assert lines["Blizzard API: WoW Forever"] == (True, "2 rulesets: Normal, Hardcore")
        assert lines["Blizzard API: items"] == (True, "item 4306 is Silk Cloth")
        assert lines["Blizzard API: professions"] == (True, "2: Tailoring, Cooking")
        assert lines["Blizzard API: recipes"] == (True, "Cooking: 1 tiers, 2 recipes in the first; Herb Baked Egg needs 1 × Small Egg, 1 × Mild Spices; "
                                                        "a recipe has: crafted_item, id, name, reagents")
        recipes["on"] = False
        without = run()
        assert without["Blizzard API: professions"] == (False, "HTTP 404") and "Blizzard API: recipes" not in without
        assert without["Blizzard API: your auction house"][0] is True, "no professions there: the rest is checked all the same"
        recipes["on"] = True
        assert lines["Blizzard API: listings"] == (True, "house 110: 1 auctions, 0 trade goods; fields: buyout, id, item, quantity, time_left")
        assert lines["Blizzard API: your auction house"] == (True, "house 111 Alliance Auction House 2: 3 of your 3 auctions are in it"), \
            "found by the player's own auctions, a trade good among them; the Horde house is not even fetched"
        assert lines["Blizzard API: price per item or per stack"] == (True, "the API's buyout is the game's price for one (2 auctions agree)")
        stack["on"] = True
        assert run()["Blizzard API: price per item or per stack"] == (True, "the API's buyout is for the whole stack (2 auctions agree)")
        bare = dict(snapshot, ownAuctions=None)
        assert run(snap=bare)["Blizzard API: your auction house"][0] is False, "with no auction of the player's up, it says how to find out"
        assert Blizzard.from_saved("id:secret").client_id == "id" and Blizzard.from_saved("nonsense") is None and Blizzard.from_saved(None) is None
        # the same data for the assistant: one item's listings, the house found by itself and remembered
        from wisp.blizzard import Live
        stack["on"], kept = True, []
        client = Blizzard("good-id", "good-secret", "eu", oauth=base + "/token", api=base)
        game = Game.load(wow)
        assert "has not set this up yet" in run_tool(game, "live_auctions", {"item": "Silk Cloth"})["error"] or game.live is None
        game.live = Live(lambda: None)
        told = run_tool(game, "live_auctions", {"item": "Silk Cloth"})["error"]
        assert "has not set this up yet" in told and "develop.battle.net" in told, "no client: the assistant can say how to set it up"
        game.live = Live(lambda: client, None, kept.append)
        silk = run_tool(game, "live_auctions", {"item": "silk cloth"})
        assert silk["auction_house"] == "Alliance Auction House 2" and kept[0]["house"] == 111 and kept[0]["each"] is False
        assert (silk["lowest"], silk["auctions"], silk["listed_in_all"]) == ("22c", 2, 60), "a stack's buyout, divided by its size"
        assert silk["price_ladder"] == [{"each": "22c", "listed": 20, "listed_up_to_here": 20}, {"each": "5s", "listed": 40, "listed_up_to_here": 60}]
        assert silk["you_hold"] == 32
        thread = run_tool(game, "live_auctions", {"item": "Fine Thread"})
        assert thread["lowest"] == "50c" and thread["listed_in_all"] == 10, "a trade good: priced for one as it is"
        assert "nothing_listed" in run_tool(game, "live_auctions", {"item": "Hearthstone"})
        again = Live(lambda: client, kept[0])   # the next start: the house is remembered, nothing is searched for
        again_game = Game.load(wow)
        again_game.live = again
        assert run_tool(again_game, "live_auctions", {"item": "Silk Cloth"})["lowest"] == "22c"
        bare_game = Game(dict(snapshot, ownAuctions=None))
        bare_game.live = Live(lambda: client)
        assert "puts one auction up" in run_tool(bare_game, "live_auctions", {"item": "Silk Cloth"})["error"]
        stack["on"] = False

        checked = {what: status for what, status, _ in check(wow, call_model=False, battlenet="missing")}
        assert checked["Blizzard API"] == "note", "not set up: an optional line, not a failure"
    finally:
        server.shutdown()


def test_one_click_cards(wow: Path, tmp: Path):
    """A card reaches the running game without a paste: Wisp writes files, the real addon reads them as the game does."""
    import shutil
    from wisp import inbox
    addons = tmp / "AddOns"
    assert inbox.install(addons) is True and inbox.installed(addons), "new files: the game must start again to see them"
    assert inbox.install(addons) is False, "what is there is left alone"
    assert len(list(addons.iterdir())) == inbox.SLOTS + 1 and (addons / "Wisp_Inbox" / "valid.wav").read_bytes()[:4] == b"RIFF"
    assert "LoadOnDemand: 1" in (addons / "Wisp_In001" / "Wisp_In001.toc").read_text() and "LoadOnDemand" not in (addons / "Wisp_Inbox" / "Wisp_Inbox.toc").read_text()

    def game(folder, later=None, flags="", delivered=None):
        env = {**os.environ, "WISP_STUB": flags, "WISP_ADDONS": str(folder)}
        if later:
            env["WISP_ADDONS_LATER"] = str(later)
        if delivered:
            env["WISP_DELIVERED"] = str(delivered)
        ran = subprocess.run([*LUA, "inbox"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)
        assert ran.returncode == 0, ran.stderr + ran.stdout
        return ran.stdout.splitlines()

    def waypoint(title, x):
        return encode_wisp1([{"do": "tomtom.waypoint", "map": 1429, "x": x, "y": 40.2, "title": title}])
    quiet = game(addons)
    assert "ATLOGIN 0" in quiet and "SLOTS 0" in quiet and not [l for l in quiet if l.startswith("WAY ")], "nothing sent: nothing loaded"

    # sent while the game runs: it sees the signal within a second, loads one slot, runs the message
    later = tmp / "later"
    shutil.copytree(addons, later)
    assert inbox.deliver(later, waypoint("First", 48.9)) is None
    live = game(addons, later)
    assert "ATLOGIN 0" in live and "WAY tomtom 1429 0.489 0.402 First" in live and "SLOTS 1" in live, live
    assert (later / "Wisp_Inbox" / "001.wav").stat().st_size > 0 and (later / "Wisp_Inbox" / "002.wav").stat().st_size == 0
    assert "WispCursor = 2" in (later / "Wisp_Inbox" / "Cursor.lua").read_text()
    number = int(float(next(l for l in live if l.startswith("DELIVERED ")).split()[1]))

    # sent while the game was closed: run at login, once; after a reload it is not run again
    queued = game(later)
    assert "ATLOGIN 1" in queued and "WAY tomtom 1429 0.489 0.402 First" in queued
    again = game(later, delivered=number)
    assert "ATLOGIN 0" in again and "SLOTS 0" in again, "already run: not even a slot is spent"

    # two more in quick succession: one slot brings both, and the earlier one is not run twice
    third = tmp / "third"
    shutil.copytree(later, third)
    assert inbox.deliver(third, waypoint("Second", 10)) is None and inbox.deliver(third, waypoint("Third", 20)) is None
    both = game(later, third, delivered=number)
    assert [l for l in both if l.startswith("WAY ")] == ["WAY tomtom 1429 0.1 0.402 Second", "WAY tomtom 1429 0.2 0.402 Third"], both
    assert "SLOTS 1" in both, "the slot said where the signals stand: the second signal costs no slot"

    # an old message is not run late; what is not a Wisp message is never written; the reader stays as strict as a paste
    stale = tmp / "stale"
    shutil.copytree(addons, stale)
    assert inbox.deliver(stale, waypoint("Old", 30), now=time.time() - 3600) is None
    assert not [l for l in game(stale) if l.startswith("WAY ")], "an hour old: dropped"
    for bad in ('WISP1:abc"\nos.exit()', "print('hi')", "WISP1:", 5, None):
        assert inbox.deliver(stale, bad) == "not a Wisp message", bad
    unknown = encode_wisp1([{"do": "run.lua", "code": "os.exit()"}])
    assert inbox.deliver(stale, unknown) is None
    assert any("skipped unknown command run.lua" in l for l in game(stale)), "the same whitelist as a paste"
    assert inbox.deliver(tmp / "nowhere", waypoint("x", 1)) == "the inbox is not installed"

    # a game whose sound cannot tell an empty file from a real one: off, and Wisp is told so
    assert "SLOTS 0" in game(later, flags="nosound")
    def told(env_addons, flags=""):
        lines = subprocess.run([*LUA, "snapshot"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                               env={**os.environ, "WISP_STUB": flags, "WISP_ADDONS": str(env_addons)}).stdout.splitlines()
        return json.loads(base64.b64decode(next(l for l in lines if l.startswith("SNAPSHOT ")).split()[1]))["addons"]["inbox"]
    assert told(addons) is True and told(addons, "nosound") is False

    # the app: installs the files beside the addon, offers Send only once the game has said it reads them
    folder = make_wow_dir(tmp / "wow")
    (folder / "Interface" / "AddOns").mkdir(parents=True)
    app = App(folder, fake=True)
    app.ensure_addon()
    assert inbox.installed(folder / "Interface" / "AddOns") and "Restart the game" in app.addon_note
    assert app.state()["inbox"] is False, "the game has not said yet that it reads the inbox"
    assert app.can_deliver({"addons": {"inbox": True}}) is True
    assert app.deliver(waypoint("From the app", 5)) == {"sent": True}
    assert "From the app" not in (folder / "Interface/AddOns/Wisp_In100/In.lua").read_text() and "WISP1:" in (folder / "Interface/AddOns/Wisp_In100/In.lua").read_text()
    assert "error" in app.deliver("rm -rf")


def test_alts_recipes(tmp: Path):
    """An alt's professions count too: every character's recipe books are handed over, the last-played one's first."""
    game = Game.load(make_wow_dir(tmp, flags="alts"))
    books = [(b["profession"], b["owner"]) for b in game.crafting.books]
    assert books == [("Tailoring", None), ("Cooking", "another character of yours: Warrior, level 30"), ("Tailoring", "another character of yours: ?")], books
    listed = run_tool(game, "recipes", {"show": "all"})["professions"]
    assert "character" not in listed[0] and listed[1]["character"].endswith("Warrior, level 30") and listed[1]["skill"] == 75
    meat = run_tool(game, "recipes", {"search": "wolf meat"})["recipes"][0]
    assert meat["name"] == "Charred Wolf Meat" and meat["crafter"].endswith("Warrior, level 30") and "learned" not in meat
    # the same recipe in two books: this character's own stands, unless only the alt has learned it
    assert game.crafting.recipes[3839]["owner"] is None and game.crafting.recipes[3839]["colour"] == "yellow"
    assert game.crafting.recipes[8791]["owner"] == "another character of yours: ?" and game.crafting.recipes[8791]["learned"], "the alt can make it"
    first = run_tool(game, "recipes", {"search": "learned yet"})["recipes"]
    assert [bool(r.get("crafter")) for r in first] == [False, True], "this character's own first"
    # the graph links through an alt's recipes too
    used = run_tool(game, "item_links", {"item": "Tough Wolf Meat"})["used_in_your_recipes"]
    assert used == [{"recipe_id": 2538, "recipe": "Charred Wolf Meat", "profession": "Cooking", "needs_each": 1, "colour": "green",
                     "makes": used[0]["makes"], "crafter": "another character of yours: Warrior, level 30"}]
    assert "Realm" not in json.dumps(run_tool(game, "recipes", {"show": "all"})), "an alt is told by class and level, never by name or realm"
    assert all(b["owner"] is None for b in Game.load(make_wow_dir(tmp / "solo")).crafting.books)


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
        with tempfile.TemporaryDirectory() as q:
            test_available_quests(wow, Path(q))
        test_questing_route(wow)
        with tempfile.TemporaryDirectory() as al:
            test_alts_recipes(Path(al))
        with tempfile.TemporaryDirectory() as oc:
            test_one_click_cards(wow, Path(oc))
        test_blizzard(wow)
        with tempfile.TemporaryDirectory() as sm:
            test_shared_market(Path(sm))
        with tempfile.TemporaryDirectory() as qs:
            test_quest_search(Path(qs))
        test_leveling(wow)
        with tempfile.TemporaryDirectory() as v:
            test_investments(wow, Path(v))
        with tempfile.TemporaryDirectory() as m:
            test_auction_mail(wow, Path(m))
        with tempfile.TemporaryDirectory() as o:
            test_my_auctions(wow, Path(o))
        with tempfile.TemporaryDirectory() as r:
            test_gold(wow, Path(r))
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
        test_spending_cap(wow)
        test_settings_api(wow)
        with tempfile.TemporaryDirectory() as e:
            test_sessions(wow, Path(e))
    with tempfile.TemporaryDirectory() as d:
        test_this_pc(Path(d))
    with tempfile.TemporaryDirectory() as bk:
        test_bank(Path(bk))
    with tempfile.TemporaryDirectory() as md:
        test_market_depth(Path(md))
    with tempfile.TemporaryDirectory() as loc:
        test_localization(Path(loc))
    with tempfile.TemporaryDirectory() as u:
        test_update(Path(u))
    print("mvp: OK (%s)" % next(l for l in stub("snapshot") if l.startswith("LUA ")))
