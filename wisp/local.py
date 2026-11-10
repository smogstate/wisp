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


# -- a global key that shows and hides the window (Windows) --------------------

HOTKEY_LABEL = "Ctrl+Shift+Space"


def start_hotkey(callback) -> bool:
    """Ask Windows to tell Wisp when the key is pressed, whichever program has
    the keyboard. This only listens; nothing is ever sent to another program.
    False if the key is taken or this is not Windows."""
    if os.name != "nt":
        return False
    import ctypes
    import threading
    from ctypes import wintypes

    ready, result = threading.Event(), []

    def loop():
        user32 = ctypes.windll.user32
        mod_control, mod_shift, mod_norepeat, vk_space, wm_hotkey = 0x0002, 0x0004, 0x4000, 0x20, 0x0312
        result.append(bool(user32.RegisterHotKey(None, 1, mod_control | mod_shift | mod_norepeat, vk_space)))
        ready.set()
        if not result[0]:
            return
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == wm_hotkey:
                try:
                    callback()
                except Exception:
                    pass   # a failed toggle must not kill the listener

    threading.Thread(target=loop, daemon=True).start()
    ready.wait(3)
    return bool(result and result[0])


def bring_to_front(window) -> None:
    """After the global key: give the window the keyboard so you can type at once."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.user32.SetForegroundWindow(int(window.native.Handle.ToInt64()))
    except Exception:
        pass


# -- how Windows draws the window ----------------------------------------------
# A web view on Windows cannot blend single pixels with the desktop, so on
# Windows the see-through look is the whole window's opacity, and the round
# wisp is a round window shape. All of it acts on Wisp's own window only.

def window_handle(window) -> int | None:
    if os.name != "nt":
        return None
    try:
        return int(window.native.Handle.ToInt64())
    except Exception:
        return None


def set_window_alpha(handle: int | None, alpha: float):
    """1.0 is solid; lower lets what is behind the window show through."""
    if not handle:
        return
    import ctypes
    user32 = ctypes.windll.user32
    gwl_exstyle, ws_ex_layered, lwa_alpha = -20, 0x00080000, 0x2
    get, put = user32.GetWindowLongPtrW, user32.SetWindowLongPtrW
    get.restype = put.restype = ctypes.c_ssize_t
    get.argtypes = [ctypes.c_void_p, ctypes.c_int]
    put.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    style = get(handle, gwl_exstyle)
    if not style & ws_ex_layered:
        put(handle, gwl_exstyle, style | ws_ex_layered)
    user32.SetLayeredWindowAttributes(ctypes.c_void_p(handle), 0, max(40, min(255, round(alpha * 255))), lwa_alpha)


def set_window_round(handle: int | None, round_shape: bool):
    """Clip the window to a disc (the collapsed wisp), or give it back its rectangle."""
    if not handle:
        return
    import ctypes
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
    gdi32.CreateEllipticRgn.restype = ctypes.c_void_p
    region = None
    if round_shape:
        rect = wintypes.RECT()
        user32.GetWindowRect(ctypes.c_void_p(handle), ctypes.byref(rect))
        region = gdi32.CreateEllipticRgn(0, 0, rect.right - rect.left + 1, rect.bottom - rect.top + 1)
    user32.SetWindowRgn(handle, region, True)   # the window owns the region from here on


def round_window_corners(handle: int | None):
    """Windows 11 rounds a borderless window's corners on request; older versions ignore this."""
    if not handle:
        return
    try:
        import ctypes
        preference = ctypes.c_int(2)   # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(handle), 33, ctypes.byref(preference), 4)
    except Exception:
        pass


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


def addon_seen_by_game(wow_dir: Path) -> str:
    """Whether the game itself lists the Wisp addon for any character: 'enabled',
    'disabled', 'unseen' (characters exist, none lists it) or 'unknown' (the
    game keeps no such list here). The game writes one AddOns.txt per character,
    under WTF/Account/<account>/<realm>/<character>, with a line per addon."""
    lists = list(wow_dir.glob("WTF/Account/*/*/*/AddOns.txt"))
    if not lists:
        return "unknown"
    seen = "unseen"
    for file in lists:
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            name, _, state = line.partition(":")
            if name.strip().lower() == "wisp":
                if state.strip().lower() == "enabled":
                    return "enabled"
                seen = "disabled"
    return seen


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
            # no inherited output: wl-copy stays alive to serve the clipboard and would hold our pipes open
            subprocess.run(command, input=text.encode("ascii"), check=True, timeout=5, creationflags=flags,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
