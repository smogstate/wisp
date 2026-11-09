# Architecture

> Status: outline agreed 2026-11-09, not yet signed off for building. Items marked **open** are undecided.

## The constraint that shapes everything

A WoW addon cannot open a socket or pipe, and cannot read or write files while the game runs. The client saves addon data only at logout or UI load, and reads addon code only at UI load. Combined with [Red Lines](Red-Lines.md), that leaves exactly these channels:

| Direction | Channel | Live? |
|---|---|---|
| Game → Wisp | Addon's saved file (SavedVariables) | No — as of last logout or login |
| Game → Wisp | Screenshot | Yes — at the moment of asking |
| Wisp → Game | Encoded command pasted by the player | Yes |
| Wisp → Game | Addon data file written by Python | No — picked up at next login |

Everything below is built from those four.

## Two halves

```
┌─ WoW ────────────────────────────────┐      ┌─ Wisp overlay (Python, Windows) ─────┐
│                                      │      │                                      │
│  Wisp addon (Lua)                    │      │  chat window, always on top          │
│   ├ integrations: TomTom, …          │      │  action cards + Copy                 │
│   ├ paste input  ◀── player pastes ──┼──────┤  clipboard                           │
│   └ saved file ── at logout ─────────┼─────▶│  character snapshot + tool manifest  │
│                                      │      │                                      │
│  screen ── capture when you ask ─────┼─────▶│  screenshot                          │
└──────────────────────────────────────┘      │  Claude API + web search             │
                                              └──────────────────────────────────────┘
```

**Overlay (Python).** The face and the brain. Holds the conversation, calls Claude, shows answers and action cards, owns the clipboard. Never touches the game.

**Addon (Lua).** The hands. Receives pasted commands and runs them through other addons' APIs. At logout it saves a character snapshot and a manifest of what it can do.

## Flows

**Asking.** Hotkey opens the overlay → you type → Wisp sends the question with the saved snapshot, the tool manifest and (if enabled) a screenshot → the answer streams in.

**Acting.** Claude calls one or more tools from the manifest → the overlay shows each as a card → the encoded message goes to the clipboard → you open Wisp's input in game and paste → the addon validates and runs each command and reports in chat. Format in [Protocol](Protocol.md).

**Learning what is installed.** At logout the addon writes the manifest of active integrations. The overlay reads it on start. A new integration becomes known to the AI after one login and logout, with no Python change.

## Decided

- Overlay window in Python; WoW runs on Windows.
- The player never types `/reload`.
- Chat happens in the overlay; copy-paste is only for commands into the game.
- Commands travel as an extensible protocol with per-addon integrations ([Protocol](Protocol.md)).
- Everything in [Red Lines](Red-Lines.md).

## Proposed, awaiting a yes

- **Screenshot context** — one capture per question, only when you ask. The only safe source of live context.
- **Auto-copy** — the message is copied the moment an answer contains an action; the Copy button copies it again.
- **Claude with web search** as the model (`claude-opus-5-5`), with hard cost caps: searches per question, questions per run, fail closed.
- **tkinter** for the window, so the only Python dependencies are the Anthropic SDK and a screen-capture library.
- **Paste key** — Ctrl+V twice in game: first opens Wisp's input, second pastes. `/wisp` opens it too.

## Open

- Which integrations ship first beyond TomTom ([Integrations](Integrations.md)).
- Where answers appear: a side panel, or a small bubble that fades.
- Whether questions can also be typed in game. Without a live channel out, they cannot reach the overlay — likely dropped.
- First-run experience: the manifest does not exist until the first logout.
- Whose API key and what monthly cap.

## Known limits

- Character data can be a session old; the screenshot covers what is on screen.
- Claude never sees whether a command worked, so commands are fire-and-forget and cannot be chained on results.
- Addons cannot cast, target or move for the player, and on Forever cannot read protected combat data. Wisp is an out-of-combat assistant.
