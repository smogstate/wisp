# Ideas — postponed & undecided

Backlog from the 2026-11-09 design session. Nothing here is committed.

## To come back to — written down 2026-11-12

Denis: "write down, we come to this later". Known gaps as of commit `4a49a2c`, none of them started.

### How you died (PvP): what the record does not see

The feature works on the test fight; a real fight shows more than it keeps.

- **Not confirmed at all:** whether the WoW Forever client lets an addon read the combat log. Check setup's line "Deaths on record" answers it after the first real death.
- **What the enemy did that was not aimed at the character.** Only events that hit the character or come from it are kept, so an attacker's own cooldowns, self-heals, bandages, Vanish, Sprint are invisible. Fix: keep the casts and self-buffs of whoever is already in the fight.
- **What kind of control the character was under.** Debuffs are names only; stun, silence or root is the model's guess, weaker still for Russian spell names (untested: the test fight's spells are English). Fix: the game's loss-of-control data, if this client has it, for the exact kind and length.
- **The character's own state.** No mana, energy or rage; nothing on which abilities or potions were ready. The advice can say "Frost Nova came late", not "Ice Block was ready and unused". The most useful for advice and the heaviest to record.
- **How close it was.** Damage dealt is summed; the enemy's health is not recorded. Fix: sample the target's health.
- **The enemy's level** only when targeted or under the mouse; gear never.
- **Resists, absorbs, overkill** are not kept: a mostly absorbed spell reads as a small hit.
- **By design:** only deaths are filed (a fight won or escaped leaves nothing); nothing is live (logout or `/wisp sync`); a pause over 20 s splits a fight; three minutes and 400 lines at most.
- Denis may want the killer's name on the card (never to the model). Not offered so far: his rule is no other players' names.

Suggested first three: the attackers' own casts, loss-of-control data, the target's health.

### Never yet seen working in Denis's real game

- One-click cards (the Send button): needs a full game restart with sound on, then `/wisp sync`.
- The collapsed icon: on his PC Windows does not cut the window round (it does on the build machine), cause unknown. Now it falls back to a square tile; which of the two he gets is the open question.
- The resize grip: tested in a browser only, not how the drag feels in the real window.
- Russian names: item search with word endings, books found by a profession's name, price history for Russian item names.
- Quest data added on 2026-11-12: rewards, the profession a quest needs, experience for a whole chain. All need one login and logout first (quest index key `f6`).
- Blizzard's live auctions: whether `buyout` is per item or per stack.
- `/research`: whether the limits of 8 searches and 6 pages hold per run or per request.

### What Wisp does not know

- **Trainers**, and so the **skill a recipe needs**. Decision pending: record at trainers, or ForeverDB with Denis's own key.
- **Item properties** (bag slots, stats, armour type): "a 14 slot bag", "food with stamina" cannot be answered. Needs a probe of what the client and Blizzard's API give.
- **The character's gear, talents and known spells**: not exported, so no "is this an upgrade" and no build advice.
- **Travel**: flight paths and how far things are across zones; a questing route stays inside one zone.
- **Drop rates** beyond the player's own measured loot.
- **Reputation, honour, PvP standing.**
- **Chains that branch**: followed down one branch only. Experience for later parts is Questie's estimate, not the game's.

### The assistant and its eval

- The eval is tuned on its own 67 questions; there is no held-out set. Wanted from Denis: 5 to 10 questions he really asks while playing.
- Advice is barely tested ("what should I do next to level fastest", "sell, hold or craft"); the eval mostly checks look-ups.
- The eval runs through Claude Code's loop on the subscription, not through Wisp's own `answer()`; that path needs an API key.
- Seen now and then: an English answer to a Russian question made mostly of English names; a waypoint nobody asked for; markdown in an answer.
- The spending limit counts Kimi at Anthropic's dearest price (far above its real cost); Haiku's cache-read price is derived, not listed.

### Smaller

