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

**Spending limit.** Wisp stops asking once a day's use has cost $5; change the amount, or set 0 for no limit, in Settings. The line above the input shows what today has cost. A long research run is stopped between two requests when it crosses the limit. Kimi's prices are not known, so Kimi's use is not counted.

**Updates.** Wisp keeps itself current without touching `Wisp.exe`: a newer version of its code (about a hundred kilobytes) is downloaded into its own data folder, checked and tried, and used the next time you open Wisp, or right away if you press *Restart*. Nothing appears next to the exe. Settings has a switch to turn this off. The addon is updated along with it; restart the game to load a new addon.

**What to do now.** *What should I do here?* gives a route card for your zone: where to hand in, what to pick up (grey and out-of-reach quests left out), which quests to go on with, in an order to walk them, with a numbered waypoint for each stop. *Where should I level next?* compares zones by the quests worth doing at your level.

**How fast you level.** *How fast am I leveling?* and *when do I hit the next level?* are answered from your own play: experience per hour, time to the next level at that pace, your rested bonus, and how much came from quests. The questing route shows what each quest gives and what the whole walk is worth as a share of a level.

**What to sell.** *What should I sell?* lists what your bags would fetch and says for each item whether to sell now, hold, or vendor it. *What can I craft for money?* ranks your recipes by profit at today's listings.

**Investments.** Tell Wisp what you bought to sell later: *I bought 200 Silk Cloth at 3s each as an investment*. It keeps the position and shows what you paid, what it is listed for now, how the price has moved since, and what you have made, both on what you sold (*I sold 150 at 5s*) and on what you still hold. *How are my investments doing?* shows them all; asking about one shows its chart against what you paid.

**Your auctions.** Look at the Auctions tab once while at the auction house; then *what do I have listed?* and *have I been undercut?* show each auction against the lowest listing, with the time left and what has sold.

**What really sold.** Open your mailbox as usual; Wisp's addon notes every auction mail. Then *what did I sell this week?*, *how much did I make at the auction house?*, *what did I pay for that?* are answered from real sales and purchases, with the house's cut. Purchases that look like investments are offered for tracking, and a sale of something you track is recorded at what actually came in.

**Quests around you.** *What quests are open here?*, *what elite quests can I do?*, *where should I quest next?* Wisp reads the list of quests Questie has open for your character, with levels and quest givers, and can put the givers on a waypoint card.

**Professions.** Open your profession window once in the game (Wisp says in chat that it read the recipes), then log out. Now ask *what should I craft to level tailoring, and what do I need to buy?* Wisp counts the materials, takes off what is in your bags, prices the rest from your own **Auctionator** scans, and gives you a card that creates the shopping list in Auctionator. It knows the recipes your character has, and counts your bank once you have opened it with Wisp's addon running.

**What is this for, where do I get it.** Ask about any item you carry, craft with or need for a quest: Wisp shows which of your recipes use it, which quests ask for it, which boss drops it, who sells it and what drops it, with a waypoint to the seller.

**Gathering.** With the **GatherMate2** addon, ask *where is the most Peacebloom?* or *a route for Copper Vein in Elwynn*: Wisp ranks the zones by the nodes GatherMate2 has on record and makes a route you paste into the game, one TomTom waypoint per stop.

**Farming.** Wisp's addon notes what you loot: what, from what, where, and how long it took. After some play, ask *where do I make the most gold per hour?* or *what does this creature really drop?* The rates are your own measurements; nobody has drop rates for ordinary loot. *What are the gathering nodes here worth?* prices GatherMate2's nodes with your auction prices.

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
