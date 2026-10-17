"""เทสต์ Unified LLM Debug Mode (task3.md)

รันด้วย:  python -m story_generator.tests.test_llm_debug_mode

Debug OFF ต้องไม่เปลี่ยน prompt / ไม่เขียนไฟล์ / ไม่เรียก LLM เพิ่ม
Debug ON ต้อง append instruction ในคำขอเดิม, เก็บ raw ก่อน parse, แยก native thinking
กับ decision report, และความล้มเหลวตอนเขียนไฟล์ห้ามทำให้ pipeline พัง

ไม่ยิง Local/Grok จริง — mock ชั้น _grok_chat_completions เท่านั้น
"""
import asyncio
import inspect
import json
import tempfile
from pathlib import Path

from .. import llm_engine as L

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


_SRC = inspect.getsource(L)
_UI = Path(__file__).resolve().parents[1].joinpath("main_ui.py").read_text(encoding="utf-8")


# ── A: Settings + persist + default OFF ────────────────────────────────
check("A Settings label is LLM Debug Logging",
      "LLM Debug Logging" in _UI)
check("A setting persisted as llm_debug_logging",
      '"llm_debug_logging"' in _UI or "llm_debug_logging" in _UI)
check("A default is OFF (saved.get False)",
      'saved.get("llm_debug_logging", False)' in _UI)
check("A module default is False",
      L._llm_debug_logging is False)
check("A debug files go under debug/ with llm_debug_ prefix",
      "llm_debug_" in _SRC and "DEBUG_DIR" in _SRC)


# ── helpers ────────────────────────────────────────────────────────────
def _report_block(**fields) -> str:
    lines = [L._DEBUG_REPORT_START]
    for k in L._DEBUG_REPORT_KEYS:
        lines.append(f"{k}: {fields.get(k, '')}")
    lines.append(L._DEBUG_REPORT_END)
    return "\n".join(lines)


def _grok_resp(content: str, reasoning=None, extra_msg=None):
    msg = {"content": content}
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    if extra_msg:
        msg.update(extra_msg)
    return {
        "id": "chatcmpl-test",
        "model": "grok-4-latest",
        "choices": [{"finish_reason": "stop", "message": msg}],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 22,
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
    }


async def _call(sys_p="SYS", user_p="USER", **kw):
    return await L._call_grok_api(sys_p, user_p, 128, "fake-key", "grok-4-latest", **kw)


def _latest(tmp: Path) -> dict:
    files = sorted(tmp.glob("llm_debug_*.json"))
    check("debug file was written", bool(files), list(tmp.iterdir()))
    if not files:
        return {}
    return json.loads(files[-1].read_text(encoding="utf-8"))


# ── B: debug OFF — production unchanged ────────────────────────────────
async def t_debug_off_no_files_no_instruction():
    tmp = Path(tempfile.mkdtemp())
    sent = []
    orig = L._grok_chat_completions
    L.set_llm_debug_logging(False, debug_dir=tmp)
    async def fake(messages, max_tokens, api_key, model, on_log=None, temperature=None):
        sent.append(messages)
        return _grok_resp('{"shots": []}')
    L._grok_chat_completions = fake
    try:
        out = await _call()
        check("B OFF: returned provider content unchanged",
              out == '{"shots": []}', out)
        check("B OFF: user prompt has no debug instruction",
              L._DEBUG_REPORT_START not in sent[0][1]["content"],
              sent[0][1]["content"][-200:])
        check("B OFF: system prompt unchanged",
              sent[0][0]["content"] == "SYS")
        check("B OFF: wrote no debug files",
              list(tmp.glob("llm_debug_*.json")) == [])
        check("B OFF: one provider call (not two)",
              len(sent) == 1, len(sent))
    finally:
        L._grok_chat_completions = orig
        L.set_llm_debug_logging(False)


