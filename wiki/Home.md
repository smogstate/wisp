# Wisp Wiki

**Wisp** — an AI assistant for World of Warcraft, built first for **WoW Forever** (Blizzard's classless server). You chat with it in an overlay window beside the game; it knows your character, can look at your screen, and can hand commands to your addons.

This wiki is the project's knowledge base. Plain Markdown, no proprietary format. Every page is editable; every decision should end up here.

## Pages

- [Vision](Vision.md) — what Wisp is for, the loop from question to action, who it is for
- [Red Lines](Red-Lines.md) — no account risk, ever: what Wisp never does, the Blizzard policy research, every channel we rejected and why
- [Architecture](Architecture.md) — overlay (Python) + addon (Lua), the only channels that exist between them, what is decided and what is open
- [Protocol](Protocol.md) — `WISP1` paste messages, the integration registry, the manifest the AI discovers tools from
- [Integrations](Integrations.md) — addons Wisp talks to: candidates, what each needs, what is verified
- [Ideas](Ideas.md) — backlog: postponed and undecided

## Status

- **2026-11-09** — project named; design worked out in one session (overlay + addon, paste protocol, red lines); wiki created. Architecture is agreed in outline, not yet signed off for building. No code yet: an early draft that predated these decisions was removed.
