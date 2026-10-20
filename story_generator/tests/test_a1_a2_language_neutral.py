"""A1 / A2 structure checks that must hold for every language (no LLM, no network).

Run:  python -m story_generator.tests.test_a1_a2_language_neutral

Python reads protocol tokens and structure only. The story text (Thai, English, Japanese, Chinese, Arabic, mixed) must pass through unchanged, and no word list may decide
whether a reply is accepted.
"""
import asyncio

from story_generator import a1_static_conversation as C
from story_generator import a1_novel_conversation as N
from story_generator import llm_engine_a1_static as S
from story_generator import llm_engine_a2 as A2

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


# one scene, six languages (the test content of the brief)
TEXTS = {
    "en": "Mary moves a glass to the table.",
    "th": "มาลียกแก้ววางบนโต๊ะ",
    "ja": "マリはグラスをテーブルに置く",
    "zh": "玛丽把杯子放在桌上",
    "ar": "وضعت مريم الكوب على الطاولة",
    "mixed": "มาลี hands the glass to 山田",
}
ALL = list(TEXTS.values())

FIELDS = ("name", "alias", "trigger", "age", "gender", "nationality", "body", "face", "skin", "marks", "init_hair", "init_cloth")


def sheet(cid, values):
    return "\n".join(f"<{cid}.{f}>{values.get(f, '' if f == 'trigger' else 'x')}</{cid}.{f}>" for f in FIELDS)


def conv_with(history_steps):
    """A local conversation whose accepted history holds the given (step, reply) pairs."""
    cv = C._Conversation(backend="local", wf_template={}, session_id="t", grok_api_key="", grok_model="", on_log=None, cap={})
    cv.source = "SOURCE " + TEXTS["mixed"]
    for step, reply in history_steps:
        cv.history += [{"role": "user", "content": "request " + step, "step": step, "short": "TASK - " + step}, {"role": "assistant", "content": reply, "step": step}]
    return cv


