"""Wisp overlay (MVP).

    pip install anthropic pywebview
    set ANTHROPIC_API_KEY=...
    python -m wisp "C:\\Program Files (x86)\\World of Warcraft\\_forever_"

The interface is a web page served on this computer only; the window is a
small always-on-top browser view of it. Without pywebview it opens in your
normal browser instead.

    --check     test the setup and exit: game folder, addons, snapshot, key, one real call to the model
    --fake      a scripted model, no key needed (to look at the interface)
    --browser   use the normal browser even if pywebview is installed
"""
import json
import os
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .game import Game, find_saved_file
from .llm import MODEL, AnthropicChat, FakeChat, answer

UI = Path(__file__).with_name("ui.html")


class App:
    def __init__(self, wow_dir: Path | None, fake: bool):
        self.wow_dir, self.fake = wow_dir, fake
        self.token = secrets.token_urlsafe(16)   # only our own page may call the API
        self.busy = threading.Lock()             # one question at a time
        self.stop = threading.Event()
        self.chat = None
        self.problem = None                      # why the model cannot be used, in words

    def new_chat(self):
        self.problem = None
        try:
            self.chat = FakeChat() if self.fake else AnthropicChat()
        except ImportError:
            self.chat, self.problem = None, "The anthropic package is not installed: pip install anthropic"
        except Exception as e:   # the SDK raises its own error when no key is set
            self.chat, self.problem = None, f"Cannot start the model: {e}"

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
        }

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
            if self.path == "/api/stop":
                app.stop.set()
                return self.send_json(200, {})
            if self.path == "/api/new":
                if app.busy.locked():
                    return self.send_json(409, {"error": "busy"})
                app.new_chat()
                return self.send_json(200, {})
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
                except (BrokenPipeError, ConnectionResetError):
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


def check(wow_dir: Path) -> list[tuple[str, str, str]]:
    """Everything a first run depends on, as (what, "ok" | "FAIL" | "note", detail).
    A "note" is something optional that is missing. Makes one small real model call."""
    out = []

    def add(what, ok, good="", bad="", optional=False):
        out.append((what, "ok" if ok else "note" if optional else "FAIL", good if ok else bad))

    addons = wow_dir / "Interface" / "AddOns"
    add("Game folder", addons.is_dir(), str(wow_dir), "there is no Interface\\AddOns folder inside it")
    add("Wisp addon installed", (addons / "Wisp" / "Wisp.toc").is_file(), bad="copy addon\\Wisp into Interface\\AddOns")
    add("Questie installed", (addons / "Questie").is_dir(), bad="without it Wisp knows almost no locations")
    add("TomTom installed", (addons / "TomTom").is_dir(), bad="without it the built-in map pin is used", optional=True)
    saved = find_saved_file(wow_dir)
    add("Snapshot file", saved is not None, str(saved), "log in with the Wisp addon enabled, then log out once")
    game = Game.load(wow_dir)
    if game.problem:
        add("Snapshot readable", False, bad=game.problem)
    else:
        add("Snapshot readable", True, f"{len(game.quests)} quests, saved {game.age_minutes()} minutes ago")
        add("Questie data in the snapshot", (game.snapshot.get("addons") or {}).get("questie"),
            bad="Questie was not ready at logout; play a little, then log out again")
        places = sum(1 for ref in game.places if ".start." in ref or ".end." in ref)
        add("Quest locations found", places > 0, f"{places} for quest givers and turn-ins", "none: is your quest log empty?")
    try:
        import webview  # noqa: F401
        add("pywebview", True)
    except ImportError:
        add("pywebview", False, bad="pip install pywebview (without it Wisp opens in your browser)", optional=True)
    add("ANTHROPIC_API_KEY is set", bool(os.environ.get("ANTHROPIC_API_KEY")), bad="set it in this terminal first")
    try:
        import anthropic
    except ImportError:
        add("anthropic package", False, bad="pip install anthropic")
        return out
    add("anthropic package", True, anthropic.__version__)
    try:
        reply = anthropic.Anthropic().messages.create(
            model=MODEL, max_tokens=2000, messages=[{"role": "user", "content": "Reply with the single word: ready"}])
        text = "".join(b.text for b in reply.content if b.type == "text").strip()
        add("A real call to the model", bool(text), f"{reply.model} answered: {text[:40]}", "the model sent no text")
    except anthropic.APIStatusError as e:
        add("A real call to the model", False, bad=f"error {e.status_code}: {e.message}")
    except Exception as e:   # no key, no network
        add("A real call to the model", False, bad=str(e)[:200])
    return out


def main():
    args = sys.argv[1:]
    fake, in_browser = "--fake" in args, "--browser" in args
    paths = [a for a in args if not a.startswith("--")]
    wow_dir = Path(paths[0]) if paths else None
    if wow_dir is not None and not wow_dir.is_dir():
        sys.exit(f"Not a folder: {wow_dir}\n{__doc__}")
    if "--check" in args:
        if wow_dir is None:
            sys.exit("--check needs the game folder")
        results = check(wow_dir)
        for what, status, detail in results:
            print(f"  {status:<4}  {what}{': ' + detail if detail else ''}")
        failed = sum(status == "FAIL" for _, status, _ in results)
        sys.exit(f"\n{failed} thing(s) to fix." if failed else 0)
    if wow_dir is None and not fake:
        sys.exit(__doc__)
    app = App(wow_dir, fake)
    app.new_chat()
    server, url = serve(app)
    try:
        import webview
    except ImportError:
        webview = None
    if webview and not in_browser:
        webview.create_window("Wisp", url, width=430, height=680, min_size=(320, 400), on_top=True)
        webview.start()
    else:
        print(f"Wisp is running. If no page opened, go to:\n{url}\nPress Ctrl+C to quit.")
        webbrowser.open(url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    server.shutdown()


if __name__ == "__main__":
    main()
