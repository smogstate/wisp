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
