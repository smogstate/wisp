"""How the character died.

The addon keeps the last half minute of the game's combat log as far as it touches the character, and
files it when the character dies (addon/Wisp/Wisp.lua, "how the character died"). Here that record is
checked and summed up: who did the damage and with what, what held the character, what the character
did in answer. All sums are done here; the model words them and adds advice.

Another player is known by class and race only. The addon never writes a player's name down, so there is
none here to leak: a creature has its name, a player has "Rogue (Undead)".
"""
import time

from .i18n import t, tn

# the game's own tokens for classes and races, the same in every language; shown through t()
CLASSES = {"WARRIOR": "Warrior", "PALADIN": "Paladin", "HUNTER": "Hunter", "ROGUE": "Rogue", "PRIEST": "Priest", "SHAMAN": "Shaman",
           "MAGE": "Mage", "WARLOCK": "Warlock", "DRUID": "Druid", "DEATHKNIGHT": "Death Knight", "MONK": "Monk"}
RACES = {"Human": "Human", "Dwarf": "Dwarf", "NightElf": "Night Elf", "Gnome": "Gnome", "Orc": "Orc", "Scourge": "Undead",
         "Tauren": "Tauren", "Troll": "Troll", "BloodElf": "Blood Elf", "Draenei": "Draenei", "Goblin": "Goblin", "Worgen": "Worgen"}
KINDS = ("hit", "tick", "miss", "heal", "debuff", "debuff_off", "interrupted", "cast", "dealt")
EVENTS, TIMELINE, DEATHS = 160, 30, 8


def _number(value, low=0, high=10**7):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high else None


def _text(value, limit=50):
    return value.replace("|", "").strip()[:limit] if isinstance(value, str) and value.strip() else None


def before(e: dict) -> str:
    """Seconds before the death, as written on a timeline: -4.5, and 0 for the blow itself."""
    return f"-{e['before']:g}" if e["before"] else "0"


