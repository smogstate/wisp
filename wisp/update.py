"""Keeping Wisp current, by itself, without touching Wisp.exe.

Wisp.exe is a launcher: Python, the libraries, and a copy of Wisp's own code
(see wisp_app.py). The build publishes, with every release:

    version.json     commit, when it was built, which exe it needs ("runtime"),
                     and the size and SHA-256 of the two files below
    wisp-code.zip    Wisp's own code: about a hundred kilobytes
    Wisp.exe         the launcher

A running Wisp looks at version.json now and then. Nearly always the newer
build needs the same exe, and then the update is the code alone: the zip is
downloaded into the player's Wisp folder, checked, unpacked, tried once in a
process of its own, and marked as the one to run. The exe is not renamed,
replaced or touched; nothing appears beside it.

Only when a build needs a different exe (a new library) is the exe itself
replaced, and that one is staged in the Wisp folder too.

Everything comes from this project's own release address, over HTTPS, and is
kept only if it is exactly the published file. A copy run from source has no
version and never updates.
"""
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

RELEASE = "https://github.com/smogstate/wisp/releases/download/mvp/"
HEX40, HEX64 = r"[0-9a-f]{40}", r"[0-9a-f]{64}"


def current() -> dict | None:
    """The code that is running: {"commit", "built", "runtime"}. None when run from source."""
    try:
        info = json.loads(Path(__file__).with_name("version.json").read_text(encoding="utf-8-sig"))
        return info if isinstance(info, dict) and re.fullmatch(HEX40, str(info.get("commit"))) else None
    except (OSError, ValueError):
        return None


def exe() -> Path | None:
    """The packaged Wisp.exe that is running, on Windows. None otherwise: nothing to update."""
    if getattr(sys, "frozen", False) and os.name == "nt":
        return Path(sys.executable)
    return None


def latest(base: str = RELEASE, timeout: float = 15) -> dict | None:
    """What is published. None if it cannot be read or is not sound."""
    try:
        with urllib.request.urlopen(base + "version.json", timeout=timeout) as reply:
            info = json.loads(reply.read(10_000).decode("utf-8-sig"))
    except Exception:
        return None

    def a_file(sha, size, least, most):
        return re.fullmatch(HEX64, str(info.get(sha))) and isinstance(info.get(size), int) and least < info[size] < most
    sound = (isinstance(info, dict) and re.fullmatch(HEX40, str(info.get("commit")))
             and a_file("sha256", "size", 1_000_000, 500_000_000))
    if sound and "code_sha256" in info and not a_file("code_sha256", "code_size", 100, 20_000_000):
        sound = False
    return info if sound else None


def sha256(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, target: Path, size: int, sha: str, should_stop=lambda: False) -> bool:
    """Download to target. True only when the whole file arrived and is exactly the published one."""
    part = target.with_name(target.name + ".part")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=30) as reply, part.open("wb") as out:
            got = 0
            for block in iter(lambda: reply.read(1 << 18), b""):
                if should_stop() or got + len(block) > size:
                    raise OSError("stopped, or more than was published")
                out.write(block)
                got += len(block)
        if got != size or sha256(part) != sha:
            raise OSError("not the published file")
        os.replace(part, target)
        return True
    except Exception:
        try:
            part.unlink()
        except OSError:
            pass
        return False