# ── C: debug ON — same call, instruction, strip, file ─────────────────
async def t_debug_on_same_call_and_file():
    tmp = Path(tempfile.mkdtemp())
    sent = []
    orig = L._grok_chat_completions
    L.set_llm_debug_logging(True, debug_dir=tmp)
    body = '{"shots": [{"shot": 1, "prompt": "a cat"}]}\n' + _report_block(
        understood_task="produce fill prompts",
        key_decisions="kept the cat",
        conflicts="none",
        reason="payload asked for it",
    )
    async def fake(messages, max_tokens, api_key, model, on_log=None, temperature=None):
        sent.append({"messages": messages, "max_tokens": max_tokens, "model": model})
        return _grok_resp(body, reasoning="native chain stays separate")
    L._grok_chat_completions = fake
    try:
        with L._llm_debug_scope("fill", shot_ids=[1], batch_ids=["fill:1"]):
            out = await _call("system here", "user here")
        check("C ON: one provider call (decision report is not a second call)",
              len(sent) == 1, len(sent))
        user_sent = sent[0]["messages"][1]["content"]
        check("C ON: debug instruction appended to the same user prompt",
              "user here" in user_sent and L._DEBUG_REPORT_START in user_sent,
              user_sent[-300:])
        check("C ON: system prompt not rewritten",
              sent[0]["messages"][0]["content"] == "system here")
        check("C ON: returned content has the report stripped so parsers still work",
              out is not None and "a cat" in out and L._DEBUG_REPORT_START not in out,
              out)
        rec = _latest(tmp)
        check("C ON: filename uses llm_debug_<ts>_<provider>_<stage>_<id>",
              rec and rec.get("provider") == "grok" and rec.get("stage") == "fill")
        check("C ON: request stores the prompts actually sent",
              rec["request"]["system_prompt"] == "system here"
              and L._DEBUG_REPORT_START in rec["request"]["user_prompt"])
        check("C ON: raw_content is the complete unstripped provider text",
              rec["raw_content"] == body)
        check("C ON: complete raw_response dict is stored (not rewritten)",
              isinstance(rec.get("raw_response"), dict)
              and rec["raw_response"].get("id") == "chatcmpl-test")
        check("C ON: native thinking stored separately from the decision report",
              rec.get("native_thinking", {}).get("reasoning_content")
              == "native chain stays separate"
              and rec.get("debug_decision_report", {}).get("understood_task")
              == "produce fill prompts")
        check("C ON: token usage + finish reason + elapsed captured",
              rec.get("finish_reason") == "stop"
              and (rec.get("token_usage") or {}).get("prompt_tokens") == 11
              and isinstance(rec.get("elapsed_ms"), (int, float)))
        check("C ON: shot/batch ids from the stage scope",
              rec.get("shot_ids") == [1] and rec.get("batch_ids") == ["fill:1"])
        check("C ON: request / raw_response / debug_decision_report are separate keys",
              all(k in rec for k in ("request", "raw_response", "debug_decision_report")))
    finally:
        L._grok_chat_completions = orig
        L.set_llm_debug_logging(False)


# ── D: native thinking absent is None, never manufactured ──────────────
async def t_no_native_thinking_manufactured():
    tmp = Path(tempfile.mkdtemp())
    orig = L._grok_chat_completions
    L.set_llm_debug_logging(True, debug_dir=tmp)
    async def fake(messages, max_tokens, api_key, model, on_log=None, temperature=None):
        return _grok_resp("hello")
    L._grok_chat_completions = fake
    try:
        await _call()
        rec = _latest(tmp)
        check("D native_thinking is null when the provider did not expose it",
              rec.get("native_thinking") in (None, {}), rec.get("native_thinking"))
    finally:
        L._grok_chat_completions = orig
        L.set_llm_debug_logging(False)


# ── E: write failure must not stop the pipeline ────────────────────────
async def t_write_failure_does_not_break():
    not_a_dir = Path(tempfile.mkdtemp()) / "blocked"
    not_a_dir.write_text("not a directory", encoding="utf-8")
    orig = L._grok_chat_completions
    L.set_llm_debug_logging(True, debug_dir=not_a_dir)
    async def fake(messages, max_tokens, api_key, model, on_log=None, temperature=None):
        return _grok_resp("still works")
    L._grok_chat_completions = fake
    try:
        out = await _call()
        check("E debug write failure still returns the LLM output",
              out == "still works", out)
    finally:
        L._grok_chat_completions = orig
        L.set_llm_debug_logging(False)