def main():
    # ---- P0: the rebuilt session carries every accepted answer, STORY EVENT k included, in order ---------------------------------------
    steps = [("CHARACTERS", sheet("char1", {"name": "มาลี"})), ("STORY EVENT 1", "<event_1>" + TEXTS["th"] + "</event_1>"),
             ("STORY EVENT 2", "<event_2>" + TEXTS["ja"] + "\n" + TEXTS["ar"] + "</event_2>"), ("STORY EVENT 3", "<event_3>" + TEXTS["mixed"] + "</event_3>")]
    cv = conv_with(steps)
    text = cv._transcript("STORY EVENT 4", [], "TASK - STORY EVENT 4: next", all_kinds=True)
    pos = [text.find(r) for _s, r in steps]
    check("rebuild carries characters and events 1-3", all(p >= 0 for p in pos), pos)
    check("rebuild keeps the order", pos == sorted(pos), pos)
    check("rebuild keeps every language exactly", all(t in text for t in (TEXTS["th"], TEXTS["ja"], TEXTS["ar"], TEXTS["mixed"])))
    check("rebuild reports nothing omitted when everything is carried", cv.last_omitted == [] and cv.last_included == 4, (cv.last_omitted, cv.last_included))

    # a retry before the rotation: the rejected reply is never an accepted answer
    cv2 = conv_with(steps)
    working = [{"role": "user", "content": "TASK - STORY EVENT 4", "step": "STORY EVENT 4"}, {"role": "assistant", "content": "REJECTED-REPLY", "step": "STORY EVENT 4"}]
    t2 = cv2._transcript("STORY EVENT 5", [], "next", all_kinds=True)
    check("a rejected answer is not in the rebuilt history", "REJECTED-REPLY" not in t2)
    t2w = cv2._transcript("STORY EVENT 4", working, "next")
    check("a rejected attempt of the CURRENT step is still carried in full", "REJECTED-REPLY" in t2w)

    # a revised block of events replaces the per-step paragraphs
    cv3 = conv_with(steps + [("REVISED STORY EVENTS", "<story_events>REVISED " + TEXTS["zh"] + "</story_events>")])
    t3 = cv3._transcript("SOURCE ANCHORS", [], "next", all_kinds=True)
    check("revised events replace the older paragraphs", "REVISED" in t3 and TEXTS["ja"] not in t3)
    check("a replaced answer is not reported as omitted", cv3.last_omitted == [], cv3.last_omitted)

    # the 'lighter' transcript (the old needs table) reports what it leaves out
    cv4 = conv_with(steps + [("WORLD SHEET", "<world_sheet>w</world_sheet>")])
    t4 = cv4._transcript("STORY EVENT 2", [], "next")
    check("a lighter transcript names the kinds it left out", "WORLD" in cv4.last_omitted, cv4.last_omitted)

    # ---- P1: the source-anchor conflict is a protocol token --------------------------------------------------------------------------
    base = ("initial_pose: p\ninitial_clothing: c\nheld_objects: h\nappearance_locks: a\nenvironment_anchors: e\nsource_facts: f\n")

    def anchors(status, details):
        return "<source_anchors>\n" + base + (f"conflict_status: {status}\n" if status is not None else "") + f"plan_conflicts: {details}\n</source_anchors>"

    for lang, t in TEXTS.items():
        a, pr = C._parse_anchors(anchors("CLEAR", t))
        check(f"CLEAR is accepted whatever the details say ({lang})", a is not None and a["conflict_status"] == "CLEAR" and a["plan_conflicts"] == "", pr)
    for lang, t in TEXTS.items():
        a, pr = C._parse_anchors(anchors("CONFLICT", t))
        check(f"CONFLICT keeps the details exactly ({lang})", a is not None and a["conflict_status"] == "CONFLICT" and a["plan_conflicts"] == t, pr)
    a, pr = C._parse_anchors(anchors("conflict", "x"))
    check("the token is case-insensitive", a is not None and a["conflict_status"] == "CONFLICT", pr)
    for bad in ("none", "ไม่มี", "なし", "aucun", "no conflict", "CLEAR!", "OK", ""):
        a, pr = C._parse_anchors(anchors(bad, "x"))
        check(f"an invalid status is refused, not guessed ({bad!r})", a is None and any("conflict_status" in p for p in pr), pr)
    a, pr = C._parse_anchors(anchors(None, "none"))
    check("a missing status is refused", a is None and any("conflict_status" in p for p in pr), pr)
    a, pr = C._parse_anchors(anchors("CONFLICT", ""))
    check("CONFLICT without details is refused", a is None, pr)
    check("no word list decides the conflict any more", not hasattr(C, "_NONE_VALUES"))

    # ---- characters: a placeholder is recognised by structure, never deleted by a word list ------------------------------------------
    real = sheet("char1", {"name": "山田", "age": "२५", "gender": "ذكر", "init_cloth": "เสื้อเชิ้ตสีขาว"})
    s1, p1 = C._parse_characters(real)
    check("a real multilingual character is kept exactly", s1 is not None and "山田" in s1["sheet"] and "เสื้อเชิ้ตสีขาว" in s1["sheet"] and not p1, p1)
    for word in ("none", "なし", "ไม่มี", "aucun", "-", "لا شيء"):
        ph = sheet("char2", {f: word for f in FIELDS if f != "trigger"})
        sh, pr = C._parse_characters(real + "\n" + ph)
        check(f"a placeholder set is refused in any language ({word})", sh is None and any("placeholder" in p for p in pr), pr)
    sh, pr = C._parse_characters(real + "\n<char1.init_footwear>boots</char1.init_footwear>")
    check("an unknown character tag is refused, not silently dropped", sh is None and any("init_footwear" in p for p in pr), pr)
    check("the word-list deleter is gone from both conversations", "_drop_empty_characters(" not in open(C.__file__, encoding="utf-8").read().replace("def _drop_empty_characters", "")
          and "_drop_empty_characters(" not in open(N.__file__, encoding="utf-8").read())

    # ---- prose is never edited inside ---------------------------------------------------------------------------------------------------
    for lang, t in TEXTS.items():
        body = f"  {t}\n\n  second line   with   spaces\n"
        txt, pr = C._parse_event_step(f"<event_2>{body}</event_2>", 2)
        check(f"an event paragraph is kept as written ({lang})", txt == body.strip() and not pr, repr(txt))
    res = {"macro_events": [{"id": "E1", "content": "HAPPENS: " + TEXTS["th"] + " DETAILS: " + TEXTS["ja"]}, {"id": "E2", "content": "He wrote DETAILS: in the margin. " + TEXTS["ar"]},
                            {"id": "E3", "content": "HAPPENS: stays inside a sentence"}]}
    S._merge_event_parts(res)
    check("only a whole legacy HAPPENS/DETAILS text is merged", res["macro_events"][0]["content"] == TEXTS["th"] + " " + TEXTS["ja"], res["macro_events"][0]["content"])
    check("a label inside prose is not removed", res["macro_events"][1]["content"] == "He wrote DETAILS: in the margin. " + TEXTS["ar"], res["macro_events"][1]["content"])
    check("a lone label is not removed either", res["macro_events"][2]["content"] == "HAPPENS: stays inside a sentence", res["macro_events"][2]["content"])
    ev, pr = C._parse_events("".join(f"<E{i}>{t}</E{i}>" for i, t in enumerate(ALL[:5], 1)), 5)
    check("macro events pass through unchanged and need no word list", ev is not None and [e["content"] for e in ev] == ALL[:5], pr)
    echo, notes = C._strip_instruction_echo("a story line\nuser\nmore prose")
    check("a line that says 'user' inside the prose is not touched", echo == "a story line\nuser\nmore prose" and not notes, repr(echo))
    echo2, notes2 = C._strip_instruction_echo("user\nWrite all text inside the tags in Thai; tag names and keys stay English.\n<locations>x</locations>")
    check("the echoed instruction at the very start is removed and reported", echo2.strip() == "<locations>x</locations>" and notes2, (repr(echo2), notes2))

    # ---- markup that is tolerated is reported, markup that is missing is refused -----------------------------------------------------------
    C._NOTES.clear()
    pb: list = []
    out = C._single_blocks("<world_sheet>" + TEXTS["th"] + "</world_sheet>\n</start_turn>", ("world_sheet",), pb)
    check("a lone chat-template marker is ignored", out.get("world_sheet") == TEXTS["th"] and not pb, (out, pb))
    check("... and it is reported, not silent", any("start_turn" in n for n in C._NOTES), C._NOTES)
    pb2: list = []
    C._single_blocks("<world_sheet>" + TEXTS["ja"], ("world_sheet",), pb2)
    check("an unclosed tag is refused (a cut-off reply looks the same)", any("missing or not closed" in p for p in pb2), pb2)
    fixed, fnotes = C._close_unclosed_block("<important_objects>\nDoor: partially open, near Silas on the right side of the frame\n", 1536)
    check("a forgotten closer is added and reported when the reply is far below the output limit", fixed.rstrip().endswith("</important_objects>") and fnotes, (fixed, fnotes))
    pbx: list = []
    got = C._single_blocks(fixed, ("important_objects",), pbx)
    check("... and the repaired reply then passes the normal structure check", got.get("important_objects", "").startswith("Door:") and not pbx, (got, pbx))
    cut, cnotes = C._close_unclosed_block("<important_objects>\n" + ("ประตูเปิดอยู่ " * 400), 1536)
    check("a reply that may have been cut off by the output limit is NOT closed for the model", cut.endswith("ประตูเปิดอยู่ ") and not cnotes, cnotes)
    for fine in ("<important_objects>x</important_objects>", "<important_objects></important_objects>", "<world_sheet>a</world_sheet>\n<source_style>b</source_style>"):
        same, n2 = C._close_unclosed_block(fine, 1536)
        check(f"a correct reply is not touched ({fine[:30]!r})", same == fine and not n2, (same, n2))
    # a block ends where the NEXT protocol tag begins: nothing can have been cut off there
    two, n3 = C._close_unclosed_block("<world_sheet>" + TEXTS["th"] + "\n<source_style>" + TEXTS["ja"] + "</source_style>", 1536)
    check("an unclosed block followed by another tag is closed before that tag", two == "<world_sheet>" + TEXTS["th"] + "</world_sheet>\n<source_style>" + TEXTS["ja"] + "</source_style>" and len(n3) == 1, (two, n3))
    pbw: list = []
    gotw = C._single_blocks(two, ("world_sheet", "source_style"), pbw, optional=("source_style",))
    check("... and the result passes the normal structure check", gotw.get("world_sheet") == TEXTS["th"] and gotw.get("source_style") == TEXTS["ja"] and not pbw, (gotw, pbw))
    near, n4 = C._close_unclosed_block("<locations>" + TEXTS["ar"] + "\n<world_sheet>w</world_sheet>", 20)
    check("this holds even when the reply is large for its budget (the next tag bounds it)", near.startswith("<locations>" + TEXTS["ar"] + "</locations>") and len(n4) == 1, (near, n4))
    both, n5 = C._close_unclosed_block("<locations>a\n<important_objects>b", 1536)
    check("several unclosed blocks are all closed, each reported", both == "<locations>a</locations>\n<important_objects>b</important_objects>" and len(n5) == 2, (both, n5))
    lt, n6 = C._close_unclosed_block("<important_objects>1 < 2 and <b>bold</b>, a <3 sign", 1536)
    check("a '<' that is not one of our tags is plain text", lt == "<important_objects>1 < 2 and <b>bold</b>, a <3 sign</important_objects>" and len(n6) == 1, (lt, n6))
    ev, n7 = C._close_unclosed_block("<story_events>\nselected_arc: a\n<event_1>x\n<event_2>y</event_2>", 4096)
    check("a nested block is bounded by its own children: story_events is never closed this way", "</story_events>" not in ev and "</event_1>" in ev, (ev, n7))
    empty, n8 = C._close_unclosed_block("<important_objects>\n   \n", 1536)
    check("an empty unclosed block is left for the normal refusal", empty == "<important_objects>\n   \n" and not n8, (empty, n8))
    pb3: list = []
    C._single_blocks("note " + "<world_sheet>x</world_sheet>", ("world_sheet",), pb3)
    check("text outside the tag is refused", any("outside" in p for p in pb3), pb3)

    # ---- A2: the structure of the beat tags --------------------------------------------------------------------------------------------------------
    ok = "".join(f"<E1.{i}>\nHAPPENS: {t}\nDETAILS: {t}\n</E1.{i}>\n" for i, t in enumerate(ALL[:3], 1)) + "".join(f"<E2.{i}>{t}</E2.{i}>" for i, t in enumerate(ALL[3:], 1))
    beats, pr = A2._micro_beat_problems(ok, allowed_parents={"E1", "E2"}, parent_order=["E1", "E2"], require_parents={"E1", "E2"})
    check("a well-formed multilingual reply is accepted", not pr and [b["id"] for b in beats] == ["E1.1", "E1.2", "E1.3", "E2.1", "E2.2", "E2.3"], pr)
    check("beat content is kept exactly", beats[0]["content"] == f"HAPPENS: {ALL[0]}\nDETAILS: {ALL[0]}" and beats[4]["content"] == ALL[4], beats[0]["content"])
    cases = {
        "a beat number that does not start at 1": "<E1.8>x</E1.8>",
        "a gap in the numbering": "<E1.1>a</E1.1><E1.3>b</E1.3>",
        "a duplicate id": "<E1.1>a</E1.1><E1.1>b</E1.1>",
        "beats of a macro event that are not together": "<E1.1>a</E1.1><E2.1>b</E2.1><E1.2>c</E1.2>",
        "text outside the tags": "intro <E1.1>a</E1.1>",
        "an unclosed trailing tag": "<E1.1>a</E1.1><E1.2>b",
        "an empty beat": "<E1.1>   </E1.1>",
        "an unknown macro event": "<E9.1>a</E9.1>",
        "a tag that is not a beat id": "<E1>a</E1>",
    }
    for name, reply in cases.items():
        beats, pr = A2._micro_beat_problems(reply, allowed_parents={"E1", "E2"}, parent_order=["E1", "E2"])
        check(f"A2 refuses {name}", bool(pr), reply)
    beats, pr = A2._micro_beat_problems("<E2.1>a</E2.1><E1.1>b</E1.1>", allowed_parents={"E1", "E2"}, parent_order=["E1", "E2"])
    check("A2 refuses macro events out of their order", any("own order" in p for p in pr), pr)
    beats, pr = A2._micro_beat_problems("<E1.1>a</E1.1>", allowed_parents={"E1", "E2"}, parent_order=["E1", "E2"], require_parents={"E1", "E2"})
    check("A2 refuses a macro event without any beat", any("E2" in p for p in pr), pr)
    check("A2 parser returns None on any problem and the reasons", A2._parse_micro_tags("<E1.8>x</E1.8>", allowed_parents={"E1"}, problems=(why := [])) is None and why)

    # ---- A1 Novel: the arc is a story arc, never a Static arc --------------------------------------------------------------------------------
    for g in S._GENRE_ARCS:
        arc = N._select_novel_arc(g, "")
        check(f"novel arc for {g} is a story arc with five steps", arc["arc_source"] == "story" and len(arc["steps"]) == 5 and "reproduce the source" not in " ".join(arc["steps"]).lower())

    # ---- prompts: story progression, places for Visual Novel, one beat rule -----------------------------------------------------------------------
    arc = {"genre": "drama", "goal": "g", "id": "a", "name": "A", "rules": "r", "steps": ["ENTRY", "ACTION 1", "ACTION 2", "ACTION 3", "PAYOFF"]}
    later = C._req_event_step(arc, 2, ["Mina"], 1, desc_lang="en", vn=True)
    check("a later static event asks for action, response and consequence", "reaction" in later and "consequence" in later and "Exactly two sentences" not in later)
    check("the pose is a result, not a reason", "visible result" in later)
    check("the Visual Novel place rule is in the per-step request", "(place:" in later)
    check("a non-Visual-Novel request keeps the source scene", "same place" in C._req_event_step(arc, 2, ["Mina"], 1, desc_lang="en", vn=False))
    check("event 1 stays on the source", "does not begin" in C._req_event_step(arc, 1, ["Mina"], 1, desc_lang="en").lower() or "Do not begin the next event" in C._req_event_step(arc, 1, ["Mina"], 1, desc_lang="en"))
    check("the static system prompt is not a pose recipe", "never the reason" in C._SYSTEM_PROMPT)
    guard = A2._beat_guard()
    check("A2 has one beat rule: a drawable state or action phase", "drawable state or action phase" in guard and "one second" not in guard and "then, after, or next" not in guard)
    check("A2 mode texts differ and neither counts seconds", A2._mode_instruction("sequential") != A2._mode_instruction("cinematic") and "second" not in A2._mode_instruction("sequential"))
    check("A2 tag example does not count seconds", "one second" not in A2._build_one_macro_user_prompt({"character_sheet": "", "world_sheet": "", "locations": "", "important_objects": "", "continuity_notes": ""},
                                                                                                    {"id": "E1", "content": "x"}, mode="sequential", desc_lang="en"))

    print("ALL PASS" if not _fails else f"{len(_fails)} FAILED: {_fails}")
    return 0 if not _fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
