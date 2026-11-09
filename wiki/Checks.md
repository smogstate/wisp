# Checks (milestone 0)

> Run 2026-11-10 from the development machine. Desk checks and network probes only: **nothing here was run inside the game client or with a provider key.** What still needs that is listed at the end.

## 1. Wowhead from Python — changes the plan

Chrome on the development machine reads everything on Wowhead. A Python client on the **same machine** does not, so the difference is the client, not the network or the country.

| Request | Plain Python, honest client name | Notes |
|---|---|---|
| `nether.wowhead.com/tooltip/…` (items, spells, quests, NPCs with coordinates) | **200** | The endpoint Wowhead's tooltip widget uses on other sites |
| `/forever/guide={id}` | **200** | Guides, with their builds |
| `/forever/search/suggestions-template` | **200** | But `robots.txt` disallows `/search` for every robot |
| `/forever/npc=…`, `zone=…`, `quest=…`, `skill=…` | **403** | The database pages. Refused with and without ordinary `Accept` headers |
| Any page, Python's default client name | 403 | |
| Any page, a Chrome client name with nothing else browser-like | 403 | A fake browser name is refused |

`robots.txt` also names AI crawlers (among them `anthropic-ai`, `ClaudeBot`, `GPTBot`) with `Disallow: /`.

**What this means.** Wowhead deliberately keeps non-browser clients off its database pages. Wisp will not pretend to be a browser to get around that. The pages behind the block are exactly the ones the worked scenarios need: a zone's quest list, a quest's chain and giver, a profession's recipes, drop tables.

Still open to a plain client: tooltips (so "what is this" and "where is this NPC" work), guides, and — against the robots file — search.

**Wowhead's terms**, read 2026-11-10 ([Fanbyte terms](https://corp.fanbyte.com/legal/terms), last updated 2025-05-14; Wowhead's own `/tos` page points there):

- Section 8 forbids accessing the site or downloading its content with robots, crawlers, data-mining tools "or the like", or with any software, tool or agent — **except generally available web browsers** and what the operator itself provides. It also forbids datamining, and framing or mirroring the site without written consent.
- Section 4 forbids circumventing any technical protection, and allows APIs "in the manner described" by their documentation.
- AI is not mentioned in the terms; the robots file is where it appears. That file shuts out AI *training* crawlers entirely, but **allows Claude's search agent** (`Claude-SearchBot`) on most of the site with a one-second delay.
- Contact for Wowhead: feedback@wowhead.com.

**So a "real browser" inside Wisp does not solve it.** It would work technically. But a browser engine driven by Wisp to pull data out of pages is still a tool downloading content, and showing Wowhead inside the overlay is framing — both against section 8. The exception is a person using a browser, not a program using one.

**Options, for Denis to choose:**

| Option | Gives | Cost |
|---|---|---|
| A. Use only what is open | Item, spell, quest and NPC lookups with coordinates; guides | No zone quest lists, chains, recipes or drops from Wowhead |
| B. Read pages through the overlay's own browser view | Everything Chrome can read, one page per question, loaded as a browser loads it | **Against Wowhead's terms** (section 8: a tool downloading content, and framing) — ruled out below |
| C. Get the data from the game instead | Recipes with materials, quests on the map, the game's own quest waypoints — first-hand, through the addon | Only what the client exposes; arrives with the snapshot, not live |
| D. Another source | An official Blizzard data API or another Forever database, if one covers this and allows it | Unresearched |
| E. Ask Wowhead | A clear answer, perhaps an allowed route | Slow, may be no |

**Recommended (2026-11-10), awaiting Denis:**

1. **The game first** (C): everything the client exposes comes through the addon — recipes and materials, quests, completed quests, the game's own quest waypoints. No third party involved.
2. **Wowhead tooltips for lookups** (part of A): the endpoint behind Wowhead's public tooltip script. It is the one interface Wowhead offers to other software; using it from a desktop tool instead of a web page is close to, not exactly, its documented use, so the email below should ask about it.
3. **Deeper Wowhead content through the model's own web search**, limited to `wowhead.com`: quest lists, chains, drops, guides. It goes through the provider's search agent, which Wowhead's robots file allows. The price: page text, not Wisp's structures, and it depends on the provider having search.
4. **"Open on Wowhead" links** on every card, so you can read the page yourself in your browser.
5. **Email Wowhead** (E) describing Wisp as a personal tool and asking what is allowed. A yes brings the structured functions back.
6. **Look for a source that permits reuse** (D) for what is still missing.

Not doing: a hidden browser, a fake browser name, or direct use of the search endpoint the robots file closes.

### If Wowhead cannot be used at all

Asked by Denis, 2026-11-10. The Forever source shows the client carries more reference data than expected, so less is lost than it first looks. Whether each of these is filled in for Forever needs a look in the client.

