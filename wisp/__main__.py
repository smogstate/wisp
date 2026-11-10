"""Wisp overlay (MVP).

Run it with no arguments: the window opens, and Settings takes the API key
and the game folder and installs the addon.

    Wisp.exe                      (or: python -m wisp)
    Wisp.exe "D:\\Games\\World of Warcraft"

    --fake        a scripted model and a sample character: look at the interface with no key and no game
    --browser     show the interface in the normal browser instead of its own window
    --check       test the setup in the terminal and exit
    --smoke FILE  open the window, drive it through one question, write the result to FILE, exit
"""
import json
import os
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import local
from .game import Game, find_saved_file
from .llm import MODEL, AnthropicChat, FakeChat, answer

UI = Path(__file__).with_name("ui.html")
NO_FOLDER = ("That folder has no Interface\\AddOns inside. Choose the World of Warcraft folder "
             "or its _forever_ folder.")


def check(wow_dir: Path | None, call_model: bool = True) -> list[tuple[str, str, str]]:
    """Everything a first run depends on, as (what, "ok" | "FAIL" | "note", detail).
    A "note" is something optional that is missing. Makes one small real model call."""
    out = []

    def add(what, ok, good="", bad="", optional=False):
        out.append((what, "ok" if ok else "note" if optional else "FAIL", good if ok else bad))

    add("Game folder", wow_dir is not None, str(wow_dir), "not chosen yet")
    if wow_dir is not None:
        addons = wow_dir / "Interface" / "AddOns"
        state = local.addon_state(wow_dir)
        add("Wisp addon installed", state == "installed", bad="not installed" if state == "missing" else
            "an older version is installed: press Install addon")
        add("Questie installed", (addons / "Questie").is_dir(), bad="without it Wisp knows almost no locations")
        add("TomTom installed", (addons / "TomTom").is_dir(), bad="without it the built-in map pin is used", optional=True)
        saved = find_saved_file(wow_dir)
        if saved is None:
            # say which step is missing, from what the game has written so far
            seen = local.addon_seen_by_game(wow_dir) if state != "missing" else "missing"
            add("Snapshot file", False, bad={
                "missing": "install the addon first",
                "unseen": "the game has not noticed the addon yet: close the game completely, start it again, and on the "
                          "character screen open AddOns and tick Wisp",
                "disabled": "Wisp is switched off in the game: on the character screen open AddOns and tick Wisp",
                "enabled": "the game has the addon but has not saved yet: log in with your character, then log out to "
                           "the character screen (the game only writes addon data at logout)",
                "unknown": "restart the game so it sees the addon, log in with your character, then log out to the "
                           "character screen (the game only writes addon data at logout)",
            }[seen])
            return _key_checks(out, add, call_model)
        add("Snapshot file", True, str(saved))
        game = Game.load(wow_dir)
        if game.problem:
            add("Snapshot readable", False, bad=game.problem)
        else:
            add("Snapshot readable", True, f"{len(game.quests)} quests, saved {game.age_minutes()} minutes ago")
            add("Questie data in the snapshot", (game.snapshot.get("addons") or {}).get("questie"),
                bad="Questie was not ready at logout; play a little, then log out again")
            places = sum(1 for ref in game.places if ".start." in ref or ".end." in ref)
            add("Quest locations found", places > 0, f"{places} for quest givers and turn-ins", "none: is your quest log empty?")
    return _key_checks(out, add, call_model)


def _key_checks(out, add, call_model):
    key, source = local.get_key()
    add("API key", key is not None, f"from {source}" if source == "environment" else "saved on this PC", "not entered yet")
    if not call_model or key is None:
        return out
    try:
        import anthropic
    except ImportError:
        add("anthropic package", False, bad="pip install anthropic")
        return out
    try:
        reply = anthropic.Anthropic(api_key=key).messages.create(
            model=MODEL, max_tokens=2000, messages=[{"role": "user", "content": "Reply with the single word: ready"}])
        text = "".join(b.text for b in reply.content if b.type == "text").strip()
        add("A real call to the model", bool(text), f"{reply.model} answered: {text[:40]}", "the model sent no text")
    except anthropic.APIStatusError as e:
        add("A real call to the model", False, bad=f"error {e.status_code}: {e.message}")
    except Exception as e:   # no network
        add("A real call to the model", False, bad=str(e)[:200])
    return out


