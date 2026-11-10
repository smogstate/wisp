"""This PC: where settings and the key are kept, where the game is, installing
the addon, the clipboard. Everything here stays on the machine."""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "Wisp"
CONFIG = APP_DIR / "config.json"
KEY_SERVICE, KEY_NAME = "Wisp", "anthropic"
ADDON_SOURCE = Path(__file__).resolve().parent.parent / "addon" / "Wisp"   # bundled next to the code, also in the exe


# -- settings file (holds no secret) -----------------------------------------

def load_config() -> dict:
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        return config if isinstance(config, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(config: dict):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(config, indent=1), encoding="utf-8")


# -- the API key: Windows Credential Manager through keyring ------------------

def get_key() -> tuple[str | None, str | None]:
    """(key, where it came from). A saved key wins over the environment."""
    try:
        import keyring
        saved = keyring.get_password(KEY_SERVICE, KEY_NAME)
        if saved:
            return saved, "saved"
    except Exception:   # no keyring package, or no credential store on this machine
        pass
    env = os.environ.get("ANTHROPIC_API_KEY")
    return (env, "environment") if env else (None, None)


def set_key(key: str) -> str | None:
    """Save the key, or remove it when empty. Returns a problem in words, or None."""
    try:
        import keyring
        if key:
            keyring.set_password(KEY_SERVICE, KEY_NAME, key)
        else:
            try:
                keyring.delete_password(KEY_SERVICE, KEY_NAME)
            except keyring.errors.PasswordDeleteError:
                pass   # there was none
        return None
    except ImportError:
        return "The keyring package is missing, so the key cannot be saved."
    except Exception as e:
        return f"This machine's credential store refused the key: {e}"


def key_store_problem() -> str | None:
    """Write, read and remove a dummy entry (never the real key). None if the
    credential store works, otherwise why not."""
    try:
        import keyring
        keyring.set_password(KEY_SERVICE, "selftest", "wisp-selftest")
        back = keyring.get_password(KEY_SERVICE, "selftest")
        keyring.delete_password(KEY_SERVICE, "selftest")
        return None if back == "wisp-selftest" else "the credential store returned something else"
    except Exception as e:
        return f"{type(e).__name__}: {e}"


# -- the game folder ----------------------------------------------------------

def game_folder(path) -> Path | None:
    """Accept the World of Warcraft folder or its _forever_ folder; return the
    one that holds Interface\\AddOns, or None."""
    if not path:
        return None
    p = Path(str(path).strip().strip('"'))
    for candidate in (p, p / "_forever_"):
        if (candidate / "Interface" / "AddOns").is_dir():
            return candidate
    return None


def guess_game_folders() -> list[Path]:
    if os.name != "nt":
        return []
    found = []
    for drive in "CDEFGHIJ":
        root = Path(f"{drive}:\\")
        if not root.exists():
            continue
        for parent in ("Program Files (x86)", "Program Files", "Games", "Blizzard", ""):
            folder = game_folder(root / parent / "World of Warcraft")
            if folder and folder not in found:
                found.append(folder)
    return found


# -- the addon ----------------------------------------------------------------

def addon_state(wow_dir: Path | None) -> str:
    """'missing', 'outdated' or 'installed', comparing with the copy Wisp carries."""
    if wow_dir is None:
        return "missing"
    target = wow_dir / "Interface" / "AddOns" / "Wisp"
    try:
        for source in ADDON_SOURCE.iterdir():
            if not (target / source.name).is_file():
                return "missing" if not (target / "Wisp.toc").is_file() else "outdated"
            if (target / source.name).read_bytes() != source.read_bytes():
                return "outdated"
    except OSError:
        return "missing"
    return "installed"


def install_addon(wow_dir: Path) -> str | None:
    """Copy the addon into Interface\\AddOns\\Wisp. Returns a problem in words, or None."""
    target = wow_dir / "Interface" / "AddOns" / "Wisp"
    try:
        target.mkdir(parents=True, exist_ok=True)
        for source in ADDON_SOURCE.iterdir():
            shutil.copyfile(source, target / source.name)
    except OSError as e:
        return f"Could not write to {target}: {e.strerror or e}"
    return None


# -- clipboard ----------------------------------------------------------------

def copy_to_clipboard(text: str) -> bool:
    """Put text on the clipboard from this side, which does not depend on what
    the web view allows. True if it worked."""
    if os.name == "nt":
        commands = [["clip"]]   # part of Windows
    elif sys.platform == "darwin":
        commands = [["pbcopy"]]
    else:
        commands = [["wl-copy"], ["xclip", "-selection", "clipboard"]]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # no console flash from a windowed exe
    for command in commands:
        try:
            subprocess.run(command, input=text.encode("ascii"), check=True, timeout=5, creationflags=flags)
            return True
        except (OSError, subprocess.SubprocessError, UnicodeEncodeError):
            continue
    return False


# -- a sample game folder, for --fake and for testing a build -----------------

def sample_game_folder() -> Path:
    """A temporary folder that looks like a game install whose Wisp addon saved
    a small snapshot. Lets the interface be seen with no game."""
    def place(x, y):
        return [{"map": 1429, "zone": "Elwynn Forest", "x": x, "y": y}]

    eagan = {"kind": "npc", "id": 196, "name": "Eagan Peltskinner", "places": place(48.9, 40.2)}
    snapshot = {
        "v": 1, "takenAt": int(time.time()) - 600,
        "client": {"version": "1.60.1", "interface": 16001, "locale": "enUS"},
        "character": {"class": "MAGE", "race": "Human", "level": 12, "faction": "Alliance", "gold": 15,
                      "map": 1429, "zone": "Elwynn Forest", "subzone": "Goldshire", "x": 42.1, "y": 65.9},
        "addons": {"questie": True, "tomtom": True},
        "quests": [
            {"id": 33, "title": "Wolves Across the Border", "level": 7, "complete": True,
             "objectives": [{"text": "8/8 Tough Wolf Meat", "done": True}], "starts": [eagan], "ends": [eagan],
             "next": {"id": 34, "title": "An Unwelcome Guest", "level": 9,
                      "starts": [{"kind": "object", "id": 55, "name": "Wanted Poster", "places": place(24.6, 74.7)}]}},
            {"id": 76, "title": "The Jasperlode Mine", "level": 10, "complete": False,
             "objectives": [{"text": "Scout through the Jasperlode Mine", "done": False}],
             "starts": [{"kind": "npc", "id": 240, "name": "Marshal Dughan", "places": place(42.1, 65.9)}],
             "ends": [{"kind": "npc", "id": 240, "name": "Marshal Dughan", "places": place(42.1, 65.9)}],
             "gameWaypoint": {"map": 1429, "zone": "Elwynn Forest", "x": 61.5, "y": 53.8}},
        ],
        "completed": [7, 15, 21, 54, 62],
    }
    folder = Path(tempfile.mkdtemp(prefix="wisp-sample-"))
    saved = folder / "WTF" / "Account" / "SAMPLE" / "SavedVariables"
    saved.mkdir(parents=True)
    (folder / "Interface" / "AddOns").mkdir(parents=True)
    encoded = base64.b64encode(json.dumps(snapshot).encode()).decode()
    (saved / "Wisp.lua").write_text(f'\nWispSnapshot = "{encoded}"\n', encoding="utf-8")
    return folder
