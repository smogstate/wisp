# Ideas — postponed & undecided

Backlog from the 2026-11-09 design session. Nothing here is committed.

## Postponed

- **Wisp as a real MCP server.** Serve the manifest as `tools/list` and answer `tools/call` by putting the command on the clipboard, so Claude Desktop or Claude Code can drive Wisp's tools. A thin wrapper over [Protocol](Protocol.md); build when wanted.
- **Queued commands through the data file.** Python writes commands to the addon's data file and the addon runs them at next login. Suits planning ahead: ask for tonight's route before logging in and every waypoint is already in TomTom. No paste, but not immediate.
- **Answers shown in game.** A pasted message could carry the answer text too, into a Wisp window inside WoW.
- **Publishing the addon.** Would need a policy re-check ([Red Lines](Red-Lines.md)), packaging, and a look at naming.

## Needs outside input

- **Ask Blizzard support about the pixel beacon.** If they approve in writing, it brings back live character state and an arrow that follows the player. Until then it stays out.
- **Image-dimension channel.** Unproven idea for getting small data into the addon without paste: the addon reading the dimensions of image files Python rewrites. Unknown whether the client allows it; would still need a keypress to trigger. Only worth testing if paste proves too clumsy.

## Considered and dropped

- **In-game window with `/reload` round trips** — the first draft. Dropped: the player must never reload.
- **Typing `/wisp <question>` in game chat** — needs a live channel out of the game, which only the beacon gives.
- Everything in the rejected table of [Red Lines](Red-Lines.md).