- Settings still takes about two screens; tabs would make it one each.
- Two low security findings left: a pasted foreign message can overwrite an Auctionator list by name; the test switches `--apply-update` and `--try-code` ship in the exe. Not checked: a DLL placed beside `Wisp.exe`.
- The market-ranking tools filter names by plain matching in places the shared matcher does not reach.
- **This wiki is behind the code.** The work of 2026-11-12 (evals, security review, name matching, coins, the new icon, Settings, resizing, quest experience, deaths) is described in `CLAUDE.md` and the commit messages, not yet in Tools, Architecture, Integrations or Red Lines.

## Feature ideas — 2026-11-10

Denis asked what else would be useful. Unprioritized by him; the first five are the ones the existing building blocks already nearly cover. All are out-of-combat help.

**Nearly free with what is designed**

- **Gear: "is this an upgrade?" and a pre-raid checklist.** Per slot: what you wear, the target piece, where it drops and how often, sorted by how easy it is to get. Snapshot gear + a best-in-slot guide + `wowhead_sources`.
- **Attunements, keys and reputations.** "What do I still need for Molten Core / the Onyxia key / Revered with X": the same chain-walking as the dungeon-quests scenario, over completed quests.
- **Dungeon and raid prep.** Before you go: quests to pick up, the key or attunement, loot that is an upgrade for you, and a short briefing per boss for your role. Information before the fight, never during it.
- **Gold.** "Which of my recipes is profitable now", "what in my bags is worth selling, vendoring or disenchanting": recipes + Auctionator's saved prices + the calculator. As fresh as the last auction scan.
- **Session planner and recap.** "I have two hours tonight" → a plan across quests, profession and a dungeon, kept as a note. Next login: "since last time you gained two levels, finished six quests, tailoring 150 → 185".

**Needs one new piece**

- **Snapshot history** — **moved into the plan** by Denis, 2026-11-10: see [Architecture](Architecture.md) and [Roadmap](Roadmap.md).
- **Quiet nudges.** After a login, code (no model call) compares the new snapshot with your goals and the wisp shows a dot: "you now have the materials for step 3".
- **Alts.** A snapshot per character: "which of my characters can craft this", materials across all banks.
- **Hardcore briefings.** Before a zone, an elite quest or a dungeon: what kills people there, from guides and comments.
- **News for you.** "What changed in the last patch for my class and professions", from the Forever news feed.

**Small and cheap**

- **Chat help.** Draft or translate a guild advert, a group-finder message, a reply to a whisper seen in a screenshot. Copied as plain text for you to paste into chat — not a Wisp command.
- **"Where is this?"** from a screenshot of a quest text: the objective's location and the most useful comment.
- **Addon help.** Explain an addon's error from a screenshot; walk through setting up another addon; hand over a WeakAuras import.

## Postponed

- **Wisp as a real MCP server.** Serve the manifest as `tools/list` and answer `tools/call` by putting the command on the clipboard, so Claude Desktop or Claude Code can drive Wisp's tools. A thin wrapper over [Protocol](Protocol.md); build when wanted.
- **Queued commands through the data file.** Python writes commands to the addon's data file and the addon runs them at next login. Suits planning ahead: ask for tonight's route before logging in and every waypoint is already in TomTom. No paste, but not immediate.
- **Answers shown in game.** A pasted message could carry the answer text too, into a Wisp window inside WoW.
- **Publishing the addon.** Would need a policy re-check ([Red Lines](Red-Lines.md)), packaging, and a look at naming.

## Needs outside input

- **Ask Blizzard support about the pixel beacon.** If they approve in writing, it brings back live character state and an arrow that follows the player. Until then it stays out.
- **Image-dimension channel.** Unproven idea for getting small data into the addon without paste: the addon reading the dimensions of image files Python rewrites. Unknown whether the client allows it; would still need a keypress to trigger. Only worth testing if paste proves too clumsy.

## Considered and dropped

- **In-game window with `/reload` round trips** — the first draft. Dropped: the player must never reload.
- **Typing `/wisp <question>` in game chat** — needs a live channel out of the game, which only the beacon gives.
- Everything in the rejected table of [Red Lines](Red-Lines.md).
