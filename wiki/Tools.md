# Tools

> Status: draft 2026-11-09. Requirement from Denis: the Python layer has a set of tools, and tools return structures, not web pages.

What the model can call from inside the overlay. Two kinds, and the difference matters.

## Knowledge tools — run in Python, result returns to the model

The model calls them, Python runs them, the model reads the result and can call again. This is ordinary tool use.

| Tool | Returns | Page |
|---|---|---|
| `wowhead_search`, `wowhead_get`, … | Items, spells, quests, NPCs with coordinates, as validated structures | [Wowhead](Wowhead.md) |
| `dungeons`, `dungeon` | **Built.** Every dungeon, and one in full: quests with this character's status and next step, bosses, loot, entrance | [Integrations](Integrations.md) |
| `recipes` | **Built.** The recipes of the character's professions as the game listed them: colour, grey point, what each makes, materials with the count in bags and the auction price | [Integrations](Integrations.md) |
| `auction_prices` | **Built.** The auction price of any item Auctionator has scanned, by name: lowest price seen, when, earlier prices Wisp has kept, the worth of the player's own stock | [Integrations](Integrations.md) |
| `craft_plan` | **Built.** The arithmetic of a plan, in code: recipes × times → materials needed, made along the way, covered by the bags, left to buy, and the cost | [Integrations](Integrations.md) |
| `character` | The saved character snapshot: class, spec, level, gear, quest log, addon state. With a date, the snapshot from then | [Architecture](Architecture.md) |
| `changes` | **Built.** What changed between two snapshots, computed in code: level, money, quests finished, picked up and dropped, profession skill, zone. For the last session, or looking back a number of days | [Architecture](Architecture.md) |
| `screenshot` | What is on screen right now (proposed) | [Architecture](Architecture.md) |
| `web_search` | Internet search as a list of results: title, link, site, snippet, date. For what no structured tool covers: guides on other sites, forum answers, news | below |
| `web_read` | One web page as clean text, capped, with its link. HTML never reaches the model | below |

## Game commands — shown as cards, pasted by the player, no result

They come from the addon's manifest ([Protocol](Protocol.md)). The model calls one, the overlay turns it into an action card with a Copy button, the player pastes it in game. The model never learns the outcome.

## How they combine

"Where is the auctioneer?" →

1. `wowhead_search("auctioneer")` → ids.
2. `wowhead_get(npc, 8719)` → Auctioneer Fitch, Stormwind, coordinates.
3. `tomtom.waypoint(…)` → an action card; the player copies and pastes.

Steps 1 and 2 loop freely inside Python. Step 3 leaves through the clipboard.

## Wowhead first

Decided by Denis, 2026-11-10: **Wisp works primarily on Wowhead** — "almost all hints will be from Wowhead". It is the Forever database, the guides, the comments and the news in one place, and it is the only source Wisp reads as structures.

- An ordinary question is answered from Wowhead and the character snapshot.
- A research run starts on Wowhead too, and goes to the wider internet only for what Wowhead does not have.
- So the Wowhead functions are where the effort goes; web search is the secondary tool.

## Web search and page reading

> Wanted by Denis, 2026-11-10: an internet search tool beside Wowhead, **to be used in research runs**. The tool design below is proposed.

Two tools, both returning structures like every other knowledge tool:

```python
class WebResult(Model):
    title: Annotated[str, Field(max_length=200)]
    url: HttpUrl
    site: Annotated[str, Field(max_length=100)]          # "warcrafttavern.com"
    snippet: Annotated[str, Field(max_length=500)]
    published: date | None = None

class WebResults(Model):
    query: str
    results: list[WebResult] = Field(max_length=10)       # empty = nothing found, not an error
    backend: str                                           # which search service answered
    fetched_at: datetime

class WebPage(Model):
    url: HttpUrl
    title: Annotated[str, Field(max_length=200)] | None = None
    text: Annotated[str, Field(max_length=20_000)]         # words a reader sees: no scripts, styles, markup
    truncated: bool
    fetched_at: datetime
```

Where the search comes from — **two routes, to choose**:

| | Wisp's own tool | The provider's built-in search |
|---|---|---|
| Works on | every provider | only those that have one |
| Extra key | yes, one for a search service (bring your own, like the model key) | no, billed on the model key |
| Results | the structures above: cached, capped, the same everywhere | each provider's own format, outside Wisp's control |
| Limit to chosen sites | yes | depends on the provider |

