# Integrations

> Status: candidate list, 2026-11-09. None built. Every API below must be checked in the Forever client before it is relied on.

Each integration is one Lua file registered through [Protocol](Protocol.md). Commands into an addon are live (by paste); data read from an addon is as of the last logout.

## Candidates

| Integration | Commands | Reads | Requires | Verified |
|---|---|---|---|---|
| Built-in map | pin on the world map | — | nothing | no |
| TomTom | waypoint, route of waypoints | current waypoints | TomTom | no |
| Auctionator | create a shopping list **with quantities** | prices, price age, vendor price | Auctionator | API read in source (v343), not run in a client |
| WeakAuras | open the import dialog with a string | — | WeakAuras | no |
| Talents | show or mark a build in game (how is open) | current talents | nothing | no |
| Macros | create or update a macro, **checked before it is offered** | existing macros, known spells | nothing | no |

TomTom is first. The rest are **open**: Denis decides which follow. **Auctionator is second** (Denis, 2026-11-10): shopping lists for crafting plans.

## Auctionator — what its API offers

Read from the source of Auctionator 343 on 2026-11-10 (a copy bundled in a Forever addon collection; the functions were not run in a client).

- **It supports Forever.** Its manifest lists interface `16001`, which is WoW Forever's interface number (game version 1.60.1).
- **`Auctionator.API.v1.CreateShoppingList(callerID, name, searchStrings)`** creates the list. A list with the same name is replaced. If the auction house is open, the search starts at once.
- **`Auctionator.API.v1.ConvertToSearchString(callerID, term)`** builds each search string from a table. The table has `searchString`, `isExact` and **`quantity`**, plus level, price and quality filters. So a list entry can say "Silk Cloth, exact, 80".
- **Reading:** `GetShoppingListItems`, auction price by item, the age of that price, vendor price and disenchant price. These can feed the saved snapshot for cost estimates.
- `callerID` is a string naming the calling addon: `"Wisp"`.

The command, as it would look in [Protocol](Protocol.md):

```json
{"do": "auctionator.shopping_list", "name": "Tailoring 150-200",
 "items": [{"name": "Silk Cloth", "quantity": 80}, {"name": "Fine Thread", "quantity": 20}]}
```

## Macros — help with verification

> Wanted by Denis, 2026-11-10: macro help, verified. The design below is proposed; no API here has been run in a client.

You describe what the button should do; the model writes the macro; **a checker in code verifies it before you ever see a Copy button**.

What "verified" covers, in three layers:

| Layer | Where | Checks |
|---|---|---|
| Syntax | Python tool `macro_check` | 255-character limit; every line is a known slash command; conditionals are well-formed (`[mod:shift,@mouseover,help,nodead]`); `#showtooltip` only on the first line; `/castsequence` reset syntax |
| Names | Python, through [Wowhead](Wowhead.md) and the snapshot | every spell and item named exists in Forever, spelled exactly; and, from the saved spellbook, that this character knows the spell |
| In game | the addon, at paste | spell names resolve in the client; a free macro slot exists; not in combat. The result is printed in chat |

A macro that fails layer 1 or 2 goes back to the model with the checker's message, to fix; it is not shown as a card. The card shows the macro text in full, line by line, with what each line does.

What verification cannot cover: **whether the macro does what you meant in a fight.** That takes pressing it. The answer says so.

Rules:

- **No `/run` or `/script` in a Wisp macro.** They execute Lua, and Wisp never hands the game code to run ([Red Lines](Red-Lines.md)). The checker rejects them.
- The command is `macro.save {name, body, scope}`: it creates the macro, or updates the one with that name. You drag it to a bar yourself.
- The addon saves existing macros and the spellbook into the snapshot, so "fix my macro" needs no retyping.

Open: the macro slot limits and the exact conditional list in the Forever client, to be read from the client; whether Forever restricts any macro commands the way Midnight restricts addons.

## Talent builds

> Wanted by Denis, 2026-11-10: help with talent builds, import strings. Proposed; the delivery into the game is open.

What Wisp can do with what was verified ([Wowhead](Wowhead.md)):

- **Find builds for your class and purpose** — raid, PvP, leveling — from the Forever guides, each with the guide's reasoning and a citation.
- **Read a build as talents, not as a code**: decode the points per tree into talent names and ranks. Needs the tree definitions, not yet located.
- **Compare with what you have**: "you are 3 points off the raid build: move 2 from X to Y". Needs the addon to save your current talents.
- **Say what comes next while leveling**: "your next five points", from the build's order.
- **Explain a talent** through the ordinary spell lookup.

Getting the build into the game — **open, depends on the Forever client**:

| Way | Needs | Status |
|---|---|---|
| The game's own Import box | The Forever talent window to have one, and a game-format string. Wowhead's guides carry Wowhead's own code, so Wisp would have to convert | unknown whether the box exists |
| A link to the Wowhead calculator | nothing | works, but outside the game |
| The addon marks the next talents in the talent window | the addon reading the talent tree; display only, you click the points | to check in the client |
| The addon spends the points | an API that allows it. Spending points costs gold to undo, so never without a clear confirmation in game | to check; lowest priority |

## Adding one

1. Find the addon's public API (not its internals — those break on updates).
2. Write the integration file: `requires`, commands with `help` and `args`, optional `state`.
3. Check each call in the Forever client.
4. Add a row here with what was verified and on which client version.

## Rules for every integration

- A command does one thing the player could do by hand in the addon's own UI.
- Nothing protected: no casting, targeting, movement, or anything in combat that the game blocks for addons.
- If the required addon is missing, the integration is simply absent from the manifest; the model never offers it.
