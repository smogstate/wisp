"""Kimi and OpenRouter on the OpenAI-shaped wire: the real openai SDK against local stand-ins.

It proves the SDK accepts the request Wisp builds and that Wisp reads the
stream correctly: text, reasoning, tool calls arriving in fragments, token
counts, a key that only works on the provider's second platform, a rejected
key; for OpenRouter also its catalogue, per-model limits, reasoning blocks
sent back and the cost it reports. It does not prove what the real model will say.

    pip install openai && npm install && python tests/test_openai.py
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

try:
    import openai
except ImportError:
    sys.exit("skipped: the openai package is not installed")

import test_mvp  # noqa: E402
from wisp.__main__ import check  # noqa: E402
from wisp.game import Game  # noqa: E402
from wisp import llm  # noqa: E402
from wisp.llm import MoonshotChat, OpenRouterChat, answer, list_models  # noqa: E402

REQUESTS = []


def chunk(delta=None, finish=None, usage=None):
    body = {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1, "model": "kimi-k3.1-pro",
            "choices": [] if delta is None and finish is None else [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
            "usage": usage}
    return f"data: {json.dumps(body)}\n\n".encode()


def call(index, call_id, name, arguments):
    """A tool call the way it streams: the id and name first, the arguments in two fragments."""
    half = len(arguments) // 2
    return [chunk({"tool_calls": [{"index": index, "id": call_id, "type": "function", "function": {"name": name, "arguments": ""}}]}),
            chunk({"tool_calls": [{"index": index, "function": {"arguments": arguments[:half]}}]}),
            chunk({"tool_calls": [{"index": index, "function": {"arguments": arguments[half:]}}]})]


CATALOGUE = {"data": [
    {"id": "anthropic/claude-opus-5.5", "supported_parameters": ["max_tokens", "reasoning", "tools"],
     "pricing": {"prompt": "0.000004", "completion": "0.00002", "input_cache_read": "0.0000004"},
     "top_provider": {"max_completion_tokens": 128000}},
    {"id": "small/model", "supported_parameters": ["max_tokens", "tools"],
     "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "top_provider": {"max_completion_tokens": 4096}},
    {"id": "no/tools", "supported_parameters": ["max_tokens"], "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "openrouter/auto", "supported_parameters": ["tools"], "pricing": {"prompt": "-1", "completion": "-1"}},
]}


def make_handler(accept_keys, openrouter=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, kind="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            key = (self.headers.get("authorization") or "").removeprefix("Bearer ")
            if key in accept_keys:
                return True
            self.reply(401, json.dumps({"error": {"message": "Invalid Authentication", "type": "invalid_authentication_error"}}).encode())
            return False

        def do_GET(self):
            if openrouter:   # the catalogue is public: no key
                self.reply(200, json.dumps(CATALOGUE).encode())
            elif self.allowed():
                self.reply(200, json.dumps({"object": "list", "data": [
                    {"id": "kimi-k2.6", "object": "model"}, {"id": "kimi-k3.1-pro", "object": "model"}, {"id": "kimi-k3", "object": "model"}]}).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            REQUESTS.append({"path": self.path, "body": body, "headers": {k.lower(): v for k, v in self.headers.items()}})
            if not self.allowed():
                return
            messages = body["messages"]
            last = messages[-1]
            question = next(m["content"] for m in reversed(messages) if m["role"] == "user")
            out = []
            if last["role"] == "user" and "bad arguments" in question:
                out += [chunk({"role": "assistant", "content": ""}), *call(0, "call_bad", "waypoint", '{"refs": ["q33.end.1.1"'),
                        chunk(finish="tool_calls")]
            elif last["role"] == "user" and "ready" in question:
                out += [chunk({"role": "assistant", "content": "ready"}), chunk(finish="stop")]
            elif last["role"] == "user" and openrouter:   # reasoning as blocks: one in two pieces, one encrypted
                out += [chunk({"role": "assistant", "reasoning": "The player asks. ", "reasoning_details": [
                            {"type": "reasoning.text", "text": "The player asks. ", "format": "anthropic-claude-v1", "index": 0}]}),
                        chunk({"reasoning_details": [{"type": "reasoning.text", "text": "Read the log.", "signature": "sig-1", "index": 0}]}),
                        chunk({"reasoning_details": [{"type": "reasoning.encrypted", "data": "b3BhcXVl", "id": "r-1", "index": 1}]}),
                        chunk({"content": "Let me look at your quests."}),
                        *call(0, "call_1", "quests", "{}"), chunk(finish="tool_calls")]
            elif last["role"] == "user":
                out += [chunk({"role": "assistant", "reasoning_content": "The player asks about a quest. "}),
                        chunk({"reasoning_content": "I should read the log."}),
                        chunk({"content": "Let me look "}), chunk({"content": "at your quests."}),
                        *call(0, "call_1", "quests", "{}"), chunk(finish="tool_calls")]
            elif last["role"] == "tool" and last["tool_call_id"] == "call_1":
                ref = json.loads(last["content"])["quests"][0]["ends"][0]["where"][0]["ref"]
                out += [chunk({"role": "assistant", "content": ""}), *call(0, "call_2", "waypoint", json.dumps({"refs": [ref]})),
                        chunk(finish="tool_calls")]
            else:
                out += [chunk({"role": "assistant", "content": "Hand it in to Eagan."}), chunk(finish="stop")]
            if (body.get("stream_options") or {}).get("include_usage"):
                used = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                        "prompt_tokens_details": {"cached_tokens": 40}}
                if openrouter and body["model"] != "small/model":
                    used["cost"] = 0.0012   # what the response really cost, when asked for
                out.append(chunk(usage=used))
            self.reply(200, b"".join(out) + b"data: [DONE]\n\n", "text/event-stream")

    return Handler


def serve(accept_keys, openrouter=False):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(accept_keys, openrouter))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def main():
    international, first = serve({"good-key"})
    chinese, second = serve({"good-key", "cn-only-key"})
    MoonshotChat.bases = [first, second]   # in place of api.moonshot.ai and api.moonshot.cn
    with tempfile.TemporaryDirectory() as tmp:
        game = Game.load(test_mvp.make_wow_dir(Path(tmp)))
        chat = MoonshotChat("good-key")
        events = list(answer(chat, game, "where do I hand in the wolf quest?"))

        assert not [e for e in events if e["type"] == "error"], events
        assert events[-1] == {"type": "done", "reason": "end"}, events[-1]
        text = "".join(e["text"] for e in events if e["type"] == "text")
        assert text == "Let me look at your quests.Hand it in to Eagan.", text
        assert [e["name"] for e in events if e["type"] == "tool"] == ["quests", "waypoint"]
        cards = [e["card"] for e in events if e["type"] == "card"]
        assert len(cards) == 1 and cards[0]["lines"][0]["text"] == "Waypoint: Eagan Peltskinner (turn in)"

        assert len(REQUESTS) == 3, "three rounds: ask, quests result, waypoint result"
        sent = REQUESTS[0]["body"]
        assert REQUESTS[0]["path"] == "/v1/chat/completions" and sent["model"] == "kimi-k3.1-pro" and sent["stream"] is True
        assert sent["stream_options"] == {"include_usage": True} and sent["max_completion_tokens"] == 16000
        assert sent["reasoning_effort"] == "low", "K3 thinks at 'max' unless told otherwise"
        assert sent["messages"][0]["role"] == "system" and sent["messages"][1] == {"role": "user", "content": "where do I hand in the wolf quest?"}
        assert [t["function"]["name"] for t in sent["tools"]] == ["character", "quests", "quest_done", "changes", "dungeons", "dungeon", "recipes", "auction_prices", "price_history",
            "market_scan", "market_query", "item_links", "craft_plan",
            "shopping_list", "waypoint"]
        assert sent["tools"][2]["type"] == "function" and sent["tools"][2]["function"]["parameters"]["required"] == ["quest_ids"]

        history = REQUESTS[2]["body"]["messages"]
        assert [m["role"] for m in history] == ["system", "user", "assistant", "tool", "assistant", "tool"]
        assert history[2] == {"role": "assistant", "content": "Let me look at your quests.",
                              "reasoning_content": "The player asks about a quest. I should read the log.",
                              "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "quests", "arguments": "{}"}}]}, \
            "the assistant turn goes back whole, reasoning included, with the fragments joined"
        assert history[3]["tool_call_id"] == "call_1" and "Wolves Across the Border" in history[3]["content"]
        assert json.loads(history[4]["tool_calls"][0]["function"]["arguments"]) == {"refs": ["q33.end.1.1"]}

        usage = [e["usage"] for e in events if e["type"] == "usage"][-1]
        assert (usage["requests"], usage["input"], usage["cache_read"], usage["output"]) == (3, 180, 120, 60), usage
        assert usage["priced"] is False and usage["cost"] == 0, "Wisp has no verified price for Kimi: tokens only"

        # arguments that are not JSON: the call is answered with an error and the conversation goes on
        events = list(answer(chat, game, "send bad arguments"))
        assert [(e["name"], e["ok"]) for e in events if e["type"] == "tool"] == [("waypoint", False)]
        assert events[-1] == {"type": "done", "reason": "end"}
        assert "must be an object" in REQUESTS[-1]["body"]["messages"][-1]["content"]

        # a key from the provider's second platform: refused by the first, tried on the second
        cn = MoonshotChat("cn-only-key")
        events = list(answer(cn, game, "where do I hand in the wolf quest?"))
        assert events[-1] == {"type": "done", "reason": "end"} and cn.base == 1, events[-1]

        events = list(answer(MoonshotChat("bad-key"), game, "hello"))
        assert events[0] == {"type": "error", "message": "The Moonshot key was rejected. Check it in Settings."}, events[0]
        assert events[-1] == {"type": "done", "reason": "error"}

        assert list_models("moonshot", "good-key") == ["kimi-k3.1-pro", "kimi-k3", "kimi-k2.6"]
        assert list_models("moonshot", "cn-only-key") == ["kimi-k3.1-pro", "kimi-k3", "kimi-k2.6"]
        assert list_models("moonshot", "bad-key") == []

        # the setup check takes the same path a question does
        os.environ["MOONSHOT_API_KEY"] = "good-key"
        results = {what: (status, detail) for what, status, detail in check(None, provider="moonshot")}
        assert results["Moonshot key"] == ("ok", "from environment")
        assert results["A real call to the model"] == ("ok", "kimi-k3.1-pro answered: ready"), results["A real call to the model"]
        os.environ["MOONSHOT_API_KEY"] = "bad-key"
        status, detail = {w: (s, d) for w, s, d in check(None, provider="moonshot")}["A real call to the model"]
        assert status == "FAIL" and "key was rejected" in detail, detail
        openrouter(game)
    international.shutdown()
    chinese.shutdown()
    print(f"openai sdk {openai.__version__}: OK")


def openrouter(game):
    server, base = serve({"or-key"}, openrouter=True)
    OpenRouterChat.bases = [base]   # in place of openrouter.ai
    llm._catalogue = None
    del REQUESTS[:]
    chat = OpenRouterChat("or-key")
    events = list(answer(chat, game, "where do I hand in the wolf quest?"))
    assert not [e for e in events if e["type"] == "error"], events
    assert events[-1] == {"type": "done", "reason": "end"}, events[-1]
    assert [e["name"] for e in events if e["type"] == "tool"] == ["quests", "waypoint"]

    sent = REQUESTS[0]["body"]
    assert sent["model"] == "anthropic/claude-opus-5.5" and "max_completion_tokens" not in sent
    assert sent["max_tokens"] == 16000 and sent["reasoning"] == {"effort": "low"} and sent["usage"] == {"include": True}
    assert REQUESTS[0]["headers"]["x-title"] == "Wisp" and "wisp" in REQUESTS[0]["headers"]["http-referer"]
    blocks = [{"type": "reasoning.text", "text": "The player asks. Read the log.", "format": "anthropic-claude-v1",
               "index": 0, "signature": "sig-1"},
              {"type": "reasoning.encrypted", "data": "b3BhcXVl", "id": "r-1", "index": 1}]
    back = REQUESTS[2]["body"]["messages"][2]
    assert back["reasoning_details"] == blocks, "reasoning goes back whole, joined and in order"
    assert "reasoned_by" not in back, "Wisp's own note of who reasoned stays on this PC"

    usage = [e["usage"] for e in events if e["type"] == "usage"][-1]
    assert (usage["requests"], usage["input"], usage["cache_read"], usage["output"]) == (3, 180, 120, 60), usage
    assert usage["priced"] is True and abs(usage["cost"] - 0.0036) < 1e-9, "the cost is the one OpenRouter reports"

    # another model: its own output limit, no reasoning asked of one that has none,
    # the first model's reasoning left behind, and the catalogue's prices when no cost is reported
    chat.model = "small/model"
    events = list(answer(chat, game, "are you ready?"))
    sent = REQUESTS[-1]["body"]
    assert sent["max_tokens"] == 4096 and "reasoning" not in sent
    assert not [m for m in sent["messages"] if "reasoning_details" in m]
    spent = [e["usage"] for e in events if e["type"] == "usage"][-1]["cost"] - 0.0036
    assert abs(spent - (100 * 1 + 20 * 2) / 1_000_000) < 1e-9, spent

    assert list_models("openrouter", "or-key") == ["anthropic/claude-opus-5.5", "small/model", "openrouter/auto"], \
        "only models that can call tools"
    assert llm.openrouter_catalogue()["openrouter/auto"]["price"] == {}

    events = list(answer(OpenRouterChat("bad-key"), game, "hello"))
    assert events[0] == {"type": "error", "message": "The OpenRouter key was rejected. Check it in Settings."}, events[0]

    os.environ["OPENROUTER_API_KEY"] = "or-key"
    results = {what: (status, detail) for what, status, detail in check(None, provider="openrouter")}
    assert results["A real call to the model"] == ("ok", "anthropic/claude-opus-5.5 answered: ready"), results
    server.shutdown()


if __name__ == "__main__":
    main()
