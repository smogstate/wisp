# Addon landscape

> Status: research 2026-11-10, from Forever addon roundups, CurseForge pages and Questie's repository. Nothing here was tried in the game.

What players already run in Forever, and what a language model adds on top. Asked by Denis: "check popular addons — what can we do better with an LLM?"

## The finding that matters most

**Several addons already do what Wisp's worked scenarios describe**, and do it with real data and an arrow on screen:

- *Dungeon Quests Forever* — every dungeon's quest chains, your position in each, an arrow to the next step.
- *SkillUp Forever* — each recipe's skill-up chance and material cost, leveling routes, shopping lists.
- *DungeonJournal*, *AtlasLoot Classic Forever* — bosses and loot.
- *Shortest Path Forever* — routes with flights and boats.

So Wisp should not rebuild them. A language model cannot beat a database at being a database. **Wisp's place is above the addons: one conversation that reads what they know, reasons across them, and tells them what to do.**

And one of them fixes the Wowhead problem from [Checks](Checks.md): **Questie has a Forever build** (its manifest lists interface 16001) and an open database another addon can query in game — quests with their chains, NPCs with positions, objects, items, and drop rates (`QuestieDB.GetQuest`, `GetNPC`, `GetItem`, `GetItemDroprate`). Its Forever work is still in progress, and quests new in Forever are thin: another addon built on the same data reports no coordinates for them.

## What players run

| Addon | What it does | How Wisp relates |
|---|---|---|
| Questie | Quest givers, objectives and turn-ins on the map; a searchable database | **Read**: quests, chains, NPC positions, drop rates |
| Dungeon Quests Forever | Dungeon quest chains with tracking and an arrow | Leave it to do its job; Wisp explains and plans around it |
| RestedXP | A fixed speed-leveling route; paid beyond level 20 | Leave alone; proprietary |
| Shortest Path Forever | Travel routing | Possibly **command**: "route me there" |
| TomTom | Waypoint arrow | **Command**: waypoints ([Integrations](Integrations.md)) |
| DungeonJournal, AtlasLoot Classic Forever | Bosses, loot tables | **Read**: what drops where |
| SkillUp Forever | Skill-up odds, material costs, profession routes | **Read**, if it exposes its data |
| Auctionator | Prices, shopping lists | **Read** prices, **command** lists |
| Pawn | Marks gear upgrades from stat weights | **Read** its weights |
| Details! | Damage and threat meters | **Read** after a fight |
| Leatrix Plus, Bartender4, Threat Plates, Titan Panel, Baganator | Automation, bars, nameplates, info bar, bags | Help *configuring* them, nothing more |
| WeakAuras, Clique | Custom displays, click-casting | Help configuring; hand over an import string |
| Quest TLDR | Turns quest text into a checklist with locations; English only, rule-based | Wisp does this natively, in any language |
| What's Training?, GnomeLevelUp | What to learn at the trainer | Read, or answer directly from the spellbook |

## Where a language model is actually better

1. **One question across everything.** "What should I do tonight?" needs Questie's open quests, your dungeon chains, your profession, your gear gaps, prices and your two free hours. Each addon knows one of those. Nothing joins them.
2. **A plan that bends.** A route addon gives one path. "I skipped that zone", "my friend is three levels lower", "hardcore — nothing risky": a model re-plans; a fixed route cannot.
3. **Reading text.** Quest text into steps and hints, in Russian or German as well as English. The rule-based addon for this is English-only and breaks on unusual wording. It also covers quests new in Forever, where the databases are empty but the quest text is right there.
4. **Why, not only what.** Pawn says "upgrade". A model can say why, and when it is not: a set bonus you would break, a hit cap, the piece you replace next week anyway.
5. **Asking instead of browsing.** "Who sells this?", "what in Scholomance is an upgrade for me?" — one sentence instead of paging through a loot browser.
6. **Setting things up by talking.** Addons with a hundred options. "Set my bars up for healing", "why is this addon throwing errors?"
7. **After the fight.** The meter shows numbers; a model says what went wrong, in words.
8. **Memory.** No addon remembers your goals, what you decided last week, or where you stopped.
9. **Coming back.** "What is different from the vanilla I remember?" for a returning player.
10. **Writing.** Macros, a guild advert, a group-finder message, a translated reply.

## Where it is not

- **Being a database.** It invents quest ids and coordinates. It must read them from Questie or the game, never recall them.
- **Anything during combat.** Too slow, and Forever restricts it.
- **An arrow, a map pin, a bag window.** Addons draw; Wisp tells them where.

## How Wisp would use an addon's database

Wisp reads; it does not copy or bundle. The data stays in the addon the player installed.