class App:
    def __init__(self, wow_dir: Path | None, fake: bool, persist: bool = False):
        self.wow_dir, self.fake = wow_dir, fake
        self.persist = persist                   # False in tests and --fake: never touch real settings or keys
        self.token = secrets.token_urlsafe(16)   # only our own page may call the API
        self.busy = threading.Lock()             # one question at a time
        self.stop = threading.Event()
        self.chat = None
        self.problem = None                      # why the model cannot be used, in words
        self.window = None                       # the pywebview window, when there is one
        self.addon_note = None                   # what Wisp just did to the addon, for Settings to show

    def ensure_addon(self):
        """One package: Wisp carries its addon and keeps the game's copy current,
        without being asked. Only ever writes its own Interface\\AddOns\\Wisp folder."""
        if self.wow_dir is None:
            return
        before = local.addon_state(self.wow_dir)
        if before == "installed":
            return
        problem = local.install_addon(self.wow_dir)
        if problem:
            self.addon_note = problem
        elif before == "missing":
            self.addon_note = "Wisp installed its addon into the game. Restart the game so it loads."
        else:
            self.addon_note = "Wisp updated its addon in the game. Restart the game so the new version loads."

    def new_chat(self):
        self.chat, self.problem = None, None
        if self.fake:
            self.chat = FakeChat()
            return
        key, _ = local.get_key()
        if key is None:
            self.problem = "No API key yet. Open Settings and paste your Anthropic key."
            return
        try:
            self.chat = AnthropicChat(key)
        except ImportError:
            self.problem = "The anthropic package is not installed: pip install anthropic"
        except Exception as e:
            self.problem = f"Cannot start the model: {e}"

    def state(self) -> dict:
        game = Game.load(self.wow_dir)
        character = game.snapshot.get("character") or {}
        return {
            "model": "scripted test model" if self.fake else "Claude",
            "model_problem": self.problem,
            "snapshot_problem": game.problem,
            "snapshot_age_minutes": game.age_minutes(),
            "quests": len(game.quests),
            "level": character.get("level"),
            "questie": (game.snapshot.get("addons") or {}).get("questie"),
            "needs_setup": self.wow_dir is None or (self.chat is None and not self.fake),
            "usage": dict(self.chat.usage) if self.chat is not None else None,
        }

    def settings(self) -> dict:
        _, source = local.get_key()
        return {
            "wow_dir": str(self.wow_dir) if self.wow_dir else "",
            "guesses": [str(p) for p in local.guess_game_folders()],
            "key": "scripted model, no key needed" if self.fake else source,   # where the key is from; never the key
            "addon": local.addon_state(self.wow_dir),
            "addon_note": self.addon_note,
            "can_browse": self.window is not None,
        }

    def save_settings(self, body: dict) -> dict:
        """Returns {"error": ...} or the new settings."""
        if isinstance(body.get("wow_dir"), str) and body["wow_dir"].strip():
            folder = local.game_folder(body["wow_dir"])
            if folder is None:
                return {"error": NO_FOLDER}
            self.wow_dir = folder
            if self.persist:
                local.save_config({**local.load_config(), "wow_dir": str(folder)})
            self.ensure_addon()
        key = body.get("key")
        if isinstance(key, str) and key.strip() and self.persist:
            problem = local.set_key(key.strip())
            if problem:
                return {"error": problem}
            self.new_chat()
        return self.settings()

    def install_addon(self) -> dict:
        if self.wow_dir is None:
            return {"error": "Choose the game folder first."}
        problem = local.install_addon(self.wow_dir)
        if problem:
            return {"error": problem}
        self.addon_note = "The addon is installed. Restart the game so it loads."
        return self.settings()

    def browse(self) -> dict:
        if self.window is None:
            return {"error": "Type or paste the folder path."}
        import webview
        picked = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        return {"path": picked[0] if picked else ""}

    def ask(self, question: str):
        """Yields UI events for one question."""
        if self.chat is None:
            self.new_chat()
        if self.chat is None:
            yield {"type": "error", "message": self.problem}
            yield {"type": "done", "reason": "error"}
            return
        self.stop.clear()
        game = Game.load(self.wow_dir)   # re-read: you may have logged out since the last question
        yield from answer(self.chat, game, question, self.stop.is_set)