def _fresh_env() -> dict:
    """For starting the exe from itself: without this the new process would take over
    this one's unpacked files, which go when this one leaves."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_MEI") and not k.startswith("_PYI")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def relaunch(app: Path, args: list[str]):
    subprocess.Popen([str(app), *args], env=_fresh_env(), close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


# -- the usual update: the code alone ------------------------------------------

def unpack(archive: Path, folder: Path):
    """Unpack a code zip. Only plain files under wisp/ and addon/, and not much of them."""
    with zipfile.ZipFile(archive) as z:
        members = [m for m in z.infolist() if not m.is_dir()]
        if len(members) > 500 or sum(m.file_size for m in members) > 30_000_000:
            raise ValueError("too much in the archive")
        for m in members:
            parts = m.filename.split("/")
            plain = all(p and p not in (".", "..") and not re.search(r'[\\:*?"<>|\x00-\x1f]', p) for p in parts)
            if not plain or parts[0] not in ("wisp", "addon"):
                raise ValueError(f"unexpected file in the archive: {m.filename[:80]}")
        for m in members:
            target = folder.joinpath(*m.filename.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(m))


def try_code(app: Path, folder: Path) -> bool:
    """Start the exe once with this code, in a process of its own, and ask it whether
    everything loads. A copy that needs something this exe lacks stops here."""
    try:
        return subprocess.run([str(app), "--try-code", str(folder)], env=_fresh_env(), timeout=120,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def install_code(info: dict, root: Path, base: str = RELEASE, tried=lambda folder: True) -> bool:
    """Fetch the published code into root/<commit>, check it, try it, and mark it as the one to run."""
    commit = info["commit"]
    archive, staging, folder = root / "code.zip", root / (commit + ".new"), root / commit
    try:
        if not fetch(base + "wisp-code.zip", archive, info["code_size"], info["code_sha256"]):
            return False
        shutil.rmtree(staging, ignore_errors=True)
        unpack(archive, staging)
        stamped = json.loads((staging / "wisp" / "version.json").read_text(encoding="utf-8-sig"))
        if stamped.get("commit") != commit or not tried(staging):
            raise ValueError("not the code that was announced, or it does not start here")
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(staging, folder)
        (root / "starting").unlink(missing_ok=True)
        partial = root / "current.part"
        partial.write_text(json.dumps({"commit": commit}), encoding="utf-8")
        os.replace(partial, root / "current.json")
        return True
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        return False
    finally:
        try:
            archive.unlink()
        except OSError:
            pass


def running(root: Path) -> str | None:
    """The downloaded commit marked as the one to run, if any."""
    try:
        return str(json.loads((root / "current.json").read_text(encoding="utf-8"))["commit"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def started_fine(root: Path):
    """The window is up: this code works. (The launcher drops a copy that never says so.)"""
    try:
        (root / "starting").unlink()
    except OSError:
        pass


def tidy_code(root: Path, keep: set):
    """Remove downloaded code that is neither running nor marked to run."""
    try:
        for entry in root.iterdir():
            if entry.is_dir() and entry.name not in keep:
                shutil.rmtree(entry, ignore_errors=True)
    except OSError:
        pass


# -- the rare update: a build that needs a different exe -----------------------
# A running exe cannot be overwritten on Windows, but it can be moved. So the old
# one is moved into the Wisp folder, the new one takes its place, and is started.

def pending(stage: Path) -> Path:
    return stage / "Wisp.new.exe"


def ready(stage: Path, expected_sha256) -> Path | None:
    """The downloaded exe, if it is there and still is exactly what was checked."""
    new = pending(stage)
    try:
        return new if isinstance(expected_sha256, str) and new.is_file() and sha256(new) == expected_sha256 else None
    except OSError:
        return None


def swap(app: Path, new: Path, stage: Path):
    """Put the new exe in the running one's place. On failure the old one is back."""
    stage.mkdir(parents=True, exist_ok=True)
    old = stage / "Wisp.old.exe"
    try:
        old.unlink(missing_ok=True)
        os.replace(app, old)   # out of sight, when the Wisp folder is on the same drive
    except OSError:
        old = app.with_name("." + app.stem + ".old.exe")
        old.unlink(missing_ok=True)
        os.replace(app, old)
        if os.name == "nt":
            ctypes.windll.kernel32.SetFileAttributesW(str(old), 2)   # hidden
    try:
        shutil.move(str(new), str(app))
    except OSError:
        os.replace(old, app)
        raise


def tidy(app: Path, stage: Path):
    """Remove what an earlier exe update left behind, here and from Wisp's first updater."""
    for leftover in (stage / "Wisp.old.exe", stage / "Wisp.new.exe.part", app.with_name("." + app.stem + ".old.exe"),
                     app.with_name(app.stem + ".old.exe"), app.with_name(app.stem + ".new.exe"), app.with_name(app.stem + ".part")):
        try:
            leftover.unlink()
        except OSError:
            pass
