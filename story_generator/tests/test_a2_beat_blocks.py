"""A2 local output protocol: beats separated by ===BEAT===, ended by ===END===. Structure only; the text of a beat is kept exactly as written. No LLM, no network.

Run:  python -m story_generator.tests.test_a2_beat_blocks
"""
import asyncio
import json

from .. import llm_engine_a2 as A

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


def parse(raw, parent="E5"):
    return A._parse_beat_blocks(raw, parent)


def main():
    two = "HAPPENS: Mira lifts the rifle\nDETAILS: both hands on the grip\n\n===BEAT===\n\nHAPPENS: Mira fires\nDETAILS: recoil in her shoulder\n\n===END==="
    beats, why = parse(two)
    check("two beats parse", not why and len(beats) == 2, why)
    check("ids come from the macro id in order", [b["id"] for b in beats] == ["E5.1", "E5.2"] and all(b["parent"] == "E5" for b in beats), beats)
    check("the text is kept exactly as written", beats[0]["content"] == "HAPPENS: Mira lifts the rifle\nDETAILS: both hands on the grip" and beats[1]["content"].endswith("recoil in her shoulder"), beats)
    check("schema is {id, parent, content}", all(set(b) == {"id", "parent", "content"} for b in beats))

    one, why = parse("HAPPENS: One frozen moment\nDETAILS: she stands\n===END===")
    check("a macro with one beat works", not why and [b["id"] for b in one] == ["E5.1"], why)
    check("Thai text is carried untouched", parse("HAPPENS: มิราเหนี่ยวไก\nDETAILS: มือจับด้าม\n===END===")[0][0]["content"] == "HAPPENS: มิราเหนี่ยวไก\nDETAILS: มือจับด้าม")
    check("quotes and angle brackets inside a beat are fine", not parse('HAPPENS: she says "a < b"\nDETAILS: x\n===END===')[1])

    check("a trailing chat-template marker is ignored", not parse(two + "\n</end_of_turn>")[1] and not parse(two + "</start_of_turn>")[1])
    check("a code fence around the answer is ignored", not parse("```\n" + two + "\n```")[1])

    _b, why = parse("HAPPENS: a\nDETAILS: b\n===BEAT===\nHAPPENS: c\nDETAILS: d")
    check("a missing END is refused with its own reason", any("no ===END===" in w for w in why), why)
    _b, why = parse("HAPPENS: a\nDETAILS: b\n===BEAT===\n===END===")
    check("an empty beat is refused", any("beat 2 is empty" in w for w in why), why)
    _b, why = parse("===BEAT===\nHAPPENS: a\nDETAILS: b\n===END===")
    check("an empty first beat is refused", any("beat 1 is empty" in w for w in why), why)
    _b, why = parse(two + "\nSome closing comment")
    check("text after END is refused", any("after ===END===" in w for w in why), why)
    _b, why = parse("HAPPENS: a\nDETAILS: b\n===NEXT===\nHAPPENS: c\nDETAILS: d\n===END===")
    check("another marker line is refused", any("===NEXT===" in w for w in why), why)
    _b, why = parse("<E5.1>\nHAPPENS: a\nDETAILS: b\n</E5.1>\n===END===")
    check("a leftover beat tag is refused", any("tag" in w for w in why), why)
    _b, why = parse("Here are the beats:\nHAPPENS: a\nDETAILS: b\n===END===")
    check("text before the first HAPPENS is refused", any("must start with HAPPENS" in w for w in why), why)
    _b, why = parse("HAPPENS: a\n===END===")
    check("a beat without DETAILS is refused", any("DETAILS" in w for w in why), why)
    _b, why = parse("")
    check("an empty reply is refused", why == ["the reply is empty"], why)

    # the old E4 failure shape (mismatched / missing closing tags) cannot exist in this format; what the model sends instead is judged by the same rules
    old_shape = "<E4.1>\nHAPPENS: a\nDETAILS: b\n</E4.1>\n<E4.2>\nHAPPENS: x\n</E4.3>\nHAPPENS: y\n</E4.4>"
    check("a reply in the OLD tag form is refused (no END, tags)", parse(old_shape, "E4")[1])

    # the loop: retry only on a failed validation, raw of every attempt kept, 3 attempts, no silent success
    calls = []
    replies = ["HAPPENS: a\nDETAILS: b\n===BEAT===\nHAPPENS: c\nDETAILS: d", "HAPPENS: a\nDETAILS: b\n===END===", None]

    async def fake_call(wf, system, user, *, max_tokens, session_id, on_log=None, reset=True):
        calls.append((user, reset))
        return replies[len(calls) - 1]

    real_call = A._call_local_text
    A._call_local_text = fake_call
    errs: list = []

    async def on_err(m):
        errs.append(m)

    async def run(macro_text):
        cap: dict = {}
        out = await A.run_a2_vnext({"macro_events": [{"id": "E5", "content": macro_text}], "character_sheet": "", "world_sheet": "", "locations": "", "important_objects": "", "continuity_notes": ""},
                                   "gemma4_llm", on_err, None, mode="sequential", chat_capture=cap)
        return out, cap

    try:
        out, cap = asyncio.run(run("m"))
    finally:
        A._call_local_text = real_call
    check("retry only after a failed check, then accepted", out and out["micro_beats"][0]["id"] == "E5.1" and len(calls) == 2, (out, len(calls)))
    check("the retry is a short correction in the SAME session", calls[1][1] is False and "===BEAT===" in calls[1][0] and "no ===END===" in calls[1][0], calls[1])
    check("the first attempt is a fresh full request", calls[0][1] is True and "OUTPUT FORMAT" in calls[0][0])

    calls.clear()
    replies[:] = ["x", "y", "z"]
    A._call_local_text = fake_call
    try:
        out, cap = asyncio.run(run("m"))
    finally:
        A._call_local_text = real_call
    turns = cap["sessions"][-1]["turns"]
    check("three failed attempts end in an error, never a silent success", out is None and errs and "after 3 attempts" in errs[-1], (out, errs))
    check("the raw reply of EVERY attempt is kept", [t["raw_response"] for t in turns] == ["x", "y", "z"], turns)

    print("ALL PASS" if not _fails else f"{len(_fails)} FAILED: {_fails}")
    return 0 if not _fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
