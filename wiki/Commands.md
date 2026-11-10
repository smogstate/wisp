# Commands

> Status: draft 2026-11-09. Requirement from Denis: the overlay has commands — `/new`, `/sessions`, `/fork` named by him. The rest is a proposal to pick from.

A `/` at the start of the input means Wisp is being told what to do, not asked a question. Same model as kintale's assistant.

## Built so far (2026-11-10)

Typing `/` lists every command that exists, filtered as you type; arrows move, Enter runs, Tab completes, Esc closes. Asked for by Denis after the MVP worked.

| Command | Does |
|---|---|
| `/new` | Start a new conversation; the current one is kept |
| `/sessions` | Earlier conversations: search by title or by anything said, peek at one read-only, reopen it and continue |
| `/fork` | Continue in a copy of this conversation. A small branch icon under every answer forks from that point |
| `/cost` | Tokens and cost of this conversation |
| `/copy` | Copy the last game instructions again |
| `/check` | Check that everything is set up |
| `/settings` | Game folder, addon and API key |

**Tokens and cost** are also shown all the time, in a line above the input: tokens used and dollars spent in this conversation. Tokens come from what the provider reports for each response; the price is Wisp's own table, and a model without a price there is shown in tokens only.

**Sessions** (built 2026-11-10): every conversation is a JSON file in `%APPDATA%\Wisp\sessions`, saved after each answer and titled from its first question. A file holds what the window showed, and the conversation in the provider's own format so it can be continued exactly, thinking blocks included. A fork copies the history up to the chosen turn into a new file and leaves the original alone; its token count starts again from zero. The window is given only what was shown, never the provider's history.

Not built yet: everything below that is not in this table.

## How commands behave

- **`/` opens a palette** above the input, filtered as you type. Enter runs the top match.
- **The empty `/` is the help.** There is no `/help`.
- **A command that needs a choice opens a picker** (sessions, models); others run at once.
- **A command that does not apply is absent, not greyed** — `/shot` does not appear on a model without vision.
- **Commands cost nothing.** None of them calls the model unless it says so below.

## Send and stop — decided

One button, as in Claude Code: it is **Send** while you type and flips to **Stop** while an answer is being generated, in the same place. Enter sends; Esc stops. There is no `/stop` command.

Stopping keeps what was already written, marks the answer as stopped, and cancels any tool call still running. A deliberate stop is not shown as an error.

## Sessions — decided

| Command | Does |
|---|---|
| `/new` | Start a fresh session. The current one is kept |
| `/sessions` | Browse earlier sessions: search by title or text, **peek** at one read-only, reopen it |
| `/fork` | Branch the current session into a new one that starts with the same history. Also offered on any message as "fork from here", to try a different direction without losing the original |

Sessions are stored on this PC as plain files, titled automatically from the first question. Nothing is uploaded.

## Proposed — first set

| Command | Does |
|---|---|
| `/model` | Choose which account and model answers, from the provider's live catalogue ([LLM Providers](LLM-Providers.md)). Works mid-session |
| `/shot` | Attach a screenshot to the next question; `/shot auto` attaches one to every question |
| `/copy` | Copy the last game instructions again — the keyboard twin of the Copy button |
| `/me` | Show the character snapshot Wisp has and how old it is, so you can see what the model knows |
| `/cost` | Tokens and money spent this session and today, against the daily cap |
| `/wh <text>` | Search Wowhead directly and show result cards. No model call: instant and free |
| `/note <text>` | Remember something across sessions ("leveling fire, no dungeons until 40"). `/notes` lists and edits them |

## Proposed — later

| Command | Does |
|---|---|
| `/research <question>` | A research run: many sources, pages read and cited, a kept report. Slower and dearer than a question; shows its cap before starting ([Tools](Tools.md)) |
| `/retry` | Generate the last answer again; with `/model` first, on a different model |
| `/way <name>` | Find an NPC or place and produce a waypoint card directly, without a conversation |
| `/actions` | Every instruction sent to the game this session, each with Copy |
| `/addons` | Which integrations the addon reported as active, and when ([Integrations](Integrations.md)) |
| `/compact` | Replace a long session's history with a summary to cut cost. Calls the model once |
| `/export` | Save the session as a Markdown file |
| `/keys` | Add, relabel or remove provider keys |
| `/theme` | Dark, light, or auto (follow Windows) |
| `/opacity <0-100>` | How see-through the panel's background is |
| `/lang` | Language of the interface and of answers |
| `/settings` | Hotkey, theme, transparency, caps |
| Custom commands | Your own prompt shortcuts, e.g. `/upgrade` = screenshot + "is this an upgrade for me?" |

## Open

- Which of the proposed commands make the first version.
- `/fork` from the end only, or from any message (the table assumes both).
- Whether notes are sent with every question or only when relevant.
- What happens to a session when the model is switched mid-way: history is kept in a provider-neutral form, but a provider's private reasoning does not carry over.
