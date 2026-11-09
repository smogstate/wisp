# Red Lines

> Status: decided 2026-11-09. Denis's rule, verbatim: **"No account risk! Never."**

The lines Wisp doesn't cross. These override every UX or feature argument.

## Never

- **No input sent to the game.** Wisp never presses keys, moves the mouse or clicks in WoW. Not as a default, not as an opt-in setting.
- **No reading or writing game memory.** No `ReadProcessMemory`, no injection, no DLLs, no shared-memory tricks.
- **No modifying the game client or its files** beyond what addons are meant to do (files in `Interface/AddOns` and the addon's own saved data).
- **No arbitrary code from outside.** Pasted text and model output are data; they are never executed as Lua ([Protocol](Protocol.md)). This includes macros: Wisp never writes a macro containing `/run` or `/script`.

## What Wisp does instead

| Piece | Why it is safe |
|---|---|
| Overlay as a separate always-on-top window | Never touches the game; same category as a browser on a second monitor |
| Our own addon on the official addon API | Addons are Blizzard's authorized extension mechanism |
| Python reading/writing the addon's data files | Same pattern as the TSM desktop app and WeakAuras Companion |
| Screenshot when you ask | Passive screen capture, as OBS or Discord do. Lowest-risk category, but Blizzard has not ruled on it |
| Commands pasted by the player | The player presses the keys; same as importing a WeakAura or a talent build |

## Policy research (2026-11-09)

- Blizzard's EULA bans software "not expressly authorized by Blizzard" that "changes and/or facilitates the gameplay". The wording is broad.
- The official post [Prohibitions on Third-party Software](https://us.forums.blizzard.com/en/wow/t/prohibitions-on-third-party-software/2142972) (2025-08-05) names no overlays or screen readers, as allowed or as banned.
- Blizzard has closed live data feeds to external overlays before, by changing the combat log ([PC Gamer](https://www.pcgamer.com/wows-ui-arms-race-rages-on-as-blizzard-says-theyll-crack-down-on-sparkly-new-combat-overlays)).
- For Midnight (and so Forever), Blizzard's line as reported: displaying information is allowed, telling the player what to do in combat is not ([kami-labs.fr](https://kami-labs.fr/en/wow-classic/wow-forever-addons-restreints-comme-sur-midnight/), one secondhand source).

Nothing here is an official ruling on Wisp. Re-check before publishing or before adding any new channel.

## Channels considered and rejected

| Channel | Verdict | Why |
|---|---|---|
| Overlay presses keys in WoW | Rejected | Software-generated input is what Blizzard treats as automation |
| Process memory / shared memory | Rejected | The addon sandbox has no access; reaching it from outside is how cheats work and what Warden detects |
| Pixel beacon (addon draws coloured cells, Python reads the screen) | Out, unless Blizzard approves in writing | Sends no input, but it is a deliberate side channel out of the sandbox and nobody at Blizzard has said it is fine |
| QR codes | Rejected | Into the game: the addon cannot see the screen. Out of the game: it is the pixel beacon again |
| `/reload` round trips | Rejected | Works and is safe, but the player must never need to reload |

## Consequences we accept

- Wisp has **no live data from the game** except a screenshot. Character data is as of the last login or logout.
- Wisp **cannot see the result** of a command it sent. The player sees it in game.
- No live arrow that follows the player's position; navigation goes through TomTom by pasted waypoint.
