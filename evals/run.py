"""Ask Wisp's assistant every question in evals/cases.py and grade the answers.

    python3 evals/run.py --check     free, no key: plays each case's right answer and an empty
                                     answer through the real tools, to prove cases and grader
    python3 evals/run.py             paid: the real model, with ANTHROPIC_API_KEY from the
                                     environment; says what it will send and waits for a yes
    python3 evals/run.py --only quest-xp,hello --reps 3 --model claude-opus-5-5 --variant v1
    python3 evals/run.py --via cli   no key: the model is reached through Claude Code's headless
                                     mode on the subscription it is signed in with (see run_case_cli)

It calls the same answer() the window calls, on the test game from tests/wow_stub.lua,
with Wisp set to Russian. Results go to .claude/hillclimb/answers/<variant>/: results.jsonl
(a row per case and repetition, written as each finishes; a rerun skips what is there),
traces/ (every exchange in full) and errors.jsonl (attempts that broke before an answer).
"""
import argparse
import base64
import json
import re
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "evals")]

import test_mvp   # noqa: E402  the test game, and the tests' clock
from cases import CASES, RU_NAMES   # noqa: E402
from wisp import i18n, llm   # noqa: E402
from wisp.assets import Assets   # noqa: E402
from wisp.game import Game   # noqa: E402
from wisp.market import Market   # noqa: E402

OUT = ROOT / ".claude/hillclimb/answers"
METRICS = [{"id": "pass", "kind": "binary", "label": "Right answer"},
           {"id": "tools", "kind": "binary", "label": "Right tools"},
           {"id": "facts", "kind": "binary", "label": "Facts"},
           {"id": "clean", "kind": "binary", "label": "Nothing wrong"}]

_run_tool = llm.run_tool


def _recording(game, name, args):
    """answer() reports a tool's name only; the grader also needs what was asked and returned."""
    result = _run_tool(game, name, args)
    game.eval_log.append({"name": name, "args": args, "ok": "error" not in result,
                          "result": json.dumps(result, ensure_ascii=False)})
    return result


llm.run_tool = _recording


def new_game(wow: Path) -> Game:
    """A fresh game per case, wired the way App.load_game wires it, on throwaway stores."""
    game = Game.load(wow)
    game.market = Market(Path(tempfile.mkdtemp(prefix="wisp-eval-")) / "market.sqlite")
    game.market.ingest(game.snapshot)
    game.assets = Assets(game.market.file)
    game.eval_log = []
    return game


def russian(wow: Path) -> Path:
    """The same saved game with its names in Russian, as on a Russian client: see cases.RU_NAMES."""
    def renamed(found):
        try:
            text = base64.b64decode(found.group(2)).decode("utf-8")
        except ValueError:
            return found.group(0)   # a key, not packed data
        for english in sorted(RU_NAMES, key=len, reverse=True):
            text = text.replace(english, RU_NAMES[english])
        return f'{found.group(1)} = "{base64.b64encode(text.encode("utf-8")).decode()}"'
    out = Path(tempfile.mkdtemp(prefix="wisp-eval-game-ru-"))
    saved = wow / "WTF/Account/TEST/SavedVariables/Wisp.lua"
    there = out / saved.relative_to(wow)
    there.parent.mkdir(parents=True)
    there.write_text(re.sub(r'^(Wisp(?:Snapshot|Quests|Items|Recipes)) = "([^"]*)"', renamed, saved.read_text(encoding="utf-8"), flags=re.M),
                     encoding="utf-8")
    return out


class Scripted:
    """A stand-in model that plays a written answer: per question, its tool calls, then its text."""
    provider, research, model = "scripted", False, "scripted"

    def __init__(self, turns):
        self.turns, self.messages, self.usage, self.turn = list(turns), [], llm.new_usage(), -1

    def web_pages(self, since=0):
        return {}, {}

    def user(self, text):
        self.turn, self.step = self.turn + 1, 0
        self.messages.append({"role": "user", "content": text})

    def tool_results(self, results):
        self.messages.append({"role": "user", "content": "(tool results)"})

    def stopped(self, partial):
        pass

    def stream(self, should_stop):
        calls, text = self.turns[self.turn]
        self.step += 1
        self.messages.append({"role": "assistant", "content": text})
        if self.step == 1 and calls:
            yield {"type": "end", "reason": "tool_use",
                   "calls": [{"id": f"t{i}", "name": name, "input": args} for i, (name, args) in enumerate(calls)]}
        else:
            yield {"type": "text", "text": text}
            yield {"type": "end", "reason": "end_turn", "calls": []}


