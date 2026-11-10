"""The model side: one conversation with a provider, and the tool loop above it.

A chat object owns its own message history in its provider's format and offers
user(text), tool_results(results), stopped(partial) and stream(). The loop in
answer() is the same for every provider.

Two wires carry the providers: Anthropic's own, and the OpenAI-shaped one that
Kimi speaks. FakeChat scripts a conversation so the rest can be tested without
a key. A conversation stays with the provider it started on, because each keeps
its history, reasoning included, in its own format."""
import json
import time

from .game import SYSTEM, TOOLS, Game, run_tool

MODEL = "claude-opus-5-5"
MAX_HOPS = 6   # tool rounds per question

# The providers a player can bring a key for. `env` is where a key is looked for
# when none is saved; `hint` is what one looks like.
PROVIDERS = {
    "anthropic": {"label": "Claude", "company": "Anthropic", "model": MODEL, "env": "ANTHROPIC_API_KEY", "hint": "sk-ant-…"},
    "moonshot": {"label": "Kimi", "company": "Moonshot", "model": "kimi-k3.1-pro", "env": "MOONSHOT_API_KEY", "hint": "sk-…"},
}

# US dollars per million tokens, from Anthropic's price list as of 2026-11-03.
# A model that is not listed here is counted in tokens and shown without a price:
# a missing number is better than a confident wrong one.
PRICES = {"claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20}}


def new_usage() -> dict:
    return {"requests": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0, "priced": True}


def add_usage(total: dict, model: str, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    """Add one model response to a conversation's running total."""
    counts = {"input": input_tokens or 0, "output": output_tokens or 0,
              "cache_read": cache_read or 0, "cache_write": cache_write or 0}
    total["requests"] += 1
    price = PRICES.get(model, {})
    for kind, tokens in counts.items():
        total[kind] += tokens
        if tokens and kind not in price:
            total["priced"] = False
        total["cost"] += tokens * price.get(kind, 0) / 1_000_000


class AnthropicChat:
    provider = "anthropic"

    def __init__(self, api_key: str | None = None, messages: list | None = None, usage: dict | None = None,
                 model: str | None = None):
        import anthropic   # imported here so everything else works without the SDK

        self.sdk = anthropic
        self.model = model or MODEL
        self.usage = usage or new_usage()   # tokens and cost of this conversation so far
        # without a key given, the SDK looks in ANTHROPIC_API_KEY
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        # Plain JSON throughout, so a conversation can be saved, reopened and forked.
        self.messages = list(messages or [])
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
                        model=self.model,
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

        used = message.usage
        add_usage(self.usage, message.model, used.input_tokens, used.output_tokens,
                  getattr(used, "cache_read_input_tokens", 0), getattr(used, "cache_creation_input_tokens", 0))
        # The assistant turn goes back whole and unchanged, thinking blocks included.
        # exclude_unset gives exactly what the SDK itself would send for these blocks.
        self.messages.append({"role": "assistant",
                              "content": [b.model_dump(exclude_unset=True, mode="json") for b in message.content]})
        calls = [{"id": b.id, "name": b.name, "input": b.input} for b in message.content if b.type == "tool_use"]
        reason = message.stop_reason
        if reason == "max_tokens" and calls:
            calls, reason = [], "truncated"   # a cut-off tool input parses as a valid partial object: do not run it
        yield {"type": "end", "reason": reason, "calls": calls}


class OpenAIChat:
    """The OpenAI-shaped wire: chat completions with function tools, streamed.
    A provider on this wire is a base URL, a model and its quirks."""
    provider = "openai"
    bases = ["https://api.openai.com/v1"]
    company = "The provider"
    extra: dict = {}

    def __init__(self, api_key: str | None = None, messages: list | None = None, usage: dict | None = None,
                 model: str | None = None):
        import openai   # imported here so everything else works without the SDK

        self.sdk = openai
        self.key = api_key
        self.model = model or PROVIDERS.get(self.provider, {}).get("model")
        self.usage = usage or new_usage()
        self.messages = list(messages or [])   # plain JSON, in this wire's own shape
        self.base = 0                          # which of the bases the key has opened
        self.tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                         "parameters": t["input_schema"]}} for t in TOOLS]

    def client(self):
        return self.sdk.OpenAI(api_key=self.key, base_url=self.bases[self.base], max_retries=1)

    def user(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def tool_results(self, results: list[dict]):
        for r in results:   # every tool call must be answered, each in its own message
            self.messages.append({"role": "tool", "tool_call_id": r["id"], "content": r["content"]})

    def stopped(self, partial: str):
        self.messages.append({"role": "assistant", "content": partial or "(stopped)"})

    def request(self) -> dict:
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM}, *self.messages],
            "tools": self.tools,
            "stream": True,
            "stream_options": {"include_usage": True},   # the last chunk then carries the token counts
            "max_completion_tokens": 16000,
            **self.extra_for(self.model),
        }

    def extra_for(self, model: str) -> dict:
        return {}

    def stream(self, should_stop):
        sdk = self.sdk
        text, reasoning, calls, finish = "", "", {}, None
        try:
            try:
                chunks = self.client().chat.completions.create(**self.request())
            except sdk.AuthenticationError:
                if self.base + 1 >= len(self.bases):
                    raise
                self.base += 1   # the same company's other platform: a key works on one of them only
                chunks = self.client().chat.completions.create(**self.request())
            for chunk in chunks:
                if should_stop():
                    chunks.close()
                    yield {"type": "end", "reason": "stopped", "calls": []}
                    return
                if chunk.usage:
                    used = chunk.usage
                    cached = getattr(getattr(used, "prompt_tokens_details", None), "cached_tokens", 0) or 0
                    add_usage(self.usage, chunk.model or self.model, (used.prompt_tokens or 0) - cached,
                              used.completion_tokens, cache_read=cached)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    text += delta.content
                    yield {"type": "text", "text": delta.content}
                reasoning += getattr(delta, "reasoning_content", None) or ""
                for piece in delta.tool_calls or []:   # a call arrives in fragments, told apart by index
                    call = calls.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
                    call["id"] = piece.id or call["id"]
                    if piece.function:
                        call["name"] += piece.function.name or ""
                        call["arguments"] += piece.function.arguments or ""
                finish = choice.finish_reason or finish
        except sdk.AuthenticationError:
            yield {"type": "error", "message": f"The {self.company} key was rejected. Check it in Settings."}
            return
        except sdk.RateLimitError:
            yield {"type": "error", "message": f"{self.company} is rate limiting this key. Wait a moment and ask again."}
            return
        except sdk.APIConnectionError:
            yield {"type": "error", "message": f"Could not reach {self.company}. Check the connection and ask again."}
            return
        except sdk.APIStatusError as e:
            yield {"type": "error", "message": f"{self.company} answered with an error ({e.status_code}): {e.message}"}
            return

        ordered = [calls[i] for i in sorted(calls)]
        # The assistant turn goes back whole: its reasoning too, which these models expect to see again.
        message = {"role": "assistant", "content": text}
        if reasoning:
            message["reasoning_content"] = reasoning
        if ordered:
            message["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": c["arguments"]}} for c in ordered]
        self.messages.append(message)
        parsed = []
        for c in ordered:
            try:
                arguments = json.loads(c["arguments"] or "{}")
            except ValueError:
                arguments = None   # run_tool answers this call with an error the model can read
            parsed.append({"id": c["id"], "name": c["name"], "input": arguments})
        reason = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens",
                  "content_filter": "refusal"}.get(finish, "tool_use" if parsed else "end_turn")
        if reason == "max_tokens" and parsed:
            # a cut-off call must not run, but it is in the history and has to be answered
            self.tool_results([{"id": c["id"], "content": "not run: the answer was cut off"} for c in parsed])
            parsed, reason = [], "truncated"
        yield {"type": "end", "reason": reason, "calls": parsed}


