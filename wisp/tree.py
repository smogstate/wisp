"""Walking the item graph further than one step.

    breakdown   down: what a product is made of, through every recipe on the
                way, to the raw materials: how many of each, what they cost,
                which crafts in which order, who can do them
    uses        up: everything an item goes into, directly and through what
                is made from it, with how many of it one end product takes

Recipes are every recipe in the game where the addon could read them
(WispRecipes), the player's own and their alts' first. All the arithmetic is
here; the model only words it.
"""
import math

from .crafting import coins
from .i18n import t, tn

DEPTH = 8       # a product, its parts, their parts...: deep enough for anything craftable
PRODUCTS = 25   # end products named for one item: the model is handed a slice of the graph, never the graph
DIRECT = 25     # and as many of the recipes that use it directly
STEPS = 30      # crafts and
MATERIALS = 40  # materials named for one product


def _rank(recipe: dict) -> tuple:
    """Which recipe to follow when several make the same item: one the player has, then an alt's, then any."""
    return (recipe["learned"] and not recipe["owner"], recipe["learned"], not recipe.get("world"), -len(recipe["reagents"]))


def _who(recipe: dict) -> str:
    if recipe["learned"]:
        return recipe["owner"] or "this character"
    if recipe.get("world"):
        return f"none of your characters has learned it: needs {recipe['profession']}"
    return "not learned yet" + (f" ({recipe['owner']})" if recipe["owner"] else "")


