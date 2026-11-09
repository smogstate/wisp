# Tools

> Status: draft 2026-11-09. Requirement from Denis: the Python layer has a set of tools, and tools return structures, not web pages.

What the model can call from inside the overlay. Two kinds, and the difference matters.

## Knowledge tools — run in Python, result returns to the model

The model calls them, Python runs them, the model reads the result and can call again. This is ordinary tool use.

| Tool | Returns | Page |
|---|---|---|
| `wowhead_search`, `wowhead_get`, … | Items, spells, quests, NPCs with coordinates, as validated structures | [Wowhead](Wowhead.md) |
| `character` | The saved character snapshot: class, spec, level, gear, quest log, addon state | [Architecture](Architecture.md) |
| `screenshot` | What is on screen right now (proposed) | [Architecture](Architecture.md) |
| `web_search` | Fallback for what no structured tool covers: news, forum answers. Built in on some providers only | [LLM Providers](LLM-Providers.md) |

## Game commands — shown as cards, pasted by the player, no result

They come from the addon's manifest ([Protocol](Protocol.md)). The model calls one, the overlay turns it into an action card with a Copy button, the player pastes it in game. The model never learns the outcome.

## How they combine

"Where is the auctioneer?" →

1. `wowhead_search("auctioneer")` → ids.
2. `wowhead_get(npc, 8719)` → Auctioneer Fitch, Stormwind, coordinates.
3. `tomtom.waypoint(…)` → an action card; the player copies and pastes.

Steps 1 and 2 loop freely inside Python. Step 3 leaves through the clipboard.

## Research run

> Proposed 2026-11-09, modelled on kintale's `research` tool. Not for the first version.

An ordinary question gets a short, bounded loop: a few lookups, a quick answer. Some questions need more than that — "pre-raid gear for my spec and where each piece comes from", "a route from 40 to 50", "which profession pays". A research run is the deliberate, slower, dearer mode for those.

- **Never starts by itself.** You type `/research <question>`, or the model *proposes* it as a card with a **Research** button when a question is too big for a quick answer. It spends your key, so it needs your tap.
- **Fan out, then read.** Several searches (Wowhead, guides, news, web), then the best few pages are read in parallel.
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
| `talents` | Decode and compare talent import strings |
| `prices` | Auction prices from the Auctionator or TSM saved data |
| `notes` | Wisp's own memory between sessions: goals, plans, what was decided |
| `guide` | A guide site other than Wowhead |
