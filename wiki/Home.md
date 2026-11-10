# Wisp Wiki

**Wisp** — an AI assistant for World of Warcraft, built first for **WoW Forever** (Blizzard's reboot of vanilla WoW). You chat with it in an overlay window beside the game; it knows your character, can look at your screen, and can hand commands to your addons.

This wiki is the project's knowledge base. Plain Markdown, no proprietary format. Every page is editable; every decision should end up here.

## Pages

- [Vision](Vision.md) — what Wisp is for, the loop from question to action, who it is for
- [Red Lines](Red-Lines.md) — no account risk, ever: what Wisp never does, the Blizzard policy research, every channel we rejected and why
- [Architecture](Architecture.md) — overlay (Python) + addon (Lua), the only channels that exist between them, what is decided and what is open
- [Protocol](Protocol.md) — `WISP1` paste messages, the integration registry, the manifest the AI discovers tools from
- [Tools](Tools.md) — what the model can call inside the overlay: knowledge tools that return structures, and game commands that leave as cards
- [Wowhead](Wowhead.md) — the first knowledge tool: probed endpoints, functions, draft structures, validation rules
- [Commands](Commands.md) — `/new`, `/sessions`, `/fork` and the rest of the overlay's slash commands
- [LLM Providers](LLM-Providers.md) — BYOK: Anthropic, Kimi, OpenRouter behind one contract, modelled on kintale's provider seam
- [Integrations](Integrations.md) — addons Wisp talks to: candidates, what each needs, what is verified
- [Addon Landscape](Addon-Landscape.md) — what players already run in Forever, where a language model is better and where it is not; Questie as the data source that replaces Wowhead's database pages
- [Game Data](Game-Data.md) — what the addon can read from the game itself: your character, quest lines, the dungeon journal, flight points, and places learned by playing
- [Checks](Checks.md) — milestone 0 results: Wowhead refuses non-browser clients on its database pages; the Forever addon API has what Wisp needs, talent import included
- [Roadmap](Roadmap.md) — the build order, milestone by milestone, starting with the checks nobody has run yet
- [Ideas](Ideas.md) — backlog: postponed and undecided

## Status

- **2026-11-10, later** — **the MVP works in the real game.** It ships as `Wisp.exe`, built and tested on Windows by GitHub Actions; Denis ran it with his key in the Forever client, asked about a quest, pasted the card, and the waypoint appeared.
- **2026-11-10** — milestone 0 desk checks done ([Checks](Checks.md)); Wowhead's database pages turned out closed to Wisp, and the game plus Questie became the primary data source ([Game Data](Game-Data.md), [Addon Landscape](Addon-Landscape.md)). A first working slice, the MVP, is built and tested without the game ([Roadmap](Roadmap.md)); it has not yet run in Forever or against a real model.
- **2026-11-09** — project named; design worked out in one session (overlay + addon, paste protocol, red lines); wiki created. Architecture is agreed in outline, not yet signed off for building. No code yet: an early draft that predated these decisions was removed. Same day: Python tool set and the Wowhead tool drafted; Forever facts corrected (it has classes — an earlier note here said classless); BYOK provider seam drafted from kintale's.
