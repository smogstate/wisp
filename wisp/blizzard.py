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
import time
import urllib.error
import urllib.parse
import urllib.request

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

    def get(self, path: str, kind: str, timeout: float = 90) -> dict:
        """One API document. kind: static, dynamic or profile."""
        query = urllib.parse.urlencode({"namespace": f"{kind}-forever-{self.region}", "locale": "en_US"})
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
    try:
        item = api.get("/data/wow/item/4306", "static")
        out.append((t("Blizzard API: items"), True, t("item 4306 is {name}", name=item.get("name") or "?")))
    except BlizzardError as e:
        out.append((t("Blizzard API: items"), False, str(e)))

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
