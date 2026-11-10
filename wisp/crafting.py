"""Professions: the recipes the addon read from the open profession window, what
is in the bags, and Auctionator's prices. All the arithmetic of a crafting plan
is done here, in code: the model chooses what to craft and never adds anything up.
"""
import re

COLOURS = ("orange", "yellow", "green", "grey")


def coins(copper) -> str:
    """Copper as the game writes money: 12g 40s, 85s, 7c."""
    copper = int(round(copper))
    gold, silver, rest = copper // 10000, copper % 10000 // 100, copper % 100
    return " ".join([f"{gold}g"] * bool(gold) + [f"{silver}s"] * bool(silver) + [f"{rest}c"] * bool(rest and not gold)) or "0c"


def _text(value, limit: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f|;]", "", str(value or ""))[:limit].strip()


def _int(value, low: int, high: int) -> int | None:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value == int(value) and low <= value <= high
    return int(value) if ok else None


def _list(value) -> list:
    return value if isinstance(value, list) else []


class Crafting:
    def __init__(self, snapshot: dict):
        self.has_auctionator = bool((snapshot.get("addons") or {}).get("auctionator"))
        self.names: dict[int, str] = {}                 # item id -> the game's name for it
        self.bags: dict[int, int] = {}                  # item id -> how many are carried
        self.bound: set[int] = set()                    # carried or banked items that are bound: not for sale
        self.bank: dict[int, int] = {}                  # item id -> how many are in the bank, as it was when last open
        self.bank_seen = None                           # when that was
        self.prices: dict[int, tuple[int, int | None]] = {}   # item id -> (copper, days since seen)
        self.recipes: dict[int, dict] = {}              # recipe id -> recipe
        self.books: list[dict] = []                     # one per profession whose window was opened

        for line in _list(snapshot.get("bags")):
            item, count = _int(line.get("id"), 1, 10**9) if isinstance(line, dict) else None, None
            if item is not None:
                count = _int(line.get("count"), 1, 10**7)
            if count:
                self.bags[item] = self.bags.get(item, 0) + count
                self._name(item, line.get("name"))
                if line.get("bound"):
                    self.bound.add(item)
        banked = snapshot.get("bank") if isinstance(snapshot.get("bank"), dict) else {}
        if isinstance(banked.get("seenAt"), (int, float)):
            self.bank_seen = banked["seenAt"]
        for line in _list(banked.get("items")):
            item = _int(line.get("id"), 1, 10**9) if isinstance(line, dict) else None
            count = _int(line.get("count"), 1, 10**7) if item is not None else None
            if count:
                self.bank[item] = self.bank.get(item, 0) + count
                self._name(item, line.get("name"))
                if line.get("bound"):
                    self.bound.add(item)
        for row in _list(snapshot.get("prices")):   # rows of [item id, copper, days since seen or -1, name or ""]
            if not isinstance(row, list) or len(row) < 4:
                continue
            item, price = _int(row[0], 1, 10**9), _int(row[1], 1, 10**10)
            if item is not None and price:
                self.prices[item] = (price, _int(row[2], 0, 10000))
                self._name(item, row[3])
        skill_now = {p.get("name"): _int(p.get("skill"), 0, 10000) for p in _list(snapshot.get("professions"))
                     if isinstance(p, dict)}
        for book in _list(snapshot.get("recipes")):
            if not isinstance(book, dict) or not _text(book.get("profession"), 40):
                continue
            name = _text(book["profession"], 40)
            read_at = _int(book.get("skill"), 0, 10000)
            entry = {"profession": name, "skill": skill_now.get(name) or read_at, "max": _int(book.get("max"), 0, 10000),
                     "seen_at": book.get("seenAt") if isinstance(book.get("seenAt"), (int, float)) else None, "recipes": []}
            for raw in _list(book.get("recipes")):
                recipe = self._recipe(raw, entry) if isinstance(raw, dict) else None
                if recipe:
                    entry["recipes"].append(recipe)
                    self.recipes[recipe["id"]] = recipe
            self.books.append(entry)

    def _name(self, item: int, name) -> None:
        if _text(name, 80):
            self.names.setdefault(item, _text(name, 80))

    def name(self, item: int) -> str:
        return self.names.get(item) or f"item {item}"

    def _recipe(self, raw: dict, book: dict) -> dict | None:
        recipe_id, name = _int(raw.get("id"), 1, 10**9), _text(raw.get("name"), 80)
        if recipe_id is None or not name:
            return None
        reagents = []
        for m in _list(raw.get("reagents")):
            item = _int(m.get("id"), 1, 10**9) if isinstance(m, dict) else None
            count = _int(m.get("count"), 1, 10000) if item is not None else None
            if count:
                reagents.append((item, count))
                self._name(item, m.get("name"))
        makes = _int(raw.get("makes"), 1, 10**9)
        if makes is not None:
            self._name(makes, raw.get("makesName") or name)
        colour = raw.get("colour") if raw.get("colour") in COLOURS else None
        grey_at, read_at = _int(raw.get("greyAt"), 1, 10000), _int(raw.get("skill"), 0, 10000)
        skill = book["skill"]
        stale = None
        if grey_at and skill is not None and skill >= grey_at:
            colour = "grey"   # certain whatever the window showed: past this skill it gives nothing
        elif colour and read_at is not None and skill is not None and skill > read_at:
            stale = read_at   # the colour was read at a lower skill and may have dropped since
        return {"id": recipe_id, "name": name, "profession": book["profession"], "learned": raw.get("learned") is not False,
                "colour": colour, "colour_read_at_skill": stale, "grey_at": grey_at,
                "makes": makes, "makes_count": _int(raw.get("makesMin"), 1, 10000) or 1, "reagents": reagents}

    # -- what the tools return ------------------------------------------------

    def _price(self, item: int) -> int | None:
        return self.prices[item][0] if item in self.prices else None

    def seen(self, item: int) -> str:
        """When Auctionator last saw the item, in words."""
        days = self.prices[item][1]
        return "over three weeks ago" if days is None else "today" if days == 0 else f"{days} day{'s' if days != 1 else ''} ago"

    def find(self, query: str) -> list[int]:
        """Priced items by name: the exact one, else those containing the words, shortest name first."""
        query = _text(query, 80).lower()
        if not query:
            return []
        named = [(self.names[item], item) for item in self.prices if item in self.names]
        exact = [item for name, item in named if name.lower() == query]
        if exact:
            return exact[:1]
        return [item for name, item in sorted((n for n in named if query in n[0].lower()), key=lambda n: (len(n[0]), n[0]))][:8]

    def describe(self, recipe: dict) -> dict:
        materials, cost, unpriced, can_make = [], 0, [], None
        for item, count in recipe["reagents"]:
            price, have = self._price(item), self.bags.get(item, 0)
            materials.append({"item": self.name(item), "per_craft": count, "in_bags": have,
                              **({"in_bank": self.bank[item]} if self.bank.get(item) else {}),
                              "price_each": coins(price) if price else None})
            if price:
                cost += price * count
            else:
                unpriced.append(self.name(item))
            can_make = have // count if can_make is None else min(can_make, have // count)
        out = {"recipe_id": recipe["id"], "name": recipe["name"], "colour": recipe["colour"], "turns_grey_at": recipe["grey_at"],
               "makes": f"{recipe['makes_count']} × {self.name(recipe['makes'])}" if recipe["makes"] else None,
               "materials": materials, "can_make_from_bags": can_make or 0}
        if recipe["colour_read_at_skill"] is not None:
            out["colour_read_at_skill"] = recipe["colour_read_at_skill"]
        if not recipe["learned"]:
            out["learned"] = False
        if recipe["reagents"]:
            out["materials_cost_per_craft"] = coins(cost) if not unpriced else None
            if unpriced:
                out["no_auction_price_for"] = unpriced
        sells = self._price(recipe["makes"]) if recipe["makes"] else None
        if sells:   # what the result is listed for, and what is left after buying every material
            out["result_listed_for"] = coins(sells * recipe["makes_count"])
            if recipe["reagents"] and not unpriced:
                profit = sells * recipe["makes_count"] - cost
                out["profit_per_craft"] = ("-" if profit < 0 else "") + coins(abs(profit))
        return out

    def plan(self, crafts) -> dict:
        """Totals for crafting each recipe a number of times: what is needed, what
        the plan itself makes along the way, what the bags cover, what to buy."""
        if not isinstance(crafts, list) or not 1 <= len(crafts) <= 30:
            return {"error": "crafts must be a list of 1 to 30 {recipe_id, times}"}
        steps, need, made, shown, professions = [], {}, {}, [], {}
        for craft in crafts:
            recipe = self.recipes.get(craft.get("recipe_id")) if isinstance(craft, dict) else None
            times = _int(craft.get("times"), 1, 5000) if isinstance(craft, dict) else None
            if recipe is None or times is None:
                return {"error": "every craft needs a recipe_id exactly as the recipes tool gave it, and times from 1 to 5000"}
            step = {"recipe": recipe["name"], "times": times, "colour": recipe["colour"]}
            if recipe["makes"]:
                made[recipe["makes"]] = made.get(recipe["makes"], 0) + times * recipe["makes_count"]
                step["makes"] = f"{times * recipe['makes_count']} × {self.name(recipe['makes'])}"
            skill = next((b["skill"] for b in self.books if b["profession"] == recipe["profession"]), None)
            if recipe["colour"] == "grey":
                step["warning"] = "grey: crafting it gives no skill"
            elif recipe["grey_at"] and skill is not None and times > recipe["grey_at"] - skill:
                step["warning"] = (f"turns grey at skill {recipe['grey_at']}: at most the first "
                                   f"{recipe['grey_at'] - skill} crafts can give skill")
            if not recipe["learned"]:
                step["not_learned"] = True
            for item, count in recipe["reagents"]:
                need[item] = need.get(item, 0) + times * count
            steps.append(step)
            professions[recipe["profession"]] = skill
            # the same step as the player sees it on the plan card
            shown.append({"text": f"{times} × {recipe['name']}", "colour": recipe["colour"],
                          "each": ", ".join(f"{count} {self.name(item)}" for item, count in recipe["reagents"]),
                          "warning": step.get("warning") or ("not learned yet" if not recipe["learned"] else "")})
        materials, total, unpriced, oldest, buy, rows = [], 0, [], 0, [], []
        for item, wanted in need.items():
            from_plan = min(made.get(item, 0), wanted)
            from_bags = min(self.bags.get(item, 0), wanted - from_plan)
            from_bank = min(self.bank.get(item, 0), wanted - from_plan - from_bags)
            to_buy = wanted - from_plan - from_bags - from_bank
            line = {"item": self.name(item), "needed": wanted, "made_in_this_plan": from_plan, "from_bags": from_bags,
                    **({"from_bank": from_bank} if from_bank else {}), "to_buy": to_buy}
            if to_buy:
                buy.append((item, to_buy))
                price = self._price(item)
                if price:
                    line["price_each"], line["cost"] = coins(price), coins(price * to_buy)
                    total += price * to_buy
                    oldest = max(oldest, self.prices[item][1] or 0)
                else:
                    unpriced.append(self.name(item))
            materials.append(line)
            if wanted > from_plan:   # what the plan makes for itself is a step, not something to gather
                rows.append({"item": self.name(item), "need": wanted - from_plan, "bags": from_bags, "bank": from_bank, "buy": to_buy,
                             "cost": line.get("cost", ""), "unpriced": bool(to_buy) and "cost" not in line})
        card = {"sub": " · ".join(f"{name}, skill {skill}" if skill is not None else name for name, skill in professions.items()),
                "steps": shown, "materials": rows,
                "total": ("Nothing to buy" if not buy else
                          (f"To buy: about {coins(total)}" if total else "To buy")
                          + (f" · {len(unpriced)} without an auction price" if unpriced else "")),
                "notes": [("As of your last logout; the bank as it was when you last opened it."
                           if self.bank_seen else "As of your last logout; the bank is not known: open it once and Wisp counts it.")
                          + (" Only orange gives a point on every craft: the rest may take more."
                             if any(step["colour"] != "orange" for step in shown) else "")]}
        out = {"steps": steps, "materials": materials, "to_buy_cost": coins(total), "_buy": buy, "_card": card,
               "note": "Counted in code from the recipes and the bags as of the last logout, and the bank as it was when last "
                       "opened (not at all if it never was). "
                       "Only orange recipes give a skill point on every craft, so for yellow and green ones the number "
                       "of crafts needed is an estimate: say so."}
        if unpriced:
            out["no_auction_price_for"] = unpriced
            out["to_buy_cost"] += " plus the items with no auction price (a vendor may sell them)"
        if total:
            out["prices"] = ("from the player's own Auctionator scans, "
                             + ("seen today" if not oldest else f"some last seen {oldest} days ago"))
        elif buy and not self.has_auctionator:
            out["prices"] = "none: the Auctionator addon was not installed at the last logout"
        return out