def run_case(case, games, make_chat, ceiling=180):
    """One conversation through the real answer(). Returns everything the grader and the trace need."""
    game, chat = new_game(games[case.get("game", "en")]), make_chat(case)
    questions = [case["ask"]] if isinstance(case["ask"], str) else case["ask"]
    started = time.monotonic()
    # ponytail: the ceiling is checked between requests only; one hung request waits for the SDK's own timeout
    over = lambda: time.monotonic() - started > ceiling
    trace = [{"role": "system", "content": llm.system_for(False, False)}]
    text, cards, problem, reason = [], [], None, None
    for question in questions:
        trace.append({"role": "user", "content": question})
        said, seen = "", 0
        for event in llm.answer(chat, game, question, should_stop=over):
            if event["type"] == "text":
                said += event["text"]
            elif event["type"] == "tool":
                if said.strip():
                    trace.append({"role": "assistant", "content": said})
                text.append(said)
                said = ""
                for call in game.eval_log[seen:]:
                    trace.append({"role": "tool_call", "name": call["name"],
                                  "content": json.dumps(call["args"], ensure_ascii=False, indent=2)})
                    trace.append({"role": "tool_result", "content": call["result"]})
                seen = len(game.eval_log)
            elif event["type"] == "card":
                cards.append(event["card"])
            elif event["type"] == "error":
                problem = event["message"]
            elif event["type"] == "done":
                reason = event["reason"]
        text.append(said)
        trace.append({"role": "assistant", "content": said})
        if reason != "end":
            break
    return {"text": "\n".join(part for part in text if part.strip()), "cards": cards, "calls": game.eval_log,
            "reason": "timeout" if reason == "stopped" else reason, "problem": problem, "trace": trace,
            "usage": dict(chat.usage), "model": chat.model, "latency_s": round(time.monotonic() - started, 1)}


def serve(wow: Path, log: Path):
    """Wisp's tools as a tool server on stdin/stdout (MCP: one JSON-RPC message a line), for --via cli.
    Every call is appended to `log` with the cards it made, which is what the grader reads."""
    i18n.set_language("ru")
    game = new_game(wow)
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, params = msg.get("method"), msg.get("params") or {}
        if "id" not in msg:
            continue   # a notification: nothing to answer
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}},
                      "serverInfo": {"name": "wisp", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
                                for t in llm.tools_for(False)]}
        elif method == "tools/call":
            shown = len(game.cards)
            out = _recording(game, params.get("name"), params.get("arguments") or {})
            if len(game.eval_log) >= llm.MAX_HOPS - 1:   # answer() says this on its last round; here it is counted in calls
                out["wisp"] = "This was the last lookup. Answer now with what you have."
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps({**game.eval_log[-1], "cards": game.cards[shown:]}, ensure_ascii=False) + "\n")
            result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "isError": "error" in out}
        elif method == "ping":
            result = {}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "unknown method"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}, ensure_ascii=False), flush=True)


