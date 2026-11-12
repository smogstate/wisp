"""Gathering nodes from the GatherMate2 addon: where each herb, vein, pool and
chest is known to be. From that, in code: which zones have the most of
something, and a route through a zone's nodes.

A node is a place GatherMate2 has on record, found by the player or imported.
It says where something can grow, not that it is there now.
"""
import math

from .crafting import _text

STOPS = 20     # a route's waypoints at most: TomTom's arrow is no use with a hundred
MERGE = 2.5    # nodes closer than this, in map units of 0-100, are one stop


class Gathering:
    def __init__(self, snapshot: dict):
        record = snapshot.get("gather") if isinstance(snapshot.get("gather"), dict) else None
        self.installed = record is not None or bool((snapshot.get("addons") or {}).get("gathermate"))
        self.cut = bool(record and record.get("cut"))
        # name -> {"kind": profession, "zones": {map id: {"zone": name, "at": [(x, y), ...]}}}
        self.nodes: dict[str, dict] = {}
        for kind in (k for k in (record or {}).get("kinds") or [] if isinstance(k, dict)):
            for zone in (z for z in kind.get("zones") or [] if isinstance(z, dict)):
                map_id = zone.get("map")
                if not isinstance(map_id, int) or isinstance(map_id, bool) or map_id <= 0:
                    continue
                for node in (n for n in zone.get("nodes") or [] if isinstance(n, dict)):
                    name, flat = _text(node.get("name"), 80), node.get("at")
                    if not name or not isinstance(flat, list):
                        continue
                    points = [(flat[i] / 100, flat[i + 1] / 100) for i in range(0, len(flat) - 1, 2)
                              if all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 10000 for v in flat[i:i + 2])]
                    if points:
                        entry = self.nodes.setdefault(name, {"kind": _text(kind.get("kind"), 40), "zones": {}})
                        place = entry["zones"].setdefault(map_id, {"zone": _text(zone.get("zone"), 60) or f"map {map_id}", "at": []})
                        place["at"] += points
        self.total = sum(len(z["at"]) for n in self.nodes.values() for z in n["zones"].values())

    def find(self, query: str) -> list[str]:
        """Node names: the exact one, else those containing the words, shortest first."""
        from .questdb import pick
        return pick(query, [(name, name) for name in self.nodes])

    def zone(self, query: str) -> list[int]:
        """Map ids of the zones whose name fits, the exact one first."""
        from .questdb import pick
        names = {m: z["zone"] for n in self.nodes.values() for m, z in n["zones"].items()}
        return pick(query, names.items(), 6)

    def zone_name(self, map_id: int) -> str:
        return next(z["zone"] for n in self.nodes.values() for m, z in n["zones"].items() if m == map_id)

    def where(self, name: str) -> list[dict]:
        """The zones a node is known in, the richest first."""
        zones = self.nodes[name]["zones"]
        return [{"map": m, "zone": z["zone"], "known_nodes": len(z["at"])}
                for m, z in sorted(zones.items(), key=lambda kv: (-len(kv[1]["at"]), kv[1]["zone"]))]

    def in_zone(self, map_id: int) -> list[dict]:
        """What is known to be gatherable in a zone, the most plentiful first."""
        found = [{"node": name, "kind": n["kind"], "known_nodes": len(n["zones"][map_id]["at"])}
                 for name, n in self.nodes.items() if map_id in n["zones"]]
        return sorted(found, key=lambda f: (-f["known_nodes"], f["node"]))

    def route(self, name: str, map_id: int, start: tuple[float, float] | None = None) -> list[dict]:
        """A walk through a zone's nodes of one kind: nodes close together become one
        stop, the richest stops are kept, and each next stop is the nearest one left.
        ponytail: nearest-neighbour order, good enough to follow an arrow; a proper
        tour (2-opt) if routes turn out to double back"""
        points = self.nodes[name]["zones"][map_id]["at"]
        cells: dict[tuple[int, int], list] = {}
        for x, y in points:
            cells.setdefault((int(x // MERGE), int(y // MERGE)), []).append((x, y))
        stops = [{"x": round(sum(p[0] for p in ps) / len(ps), 1), "y": round(sum(p[1] for p in ps) / len(ps), 1), "nodes": len(ps)}
                 for ps in cells.values()]
        stops = sorted(stops, key=lambda s: (-s["nodes"], s["x"], s["y"]))[:STOPS]
        here = start or (stops[0]["x"], stops[0]["y"])
        ordered = []
        while stops:
            nearest = min(stops, key=lambda s: (math.hypot(s["x"] - here[0], s["y"] - here[1]), s["x"], s["y"]))
            stops.remove(nearest)
            ordered.append(nearest)
            here = (nearest["x"], nearest["y"])
        return ordered
