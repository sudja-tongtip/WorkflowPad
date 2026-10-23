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


def _try(raw):
    """True when the parser accepted `raw` (used to prove that something is NOT accepted)."""
    try:
        PL._parse_llm_output(raw)
        return True
    except PL.LLMFormatError:
        return False


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
    check("text before REPLY is not part of the answer (only the blocks count)", PL._parse_llm_output(f"Sure!\n{R}\na\n{P}\nx\n{E}") == ("a", "x"))
    check("text after END is not part of the answer (only the blocks count)", PL._parse_llm_output(f"{R}\na\n{P}\nx\n{E}\nBye") == ("a", "x"))
    check("empty REPLY is refused", refused(f"{R}\n\n{P}\nx\n{E}", "REPLY section is empty")[0])
    check("a single '=' marker word inside a sentence does not count", refused(f"{R}\na\nsee =PROMPT= here\n{E}", "PROMPT separator line is missing")[0])
    check("a fully decorated marker in a longer line is still a marker (text before goes to the section before, text after to the next)", PL._parse_llm_output(f"{R}\na\nsee {P} here\n{E}") == ("a\nsee", "here"))
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

    # a model often writes a different NUMBER of '=' (real answer from Grok: ten at the end); the word on a line of its own is the protocol
    real = "=========== REPLY ===========\nได้ครับ ปรับผ้าให้บางลงแล้ว\n\n=========== PROMPT ===========\nA Thai woman steps out of a mural.\nLine two.\n\n=========== END =========="
    check("a separator with a different number of '=' is accepted (real Grok answer)", PL._parse_llm_output(real) == ("ได้ครับ ปรับผ้าให้บางลงแล้ว", "A Thai woman steps out of a mural.\nLine two."))
    check("lower case and extra spaces around the word are accepted", PL._parse_llm_output("=== reply ===\nHi\n==== Prompt ====\nx\n====== end ======")[0] == "Hi")
    check("a separator word with other text on the line does not count", refused(f"{R}\na\n{P}\nx\n=========== END of prompt ===========", "END separator line is missing")[0])

    # ---- many ways a model decorates the same three words ----------------------------------------------------------------------------------------------------------------------
    def with_end(end_line):
        return f"{R}\nHi\n{P}\nx\n{end_line}"

    ends = ["=== END ===", "=END=", "= END =", "==END==", "===END", "END===", "=========== END ==========", "============= END =============", "--- END ---", "*** END ***", "**END**",
            "[END]", "<END>", "### END ###", "## end ##", "~~~ End ~~~", "___ END ___", "═══ END ═══", "＝＝ END ＝＝", "=== END ===   ", "  === END ===", "=== END ===\r", "= = END = =", "END:", "<<< END >>>", "-=- END -=-"]
    bad_ends = [x for x in ends if PL._parse_llm_output(with_end(x)) != ("Hi", "x")]
    check("26 decorations of END are all accepted", not bad_ends, bad_ends)
    replies = ["=== REPLY ===", "=REPLY=", "**REPLY**", "[reply]", "### Reply ###", "--- REPLY ---", "REPLY:", "<REPLY>"]
    prompts = ["=== PROMPT ===", "=PROMPT=", "**PROMPT**", "[prompt]", "### Prompt ###", "--- PROMPT ---", "PROMPT:", "<PROMPT>"]
    bad = [(a, b) for a, b in zip(replies, prompts) if PL._parse_llm_output(f"{a}\nHi\n{b}\nx\n=== END ===") != ("Hi", "x")]
    check("REPLY and PROMPT decorations are accepted the same way", not bad, bad)
    check("mixed decorations in one answer", PL._parse_llm_output("**REPLY**\nHi\n=== PROMPT ===\nx\n--- END ---") == ("Hi", "x"))
    not_sep = ["END", "end", "THE END", "END of prompt", "ENDING", "=== THE END ===", "= ENDS =", "== SEND ==", "=== PREEND ===", "=== END PROMPT ===", "--- END 2 ---", "=== E N D ===", "==="]
    leaked = [x for x in not_sep if _try(with_end(x))]
    check("a bare word, extra words, other words or no word are NOT an END separator", not leaked, leaked)
    check("the wrapping code fence around the whole answer is ignored", PL._parse_llm_output("```\n" + with_end("=== END ===") + "\n```") == ("Hi", "x") and PL._parse_llm_output("```text\n" + with_end("=END=") + "\n```") == ("Hi", "x"))
    check("a code fence inside the prompt is kept as written", PL._parse_llm_output(f"{R}\nHi\n{P}\n```\nx\n```\n=== END ===")[1] == "```\nx\n```")
    check("an ordinary line 'End' inside the prompt text does not split it", PL._parse_llm_output(f"{R}\nHi\n{P}\nThe story: start\nEnd\nmiddle end of day\n=== END ===")[1] == "The story: start\nEnd\nmiddle end of day")
    check("a decorated marker followed by text on the same line", PL._parse_llm_output("=== REPLY === Hi there\n=== PROMPT === A woman\nsecond line\n=== END ===") == ("Hi there", "A woman\nsecond line"))
    check("a decorated END glued to the last words", PL._parse_llm_output("=== REPLY ===\nHi\n=== PROMPT ===\nA woman stands === END ===") == ("Hi", "A woman stands"))
    check("ordinary lines with '=' or a label are not split", PL._parse_llm_output("=== REPLY ===\nHi: a == b\n=== PROMPT ===\nPrompt: a woman\nCamera = 35mm\n=== END ===") == ("Hi: a == b", "Prompt: a woman\nCamera = 35mm"))
    check("a single '=' next to the word inside a sentence is not a marker", _try("=== REPLY ===\nHi\n=== PROMPT ===\nx =END= y\n=== END ===") is True and PL._parse_llm_output("=== REPLY ===\nHi\n=== PROMPT ===\nx =END= y\n=== END ===")[1] == "x =END= y")
    check("two END lines are still refused (not guessed)", refused(f"{R}\nHi\n{P}\nx\n=== END ===\n--- END ---", "END separator line appears 2 times")[0])
    check("a custom system prompt with decorated separators counts as having the format", PL._prompt_has_contract("rules\n=== REPLY ===\n...\n=== PROMPT ===\n...\n=== END ==="))
    check("a system prompt that only mentions the words in sentences does not", not PL._prompt_has_contract("Write a reply, then the prompt, then THE END."))
    r = refused(f"{R}\na\n{P}\nx", "END separator line is missing")
    check("the missing-END message tells how it must be written, and mentions a cut-off only as a possibility", r[0] and "=========== END ===========" in r[1] and "stops in the middle" in r[1], r)

    print("ALL PASS" if not _fails else f"{len(_fails)} FAILED: {_fails}")
    return 0 if not _fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
