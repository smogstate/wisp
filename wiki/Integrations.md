# Integrations

> Status: candidate list, 2026-11-09. None built. Every API below must be checked in the Forever client before it is relied on.

Each integration is one Lua file registered through [Protocol](Protocol.md). Commands into an addon are live (by paste); data read from an addon is as of the last logout.

## Candidates

| Integration | Commands | Reads | Requires | Verified |
|---|---|---|---|---|
| Built-in map | pin on the world map | — | nothing | no |
| TomTom | waypoint | — | TomTom | **works in the game** (Denis, 2026-11-10) |
| Questie | — | quests, chains, NPC positions; the items a quest asks for; who sells an item, what drops it, what holds it | Questie | quests **work in the game**; the item side is built and not yet run there |
| DungeonJournal | — | dungeons, their quests and prerequisites, bosses, loot, entrances | DungeonJournal | export tested against its real data files; not yet run in the game |
| Auctionator | create a shopping list **with quantities** | auction price and its age, per item | Auctionator | **built 2026-11-10**; API read in source (v343), tested against a stand-in of it; not yet run in the game |
| The game's profession window | — | every recipe it lists: colour, the skill it turns grey at, what it makes, materials | nothing | built; the client's API read in Blizzard's source, not yet run in the game |
| WeakAuras | open the import dialog with a string | — | WeakAuras | no |
| Talents | show or mark a build in game (how is open) | current talents | nothing | no |
| Macros | create or update a macro, **checked before it is offered** | existing macros, known spells | nothing | no |

TomTom is first. The rest are **open**: Denis decides which follow. **Auctionator is second** (Denis, 2026-11-10): shopping lists for crafting plans.

## DungeonJournal — built 2026-11-10

Denis pointed at it: "we can get a lot of data". It is Exehn's dungeon journal for Forever, and its tables are the curated, Forever-specific data nothing else has, the new dungeons included.

Measured by running Wisp's export against the journal's data files outside the game: **35 dungeons, 204 quests for one faction, 283 bosses, 1201 loot entries**; every dungeon has an entrance with a map position; 130 quests have a pickup position; 64 have prerequisites. The export adds about 250 KB to the saved snapshot.

- **Read in the game, not copied.** The journal keeps its tables in a shared namespace (`ForeverDungeonJournal_NS`). At logout the Wisp addon reads them there and adds, for this character, whether each quest and each prerequisite is done or in the log. Nothing of the journal is in Wisp's files or repository; the tests use a small made-up stand-in in the same shape.
- **Two tools** ([Tools](Tools.md)): `dungeons` lists every dungeon with level, place and quest counts for this character; `dungeon` gives one in full — entrance, each quest's status (done, in the log, available, locked behind an earlier quest, needing a level), who gives it and where, what must be done first and **the next step to take**, rewards, and every boss with its loot.
- Every position carries a ref, so the entrance and the next quest giver can go straight to a waypoint card.
- The worked example "what am I missing for the Deadmines?" is now answered from this, with Questie for the older quests' NPC positions.
- Without the journal the tools say so and name the addon.

Not used yet: its dungeon routes, in-dungeon quest pins, and its run log. Its licence was not found stated; Wisp only reads what the installed addon exposes in the game.

## Professions and Auctionator — built 2026-11-10

Denis: "where do I level, how many materials do I need, and a list for the auction addon, step by step".

**What Wisp reads, and when**

- **Recipes: while the profession window is open.** The game only gives a profession's recipes then, so the addon copies them at that moment into a second saved variable (`WispData`, per character and profession) and they ride along in every later snapshot. For each recipe: its skill-up colour, the skill at which it turns grey, what it makes and how many, and its materials. The addon says once in chat that it read them. Recipes of a linked, guild or NPC profession are ignored.
- **Bags: at logout.** One line per item with the total carried. **The bank is not read** (it is closed at logout); a plan says so.
- **Prices: at logout, for every item Auctionator has scanned** (Denis, 2026-11-10: "prices for items on auc"). Auctionator's public API answers per item and cannot list items, so the list of item ids, and only that, is taken from its price table (`Auctionator.Database.db`); each price and its age then come from the API. Pets and item-level variants, which it keeps under other keys, are left out. Rows are compact: `[item id, copper, days since seen or -1, name]`. They are the player's own scans, so as fresh as the last visit to the auction house.
- **Item names are learned while playing.** The game loads an item it has not shown yet only on request and a moment later, too late at logout. So after the auction house closes, and shortly after login, the addon asks for the missing names a few hundred at a time and keeps them in `WispData.items`. Until an item has a name it has a price but cannot be found by name; *Check setup* shows how many are named.
- **`auction_prices`** gives the model the price of any item by name (closest matches when the name is not exact), when it was seen, and what the player's own stock is worth. `recipes` now also says what the crafted item is listed for and the profit per craft.

