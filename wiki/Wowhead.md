# Wowhead tool

> Status: draft 2026-11-09. Structures and functions are a proposal; the code blocks have not been run. Endpoint facts marked **verified** were probed live that day.

Gives the AI game data as validated structures instead of web pages. Part of the Python tool set ([Tools](Tools.md)).

## What Wowhead actually offers

Wowhead has no official data API. These are the endpoints its own tooltip widget and site use.

| Endpoint | Returns | Status |
|---|---|---|
| `nether.wowhead.com/tooltip/{type}/{id}?dataEnv=16&locale={n}` | JSON: `name`, `quality`, `icon`, `tooltip` (HTML with machine markers), and for NPCs and objects `map: {zone, coords}` | **verified**, works without a browser |
| `www.wowhead.com/forever/search/suggestions-template?q=…` | JSON: `search`, `results` (top 10, news mixed in) and `categories.database` (entities only, up to 20) | **verified in Chrome** 2026-11-09. Answered 403 (CloudFront) to a plain request from the dev machine; not yet tried from Python on the PC |
| `www.wowhead.com/forever/{type}={id}` pages | HTML with embedded data tables (`Listview`), map data (`g_mapperData`) and comments | **verified in Chrome**: pages exist and carry the tables; the table contents are not yet parsed |
| `www.wowhead.com/forever/guides/…` | Guides, including builds with talent import strings | unverified, known from a third-party description only |

Verified details of the search endpoint:

- **The `/forever/` prefix scopes search to Forever.** Without it the same URL searches retail: the quest *Rise, Thunderfury!* is level 30 there and level 60 under `/forever/`, and Forever-only entries appear (spell `468157`).
- **Use `categories.database`, not `results`.** `results` is the top 10 by popularity and includes news posts; `categories.database` holds only game entities.
- **Each entry:** `type` (number), `id`, `name`, `typeName`, and optionally `icon`, `quality`, `side`, `pinFooterText` ("One-Handed Sword", "Level: 60"), `pinDescription`, `pinBreadcrumb` (["Weapon", "One-Handed Sword"]).
- **Type codes seen:** `1` NPC, `2` object, `3` item, `5` quest, `6` spell, `10` achievement, `162` news post.
- **Nothing found is not an error:** an unknown word or a one-letter query answers HTTP 200 with empty lists.

Verified about the pages (existence only):

- An NPC page that drops loot has a `drops` table; an item page has `reward-from-q`; NPC and quest pages carry map data; every page has `comments`.

Verified details of the tooltip endpoint:

- **`dataEnv=16` is WoW Forever.** Thunderfury there is item level 80, against 40 in the default data set.
- **An unknown `dataEnv` silently returns retail data.** `dataEnv=99` gave the retail item with HTTP 200. The tool must never send a value it does not know.
- **Types that work:** `item`, `spell`, `quest`, `npc`, `object`, `zone`, `achievement`. `itemset` and `faction` answer "Entity type is invalid".
- **Locales:** `0` English, `7` Russian, `8` Portuguese. A third-party table that lists 8 as Russian is wrong.
- **Errors are JSON with HTTP 404:** `ID is out of range`, `Entity not found`, `Entity type is invalid`.
- **Item tooltips carry markers** that make parsing reliable: `<!--ilvl-->80`, `<!--dmg-->44 - 115`, `<!--spd-->1.90`, `<!--stat3-->+5 Agility`, `<!--rlvl-->60`, quality as class `q5`, effects as links to `/forever/spell={id}`, money as `moneygold` / `moneysilver` / `moneycopper` spans.
- **NPCs come with coordinates:** Auctioneer Fitch returns `zone: 1519` and four points. Instance bosses return an empty list.

## Functions

Two for the first version; they answer "what is this" and "where is this".

| Function | Purpose | Needs |
|---|---|---|
| `wowhead_search(query, kinds?, limit?)` | Turn a name into ids. Always the first step: the model must not guess ids | search endpoint (verified in Chrome) |
| `wowhead_get(kind, id)` | One entity as a structure: item, spell, quest, NPC, object or zone | tooltip endpoint (verified) |

Later, each needing page parsing and so the unverified `www` host:

