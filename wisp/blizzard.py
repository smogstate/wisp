"""Blizzard's own game data for WoW Forever, through the Battle.net API.

Undocumented so far: what is known comes from community reports (2026-11). The
namespaces are `static-forever-{region}` (items), `dynamic-forever-{region}`
(rulesets and auction houses) and `profile-forever-{region}`. Auction houses hang
under a ruleset: 15 of them, five each for Alliance, Horde and Blackwater, every
listing with item, buyout, quantity and time left.

The player brings a free developer client of their own (develop.battle.net), kept
in this PC's credential store as "client id:client secret" and sent only to
Blizzard. Nothing about the player or the character is sent.

Check setup tries the API from the player's PC, including the two things nobody
knows yet: which auction house is the player's, found by their own auctions in
its listings, and whether a listing's buyout is for one item or for the whole
stack. `Live` serves the model's live_auctions tool from the same data.
"""
import base64
import gzip
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import i18n
from .i18n import t

OAUTH = "https://oauth.battle.net/token"
API = "https://{region}.api.blizzard.com"
REGIONS = ("eu", "us", "kr", "tw")
KEY_NAME = "battlenet"   # its entry in the credential store


class BlizzardError(Exception):
    pass


class Blizzard:
    def __init__(self, client_id: str, secret: str, region: str = "eu", oauth: str = OAUTH, api: str | None = None):
        self.client_id, self.secret = client_id, secret
        self.region = region if region in REGIONS else "eu"
        self.oauth, self.api = oauth, api or API.format(region=self.region)
        self._token, self._expires = None, 0.0
        self.static = None    # the namespace game data answers under, once found: it may carry the data's version
        self.tried = []       # the ones that did not answer
        self.client = None    # the game's own version and build, a last guess at that version

    @classmethod
    def from_saved(cls, saved: str | None, region: str = "eu") -> "Blizzard | None":
        if not saved or ":" not in saved:
            return None
        client_id, secret = saved.split(":", 1)
        return cls(client_id.strip(), secret.strip(), region) if client_id.strip() and secret.strip() else None

    def _open(self, request: urllib.request.Request, timeout: float) -> bytes:
        request.add_header("Accept-Encoding", "gzip")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                body = reply.read(200_000_000)
                return gzip.decompress(body) if reply.headers.get("Content-Encoding") == "gzip" else body
        except urllib.error.HTTPError as e:
            raise BlizzardError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise BlizzardError(t("no connection: {why}", why=getattr(e, "reason", e))) from e

    def token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        basic = base64.b64encode(f"{self.client_id}:{self.secret}".encode()).decode()
        request = urllib.request.Request(self.oauth, data=b"grant_type=client_credentials", method="POST",
                                         headers={"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"})
        try:
            answer = json.loads(self._open(request, 20))
        except BlizzardError as e:
            raise BlizzardError(t("Blizzard did not accept the client: {why}", why=e)) from e
        if not isinstance(answer, dict) or not isinstance(answer.get("access_token"), str):
            raise BlizzardError(t("Blizzard sent no token"))
        self._token, self._expires = answer["access_token"], time.time() + float(answer.get("expires_in") or 3600)
        return self._token

    def _static_namespace(self) -> str:
        """Game data for Forever was first seen only under a namespace that names the data's version
        (static-1.60.1_70399-forever-eu), the plain one answering 403. Try the plain one, then any such name
        found in what the API itself links to, then the game client's own version and build."""
        if self.static:
            return self.static
        plain, self.tried = f"static-forever-{self.region}", []
        candidates = [plain]
        try:   # what the API links to from its ruleset pages
            index = self._get_ns("/data/wow/ruleset/index", f"dynamic-forever-{self.region}", 30)
            text = json.dumps(index)
            first = next((r.get("id") for r in index.get("rulesets") or [] if isinstance(r, dict) and isinstance(r.get("id"), int)), None)
            if first is not None:
                text += json.dumps(self._get_ns(f"/data/wow/ruleset/{first}", f"dynamic-forever-{self.region}", 30))
            candidates += re.findall(rf"static-[0-9A-Za-z._]+-forever-{self.region}", text)
        except BlizzardError:
            pass
        if self.client:
            candidates.append(f"static-{self.client}-forever-{self.region}")
        for namespace in dict.fromkeys(candidates):
            try:
                self._get_ns("/data/wow/playable-class/index", namespace, 30)   # small, and there in every version of the game
                self.static = namespace
                return namespace
            except BlizzardError as e:
                self.tried.append(f"{namespace}: {e}")
        raise BlizzardError(t("game data answers under none of: {tried}", tried="; ".join(self.tried)))

    def get(self, path: str, kind: str, timeout: float = 90) -> dict:
        """One API document. kind: static, dynamic or profile."""
        return self._get_ns(path, self._static_namespace() if kind == "static" else f"{kind}-forever-{self.region}", timeout)

    def _get_ns(self, path: str, namespace: str, timeout: float, more: dict | None = None) -> dict:
        # names in the player's language, as the game writes them
        query = urllib.parse.urlencode({"namespace": namespace, "locale": "ru_RU" if i18n.LANG == "ru" else "en_US", **(more or {})})
        request = urllib.request.Request(f"{self.api}{path}?{query}", headers={"Authorization": "Bearer " + self.token()})
        try:
            data = json.loads(self._open(request, timeout))
        except ValueError as e:
            raise BlizzardError(t("not JSON")) from e
        if not isinstance(data, dict):
            raise BlizzardError(t("not JSON"))
        return data