class Deaths:
    def __init__(self, snapshot: dict | None):
        self.records = []   # newest first
        for raw in reversed((snapshot or {}).get("deaths") or []) if isinstance((snapshot or {}).get("deaths"), list) else []:
            record = self._record(raw)
            if record:
                self.records.append(record)
        self.records = self.records[:DEATHS]

    @staticmethod
    def _record(raw) -> dict | None:
        if not isinstance(raw, dict) or not isinstance(raw.get("log"), list) or _number(raw.get("at"), 1, 2**40) is None:
            return None
        who = []
        for one in raw.get("who") if isinstance(raw.get("who"), list) else []:
            one = one if isinstance(one, dict) else {}
            who.append({"player": one.get("player") is True, "pet": one.get("pet") is True, "enemy": one.get("enemy") is True,
                        "class": one.get("class") if one.get("class") in CLASSES else None,
                        "race": one.get("race") if one.get("race") in RACES else None,
                        "level": _number(one.get("level"), 1, 100),
                        # only a creature has a name here; whatever claims to be a player's is not passed on
                        "name": None if one.get("player") is True or one.get("pet") is True else _text(one.get("name"), 40)})
        log = []
        for e in raw["log"][-EVENTS:]:
            if not (isinstance(e, list) and len(e) >= 7 and e[1] in KINDS and _number(e[0], 0, 120) is not None):
                continue
            by = e[2] if isinstance(e[2], int) and not isinstance(e[2], bool) and 1 <= e[2] <= len(who) else 0
            log.append({"before": float(e[0]), "kind": e[1], "who": by, "spell": _text(e[3]), "amount": _number(e[4]) or 0,
                        "extra": _text(e[5], 20), "health": _number(e[6])})
        if not log:
            return None
        log.sort(key=lambda e: -e["before"])   # in the order it happened: the death is at 0
        return {"at": int(raw["at"]), "zone": _text(raw.get("zone"), 60), "level": _number(raw.get("level"), 1, 100),
                "max_health": _number(raw.get("maxHealth"), 1), "who": who, "log": log}

    # -- words ---------------------------------------------------------------------------------

    @staticmethod
    def label(record: dict, index: int) -> str:
        if not index:   # the character itself, or nobody: a fall, fire
            return t("the world")
        one = record["who"][index - 1]
        if one["player"]:
            name = t(CLASSES[one["class"]]) if one["class"] else t("a player")
            if one["race"]:
                name += f" ({t(RACES[one['race']])})"
            return name + (" " + t("level {n}", n=one["level"]) if one["level"] else "")
        if one["pet"]:
            return t("a player's pet")
        return one["name"] or t("a creature")

    @staticmethod
    def _what(e: dict) -> str:
        spell = e["spell"]
        if e["kind"] in ("hit", "tick"):
            return (spell or t("melee")) + (" · " + t("critical") if e["extra"] == "crit" else "") + (" · " + t("over time") if e["kind"] == "tick" else "")
        if e["kind"] == "miss":
            return (spell or t("melee")) + " · " + t("missed") + (f" ({e['extra']})" if e["extra"] else "")
        if e["kind"] == "heal":
            return t("healing: {spell}", spell=spell or "?")
        if e["kind"] == "debuff":
            return t("on you: {spell}", spell=spell or "?")
        if e["kind"] == "debuff_off":
            return t("{spell} ends", spell=spell or "?")
        if e["kind"] == "interrupted":
            return t("your {spell} is interrupted", spell=spell or t("cast"))
        if e["kind"] == "cast":
            return t("you cast {spell}", spell=spell or "?")
        return t("you hit with {spell}", spell=spell or t("melee"))

    # -- sums ----------------------------------------------------------------------------------

    def summed(self, record: dict) -> dict:
        log, who = record["log"], record["who"]
        hostile = [e for e in log if e["kind"] in ("hit", "tick", "debuff", "miss", "interrupted") and e["who"]]
        by_players = any(who[e["who"] - 1]["enemy"] and (who[e["who"] - 1]["player"] or who[e["who"] - 1]["pet"]) for e in hostile)
        damage = [e for e in log if e["kind"] in ("hit", "tick")]
        taken = sum(e["amount"] for e in damage)
        sources, spells = {}, {}
        for e in damage:
            sources[e["who"]] = sources.get(e["who"], 0) + e["amount"]
            row = spells.setdefault((e["who"], e["spell"]), {"hits": 0, "damage": 0, "biggest": 0, "crits": 0})
            row["hits"] += 1
            row["damage"] += e["amount"]
            row["biggest"] = max(row["biggest"], e["amount"])
            row["crits"] += e["extra"] == "crit"
        held, open_ = [], {}
        for e in log:   # a debuff from when it lands until it ends, or until the death
            if e["kind"] == "debuff":
                open_[(e["who"], e["spell"])] = e["before"]
            elif e["kind"] == "debuff_off" and (e["who"], e["spell"]) in open_:
                held.append((e["who"], e["spell"], round(open_.pop((e["who"], e["spell"])) - e["before"], 1), False))
        held += [(by, spell, round(since, 1), True) for (by, spell), since in open_.items()]
        last = damage[-1] if damage else None
        return {"by_players": by_players, "taken": taken, "sources": sources, "spells": spells, "held": held, "last": last,
                "lasted": round(hostile[0]["before"], 1) if hostile else 0.0,
                "healed": sum(e["amount"] for e in log if e["kind"] == "heal"),
                "dealt": sum(e["amount"] for e in log if e["kind"] == "dealt"),
                "casts": [e for e in log if e["kind"] == "cast"]}

    def ago(self, record: dict) -> str:
        from .history import ago_words
        return t("{time} ago", time=ago_words(max(0, time.time() - record["at"]) / 3600))

    def killers(self, record: dict, summed: dict) -> str:
        order = sorted(summed["sources"], key=lambda i: -summed["sources"][i])
        return ", ".join(self.label(record, i) for i in order[:4]) or t("unknown")

    def doer(self, record: dict, e: dict) -> str:
        """Who a line of the fight belongs to: what the character did or did to itself is 'you'."""
        mine = e["kind"] in ("cast", "dealt") or (not e["who"] and e["kind"] in ("heal", "debuff", "debuff_off"))
        return t("you") if mine else self.label(record, e["who"])

    def row(self, number: int) -> dict:
        """One death in a line, for the list."""
        record = self.records[number - 1]
        s = self.summed(record)
        return {"death": number, "when": self.ago(record), "zone": record["zone"], "killed_by_a_player": s["by_players"],
                "by": self.killers(record, s), "damage_taken": s["taken"], "fight_lasted_seconds": s["lasted"]}

    def detail(self, number: int) -> dict:
        """One death in full, for the model: every figure already worked out."""
        record = self.records[number - 1]
        s, label = self.summed(record), lambda i: self.label(record, i)
        share = lambda n: f"{n / s['taken']:.0%}" if s["taken"] else "0%"
        out = {**self.row(number), "character_level": record["level"], "max_health": record["max_health"],
               "enemies": [{"who": label(i), "damage_to_you": n, "share": share(n)} for i, n in sorted(s["sources"].items(), key=lambda x: -x[1])][:6],
               "damage_by_spell": [{"spell": spell or "melee swing", "from": label(by), **row, "share": share(row["damage"])}
                                   for (by, spell), row in sorted(s["spells"].items(), key=lambda x: -x[1]["damage"])][:10],
               "killing_blow": {"spell": s["last"]["spell"] or "melee swing", "from": label(s["last"]["who"]), "damage": s["last"]["amount"]} if s["last"] else None,
               "held_by": [{"debuff": spell, "from": label(by), "seconds": seconds, **({"until_death": True} if to_death else {})}
                           for by, spell, seconds, to_death in s["held"]][:10],
               "healing_received": s["healed"], "damage_you_dealt": s["dealt"],
               "what_you_cast": [{"seconds_before_death": e["before"], "spell": e["spell"]} for e in s["casts"]][-15:],
               "timeline": [f"{before(e)}s  {self.doer(record, e)}: {self._what(e)}"
                            + (f" {e['amount']:g}" if e["amount"] else "") + (f"  (health {e['health']:g})" if e["health"] is not None else "")
                            for e in record["log"][-TIMELINE:]]}
        if len(record["log"]) > TIMELINE:
            out["earlier_events_left_out"] = len(record["log"]) - TIMELINE
        return out

    def card(self, number: int) -> dict:
        """The same death laid out for the player: the timeline as a table, the sums as notes."""
        record = self.records[number - 1]
        s = self.summed(record)
        # The window is narrow: what happened goes first, where a table lets text wrap, and the figures stay short.
        # With one enemy the title already says who; with several, each line names its own.
        several = len({e["who"] for e in record["log"] if e["who"]}) > 1
        said = lambda e: (self.label(record, e["who"]) + ": " if several and e["who"] else "") + self._what(e)
        rows = [[said(e), f"{before(e)} " + t("s"),
                 (("+" if e["kind"] == "heal" else "") + f"{e['amount']:g}") if e["amount"] else "–",
                 f"{e['health']:g}" if e["health"] is not None and e["kind"] in ("hit", "tick", "heal") else "–"]
                for e in record["log"][-TIMELINE:]]
        spells = sorted(s["spells"].items(), key=lambda x: -x[1]["damage"])[:5]
        notes = []
        if spells:
            notes.append(t("Damage taken: {total}", total=f"{s['taken']:g}") + " · "
                         + ", ".join(f"{spell or t('melee')} {row['damage']:g}" for (_, spell), row in spells))
        if s["held"]:
            notes.append(t("Held by: {list}", list=", ".join(f"{spell} ({seconds:g} " + t("s") + ")" for _, spell, seconds, _ in s["held"][:6])))
        notes.append(t("From the game's combat log, the last 30 seconds before the death. Other players are shown by class and race only."))
        return {"title": t("Killed by: {who}", who=self.killers(record, s)),
                "sub": " · ".join(x for x in (record["zone"], self.ago(record),
                                              tn(round(s["lasted"]), "the fight took {n} second", "the fight took {n} seconds")) if x),
                "columns": [t("What happened"), t("Before death"), t("Amount"), t("Health")], "rows": rows, "notes": notes}