| Function | Purpose |
|---|---|
| `wowhead_sources(item_id)` | Where an item comes from: dropped by, sold by, quest reward, crafted |
| `wowhead_loot(npc_id)` | What an NPC drops or sells |
| `wowhead_quest(quest_id)` | Full quest: giver, turn-in, rewards, prerequisites, chain |
| `wowhead_guides(class, spec)` / `wowhead_guide(url)` | Guide list, and one guide with its builds and talent import strings |
| `wowhead_comments(kind, id, limit?)` | Top-rated comments, often the most practical tip on the page |
| `wowhead_news(limit?)` | Forever news feed: patches and hotfixes |

The game version and the language are set by Wisp's configuration, not by the model: the model cannot ask for another data set.

## Structures

Pydantic models: one definition gives the validation and the JSON Schema the model needs for the tool. Pydantic is already a dependency of the Anthropic SDK.

### Shared pieces

```python
from datetime import datetime
from enum import Enum, IntEnum
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class Env(IntEnum):
    FOREVER = 16          # verified 2026-11-09. Add others only after probing them.


class Locale(IntEnum):
    EN = 0                # verified
    RU = 7                # verified


class Kind(str, Enum):
    ITEM = "item"
    SPELL = "spell"
    QUEST = "quest"
    NPC = "npc"
    OBJECT = "object"
    ZONE = "zone"


Id = Annotated[int, Field(gt=0, lt=2**31)]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Text = Annotated[str, Field(max_length=4000)]
Level = Annotated[int, Field(ge=1, le=63)]        # cap is 60; bosses run a few above
Percent = Annotated[float, Field(ge=0, le=100)]
Copper = Annotated[int, Field(ge=0)]


class Model(BaseModel):
    # unknown fields are an error, values are immutable, strings are trimmed
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Source(Model):
    """Where a result came from, so the model can cite it and the UI can link it."""
    url: HttpUrl
    env: Env
    locale: Locale
    fetched_at: datetime
    cached: bool

    @field_validator("url")
    @classmethod
    def on_wowhead(cls, v):
        if not (v.host or "").endswith("wowhead.com"):
            raise ValueError("not a wowhead.com url")
        return v


class Ref(Model):
    """A pointer to another entity, for lists."""
    kind: Kind
    id: Id
    name: Name


class Entity(Model):
    id: Id
    name: Name
    source: Source
    raw_text: Text                 # the tooltip as plain text: nothing is lost if parsing misses a field
    warnings: list[str] = Field(default_factory=list, max_length=20)   # fields the parser could not read
```

### Entities