def run_case_cli(case, games, model, ceiling=300):
    """The same case through `claude -p`: Wisp's system prompt and tools, but Claude Code's loop
    in place of answer(). So what differs from the window: the tools are named mcp__wisp__<name>,
    the round limit is --max-turns instead of MAX_HOPS with its "last lookup" nudge, and Claude Code
    adds a little of its own to the prompt. Good for 'right tool, true answer'; not a test of answer()."""
    wow = games[case.get("game", "en")]
    work = Path(tempfile.mkdtemp(prefix="wisp-eval-cli-"))   # empty: no project instructions leak in
    log, config, session = work / "calls.jsonl", work / "mcp.json", str(uuid.uuid4())
    config.write_text(json.dumps({"mcpServers": {"wisp": {
        "command": sys.executable, "args": [str(Path(__file__).resolve()), "--serve", str(wow), str(log)]}}}))
    questions = [case["ask"]] if isinstance(case["ask"], str) else case["ask"]
    started = time.monotonic()
    trace = [{"role": "system", "content": llm.system_for(False, False)}]
    text, calls, usage, reason, problem, served = [], [], llm.new_usage(), "end", None, model
    for n, question in enumerate(questions):
        trace.append({"role": "user", "content": question})
        out = subprocess.run(
            ["claude", "-p", question, "--model", model, "--system-prompt", llm.system_for(False, False),
             "--mcp-config", str(config), "--strict-mcp-config", "--tools", "", "--allowedTools", "mcp__wisp",
             "--setting-sources", "", "--output-format", "json", "--max-turns", str(llm.MAX_HOPS + 1),
             *(["--resume", session] if n else ["--session-id", session])],
            cwd=work, capture_output=True, text=True, encoding="utf-8", timeout=ceiling)
        try:
            reply = json.loads(out.stdout)
        except ValueError:
            raise RuntimeError(f"claude -p gave no JSON (exit {out.returncode}): {(out.stderr or out.stdout)[:300]}")
        new = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()][len(calls):] if log.exists() else []
        for call in new:
            trace.append({"role": "tool_call", "name": call["name"], "content": json.dumps(call["args"], ensure_ascii=False, indent=2)})
            trace.append({"role": "tool_result", "content": call["result"]})
        calls += new
        said = reply.get("result") or ""
        text.append(said)
        trace.append({"role": "assistant", "content": said})
        used = reply.get("usage") or {}
        usage["requests"] += reply.get("num_turns") or 0
        for mine, theirs in (("input", "input_tokens"), ("output", "output_tokens"),
                             ("cache_read", "cache_read_input_tokens"), ("cache_write", "cache_creation_input_tokens")):
            usage[mine] += used.get(theirs) or 0
        served = next(iter(reply.get("modelUsage") or {}), served)
        if reply.get("is_error") or reply.get("subtype") not in (None, "success"):
            reason, problem = "error", f"{reply.get('subtype')}: {said[:200]}"
            break
    return {"text": "\n".join(part for part in text if part.strip()), "cards": [c for call in calls for c in call["cards"]],
            "calls": calls, "reason": reason, "problem": problem, "trace": trace, "usage": usage, "model": served,
            "latency_s": round(time.monotonic() - started, 1)}


def card_text(cards) -> str:
    """Cards as text, with each card's command to the game decoded so its contents can be checked."""
    out = json.dumps(cards, ensure_ascii=False)
    for card in cards:
        packed = card.get("wisp") or ""
        if isinstance(packed, str) and packed.startswith("WISP1:"):
            out += "\n" + base64.b64decode(packed[6:]).decode("utf-8", "replace")
    return out


