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
| Macros | create or update a macro | macro list | nothing | no |

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

## Adding one

1. Find the addon's public API (not its internals — those break on updates).
2. Write the integration file: `requires`, commands with `help` and `args`, optional `state`.
3. Check each call in the Forever client.
4. Add a row here with what was verified and on which client version.

## Rules for every integration

- A command does one thing the player could do by hand in the addon's own UI.
- Nothing protected: no casting, targeting, movement, or anything in combat that the game blocks for addons.
- If the required addon is missing, the integration is simply absent from the manifest; the model never offers it.
