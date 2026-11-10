"""Farming for gold, from what is measured rather than what is said.

Nothing in the game or its addons has drop rates for ordinary loot, so the
addon notes what the character really loots: per day, zone and source (a kind
of creature, a kind of node), how many were looted, what came out, the coins,
and the time spent looting. This module turns that, with auction and vendor
prices, into three views:

    spots       gold per hour, per zone the character has farmed
    sources     per creature or node: how often each item came, worth per loot
    gathering   what the known gathering nodes of each zone are worth

Worth is the lowest auction listing where there is one, else what a vendor
pays. It is what the loot is listed for, not what it sold for.
"""
from .crafting import _int, _list, _text, coins

ENOUGH = 600   # seconds of looting before a per-hour figure means anything


class Farming:
    def __init__(self, snapshot: dict, crafting, gathering):
        self.crafting, self.gathering = crafting, gathering
        self.vendor: dict[int, int] = {}
        for row in _list(snapshot.get("vendor")):
            if isinstance(row, list) and len(row) >= 2 and _int(row[0], 1, 10**9) and _int(row[1], 1, 10**10):
                self.vendor[row[0]] = row[1]
        taken = snapshot.get("takenAt")
        self.today = int(taken) // 86400 if isinstance(taken, (int, float)) else 0
        self.records = []   # one per day and zone
        for raw in (r for r in _list(snapshot.get("loot")) if isinstance(r, dict)):
            day, map_id = _int(raw.get("day"), 0, 10**6), _int(raw.get("map"), 1, 10**9)
            if day is None or map_id is None:
                continue
            sources = []
            for src in (s for s in _list(raw.get("sources")) if isinstance(s, dict)):
                key, looted = _text(src.get("key"), 20), _int(src.get("n"), 0, 10**7) or 0
                items = {}
                for row in _list(src.get("items")):
                    if isinstance(row, list) and len(row) >= 2 and _int(row[0], 1, 10**9) and _int(row[1], 1, 10**7):
                        items[row[0]] = items.get(row[0], 0) + row[1]
                        if len(row) >= 3:
                            crafting._name(row[0], row[2])
                if key[:1] in ("n", "o") and (looted or items):
                    sources.append({"key": key, "node": key[0] == "o", "looted": max(looted, 1), "items": items,
                                    "name": _text(src.get("name"), 80) or ("a node" if key[0] == "o" else "a creature") + f" ({key[1:]})"})
            self.records.append({"day": day, "map": map_id, "zone": _text(raw.get("zone"), 60) or f"map {map_id}",
                                 "secs": _int(raw.get("secs"), 0, 10**7) or 0, "copper": _int(raw.get("copper"), 0, 10**10) or 0,
                                 "sources": sources})

    def worth(self, item: int) -> int:
        """Copper for one of an item: its lowest auction listing, else what a vendor pays, else nothing."""
        if item in self.crafting.prices:
            return self.crafting.prices[item][0]
        return self.vendor.get(item, 0)

    def _within(self, days: int) -> list:
        return [r for r in self.records if r["day"] > self.today - days]

    def zone(self, query: str) -> list[tuple[int, str]]:
        query = query.strip().lower()
        zones = {r["map"]: r["zone"] for r in self.records}
        exact = [(m, z) for m, z in zones.items() if z.lower() == query]
        return exact[:1] or sorted((m, z) for m, z in zones.items() if query and query in z.lower())[:6]

    def spots(self, days: int) -> list[dict]:
        """Each zone farmed in the period: time looting, what came out, and per hour where there is enough time to say."""
        zones = {}
        for r in self._within(days):
            z = zones.setdefault(r["map"], {"zone": r["zone"], "secs": 0, "coins": 0, "loot": 0, "looted": 0, "days": set(), "items": {}})
            z["secs"] += r["secs"]
            z["coins"] += r["copper"]
            z["days"].add(r["day"])
            for src in r["sources"]:
                z["looted"] += src["looted"]
                for item, count in src["items"].items():
                    z["loot"] += count * self.worth(item)
                    z["items"][item] = z["items"].get(item, 0) + count
        out = []
        for z in zones.values():
            total = z["coins"] + z["loot"]
            best = sorted(z["items"].items(), key=lambda kv: -kv[1] * self.worth(kv[0]))[:4]
            entry = {"zone": z["zone"], "days_farmed": len(z["days"]), "time_looting": f"{z['secs'] // 3600}h {z['secs'] % 3600 // 60}m",
                     "looted": z["looted"], "coins": coins(z["coins"]), "loot_worth": coins(z["loot"]), "total": coins(total),
                     "main_loot": [f"{count} × {self.crafting.name(item)}" for item, count in best], "_per_hour": None}
            if z["secs"] >= ENOUGH:
                entry["_per_hour"] = total * 3600 / z["secs"]
                entry["per_hour"] = coins(entry["_per_hour"])
            else:
                entry["per_hour"] = "too little time to say"
            out.append(entry)
        return sorted(out, key=lambda e: (-(e["_per_hour"] or -1), e["zone"]))

    def sources(self, days: int, map_id: int | None = None) -> list[dict]:
        """Each kind of creature or node looted: how many, what each item's measured rate is, worth per loot."""
        merged = {}
        for r in self._within(days):
            if map_id is not None and r["map"] != map_id:
                continue
            for src in r["sources"]:
                m = merged.setdefault((src["key"], r["map"]), {"name": src["name"], "node": src["node"], "zone": r["zone"], "looted": 0, "items": {}})
                m["looted"] += src["looted"]
                for item, count in src["items"].items():
                    m["items"][item] = m["items"].get(item, 0) + count
        out = []
        for m in merged.values():
            each = sum(count * self.worth(item) for item, count in m["items"].items()) / m["looted"]
            drops = sorted(m["items"].items(), key=lambda kv: -kv[1] * self.worth(kv[0]))[:8]
            out.append({"source": m["name"], "kind": "node" if m["node"] else "creature", "zone": m["zone"], "looted": m["looted"],
                        "worth_per_loot": coins(each), "_each": each,
                        "drops": [{"item": self.crafting.name(item), "per_loot": round(count / m["looted"], 2),
                                   "worth_each": coins(self.worth(item)) if self.worth(item) else "no price"} for item, count in drops]})
        return sorted(out, key=lambda e: (-e["_each"], e["source"]))

    def yields(self) -> dict[str, dict]:
        """node name (lower case) -> {"gathers": n, "items": {item id: average per gather}}, from the player's own gathering."""
        seen = {}
        for r in self.records:
            for src in r["sources"]:
                if src["node"]:
                    y = seen.setdefault(src["name"].lower(), {"gathers": 0, "items": {}})
                    y["gathers"] += src["looted"]
                    for item, count in src["items"].items():
                        y["items"][item] = y["items"].get(item, 0) + count
        return {name: {"gathers": y["gathers"], "items": {i: c / y["gathers"] for i, c in y["items"].items()}} for name, y in seen.items()}

    def gathering_worth(self, map_id: int | None = None) -> tuple[list[dict], list[str]]:
        """The known nodes of each zone priced: what one gather is worth and what all the zone's
        nodes come to. What a node gives is the player's own measured average where they have
        gathered it, else one of the item that shares its name. Nodes with no price are named apart."""
        measured, by_name = self.yields(), {name.lower(): item for item, name in self.crafting.names.items()}
        ranked, unknown = [], set()
        for name, node in self.gathering.nodes.items():
            known = measured.get(name.lower())
            if known:
                each = sum(avg * self.worth(item) for item, avg in known["items"].items())
                basis = f"measured over {known['gathers']} of your gathers"
            else:
                each = self.worth(by_name[name.lower()]) if name.lower() in by_name else 0
                basis = "assumed one per node: not gathered by you yet"
            if not each:
                unknown.add(name)
                continue
            for zone_map, zone in node["zones"].items():
                if map_id is None or zone_map == map_id:
                    ranked.append({"node": name, "profession": node["kind"], "zone": zone["zone"], "known_nodes": len(zone["at"]),
                                   "worth_each": coins(each), "worth_all": coins(each * len(zone["at"])), "basis": basis,
                                   "_all": each * len(zone["at"])})
        return sorted(ranked, key=lambda e: (-e["_all"], e["node"], e["zone"])), sorted(unknown)
