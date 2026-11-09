# Ideas — postponed & undecided

Backlog from the 2026-11-09 design session. Nothing here is committed.

## Feature ideas — 2026-11-10

Denis asked what else would be useful. Unprioritized by him; the first five are the ones the existing building blocks already nearly cover. All are out-of-combat help.

**Nearly free with what is designed**

- **Gear: "is this an upgrade?" and a pre-raid checklist.** Per slot: what you wear, the target piece, where it drops and how often, sorted by how easy it is to get. Snapshot gear + a best-in-slot guide + `wowhead_sources`.
- **Attunements, keys and reputations.** "What do I still need for Molten Core / the Onyxia key / Revered with X": the same chain-walking as the dungeon-quests scenario, over completed quests.
- **Dungeon and raid prep.** Before you go: quests to pick up, the key or attunement, loot that is an upgrade for you, and a short briefing per boss for your role. Information before the fight, never during it.
- **Gold.** "Which of my recipes is profitable now", "what in my bags is worth selling, vendoring or disenchanting": recipes + Auctionator's saved prices + the calculator. As fresh as the last auction scan.
- **Session planner and recap.** "I have two hours tonight" → a plan across quests, profession and a dungeon, kept as a note. Next login: "since last time you gained two levels, finished six quests, tailoring 150 → 185".

**Needs one new piece**

- **Snapshot history.** Python keeps every snapshot, not only the latest. Gives the recap above, gold and progress over time, and goal tracking across sessions ("Thunderfury: step 4 of 9").
- **Quiet nudges.** After a login, code (no model call) compares the new snapshot with your goals and the wisp shows a dot: "you now have the materials for step 3".
- **Alts.** A snapshot per character: "which of my characters can craft this", materials across all banks.
- **Hardcore briefings.** Before a zone, an elite quest or a dungeon: what kills people there, from guides and comments.
- **News for you.** "What changed in the last patch for my class and professions", from the Forever news feed.

**Small and cheap**

- **Chat help.** Draft or translate a guild advert, a group-finder message, a reply to a whisper seen in a screenshot. Copied as plain text for you to paste into chat — not a Wisp command.
- **"Where is this?"** from a screenshot of a quest text: the objective's location and the most useful comment.
- **Addon help.** Explain an addon's error from a screenshot; walk through setting up another addon; hand over a WeakAuras import.

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
