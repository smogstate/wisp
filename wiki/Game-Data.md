# Game data

> Status: survey 2026-11-10, from Blizzard's interface source for Forever (client 1.60.1). **An interface being present does not prove Forever fills it with data** — the source is shared with retail, and it also documents retail-only systems. Every row marked "to verify" needs one look in the client.

What the Wisp addon can read from the game itself. This matters most since [Checks](Checks.md) found Wowhead's database pages closed to Wisp: the game is the one source with no third party and no terms to breach.

## About your character — certain

| Data | Interface |
|---|---|
| Name, class, specialization, level, gold | basic unit functions |
| Gear, with the full tooltip of every piece as data | `C_TooltipInfo`, `C_Item` |
| Talents, and **your current build as an import string** | `C_Traits.GenerateImportString`, `C_ClassTalents` |
| Spellbook | `C_SpellBook` |
| Professions with skill levels; known recipes with materials | `C_SkillInfo`, `C_TradeSkillUI` |
| Bags; bank when it has been opened | `C_Container`, `C_Bank` |
| Quest log with objectives and progress; completed quests | `C_QuestLog` |
| Reputations | `C_Reputation` |
| Macros, equipment sets, raid lockouts | `EditMacro`/`GetNumMacros`, `C_EquipmentSet`, `C_RaidLocks` |
| Guild information; the calendar | `C_GuildInfo`, `C_Calendar` |

## About the world — interface present, to verify

| Data | Interface | Replaces |
|---|---|---|
| **Quest lines on a map**: which chains exist, their quests in order, which are open to you | `C_QuestLine.GetAvailableQuestLines`, `GetQuestLineQuests`, `IsComplete` | Wowhead's quest lists and chains |
| **The game's own route to a quest objective** | `C_QuestLog.GetNextWaypoint`, `C_Navigation` | NPC coordinates, for anything quest-related |
| **Dungeon journal**: where each dungeon's entrance is, its bosses, each boss's loot and abilities | `C_EncounterJournal.GetDungeonEntrancesForMap`, `GetEncountersOnMap`, `GetLootInfoByIndex`, `GetSectionInfo` | Drop tables, boss notes |
| **Flight points**, with positions | `C_TaxiMap.GetAllTaxiNodes` | — |
| **Points of interest and quest hubs** on a map | `C_AreaPoiInfo` | Some locations |
| **Any item or spell by its id**, as a structured tooltip | `C_TooltipInfo.GetItemByID`, `GetSpellByID` | Wowhead's item and spell lookups, when the id is known |
| Item sets | `C_LootJournal` | — |
| The map tree: zones, their names, area names | `C_Map.GetMapInfo`, `GetMapChildrenInfo`, `GetAreaInfo` | Turning a zone name into the id TomTom needs |
| Rares and treasures near you | `C_VignetteInfo` | — |

## Learned by playing

The addon can record what you meet. This is the answer to the one thing the game does not hand over — where ordinary NPCs stand.

- **Every vendor, trainer, auctioneer, banker, flight master and quest giver you talk to**: its id and name, what it is, the map and your position at that moment. After a few sessions Wisp knows your world: "the tailoring trainer you used is in Stormwind at 53, 44".
- **What a vendor sells and a trainer teaches**, read while its window is open.
- **Auction prices** are already kept by Auctionator.
- It only knows places you have been. A new zone starts empty.

## More about you and your setup — interface present, to verify

| Data | Interface | Use |
|---|---|---|
| **Action bars**: what sits on every button | `C_ActionBar`, action functions | Macro and rotation help that knows your real bars |
| **Key bindings** | `C_KeyBindings` | "Which key is free?", binding advice |
| **Installed addons**, and which are enabled | `C_AddOns` | Wisp finds its integrations by itself; addon help |
| Currencies, achievements, titles | `C_CurrencyInfo`, `C_AchievementInfo` | Goals and progress |
| Hunter pets and the stable | `C_StableInfo`, `C_PetInfo` | Pet advice |
| Explored areas, known flight points | `C_MapExplorationInfo`, `C_TaxiMap` | "Where haven't I been?", route planning |
| Honor and battleground results | `C_PvP` | PvP goals |
| Quest rewards before you choose | `C_QuestInfoSystem`, `C_QuestOffer` | "Which reward should I take?" |
| Game settings | `C_CVar` | Settings advice |

## Other people and the economy — interface present, to verify

| Data | Interface | Use |
|---|---|---|
| **Auction house listings**, when the window is open | `C_AuctionHouse` | Prices beyond what Auctionator stored |
| **Guild roster, and which guild members can craft a recipe** | `C_GuildInfo.QueryGuildMembersForRecipe` | "Who in the guild can make this?" |
| Group and raid composition: classes, roles | `C_PartyInfo`, unit functions | Dungeon prep for the actual group |
| Group finder listings | `C_LFGList` | "Is anyone forming a group for this?" |
| An inspected player's gear and build | inspect functions, `C_Traits.GenerateInspectImportString` | Compare with someone you inspected |
| Friends, calendar events and invitations | `C_FriendList`, `C_Calendar` | Raid night planning |
| Mail: what is waiting | `C_Mail`, inbox functions | Auction sales history |

Data about other players is the one place to hold back: keep only what a question needs, and nothing from private chat.

## The event stream — what happens while you play

Beyond what the game *has*, an addon hears what *happens*. Recording it turns your own play into a personal database that nobody's terms cover.

| Event | Recorded | Becomes |
|---|---|---|
| You loot a herb, an ore vein, a chest | Node name, map, position | Your own gathering map |
| You loot a creature | Items, the creature, where | Your own drop notes |
| You accept or hand in a quest | The NPC, its position, the quest | Quest givers and turn-ins you have used |
| A craft gives a skill point — or does not | Recipe, your skill at that moment | Real skill-up odds, better than a calculation |
| You open a vendor or trainer | What it sells or teaches, prices | Vendor and trainer lists |
| You take a flight | From, to, duration | Travel times |
| You gain experience, gold, reputation | Amount, source, time | Experience and gold per hour, by activity |
| You die | Where, to what (from the recap) | Hardcore: your danger map |
| A rare or treasure appears nearby | What and where | Rare spawn notes |
| An auction sells or expires | Item, price | Your sales history |

This is what makes [snapshot history](Architecture.md) richer than a series of photographs: it records the session, not only where it ended.

## After a fight, never during one

- **What killed you**: `C_DeathRecap`. Useful for hardcore, and for "what went wrong".
- **The built-in damage meter's session summaries**: `C_DamageMeter`.

Both are read after combat and saved with the snapshot. Forever is reported to hide some combat values from addons; what remains readable is to verify.

## What the game cannot give

- **Where NPCs you have never met stand.**
- **Drop chances.** The journal lists loot, not odds.
- **Advice**: guides, best-in-slot lists, strategy, player comments.
- **Anything live to Wisp.** See below.

## How it reaches Wisp

- **Only through the saved file, at logout or login.** The addon cannot be asked a question while you play.
- So the addon **collects as you play and exports**: your character every time; reference data (quest lines per zone, journal loot, flight points) once, refreshed after a game patch.
- The client can write JSON and compress it (`C_EncodingUtil.SerializeJSON`, `CompressString`), so a large export is practical.
- Some of this data is fetched from the server on request and arrives a moment later, so the addon gathers it in the background over a session, not in one instant.
