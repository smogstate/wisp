# Wisp

An AI assistant for World of Warcraft: Forever. You chat with it in a small window beside the game; it knows your character and quest log, and hands waypoints to your addons.

The design lives in [`wiki/`](wiki/Home.md). What exists today is a first working slice.

## What works now (MVP)

Ask about a quest in your log — where to hand it in, what comes next in the chain — and get an answer plus a waypoint card to paste into the game, where TomTom sets it.

- **`addon/Wisp`** — saves your character, quest log and completed quests when you log out. For each quest it copies out what Questie knows: who starts and ends it, where they stand, the next quest in the chain. Runs pasted commands.
- **`wisp/`** — the overlay: a chat window, Claude with your key, and three tools over the saved snapshot.

Wisp never sends input to the game and never reads its memory. See [Red Lines](wiki/Red-Lines.md).

## Run it

You need the game on Windows, an Anthropic API key, and the Questie addon. TomTom is recommended; without it Wisp uses the built-in map pin. **No Python is needed.**

1. **Download [Wisp.exe](https://github.com/smogstate/wisp/releases/download/mvp/Wisp.exe)** and run it. It is not signed, so Windows SmartScreen will warn: choose *More info*, then *Run anyway*. An antivirus may also ask, as it can with any small unsigned program.
2. **Settings** opens by itself the first time.
   - *Game folder*: Wisp looks in the usual places; otherwise browse to your `World of Warcraft` folder.
   - The **addon installs itself** once Wisp knows the game folder, and updates itself with each new `Wisp.exe`. It is one package; there is nothing else to download.
   - *Anthropic API key*: paste it. It is kept in the Windows credential store on this PC.
3. **Start the game** (or restart it, so it sees the new addon). On the character screen, check that Wisp is ticked under AddOns. Log in, then **log out once**. The addon writes your character and quests to disk at logout.
4. In Settings press **Check setup**. Every line should say `ok`; a line that says `FAIL` tells you what to do.
5. **Ask**, for example: *where do I hand in my wolf quest, and what comes next?*
6. **Copy** on the card, then in the game press **Ctrl+V twice**: the first opens Wisp's inbox, the second pastes. `/wisp` opens the inbox too.

To see the interface with a sample character, no key and no game: `Wisp.exe --fake`.

### Commands and cost

Type `/` to see the commands: `/new`, `/sessions`, `/fork`, `/cost`, `/copy`, `/check`, `/settings`.

Every conversation is kept on your PC (`%APPDATA%\Wisp\sessions`). The clock icon, or `/sessions`, lists them: search, look at one without opening it, or open it and carry on. `/fork` continues in a copy, and the small branch icon under any answer forks from that point, so you can try another direction without losing the original. The line above the input shows the tokens and dollars this conversation has used so far; `/cost` gives the breakdown. The price is Wisp's estimate from Anthropic's list prices, and your Anthropic bill is the final word.

### Things to know

- Wisp sees your character **as of your last logout**. Log out and back in to refresh it.
- While the addon is enabled, Ctrl+V opens its inbox instead of toggling nameplates.
- `/wisp save` builds the snapshot on demand and tells you what it found. It still reaches disk only at logout.
- If the window fails to open, the reason is written to `%APPDATA%\Wisp\wisp.log`.

### From source

`pip install -r requirements.txt`, then `python -m wisp`. The exe is built by `.github/workflows/build.yml`.

## What has and has not been tested

Tested without the game:

- The addon, run under real Lua 5.1 (the version the game uses) and under a Lua 5.3 VM, against a faked game, Questie and TomTom.
- The whole loop with a scripted model: snapshot → question → card → the card's message pasted back into the addon → TomTom receives the waypoint. `npm install && python3 tests/test_mvp.py`
- The real Anthropic SDK against a local stand-in for the API: the request Wisp builds is accepted, and text, tool calls, several rounds and a rejected key are all read correctly. `python tests/test_anthropic.py`
- **On Windows, by the build workflow, every time the exe is built:** both test files, then the real window — from source and from `Wisp.exe` itself — driven through a question, a card and a Copy, plus saving to the credential store.
- The interface in headless Chrome, every state, light and dark, at three sizes.
- The game functions and Questie record fields the addon uses, checked against Blizzard's Forever interface source and Questie's source.

**In the real game:** the whole loop was run once on a player's PC in the Forever client with a real key (2026-11-10): the addon saved its snapshot with Questie's data, Claude answered a question about a quest, and the pasted card set the waypoint. That is one character on one PC, so expect rough edges. `checks/WispCheck` exists to test what the game actually provides; see [`checks/README.md`](checks/README.md).
