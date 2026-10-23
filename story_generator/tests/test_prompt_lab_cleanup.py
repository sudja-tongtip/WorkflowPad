"""Prompt Lab separator format - cleanup: chat history after a malformed answer, sessions, System Prompt change, custom prompt conversion. Mock data only. No LLM, no network.

Run:  python -m story_generator.tests.test_prompt_lab_cleanup
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import flet as ft

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
_real_app = ft.app
ft.app = lambda *a, **k: None
spec = importlib.util.spec_from_file_location("prompt_lab_cleanup_under_test", ROOT / "prompt_lab.py")
PL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PL)
ft.app = _real_app

_fails: list = []
R, P, E = PL._SEP_REPLY, PL._SEP_PROMPT, PL._SEP_END
GOOD = f"{R}\nDone.\n{P}\nA woman stands.\n{E}"
BAD = f"{R}\nDone.\n{P}\nA woman stands in"            # cut off: no END

# the kind of custom prompt a user has (persona + flow + rules + the old JSON output lines)
OLD_CUSTOM = "\n".join([
    "You are an AI image prompt engineer and friendly assistant for Krea2.",
    "Always reply in Thai. Prompt must be in English.",
    "Output ONLY this JSON on a single line, nothing else:",
    '{"reply": "Thai reply here", "prompt": "English prompt here"}',
    "",
    "FLOW:",
    '- If user wants to change a prompt: explain in 1-2 sentences, ask for confirmation, set prompt to ""',
    "- If user confirms: make the change and return the full updated prompt",
    "",
    "PROMPT RULES:",
    "- Keep ALL details from the current prompt, only change what user asked",
    "",
    "OUTPUT:",
    "- Single line JSON only, no pretty-print, no line breaks in the JSON structure",
    '- set prompt to "" if no prompt change is needed',
])


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


def grok_turn(history, text, answer=None, boom=None):
    seen: list = []

    async def call(sp, msgs):
        seen.append(list(msgs))
        if boom:
            raise boom
        return answer

    try:
        res = asyncio.run(PL.grok_chat_turn(history, text, call, "SYS"))
        return res, None, seen
    except Exception as exc:
        return None, exc, seen


class _Local:
    """Mock ComfyUI for one Local model: queue captures the session id of every call, history serves the next scripted answer."""
    def __init__(self, answers):
        self.answers, self.sessions, self.n = list(answers), [], 0
        wf = {"1": {"class_type": "LLMSessionChatNode", "inputs": {}}}

        class _F:
            def exists(self): return True
            def read_text(self, encoding=None): return json.dumps(wf)

        class _D:
            def __truediv__(self, name): return _F()

        PL.LLM_DIR = _D()
        outer = self

        def queue(w, cid):
            outer.sessions.append(w["1"]["inputs"]["session_id"])
            outer.n += 1
            return f"pid{outer.n:09d}"

        PL.comfy_queue = queue
        PL.comfy_hist = lambda pid: {pid: {"status": {"status_str": "success"}, "outputs": {"3": {"text": [outer.answers[outer.n - 1]]}}}}


def local_turn(state, mock, text="hi"):
    got = {"ok": [], "bad": [], "err": []}

    async def on_result(t, p): got["ok"].append((t, p))
    async def on_bad(r, raw): got["bad"].append((r, raw))
    async def on_err(m): got["err"].append(m)

    asyncio.run(asyncio.wait_for(PL.local_chat_turn(state, text, {"system_prompt": PL._DEFAULT_SYS}, "x.json", on_result, on_err, on_bad=on_bad), 20))
    return got


def main():
    # ---- 1. Grok valid: the history gets the user turn and the answer ---------------------------------------------------------------------------------------------------
    hist: list = []
    res, err, seen = grok_turn(hist, "make it red", GOOD)
    check("Grok valid: the history holds the user turn and the accepted answer", err is None and hist == [{"role": "user", "content": "make it red"}, {"role": "assistant", "content": GOOD}], (hist, err))
    check("Grok valid: reply and prompt are parsed", res[1:] == ("Done.", "A woman stands."), res)
    res, err, seen = grok_turn(hist, "and bigger", GOOD)
    check("Grok valid: the next call carries the earlier turns plus the new user message", len(seen[0]) == 3 and seen[0][-1]["content"] == "and bigger" and len(hist) == 4, (len(seen[0]), len(hist)))

    # ---- 2. Grok invalid: nothing is kept, no orphan user turn -------------------------------------------------------------------------------------------------------------
    before = json.loads(json.dumps(hist))
    res, err, seen = grok_turn(hist, "make it blue", BAD)
    check("Grok invalid: the error carries the raw answer", isinstance(err, PL.LLMFormatError) and err.raw == BAD and "END" in str(err), err)
    check("Grok invalid: the history is exactly as before (no wrong answer, no orphan user turn)", hist == before, hist)
    res, err, seen = grok_turn(hist, "make it blue", None, boom=RuntimeError("HTTP 500"))
    check("Grok call failed: the history is exactly as before", isinstance(err, RuntimeError) and hist == before, (err, hist))
    res, err, seen = grok_turn(hist, "make it blue", GOOD)
    check("Grok retry after a failure: no duplicate user turn", hist == before + [{"role": "user", "content": "make it blue"}, {"role": "assistant", "content": GOOD}], hist)

    # ---- 3 + 4. Local: a malformed answer rotates the session, a good one keeps it ------------------------------------------------------------------------------------------
    state = {"session_id": "aaaa1111"}
    mock = _Local([BAD, GOOD, GOOD])
    got = local_turn(state, mock)
    check("Local invalid: the answer is refused with its raw text", got["bad"] and got["bad"][0][1] == BAD and not got["ok"], got)
    check("Local invalid: the session id changed", state["session_id"] != "aaaa1111", state)
    new_id = state["session_id"]
    got = local_turn(state, mock)
    check("Local: the next call uses the NEW session id", mock.sessions == ["aaaa1111", new_id], mock.sessions)
    check("Local valid: the session id stays", state["session_id"] == new_id and got["ok"], state)
    got = local_turn(state, mock)
    check("Local valid x2: the same session continues", mock.sessions == ["aaaa1111", new_id, new_id], mock.sessions)

    # ---- 5. System Prompt changed: every Local model gets a new session, Grok history is cleared, what is on screen stays ----------------------------------------------------
    PL_GROK = "__grok__"
    states = {"gemma4_llm": {"session_id": "g1", "chat": ["bubble"]}, "qwen3.5_llm": {"session_id": "q1", "chat": ["bubble", "bubble"]}, PL_GROK: {"session_id": "k1", "chat": ["x"]}}
    gh = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    done = PL._reset_conversations(states, PL_GROK, gh)
    check("System Prompt change: every Local model has a new session", states["gemma4_llm"]["session_id"] != "g1" and states["qwen3.5_llm"]["session_id"] != "q1" and sorted(done) == ["gemma4_llm", "qwen3.5_llm"], states)
    check("System Prompt change: the Grok history is cleared", gh == [])
    check("System Prompt change: the shown chats are not touched", states["gemma4_llm"]["chat"] == ["bubble"] and states["qwen3.5_llm"]["chat"] == ["bubble", "bubble"] and states[PL_GROK]["chat"] == ["x"])

    # ---- 6. Custom prompt migration -----------------------------------------------------------------------------------------------------------------------------------------------
    new, removed, note = PL._convert_system_prompt(OLD_CUSTOM)
    check("migration: converted", new is not None and not note, (note, removed))
    check("migration: only the three old-format lines were removed", len(removed) == 3 and all(("JSON" in r or '"reply"' in r) for r in removed), removed)
    kept_lines = [ln for ln in OLD_CUSTOM.splitlines() if ln not in removed]
    new_lines = new.splitlines()
    want = [ln for ln in kept_lines if ln.strip()]
    pos, at = [], 0
    for ln in want:                                   # each kept line is found after the previous one: present, and in the original order
        at = new_lines.index(ln, at)
        pos.append(at)
    check("migration: every other custom line is still there, in order", len(pos) == len(want) and pos == sorted(pos), (want, new_lines))
    check("migration: exactly one Output Contract (the separator one)", PL._prompt_has_contract(new) and new.count(R) == 1 and new.count(P) == 1 and new.count(E) == 1)
    check("migration: no JSON contract is left beside it", not PL._old_contract_lines(new) and "JSON" not in new, new)
    again, removed2, note2 = PL._convert_system_prompt(new)
    check("migration: converting an already converted prompt changes nothing", again == new and removed2 == [], (note2, removed2))
    plain, _r, _n = PL._convert_system_prompt("Be helpful.\nUse Thai.")
    check("migration: a prompt with no old format just gets the contract added", plain.startswith("Be helpful.\nUse Thai.") and PL._prompt_has_contract(plain))
    cfg: dict = {}
    PL._remember_backup(cfg, OLD_CUSTOM)
    PL._remember_backup(cfg, OLD_CUSTOM)
    PL._remember_backup(cfg, "another prompt")
    check("backup: the original is kept (latest + every different one), never lost", cfg["system_prompt_backup"] == "another prompt" and cfg["system_prompt_backups"] == [OLD_CUSTOM, "another prompt"], cfg)

    # ---- 7. Migration failure: nothing changes ---------------------------------------------------------------------------------------------------------------------------------
    pretty = "Be helpful.\nReturn JSON like this:\n{\n  \"reply\": \"...\",\n  \"prompt\": \"...\"\n}\n"
    original = pretty
    res, removed, why = PL._convert_system_prompt(pretty)
    check("migration failure: pretty-printed JSON left over is refused", res is None and "left over" in why, (res, why))
    check("migration failure: the original text is untouched", pretty == original)
    many = "\n".join(f"line {n} says JSON" for n in range(12))
    res, removed, why = PL._convert_system_prompt(many)
    check("migration failure: too many old-format lines are refused", res is None and "too many" in why and len(removed) == 12, (res, why))
    twice = f"x\n{R}\n{P}\n{E}\n{R}\n"
    res, _r, why = PL._convert_system_prompt(twice)
    check("migration failure: a prompt holding the separators more than once is refused", res is None and "exactly one" in why, (res, why))

    # ---- 8. the old app default is replaced on its own (backup first, nothing else reset); a custom prompt is never touched ------------------------------------------------------
    saved_files: list = []
    PL.save_llm_cfg = lambda c: saved_files.append(dict(c))          # never write the user's real settings file from a test
    cfg = {"system_prompt": PL._OLD_DEFAULT_SYS, "max_tokens": 2048}
    note = PL._auto_upgrade_default_prompt(cfg)
    check("old default: replaced by the new default, with a notice", cfg["system_prompt"] == PL._DEFAULT_SYS and "backup" in note, (note,))
    check("old default: the previous one is kept as a backup", cfg["system_prompt_backup"] == PL._OLD_DEFAULT_SYS and PL._OLD_DEFAULT_SYS in cfg["system_prompt_backups"])
    check("old default: Max Tokens and the other settings are left as they are", cfg["max_tokens"] == 2048 and saved_files)
    cfg2 = {"system_prompt": OLD_CUSTOM}
    check("custom old prompt: not touched by the automatic upgrade", PL._auto_upgrade_default_prompt(cfg2) == "" and cfg2["system_prompt"] == OLD_CUSTOM and "system_prompt_backup" not in cfg2)
    cfg3 = {"system_prompt": PL._DEFAULT_SYS}
    check("new default: nothing to do", PL._auto_upgrade_default_prompt(cfg3) == "" and cfg3["system_prompt"] == PL._DEFAULT_SYS)
    check("the default Max Tokens is 8192", PL._DEFAULT_MAX_TOKENS == 8192)

    print("ALL PASS" if not _fails else f"{len(_fails)} FAILED: {_fails}")
    return 0 if not _fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
