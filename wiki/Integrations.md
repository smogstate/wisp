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
| GatherMate2 | — | every gathering node it knows: profession, zone, name, places | GatherMate2 | **built 2026-11-10**; API read in its source; not yet run in the game |
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
- **Where it is kept.** One SQLite file on this PC, `%APPDATA%\Wisp\market.sqlite`, one row per item and scan day, one market shared by every character (Denis: "shared among all chars"): low, high, listed. Nothing is ever pruned. It is fed only while the overlay sees the snapshot, so days pass through as long as Wisp runs at least every couple of sessions.
- **How the model navigates it.** It cannot read thousands of items, so code ranks and it asks: `market_scan` finds what stands out across the whole market (below or above its usual price, rising, falling, swinging, most listed), optionally only among the bags, the recipe materials or what the character crafts; `price_history` gives one item in depth.
- **And freely, in SQL** (Denis: "LLM must have a tool to navigate price db"). `market_query` runs one read-only `SELECT` the model writes, over a view `p` of the shared market: item, name, date, days_ago, low, high, listed. For whatever the two fixed tools do not cover: price bands, listing thresholds, comparing dates, weekdays, counts. The file is opened read-only, an authorizer refuses everything but reading (no writes, no `ATTACH`, no `PRAGMA`), a query that runs long is stopped, and at most 200 rows come back. The arithmetic stays in the SQL, so the model still adds nothing up itself.
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

## Questie: every open quest — built 2026-11-10

Denis: "why don't we have access to the quests db? I can't ask, for example, what elite quests are available in the area". Until now Wisp asked Questie only about quests already in the log, and about dungeon quests.

- **Questie already works out which quests a character can pick up** — level, class, race, earlier quests — and draws its map icons from that list (`AvailableQuests.__availableQuests`). The addon exports that list at logout, 600 quests at most: level, required level, who gives each and where, the items it asks for.
- **Kind** comes from Questie's own tests: elite (the game's tag for a group quest), dungeon, raid, PvP; everything else is normal.
- **`available_quests`** ([Tools](Tools.md)): with nothing, how many quests are open in each zone and how many of them are elite and so on; with a zone (a name, or `here`), that zone's quests, **nearest first** when the character stands in it; with a kind, only that kind. Every quest giver has a ref, so "the elite quests here" can end in a waypoint card.
- **`quest_search`**: any quest at all, not only those in reach. About a minute after login the addon reads Questie's whole quest database (only the quests this character's race and class could ever take), a slice per frame so the game never stalls, and keeps it in saved variables of its own (`WispQuests`, `WispQuestsKey`). It is read again only when Questie's version, the game's language or the character changes; otherwise it simply stays in the saved file. Per quest: name, level, the level it needs, zone, kind, who gives it and takes it back (one place each), what comes before (`preQuestSingle`: any one; `preQuestGroup`: all) and after it, what closes it, and what it asks for. Wisp works out in code whether this character has done it, has it, can take it now, or what is missing, and walks the chain around it. Search by name (part of a name, misspellings, ё/е), id, zone, kind or level range.
- **Every item, the same way** (Denis, 2026-11-11: asked where to buy a cooking recipe and Wisp did not know). After the quests the addon reads Questie's whole item list into `WispItems`: every item somebody sells, something drops or something holds, with up to four vendors friendly to the character, the first three creatures and two objects, and how many there are in all. `item_links` answers for any item, and `recipes` with `search` finds a recipe of the character's professions, learned or not, with **how to learn it**: the game's own source text where the client has one (`C_TradeSkillUI.GetRecipeSourceText`), and the recipe item that teaches it ("Recipe: Herb Baked Egg", matched by the name after the colon) with who sells or drops it. Recipes of professions the character does not have are not known: the game lists a profession's recipes only to someone who has it.
- **Joined to the item graph**: an item an open quest asks for says so.

Limits:

- **It is Questie's view, with Questie's settings.** Quests far below the character's level are left out unless Questie is set to show them.
- **As of the last logout.** A quest picked up or finished since is still listed until the next one.
- The list is read from a field Questie keeps for itself, not a documented call; if a Questie version moves it, the tool says the list was not found.
- How complete Questie's quest data is for Forever's new content is not known. Not yet run in the game.

## Questing routes and gold from your own things — built 2026-11-10

Denis: "I am now interested in leveling, questing and earning gold with the auction house." Both rest on data Wisp already had; what was missing were the two views that put it to use.

**Questing**

