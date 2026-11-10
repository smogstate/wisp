# Wisp

An AI assistant for World of Warcraft: Forever. You chat with it in a small window beside the game; it knows your character and quest log, and hands waypoints to your addons.

The design lives in [`wiki/`](wiki/Home.md). What exists today is a first working slice.

## What works now (MVP)

Ask about a quest in your log — where to hand it in, what comes next in the chain — and get an answer plus a waypoint card to paste into the game, where TomTom sets it.

- **`addon/Wisp`** — saves your character, quest log and completed quests when you log out. For each quest it copies out what Questie knows: who starts and ends it, where they stand, the next quest in the chain. Runs pasted commands.
- **`wisp/`** — the overlay: a chat window, Claude with your key, and three tools over the saved snapshot.

Wisp never sends input to the game and never reads its memory. See [Red Lines](wiki/Red-Lines.md).

## Run it

You need the game on Windows, an API key for Claude (Anthropic), Kimi (Moonshot) or OpenRouter, and the Questie addon. TomTom is recommended; without it Wisp uses the built-in map pin. With the **DungeonJournal** addon installed, Wisp also knows every dungeon: ask *what am I missing for the Deadmines?* or *what drops in Scholomance?* **No Python is needed.**

1. **Download [Wisp.exe](https://github.com/smogstate/wisp/releases/download/mvp/Wisp.exe)** and run it. It is not signed, so Windows SmartScreen will warn: choose *More info*, then *Run anyway*. An antivirus may also ask, as it can with any small unsigned program.
2. **Settings** opens by itself the first time.
   - *Game folder*: Wisp looks in the usual places; otherwise browse to your `World of Warcraft` folder.
   - The **addon installs itself** once Wisp knows the game folder, and updates itself with each new `Wisp.exe`. It is one package; there is nothing else to download.
   - *API keys*: paste a key for Anthropic, for Moonshot (Kimi), for OpenRouter, or any of them. They are kept in the Windows credential store on this PC.
   - *Answers come from*: which of them answers, and which of its models (type to narrow the list; OpenRouter has hundreds, and Wisp offers the ones that can use its tools). `/model` opens this. Changing the provider starts a new conversation, since one provider cannot read the other's history; the earlier conversation is kept.
3. **Start the game** (or restart it, so it sees the new addon). On the character screen, check that Wisp is ticked under AddOns. Log in, then **log out once**. The addon writes your character and quests to disk at logout.
4. In Settings press **Check setup**. Every line should say `ok`; a line that says `FAIL` tells you what to do.
5. **Ask**, for example: *where do I hand in my wolf quest, and what comes next?*

**Professions.** Open your profession window once in the game (Wisp says in chat that it read the recipes), then log out. Now ask *what should I craft to level tailoring, and what do I need to buy?* Wisp counts the materials, takes off what is in your bags, prices the rest from your own **Auctionator** scans, and gives you a card that creates the shopping list in Auctionator. It knows the recipes your character has; the bank is not counted.

**What is this for, where do I get it.** Ask about any item you carry, craft with or need for a quest: Wisp shows which of your recipes use it, which quests ask for it, which boss drops it, who sells it and what drops it, with a waypoint to the seller.

**Gathering.** With the **GatherMate2** addon, ask *where is the most Peacebloom?* or *a route for Copper Vein in Elwynn*: Wisp ranks the zones by the nodes GatherMate2 has on record and makes a route you paste into the game, one TomTom waypoint per stop.

**Research.** Type `/research` and a real question: *what should I buy now that will be worth more later?*, *what are my best ways to earn gold?* Wisp works through your own data, then searches and reads the web (with a Claude key; 8 searches at a cent each and 6 pages at most), and ends with a report: short points, next steps, numbered sources you can click. Reports are kept; `/notes` opens the folder.

**Gold and prices.** Scan the auction house with Auctionator as you normally do and log out. Wisp keeps every scan day for good (Auctionator itself forgets after three weeks), so you can ask *what is cheap right now?*, *what in my bags is worth selling?*, *how has Silk Cloth moved, and where is it heading?* Prices are your own scans: the lowest listing of the day, not a sale.
6. **Copy** on the card, then in the game press **Ctrl+V twice**: the first opens Wisp's inbox, the second pastes. `/wisp` opens the inbox too.

To see the interface with a sample character, no key and no game: `Wisp.exe --fake`.

### Commands and cost

Type `/` to see the commands: `/new`, `/sessions`, `/fork`, `/cost`, `/copy`, `/check`, `/settings`.

Every conversation is kept on your PC (`%APPDATA%\Wisp\sessions`). The clock icon, or `/sessions`, lists them: search, look at one without opening it, or open it and carry on. `/fork` continues in a copy, and the small branch icon under any answer forks from that point, so you can try another direction without losing the original. The line above the input shows the tokens and dollars this conversation has used so far; `/cost` gives the breakdown. For Claude the price is Wisp's estimate from Anthropic's list prices; your bill is the final word. For Kimi only tokens are shown: Wisp has no verified price list for it yet.

### It remembers your character

Each time you log out, Wisp keeps that snapshot of your character on your PC (`%APPDATA%\Wisp\snapshots`). When you come back, an empty conversation shows **Since last time**: levels gained, quests finished, professions raised, money earned. `/recap` shows it again, and you can ask things like *what did I get done this week?*

### The window

Wisp's window has no title bar: drag it by its header. It turns see-through when you are not using it, so the game shows behind it, and is solid again as soon as the mouse is over it or you type. **Collapse** it to a small glowing wisp with the header button, Esc, or **Ctrl+Shift+Space** from anywhere, including inside the game; click the wisp to bring it back. The wisp breathes while Wisp is thinking and shows a mark when an answer or instructions are waiting. Theme (dark, light, or following Windows) and transparency are in Settings, and as `/theme` and `/opacity 70`. If the see-through window misbehaves on your PC, Settings has *Use an ordinary window*.

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
