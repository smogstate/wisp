# Wisp

AI assistant for World of Warcraft (WoW Forever first): a Python overlay beside the game plus a small Lua addon.

**The wiki is the source of truth.** Read `wiki/Home.md` first; every product
and architecture decision lives there. Update the wiki when decisions change.

## Quick facts

- Two halves: overlay in Python on Windows (chat, LLM, tools, clipboard), addon in Lua (runs pasted commands through other addons).
- Red lines (wiki/Red-Lines.md): never send input to the game, never touch game memory, never run pasted text as Lua. "No account risk, never."
- The player never types `/reload`; chat never involves copy-paste. Paste is only for commands into the game.
- Commands use the extensible `WISP1` protocol; one Lua file per addon integration (wiki/Protocol.md).
- BYOK: Anthropic, Kimi, OpenRouter behind one provider contract modelled on kintale's (wiki/LLM-Providers.md). Tools return validated structures, not pages (wiki/Tools.md).
- No live data out of the game except a screenshot. Do not reintroduce the pixel beacon.
- Code so far is the MVP: `addon/Wisp` (Lua) and `wisp/` (Python, stdlib server + Anthropic SDK + pywebview). Run the tests with `npm install && python3 tests/test_mvp.py`; they need Node for a Lua VM (or `WISP_LUA=/path/to/lua5.1`, which is the game's Lua version), no game and no key. `python -m wisp --fake` shows the interface with a scripted model.
- The model never handles coordinates: locations carry refs, and `waypoint` accepts only refs. Keep that when adding tools.
- `checks/` is throwaway code for milestone 0 (wiki/Checks.md), not part of the product.
- Does not use Loom.