def grade(case, run):
    """Code only, no judge: which tools ran, which facts are there, and nothing that must not be."""
    found = lambda pattern, where: re.search(pattern, where, re.I) is not None
    good = [c for c in run["calls"] if c["ok"]]
    names = {c["name"] for c in good}
    text, cards = run["text"], card_text(run["cards"])
    wrong = {"tools": [], "facts": [], "clean": []}
    for need in case.get("tools", []):
        if not (names if need == "any" else names & set(need.split("|"))):
            wrong["tools"].append(f"no call to {need}")
    if case.get("no_tools") and run["calls"]:
        wrong["tools"].append("looked something up for a question that needs no lookup: " + ", ".join(c["name"] for c in run["calls"]))
    for tool, wanted in case.get("args", {}).items():
        if not any(c["name"] == tool and all(c["args"].get(k) == v for k, v in wanted.items()) for c in good):
            wrong["tools"].append(f"no {tool} call with {wanted}")
    for pattern in case.get("say", []):
        if not found(pattern, text + "\n" + cards):
            wrong["facts"].append(f"missing: {pattern}")
    for pattern in case.get("text", []):
        if not found(pattern, text):
            wrong["facts"].append(f"not said: {pattern}")
    for pattern in case.get("card", []):
        if not found(pattern, cards):
            wrong["facts"].append(f"not on a card: {pattern}")
    for pattern in case.get("never", []):
        if found(pattern, text):
            wrong["clean"].append(f"said: {pattern}")
    for pattern in case.get("card_never", []):
        if found(pattern, cards):
            wrong["clean"].append(f"on a card: {pattern}")
    # item and quest names are English in the test game, so a Russian answer is told by its small words
    russian = len(re.findall("[а-яё]", text, re.I))
    english = set(re.findall(r"\b(?:you|your|is|are|and|with|from|this|that|can|have|it)\b", text.lower()))
    if case["lang"] == "ru" and len(english) >= 3:
        wrong["clean"].append("answered a Russian question in English")
    if case["lang"] == "en" and russian > 5:
        wrong["clean"].append("answered an English question in Russian")
    if run["reason"] != "end":
        wrong["clean"].append(f"did not finish: {run['reason']} {run['problem'] or ''}".strip())
    marks = {key: float(not problems) for key, problems in wrong.items()}
    marks = {"pass": float(all(marks.values())), **marks}
    return marks, {key: "; ".join(problems) for key, problems in wrong.items() if problems}