- **Quest colours.** Every quest, in the log or open, gets the colour the quest log would give it for this character: red, orange, yellow, green, grey. The band below the character's level that stays green is the game's own number (`GetQuestGreenRange`), read at logout; without it, the game's table.
- **`questing_route`** lays a zone out as a route card. Three kinds of stop: quests to **hand in** (complete in the log, turn-in in this zone), open quests to **pick up** (not grey, not red; elite, dungeon and so on are marked), and quests to **go on** with (the game's own pointer to the next step). Two quests at one giver are one stop. The order is made in code: quest givers first, then the quests under way, each time the nearest, from where the character stands. One Copy sets a numbered TomTom waypoint for each stop. Fourteen givers and ten outings at most.
- **Where to level next**: `available_quests` with no arguments now says, per zone, how many open quests are worth doing and the span of their levels.

**Gold**

- **`gold`, view `bags`**: every carried item that can be sold (bound ones are left out), with its listing, its worth, what a vendor pays, and a plain verdict: above its usual price (sell), below (hold), a vendor pays as much (vendor it), nobody lists it. "Usual" is the median of the kept price history over 30 days, once there are three scan days.
- **`gold`, view `crafts`**: the character's learned recipes ranked by profit per craft at current listings, with the return on the materials and how many can be made from the bags. Recipes where the result or a material has no price are counted, not guessed.
- Both come as table cards.

Limits: the route is as of the last logout and knows the next step of a quest only where the game points to it; it is nearest-first, not the shortest walk. Gold figures are listings before the auction house's cut, the bank is not known, and nothing here knows what actually sold.

Not built, and the natural next steps: **experience per hour** and time to the next level (the addon would note each session), and **what really sold and for how much** (from the auction house's mail), which would turn "listed for" into "sold for".

## Leveling: experience per hour and what quests give — built 2026-11-10

For Denis's interest in leveling. Two things the route and the zone comparison lacked: how fast the character really levels, and what each quest is worth.

- **The addon measures gains.** On every change of the experience bar it adds the gain to the day's total, with the time between one gain and the next when under five minutes; a level gained is the rest of the old bar plus the new one so far. `QUEST_TURNED_IN` (verified in the Forever source) gives each hand-in's experience and money, so the share that came from quests is known. Kept for 60 days per character. The rested pool is read at logout.
- **`leveling`**: experience per hour over the period, **the time to the next level at that pace**, the rested bonus as a share of a level, the share from quests, and day by day; as a table card on request. A rate needs ten minutes of gaining behind it.
- **What a quest gives** comes from Questie's own reckoning (`QuestXP`, which adjusts for the character's level and knows about Forever): experience and money, for quests in the log and for open ones.
- **The questing route now says what it is worth**: each stop's quests with their experience, and on the card the experience to hand in now and the experience in the quests picked up, each as a share of a level. Zones are compared by the experience on offer in quests worth doing.

Limits: time is "time between gains", so a rate is a little generous and says nothing about travel. Quest experience is Questie's figure, not the game's; where Questie has none, the quest simply shows none. Not yet run in the game.

## Market depth: every listing, not only the lowest — built 2026-11-10

Auctionator keeps one number per item, the lowest price. Whether that is one item or five hundred, and what stands above it, is lost. The client has the rest, briefly.

- **Wisp sends the auction house nothing.** It reads what is already in the client after somebody else asked:
  - **After a full scan** (Auctionator's, which on the modern auction house loads every single auction: `ReplicateItems`), the addon walks the same list (`GetReplicateItemInfo`), a thousand auctions at a time so the game never stutters, and adds up, per item, how many are listed at each price for one.
  - **After a search for one item** (the player's, or a shopping list's: `COMMODITY_SEARCH_RESULTS_UPDATED`, `ITEM_SEARCH_RESULTS_UPDATED`), it reads that item's whole ladder, and which of the listings are the player's own.
- **Made small before it is saved**: per item the lowest price, then how many are listed up to 1%, 2%, 3%, 5%, 7.5%, 10%, 15%, 25%, 50% and 100% above it, and in all. A few numbers an item. Held for the session, handed over in the snapshot, kept by day in the price file.
- **`market_depth`** ([Tools](Tools.md)): the ladder of one item; the biggest block of listings; with a quantity, **what buying that many would cost at most** (each step priced at its top); with a price, **how many are listed for less**; and where the player's own auctions stand.
- **`my_auctions` now says how many stand in front**: not only "undercut", but "70 are listed for less than yours".

Limits: it is one moment, as old as the scan or the search, and listings change by the minute. A full scan cannot tell whose an auction is, so the player's own position comes only from a search. Auctions with no buyout are left out. The search events and the replicate list are read from the Forever source, not yet run in the game.

## The bank — built 2026-11-10

- **Read while its window is open, and kept**, since the bank is shut at logout. Forever's bank is the modern one: tabs, each a container, for the character and for the whole account (`C_Bank.FetchPurchasedBankTabData`, then the ordinary container calls; read from the game's own bank window code). The addon reads them when the bank opens and whenever a slot changes while it is open; with the window shut it reads nothing, so an empty answer can never overwrite what was seen.
- **Counted wherever stock matters**: a crafting plan takes from the bags, then the bank, and buys the rest (the plan card gains a Bank column when the bank supplies anything); recipe materials show what is banked; "what to sell" counts bags and bank together and says how many are banked; an investment's "do I still hold it" looks in both; item links show the banked count.
- **As old as the last visit.** Every answer that uses it says the bank is as it was when last opened; before the first visit it says the bank is not known.
- The account's shared tabs are counted as the character's stock; bound items are left out of what can be sold.

## Your own auctions — built 2026-11-10

- **Read, never asked for.** Forever uses the modern auction house interface (`C_AuctionHouse`). When the player looks at the Auctions tab, the game fires `OWNED_AUCTIONS_UPDATED`, and the addon copies what `GetOwnedAuctions` then holds: item, quantity, the price for one, time left, whether it sold, whether it has a bid. Wisp sends the auction house no query of its own, so it cannot get in the way of the game's limit on queries or of Auctionator's scans. The bidder's name is not kept.
- **`my_auctions`** sets each auction against the lowest listing from the player's Auctionator scans: still the lowest, or undercut and by how much; time left counted down to the logout; what sold and waits in the mail. As a table card on request.
- Limits: it is as fresh as the last look at the Auctions tab, and the lowest listing may be older or newer than that look. The lowest listing includes the player's own. Taken on trust from the modern interface: the price is for one.

## Auction house mail: what really sold — built 2026-11-10

Denis: "read buys and sells from auction house mail". Until now every gold figure in Wisp was a listing. The auction house's mail is the one place the game states what really happened.

- **Every auction ends in a mail with an invoice**: bought or sold, the item, what it went for, the deposit, the house's cut, the quantity (`GetInboxInvoiceInfo`, as the game's own mail window reads it; verified in the Forever source). The addon notes each one whenever the mailbox is open and keeps the note after the mail is gone.
- **Mails have no number of their own**, so one is told from another by what it says and by when it runs out (to the second, in practice). Several that say exactly the same and arrived together — five stacks bought in one go — are counted; if some are emptied before the next look, the count never goes down. "Your money is on its way" notices are not sales and are skipped.
- **When it happened** is worked out from when the mail runs out: an auction mail lives thirty days.
- **The other player's name never leaves the game's own file.** It is used only to tell mails apart.
- **The overlay keeps the deals for good** in the same file as prices and investments; the addon drops a note sixty days after its auction.
- **`auction_ledger`** ([Tools](Tools.md)): a summary (received, paid, the house's cut and its rate, the balance, the items that brought and cost the most), the sales, or the purchases, each with its mail's number. Real prices.
- **Investments from the mailbox.** `investments` now lists purchases that are not tracked and sales of tracked items that are not recorded. The model offers them once; on a yes it records one with `from_mail`, and the item, the quantity and the price come from the mail itself. **A sale is recorded at what came in**, the price less the house's cut, so "made" is what was really made. No mail is recorded twice.

Limits: a mail emptied before the mailbox was opened with Wisp's addon loaded is never seen. Auctions that ran out unsold leave no invoice, so lost deposits are not counted. A sale's item is known by name only, so it is matched to an item Wisp already knows.

Not yet run in the game.

## Investments — built 2026-11-10

Denis: "can we add an asset entity? I bought this item as an asset; I can see how it behaves, how much gold I made so far. Investments."

- **An investment is a record of trades the player reports.** "I bought 200 Silk Cloth at 3s each to sell later", "I sold 150 of them at 5s". The model calls `investments` with the item, the quantity and the price **exactly as the player gave it**, as text with its units (`3s 50c`, `1g 20s`), for one or for the whole lot. The price is read in code; a number without units is refused, since guessing between gold and copper would be a hundredfold mistake. With no price given, the day's lowest listing is taken and the trade is marked as assumed.
- **Everything else is worked out in code** (`wisp/assets.py`): how many are held, the average cost (a moving average over the buys), the worth at today's listing, the gain on what is unsold, what the sales made (each sale set against the average cost of what was held then), and the sum of the two: "made in all".
- **How it behaves**: for each position, the lowest and highest listing since it was bought, the number of scan days behind that, and the direction once there are enough of them. One position in depth comes with a chart: the price line, and the level that was paid.
- **A hint from the bags**: when fewer are carried than are recorded as held, the tool says so. The rest may be in the bank, at auction, or sold and not yet reported.
- **Kept for good** in the same SQLite file as the price history, shared by every character; a mistaken trade is removed by its number, a whole record by item. A purchase that a later sale rests on cannot be removed before the sale.
- **Cards**: a table of every position after any change and on request; the chart for one.

Limits: Wisp knows only the trades it is told about; it does not see purchases or sales in the game. Worth is the lowest listing before the auction house's cut, not a sale. An item has to be known to Wisp (carried, crafted, or seen at auction by Auctionator) before it can be recorded.

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

## Farming for gold — built 2026-11-10

Denis: "farming for gold next". The plan had been drop rates for every priced item from Questie. **That data does not exist**: Questie's drop tables (`Database/DropTables/dropDB.lua`) hold rates only for quest items, the ones in quest objectives, and have no branch for Forever at all. Nothing else installed has rates for ordinary loot either. So Wisp measures the player's own.

- **The addon notes every loot window.** For each slot the game says which corpses or nodes it came from (`GetLootSourceInfo`), so three corpses looted at once are told apart, and a corpse opened twice counts once. Added up per day, zone and source — a kind of creature, a kind of node: how many were looted, how many of each item came out, the coins (the purse before and after the window), and **time spent looting**: the gaps between one loot and the next when under five minutes. Kept for 60 days in `WispData.loot`, per character.
- **Names.** A creature's name is taken from the target while looting, else from Questie at logout. A node's name is the target of the gathering spell just cast, which is the same name GatherMate2 uses.
- **Worth** is the lowest auction listing where there is one, else what a vendor pays (from Auctionator's own record of vendor prices).
- **`farming`** has three views ([Tools](Tools.md)), each with a table card:
  - `spots`: zones the character has farmed — time looting, coins, loot worth, **gold per hour** once there are ten minutes behind it.
  - `sources`: each creature and node looted — **the measured rate of every item** and the worth per loot.
  - `gathering`: GatherMate2's nodes priced by zone. What a node gives is the player's own measured average where they have gathered it (so veins get their ore, stone and gems right), else one of the item that shares its name.

Limits, all stated by the tool:

- **Only places the player has farmed.** For anywhere else there is no measurement; the model is told to say so and to offer a research run for what other players report.
- **Gold per hour is a little generous**: travel and long fights between loots are partly left out.
- **Worth is listings, not sales**, and selling a stack at once pushes a price down.
- **Gathering worth is "every known node once"**: a way to compare zones, not an hourly rate.
- Corpses with nothing on them are never seen, so "per loot" means per corpse that had loot.
- The loot calls (`GetLootSourceInfo`, the format of unit ids) are read from general knowledge of the client, not from Forever's source. Not yet run in the game.

## GatherMate2 — built 2026-11-10

Denis: "we need gatherer db integration, GatherMate". GatherMate2 supports Forever (its manifest lists interface `16001`; its licence file is GPL 2). It records every herb, ore vein, fishing pool and chest the player gathers from, and its companion GatherMate2_Data can import a ready-made set.

- **Read at logout, through its own functions**: `GetNodesForZone`, `GetNameForNode`, `DecodeLoc`, over the professions in `db_types`. Only the list of zones is taken from its saved tables (`GatherMate2HerbDB` and the like), which is where it keeps them. Everything it knows goes into the snapshot, up to 80,000 nodes: per profession and zone, each node's name and its places.
- **`gathering`** is the tool. With nothing: what is on record, by profession. With a node: the zones it is known in, richest first. With a zone: what that zone holds. With both: **a route**.
- **A route is made in code.** Nodes within 2.5 map units of each other become one stop, the 20 richest stops are kept, and the walk goes from where the character stands (when in that zone) to the nearest stop, then the nearest after that. The model gets one ref; `waypoint` turns it into one card line and a TomTom waypoint per stop.
- **It joins the item graph.** A herb's node has the herb's own name; an ore's vein is the object Questie says holds the ore. So `item_links` on Copper Ore names Copper Vein with its richest zones, and the model walks on to `gathering` for the route.
- Node and item names are the client's own, so the join works in any game language.

Limits:

- **A node is where something can appear, not that it is there now.** And a zone the player has not gathered in looks empty unless data was imported.
- **The route is nearest-neighbour**, good for following an arrow, not the shortest possible loop.
- Whether GatherMate2_Data has nodes for Forever's zones is not known.
- Not yet run in the game.

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
