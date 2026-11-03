"""Analysis-only chat stays words. A suggestion can use Analyze Song findings and still comes back as a preview.

No LLM call, no song library, no editor. Run: python -m pytest test_analysis_suggestions.py -q
"""
from music_studio import llm_chat as LC
from music_studio import specialist_engine as SE

LYRICS = "[Verse 1]\nhello\n"
EDIT = "EDIT\ntarget: line 2\noriginal: <<<\nhello\n>>>\nreplacement: <<<\nhi there\n>>>\nreason: clearer\n"
OP = "OP\nop: add_section\nat: 1\nname: Intro\ncues:\nreason: a short opening\n"


def _sw(edits: str = "", proposal: str = "") -> str:
    block = ("=========== EDITS ===========\n" + edits + "\n") if edits else ""
    return LC.SEP["REPLY"] + "\nดูบรรทัดนี้\n\n" + block + LC.SEP["PROPOSAL"] + "\n" + proposal + "\n\n" + LC.SEP["END"] + "\n"


def _producer(ops: str) -> str:
    return (LC.SEP["REPLY"] + "\nดูโครงสร้าง\n\n=========== OPERATIONS ===========\n" + ops
            + "\n=========== PROPOSAL ===========\n\n=========== END ===========\n")


def test_modes():
    assert SE.chat_mode("วิเคราะห์เพลงนี้") == "talk"
    assert SE.chat_mode("วิเคราะห์โครงสร้างเพลงนี้") == "talk"
    assert SE.chat_mode("แนะนำวิธีปรับท่อนฮุก") == "edit"
    assert SE.chat_mode("เพิ่ม Intro ให้หน่อย") == "edit"
    assert SE.chat_mode("เอาแบบนั้น", True) == "edit"
    assert SE.chat_mode("อย่าเพิ่งแก้ แค่วิเคราะห์") == "talk"
    assert "=========== EDITS ===========" not in SE.chat_contract("songwriter", "talk")
    assert "=========== EDITS ===========" in SE.chat_contract("songwriter", "edit")
    assert "=========== OPERATIONS ===========" not in SE.chat_contract("music_producer", "talk")
    assert "=========== OPERATIONS ===========" in SE.chat_contract("music_producer", "edit")
    assert "Leave PROPOSAL, EDITS and OPERATIONS empty" in SE.chat_field_rule("songwriter", "talk")
    assert "write the OPERATIONS" not in SE.chat_field_rule("music_producer", "talk")


def test_analysis_only_makes_no_cards():
    song = LYRICS
    analyzed = SE.handle_chat_answer("songwriter", _sw(EDIT, "hi there"), song, "", False, "วิเคราะห์เพลงนี้")
    assert analyzed.edits == [] and analyzed.proposal == "" and not analyzed.offered
    assert song == LYRICS
    assert any("not turned into cards" in s for s in analyzed.steps)
    suggested = SE.handle_chat_answer("songwriter", _sw(EDIT), song, "", False, "แนะนำวิธีปรับบรรทัดนี้")
    assert suggested.offered and suggested.edits and suggested.edits[0]["replacement"] == "hi there"
    assert song == LYRICS
    go = SE.handle_chat_answer("songwriter", _sw(EDIT), song, "", False, "เอาแบบนั้น", True)
    assert go.offered and go.edits


def test_producer_analysis_makes_no_cards_and_a_change_does():
    song = LYRICS
    analyzed = SE.handle_chat_answer("music_producer", _producer(OP), song, "", False, "วิเคราะห์โครงสร้างเพลงนี้")
    assert analyzed.edits == [] and not analyzed.offered and song == LYRICS
    changed = SE.handle_chat_answer("music_producer", _producer(OP), song, "", False, "เพิ่ม Intro ให้หน่อย")
    assert changed.offered and changed.edits
    assert changed.edits[0]["original"] == "[Verse 1]" and "[Intro]" in changed.edits[0]["replacement"]
    applied = SE.apply_edit(changed.edits[0], song, "music_producer")
    assert isinstance(applied, tuple) and applied[0].count("[Intro]") == 1 and "hello" in applied[0]
    assert song == LYRICS


def test_findings_stay_with_the_right_specialist():
    writer = SE.SpecReport("songwriter", "ok", summary="ฮุคแน่นไป", lyrics_hash="abc")
    writer.findings = [
        SE.Finding("f1", "songwriter", "wording", "high", 2, 2, "hello", "บรรทัดแน่น", "ร้องยาก", "hi there", ""),
        SE.Finding("f2", "songwriter", "arrangement", "low", 1, 1, "", "should not pass", "not this role", "", ""),
    ]
    producer = SE.SpecReport("music_producer", "ok", summary="โครงยังโล่ง", lyrics_hash="abc")
    producer.findings = [
        SE.Finding("p1", "music_producer", "arrangement", "medium", 1, 1, "", "chorus needs a lift", "energy drops", "", ""),
        SE.Finding("p2", "music_producer", "structure", "high", 1, 1, "[Verse 1]", "an intro would help", "the song starts on the verse", "[Intro]\n[Verse 1]", ""),
    ]
    words = SE.findings_for_suggestion("songwriter", writer, "abc")
    shape = SE.findings_for_suggestion("music_producer", producer, "abc")
    assert "บรรทัดแน่น" in words and "hi there" in words and "EDIT" in words and "do not apply" in words
    assert "should not pass" not in words and "chorus needs a lift" not in words
    assert "chorus needs a lift" in shape and "arrangement advice" in shape and "an intro would help" in shape and "OPERATION" in shape
    assert "hi there" not in shape and "do not rewrite lyrics" in shape
    assert "OLDER" not in words and "OLDER" in SE.findings_for_suggestion("songwriter", writer, "changed")
    assert SE.findings_for_suggestion("music_producer", writer, "abc") == ""
    assert SE.findings_for_suggestion("songwriter", producer, "abc") == ""
    assert SE.findings_for_suggestion("songwriter", None, "abc") == ""
    assert SE.suggestion_attachment("songwriter", writer, "abc", False, "talk") == ""
    assert SE.suggestion_attachment("songwriter", writer, "abc", True, "edit") == ""
    assert "บรรทัดแน่น" in SE.suggestion_attachment("songwriter", writer, "abc", False, "edit")
    assert "chorus needs a lift" in SE.suggestion_attachment("music_producer", producer, "abc", True, "edit")


def test_both_backends_keep_the_same_selection_message():
    user = ("CURRENT LYRICS:\nhello\n\nSELECTED TEXT (lines 2-2 — FOCUS ON THIS SELECTION: read the rest only as context):\nhello\n\n"
            "USER REQUEST:\nปรับเฉพาะท่อนที่เลือก")
    grok = SE.SpecSession("songwriter", "sel-grok", "sheet")
    local = SE.SpecSession("songwriter", "sel-local", "sheet")
    try:
        pg = SE.prepare_live(grok, "grok", "grok-test", "", user, 32768)
        pl = SE.prepare_live(local, "local:dummy", "local-test", "", user, 8192)
    finally:
        SE.drop_live("sel-grok", "sheet", "songwriter")
        SE.drop_live("sel-local", "sheet", "songwriter")
    assert pg.user.count("SELECTED TEXT (") == 1 and pl.user.count("SELECTED TEXT (") == 1
    assert pg.user.split("USER REQUEST:\n", 1)[1] == pl.user.split("USER REQUEST:\n", 1)[1] == "ปรับเฉพาะท่อนที่เลือก"
    assert "hello" in pg.user and pg.history == [] and pl.history == []