def check(wow, cases) -> bool:
    """Free self-test of the eval: a right answer must pass every case, an empty one almost none."""
    fine = True
    for case in cases:
        marks, why = grade(case, run_case(case, wow, lambda c: Scripted(c["oracle"])))
        if not marks["pass"]:
            fine = False
            print(f"RIGHT ANSWER FAILS  {case['id']}: {why}")
    empty = [c["id"] for c in cases
             if grade(c, run_case(c, wow, lambda c: Scripted([([], "Не знаю, к сожалению.")] * 2)))[0]["pass"]]
    # a greeting is rightly answered without a lookup; anything else passing on "I don't know" is a hole
    if set(empty) - {"hello"}:
        fine = False
        print("EMPTY ANSWER PASSES ", ", ".join(empty))
    print(f"{len(cases)} cases: right answers {'all pass' if fine else 'DO NOT all pass'}; "
          f"an empty answer passes {len(empty)} ({', '.join(empty) or 'none'})")
    return fine


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--check", action="store_true", help="free self-test of cases and grader, no model")
    p.add_argument("--model", default="claude-haiku-5-5", help="default: the model Denis plays with")
    p.add_argument("--variant", default="baseline", help="baseline, or v1, v2, ... after a change")
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--only", default="", help="case ids, comma-separated")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--max-tokens", type=int, default=4_000_000, help="stop starting cases past this many tokens in all")
    p.add_argument("--yes", action="store_true", help="skip the question before spending")
    p.add_argument("--via", choices=["api", "cli"], default="api",
                   help="api: the Anthropic API with a key. cli: Claude Code's headless mode, on its subscription")
    p.add_argument("--serve", nargs=2, metavar=("GAME", "LOG"), help="internal: the tool server --via cli starts")
    args = p.parse_args()
    if args.serve:
        return serve(Path(args.serve[0]), Path(args.serve[1]))

    i18n.set_language("ru")
    wow = test_mvp.make_wow_dir(Path(tempfile.mkdtemp(prefix="wisp-eval-game-")), flags="questindex,alts,rewards,ranks,pvpdeath")
    # the Russian game is the stub's own Russian client (its locale, its texts), with the names it leaves in English renamed
    wow = {"en": wow, "ru": russian(test_mvp.make_wow_dir(Path(tempfile.mkdtemp(prefix="wisp-eval-game-")),
                                                             flags="questindex,alts,rewards,ranks,pvpdeath,russian"))}
    only = set(filter(None, args.only.split(",")))
    cases = [c for c in CASES if not only or c["id"] in only]
    assert len({c["id"] for c in CASES}) == len(CASES), "two cases share an id"
    if args.check:
        sys.exit(0 if check(wow, cases) else 1)

    flow = OUT if args.via == "api" else OUT.with_name("answers-cli")   # another harness: never mixed into one score
    out = flow / args.variant
    (out / "traces").mkdir(parents=True, exist_ok=True)
    state = flow / "_state.json"
    if not state.exists():
        state.write_text(json.dumps({"metrics": METRICS}, indent=2))
    rows = out / "results.jsonl"
    done = {(r["prompt_id"], r["rep"]) for r in map(json.loads, rows.read_text().splitlines())} if rows.exists() else set()
    todo = [(c, rep) for c in cases for rep in range(args.reps) if (c["id"], rep) not in done]
    if not todo:
        print("nothing to run: every case is already in", rows)
    elif not args.yes:
        turns = sum(1 if isinstance(c["ask"], str) else len(c["ask"]) for c, _ in todo)
        print(f"{len(todo)} runs ({turns} questions) on {args.model}: at most {turns * llm.MAX_HOPS} requests, "
              f"each carrying the system prompt and {len(llm.tools_for(False))} tool descriptions.\n"
              f"It stops starting new cases after {args.max_tokens:,} tokens. "
              + ("This spends money on your key." if args.via == "api" else "This uses your Claude subscription's allowance."))
        if input("Type yes to run: ").strip().lower() != "yes":
            sys.exit("not run")

    spent = 0

    def one(job):
        case, rep = job
        if spent > args.max_tokens:
            return case, rep, None, "token limit reached before this case"
        try:
            if args.via == "cli":
                return case, rep, run_case_cli(case, wow, args.model), None
            return case, rep, run_case(case, wow, lambda c: llm.AnthropicChat(model=args.model)), None
        except Exception as error:   # a broken attempt is recorded, never scored as a wrong answer
            return case, rep, None, f"{type(error).__name__}: {error}"

    with ThreadPoolExecutor(args.workers) as pool:
        for case, rep, run, broke in pool.map(one, todo):
            if run:
                spent += sum(run["usage"].get(k, 0) for k in ("input", "output", "cache_read", "cache_write"))
            if run and run["reason"] in ("error", "timeout") and not run["text"]:
                broke = f"{run['reason']}: {run['problem']}"
            if broke:
                with open(out / "errors.jsonl", "a") as f:
                    f.write(json.dumps({"prompt_id": case["id"], "rep": rep, "class": "harness", "error": broke,
                                        "model": args.model, "usage": run and run["usage"]}, ensure_ascii=False) + "\n")
                print(f"BROKE {case['id']}: {broke}")
                continue
            marks, why = grade(case, run)
            usage = run["usage"]
            (out / "traces" / f"{case['id']}_rep{rep}.json").write_text(json.dumps(run["trace"], ensure_ascii=False, indent=1))
            with open(rows, "a") as f:
                f.write(json.dumps({
                    "prompt_id": case["id"], "rep": rep, "tags": case["tags"],
                    "prompt": case["ask"] if isinstance(case["ask"], str) else "\n".join(case["ask"]),
                    "status": "ok", "stop_reason": run["reason"], "grade": marks, "explanation": why,
                    "model": run["model"], "latency_s": run["latency_s"], "tool_calls": len(run["calls"]),
                    "usage": {"input_tokens": usage["input"], "output_tokens": usage["output"],
                              "cache_read_input_tokens": usage["cache_read"],
                              "cache_creation_input_tokens": usage["cache_write"]},
                    "meta": {"answer": run["text"], "tools": [c["name"] for c in run["calls"]],
                             "requests": usage["requests"]}}, ensure_ascii=False) + "\n")
            print(f"{'ok  ' if marks['pass'] else 'FAIL'} {case['id']}" + (f"  {why}" if why else ""))

    graded = [json.loads(line) for line in rows.read_text().splitlines()] if rows.exists() else []
    if graded:
        n, passed = len(graded), sum(r["grade"]["pass"] for r in graded)
        rate = passed / n
        tokens = {k: sum(r["usage"][k] for r in graded) for k in graded[0]["usage"]}
        print(f"\n{passed:.0f} of {n} right ({rate:.0%}, give or take {1.96 * (rate * (1 - rate) / n) ** 0.5:.0%})"
              f"\ntokens: {tokens}\nresults: {rows}")


if __name__ == "__main__":
    main()