```python
class Quality(IntEnum):
    POOR = 0
    COMMON = 1
    UNCOMMON = 2
    RARE = 3
    EPIC = 4
    LEGENDARY = 5
    ARTIFACT = 6


class Stat(Model):
    name: Annotated[str, Field(min_length=1, max_length=40)]   # "Agility", "Fire Resistance"
    value: Annotated[int, Field(ge=-10_000, le=10_000)]


class Effect(Model):
    trigger: Literal["equip", "use", "chance_on_hit", "other"]
    text: Text
    spell_id: Id | None = None


class Item(Entity):
    kind: Literal[Kind.ITEM] = Kind.ITEM
    quality: Quality
    icon: str | None = None
    item_level: Annotated[int, Field(ge=1, le=1000)] | None = None
    required_level: Level | None = None
    binding: Literal["pickup", "equip", "use", "quest"] | None = None
    unique: bool = False
    slot: str | None = None            # "One-Hand", "Chest"
    item_type: str | None = None       # "Sword", "Cloth"
    damage_min: Annotated[int, Field(ge=0)] | None = None
    damage_max: Annotated[int, Field(ge=0)] | None = None
    speed: Annotated[float, Field(gt=0, le=10)] | None = None
    dps: Annotated[float, Field(ge=0)] | None = None
    armor: Annotated[int, Field(ge=0)] | None = None
    stats: list[Stat] = Field(default_factory=list, max_length=20)
    effects: list[Effect] = Field(default_factory=list, max_length=10)
    durability: Annotated[int, Field(ge=1)] | None = None
    max_stack: Annotated[int, Field(ge=1)] | None = None
    sell_price: Copper | None = None

    @model_validator(mode="after")
    def damage_is_a_range(self):
        lo, hi = self.damage_min, self.damage_max
        if (lo is None) != (hi is None) or (lo is not None and lo > hi):
            raise ValueError("damage_min and damage_max must come together, min <= max")
        return self


class Spell(Entity):
    kind: Literal[Kind.SPELL] = Kind.SPELL
    icon: str | None = None
    cost: str | None = None            # "30 Mana" — units vary, kept as text
    range: str | None = None           # "35 yd range"
    cast_time: str | None = None       # "1.5 sec cast", "Instant"
    cooldown: str | None = None
    requires_class: str | None = None
    requires_level: Level | None = None
    description: Text
    buff: Text | None = None           # what the aura tooltip says, if any


class Requirement(Model):
    name: Name
    count: Annotated[int, Field(ge=1, le=10_000)] = 1


class Quest(Entity):
    kind: Literal[Kind.QUEST] = Kind.QUEST
    level: Level | None = None
    description: Text
    requirements: list[Requirement] = Field(default_factory=list, max_length=20)


class Point(Model):
    x: Percent
    y: Percent


class Location(Model):
    zone_id: Id                        # Wowhead's AREA id, not TomTom's uiMapID — see Open
    zone_name: Name | None = None
    floor: int = 0
    points: list[Point] = Field(max_length=50)
    primary: Point | None = None       # the point Wisp would send as a waypoint

    @model_validator(mode="after")
    def primary_is_one_of_points(self):
        if self.primary is not None and self.primary not in self.points:
            raise ValueError("primary must be one of points")
        return self


class Npc(Entity):
    kind: Literal[Kind.NPC] = Kind.NPC
    level_min: Level | None = None     # None for bosses shown as "??"
    level_max: Level | None = None
    creature_type: str | None = None   # "Humanoid", "Elemental"
    classification: Literal["normal", "elite", "rare", "rare_elite", "boss"] | None = None
    location: Location | None = None   # None inside instances

    @model_validator(mode="after")
    def levels_are_a_range(self):
        lo, hi = self.level_min, self.level_max
        if (lo is None) != (hi is None) or (lo is not None and lo > hi):
            raise ValueError("level_min and level_max must come together, min <= max")
        return self


class GameObject(Entity):
    kind: Literal[Kind.OBJECT] = Kind.OBJECT
    object_type: str | None = None     # "Container", "Mining Node"
    location: Location | None = None


class Zone(Entity):
    kind: Literal[Kind.ZONE] = Kind.ZONE
    level_min: Level | None = None
    level_max: Level | None = None
    zone_type: str | None = None       # "City", "Dungeon"
    territory: Literal["alliance", "horde", "contested", "sanctuary"] | None = None


AnyEntity = Annotated[Item | Spell | Quest | Npc | GameObject | Zone, Field(discriminator="kind")]
```

### Search and errors

```python
TYPE_CODES = {1: Kind.NPC, 2: Kind.OBJECT, 3: Kind.ITEM, 5: Kind.QUEST, 6: Kind.SPELL}   # verified 2026-11-09
# 10 (achievement) and 162 (news post) are dropped: not entities wowhead_get can fetch.


class SearchHit(Model):
    kind: Kind                         # from the numeric `type`, through TYPE_CODES
    id: Id
    name: Name
    quality: Quality | None = None     # items only
    icon: str | None = None
    detail: Annotated[str, Field(max_length=80)] | None = None      # pinFooterText: "One-Handed Sword", "Level: 60"
    category: list[Annotated[str, Field(max_length=60)]] = Field(default_factory=list, max_length=5)   # pinBreadcrumb
    summary: Annotated[str, Field(max_length=300)] | None = None    # pinDescription, HTML stripped
    url: HttpUrl                       # built by Wisp: /forever/{kind}={id}


class SearchResult(Model):
    query: str
    hits: list[SearchHit] = Field(max_length=20)   # from categories.database; empty = nothing found, not an error
    skipped: int = 0                               # entries of a type Wisp does not handle
    source: Source


class ToolError(Model):
    code: Literal["not_found", "invalid_input", "blocked", "rate_limited", "parse_failed", "unavailable"]
    message: Annotated[str, Field(max_length=300)]
    retryable: bool
```

### Function inputs