def _href_id(entry: dict):
    return entry.get("id") if isinstance(entry.get("id"), int) else None


def probe(api: Blizzard, snapshot: dict, remember=None) -> list[tuple[str, bool, str]]:
    """Check setup's lines for the API: (what, worked, detail). Stops at the first step that fails."""
    out = []
    try:
        api.token()
    except BlizzardError as e:
        return [(t("Blizzard API: the client"), False, str(e))]
    out.append((t("Blizzard API: the client"), True, t("accepted ({region})", region=api.region.upper())))
    try:
        rulesets = [r for r in api.get("/data/wow/ruleset/index", "dynamic").get("rulesets") or [] if isinstance(r, dict) and _href_id(r)]
        if not rulesets:
            raise BlizzardError(t("no rulesets listed"))
        out.append((t("Blizzard API: WoW Forever"), True, t("{n} rulesets: {names}", n=len(rulesets),
                                                             names=", ".join(str(r.get("name") or r["id"]) for r in rulesets[:6]))))
        index = api.get(f"/data/wow/ruleset/{rulesets[0]['id']}/auctions/", "dynamic")
        houses = [h for h in index.get("auctions") or [] if isinstance(h, dict) and _href_id(h)]
        if not houses:
            raise BlizzardError(t("no auction houses listed"))
        out.append((t("Blizzard API: auction houses"), True, ", ".join(f"{h['id']} {h.get('name') or ''}".strip() for h in houses)))
    except BlizzardError as e:
        return out + [(t("Blizzard API: WoW Forever"), False, str(e))]
    client = snapshot.get("client") if isinstance(snapshot.get("client"), dict) else {}
    if isinstance(client.get("version"), str) and client.get("build"):
        api.client = f"{client['version']}_{client['build']}"
    try:
        out.append((t("Blizzard API: game data"), True, t("answers as {namespace}", namespace=api._static_namespace())))
        static = True
    except BlizzardError as e:   # no items, professions or recipes without it; the auction house does not need it
        out.append((t("Blizzard API: game data"), False, str(e)))
        static = False
    if static:
        try:
            item = api.get("/data/wow/item/4306", "static")
            out.append((t("Blizzard API: items"), True, t("item 4306 is {name}; an item has: {fields}", name=item.get("name") or "?",
                                                          fields=", ".join(sorted(item))[:300])))
        except BlizzardError as e:   # many items are missing from Blizzard's data: not the end of the check
            out.append((t("Blizzard API: items"), False, str(e)))
        out.extend(recipes_probe(api))
        out.extend(names_probe(api))

    if not own_auctions(snapshot):
        try:
            auctions, goods = listings(api, rulesets[0]["id"], houses[0]["id"])
            out.append(_listed_line(houses[0], auctions, goods))
        except BlizzardError as e:
            out.append((t("Blizzard API: listings"), False, t("house {house}: {why}", house=houses[0]["id"], why=e)))
            return out
        out.append((t("Blizzard API: your auction house"), False,
                    t("not known yet: put an auction up, look at the Auctions tab, log out, then check again")))
        return out
    try:
        found = find_house(api, snapshot, rulesets[0]["id"], houses, seen=lambda house, auctions, goods: any(line[0] == t("Blizzard API: listings") for line in out)
                           or out.append(_listed_line(house, auctions, goods)))
    except BlizzardError as e:
        out.append((t("Blizzard API: listings"), False, str(e)))
        return out
    if not found:
        out.append((t("Blizzard API: your auction house"), False, t("your auctions are in none of the {n} houses", n=len(houses))))
        return out
    out.append((t("Blizzard API: your auction house"), True,
                t("house {house} {name}: {found} of your {mine} auctions are in it", house=found["house"], name=found["name"],
                  found=found["found"], mine=found["mine"])))
    if found["compared"]:
        per_item, per_stack = found["per_item"], found["per_stack"]
        out.append((t("Blizzard API: price per item or per stack"), per_item + per_stack > 0,
                    t("the API's buyout is the game's price for one ({n} auctions agree)", n=per_item) if per_item >= per_stack and per_item
                    else t("the API's buyout is for the whole stack ({n} auctions agree)", n=per_stack)
                    if per_stack else t("neither: the prices differ from the game's")))
    if remember:
        remember(found)
    return out