| Feature | Without Wowhead | What can stand in |
|---|---|---|
| Knowing your character, history, recap, planner | **No loss** | — |
| Macros with verification | **No loss** | Spell names come from the spellbook |
| Gold and Auctionator lists | **No loss** | Prices and items come from the game |
| Chat help, addon help | **No loss** | — |
| "What is this item or spell?" | Lost for things you do not own or see | A screenshot of the in-game tooltip; the client's tooltip data for ids the addon is told to save |
| "What am I missing for this dungeon?", quest chains | Lost as designed | The client has a quest-line interface (`C_QuestLine`) and the quests available on a map — possibly enough; unverified |
| Dungeon loot, "where does this drop?" | Lost as designed | The client has the dungeon journal (bosses and loot); whether Forever fills it is unverified |
| Profession leveling path | Partly lost | Recipes and materials from the game; the tested path and skill thresholds from a guide on another site |
| "Where is this NPC?" | **Lost** | Quest objectives only, through the game's own quest waypoints. Vendors, trainers and auctioneers have no in-game source |
| Guides, builds, best-in-slot lists | Lost from Wowhead | Other guide sites through the model's web search, each with its own terms |
| Player comments and tips | **Lost** | Nothing equivalent |
| Wowhead links on cards | Kept | A link is not access |

**The pattern:** what is about *you* survives untouched. What is about *the game world* moves to the game client where it has the data, and to other sites where it does not. The real losses are NPC locations outside quests, and the comments.

**A limit on the in-game route:** the addon cannot be asked a question while you play. Anything it provides must be saved in advance and arrives with the snapshot. Reference data such as loot tables would be exported once per game patch, not looked up on demand.

## 2. The Forever client's addon API — good news

Read from Blizzard's own interface source for Forever (the `forever` branch of the public mirror, client **1.60.1**, interface **16001**). Presence in the source is strong evidence, not a run.

| Need | In the Forever source |
|---|---|
| Decode pasted messages | `C_EncodingUtil.DecodeBase64`, `DeserializeJSON`; also `SerializeJSON` and compression — so the snapshot can be written as JSON |
| Completed quests | `C_QuestLog.GetAllCompletedQuestIDs` |
| Quest log | `C_QuestLog.GetInfo`, `GetNumQuestLogEntries`, `GetQuestObjectives` |
| Position and maps | `C_Map.GetBestMapForUnit`, `GetPlayerMapPosition`, `GetMapInfo`, `GetAreaInfo`, `GetMapChildrenInfo` |
| Built-in map pin | `C_Map.SetUserWaypoint`, `CanSetUserWaypointOnMap`, `C_SuperTrack.SetSuperTrackedUserWaypoint` |
| Professions and recipes | `C_TradeSkillUI.GetRecipeInfo`, `GetRecipeSchematic`, `GetProfessionInfoBySkillLineID`. No `GetAllRecipeIDs`: how to list recipes needs a look in the client |
| Spellbook | `C_SpellBook.GetSpellBookItemInfo`, `IsSpellKnown` |
| Bags | `C_Container.GetContainerItemInfo`, `GetContainerNumSlots`; a `C_Bank` namespace exists |
| Macros | `EditMacro`, `GetNumMacros`; limits **120** account-wide and **30** per character; **255** characters. `CreateMacro` was not in the file read — to confirm in the client |
| **Talent import** | **Yes.** `C_ClassTalents.ImportLoadout`, `ViewLoadout`, and the game's own import dialog. Forever uses the modern talent system |

The talent import string is documented in the source: a base64 bit stream holding the specialization and, per tree node, whether and how far it is taken, in the order the client lists the nodes. Wowhead's guide code is a different format, so a build from a guide needs converting, and the node order comes from the client.

## 3. Kimi's tool calling — confirmed on paper

- Kimi's documentation (now at `platform.kimi.ai`) describes tool calling through the OpenAI-style `tools` field at `https://api.moonshot.ai/v1`, **including while streaming** (the call arrives in pieces, tagged with an index), and parallel calls.
- Its examples use `kimi-k3.1-pro`.
- OpenRouter's public catalogue lists ten Kimi models; all declare tool calling. Images are accepted from `kimi-k2.5` up. **`kimi-k2-0905`, kintale's default, is text only** — no screenshots on it.
- That documentation page does not mention a built-in web search.

## Still to run for real

- **In the Forever client:** the paste box (does a paste arrive in one piece, does the key binding work); `CreateMacro`; listing a profession's recipes; a TomTom waypoint and an Auctionator list called from another addon; what is blocked in combat or in instances.
- **With a key:** one tool-calling round trip on Kimi, and one on OpenRouter.
- **On the Windows PC:** the Wowhead results are expected to be the same there, since the block follows the client and not the network; one request would confirm it.