class MoonshotChat(OpenAIChat):
    """Kimi. Moonshot runs two platforms whose keys do not work on each other,
    so the key is tried on the international one and then on the Chinese one."""
    provider = "moonshot"
    company = "Moonshot"
    bases = ["https://api.moonshot.ai/v1", "https://api.moonshot.cn/v1"]

    def extra_for(self, model: str) -> dict:
        # K3 models always think, at "max" unless told otherwise: far too slow for a chat beside a game
        return {"reasoning_effort": "low"} if model.startswith("kimi-k3") else {}


CHATS = {"anthropic": AnthropicChat, "moonshot": MoonshotChat}


def list_models(provider: str, key: str) -> list[str]:
    """The provider's own catalogue, newest first where it says so. Empty when it
    cannot be fetched: the provider is still usable on its default model."""
    try:
        if provider == "anthropic":
            import anthropic
            return [m.id for m in anthropic.Anthropic(api_key=key, max_retries=0).models.list(limit=100)]
        chat = CHATS[provider](key)
        for chat.base in range(len(chat.bases)):
            try:
                return sorted((m.id for m in chat.client().models.list()), reverse=True)
            except chat.sdk.AuthenticationError:
                continue
    except Exception:
        pass
    return []


class FakeChat:
    """Scripted stand-in for tests and for looking at the UI without a key.
    It asks for the quest log, then for a waypoint to the first location it saw."""

    provider = "fake"

    def __init__(self, api_key=None, messages: list | None = None, usage: dict | None = None, model=None):
        self.model = model or "scripted"
        self.turn = 0
        self.first_ref = None
        self.usage = usage or new_usage()
        self.messages = list(messages or [])   # same shape of bookkeeping as a real provider

    def user(self, text):
        self.turn = 0
        self.question = text
        self.messages.append({"role": "user", "content": text})

    def tool_results(self, results):
        self.messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": r["id"]} for r in results]})
        for r in results:
            data = json.loads(r["content"])
            for quest in data.get("quests", []):
                for group in (quest["ends"], quest["starts"]):
                    for who in group:
                        for where in who["where"]:
                            self.first_ref = self.first_ref or where["ref"]
            self.last = data

    def stopped(self, partial):
        self.messages.append({"role": "assistant", "content": partial or "(stopped)"})

    def stream(self, should_stop):
        for event in self._script(should_stop):
            if event["type"] == "end" and event["reason"] != "stopped":
                self.messages.append({"role": "assistant", "content": event["reason"]})
            yield event

    def _script(self, should_stop):
        self.turn += 1
        add_usage(self.usage, MODEL, input_tokens=900 + 400 * self.turn, output_tokens=120)   # made-up numbers
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
        yield {"type": "usage", "usage": dict(chat.usage)}
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
