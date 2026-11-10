# LLM providers

> **Built 2026-11-10: Anthropic and Kimi.** Two wires as planned — Anthropic's own, and the OpenAI-shaped one (`OpenAIChat` in `wisp/llm.py`) on which Kimi is a base URL, a default model and two quirks. OpenRouter is the same wire and not added yet. What turned out differently from the draft below is listed first.
>
> - **A conversation stays with its provider.** Each keeps the history in its own format, reasoning included, so switching provider starts a new conversation and the earlier one is kept. Changing model within a provider continues the same conversation.
> - **Kimi's reasoning goes back with every turn**, as its API reference asks: the assistant message is stored whole, `reasoning_content` and tool calls included.
> - **K3 models are asked for low reasoning effort**; their default is the maximum, which is too slow beside a game.
> - **The key is tried on `api.moonshot.ai`, then `api.moonshot.cn`**, kintale's lesson, built in.
> - **No price for Kimi yet.** Moonshot's pricing page showed no numbers when read, so Kimi conversations show tokens and "cost unknown" until there is a verified price.
> - **The `openai` package** is used for the OpenAI-shaped wire.
> - One key and one remembered model per provider, in the Windows credential store; the model list comes from the provider's own catalogue.
>
> Tested with the real `openai` SDK against a local stand-in for the API. **Not yet run against Kimi itself.**
>
> Status of the text below: draft 2026-11-09. Requirement from Denis: **BYOK**, several providers supported — **Anthropic, Kimi, OpenRouter** — abstracted the way kintale does it. Nothing here is built; the code block has not been run.

Wisp ships no key and no house model. The player brings their own key for whichever provider they use, and can switch model at any time.

## What kintale does

Read from `kintale/src/kintale/llm/` and its wiki page *LLM Chat*.

- **A contract, not a wrapper.** `proto` defines what every provider must answer: `chat!`, `configured?`, `pricing` (with currency), `capabilities`, `max-output`, `list-models`, `label`, `secret-fields`, `default-model`. A new backend is one namespace implementing these.
- **One shared wire format.** `openai-compat` carries every OpenAI-shaped API. Kimi, OpenRouter, xAI, DeepSeek and Alibaba are 40–130 lines each on top of it; only Anthropic and Yandex have their own wire code.
- **Accounts.** An account is one credential for one provider: `{id, provider, label, secret}`. Several accounts per provider are allowed.
- **The thing you pick is account × model**, through a `/model` command that browses the provider's live catalogue. Settings never ask which models a key may use.
- **Capabilities belong to the model, not the provider.** One OpenRouter key reaches hundreds of models with different abilities, so the question is asked per model, from the catalogue.
- **Errors are normalised** to a fixed set of kinds, because the UI switches on them.
- **The settings form is generated** from `secret-fields`, so a new provider needs no UI code.
- **Cost is honest.** Where the provider reports the real charge (OpenRouter), that is shown; where a rate is unknown, tokens are shown without a price.

## What carries over, what changes

| | kintale | Wisp |
|---|---|---|
| Contract + shared OpenAI wire + thin providers | yes | same shape |
| Accounts, several per provider, account × model, `/model` | yes | same |
| Per-model capabilities, normalised errors, generated settings form | yes | same |
| How the model acts | structured output (a fixed list of ops) | **tool calling**, in a loop |
| Must-have capability | `:schema` | **`tools`** — a model without tool calling cannot be selected |
| Where it runs | browser: CORS and browser-reachability are part of the contract | Python on the desktop: no CORS, no relay |
| Where keys live | shared with the family, encrypted in the circle | this PC only |
| Keyless house model | yes | no |

## The contract

```python
from typing import Iterator, Literal, Protocol

Capability = Literal["tools", "vision", "web_search", "caching"]

class Provider(Protocol):
    id: str                        # "anthropic" | "moonshot" | "openrouter"
    label: str                     # "Anthropic Claude"
    secret_fields: list["SecretField"]   # what settings must collect; the form is generated from this
    default_model: str

    def configured(self, account: "Account") -> bool: ...
    def list_models(self, account: "Account") -> list["ModelInfo"]: ...     # [] when the catalogue is unavailable
    def capabilities(self, account: "Account", model: str) -> set[Capability]: ...
    def max_output(self, account: "Account", model: str) -> int: ...
    def pricing(self, account: "Account", model: str) -> "Pricing | None": ...   # None = unknown: show tokens, no price
    def stream(self, account: "Account", model: str, request: "ChatRequest") -> Iterator["Event"]: ...
```

Everything that crosses the contract is provider-neutral:

