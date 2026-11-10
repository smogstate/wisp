"""Keeping Wisp.exe current, by itself.

The build workflow publishes, next to Wisp.exe, a small version.json: the commit
it was built from, its size and its SHA-256. A packaged Wisp knows its own
commit, looks at that file now and then, and when the two differ downloads the
new exe beside itself as Wisp.new.exe, checks size and hash, and swaps at the
next start (or when the player presses Restart):

    Wisp.exe      ->  Wisp.old.exe   a running exe can be renamed, not overwritten
    Wisp.new.exe  ->  Wisp.exe
    start the new Wisp.exe, leave

Only ever from this project's own release address, over HTTPS. Nothing is sent
but the request itself. A copy run from source has no version and never updates.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

RELEASE = "https://github.com/smogstate/wisp/releases/download/mvp/"


def current() -> dict | None:
    """What this copy was built from: {"commit", "built"}. None when run from source."""
    try:
        info = json.loads(Path(__file__).with_name("version.json").read_text(encoding="utf-8-sig"))
        return info if isinstance(info, dict) and re.fullmatch(r"[0-9a-f]{40}", str(info.get("commit"))) else None
    except (OSError, ValueError):
        return None


def exe() -> Path | None:
    """The packaged Wisp.exe that is running, on Windows. None otherwise: nothing to update."""
    if getattr(sys, "frozen", False) and os.name == "nt":
        return Path(sys.executable)
    return None


def latest(base: str = RELEASE, timeout: float = 15) -> dict | None:
    """The published version: {"commit", "built", "sha256", "size"}. None if it cannot be read or is not sound."""
    try:
        with urllib.request.urlopen(base + "version.json", timeout=timeout) as reply:
            info = json.loads(reply.read(10_000).decode("utf-8-sig"))
    except Exception:
        return None
    sound = (isinstance(info, dict) and re.fullmatch(r"[0-9a-f]{40}", str(info.get("commit")))
             and re.fullmatch(r"[0-9a-f]{64}", str(info.get("sha256")))
             and isinstance(info.get("size"), int) and 1_000_000 < info["size"] < 500_000_000)
    return info if sound else None


def sha256(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def pending(app: Path) -> Path:
    return app.with_name(app.stem + ".new.exe")


def download(info: dict, app: Path, base: str = RELEASE, should_stop=lambda: False) -> bool:
    """Fetch the new exe to Wisp.new.exe beside the running one. True only when the
    whole file arrived and its size and hash are the published ones."""
    part = app.with_name(app.stem + ".part")
    try:
        with urllib.request.urlopen(base + "Wisp.exe", timeout=30) as reply, part.open("wb") as out:
            got = 0
            for block in iter(lambda: reply.read(1 << 18), b""):
                if should_stop() or got + len(block) > info["size"]:
                    raise OSError("stopped, or more than was published")
                out.write(block)
                got += len(block)
        if got != info["size"] or sha256(part) != info["sha256"]:
            raise OSError("not the published file")
        os.replace(part, pending(app))
        return True
    except Exception:
        try:
            part.unlink()
        except OSError:
            pass
        return False


def ready(app: Path, expected_sha256) -> Path | None:
    """The downloaded exe, if it is there and still is exactly what was checked."""
    new = pending(app)
    try:
        return new if isinstance(expected_sha256, str) and new.is_file() and sha256(new) == expected_sha256 else None
    except OSError:
        return None


def swap(app: Path, new: Path):
    """Put the new exe in the running one's place. On failure the old one is back."""
    old = app.with_name(app.stem + ".old.exe")
    if old.exists():
        old.unlink()
    os.replace(app, old)
    try:
        os.replace(new, app)
    except OSError:
        os.replace(old, app)
        raise


def relaunch(app: Path, args: list[str]):
    """Start the exe afresh. A packaged app started by its own older self would otherwise
    take over that one's unpacked files, which are about to go."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_MEI") and not k.startswith("_PYI")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([str(app), *args], env=env, close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def tidy(app: Path):
    """Remove what an earlier update left behind."""
    for name in (app.stem + ".old.exe", app.stem + ".part"):
        try:
            app.with_name(name).unlink()
        except OSError:
            pass
