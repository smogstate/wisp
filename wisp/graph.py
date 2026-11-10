"""The item graph: how things in the snapshot hang together through items.

An item is the hub. Around it: the recipes that use it and the ones that make
it, the quests that ask for it, the bosses that drop it, who sells it, what
drops it in the open world, and its price. The model asks for one item and
gets everything one step away, then follows whichever link matters.

Built in memory from the current snapshot each time it is read.
ponytail: not kept between snapshots; into the SQLite file beside the prices
once the addon hands over more items than the ones that matter today.
"""
from .crafting import _text, coins


def _list(value) -> list:
    return value if isinstance(value, list) else []


class ItemGraph:
    def __init__(self, game):
        craft, snapshot = game.crafting, game.snapshot
        self.gathering = game.gathering
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
                "sold_by": game._who(f"i{item}.vendor", entry.get("vendors"), f"sells {label}"),
                "dropped_by": game._who(f"i{item}.drop", entry.get("drops"), f"drops {label}"),
                "found_in": game._who(f"i{item}.object", entry.get("objects"), f"holds {label}"),
            }
            for key, total in (("dropped_by", entry.get("dropCount")), ("found_in", entry.get("objectCount"))):
                if isinstance(total, int) and total > len(found[key]):
                    found[key + "_in_all"] = total   # Questie knows more than the few listed here
            self.sources[item] = {k: v for k, v in found.items() if v}

    def find(self, query: str) -> list[int]:
        """Items by name: the exact one, else those containing the words, shortest name first."""
        query = query.strip().lower()
        if not query:
            return []
        exact = [item for item, name in self.names.items() if name.lower() == query]
        if exact:
            return exact[:1]
        near = [(len(name), name, item) for item, name in self.names.items() if query in name.lower()]
        return [item for _, _, item in sorted(near)[:8]]

    def around(self, item: int, craft) -> dict:
        """Everything one step from an item."""
        out = {"item": self.names.get(item, f"item {item}")}
        if item in craft.prices:
            out["auction_price"] = {"lowest": coins(craft.prices[item][0]), "seen": craft.seen(item)}
        if craft.bags.get(item):
            out["in_bags"] = craft.bags[item]
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
        out.update(self.sources.get(item, {}))
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
