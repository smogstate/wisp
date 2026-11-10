"""The real Anthropic SDK against a local stand-in for the API.

It proves the SDK accepts the request Wisp builds and that Wisp reads the
SDK's stream correctly: text, tool calls, a second and third round, and an
authentication failure. It does not prove what the real model will say.

    pip install anthropic && npm install && python tests/test_anthropic.py
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
    import anthropic
except ImportError:
    sys.exit("skipped: the anthropic package is not installed")

import test_mvp  # noqa: E402
from wisp.game import Game  # noqa: E402
from wisp.llm import AnthropicChat, answer  # noqa: E402

REQUESTS = []   # every request body the SDK sent, with its headers


def sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


GUIDE = "https://example.com/forever-gold-guide"
SEARCH = [
    {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "wow forever gold making"}},
    {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
        {"type": "web_search_result", "url": GUIDE, "title": "Forever gold guide", "encrypted_content": "ENCRYPTED", "page_age": "November 2026"}]},
    {"type": "text", "text": "Cloth sells early.", "citations": [
        {"type": "web_search_result_location", "url": GUIDE, "title": "Forever gold guide", "encrypted_index": "INDEX", "cited_text": "cloth"}]},
]


def research_reply(messages):
    """A research run in four requests: own data, a web search that pauses, the report, the last word."""
    said = [m for m in messages if m["role"] == "assistant"]
    if "cited only" in messages[0]["content"]:
        return SEARCH, "end_turn"
    if not said:
        return [{"type": "text", "text": "Your character first."}, {"type": "tool_use", "id": "toolu_r1", "name": "character", "input": {}}], "tool_use"
    if len(said) == 1:
        return SEARCH, "pause_turn"
    if len(said) == 2:
        return [{"type": "tool_use", "id": "toolu_r2", "name": "report", "input": {
            "title": "Gold at the start", "summary": "Buy cloth early. This rests on one guide.",
            "sections": [{"heading": "What to buy", "points": [
                {"text": "Cloth sells early.", "sources": [GUIDE, "https://invented.example/never-seen"]},
                {"text": "You have 15 gold to start with."}]}],
            "next_steps": ["Scan the auction house daily."]}}], "tool_use"
    return [{"type": "text", "text": "That is the plan."}], "end_turn"


def reply(blocks, stop_reason):
    """A streamed assistant message made of text and tool_use blocks."""
    events = [{"type": "message_start", "message": {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}]
    for i, block in enumerate(blocks):
        if block["type"] == "web_search_tool_result":   # arrives whole, once the search has run
            events.append({"type": "content_block_start", "index": i, "content_block": block})
        elif block["type"] == "text":
            events.append({"type": "content_block_start", "index": i, "content_block": {
                "type": "text", "text": "", **({"citations": []} if block.get("citations") else {})}})
            for cite in block.get("citations", []):
                events.append({"type": "content_block_delta", "index": i, "delta": {"type": "citations_delta", "citation": cite}})
            for piece in block["text"].split(" "):
                events.append({"type": "content_block_delta", "index": i,
                               "delta": {"type": "text_delta", "text": piece + " "}})
        else:
            events.append({"type": "content_block_start", "index": i, "content_block": {
                "type": block["type"], "id": block["id"], "name": block["name"], "input": {}}})
            raw = json.dumps(block["input"])
            for piece in (raw[:len(raw) // 2], raw[len(raw) // 2:]):   # arrives in two fragments
                events.append({"type": "content_block_delta", "index": i,
                               "delta": {"type": "input_json_delta", "partial_json": piece}})
        events.append({"type": "content_block_stop", "index": i})
    searches = sum(1 for b in blocks if b["type"] == "web_search_tool_result")
    events.append({"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                   "usage": {"output_tokens": 30, **({"server_tool_use": {"web_search_requests": searches}} if searches else {})}})
    events.append({"type": "message_stop"})
    return events


class FakeAnthropic(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        REQUESTS.append({"path": self.path, "headers": dict(self.headers), "body": body})
        # the SDK sends the key as x-api-key or as a bearer token, depending on its version
        if "bad-key" in (self.headers.get("x-api-key") or "") + (self.headers.get("authorization") or ""):
            data = json.dumps({"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        last = body["messages"][-1]
        results = [b for b in last["content"] if isinstance(b, dict) and b.get("type") == "tool_result"] \
            if isinstance(last["content"], list) else []
        asked = last["content"] if isinstance(last["content"], str) else ""
        if any(t.get("name") == "web_search" for t in body.get("tools", [])):
            blocks, stop = research_reply(body["messages"])
        elif "ready" in asked:   # the setup check's one plain question
            blocks, stop = [{"type": "text", "text": "ready"}], "end_turn"
        elif not results:
            blocks, stop = [{"type": "text", "text": "Let me look at your quests."},
                            {"type": "tool_use", "id": "toolu_1", "name": "quests", "input": {}}], "tool_use"
        elif results[0]["tool_use_id"] == "toolu_1":
            quests = json.loads(results[0]["content"])["quests"]
            ref = quests[0]["ends"][0]["where"][0]["ref"]
            blocks, stop = [{"type": "tool_use", "id": "toolu_2", "name": "waypoint", "input": {"refs": [ref]}}], "tool_use"
        else:
            blocks, stop = [{"type": "text", "text": "Hand it in to Eagan. Press Copy, then Ctrl+V twice."}], "end_turn"
        if not body.get("stream"):
            data = json.dumps({"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                               "content": [{"type": "text", "text": "ready"}], "stop_reason": "end_turn",
                               "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 1}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        data = sse(reply(blocks, stop))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def research(game, tmp):
    """/research: the web through Anthropic's own tools, hard caps, a report with checked sources."""
    del REQUESTS[:]
    game.notes_dir = tmp / "notes"
    chat = AnthropicChat()
    events = list(answer(chat, game, "how do I earn gold at the start?", research=True))
    assert not [e for e in events if e["type"] == "error"], events
    assert events[-1] == {"type": "done", "reason": "end"} and len(REQUESTS) == 4
    sent = REQUESTS[0]["body"]
    web = {t["name"]: t for t in sent["tools"] if t.get("type", "").startswith("web_")}
    assert web["web_search"] == {"type": "web_search_20260209", "name": "web_search", "max_uses": 8, "allowed_callers": ["direct"]}
    assert web["web_fetch"]["max_uses"] == 6 and web["web_fetch"]["max_content_tokens"] == 8000
    assert "report" in [t["name"] for t in sent["tools"]] and "This is a research run" in sent["system"]
    assert "not available with this provider" not in sent["system"]
    traces = [(e["name"], e.get("detail")) for e in events if e["type"] == "tool"]
    assert traces == [("character", None), ("the web", "wow forever gold making"), ("report", None)], traces

    # the paused turn went back exactly as it came, encrypted parts included, with nothing added after it
    resumed = REQUESTS[2]["body"]["messages"]
    assert resumed[-1]["role"] == "assistant" and [b["type"] for b in resumed[-1]["content"]] == ["server_tool_use", "web_search_tool_result", "text"]
    assert resumed[-1]["content"][1]["content"][0]["encrypted_content"] == "ENCRYPTED"
    assert resumed[-1]["content"][2]["citations"][0]["encrypted_index"] == "INDEX"

    report = next(e["card"]["report"] for e in events if e["type"] == "card")
    assert report["sources"] == [{"n": 1, "title": "Forever gold guide", "url": GUIDE, "site": "example.com"}], "a page the web never returned is not cited"
    assert report["sections"][0]["points"] == [{"text": "Cloth sells early.", "refs": [1]}, {"text": "You have 15 gold to start with.", "refs": []}]
    assert report["basis"] == "Your own game data and 1 web page" and report["saved"] == "Kept in your notes"
    told = json.loads(REQUESTS[3]["body"]["messages"][-1]["content"][0]["content"])
    assert told["sources_left_out"] == ["https://invented.example/never-seen"]
    note = next((tmp / "notes").glob("*-gold-at-the-start.md")).read_text(encoding="utf-8")
    assert note.startswith("# Gold at the start") and "- Cloth sells early. [1]" in note and f"1. Forever gold guide — {GUIDE}" in note
    assert len([e for e in events if e["type"] == "card"]) == 1, "the report lists its sources: no second card"
    listed = game.tool_notes({})
    assert listed["notes"][0]["title"] == "Gold at the start" and "Cloth sells early" in game.tool_notes({"read": "gold at the"})["text"]

    # a web search is a cent, on top of the tokens
    usage = [e["usage"] for e in events if e["type"] == "usage"][-1]
    assert usage["searches"] == 1 and abs(usage["cost"] - (0.01 + (40 * 4 + 120 * 20) / 1_000_000)) < 1e-12, usage

    # an answer that cites the web without writing a report still shows its sources
    events = list(answer(AnthropicChat(), game, "cited only", research=True))
    cards = [e["card"] for e in events if e["type"] == "card"]
    assert len(cards) == 1 and cards[0]["sources"] == [{"n": 1, "title": "Forever gold guide", "url": GUIDE, "site": "example.com"}]

    # an ordinary question after it: no web, no report tool
    list(answer(chat, game, "and where do I hand in the wolf quest?"))
    assert not [t for t in REQUESTS[-1]["body"]["tools"] if t.get("type", "").startswith("web_") or t["name"] == "report"]
    assert "research run" not in REQUESTS[-1]["body"]["system"]


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAnthropic)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    with tempfile.TemporaryDirectory() as tmp:
        game = Game.load(test_mvp.make_wow_dir(Path(tmp)))
        chat = AnthropicChat()
        events = list(answer(chat, game, "where do I hand in the wolf quest?"))

        errors = [e for e in events if e["type"] == "error"]
        assert not errors, errors
        assert events[-1] == {"type": "done", "reason": "end"}, events[-1]
        text = "".join(e["text"] for e in events if e["type"] == "text")
        assert text.startswith("Let me look at your quests.") and "Hand it in to Eagan." in text, text
        assert [e["name"] for e in events if e["type"] == "tool"] == ["quests", "waypoint"]
        cards = [e["card"] for e in events if e["type"] == "card"]
        assert len(cards) == 1 and cards[0]["lines"][0]["text"] == "Waypoint: Eagan Peltskinner (turn in)"

        assert len(REQUESTS) == 3, "three rounds: ask, quests result, waypoint result"
        first = REQUESTS[0]["body"]
        assert first["model"] == "claude-opus-5-5" and first["stream"] is True
        assert first["fallbacks"] == "default" and first["output_config"] == {"effort": "medium"}
        assert "server-side-fallback-2026-07-01" in REQUESTS[0]["headers"].get("anthropic-beta", "")
        assert [t["name"] for t in first["tools"]] == ["character", "quests", "quest_done", "changes", "dungeons", "dungeon", "recipes", "auction_prices", "price_history",
            "market_scan", "market_query", "item_links", "gathering", "craft_plan",
            "shopping_list", "notes", "waypoint"], "and neither report nor the web outside a research run"
        assert all(t["eager_input_streaming"] is True for t in first["tools"])
        third = REQUESTS[2]["body"]["messages"]
        assert [m["role"] for m in third] == ["user", "assistant", "user", "assistant", "user"]
        assert third[1]["content"][1]["type"] == "tool_use", "the assistant turn is sent back whole"
        assert third[4]["content"][0]["type"] == "tool_result" and third[4]["content"][0]["is_error"] is False

        # three responses of 10 input and 30 output tokens each, at $4 and $20 per million
        usage = [e["usage"] for e in events if e["type"] == "usage"][-1]
        assert usage["requests"] == 3 and usage["input"] == 30 and usage["output"] == 90 and usage["priced"]
        assert abs(usage["cost"] - (30 * 4 + 90 * 20) / 1_000_000) < 1e-12, usage

        # a second question in the same conversation keeps the history
        events = list(answer(chat, game, "and again?"))
        assert events[-1]["type"] == "done" and len(REQUESTS[-1]["body"]["messages"]) > 5

        # the assistant turn is kept as plain JSON, exactly the blocks the API sent
        assert chat.messages[1] == {"role": "assistant", "content": [
            {"type": "text", "text": "Let me look at your quests. "},
            {"type": "tool_use", "id": "toolu_1", "name": "quests", "input": {}}]}, chat.messages[1]

        research(game, Path(tmp))

        # saved to disk, reopened and continued: the model receives the same history
        from wisp.sessions import Sessions
        store = Sessions(Path(tmp) / "sessions")
        session = store.new()
        session["turns"] = [{"items": [{"type": "user", "text": "q1"}], "end": 6},
                            {"items": [{"type": "user", "text": "q2"}], "end": len(chat.messages)}]
        session["messages"], session["usage"] = chat.messages, dict(chat.usage)
        store.save(session)
        loaded = store.load(session["id"])
        assert loaded["messages"] == json.loads(json.dumps(chat.messages)), "the history survives the disk unchanged"
        requests_so_far = loaded["usage"]["requests"]
        resumed = AnthropicChat(messages=loaded["messages"], usage=loaded["usage"])
        before = len(REQUESTS)
        events = list(answer(resumed, game, "and after reopening?"))
        assert events[-1]["type"] == "done" and events[-1]["reason"] != "error", events[-1]
        sent = REQUESTS[before]["body"]["messages"]
        assert sent[:len(loaded["messages"])] == loaded["messages"] and sent[-1]["content"] == "and after reopening?"
        assert resumed.usage["requests"] > requests_so_far, "the token count carries on"

        # a fork after the first question sends only that much history
        loaded = store.load(session["id"])
        fork = store.fork(loaded, 0)
        forked = AnthropicChat(messages=fork["messages"], usage=fork["usage"])
        before = len(REQUESTS)
        list(answer(forked, game, "a different direction"))
        sent = REQUESTS[before]["body"]["messages"]
        assert len(sent) == 7 and sent[:6] == loaded["messages"][:6] and sent[6]["content"] == "a different direction"

        os.environ["ANTHROPIC_API_KEY"] = "bad-key"
        events = list(answer(AnthropicChat(), game, "hello"))
        assert events[0]["type"] == "error" and "key was rejected" in events[0]["message"], events
        assert events[-1] == {"type": "done", "reason": "error"}
        # the setup check a first run starts with, including its one real model call
        from wisp import local
        from wisp.__main__ import check
        wow = Path(tmp)
        (wow / "Interface/AddOns/Questie").mkdir(parents=True)
        assert local.install_addon(wow) is None
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        results = {what: (status, detail) for what, status, detail in check(wow)}
        assert results["Wisp addon installed"][0] == "ok" and results["Questie installed"][0] == "ok"
        assert results["TomTom installed"][0] == "note", "optional and missing: reported, not counted as a failure"
        assert results["Snapshot readable"] == ("ok", "2 quests, saved 0 minutes ago"), results["Snapshot readable"]
        assert results["Quest locations found"][0] == "ok" and results["Questie data in the snapshot"][0] == "ok"
        assert results["Anthropic key"] == ("ok", "from environment")
        assert results["Moonshot key"][0] == "note", "a provider that is not in use is optional"
        assert results["A real call to the model"] == ("ok", "claude-opus-5-5 answered: ready"), results["A real call to the model"]
        os.environ["ANTHROPIC_API_KEY"] = "bad-key"
        status, detail = {what: (st, d) for what, st, d in check(wow)}["A real call to the model"]
        assert status == "FAIL" and "key was rejected" in detail, detail
    server.shutdown()
    print(f"anthropic sdk {anthropic.__version__}: OK")


if __name__ == "__main__":
    main()