def breakdown(game, item: int, quantity: int) -> dict:
    graph, craft = game.graph, game.crafting
    raw, steps, dearer, taken = {}, [], [], {}
    places = craft.stock()                                  # bags, bank, the shared bank, then each other character
    pool = [dict(held) for _, _, held in places]            # used up as the walk goes: nothing is counted twice

    def name(some: int) -> str:
        return graph.names.get(some, f"item {some}")

    def cost_of(some: int):   # what one costs to buy: the lowest listing, else unknown
        return craft.prices[some][0] if some in craft.prices else None

    def from_stock(some: int, need: int) -> int:
        """Take what is already held, nearest first. Returns how many are still needed."""
        for at, held in enumerate(pool):
            if need and held.get(some):
                use = min(need, held[some])
                held[some] -= use
                need -= use
                taken.setdefault(some, {})[at] = taken.get(some, {}).get(at, 0) + use
        return need

    def walk(some: int, need: int, path: tuple):
        """Returns what the part still to be got costs at auction, or None when a price is missing."""
        if path:   # what is already held of a part needs neither making nor buying; the product itself is what was asked for
            need = from_stock(some, need)
            if not need:
                return 0
        options = [r for r in graph.made_by.get(some, []) if r["reagents"]]
        if not options or some in path or len(path) >= DEPTH:
            raw[some] = raw.get(some, 0) + need
            each = cost_of(some)
            return each * need if each is not None else None
        recipe = max(options, key=_rank)
        crafts = math.ceil(need / recipe["makes_count"])
        total, known = 0, True
        for part, count in recipe["reagents"]:
            part_cost = walk(part, crafts * count, path + (some,))
            if part_cost is None:
                known = False
            else:
                total += part_cost
        if recipe["id"] in crafted:   # the same part wanted in two places of the tree: one line, the crafts added up
            crafted[recipe["id"]]["crafts"] += crafts
        else:
            crafted[recipe["id"]] = {"recipe": recipe, "item": some, "crafts": crafts}
        bought = cost_of(some)
        if path and known and bought is not None and bought * need < total:   # an intermediate cheaper to buy than to make
            dearer.append({"item": name(some), "need": need, "buying_costs": coins(bought * need), "making_costs": coins(total)})
        return total if known else None

    crafted = {}   # recipe id -> the recipe, what it makes, how many crafts in all; in the order they are to be done
    walk(item, quantity, ())
    for done in crafted.values():
        recipe, crafts = done["recipe"], done["crafts"]
        steps.append({"craft": f"{crafts} × {recipe['name']}", "makes": f"{crafts * recipe['makes_count']} × {name(done['item'])}",
                      "profession": recipe["profession"], "who_can": _who(recipe),
                      **({"turns_grey_at_skill": recipe["grey_at"]} if recipe["grey_at"] else {}),
                      # for the card: what these crafts take; a part that is itself crafted opens its own card
                      "_parts": [{"text": f"{crafts * count} × {name(part)}", "item": name(part),
                                  "link": any(r["reagents"] for r in graph.made_by.get(part, []))} for part, count in recipe["reagents"]]})
    if not steps:
        return {"error": "no recipe"}
    rows, total, unpriced, buy = [], 0, [], []
    for some in sorted(set(raw) | set(taken), key=lambda i: (-(cost_of(i) or 0) * raw.get(i, 0), name(i))):
        get, each = raw.get(some, 0), cost_of(some)
        held = taken.get(some, {})
        world = graph.world.get(some) or {}
        row = {"item": name(some), "need": get + sum(held.values()),
               "already_held": [{"where": places[at][0], "count": n} for at, n in sorted(held.items())],
               "to_buy": get, "each": coins(each) if each is not None else None,
               "cost_to_buy": coins(each * get) if each is not None and get else None,
               "_held": sum(held.values()), "_where": ", ".join(f"{n} — {places[at][1]}" for at, n in sorted(held.items()) if at > 0)}   # all but the bags: to be fetched
        if not row["already_held"]:
            del row["already_held"]
        if get and world.get("v"):
            row["sold_by_a_vendor"] = True
        if get:
            buy.append((some, name(some), get))
            if each is None:
                unpriced.append(row["item"])
            else:
                total += each * get
        rows.append(row)
    product = name(item)
    out = {"product": product, "quantity": quantity,
           "crafts_in_order": [{k: v for k, v in s.items() if k[0] != "_"} for s in steps[-STEPS:]],   # the last ones lead to the product
           "professions_needed": sorted({s["profession"] for s in steps}),
           "materials": [{k: v for k, v in r.items() if k[0] != "_" and v is not None} for r in rows[:MATERIALS]],
           "to_buy_in_all": coins(total) if not unpriced else None}
    if len(steps) > STEPS:
        out["earlier_crafts_left_out"] = len(steps) - STEPS
    if len(rows) > MATERIALS:
        out["cheaper_materials_left_out"] = len(rows) - MATERIALS   # the totals count them all the same
    if unpriced:
        out["no_auction_price_for"] = unpriced[:15]
        if total:
            out["cost_of_the_priced_ones"] = coins(total)
    listed = cost_of(item)
    if listed is not None:
        out["product_listed_for"] = coins(listed * quantity)
    # What the materials are worth whether held or bought: a held material is not free, it could be sold instead.
    # Worked out here so that the profit is never the model's own sum.
    worths = [cost_of(some) for some in set(raw) | set(taken)]
    if None not in worths:
        worth = sum(cost_of(some) * (raw.get(some, 0) + sum(taken.get(some, {}).values())) for some in set(raw) | set(taken))
        out["all_materials_worth"] = coins(worth)
        if listed is not None:
            gain = listed * quantity - worth
            out["profit_over_materials"] = ("-" if gain < 0 else "+") + coins(abs(gain))
    if dearer:
        out["cheaper_to_buy_than_to_make"] = dearer[:10]
    elsewhere = [f"{r['item']}: {r['_where']}" for r in rows if r["_where"]]
    out["_buy"] = buy
    out["_card"] = {
        "title": t("To craft {n} × {item}", n=quantity, item=product),
        "sub": (t("nothing to buy: you hold it all") if not buy else
                t("to buy: about {sum}", sum=coins(total)) if not unpriced else
                t("to buy: {sum} for what has a price", sum=coins(total)) if total else
                tn(len(buy), "to buy: {n} item, price unknown", "to buy: {n} items, prices unknown"))
               + " · " + tn(len(steps), "{n} kind of craft", "{n} kinds of craft"),
        "columns": [t("Item"), t("Need"), t("Have"), t("Buy"), t("Cost")],
        "rows": [[r["item"], r["need"], r["_held"] or "–", r["to_buy"] or "–", r["cost_to_buy"] or "–"] for r in rows[:25]],
        "steps": [{"text": s["craft"], "parts": s["_parts"],
                   "detail": s["profession"] + (" · " + t("turns grey at skill {n}", n=s["turns_grey_at_skill"]) if s.get("turns_grey_at_skill") else "")}
                  for s in steps[-20:]],
        "notes": ([t("Held elsewhere: {where}", where="; ".join(elsewhere[:10]))] if elsewhere else [])
                 + [t("Lowest listings from your own scans. What you and your other characters held at each one's last logout.")],
    }
    return out