| Type | Holds |
|---|---|
| `Account` | `id`, `provider`, `label`, optional default `model`. The secret is fetched from the key store by `id`, never held in this record |
| `ChatRequest` | `system`, `messages`, `tools`, `max_tokens` |
| `Message` | `role` and content parts: text, image, tool call, tool result |
| `ToolSpec` | `name`, `description`, `input_schema` (JSON Schema) — the same shape as the addon manifest ([Protocol](Protocol.md)) and the Pydantic schemas ([Wowhead](Wowhead.md)) |
| `Event` | `TextDelta`, `ToolCall(id, name, arguments)`, `Usage(input, output, cached, cost?)`, `Done(reason)` |
| `Done.reason` | `end`, `tool_calls`, `truncated`, `refusal` |
| `LLMError.kind` | `not_configured`, `auth`, `rate_limited`, `unreachable`, `http`, `refusal`, `truncated`, `parse` |

The tool loop lives **above** the contract, once: send → collect tool calls → validate arguments → run the tool → send results → repeat. A provider only translates one request and one stream.

## Two wires, three providers

```
wisp.llm                 facade: accounts, the tool loop, cost caps
  ├ wisp.llm.proto         the contract and neutral types
  ├ wisp.llm.anthropic     Anthropic's own API, through the official SDK
  └ wisp.llm.openai_compat the OpenAI-shaped wire, shared
      ├ moonshot             Kimi: base URL, default model, quirks
      └ openrouter           base URL, catalogue, real cost
```

| | Anthropic | Kimi (Moonshot) | OpenRouter |
|---|---|---|---|
| Wire | own | OpenAI-compatible | OpenAI-compatible |
| Tools sent as | `tools: [{name, description, input_schema}]` | `tools: [{type: "function", function: {name, description, parameters}}]` | same as Kimi |
| Tool calling | yes | to verify per model | per model, from the catalogue |
| Vision (screenshots) | yes | per model, to verify | per model, from the catalogue |
| Web search | built-in server tool | has a built-in search tool, to verify | via plugin, to verify |
| Model list | API | API | public catalogue, no key needed |
| Cost | from a rate table | from a rate table | **reported per call** by the provider |
| Quirk | — | two platforms, see below | send attribution headers |

A fourth OpenAI-compatible provider is a few lines of configuration, not a new implementation.

## Lessons from kintale to keep

- **Kimi has two platforms with incompatible keys.** `platform.moonshot.ai` and `platform.moonshot.cn` each reject the other's key with 401. Do not ask the user which one: try `.ai`, fall back to `.cn`, remember which worked for that key.
- **Kimi rejected `json_schema` structured output** in kintale (400). Wisp uses tool calling instead, which must be verified on Kimi before it is offered.
- **OpenRouter: read capabilities and output cap per model** from its catalogue. Some models cap output far below a hardcoded default, and asking for more is an error.
- **OpenRouter: ask for the real cost** (`usage.include`) instead of estimating.
- **An empty model list is not an error.** The provider stays usable on its default model.
- **Never show a confident wrong price.**

## Keys

- Stored in **Windows Credential Manager**, one entry per account. Not in the repo, not in a plain config file, not in the addon's files, not in logs.
- The accounts list (labels, providers, chosen model) is an ordinary local settings file; it holds no secret.
- A key is sent only to its own provider's host.
- Settings show a key masked and never echo it back.

## What depends on the provider

- **Tools are ours, so they work everywhere.** Wowhead, character, screenshot and the game commands run in Python; any model with tool calling can use them ([Tools](Tools.md)).
- **Web search** is secondary — Wisp works primarily on Wowhead. It starts as the provider's built-in search, used in research runs only; Wisp's own search tool is a later step if needed ([Tools](Tools.md)).
- **Vision.** On a text-only model the screenshot option is hidden, not left to fail.
- **Cost caps** apply on every provider: tool calls per question, spend per day, fail closed. Where the price is unknown the cap counts tokens.

## Open

- **Kimi, on paper** ([Checks](Checks.md)): tool calling and streaming tool calls are documented; current models are the `kimi-k3` family; images from `kimi-k2.5` up; kintale's default `kimi-k2-0905` is text only. Still to do: one real round trip with a key.
- **Which SDK for the OpenAI wire:** the `openai` package with a custom base URL, or plain HTTP as kintale does.
- **One wire instead of two?** Moonshot and OpenRouter are said to offer Anthropic-compatible endpoints. Unverified; if true and complete, the Anthropic SDK alone could serve all three.
- **Default provider and model** for a first run, and the order of the provider picker.
