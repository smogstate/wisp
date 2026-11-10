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
- `Wisp.exe` updates itself (`wisp/update.py`): the build publishes `version.json` (commit, size, SHA-256) with the release, and tests the rename-and-restart swap on Windows before publishing. Anything that changes how the exe is named, released or started must keep that working.
- Players get `Wisp.exe`, not Python: `.github/workflows/build.yml` builds, tests and publishes it as the `mvp` release on every push that touches code. `--smoke FILE` is the self-test it runs on the built exe. Nothing may require a command line or an environment variable.
- Three providers: Anthropic (its own SDK), Kimi and OpenRouter (the `openai` SDK on the shared OpenAI-shaped wire, `OpenAIChat`; OpenRouter reads limits, prices and abilities per model from its public catalogue). A chat object owns its history in its provider's format; a conversation never changes provider. Tests: `tests/test_anthropic.py`, `tests/test_openai.py`, each against a local stand-in API.
- The model never handles coordinates: locations carry refs, and `waypoint` accepts only refs. Keep that when adding tools.
- The model never adds up quantities or prices either: `wisp/crafting.py` does the sums (`craft_plan`), and `shopping_list` takes the same plan. Recipes are read by the addon while the profession window is open and kept in `WispData`.
- `wisp/gathering.py`: GatherMate2's nodes, zone rankings and routes (built in code, one ref for `waypoint`).
- `wisp/farming.py`: no addon has drop rates for ordinary loot (Questie's are quest items only), so the addon records the player's own looting (`WispData.loot`) and everything about farming is computed from that.
- `/research` is the only way the web is used: `answer(..., research=True)` raises the round budget, adds Anthropic's server-side web tools with hard `max_uses` caps, and offers `report`, whose sources are checked against the pages actually returned. Keep the caps hard and shown up front.
- `wisp/graph.py` is the item graph: items tie recipes, quests, bosses, vendors, drops and prices together, and `item_links` returns one step around an item. New data that mentions items should join it.
- Auction prices over time live in `wisp/market.py` (SQLite, `%APPDATA%\Wisp\market.sqlite`), fed by the per-day record the addon takes from Auctionator at logout. Rankings, trends and the estimate are computed there; the model only words them.
- `checks/` is throwaway code for milestone 0 (wiki/Checks.md), not part of the product.
- Does not use Loom.