# ── F: Grok batch still parses after the report is stripped ────────────
async def t_batch_strips_report_and_tags_shots():
    tmp = Path(tempfile.mkdtemp())
    sent = []
    orig = L._grok_chat_completions
    L.set_llm_debug_logging(True, debug_dir=tmp)
    payload = (
        '{"shots": [{"shot": 2, "prompt": "wide street"}]}\n'
        + _report_block(understood_task="fill batch", key_decisions="wide",
                        conflicts="none", reason="asked")
    )
    async def fake(messages, max_tokens, api_key, model, on_log=None, temperature=None):
        sent.append(messages)
        return _grok_resp(payload)
    L._grok_chat_completions = fake
    try:
        items = await L._grok_batch_call(
            "SYS", [{"shot": 2, "scene_desc": "x"}], 4096,
            "key", "grok-4-latest", "Fill")
        check("F batch parse survives the appended decision report",
              len(items) == 1 and items[0].get("shot") == 2
              and "wide street" in items[0].get("prompt", ""), items)
        check("F batch is still a single provider call",
              len(sent) == 1, len(sent))
        rec = _latest(tmp)
        check("F batch debug record is tagged fill with shot ids",
              rec.get("stage") == "fill" and rec.get("shot_ids") == [2], rec)
    finally:
        L._grok_chat_completions = orig
        L.set_llm_debug_logging(False)


# ── G: extract helpers ─────────────────────────────────────────────────
def t_helpers():
    main, report = L._split_debug_decision_report(
        'KEEP ME\n' + _report_block(understood_task="outline",
                                    key_decisions="split A/B",
                                    conflicts="none",
                                    reason="two-pass"))
    check("G strip leaves the production payload",
          main == "KEEP ME", main)
    check("G parsed the four short decision fields",
          report == {
              "understood_task": "outline",
              "key_decisions": "split A/B",
              "conflicts": "none",
              "reason": "two-pass",
          }, report)
    check("G no-op when the block is absent",
          L._split_debug_decision_report("just text") == ("just text", None))
    native = L._extract_native_thinking(_grok_resp("x", reasoning="abc"))
    check("G native thinking copies reasoning_content as returned",
          native == {"reasoning_content": "abc"}, native)
    check("G native thinking is None when no such field exists",
          L._extract_native_thinking(_grok_resp("x")) in (None, {}))
    L.set_llm_debug_logging(False)
    check("G instruction is not appended when debug is OFF",
          "hello" == L._apply_debug_instruction("hello"))
    L.set_llm_debug_logging(True)
    try:
        applied = L._apply_debug_instruction("hello")
        check("G instruction is appended once when debug is ON",
              applied.startswith("hello") and L._DEBUG_REPORT_START in applied
              and applied.count(L._DEBUG_REPORT_START) == 1)
        check("G instruction is not appended twice",
              L._apply_debug_instruction(applied) == applied)
        with L._llm_debug_scope(outline_pass="story"):
            check("G Outline Step 1 does not append DEBUG_DECISION_REPORT",
                  "hello" == L._apply_debug_instruction("hello"))
        with L._llm_debug_scope(outline_pass="shots"):
            check("G Outline Step 2 does not append DEBUG_DECISION_REPORT",
                  "hello" == L._apply_debug_instruction("hello"))
    finally:
        L.set_llm_debug_logging(False)


# ── H: stage entry points are tagged ───────────────────────────────────
_src_flat = _SRC.replace("\r\n", "\n")
for stage, fn in (
    ("groom", "run_story_grooming"),
    ("outline", "run_outline_story_pass"),
    ("outline", "run_outline_shots_pass"),
    ("fill", "run_fill_prompts"),
    ("checker", "run_check_prompts"),
    ("director", "run_director_prompts"),
):
    needle = f'@_llm_debug_stage("{stage}")\nasync def {fn}'
    check(f"H {fn} tagged as {stage}", needle in _src_flat, needle)


def main():
    t_helpers()
    asyncio.run(t_debug_off_no_files_no_instruction())
    asyncio.run(t_debug_on_same_call_and_file())
    asyncio.run(t_no_native_thinking_manufactured())
    asyncio.run(t_write_failure_does_not_break())
    asyncio.run(t_batch_strips_report_and_tags_shots())
    print()
    if _fails:
        print(f"{len(_fails)} FAIL: {', '.join(_fails)}")
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
