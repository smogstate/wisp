# Roadmap

> Status: order proposed 2026-11-10; nothing is built. Decided by Denis so far: **snapshot history comes first among the feature ideas**, so it is part of the base, not an extra. Each milestone ends with a review before the next starts.

## MVP — built 2026-11-10, ahead of the order below

Denis asked for a first working slice once it was clear he has Questie. It cuts across milestones 1, 2 and 4:

> Ask about a quest in your log; Wisp answers knowing your character; a card gives a waypoint to paste into TomTom.

- **Addon** (`addon/Wisp`): snapshot at logout — character, quest log, completed quests, and Questie's data for each quest; a paste inbox with one command, `tomtom.waypoint`.
- **Overlay** (`wisp/`): a chat window, Claude with the player's key, tools `character`, `quests`, `quest_done`, `waypoint`.
- **A guard worth keeping:** the model never handles coordinates. Locations carry a ref; the waypoint tool accepts only refs, so a coordinate cannot be invented on its way to the game.
- The interface is a web page served on the local machine only, shown in a pywebview window. This made it testable in a headless browser.

Left out on purpose: themes beyond following the system, transparency, collapsing, sessions and slash commands, snapshot history, other providers, every other tool and integration.

**Tested** only without the game and without a model key: the addon in a Lua interpreter against a faked game and Questie, the whole loop with a scripted model, the interface in headless Chrome, and the real Anthropic SDK against a local stand-in for the API. The game functions the addon calls were checked against Blizzard's Forever interface source. The real pywebview window was opened on Linux and driven through a question and a Copy; Questie's record fields were checked in its source. `python -m wisp --check <game folder>` tests a real setup step by step, including one real model call. **Not tested:** the addon in Forever and what the real model answers.

**It ships as `Wisp.exe`** (2026-11-10): Denis has no Python on the game PC. Everything the command line did moved into a Settings panel — game folder, one-button addon install, the key in the Windows credential store, Check setup. `.github/workflows/build.yml` builds the exe on a Windows machine, runs the tests there, opens the exe's own window and drives it through a question, a card, a Copy and a credential-store write, and publishes it as the `mvp` release. That build passed, so the window on Windows is no longer an unknown. Those are the next things to learn, and `checks/WispCheck` covers the game side.

## 0. Checks, before any product code

Things the design assumes and nobody has run. Each is minutes of work and can change the plan. **Desk checks done 2026-11-10 — results in [Checks](Checks.md).** One of them does change the plan: Wowhead's database pages are closed to a plain client, and how Wisp gets that data is an open decision. The checks that need the game client or a provider key are still to run.

- **On the Windows PC:** does Wowhead answer a plain Python request, or only a browser ([Wowhead](Wowhead.md)).
- **In the Forever client:** JSON and base64 decoding (`C_EncodingUtil`); reading completed quests, professions, bags, spellbook, talents, macros; creating a macro; a TomTom waypoint and an Auctionator list from another addon; whether the talent window has an Import box.
- **Providers:** tool calling on Kimi, including while streaming ([LLM Providers](LLM-Providers.md)).

## 1. The overlay, talking

A window you can chat in. Modern UI, dark and light themes, adjustable transparency, collapsible, Send that flips to Stop, sessions with `/new`, `/sessions`, `/fork`. One provider with your key. No game data yet.

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