def uses(game, item: int) -> dict:
    graph, craft = game.graph, game.crafting
    name = graph.names.get(item, f"item {item}")
    direct = []
    for recipe, count in graph.used_in.get(item, []):
        entry = {"recipe": recipe["name"], "profession": recipe["profession"], "needs_each": count, "who_can": _who(recipe)}
        if recipe["makes"]:
            entry["makes"] = f"{recipe['makes_count']} × {graph.names.get(recipe['makes'], recipe['name'])}"
            if recipe["makes"] in craft.prices:
                entry["product_listed_for"] = coins(craft.prices[recipe["makes"]][0] * recipe["makes_count"])
        costs = [craft.prices.get(part, (None,))[0] for part, _ in recipe["reagents"]]
        if all(c is not None for c in costs):
            entry["all_materials_cost"] = coins(sum(c * n for c, (_, n) in zip(costs, recipe["reagents"])))
        direct.append(entry)

    # upward: every product this item ends up in, and how many of the item one of that product takes
    reached, frontier = {}, [(item, 1.0, (name,))]
    for _ in range(DEPTH):
        following = []
        for some, per_one, path in frontier:
            for recipe, count in graph.used_in.get(some, []):
                product = recipe["makes"]
                if not product or product == item or product in reached:
                    continue
                reached[product] = {"per_one": per_one * count / recipe["makes_count"], "path": path + (graph.names.get(product, recipe["name"]),),
                                    "recipe": recipe}
                following.append((product, reached[product]["per_one"], reached[product]["path"]))
        frontier = following
        if not frontier or len(reached) > 400:
            break
    products = []
    for product, info in reached.items():
        price = craft.prices.get(product, (None,))[0]
        products.append({"product": info["path"][-1], "through": " → ".join(info["path"]), "takes_of_this_item": round(info["per_one"], 2),
                         "profession": info["recipe"]["profession"], "who_can": _who(info["recipe"]),
                         "listed_for": coins(price) if price is not None else None, "_price": price or 0, "_steps": len(info["path"])})
    products.sort(key=lambda p: (-p["_price"], p["_steps"], p["product"]))
    world = graph.world.get(item) or {}
    price = craft.prices.get(item, (None,))[0]
    out = {"item": name,
           "used_directly_in": len(direct), "ends_up_in": len(products),
           "professions": sorted({d["profession"] for d in direct} | {p["profession"] for p in products}),
           "where_it_comes_from": {k: v for k, v in {"vendors_selling_it": len(world.get("v") or []), "creatures_dropping_it": world.get("dn"),
                                                     "objects_holding_it": world.get("on"),
                                                     "made_by_recipes": len(graph.made_by.get(item, []))}.items() if v},
           "listed_for": coins(price) if price is not None else None,
           "direct_recipes": direct[:DIRECT],
           "end_products": [{k: v for k, v in p.items() if k[0] != "_" and v is not None} for p in products[:PRODUCTS]]}
    held = craft.bags.get(item, 0) + craft.bank.get(item, 0)
    if held:
        out["you_hold"] = held
    if len(direct) > DIRECT:
        out["direct_recipes_left_out"] = len(direct) - DIRECT
    if len(products) > PRODUCTS:
        out["end_products_left_out"] = len(products) - PRODUCTS
    out["_card"] = {
        "title": t("What {item} goes into", item=name),
        "sub": tn(len(direct), "{n} recipe uses it directly", "{n} recipes use it directly") + " · "
               + tn(len(products), "{n} product in all", "{n} products in all"),
        "columns": [t("Product"), t("Profession"), t("Takes"), t("Listed for")],
        "rows": [[p["product"], p["profession"], p["takes_of_this_item"], p["listed_for"] or "–"] for p in products[:20]],
        "links": [p["product"] for p in products[:20]],   # each product opens its own card: what it is made of
        "notes": [t("Takes: how many {item} one of the product needs in all, through every craft on the way.", item=name)],
    }
    return out
