# Integrations

> Status: candidate list, 2026-11-09. None built. Every API below must be checked in the Forever client before it is relied on.

Each integration is one Lua file registered through [Protocol](Protocol.md). Commands into an addon are live (by paste); data read from an addon is as of the last logout.

## Candidates

| Integration | Commands | Reads | Requires | Verified |
|---|---|---|---|---|
| Built-in map | pin on the world map | — | nothing | no |
| TomTom | waypoint | — | TomTom | **works in the game** (Denis, 2026-11-10) |
| Questie | — | quests, chains, NPC positions | Questie | **works in the game** |
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
- **Prices: at logout, from Auctionator's public API**, only for the items that matter: what is carried and what the recipes use and make. Each price comes with the number of days since Auctionator last saw the item. They are the player's own scans, so they are as fresh as the last visit to the auction house.

**What the model gets** ([Tools](Tools.md)): `recipes`, `craft_plan`, `shopping_list`. The rule that keeps it honest is the same as with coordinates: **the model never adds anything up.** It picks recipes and how many times; `craft_plan` does the sums in code — total materials, what an earlier step of the same plan supplies (bolts made, then used), what the bags cover, what is left to buy and what that costs. `shopping_list` takes the same plan and lists exactly the remainder, so the numbers on the card are the numbers of the plan.

**The card** creates the list through `Auctionator.API.v1.CreateShoppingList`, each line an exact-name search with a quantity. The command carries item ids, and the addon uses **the game's own name for each id** — so the list is right in a Russian or German client too. A name typed from outside is used only when there is no id, and `|`, `;` and control characters never pass.

**What it cannot do yet**

- **A full 1-to-300 path.** Wisp knows the recipes the window lists. In Forever that is the learned ones (the client hides unlearned recipes by default; the addon asks for all of them and takes what it gets). Recipes still to be bought from a trainer, and the skill at which each is learned, are not in the game's answer. The prompt tells the model to say what is missing rather than fill it in from memory.
- **How many crafts a yellow or green recipe needs.** The game gives the colour and the grey point, not the chances. Only orange is certain; plans say the rest is an estimate.
- **Vendor materials.** Thread and dye have no auction price. The plan lists them as "no auction price (a vendor may sell them)"; Wisp does not know vendor prices or where the vendor stands.
- **Colours go stale.** They are as of the last time the window was open. Past the grey point Wisp corrects them itself; otherwise the tool says at which skill the colour was read.

Not yet run in the game: the recipe calls (`GetRecipeInfo`, `GetRecipeSchematic`, and whether `GetAllRecipeIDs` exists there), the bag scan, and Auctionator's API from another addon.

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
