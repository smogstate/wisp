# Roadmap

> Status: order proposed 2026-11-10; nothing is built. Decided by Denis so far: **snapshot history comes first among the feature ideas**, so it is part of the base, not an extra. Each milestone ends with a review before the next starts.

## 0. Checks, before any product code

Things the design assumes and nobody has run. Each is minutes of work and can change the plan.

- **On the Windows PC:** does Wowhead answer a plain Python request, or only a browser ([Wowhead](Wowhead.md)).
- **In the Forever client:** JSON and base64 decoding (`C_EncodingUtil`); reading completed quests, professions, bags, spellbook, talents, macros; creating a macro; a TomTom waypoint and an Auctionator list from another addon; whether the talent window has an Import box.
- **Providers:** tool calling on Kimi, including while streaming ([LLM Providers](LLM-Providers.md)).

## 1. The overlay, talking

A window you can chat in. Modern UI, collapsible, Send that flips to Stop, sessions with `/new`, `/sessions`, `/fork`. One provider with your key. No game data yet.

## 2. The character, with history

- The addon saves the snapshot.
- **Snapshot history**: Python keeps every snapshot ([Architecture](Architecture.md)).
- The `character` tool, and the recap when you come back.

## 3. Wowhead as structures

`wowhead_search`, `wowhead_get`, `wowhead_zone_quests`, `wowhead_quest`, and entity cards ([Wowhead](Wowhead.md)).

## 4. Into the game

The `WISP1` paste protocol, the integration registry and manifest, TomTom, action cards with Copy ([Protocol](Protocol.md)). **The dungeon-quests scenario works end to end here** ([Tools](Tools.md)).

## 5. The other providers

Kimi and OpenRouter, `/model`, `/cost`, the daily cap.

## 6. Professions

`wowhead_profession`, the `craft_plan` calculator, Auctionator shopping lists. The tailoring scenario works end to end.

## Later, order open

Macros with verification · talent builds · screenshots · web search and the research run that uses it · notes and goals · nudges · the rest of [Ideas](Ideas.md).
