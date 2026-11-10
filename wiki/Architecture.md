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
└──────────────────────────────────────┘      │  LLM, your key (BYOK)                │
                                              └──────────────────────────────────────┘
```

**Overlay (Python).** The face and the brain. Holds the conversation, calls the model, runs the knowledge tools ([Tools](Tools.md)), shows answers and action cards, owns the clipboard. Never touches the game.

**Addon (Lua).** The hands. Receives pasted commands and runs them through other addons' APIs. At logout it saves a character snapshot and a manifest of what it can do.

## Flows

**Asking.** Hotkey opens the overlay → you type → Wisp sends the question with the saved snapshot, the tool manifest and (if enabled) a screenshot → the answer streams in.

**Acting.** The model calls one or more tools from the manifest → the overlay shows each as a card → the encoded message goes to the clipboard → you open Wisp's input in game and paste → the addon validates and runs each command and reports in chat. Format in [Protocol](Protocol.md).

**Learning what is installed.** At logout the addon writes the manifest of active integrations. The overlay reads it on start. A new integration becomes known to the AI after one login and logout, with no Python change.

## Cards

> Proposed 2026-11-09.

A card is a box in the conversation for something the model produced that is not prose. Two kinds.

**Action card** — one batch of instructions for the game. It exists because the model cannot act in the game itself: when it calls a game command, the overlay does not run anything, it shows the card.

```
┌ For the game ───────────────────────────────┐
│ ☑ TomTom  Waypoint: Auctioneer Fitch        │
│           Stormwind City  53.4, 59.4        │
│ ☑ TomTom  Waypoint: Bank                    │
│           Stormwind City  57.0, 72.5        │
│                                             │
│ Paste in game: Ctrl+V twice        [ Copy ] │
└─────────────────────────────────────────────┘
```

- One line per command, in plain words: which addon, what it will do.
- All commands of one answer share one card and one Copy, because one paste carries them all.
- A line can be unticked to leave it out.
- After copying, the button shows "Copied". The card never shows "done": Wisp cannot see the result.

**Entity card** — a Wowhead result shown as a thing, not as text: icon, name in its quality colour, the few stats that matter, a link to the Wowhead page. Shown for `/wh` results and when an answer centres on one item, NPC or quest. An NPC's card can offer "waypoint", which produces an action card.

## Themes and transparency

> Built 2026-11-10. Wisp's window has no title bar and a see-through background; the header is its handle. Theme and transparency are in Settings and in `/theme` and `/opacity`. An ordinary framed, opaque window remains available as a setting, in case a PC draws the see-through one badly.
>
> **On Windows the whole window fades, text included.** The first plan was a see-through background under solid text. A web view on Windows cannot blend single pixels with the desktop, which the first Windows screenshots showed: an opaque window and a white square around the wisp. So the window's own opacity is used: solid while the mouse is over it or it has the keyboard, and as see-through as the setting says otherwise. The collapsed wisp is a window cut to a disc. On systems that do blend pixels the page's translucent background is used, with the same meaning for the setting.

- **Themes: dark, light, and auto.** Auto follows the Windows setting and is the default. `/theme` switches.
- **Transparency is of the panel's background, not of the text.** The game shows through the panel; text, cards and buttons stay fully solid so they remain readable over a busy scene. A see-through window with see-through text is the thing to avoid.
- **One setting, a slider**, from solid to mostly clear. `/opacity 70` sets it from the keyboard.
- **Proposed: more see-through when you are not using it.** While the mouse is over the panel or you are typing, it goes to its solid setting; otherwise it fades to a lighter one, so it gets out of the way of the game by itself.
- Both themes must pass the same check at every transparency level: text contrast against the brightest and the darkest game scene.
- The collapsed wisp follows the theme too.

To confirm when the window is built: that the chosen window toolkit supports a see-through background on Windows the way this needs.

## Snapshot history

> Keeping history is decided (2026-11-10). The mechanics below are proposed.

The addon saves one snapshot of the character; the game writes it to disk at logout. **Python keeps every one of them**, so Wisp knows not only what the character is but how it got there.

- **The addon stays simple.** It holds only the current snapshot. History lives on the Python side, outside the game's files.
- **Python watches the saved file.** When it changes, the snapshot is copied into Wisp's own store, one file per snapshot, never overwritten: `snapshots/<realm>-<character>/<time>.json`.
- **Each snapshot carries its own time** (the game clock when it was taken) and a format version. An unchanged snapshot is not stored twice.
- **Per character**, so alts come for free.
- **If Wisp was not running**, it picks the snapshot up at its next start. If you logged out several times in between, only the last of those is seen; the gap is accepted.
- **Differences are computed in code**, not by the model: levels, gold, quests completed, profession skill, gear, talents, what entered and left the bags.
- **Everything stays on this PC.**

What it gives:

- **A recap when you come back**: "since last time: two levels, six quests, tailoring 150 → 185". Code only, no model call.
- **Goals across sessions**: "Thunderfury: step 4 of 9".
- **Progress over time**: gold, levels, skill.
- **The model can ask about the past**: what changed since a date, what the character looked like then.

Open:

- **How the snapshot is written.** Preferred: the addon stores it as one JSON text, which Python reads directly; this depends on the client having a JSON encoder. Otherwise Python needs a small reader for the game's saved-table format.
- **How long to keep everything.** A snapshot is small, so "forever" is fine for a long time; thinning old ones to one a day is the later answer.

## Collapsing

> Built 2026-11-10: the window shrinks to a 64-pixel square holding the wisp, keeping its top-right corner in place. Collapse with the header button, Esc when nothing is going on, `/collapse`, or the global key Ctrl+Shift+Space (which Windows delivers to Wisp whichever program has the keyboard; Wisp only listens). Click the wisp to come back; drag it to move it. It breathes while thinking and shows a mark when an answer or game instructions are waiting. Not built: collapsing by itself when the game takes focus.

Two states. **Open**: the chat panel. **Collapsed**: a small glowing wisp at the screen edge that stays on top and out of the way.

- Collapse with the hotkey, a button in the panel header, or Esc when nothing is being typed or generated. The same hotkey opens it again; so does a click on the wisp.
- The wisp shows state without opening: idle, thinking (animated), answer ready (a dot), instructions waiting to be copied (a different mark).
- An answer keeps generating while collapsed.
- The wisp can be dragged; position and state are remembered between runs.
- **Open**: should it collapse by itself when the game window takes focus.

## Where cards come from

The card pattern is kintale's (and vintry's before it). kintale's tool registry gives every tool a confirm class, and Wisp's two kinds of tool map onto it:

| kintale class | Meaning there | Wisp |
|---|---|---|
| `:auto` | Pure reads: run at once, result returns to the model, bounded number of hops | Knowledge tools ([Tools](Tools.md)) |
| `:card` / `:action` | Never auto-runs: the request renders as a card with its own verb button | Game commands — the verb is **Copy** |

The difference: kintale's card applies the change itself and knows the outcome. Wisp's card can only hand the instruction over; the player carries it and Wisp never sees the result.

Worth carrying over from kintale as well (proposed): **one registry** where a tool is a single record and everything else is derived from it; a **hop limit** on the read loop; a **trace line** in the chat for each tool call, so it is visible what the model looked up.

## Decided

- Overlay window in Python; WoW runs on Windows.
- The player never types `/reload`.
- Chat happens in the overlay; copy-paste is only for commands into the game.
- Commands travel as an extensible protocol with per-addon integrations ([Protocol](Protocol.md)).
- **A nice, modern UI** for the overlay, with a **Copy button** on instructions for the game and one **Send button that flips to Stop** while an answer is generated, as in Claude Code.
- **The overlay is collapsible** (Denis, 2026-11-09).
- **Dark and light themes, and transparency** (Denis, 2026-11-10).
- **Slash commands**: `/new`, `/sessions`, `/fork` at least ([Commands](Commands.md)).
- The Python layer has a **set of tools** that return structures, not web pages; Wowhead is the first ([Tools](Tools.md)).
- **Snapshot history**: every character snapshot is kept, not only the latest (Denis, 2026-11-10).
- **BYOK.** The player brings their own key; Anthropic, Kimi and OpenRouter are supported behind one contract ([LLM Providers](LLM-Providers.md)).
- Everything in [Red Lines](Red-Lines.md).

## Proposed, awaiting a yes

- **Screenshot context** — one capture per question, only when you ask. The only safe source of live context.
- **Auto-copy** — the message is copied the moment an answer contains an action; the Copy button copies it again.
- **Hard cost caps** on every provider: tool calls per question, spend per day, fail closed.
- **pywebview** for the window: the UI is HTML and CSS in a native frameless, always-on-top window (WebView2 on Windows). This replaces the earlier tkinter proposal, which cannot look modern. Chat with streamed, formatted answers; action cards with Copy; links to sources.
- **Paste key** — Ctrl+V twice in game: first opens Wisp's input, second pastes. `/wisp` opens it too.

## Open

- What exactly the addon saves in the character snapshot. Known needs: **completed quest ids**, for "what am I missing" questions, **professions with skill levels plus bag and bank contents**, for crafting plans, **the spellbook and existing macros**, for macro help, and **current talents**, for build comparison ([Tools](Tools.md)).
- Which integrations ship first beyond TomTom ([Integrations](Integrations.md)).
- Where answers appear: a side panel, or a small bubble that fades.
- Whether questions can also be typed in game. Without a live channel out, they cannot reach the overlay — likely dropped.
- First-run experience: the manifest does not exist until the first logout.
- The daily spend cap, and the default provider and model for a first run.

## Known limits

- Character data can be a session old; the screenshot covers what is on screen.
- The model never sees whether a command worked, so commands are fire-and-forget and cannot be chained on results.
- Addons cannot cast, target or move for the player, and on Forever cannot read protected combat data. Wisp is an out-of-combat assistant.
