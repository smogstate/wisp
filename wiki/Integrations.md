# Integrations

> Status: candidate list, 2026-11-09. None built. Every API below must be checked in the Forever client before it is relied on.

Each integration is one Lua file registered through [Protocol](Protocol.md). Commands into an addon are live (by paste); data read from an addon is as of the last logout.

## Candidates

| Integration | Commands | Reads | Requires | Verified |
|---|---|---|---|---|
| Built-in map | pin on the world map | — | nothing | no |
| TomTom | waypoint, route of waypoints | current waypoints | TomTom | no |
| Auctionator | create a shopping list | prices | Auctionator | no |
| WeakAuras | open the import dialog with a string | — | WeakAuras | no |
| Macros | create or update a macro | macro list | nothing | no |

TomTom is first. The rest are **open**: Denis decides which follow.

## Adding one

1. Find the addon's public API (not its internals — those break on updates).
2. Write the integration file: `requires`, commands with `help` and `args`, optional `state`.
3. Check each call in the Forever client.
4. Add a row here with what was verified and on which client version.

## Rules for every integration

- A command does one thing the player could do by hand in the addon's own UI.
- Nothing protected: no casting, targeting, movement, or anything in combat that the game blocks for addons.
- If the required addon is missing, the integration is simply absent from the manifest; the model never offers it.