**Price history — built 2026-11-10** (Denis: "historised data of auction we must have and must be able to navigate in it, big data to predict prices").

- **Where it comes from.** Auctionator keeps a day-by-day record of its scans for about three weeks: per item and day, the lowest and highest price it saw and the most it saw listed. At each logout the addon hands Wisp the days since the last hand-over, plus two for overlap, newest first and at most 60,000 rows so a logout never takes long. If that is more than fits (a full scan every day is about 10,000 rows a day), the older days follow at the next logouts, so within a few sessions Wisp has everything Auctionator still held: history starts three weeks deep.
- **Where it is kept.** One SQLite file on this PC, `%APPDATA%\Wisp\market.sqlite`, one row per market (realm and faction), item and scan day: low, high, listed. Nothing is ever pruned. It is fed only while the overlay sees the snapshot, so days pass through as long as Wisp runs at least every couple of sessions.
- **How the model navigates it.** It cannot read thousands of items, so code ranks and it asks: `market_scan` finds what stands out across the whole market (below or above its usual price, rising, falling, swinging, most listed), optionally only among the bags, the recipe materials or what the character crafts; `price_history` gives one item in depth.
- **And freely, in SQL** (Denis: "LLM must have a tool to navigate price db"). `market_query` runs one read-only `SELECT` the model writes, over a view `p` of this realm and faction: item, name, date, days_ago, low, high, listed. For whatever the two fixed tools do not cover: price bands, listing thresholds, comparing dates, weekdays, counts. The file is opened read-only, an authorizer refuses everything but reading (no writes, no `ATTACH`, no `PRAGMA`), a query that runs long is stopped, and at most 200 rows come back. The arithmetic stays in the SQL, so the model still adds nothing up itself.
- **Prediction, said honestly.** `price_history` returns the usual level (median), the direction (a robust slope, so one freak price does not bend it), the cheapest and dearest weekday once every weekday has been seen, and an estimate for next week: the recent level moved along the recent direction, as a range, with the number of scan days behind it and a confidence of weak, moderate or fair. The prompt forbids presenting it as a certainty. It is plain statistics on purpose; a better model is worth building once there are months of daily scans to test it against.
- **Shown as cards.** Called with `show`, `price_history` puts a chart card in the window (the day's lowest listing as a line, the usual level, the estimated range for next week, four figures above it, hover or arrow keys to read any day, a table of every scan day underneath) and `market_scan` a table card of the ranking. The model's text then only says what it means.
- **Limits.** A price is a lowest listing, not a sale. A day with no scan has no row. Thinly traded items show moves that mean nothing; the scan needs four scan days and a recent sighting before it ranks an item.

The one place Wisp looks past Auctionator's public API is its price table (`Auctionator.Database.db`), for the list of item ids and the per-day record, which the API does not offer. It is read in the game, on the player's PC, guarded so that a change of its shape costs only this feature.

**What the model gets** ([Tools](Tools.md)): `recipes`, `auction_prices`, `price_history`, `market_scan`, `craft_plan`, `shopping_list`. The rule that keeps it honest is the same as with coordinates: **the model never adds anything up.** It picks recipes and how many times; `craft_plan` does the sums in code — total materials, what an earlier step of the same plan supplies (bolts made, then used), what the bags cover, what is left to buy and what that costs. `shopping_list` takes the same plan and lists exactly the remainder, so the numbers on the card are the numbers of the plan.

**The plan is a card, not prose** (Denis, 2026-11-10: "must be more structured, not a wall of text"). When the model has chosen what to craft it calls `shopping_list` with those crafts, and the card lays the plan out: the steps numbered in order, each with its colour, its materials per craft and a warning where it applies (turns grey, not learned); a table of materials — need, in bags, buy, cost; the total; and the Copy button for the Auctionator list. Every number on it is the calculator's. The model's own text is then held to two short sentences the card cannot say, and for any other answer with steps the prompt asks for one short line per step instead of a paragraph. When the answer ends, the window scrolls to the top of the plan. If nothing needs buying the card still shows the plan, without a Copy button.

**The card** creates the list through `Auctionator.API.v1.CreateShoppingList`, each line an exact-name search with a quantity. The command carries item ids, and the addon uses **the game's own name for each id** — so the list is right in a Russian or German client too. A name typed from outside is used only when there is no id, and `|`, `;` and control characters never pass.

**What it cannot do yet**

- **A full 1-to-300 path.** Wisp knows the recipes the window lists. In Forever that is the learned ones (the client hides unlearned recipes by default; the addon asks for all of them and takes what it gets). Recipes still to be bought from a trainer, and the skill at which each is learned, are not in the game's answer. The prompt tells the model to say what is missing rather than fill it in from memory.
- **How many crafts a yellow or green recipe needs.** The game gives the colour and the grey point, not the chances. Only orange is certain; plans say the rest is an estimate.
- **Vendor materials.** Thread and dye have no auction price. The plan lists them as "no auction price (a vendor may sell them)"; Wisp does not know vendor prices or where the vendor stands.
- **Colours go stale.** They are as of the last time the window was open. Past the grey point Wisp corrects them itself; otherwise the tool says at which skill the colour was read.

Not yet run in the game: the recipe calls (`GetRecipeInfo`, `GetRecipeSchematic`, and whether `GetAllRecipeIDs` exists there), the bag scan, and Auctionator's API from another addon.

## The item graph — built 2026-11-10

Denis: "build a graph of items, what is used for what, for the LLM to navigate; bind recipes, items, quests".

An item is the hub; everything else hangs off it. One tool, `item_links`, takes an item name and returns everything one step away. The model walks on from there one item at a time, the way the kintale assistant walks the family tree.

| Link | From | Covers |
|---|---|---|
| recipe uses item, recipe makes item | the profession window | the character's own recipes |
| quest asks for item | Questie (`ObjectiveData` of type item) | quests in the log, and every dungeon quest of the DungeonJournal |
| boss drops item | DungeonJournal loot | every dungeon |
| vendor sells item, creature drops item, object holds item — each with places and refs | Questie (`GetItem(...).Sources`) | only items that matter to this character |
| item has a price | Auctionator | every scanned item |

- **Items that matter** are the ones carried, crafted with, crafted, or asked for by a quest, 500 at most. Asking Questie about every item in the game at logout would take too long. Of each kind of source the first few are listed (4 vendors, 5 creatures, 3 objects), with the total where Questie knows more.
- **Vendors close the gap in crafting plans**: a material with no auction price can be looked up, and its seller goes straight onto a waypoint card.
- **Built in memory** from the current snapshot (`wisp/graph.py`); nothing is kept between snapshots yet.

Open:

- **Sources are in Questie's order, not the best first.** Questie has drop rates (`GetItemDroprate`); they are not read yet, so "where do I farm this" names creatures that drop it, not the best spot.
- **Farming for gold needs sources for items the character has nothing to do with.** That takes a background pass over every priced item while playing, and keeping the result in the SQLite file.
- **How complete Questie's item data is for Forever is not known.** Its code has the fields; its repository lists Forever work still to do. To see in the game.
- Quest rewards are not linked; recipes of other professions and unlearned recipes are not known.

## Auctionator — what its API offers

Read from the source of Auctionator 343 on 2026-11-10 (a copy bundled in a Forever addon collection; the functions were not run in a client).

- **It supports Forever.** Its manifest lists interface `16001`, which is WoW Forever's interface number (game version 1.60.1).
- **`Auctionator.API.v1.CreateShoppingList(callerID, name, searchStrings)`** creates the list. A list with the same name is replaced. If the auction house is open, the search starts at once.
- **`Auctionator.API.v1.ConvertToSearchString(callerID, term)`** builds each search string from a table. The table has `searchString`, `isExact` and **`quantity`**, plus level, price and quality filters. So a list entry can say "Silk Cloth, exact, 80".
- **Reading:** `GetShoppingListItems`, auction price by item, the age of that price, vendor price and disenchant price. These can feed the saved snapshot for cost estimates.
- `callerID` is a string naming the calling addon: `"Wisp"`.

The command, as it would look in [Protocol](Protocol.md):

```json
{"do": "auctionator.shopping_list", "name": "Tailoring 150-200",
 "items": [{"id": 4306, "name": "Silk Cloth", "quantity": 80}, {"id": 2321, "name": "Fine Thread", "quantity": 20}]}
```

## Macros — help with verification

> Wanted by Denis, 2026-11-10: macro help, verified. The design below is proposed; no API here has been run in a client.

You describe what the button should do; the model writes the macro; **a checker in code verifies it before you ever see a Copy button**.

What "verified" covers, in three layers:

| Layer | Where | Checks |
|---|---|---|
| Syntax | Python tool `macro_check` | 255-character limit; every line is a known slash command; conditionals are well-formed (`[mod:shift,@mouseover,help,nodead]`); `#showtooltip` only on the first line; `/castsequence` reset syntax |
| Names | Python, through [Wowhead](Wowhead.md) and the snapshot | every spell and item named exists in Forever, spelled exactly; and, from the saved spellbook, that this character knows the spell |
| In game | the addon, at paste | spell names resolve in the client; a free macro slot exists; not in combat. The result is printed in chat |

A macro that fails layer 1 or 2 goes back to the model with the checker's message, to fix; it is not shown as a card. The card shows the macro text in full, line by line, with what each line does.

What verification cannot cover: **whether the macro does what you meant in a fight.** That takes pressing it. The answer says so.

Rules:

- **No `/run` or `/script` in a Wisp macro.** They execute Lua, and Wisp never hands the game code to run ([Red Lines](Red-Lines.md)). The checker rejects them.
- The command is `macro.save {name, body, scope}`: it creates the macro, or updates the one with that name. You drag it to a bar yourself.
- The addon saves existing macros and the spellbook into the snapshot, so "fix my macro" needs no retyping.

Open: the macro slot limits and the exact conditional list in the Forever client, to be read from the client; whether Forever restricts any macro commands the way Midnight restricts addons.

## Talent builds

> Wanted by Denis, 2026-11-10: help with talent builds, import strings. Proposed; the delivery into the game is open.

What Wisp can do with what was verified ([Wowhead](Wowhead.md)):

- **Find builds for your class and purpose** — raid, PvP, leveling — from the Forever guides, each with the guide's reasoning and a citation.
- **Read a build as talents, not as a code**: decode the points per tree into talent names and ranks. Needs the tree definitions, not yet located.
- **Compare with what you have**: "you are 3 points off the raid build: move 2 from X to Y". Needs the addon to save your current talents.
- **Say what comes next while leveling**: "your next five points", from the build's order.
- **Explain a talent** through the ordinary spell lookup.

Getting the build into the game — **open, depends on the Forever client**:

| Way | Needs | Status |
|---|---|---|
| The game's own Import box | A game-format string. Wowhead's guides carry Wowhead's own code, so Wisp would have to convert, using the node order the client provides | **the box exists** in the Forever source ([Checks](Checks.md)); conversion not designed |
| A link to the Wowhead calculator | nothing | works, but outside the game |
| The addon marks the next talents in the talent window | the addon reading the talent tree; display only, you click the points | to check in the client |
| The addon spends the points | an API that allows it. Spending points costs gold to undo, so never without a clear confirmation in game | to check; lowest priority |

## Adding one

1. Find the addon's public API (not its internals — those break on updates).
2. Write the integration file: `requires`, commands with `help` and `args`, optional `state`.
3. Check each call in the Forever client.
4. Add a row here with what was verified and on which client version.

## Rules for every integration

- A command does one thing the player could do by hand in the addon's own UI.
- Nothing protected: no casting, targeting, movement, or anything in combat that the game blocks for addons.
- If the required addon is missing, the integration is simply absent from the manifest; the model never offers it.
