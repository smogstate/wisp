# Vision

## What Wisp is

An AI companion that sits beside the game. You ask it things in plain language — where to go, what to take, whether an item is worth it, how to plan tonight — and it answers knowing who your character is and what is on your screen. When the answer is something to *do* in game, Wisp hands it to your addons.

## Why WoW Forever

WoW Forever is Blizzard's reboot of vanilla WoW: the nine original classes with 27 reworked specializations, level 60 permanently, no expansions, and new zones, dungeons and raids on top of the original world ([Warcraft Tavern overview](https://www.warcrafttavern.com/forever/guides/overview/)). It is live. Items and abilities differ from every earlier version, and it is too new for a language model to know from training. Wisp answers from Wowhead's Forever data ([Wowhead](Wowhead.md)), web search and your own character data, not from memory.

Forever's addon API is reported to match retail Midnight, including its restrictions: addons cannot read protected combat data (one secondhand source; to confirm in the client). Wisp is therefore an out-of-combat assistant — planning, navigation, economy, builds — not a combat helper. See [Red Lines](Red-Lines.md).

## The loop

1. **Ask** — in the Wisp window, opened with a hotkey. No describing your character: Wisp already has it.
2. **Answer** — streamed text, grounded in web search, your saved character snapshot and (if enabled) a screenshot.
3. **Act** — if the answer includes an in-game action, it appears as a card. You copy, paste into Wisp's input in game, and the addon carries it out through other addons (a TomTom waypoint, a shopping list…).

## What good looks like

- Chatting never involves copy-paste, typing commands, or `/reload`.
- An action costs one copy and one paste, and one paste can carry a whole batch.
- Adding support for another addon is one small file; the AI learns about it on its own ([Protocol](Protocol.md)).
- Nothing Wisp does can put the account at risk ([Red Lines](Red-Lines.md)).

## Audience

Built for Denis first. Decisions that would only matter for publishing (branding, trademarks, distribution) are parked in [Ideas](Ideas.md). The name avoids Blizzard trademarks so publishing stays possible.
