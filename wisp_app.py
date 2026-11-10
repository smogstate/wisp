"""What Wisp.exe is: Python, the libraries, and this launcher. Wisp's own code is
not baked in. A copy of it travels inside the exe, and a newer copy, when one
has been downloaded, sits in the player's Wisp folder:

    %APPDATA%\\Wisp\\code\\<commit>\\wisp\\...      the code
    %APPDATA%\\Wisp\\code\\current.json            which commit to run

The launcher runs the downloaded copy when it is newer than the one inside and
was made for this exe; otherwise the one inside. So an update replaces a small
folder of code, and the exe file itself is never renamed, replaced or touched.

A downloaded copy that fails to bring up a window is dropped at the next start,
and the copy inside takes over: a bad update cannot lock anyone out.
"""
import importlib
import json
import os
import re
import sys
from pathlib import Path


def _carried():
    """Never called. Naming these here is what makes the packaged exe carry them,
    since Wisp's own code is loaded from a folder and the packager cannot see what
    it imports. More than today's code uses, so that tomorrow's code-only update
    does not find something missing."""
    import anthropic, keyring, openai, webview   # noqa: F401,E401
    import asyncio, base64, bisect, collections, concurrent.futures, contextlib, copy, csv, ctypes, dataclasses   # noqa: F401,E401
    import datetime, decimal, difflib, email, enum, fnmatch, fractions, functools, glob, gzip, hashlib, heapq   # noqa: F401,E401
    import html, html.parser, http.client, http.server, inspect, io, itertools, json, locale, logging, math   # noqa: F401,E401
    import operator, pathlib, platform, queue, random, secrets, shutil, socket, sqlite3, ssl, statistics   # noqa: F401,E401
    import string, struct, subprocess, tempfile, textwrap, threading, time, traceback, typing, unicodedata   # noqa: F401,E401
    import urllib.error, urllib.parse, urllib.request, uuid, webbrowser, xml.etree.ElementTree, zipfile, zlib   # noqa: F401,E401


def inside() -> Path:
    """The copy of the code that came with this exe (or, run from source, the repository)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "code"
    return Path(__file__).resolve().parent


def downloaded() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "Wisp" / "code"


def version(folder: Path) -> dict:
    try:
        info = json.loads((folder / "wisp" / "version.json").read_text(encoding="utf-8-sig"))
        return info if isinstance(info, dict) else {}
    except (OSError, ValueError):
        return {}


def choose(own: Path, root: Path) -> Path:
    """The folder of code to run."""
    try:
        commit = json.loads((root / "current.json").read_text(encoding="utf-8"))["commit"]
        if not re.fullmatch(r"[0-9a-f]{40}", str(commit)):
            return own
        folder, mine, theirs = root / commit, version(own), version(root / commit)
        fits = (theirs.get("commit") == commit and (folder / "wisp" / "__main__.py").is_file()
                and theirs.get("runtime") == mine.get("runtime")              # made for this exe's libraries
                and str(theirs.get("built") or "") > str(mine.get("built") or ""))   # and newer than what is inside
        if not fits:
            return own
        marker = root / "starting"
        if marker.exists():   # the last start with this copy never got as far as a window
            marker.unlink()
            (root / "current.json").unlink()
            return own
        marker.write_text(commit, encoding="utf-8")   # Wisp removes it once its window is up
        return folder
    except Exception:
        return own


def main():
    args = sys.argv[1:]
    if "--try-code" in args and len(args) > args.index("--try-code") + 1:
        # asked by a running Wisp, before it trusts a download: does this code even start here?
        try:
            sys.path.insert(0, args[args.index("--try-code") + 1])
            fine = importlib.import_module("wisp.__main__").selfcheck()
        except BaseException:   # whatever is wrong with it, the answer is no, quietly
            fine = False
        sys.exit(0 if fine else 1)
    own = inside()
    folder = choose(own, downloaded())
    sys.path.insert(0, str(folder))
    try:
        app = importlib.import_module("wisp.__main__")
    except Exception:
        if folder == own:
            raise
        # the downloaded copy will not even load: forget it and run the one that came with the exe
        for name in [n for n in sys.modules if n == "wisp" or n.startswith("wisp.")]:
            del sys.modules[name]
        for leftover in ("current.json", "starting"):
            try:
                (downloaded() / leftover).unlink()
            except OSError:
                pass
        sys.path[0] = str(own)
        app = importlib.import_module("wisp.__main__")
    app.run()


if __name__ == "__main__":
    main()
