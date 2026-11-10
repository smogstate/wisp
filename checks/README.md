# Checks

Throwaway code for milestone 0 ([wiki/Checks.md](../wiki/Checks.md)). Not part of Wisp; delete once the questions are answered.

## WispCheck — what the Forever client gives an addon

It reads, and reports. Side effects, all undone at once: it creates one macro named `WispCheck` and deletes it; if TomTom is installed it adds one waypoint where you stand and removes it. While it is enabled, **Ctrl+V opens its paste box** instead of toggling nameplates.

### Run it

1. Copy the folder `WispCheck` into `World of Warcraft\_forever_\Interface\AddOns` (the Forever folder is reported to be `_forever_`). Start the game (a new addon is only seen at startup) and enable it on the character screen.
2. Log in. Stand in an ordinary outdoor zone or a city, not in a dungeon.
3. Have at least one quest in your log (the Questie check looks one up). Open your profession window once and leave it open. Optionally talk to a vendor, a trainer and a flight master first.
4. Type `/wispcheck`. Wait for the line that starts with `done:`.
5. Paste test. Open `paste-samples.txt`, copy sample 1, type `/wispcheck paste` in game, press Ctrl+V. Repeat with sample 2.
6. Key test. Press Ctrl+V once with no chat box open. The paste box should appear; press Esc.
7. Log out completely (the report is written to disk at logout).
8. Send this file: `WTF\Account\<your account>\SavedVariables\WispCheck.lua`

### What the file contains

The report, the paste results, and up to 30 NPCs you talked to with their positions. Your class and level are in it; your character name, realm and account name are not. The talent import string of your current build is.

### What it answers

| Question | Check |
|---|---|
| Is this really client 1.60.1, interface 16001? | `client` |
| Which of 75 interfaces exist? | `presence` |
| JSON, base64, compression; a message made outside the game decodes inside it | `encoding` |
| Quest lines for the zone you are in, and the quests in a line | `questLines` |
| The game's own waypoint to a quest objective | `questLog` |
| Dungeon journal: dungeons, raids, a boss, its loot; dungeon entrances on the map | `journal`, `journalLoot` |
| Flight points and points of interest with positions | `flightPoints`, `pointsOfInterest` |
| An item's tooltip by id, as data | `tooltipById` |
| Your build as an import string | `talents` |
| Professions, and how recipes can be listed | `professions`, `recipes` |
| Spellbook, bags, completed quests, reputations | `spellbook`, `bags`, `completedQuests`, `misc` |
| Creating a macro from an addon; the slot counts | `macros` |
| TomTom and Auctionator called from another addon | `tomtom`, `auctionator` |
| Which addons you have installed and running | `addons` |
| Can another addon read Questie's database: a quest, its chain link, the NPC that starts it and where it stands | `questie` |
| Does a paste arrive in one piece, at 130 and at 7690 characters? | `paste` |
| Can an addon take Ctrl+V as its key? | `bindingSet`, `bindingFired` |
| Does talking to an NPC give its id and your position? | `seen` |
| How big is a snapshot as JSON, and compressed? | `exportSize` |

### Tested so far

Only in a Lua interpreter with a faked game: once with nothing defined (every check reports an error, nothing breaks) and once with everything answering (all pass, both samples decode). **Never in the game.** A wrong guess about an interface appears as an `error` line for that check; that is itself a result.