Proposed, given Wowhead first: **start with the provider's built-in search**, and no second key. Web search is a fallback inside research, so it does not yet justify its own key and backend. Wisp's own tool (first backend Brave Search, priced by kintale's probe on 2026-11-04 at $5 per 1000 queries with $5 of free use a month) is the step to take later if research on Kimi or OpenRouter turns out to need it.

Rules, on top of those for every knowledge tool:

- **`web_read` only fetches the public web**: http and https, no local or private addresses, no files; a size limit and a timeout; text pages only.
- **Search can be limited to chosen sites**, and a WoW question prefers a short list of known ones before the open web.
- **Results are cached**, a week as in kintale; a failed read is not cached.
- **Caps per question**: searches and pages read. A research run has its own, higher ones.
- An ordinary question does not search the web; only a research run does, and only after Wowhead (proposed, following "Wowhead first").
- **A web page is the least trusted input Wisp has.** Its text is data; an instruction inside it is ignored, and nothing from it reaches the game except through a card you copy.

## Worked example: "what am I missing for the Deadmines?"

The scenario Denis asked for on 2026-11-10. Every data source in it was checked that day; none of it is built.

1. `wowhead_search("deadmines")` → the zone, id 1581.
2. `wowhead_zone_quests(1581)` → the dungeon's 8 quests, with level and faction.
3. `character` → the quests already completed and those in the log. Subtract them, and drop the other faction's.
4. For each quest left, `wowhead_quest(id)` → its chain. The answer names **the first step not yet done**, not the first step of the chain, and who starts it.
5. `wowhead_get(npc, id)` → where that NPC stands.
6. The answer: one quest card per missing quest (name, level, "step 2 of 2", starts at Gryan Stoutmantle in Westfall), and one action card with a waypoint to each starting NPC.

What it depends on:

- **The addon must save completed quest ids**, not only the quest log. The log shows what is in progress; "missing" needs what is done.
- **That list is as of the last login or logout.** A quest finished this session still looks missing until then. A screenshot of the open quest log covers the in-progress side live, but only the part that is on screen, and it cannot show completed quests.
- `wowhead_zone_quests` and `wowhead_quest` — listed as later functions; this scenario is the argument for having them early.

## Worked example: "level my tailoring, and give me the shopping list"

Asked by Denis on 2026-11-10. **Built the same day without Wowhead** ([Integrations](Integrations.md)): recipes come from the game's own profession window, so the plan covers the recipes the character has; the steps below that lean on `wowhead_profession` and a guide remain the design for the full path.

1. `character` → profession and current skill, and what is already in bags and bank.
2. `wowhead_profession(197)` → every recipe with its materials and the skill range in which it still gives skill. The profession's leveling guide, where one exists, gives a tested path to follow and cite.
3. The model picks the path: which recipe to craft over which skill range.
4. **`craft_plan` does the arithmetic** — a calculator tool, in code: steps × materials, summed, minus what you own. The model never adds up quantities itself; totals from a language model are not trustworthy.
5. The answer, step by step: "150–165: 20 × Bolt of Silk Cloth — 80 Silk Cloth", with a stop at each point where a trainer must be visited.
6. Cards: one **plan card** with the steps as a checklist; one **action card** with the shopping list for the auction addon and waypoints to the trainers.

What it depends on:

- **The addon must save professions with skill levels, and bag and bank contents.** Bank contents are only known from the last time the bank was open.
- **Skill is as of the last login or logout.** Mid-session you say "I'm at 180 now" and the plan continues from there.
- **Recipes that are not orange do not always give a point.** The plan must say its counts are an estimate and add a margin; a guide's tested numbers are better than a calculation.
- **A shopping-list command for Auctionator** ([Integrations](Integrations.md)). Its API accepts quantities and supports Forever, per its source; not yet run in a client.
- **Prices** come only from the auction addon's saved scan, so a cost estimate is as old as that scan.
- This is a **research run** more than a quick question: many recipes, a guide to read, a plan worth keeping.

## Research run

> Proposed 2026-11-09, modelled on kintale's `research` tool. Not for the first version.

An ordinary question gets a short, bounded loop: a few lookups, a quick answer. Some questions need more than that — "pre-raid gear for my spec and where each piece comes from", "a route from 40 to 50", "which profession pays". A research run is the deliberate, slower, dearer mode for those.

- **Never starts by itself.** You type `/research <question>`, or the model *proposes* it as a card with a **Research** button when a question is too big for a quick answer. It spends your key, so it needs your tap.
- **Fan out, then read.** Several searches at once — Wowhead and, **decided by Denis on 2026-11-10, the internet through `web_search`** — then the best few pages are read in parallel with `web_read`. Research is the main place web search is used.
- **Small model per page, big model for judgement.** Each page goes to the provider's cheap model with one job: extract the facts relevant to the question, with a short quote. The main model only sees those facts and decides. This is kintale's split and it is what keeps the cost down.
- **A research note written by code, not by the model:** what was searched, which pages were read (with links), what was not found. An honest "nothing found" never depends on the model saying so.
- **Every fact cites its page** as [n].
- **The note is kept** and reused: a later question on the same topic starts from it and does not read the pages again.
- **Runs while you play.** The overlay can be collapsed; the wisp shows progress and signals when the report is ready.
- **Ends in a report, and action cards where they apply** — a gear list can come with its waypoints.
- **Its own caps**, higher than a normal question's, shown before it starts.

## Rules for every knowledge tool

- **Structures, not pages.** A tool returns a validated model with named fields. HTML never reaches the model.
- **Typed errors, not exceptions.** A failure is a `ToolError` the model can read and react to.
- **Provenance on every result**: where it came from and when, so answers can cite and the UI can link.
- **Bounded.** Lists and text are capped; one call cannot flood the context.
- **Outside text is data.** Nothing a tool fetched is treated as an instruction.
- **Cost caps fail closed**: calls per question, questions per run.

## Candidates beyond Wowhead

Undecided. Listed so the tool set is designed as a set.

| Tool | What for |
|---|---|
| `craft_plan` | Calculator: crafting steps → total materials, minus what the character owns. Arithmetic in code, never by the model |
| `macro_check` | Validator: macro length, commands, conditional syntax, spell and item names. Runs before a macro is offered ([Integrations](Integrations.md)) |
| `talents` | Decode a build code into talent names and ranks; diff it against the character's current talents; list the next points while leveling ([Integrations](Integrations.md)) |
| `prices` | Auction prices from the Auctionator or TSM saved data |
| `notes` | Wisp's own memory between sessions: goals, plans, what was decided |
| `guide` | A guide site other than Wowhead |
