"""The item graph: how things in the snapshot hang together through items.

An item is the hub. Around it: the recipes that use it and the ones that make
it, the quests that ask for it, the bosses that drop it, who sells it, what
drops it in the open world, and its price. The model asks for one item and
gets everything one step away, then follows whichever link matters.

Built in memory from the current snapshot each time it is read. On top of it
comes Questie's whole item list, when the addon has read it (WispItems in the
saved file): who sells, drops or holds any item at all, and with it the recipe
items ("Recipe: Herb Baked Egg") that teach the recipes of the character's
professions, matched by the name after the colon.
"""
from .crafting import _text, coins
from .i18n import t


def _list(value) -> list:
    return value if isinstance(value, list) else []


class ItemGraph:
    def __init__(self, game):
        craft, snapshot = game.crafting, game.snapshot
        self.game, self.gathering = game, game.gathering
        self.world: dict[int, dict] = {}      # item -> its entry in Questie's whole item list
        self.people: dict[str, dict] = {"npc": {}, "object": {}}
        self.maps: dict[int, str] = {}
        self.teachers: dict[str, list[int]] = {}   # what a recipe item's name says it teaches -> those items
        self.names: dict[int, str] = dict(craft.names)
        self.used_in: dict[int, list] = {}    # item -> recipes that need it
        self.made_by: dict[int, list] = {}    # item -> recipes that make it
        self.quests: dict[int, list] = {}     # item -> quests that ask for it
        self.bosses: dict[int, list] = {}     # item -> bosses that drop it
        self.sources: dict[int, dict] = {}    # item -> who sells it, what drops it, what it is found in

        for recipe in craft.recipes.values():
            for item, count in recipe["reagents"]:
                self.used_in.setdefault(item, []).append((recipe, count))
            if recipe["makes"]:
                self.made_by.setdefault(recipe["makes"], []).append(recipe)

        def name(item, text):
            text = _text(text, 80)
            if isinstance(item, int) and not isinstance(item, bool) and item > 0:
                if text:
                    self.names.setdefault(item, text)
                return item
            return None

        for raw, quest in zip((q for q in _list(snapshot.get("quests")) if isinstance(q, dict)), game.quests):
            for need in (n for n in _list(raw.get("needs")) if isinstance(n, dict)):
                item = name(need.get("id"), need.get("name"))
                if item:
                    self.quests.setdefault(item, []).append({"quest": quest["title"], "where": "in your quest log",
                                                             "status": "ready to hand in" if quest["complete"] else "in progress"})
        for raw, quest in zip((q for q in _list(snapshot.get("available")) if isinstance(q, dict)), game.available):
            for need in (n for n in _list(raw.get("needs")) if isinstance(n, dict)):
                item = name(need.get("id"), need.get("name"))
                if item:
                    self.quests.setdefault(item, []).append({"quest": quest["title"], "where": "open to you, not picked up",
                                                             "status": f"level {quest['level']}" + ("" if quest["kind"] == "normal" else f", {quest['kind']}")})
        for raw, dungeon in zip((d for d in _list(snapshot.get("dungeons")) if isinstance(d, dict)), game.dungeons):
            status = {q["id"]: q["status"] for q in dungeon["quests"]}
            for quest in (q for q in _list(raw.get("quests")) if isinstance(q, dict)):
                for need in (n for n in _list(quest.get("items")) if isinstance(n, dict)):
                    item = name(need.get("id"), need.get("name"))
                    if item:
                        self.quests.setdefault(item, []).append({"quest": _text(quest.get("name"), 80),
                                                                 "where": f"dungeon quest: {dungeon['name']}",
                                                                 "status": status.get(quest.get("id"), "?")})
            for boss in (b for b in _list(raw.get("bosses")) if isinstance(b, dict)):
                for loot in (i for i in _list(boss.get("loot")) if isinstance(i, list) and len(i) >= 2):
                    item = name(loot[0], loot[1])
                    if item:
                        self.bosses.setdefault(item, []).append({"boss": _text(boss.get("name"), 80), "dungeon": dungeon["name"]})

        for entry in (e for e in _list(snapshot.get("sources")) if isinstance(e, dict)):
            item = name(entry.get("id"), entry.get("name"))
            if not item:
                continue
            label = self.names.get(item, f"item {item}")
            found = {
                "sold_by": game._who(f"i{item}.vendor", entry.get("vendors"), t("sells {item}", item=label)),
                "dropped_by": game._who(f"i{item}.drop", entry.get("drops"), t("drops {item}", item=label)),
                "found_in": game._who(f"i{item}.object", entry.get("objects"), t("holds {item}", item=label)),
            }
            for key, total in (("dropped_by", entry.get("dropCount")), ("found_in", entry.get("objectCount"))):
                if isinstance(total, int) and total > len(found[key]):
                    found[key + "_in_all"] = total   # Questie knows more than the few listed here
            self.sources[item] = {k: v for k, v in found.items() if v}

    def attach(self, index: dict | None):
        """Questie's whole item list, as the addon read it."""
        if not index:
            return
        for kind, rows in (("npc", index.get("npcs")), ("object", index.get("objects"))):
            self.people[kind] = {r["id"]: r for r in _list(rows) if isinstance(r, dict) and isinstance(r.get("id"), int)}
        self.maps = {m["id"]: m.get("name") for m in _list(index.get("maps")) if isinstance(m, dict) and isinstance(m.get("id"), int)}
        for entry in _list(index.get("items")):
            name = _text(entry.get("name"), 80) if isinstance(entry, dict) else None
            if not name or not isinstance(entry.get("id"), int) or isinstance(entry["id"], bool):
                continue
            self.world[entry["id"]] = entry
            self.names.setdefault(entry["id"], name)
            if ":" in name:   # "Recipe: Herb Baked Egg", "Pattern: ...", "Plans: ...": in any language, what follows the colon
                self.teachers.setdefault(name.split(":", 1)[1].strip().lower(), []).append(entry["id"])

    def _from_world(self, item: int) -> dict:
        """Who sells an item, what drops it and what holds it, from the whole list; places get refs."""
        entry, label = self.world.get(item), self.names.get(item, f"item {item}")
        if not entry:
            return {}

        def people(kind, ids):
            out = []
            for some in _list(ids):
                known = self.people[kind].get(some)
                if known and _text(known.get("name"), 80):
                    spot = [{"map": known["map"], "zone": self.maps.get(known["map"]), "x": known.get("x"), "y": known.get("y")}] if known.get("map") else []
                    out.append({"kind": kind, "name": known["name"], "places": spot})
            return out
        found = {"sold_by": self.game._who(f"w{item}.vendor", people("npc", entry.get("v")), t("sells {item}", item=label)),
                 "dropped_by": self.game._who(f"w{item}.drop", people("npc", entry.get("d")), t("drops {item}", item=label)),
                 "found_in": self.game._who(f"w{item}.object", people("object", entry.get("o")), t("holds {item}", item=label))}
        for key, total in (("dropped_by", entry.get("dn")), ("found_in", entry.get("on"))):
            if isinstance(total, int) and total > len(found[key]):
                found[key + "_in_all"] = total
        return {k: v for k, v in found.items() if v}

    def learn_from(self, recipe: dict) -> dict:
        """How a recipe is come by: the recipe item that teaches it and who sells or drops that, and the game's own word."""
        out = {}
        if recipe.get("source"):
            from .game import _clean   # game text: no colour codes or links
            out["the_game_says"] = _clean(recipe["source"], 200)
        made = self.names.get(recipe["makes"], "") if recipe["makes"] else ""
        scrolls = self.teachers.get(recipe["name"].strip().lower()) or self.teachers.get(made.strip().lower()) or []
        if scrolls:
            out["recipe_items"] = [{"item": self.names[s], **(self._from_world(s) or {"source": "none known"})} for s in scrolls[:3]]
        return out

    def find(self, query: str) -> list[int]:
        """Items by name: the exact one, else those containing the words, shortest name first."""
        from .questdb import fold   # as people type names: case, ё and punctuation do not matter
        query = fold(query)
        if not query:
            return []
        folded = [(fold(name), name, item) for item, name in self.names.items()]
        exact = [item for text, _, item in folded if text == query]
        if exact:
            return exact[:1]
        words = query.split()
        near = [(len(name), name, item) for text, name, item in folded if all(w in text for w in words)]
        return [item for _, _, item in sorted(near)[:8]]

    def around(self, item: int, craft) -> dict:
        """Everything one step from an item."""
        out = {"item": self.names.get(item, f"item {item}")}
        if item in craft.prices:
            out["auction_price"] = {"lowest": coins(craft.prices[item][0]), "seen": craft.seen(item)}
        if craft.bags.get(item):
            out["in_bags"] = craft.bags[item]
        if craft.bank.get(item):
            out["in_bank"] = craft.bank[item]
        if item in self.used_in:
            out["used_in_your_recipes"] = [
                {"recipe_id": r["id"], "recipe": r["name"], "profession": r["profession"], "needs_each": count, "colour": r["colour"],
                 **({"makes": self.names.get(r["makes"], f"item {r['makes']}")} if r["makes"] else {}),
                 **({} if r["learned"] else {"learned": False})} for r, count in self.used_in[item]]
        if item in self.made_by:
            out["made_by_your_recipes"] = [
                {"recipe_id": r["id"], "recipe": r["name"], "profession": r["profession"], "colour": r["colour"],
                 "from": ", ".join(f"{count} {self.names.get(i, f'item {i}')}" for i, count in r["reagents"]),
                 **({} if r["learned"] else {"learned": False})} for r in self.made_by[item]]
        if item in self.quests:
            out["asked_for_by_quests"] = self.quests[item]
        if item in self.bosses:
            out["dropped_by_bosses"] = self.bosses[item]
        out.update(self.sources.get(item) or self._from_world(item))
        if ":" in out["item"]:   # a recipe item: which of the character's recipes it teaches
            taught = out["item"].split(":", 1)[1].strip().lower()
            for r in craft.recipes.values():
                if taught in (r["name"].lower(), self.names.get(r["makes"], "").lower() if r["makes"] else None):
                    out["teaches"] = {"recipe_id": r["id"], "recipe": r["name"], "profession": r["profession"],
                                      "already_learned": r["learned"]}
                    break
        # gathered: a herb's node carries the herb's own name, an ore's vein is the object Questie says holds it
        gather = self.gathering
        for name in dict.fromkeys([out["item"]] + [o["name"] for o in out.get("found_in", [])]):
            match = next((n for n in gather.nodes if n.lower() == name.lower()), None)
            if match:
                zones = gather.where(match)
                out.setdefault("gathered_from", []).append({
                    "node": match, "profession": gather.nodes[match]["kind"], "known_nodes": sum(z["known_nodes"] for z in zones),
                    "richest_zones": [f"{z['zone']} ({z['known_nodes']})" for z in zones[:3]]})
        return out
