"""Prompt Lab answer format: REPLY / PROMPT / END separator lines. Mock data only; the parser is structure-only. No LLM, no network.

Run:  python -m story_generator.tests.test_prompt_lab_separators
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

import flet as ft

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
_real_app = ft.app
ft.app = lambda *a, **k: None                      # prompt_lab starts its window at import: not here
spec = importlib.util.spec_from_file_location("prompt_lab_under_test", ROOT / "prompt_lab.py")
PL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PL)
ft.app = _real_app

_fails: list = []
R, P, E = PL._SEP_REPLY, PL._SEP_PROMPT, PL._SEP_END


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


def refused(raw, needle):
    try:
        PL._parse_llm_output(raw)
    except PL.LLMFormatError as exc:
        return needle in str(exc), str(exc)
    return False, "accepted"


def main():
    ok = f"{R}\nDone, here it is.\n\n{P}\nA woman stands in a field.\n\nLighting: soft\nCamera: 35mm\n\n{E}"
    reply, prompt = PL._parse_llm_output(ok)
    check("valid answer parses", reply == "Done, here it is." and prompt == "A woman stands in a field.\n\nLighting: soft\nCamera: 35mm", (reply, prompt))
    check("multiline prompt keeps its own lines and blank lines", prompt.count("\n") == 3)
    check("Thai text is carried untouched", PL._parse_llm_output(f"{R}\nรับทราบค่ะ\n{P}\nหญิงสาวยืน\n{E}") == ("รับทราบค่ะ", "หญิงสาวยืน"))
    check("empty PROMPT is allowed (chatting)", PL._parse_llm_output(f"{R}\nHello!\n{P}\n{E}") == ("Hello!", ""))
    check("JSON-looking text inside the prompt is just text", PL._parse_llm_output(f'{R}\nok\n{P}\n{{"a": 1}} and "reply": x\n{E}')[1] == '{"a": 1} and "reply": x')
    check("a chat-template marker at the very end is ignored", PL._parse_llm_output(ok + "\n</end_of_turn>") == (reply, prompt))

    r = refused(f"{R}\nHi\n{P}\nA woman stands.", "END separator line is missing")
    check("missing END is refused (cut off hint)", r[0] and "raise Max Tokens" in r[1], r)
    check("missing PROMPT is refused", refused(f"{R}\nHi\n{E}", "PROMPT separator line is missing")[0])
    check("missing REPLY is refused", refused(f"{P}\nx\n{E}", "REPLY separator line is missing")[0])
    check("duplicate REPLY is refused", refused(f"{R}\na\n{R}\nb\n{P}\nx\n{E}", "REPLY separator line appears 2 times")[0])
    check("duplicate END is refused", refused(f"{R}\na\n{P}\nx\n{E}\n{E}", "END separator line appears 2 times")[0])
    check("wrong order is refused", refused(f"{P}\nx\n{R}\na\n{E}", "not in the order")[0])
    check("text before REPLY is refused", refused(f"Sure!\n{R}\na\n{P}\nx\n{E}", "text before the REPLY")[0])
    check("text after END is refused", refused(f"{R}\na\n{P}\nx\n{E}\nBye", "text after the END")[0])
    check("empty REPLY is refused", refused(f"{R}\n\n{P}\nx\n{E}", "REPLY section is empty")[0])
    check("a separator inside a longer line does not count", refused(f"{R}\na\nsee {P} here\n{E}", "PROMPT separator line is missing")[0])
    check("truncated output (cut inside the prompt) is refused", refused(f"{R}\na\n{P}\nA woman stands in", "END separator line is missing")[0])
    check("the old JSON answer is refused, not salvaged", refused('{"reply": "hi", "prompt": "a woman"}', "REPLY separator line is missing")[0])
    check("an empty answer is refused", refused("   ", "empty")[0])

    check("the default system prompt carries the three lines", PL._prompt_has_contract(PL._DEFAULT_SYS) and "JSON" not in PL._DEFAULT_SYS)
    old = 'Output ONLY this JSON on a single line:\n{"reply": "x", "prompt": "y"}'
    check("an old JSON system prompt is detected as not usable", not PL._prompt_has_contract(old))
    check("a custom prompt with the three lines is accepted", PL._prompt_has_contract("My rules...\n" + R + "\n...\n" + P + "\n...\n" + E))

    # run_llm: a malformed answer goes to on_bad and never to on_result; a good one reaches on_result
    results, bad, errs = [], [], []

    async def on_result(t, p):
        results.append((t, p))

    async def on_bad(reason, raw):
        bad.append((reason, raw))

    async def on_err(m):
        errs.append(m)

    def make_hist(text):
        return lambda pid: {pid: {"status": {"status_str": "success"}, "outputs": {"9": {"text": [text]}}}}

    async def run(text):
        results.clear(); bad.clear(); errs.clear()
        PL.comfy_queue = lambda wf, cid: "pid123456789"
        PL.comfy_hist = make_hist(text)
        wf = {"1": {"class_type": "LLMSessionChatNode", "inputs": {}}}
        class _P:  # a fake workflow file
            def exists(self): return True
            def read_text(self, encoding=None): return PL.json.dumps(wf)
        class _Dir:
            def __truediv__(self, name): return _P()
        PL.LLM_DIR = _Dir()
        await asyncio.wait_for(PL.run_llm("hi", {"system_prompt": PL._DEFAULT_SYS}, "sid", on_result, on_err, wf_file="x.json", on_bad=on_bad), 20)

    asyncio.run(run(ok))
    check("run_llm: a good answer reaches on_result only", results == [(reply, prompt)] and not bad and not errs, (results, bad, errs))
    asyncio.run(run(f"{R}\nHi\n{P}\nHalf a prompt"))
    check("run_llm: a truncated answer goes to on_bad with its RAW text and is never saved", not results and bad and "END" in bad[0][0] and "Half a prompt" in bad[0][1], (results, bad))
    asyncio.run(run('{"reply": "hi", "prompt": "x"}'))
    check("run_llm: an old JSON answer is refused", not results and bad, (results, bad))

    print("ALL PASS" if not _fails else f"{len(_fails)} FAILED: {_fails}")
    return 0 if not _fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