```python
class SearchInput(Model):
    query: Annotated[str, Field(min_length=2, max_length=80)]     # the endpoint needs 2+ characters
    kinds: set[Kind] | None = None
    limit: Annotated[int, Field(ge=1, le=20)] = 10


class GetInput(Model):
    kind: Kind
    id: Id
```

`wowhead_search` returns `SearchResult | ToolError`; `wowhead_get` returns `AnyEntity | ToolError`.

### Later functions (sketch)

```python
class Drop(Model):
    npc: Ref
    chance: Percent | None = None
    location: Location | None = None

class Vendor(Model):
    npc: Ref
    price: Copper | None = None
    location: Location | None = None

class ItemSources(Model):
    item: Ref
    dropped_by: list[Drop] = Field(default_factory=list, max_length=30)
    sold_by: list[Vendor] = Field(default_factory=list, max_length=30)
    quest_rewards: list[Ref] = Field(default_factory=list, max_length=30)
    crafted_by: list[Ref] = Field(default_factory=list, max_length=30)
    source: Source

class Build(Model):
    name: Name
    import_string: Annotated[str, Field(min_length=1, max_length=2000, pattern=r"^[A-Za-z0-9+/=_\-]+$")]

class Guide(Model):
    title: Name
    updated_at: datetime | None = None
    builds: list[Build] = Field(default_factory=list, max_length=20)
    text: Annotated[str, Field(max_length=20_000)]
    source: Source

class Comment(Model):
    author: Name
    rating: int
    posted_at: datetime | None = None
    text: Text
```

## Validation rules

What the models enforce, and what the tool does around them.

**On what the model asks for**
- `kind` is one of the six known types; `id` is a positive 32-bit integer; `query` is 2–80 characters; `limit` is 1–20.
- The model cannot choose the game version or language.

**On what Wowhead answers**
- HTTP 404 with a JSON `error` becomes `ToolError(not_found | invalid_input)`. HTTP 403 becomes `blocked`. Anything else unexpected becomes `unavailable`.
- **Data-set check.** Links inside a Forever tooltip point to `/forever/…`. If a tooltip's links point elsewhere, Wowhead fell back to another data set: the result is rejected, not returned.
- All text is stripped of HTML, of WoW `|` escape codes and of control characters, then length-capped.
- Every list has a maximum length, so one result cannot flood the model's context.
- Unknown fields are an error (`extra="forbid"`), so a change in Wowhead's format shows up as a failure and not as silently wrong data.

**On parsing**
- `id`, `name` and `source` must parse, or the call fails with `parse_failed`.
- Any other field that cannot be read is `None` and is named in `warnings`.
- `raw_text` always carries the whole tooltip as plain text, so a missed field never hides information from the model.
- Cross-field checks: damage and level ranges come in pairs with min ≤ max; a location's `primary` is one of its `points`; coordinates are 0–100.

**On trust**
- Wowhead text, comments above all, is data. Nothing in it is treated as an instruction, and nothing from it reaches the game except through an action card the player copies ([Protocol](Protocol.md)).

**On being a polite client**
- Results are cached on disk; game data only changes with patches.
- At most one request a second, and a hard cap per question.
- On `blocked` or `rate_limited` the tool stops and says so. It does not retry around the block.

## Open

- **Does `www.wowhead.com` answer Python, not only a browser?** Search and the pages work in Chrome. A plain request from the dev machine got 403 from CloudFront, so a non-browser client may be refused on the PC too. The first thing to run there is one plain request from Python. If it is refused, the options are a web search limited to `wowhead.com`, another Forever database, or fetching through the overlay's own embedded browser view.
- **Wowhead's terms on automated access have not been read.** To check before building anything beyond the tooltip endpoint.
- **Zone ids.** Wowhead gives area ids (`1519` Stormwind); TomTom wants uiMapIDs. The waypoint command should carry the area id or zone name and let the addon resolve it — this changes the `tomtom.waypoint` arguments in [Protocol](Protocol.md).
- **Which point is `primary`** when an NPC has many spawns: the densest cluster, or the one nearest the player (needs the player's position, which Wisp only has from a screenshot).
- **German locale id**, and whether Forever data exists in every locale.
- **Stats text is localized.** `"+5 Agility"` is `"+5 к ловкости"` in Russian; `Stat.name` keeps the localized text. If the model needs stable keys, the marker number (`stat3`) is the language-independent source.