def recipes_probe(api: Blizzard) -> list[tuple[str, bool, str]]:
    """Does Blizzard publish every profession's recipes with their ingredients for WoW Forever, as it does for
    other versions? One profession followed all the way down: its tiers, a tier's recipes, one recipe."""
    try:
        professions = [p for p in api.get("/data/wow/profession/index", "static").get("professions") or [] if isinstance(p, dict) and _href_id(p)]
        if not professions:
            raise BlizzardError(t("no professions listed"))
    except BlizzardError as e:
        return [(t("Blizzard API: professions"), False, str(e))]
    lines = [(t("Blizzard API: professions"), True, t("{n}: {names}", n=len(professions),
                                                      names=", ".join(str(p.get("name") or p["id"]) for p in professions[:16])))]
    try:
        pick = next((p for p in professions if "cook" in str(p.get("name") or "").lower()), professions[0])
        detail = api.get(f"/data/wow/profession/{pick['id']}", "static")
        tiers = [x for x in detail.get("skill_tiers") or [] if isinstance(x, dict) and _href_id(x)]
        if not tiers:
            raise BlizzardError(t("{profession} has no skill tiers; it has: {fields}", profession=pick.get("name") or pick["id"],
                                  fields=", ".join(sorted(detail))))
        tier = api.get(f"/data/wow/profession/{pick['id']}/skill-tier/{tiers[0]['id']}", "static")
        recipes = [r for c in tier.get("categories") or [] if isinstance(c, dict) for r in c.get("recipes") or []
                   if isinstance(r, dict) and _href_id(r)]
        if not recipes:
            raise BlizzardError(t("{profession} lists no recipes; its tier has: {fields}", profession=pick.get("name") or pick["id"],
                                  fields=", ".join(sorted(tier))))
        recipe = api.get(f"/data/wow/recipe/{recipes[0]['id']}", "static")
        needs = [f"{r.get('quantity')} × {(r.get('reagent') or {}).get('name') or (r.get('reagent') or {}).get('id')}"
                 for r in recipe.get("reagents") or [] if isinstance(r, dict)]
        lines.append((t("Blizzard API: recipes"), bool(needs),
                      t("{profession}: {tiers} tiers, {n} recipes in the first; {recipe} needs {needs}; a recipe has: {fields}",
                        profession=pick.get("name") or pick["id"], tiers=len(tiers), n=len(recipes), recipe=recipe.get("name") or recipes[0]["id"],
                        needs=", ".join(needs) or "?", fields=", ".join(sorted(recipe)))))
        # the skill a recipe needs: nobody knows yet whether Blizzard gives it, or where. Say what looks like it.
        skill = sorted({f"{where}.{key}" for where, doc in (("profession", detail), ("tier", tier), ("recipe", recipe))
                        for key in doc if re.search(r"skill|rank|level|difficult|trivial|learn|source", key, re.I)})
        lines.append((t("Blizzard API: skill a recipe needs"), bool(skill),
                      t("fields that may hold it: {fields}", fields=", ".join(skill)) if skill else t("no such field; a tier has: {fields}",
                                                                                                   fields=", ".join(sorted(tier)))))
    except BlizzardError as e:
        lines.append((t("Blizzard API: recipes"), False, str(e)))
    return lines


