"""The model side: one conversation with a provider, and the tool loop above it.

A chat object owns its own message history in its provider's format and offers
three methods: user(text), tool_results(results), stream(). The loop in
answer() is the same for every provider. Anthropic is the one implemented;
FakeChat scripts a conversation so the rest can be tested without a key."""
import json
import time

from .game import SYSTEM, TOOLS, Game, run_tool

MODEL = "claude-opus-5-5"
MAX_HOPS = 6   # tool rounds per question


class AnthropicChat:
    def __init__(self, api_key: str | None = None):
        import anthropic   # imported here so everything else works without the SDK

        self.sdk = anthropic
        # without a key given, the SDK looks in ANTHROPIC_API_KEY
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.messages = []
        # eager_input_streaming: tool inputs stream as they are generated; the
        # server then no longer validates them, which run_tool does instead
        self.tools = [{**t, "eager_input_streaming": True} for t in TOOLS]

    def user(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def tool_results(self, results: list[dict]):
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"], "is_error": r["is_error"]}
            for r in results]})

    def stopped(self, partial: str):
        """The player pressed Stop: keep the roles alternating for the next question."""
        self.messages.append({"role": "assistant", "content": partial or "(stopped)"})

    def stream(self, should_stop):
        """Yields {"type": "text"}; ends with {"type": "end", "reason", "calls"} or {"type": "error"}."""
        sdk = self.sdk
        try:
            for attempt in range(3):
                try:
                    with self.client.beta.messages.stream(
                        model=MODEL,
                        max_tokens=16000,
                        system=SYSTEM,
                        tools=self.tools,
                        messages=self.messages,
                        output_config={"effort": "medium"},
                        # if a safety classifier declines, let the server retry on another model
                        betas=["server-side-fallback-2026-07-01"],
                        fallbacks="default",
                    ) as stream:
                        for event in stream:
                            if should_stop():
                                yield {"type": "end", "reason": "stopped", "calls": []}
                                return
                            if event.type == "text":
                                yield {"type": "text", "text": event.text}
                        message = stream.get_final_message()
                    break
                except ValueError:   # a tool input the SDK could not parse at all: ask again
                    if attempt == 2:
                        raise
        except sdk.AuthenticationError:
            yield {"type": "error", "message": "The Anthropic key was rejected. Check it in Settings."}
            return
        except sdk.RateLimitError:
            yield {"type": "error", "message": "Anthropic is rate limiting this key. Wait a moment and ask again."}
            return
        except sdk.APIConnectionError:
            yield {"type": "error", "message": "Could not reach Anthropic. Check the connection and ask again."}
            return
        except sdk.APIStatusError as e:
            yield {"type": "error", "message": f"Anthropic answered with an error ({e.status_code}): {e.message}"}
            return
        except ValueError:
            yield {"type": "error", "message": "The model sent a tool call that could not be read. Ask again."}
            return

        self.messages.append({"role": "assistant", "content": message.content})
        calls = [{"id": b.id, "name": b.name, "input": b.input} for b in message.content if b.type == "tool_use"]
        reason = message.stop_reason
        if reason == "max_tokens" and calls:
            calls, reason = [], "truncated"   # a cut-off tool input parses as a valid partial object: do not run it
        yield {"type": "end", "reason": reason, "calls": calls}


class FakeChat:
    """Scripted stand-in for tests and for looking at the UI without a key.
    It asks for the quest log, then for a waypoint to the first location it saw."""

    def __init__(self):
        self.turn = 0
        self.first_ref = None

    def user(self, text):
        self.turn = 0
        self.question = text

    def tool_results(self, results):
        for r in results:
            data = json.loads(r["content"])
            for quest in data.get("quests", []):
                for group in (quest["ends"], quest["starts"]):
                    for who in group:
                        for where in who["where"]:
                            self.first_ref = self.first_ref or where["ref"]
            self.last = data

    def stopped(self, partial):
        pass

    def stream(self, should_stop):
        self.turn += 1
        if self.turn == 1:
            yield {"type": "text", "text": "Let me look at your quest log. "}
            yield {"type": "end", "reason": "tool_use", "calls": [{"id": "t1", "name": "quests", "input": {}}]}
        elif self.turn == 2 and self.first_ref:
            yield {"type": "end", "reason": "tool_use",
                   "calls": [{"id": "t2", "name": "waypoint", "input": {"refs": [self.first_ref]}}]}
        else:
            words = ("You have %d quests in your log. " % len(self.last.get("quests", []))
                     if self.turn == 2 else
                     "I made a waypoint for the first one. Press Copy, then Ctrl+V twice in the game.")
            for word in words.split(" "):
                if should_stop():
                    yield {"type": "end", "reason": "stopped", "calls": []}
                    return
                time.sleep(0.04)   # so that streaming and Stop can be seen
                yield {"type": "text", "text": word + " "}
            yield {"type": "end", "reason": "end_turn", "calls": []}


def answer(chat, game: Game, question: str, should_stop=lambda: False):
    """One question, start to finish. Yields UI events:
    text, tool (a trace line), card, error, done."""
    chat.user(question)
    game.cards = []
    said = ""
    for _ in range(MAX_HOPS):
        calls, reason = [], None
        for event in chat.stream(should_stop):
            if event["type"] == "text":
                said += event["text"]
                yield event
            elif event["type"] == "error":
                yield event
                yield {"type": "done", "reason": "error"}
                return
            else:
                calls, reason = event["calls"], event["reason"]
        if reason == "stopped":
            chat.stopped(said)
            yield {"type": "done", "reason": "stopped"}
            return
        if reason == "refusal":
            yield {"type": "error", "message": "The model declined to answer that."}
            yield {"type": "done", "reason": "error"}
            return
        if reason == "truncated":
            yield {"type": "error", "message": "The answer was cut off before it finished. Ask again."}
            yield {"type": "done", "reason": "error"}
            return
        if not calls:
            yield {"type": "done", "reason": "end"}
            return
        results = []
        for call in calls:
            shown_before = len(game.cards)
            result = run_tool(game, call["name"], call["input"])
            yield {"type": "tool", "name": call["name"], "ok": "error" not in result}
            for card in game.cards[shown_before:]:
                yield {"type": "card", "card": card}
            results.append({"id": call["id"], "content": json.dumps(result, ensure_ascii=False),
                            "is_error": "error" in result})
        chat.tool_results(results)
    yield {"type": "error", "message": "That took too many lookups. Try a narrower question."}
    yield {"type": "done", "reason": "error"}