| Way | How | Good | Limit |
|---|---|---|---|
| **In the game** | The Wisp addon calls the other addon's own functions (`QuestieDB.GetQuest(id)`), and saves what it got with the snapshot | Uses the addon exactly as its author exposes it; ordinary addon interplay | Reaches the overlay only at logout, so the Wisp addon must copy out what questions will need, ahead of time |
| **From disk** | The overlay reads the addon's data files in `Interface\AddOns` directly, on the player's own PC | Available at any moment, for any quest or NPC; no logout | Needs a reader for that addon's file format, which can change between versions. Unexamined |

Rules:

- **Only addons that expose their data on purpose**: Questie (open, moving to GPLv3), Auctionator and TomTom (documented APIs). Not the "all rights reserved" addons without a public API.
- **The player must have the addon.** Without it Wisp still works and simply knows less; it says so and names the addon that would help.
- **Never redistributed.** Nothing of another addon's data goes into Wisp's own files or the repository.
- **Forever gaps are inherited.** Where Questie has no data for a new Forever quest, Wisp falls back to the quest text and the game's own waypoint.

Whether another addon can really read Questie in the Forever client is one of the checks in `checks/WispCheck`.

## What this changes

- **Integrations become the core, and reading comes before commanding.** The list in [Integrations](Integrations.md) grows a "read" side: Questie first, then the loot, profession and gear addons.
- **Questie replaces the Wowhead database pages** for vanilla-era content. Wowhead tooltips and guides stay as supplements.
- **The worked scenarios get simpler.** "What am I missing for the Deadmines?" is Questie's data plus completed quests — or, with Dungeon Quests Forever installed, mostly an explanation of what that addon already shows.
- **A limit to state plainly:** Wisp reads another addon inside the game, so the data still reaches the overlay through the saved file. The Wisp addon must copy out what a question will need ahead of time.
- **Licences:** calling an installed addon's functions is ordinary addon interplay. Copying its data into Wisp's own files is redistribution and falls under that addon's licence; Questie is moving to GPLv3, and several Forever addons are "all rights reserved".

## Other AI assistants for the game (survey 2026-11-11)

Read from their own pages; none was run by us.

| Project | How data leaves the game | How answers reach the game | Worth noting |
|---|---|---|---|
| **wow-ai** / "AI Agents Chat" on CurseForge (chelinho139, ~180 stars) | A strip of coloured pixels in a screen corner, captured four times a second by its bridge | A pool of 200 load-on-demand addons: the bridge writes a reply into a free one and the game loads it from a timer; a sound file that plays or not says "ready" | Chat inside the game; runs the player's own agent CLIs (Claude Code, Codex…); routes with an arrow; macros with a Create button, checked against the game's limits |
| **WoW Eye** (TravinDSO) | Saved variables at reload or logout (`/eye sync`) | An inbox file read at reload | An MCP server; routes that advance by themselves on quest events; a compass bar and a beacon; learns NPCs, vendor stock, trainers and flight points as you play |
| **wow-agent** (Oighty) | Saved variables, `/agent sync` reloads | Chat only, in its own window | Watches the Screenshots folder and attaches the newest; looks a new zone up on the web |
| **Claude WoW Overlay** (BabyGrug) | Optional addon; screenshots by hotkey | Map marks | Runs on the Claude desktop app's CLI and the player's own subscription, no API key; remembers settled facts about the character |
| **WoWGPT** (CurseForge) | — | In-game window | Mostly answers from the game's own API without a model; personalities, a UI editor |

What Wisp could take, in order of value:

1. **Reload instead of logout.** `PLAYER_LOGOUT` fires on a UI reload too, so `/reload` already saves Wisp's snapshot. A `/wisp sync` that reloads is a five-second refresh instead of a trip to the character screen.
2. **The load-on-demand inbox** (wow-ai): cards reach the game at a click, no paste. Documented addon API only; what is loaded would be a data file Wisp wrote. Touches the red line about text run as Lua, so it needs Denis's decision.
3. **Screenshot with a question**: in the design since the start ([Architecture](Architecture.md)), never built. Watching the Screenshots folder needs no capture code at all.
4. **Learning by playing**: vendors' stock, trainers, flight points ([Game data](Game-Data.md) already plans it).
5. **Macros with a Create button**, **routes that advance by themselves**, **the player's Claude subscription instead of a key**, **an MCP server**.

Not taking: the pixel strip. It is the pixel beacon under another name ([Red Lines](Red-Lines.md)); the most used competitor ships it and says nothing about the game's terms.

Where Wisp is ahead of all of them: auction history and forecasts, investments, crafting plans, the whole quest and item lists, measured farming, a spending cap, Russian, one exe that updates itself (the others need Node, git or a CLI).