def make_handler(app: App, port: int):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed(self) -> bool:
            # Host guards against DNS rebinding, Origin against other pages in the
            # browser, the token against everything else on this computer.
            if self.headers.get("Host") not in hosts:
                return False
            origin = self.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] not in hosts:
                return False
            return secrets.compare_digest(self.headers.get("X-Wisp-Token", ""), app.token)

        def send_json(self, code: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/" and self.headers.get("Host") in hosts and query == f"t={app.token}":
                page = UI.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(page)
            elif path == "/api/state" and self.allowed():
                self.send_json(200, app.state())
            elif path == "/api/settings" and self.allowed():
                self.send_json(200, app.settings())
            else:
                self.send_json(403, {"error": "forbidden"})

        def do_POST(self):
            if not self.allowed():
                return self.send_json(403, {"error": "forbidden"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > 100_000:
                return self.send_json(413, {"error": "too long"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self.send_json(400, {"error": "bad json"})
            if not isinstance(body, dict):
                return self.send_json(400, {"error": "bad json"})
            if self.path == "/api/stop":
                app.stop.set()
                return self.send_json(200, {})
            if self.path == "/api/copy":
                text = body.get("text")
                # only Wisp's own messages go to the clipboard this way
                ok = isinstance(text, str) and text.startswith("WISP1:") and len(text) < 100_000 and text.isascii()
                return self.send_json(200, {"copied": bool(ok and local.copy_to_clipboard(text))})
            if self.path in ("/api/new", "/api/settings", "/api/install-addon", "/api/browse", "/api/check"):
                if app.busy.locked():
                    return self.send_json(409, {"error": "Wait for the answer to finish."})
                if self.path == "/api/new":
                    app.new_chat()
                    return self.send_json(200, {})
                if self.path == "/api/settings":
                    return self.send_json(200, app.save_settings(body))
                if self.path == "/api/install-addon":
                    return self.send_json(200, app.install_addon())
                if self.path == "/api/browse":
                    return self.send_json(200, app.browse())
                results = check(app.wow_dir, call_model=not app.fake)
                return self.send_json(200, {"results": [{"what": w, "status": s, "detail": d} for w, s, d in results]})
            if self.path != "/api/ask":
                return self.send_json(404, {"error": "not found"})
            question = body.get("text")
            if not isinstance(question, str) or not question.strip() or len(question) > 4000:
                return self.send_json(400, {"error": "ask something, up to 4000 characters"})
            if not app.busy.acquire(blocking=False):
                return self.send_json(409, {"error": "busy"})
            try:
                # one JSON event per line; the connection closing ends the answer
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    for event in app.ask(question.strip()):
                        self.wfile.write(json.dumps(event, ensure_ascii=False).encode() + b"\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    app.stop.set()   # the window went away: stop spending tokens
            finally:
                app.busy.release()

    return Handler


def serve(app: App, port: int = 0):
    """Start the server on a free local port. Returns (server, url)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    port = server.server_address[1]
    server.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}/?t={app.token}"


def smoke(window, result: dict):
    """Drive the real window through one question with the scripted model."""
    try:
        js = window.evaluate_js

        def wait(condition, seconds=30):
            deadline = time.time() + seconds
            while time.time() < deadline:
                if js(condition):
                    return True
                time.sleep(0.2)
            return False

        result["loaded"] = wait("!!document.getElementById('status') && document.getElementById('status').textContent.includes('level')")
        result["status"] = js("document.getElementById('status') ? document.getElementById('status').textContent : null")
        result["engine"] = js("navigator.userAgent")
        js("document.getElementById('input').value = 'where do I hand in the wolf quest?'; "
           "document.getElementById('form').requestSubmit(); true")
        result["answered"] = wait("!!document.querySelector('.card') && document.getElementById('send').dataset.state === 'send'")
        result["card"] = js("document.querySelector('.card li') ? document.querySelector('.card li').textContent : null")
        result["answer"] = js("[...document.querySelectorAll('.msg.wisp')].map(e => e.textContent).filter(Boolean).join(' | ')")
        js("document.querySelector('.card button') && document.querySelector('.card button').click(); true")
        wait("document.querySelector('.card button') && !!document.querySelector('.card button').dataset.state", 10)
        result["copy_button"] = js("document.querySelector('.card button') ? document.querySelector('.card button').dataset.state : null")
        result["key_store"] = local.key_store_problem() or "works"
        # saving the key is part of the first run on Windows; elsewhere the environment variable does
        key_store_ok = result["key_store"] == "works" or os.name != "nt"
        result["ok"] = bool(result["loaded"] and result["answered"] and result["card"] and key_store_ok)
    except Exception as e:
        result["ok"], result["error"] = False, repr(e)
    finally:
        window.destroy()


def main():
    args = sys.argv[1:]
    fake, in_browser = "--fake" in args, "--browser" in args
    smoke_file = args[args.index("--smoke") + 1] if "--smoke" in args and len(args) > args.index("--smoke") + 1 else None
    paths = [a for a in args if not a.startswith("--") and a != smoke_file]
    fake = fake or smoke_file is not None

    wow_dir = None
    if paths:
        wow_dir = local.game_folder(paths[0])
        if wow_dir is None:
            sys.exit(f"{paths[0]}: {NO_FOLDER}")
    elif fake:
        wow_dir = local.sample_game_folder()
    else:
        wow_dir = local.game_folder(local.load_config().get("wow_dir"))
        if wow_dir is None:
            guesses = local.guess_game_folders()
            wow_dir = guesses[0] if guesses else None

    if "--check" in args:
        results = check(wow_dir)
        for what, status, detail in results:
            print(f"  {status:<4}  {what}{': ' + detail if detail else ''}")
        failed = sum(status == "FAIL" for _, status, _ in results)
        sys.exit(f"\n{failed} thing(s) to fix." if failed else 0)

    app = App(wow_dir, fake, persist=not fake)
    if not fake:
        app.ensure_addon()   # a newer Wisp.exe brings a newer addon
    app.new_chat()
    server, url = serve(app)
    try:
        import webview
    except ImportError:
        webview = None
    if smoke_file and webview is None:
        sys.exit("--smoke needs pywebview")
    if webview and not in_browser:
        window = webview.create_window("Wisp", url, width=430, height=680, min_size=(320, 400), on_top=True)
        app.window = window
        if smoke_file:
            result = {}
            threading.Timer(90, lambda: (result.setdefault("error", "timed out"), window.destroy())).start()
            webview.start(smoke, (window, result))
            Path(smoke_file).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            os._exit(0 if result.get("ok") else 1)   # the timer thread must not keep the process alive
        webview.start()
    else:
        print(f"Wisp is running. If no page opened, go to:\n{url}\nPress Ctrl+C to quit.")
        webbrowser.open(url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    server.shutdown()


def run():
    """Entry point for the packaged app, which has no console: failures go to a log file."""
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        local.APP_DIR.mkdir(parents=True, exist_ok=True)
        (local.APP_DIR / "wisp.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    run()
