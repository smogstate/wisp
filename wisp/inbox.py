"""Cards into the running game without a paste.

The overlay cannot talk to the game, but the game can look at files that were
in its AddOns folder when it started (see the addon's "one-click inbox"):

    Wisp_In001 .. Wisp_In100/In.lua   load-on-demand addons: a data file each,
                                      read from disk when the addon loads one
    Wisp_Inbox/001.wav .. 300.wav     signals: empty will not play, a real
                                      (silent) sound will
    Wisp_Inbox/Cursor.lua             read at every UI load: the next signal
                                      and the newest message
    Wisp_Inbox/empty.wav, valid.wav   never change: the addon's self-test

To deliver, the same recent messages go into every slot (the overlay cannot
know which one the game loads next), then the cursor, then the next signal is
raised. A message is the WISP1 text a paste would carry, and the addon hands it
to the same strict reader; its number is the time it was sent, in milliseconds,
so nothing is run twice and nothing old is run late. (Written with ".0": a
Lua that keeps whole numbers in 32 bits, as the test VM does, would mangle it.)

Only ever writes inside these folders of its own.
"""
import os
import re
import struct
import time
from pathlib import Path

SLOTS, SIGNALS, KEEPS = 100, 300, 900   # the same numbers as in the addon
RECENT = 4                              # messages carried along, for a game that was not looking
WISP1 = re.compile(r"WISP1:[A-Za-z0-9+/=]{1,60000}")
# a valid, silent sound: 10 ms of 8-bit silence
SILENT = (b"RIFF" + struct.pack("<I", 36 + 80) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8)
          + b"data" + struct.pack("<I", 80) + b"\x80" * 80)
TOC = "## Interface: 16001\n## Title: |cff808080Wisp inbox{n}|r\n## Notes: {notes}\n{more}\n{file}\n"
EMPTY_SLOT = "WispDelivery = nil\n"


def _write(file: Path, data: bytes):
    """In one piece: the game may read at any moment."""
    part = file.with_name(file.name + ".part")
    part.write_bytes(data)
    for attempt in range(4):
        try:
            os.replace(part, file)
            return
        except PermissionError:   # the game has it open this instant
            time.sleep(0.05)
    file.write_bytes(data)
    part.unlink(missing_ok=True)


def installed(addons: Path) -> bool:
    return ((addons / "Wisp_Inbox" / "Cursor.lua").is_file() and (addons / f"Wisp_In{SLOTS:03d}" / "In.lua").is_file()
            and (addons / "Wisp_Inbox" / f"{SIGNALS:03d}.wav").is_file())


def install(addons: Path) -> bool:
    """Create what is missing; what is there is left alone. True when anything was created:
    the game finds new files only when it starts."""
    made = False

    def ensure(file: Path, data: bytes):
        nonlocal made
        if not file.is_file():
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(data)
            made = True
    box = addons / "Wisp_Inbox"
    ensure(box / "Wisp_Inbox.toc", TOC.format(n="", notes="Lets Wisp's cards reach the game at a click. Leave it enabled.", more="",
                                               file="Cursor.lua").encode())
    ensure(box / "Cursor.lua", b"WispCursor = 1\nWispLatest = 0\n")
    ensure(box / "empty.wav", b"")
    ensure(box / "valid.wav", SILENT)
    for i in range(1, SIGNALS + 1):
        ensure(box / f"{i:03d}.wav", b"")
    for i in range(1, SLOTS + 1):
        name = f"Wisp_In{i:03d}"
        ensure(addons / name / f"{name}.toc", TOC.format(n=f" {i:03d}", notes="A slot of Wisp's inbox. Load-on-demand; leave it enabled.",
                                                         more="## LoadOnDemand: 1\n", file="In.lua").encode())
        ensure(addons / name / "In.lua", EMPTY_SLOT.encode())
    return made


def deliver(addons: Path, text: str, now: float | None = None) -> str | None:
    """Put a WISP1 message where the running game will find it. Returns a problem in words, or None."""
    if not isinstance(text, str) or not WISP1.fullmatch(text):
        return "not a Wisp message"
    if not installed(addons):
        return "the inbox is not installed"
    box, now = addons / "Wisp_Inbox", time.time() if now is None else now
    try:
        cursor = re.search(r"WispCursor = (\d+)", (box / "Cursor.lua").read_text(encoding="utf-8"))
        signal = int(cursor.group(1)) if cursor and 1 <= int(cursor.group(1)) <= SIGNALS else 1
        earlier = re.findall(r'\{ n = (\d+)\.0, text = "(WISP1:[A-Za-z0-9+/=]+)" \}', (addons / "Wisp_In001" / "In.lua").read_text(encoding="utf-8"))
        number = max([int(now * 1000)] + [int(n) + 1 for n, _ in earlier])   # always above what went before
        recent = [(int(n), body) for n, body in earlier if now - int(n) / 1000 < KEEPS][-(RECENT - 1):] + [(number, text)]
        following = signal % SIGNALS + 1
        slot = ("WispDelivery = { c = %d, msgs = {\n%s} }\n" % (following, "".join('  { n = %d.0, text = "%s" },\n' % m for m in recent))).encode()
        for i in range(1, SLOTS + 1):
            _write(addons / f"Wisp_In{i:03d}" / "In.lua", slot)
        _write(box / "Cursor.lua", f"WispCursor = {following}\nWispLatest = {number}.0\n".encode())
        for ahead in range(1, 6):   # the signals to come must be down: a ring comes round again
            file = box / f"{(signal + ahead - 1) % SIGNALS + 1:03d}.wav"
            if file.stat().st_size:
                _write(file, b"")
        _write(box / f"{signal:03d}.wav", SILENT)   # last: this is what the game is watching for
    except (OSError, ValueError) as e:
        return str(e)
    return None