def names_probe(api: Blizzard) -> list[tuple[str, bool, str]]:
    """Whether Blizzard hands out whole lists with names in every language: a page of items, a page of
    creatures. With them every item and NPC could be named as the player's game names it."""
    found, any_list = [], False
    for kind in ("item", "creature"):
        try:
            page = api._get_ns(f"/data/wow/search/{kind}", api._static_namespace(), 60, {"orderby": "id", "_pageSize": 5, "_page": 1})
            first = next((r.get("data") for r in page.get("results") or [] if isinstance(r, dict) and isinstance(r.get("data"), dict)), {})
            name = first.get("name")
            tongues = ", ".join(sorted(name)) if isinstance(name, dict) else "one language only"
            sample = name.get("ru_RU" if i18n.LANG == "ru" else "en_US") if isinstance(name, dict) else name
            found.append(f"{kind}: {page.get('pageCount', '?')} pages of {page.get('pageSize', '?')} (up to {page.get('maxPageSize', '?')}), "
                         f"names in {tongues}, e.g. {first.get('id')} = {sample}")
            any_list = True
        except BlizzardError as e:
            found.append(f"{kind}: {e}")
    return [(t("Blizzard API: lists of names"), any_list, "; ".join(found))]


def _listed_line(house: dict, auctions: list, goods: list):
    fields = sorted({k for a in auctions[:200] for k in a})
    return (t("Blizzard API: listings"), True, t("house {house}: {n} auctions, {goods} trade goods; fields: {fields}", house=house["id"],
                                                 n=len(auctions), goods=len(goods), fields=", ".join(fields)))


def auction_houses(api: Blizzard) -> tuple[int, list[dict]]:
    """(a ruleset's id, its auction houses). Every ruleset lists the same houses."""
    rulesets = [r for r in api.get("/data/wow/ruleset/index", "dynamic").get("rulesets") or [] if isinstance(r, dict) and _href_id(r)]
    if not rulesets:
        raise BlizzardError(t("no rulesets listed"))
    index = api.get(f"/data/wow/ruleset/{rulesets[0]['id']}/auctions/", "dynamic")
    houses = [h for h in index.get("auctions") or [] if isinstance(h, dict) and _href_id(h)]
    if not houses:
        raise BlizzardError(t("no auction houses listed"))
    return rulesets[0]["id"], houses


def listings(api: Blizzard, ruleset: int, house: int) -> tuple[list[dict], list[dict]]:
    """One house: (auctions, trade goods). Trade goods are listed apart, priced for one."""
    base = f"/data/wow/ruleset/{ruleset}/auctions/{house}"
    auctions = [a for a in api.get(base, "dynamic").get("auctions") or [] if isinstance(a, dict)]
    try:
        goods = [a for a in api.get(base + "/commodities", "dynamic").get("auctions") or [] if isinstance(a, dict)]
    except BlizzardError:
        goods = []
    return auctions, goods


def own_auctions(snapshot: dict) -> list[dict]:
    return [a for a in ((snapshot.get("ownAuctions") or {}).get("list") or []) if isinstance(a, dict) and isinstance(a.get("item"), int)
            and isinstance(a.get("quantity"), int) and isinstance(a.get("buyout"), int) and a["buyout"] > 0 and not a.get("sold")]


