# Wisp

An AI assistant for World of Warcraft: Forever. You chat with it in a small window beside the game; it knows your character and quest log, and hands waypoints to your addons.

The design lives in [`wiki/`](wiki/Home.md). What exists today is a first working slice.

## What works now (MVP)

Ask about a quest in your log — where to hand it in, what comes next in the chain — and get an answer plus a waypoint card to paste into the game, where TomTom sets it.

- **`addon/Wisp`** — saves your character, quest log and completed quests when you log out. For each quest it copies out what Questie knows: who starts and ends it, where they stand, the next quest in the chain. Runs pasted commands.
- **`wisp/`** — the overlay: a chat window, Claude with your key, and three tools over the saved snapshot.

Wisp never sends input to the game and never reads its memory. See [Red Lines](wiki/Red-Lines.md).

## Run it

You need the game on Windows, Python 3.10 or newer, an Anthropic API key, and the Questie addon. TomTom is recommended; without it Wisp uses the built-in map pin.

1. **Addon.** Copy `addon\Wisp` to `World of Warcraft\_forever_\Interface\AddOns\Wisp`. Start the game, log in, then **log out once** — the snapshot is written to disk at logout.
2. **Overlay.**
   ```
   pip install -r requirements.txt
   set ANTHROPIC_API_KEY=your-key
   python -m wisp "C:\Program Files (x86)\World of Warcraft\_forever_"
   ```
   In PowerShell the second line is `$env:ANTHROPIC_API_KEY="your-key"`.
   If something is off, `python -m wisp --check "C:\...\_forever_"` tests the setup step by step: the game folder, the addons, the snapshot, the key, and one real call to the model.
3. **Ask**, for example: *where do I hand in my wolf quest, and what comes next?*
4. **Copy** on the card, then in the game press **Ctrl+V twice**: the first opens Wisp's inbox, the second pastes. `/wisp` opens the inbox too.

To see the interface without a key or the game: `python -m wisp --fake`.

### Things to know

- Wisp sees your character **as of your last logout**. Log out and back in to refresh it.
- While the addon is enabled, Ctrl+V opens its inbox instead of toggling nameplates.
- `/wisp save` builds the snapshot on demand and tells you what it found. It still reaches disk only at logout.

## What has and has not been tested

Tested on a development machine without the game:

- The addon, run in a Lua interpreter against a faked game, Questie and TomTom.
- The whole loop with a scripted model: snapshot → question → card → the card's message pasted back into the addon → TomTom receives the waypoint. `npm install && python3 tests/test_mvp.py`
- The interface in headless Chrome, every state, light and dark, at three sizes.
- The real Anthropic SDK (2.7.0) against a local stand-in for the API: the request Wisp builds is accepted, and text, tool calls, several rounds and a rejected key are all read correctly. `python tests/test_anthropic.py`

- The real pywebview window, opened on Linux and driven through a question and a Copy. That is the WebKit engine; Windows uses WebView2, which is Chromium, the engine of the headless run.
- The game functions and Questie record fields the addon uses, checked against Blizzard's Forever interface source and Questie's source.

**Not tested: the addon in the real game, what the real model answers, and the window on Windows itself.** `checks/WispCheck` exists to test what the game actually provides; see [`checks/README.md`](checks/README.md).
