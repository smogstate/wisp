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
PRODUCTS = 30   # end products named for one item


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
    raw, steps, dearer = {}, [], []

    def cost_of(some: int):   # what one costs to buy: the lowest listing, else unknown
        return craft.prices[some][0] if some in craft.prices else None

    def walk(some: int, need: int, path: tuple):
        """Returns what `need` of this cost when made from raw materials at auction prices, or None when a price is missing."""
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
        steps.append({"craft": f"{crafts} × {recipe['name']}", "makes": f"{crafts * recipe['makes_count']} × {graph.names.get(some, f'item {some}')}",
                      "profession": recipe["profession"], "who_can": _who(recipe), "_item": some, "_crafts": crafts})
        bought = cost_of(some)
        if path and known and bought is not None and bought * need < total:   # an intermediate cheaper to buy than to make
            dearer.append({"item": graph.names.get(some, f"item {some}"), "need": need, "buying_costs": coins(bought * need),
                           "making_costs": coins(total)})
        return total if known else None

    made_cost = walk(item, quantity, ())
    if not steps:
        return {"error": "no recipe"}
    rows, total, unpriced = [], 0, []
    for some, need in sorted(raw.items(), key=lambda kv: (-(cost_of(kv[0]) or 0) * kv[1], graph.names.get(kv[0], ""))):
        have = craft.bags.get(some, 0) + craft.bank.get(some, 0)
        each, world = cost_of(some), graph.world.get(some) or {}
        row = {"item": graph.names.get(some, f"item {some}"), "need": need, "have": have, "to_get": max(0, need - have),
               "each": coins(each) if each is not None else None, "cost_of_all": coins(each * need) if each is not None else None,
               "_cost": (each or 0) * need}
        if world.get("v"):
            row["sold_by_a_vendor"] = True
        elif world.get("dn") or world.get("on"):
            row["dropped_or_found"] = True
        if each is None:
            unpriced.append(row["item"])
        else:
            total += each * need
        rows.append(row)
    name = graph.names.get(item, f"item {item}")
    out = {"product": name, "quantity": quantity,
           "crafts_in_order": [{k: v for k, v in s.items() if k[0] != "_"} for s in steps],
           "professions_needed": sorted({s["profession"] for s in steps}),
           "raw_materials": [{k: v for k, v in r.items() if k[0] != "_" and v is not None} for r in rows],
           "raw_materials_cost": coins(total) if not unpriced else None}
    if unpriced:
        out["no_auction_price_for"] = unpriced
        out["cost_of_the_priced_ones"] = coins(total)
    listed = cost_of(item)
    if listed is not None:
        out["product_listed_for"] = coins(listed * quantity)
        if not unpriced:
            margin = listed * quantity - total
            out["left_after_materials"] = ("-" if margin < 0 else "") + coins(abs(margin))
    if dearer:
        out["cheaper_to_buy_than_to_make"] = dearer
    out["_card"] = {
        "title": t("Raw materials for {n} × {item}", n=quantity, item=name),
        "sub": (t("about {sum} at auction", sum=coins(total)) if not unpriced else t("{sum} for what has a price", sum=coins(total)))
               + " · " + tn(len(steps), "{n} kind of craft", "{n} kinds of craft"),
        "columns": [t("Item"), t("Need"), t("Have"), t("Each"), t("Cost")],
        "rows": [[r["item"], r["need"], r["have"] or "–", r["each"] or "–", r["cost_of_all"] or "–"] for r in rows[:25]],
        "notes": [t("In this order: {steps}", steps=" → ".join(s["craft"] for s in steps[:12])),
                  t("Lowest listings from your own scans. Bags and bank as of your last logout.")],
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
           "direct_recipes": direct[:40],
           "end_products": [{k: v for k, v in p.items() if k[0] != "_" and v is not None} for p in products[:PRODUCTS]]}
    held = craft.bags.get(item, 0) + craft.bank.get(item, 0)
    if held:
        out["you_hold"] = held
    if len(products) > PRODUCTS:
        out["end_products_left_out"] = len(products) - PRODUCTS
    out["_card"] = {
        "title": t("What {item} goes into", item=name),
        "sub": tn(len(direct), "{n} recipe uses it directly", "{n} recipes use it directly") + " · "
               + tn(len(products), "{n} product in all", "{n} products in all"),
        "columns": [t("Product"), t("Profession"), t("Takes"), t("Listed for")],
        "rows": [[p["product"], p["profession"], p["takes_of_this_item"], p["listed_for"] or "–"] for p in products[:20]],
        "notes": [t("Takes: how many {item} one of the product needs in all, through every craft on the way.", item=name)],
    }
    return out