def find_house(api: Blizzard, snapshot: dict, ruleset: int, houses: list[dict], seen=None) -> dict | None:
    """Which auction house is the player's: the one holding most of their own auctions, as the game
    last showed them. Also how the API's buyout compares with the game's price: for one, or for the stack."""
    mine = own_auctions(snapshot)
    if not mine:
        return None
    faction = str((snapshot.get("character") or {}).get("faction") or "").lower()
    for house in sorted(houses, key=lambda h: faction not in str(h.get("name") or "").lower()):   # the character's faction first
        auctions, goods = listings(api, ruleset, house["id"])
        if seen:
            seen(house, auctions, goods)

        def same(listing, a):
            return isinstance(listing.get("item"), dict) and listing["item"].get("id") == a["item"] and listing.get("quantity") == a["quantity"]
        found, as_goods = [], 0
        for a in mine:
            listing = next((x for x in auctions if same(x, a) and isinstance(x.get("buyout"), int)), None)
            if listing:
                found.append((a, listing["buyout"]))
            elif any(same(x, a) for x in goods):
                as_goods += 1
        if (len(found) + as_goods) * 2 >= len(mine):
            return {"ruleset": ruleset, "house": house["id"], "name": str(house.get("name") or ""), "found": len(found) + as_goods,
                    "mine": len(mine), "compared": len(found), "per_item": sum(1 for a, buyout in found if buyout == a["buyout"]),
                    "per_stack": sum(1 for a, buyout in found if buyout == a["buyout"] * a["quantity"] and a["quantity"] > 1)}
    return None


class Live:
    """The player's auction house as Blizzard publishes it, for the model's tool: every listing of an
    item, without a scan. Fetched when asked and kept for most of an hour, which is how often Blizzard
    refreshes it. Which house is the player's is found once, by their own auctions, and remembered."""
    FRESH = 50 * 60

    def __init__(self, client, remembered: dict | None = None, remember=lambda where: None):
        self.client, self.remember = client, remember     # client() -> a Blizzard, or None when the player has not set one up
        self.where = remembered if isinstance(remembered, dict) and isinstance(remembered.get("house"), int) else None
        self.at, self.by_item = 0.0, {}

    def found(self, where: dict):
        """Check setup, or a first lookup, has found the player's house."""
        # Blizzard's other WoW versions give a buyout for the whole auction: taken so unless the game's own prices say otherwise
        self.where = {"ruleset": where["ruleset"], "house": where["house"], "name": where["name"],
                      "each": where["per_item"] > where["per_stack"]}
        self.at = 0.0
        self.remember(self.where)

    def item(self, item: int, snapshot: dict) -> dict:
        api = self.client()
        if api is None:
            return {"error": "not_set_up"}
        try:
            if self.where is None:
                ruleset, houses = auction_houses(api)
                found = find_house(api, snapshot, ruleset, houses)
                if not found:
                    return {"error": "house_unknown"}
                self.found(found)
            if time.time() - self.at > self.FRESH:
                auctions, goods = listings(api, self.where["ruleset"], self.where["house"])
                by_item = {}
                for a in auctions:
                    qty = a.get("quantity") if isinstance(a.get("quantity"), int) and a["quantity"] > 0 else 1
                    if isinstance(a.get("item"), dict) and isinstance(a.get("buyout"), int) and a["buyout"] > 0:
                        each = a["buyout"] if self.where["each"] else a["buyout"] // qty
                        by_item.setdefault(a["item"].get("id"), []).append((each, qty))
                for a in goods:
                    if isinstance(a.get("item"), dict) and isinstance(a.get("unit_price"), int) and isinstance(a.get("quantity"), int):
                        by_item.setdefault(a["item"].get("id"), []).append((a["unit_price"], a["quantity"]))
                self.by_item, self.at = by_item, time.time()
        except BlizzardError as e:
            return {"error": str(e)}
        rows = sorted(self.by_item.get(item, []))
        steps = {}
        for each, qty in rows:
            steps[each] = steps.get(each, 0) + qty
        return {"house": self.where["name"] or self.where["house"], "minutes_old": int((time.time() - self.at) / 60),
                "auctions": len(rows), "listed": sum(qty for _, qty in rows), "steps": sorted(steps.items())}
