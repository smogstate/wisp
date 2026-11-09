# Protocol

> Status: proposed 2026-11-09. Requirement from Denis: it must be extensible, with integrations for various addons. Nothing here is built or tested in a client.

How the overlay tells the addon what to do, and how the AI finds out what it may ask for.

## The message

One line: a version tag, then base64 of a JSON document.

```
WISP1:eyJjbWRzIjpbeyJkbyI6InRvbXRvbS53YXlwb2ludCIsIm1hcCI6MjMzOSwi...
```

Decoded:

```json
{"cmds": [
  {"do": "tomtom.waypoint", "map": 2339, "x": 45.2, "y": 33.1, "title": "Auctioneer"},
  {"do": "auctionator.shopping_list", "name": "Tonight", "items": ["Bismuth", "Ironclaw Ore"]}
]}
```

- **`do`** is `integration.command`. Every other key is an argument of that command.
- **A message carries any number of commands**, for any mix of integrations.
- **Base64** keeps it paste-safe: no line breaks, quotes, or WoW's `|` escape codes.
- **`WISP1`** is the protocol version. An addon that sees a version it does not know says so instead of guessing.

## An integration

One Lua file per addon Wisp talks to. It registers itself:

```lua
Wisp:Register("tomtom", {
  requires = "TomTom",                       -- inactive unless this addon is loaded
  commands = {
    waypoint = {
      help = "Set a waypoint arrow to a map position",
      args = { map = "number", x = "number", y = "number", title = "string?" },
      run  = function(a) TomTom:AddWaypoint(a.map, a.x / 100, a.y / 100, { title = a.title }) end,
    },
  },
  state = function() return { waypoints = ... } end,   -- optional: data Wisp may read
})
```

`Wisp:Register` is public, so another addon's author can ship their own integration without touching Wisp.

## Discovery: the manifest

At logout the addon saves a manifest of every **active** integration (its required addon is loaded): command names, help text, argument schemas. The overlay reads it and passes each command to the model as a tool.

The manifest uses the shape of an MCP tool definition — `name`, `description`, a JSON Schema for the input — which every provider's tool format can be built from. So:

- adding an integration needs no Python change;
- the model only offers actions for addons the player actually has;
- the overlay could later be exposed as a real MCP server with a thin wrapper ([Ideas](Ideas.md)).

## How this differs from MCP

| | MCP | Wisp |
|---|---|---|
| Tool discovery | live | manifest, as of last logout |
| Tool call | live connection | pasted by the player |
| Tool result | returned to the model | **none** |

Commands are fire-and-forget. The model cannot check an outcome and continue; the player sees the outcome in game.

## Reading from addons

An integration's `state` function contributes to the saved snapshot (current waypoints, prices, professions…). It is written only at logout or UI load, so it can be a session old. There is no live read ([Red Lines](Red-Lines.md)).

## Safety rules

- Only registered commands run. Pasted text is never executed as Lua.
- Every argument is checked against the declared type before `run` is called.
- Text arguments are stripped of `|` so nothing from outside can inject colour codes, textures or clickable links.
- A failed or unknown command is skipped and reported in chat; the rest of the batch still runs.
- The overlay shows every command as a card before copying: what you paste is what you saw.

## Open

- **Decoding in the client.** The plan is the built-in `C_EncodingUtil` (JSON and base64). It is believed to exist in retail and so in Forever, unverified. Fallback: a small bundled decoder.
- **Who converts argument types to JSON Schema** — the addon when it writes the manifest, or Python when it reads it.
- **Zone identifiers.** Wowhead gives area ids, TomTom wants uiMapIDs ([Wowhead](Wowhead.md)). `tomtom.waypoint` should probably take an area id or zone name and resolve it in the addon; the `map` argument above is a placeholder.
- **Size limit** per message, and whether to compress large batches.
- **Versioning of individual integrations**, if a command's arguments change.
