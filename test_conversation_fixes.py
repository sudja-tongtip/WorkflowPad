"""The six conversation bugs from the 16-turn Gemma/Grok run. No LLM, no song files.

Run: python test_conversation_fixes.py
"""
from music_studio import lyric_actions as AC
from music_studio import lyric_analysis as LA
from music_studio import llm_chat as LC
from music_studio import specialist_engine as SE

SONG = (
    "[Verse 1]\n"
    "ฉันยังรอเธอตรงที่เดิม\n"
    "ริมหน้าต่างที่ฝนเพิ่งหยุด\n"
    "\n"
    "[Chorus]\n"
    "กลับมาได้ไหม\n"
    "ฉันยังอยู่ตรงนี้\n"
    "\n"
    "[Bridge]\n"
    "คืนนี้เงียบไป\n"
)
CHORUS = "กลับมาได้ไหม\nฉันยังอยู่ตรงนี้"


def _sw(reply: str, proposal: str = "", edits: str = "") -> str:
    block = ("=========== EDITS ===========\n" + edits + "\n") if edits else ""
    return LC.SEP["REPLY"] + "\n" + reply + "\n\n" + block + LC.SEP["PROPOSAL"] + "\n" + proposal + "\n\n" + LC.SEP["END"] + "\n"


def _op(at: str, name: str, cues: str = "") -> str:
    return (
        LC.SEP["REPLY"] + "\nเพิ่มท่อน\n\n=========== OPERATIONS ===========\n"
        f"OP\nop: add_section\nat: {at}\nname: {name}\ncues: {cues}\nreason: test\n"
        "=========== PROPOSAL ===========\n\n=========== END ===========\n"
    )


def test_pending_hook_is_not_a_question_and_a_real_question_still_is():
    assert SE.chat_mode("กลับมาได้ไหม") == "talk"
    assert SE.chat_mode("กลับมาได้ไหม", pending_lyrics=True) == "edit"
    assert SE.chat_mode("BPM เท่าไหร่ดี") == "talk"
    assert SE.chat_mode("BPM เท่าไหร่ดี", pending_lyrics=True) == "talk"
    assert SE.chat_mode("ความยาวเพลงควรเป็นเท่าไร") == "talk"
    assert SE.chat_mode("เพลงนี้ดีไหม", pending_lyrics=True) == "talk"
    assert SE.chat_mode("นับพยางค์อย่างเดียว ห้ามแก้", pending_lyrics=True) == "talk"
    assert SE.chat_mode("ไม่ต้องแก้แล้ว มาคุยเรื่อง Arrangement", pending_lyrics=True) == "talk"
    assert SE.chat_mode("ช่วยปรับท่อน Chorus ให้เศร้ากว่านี้", pending_lyrics=True) == "edit"
    assert SE.chat_mode("ปรับ BPM ให้เท่าไหร่") == "edit"
    kept = SE.handle_chat_answer("songwriter", _sw("รับฮุกแล้ว", "[Verse 2]\nเช้าวันใหม่ยังเหมือนเดิม\n"), SONG, "", False, "กลับมาได้ไหม", pending_lyrics=True)
    assert kept.offered and "[Verse 2]" in kept.proposal and "ฉันยังรอเธอตรงที่เดิม" in kept.proposal
    blocked = SE.handle_chat_answer("songwriter", _sw("รับฮุกแล้ว", "[Verse 2]\nเช้าวันใหม่ยังเหมือนเดิม\n"), SONG, "", False, "กลับมาได้ไหม")
    assert not blocked.offered and blocked.proposal == ""


def test_bridge_is_not_stacked_unless_another_was_asked():
    stacked = SE.handle_chat_answer("music_producer", _op("9", "Bridge"), SONG, "", False, "เพิ่ม Bridge หลัง chorus")
    assert stacked.edits == [] and any("already here" in n for n in stacked.notes)
    assert SONG == SONG
    forced = {"id": "e1", "first": 9, "last": 9, "original": "[Bridge]", "replacement": "[Bridge]\n[Bridge]", "reason": ""}
    refused = SE.apply_edit(forced, SONG, "music_producer")
    assert isinstance(refused, str) and "already here" in refused and SONG.count("[Bridge]") == 1
    forced["allow_repeat"] = True
    allowed = SE.apply_edit(forced, SONG, "music_producer")
    assert isinstance(allowed, tuple) and allowed[0].count("[Bridge]") == 2 and "คืนนี้เงียบไป" in allowed[0]
    asked = SE.handle_chat_answer("music_producer", _op("9", "Bridge"), SONG, "", False, "เพิ่ม Bridge อีกท่อนหลัง chorus")
    assert asked.edits and asked.edits[0].get("allow_repeat") is True
    stolen = SE.handle_chat_answer("music_producer", _op("6", "Bridge"), SONG, "", False, "เพิ่ม Bridge หลัง chorus")
    assert stolen.edits == [] and any("lyric line" in n for n in stolen.notes)
    intro = SE.handle_chat_answer("music_producer", _op("1", "Intro"), "[Verse 1]\nhello\n", "", False, "เพิ่ม Intro ให้หน่อย")
    assert intro.edits and "[Intro]" in intro.edits[0]["replacement"] and "hello" in SE.apply_edit(intro.edits[0], "[Verse 1]\nhello\n", "music_producer")[0]


def test_caption_survives_an_empty_reply():
    cap = (
        "A melancholic Thai pop ballad at a slow tempo, featuring a soft intimate female vocal. "
        "Sparse warm piano and subtle acoustic guitar carry the verse, with gentle drums and bass underneath. "
        "The chorus lifts with a whispered ethereal quality and light strings, and the bridge opens into a dreamy spacious texture."
    )
    raw = (
        "FIELD: mine\nKIND: caption\n\n=========== CAPTION ===========\n" + cap + "\n\n"
        "=========== PROPOSAL ===========\nผมได้เขียน Music Caption ให้แล้วครับ\n=========== END ===========\n"
    )
    out = SE.handle_chat_answer("music_producer", raw, SONG, "", False, "เขียน music caption ให้หน่อย")
    assert out.caption and "melancholic" in out.caption
    assert "Music Caption" in out.reply
    assert any("caption was kept" in s for s in out.steps)


def test_mixed_proposal_keeps_the_edit_and_the_new_section():
    proposal = (
        "[Verse 1]\nฉันยังรอเธอตรงที่เดิม\nริมหน้าต่างที่ฝนเพิ่งหยุด\n\n"
        "[Chorus]\nกลับมาได้ไหม\nฉันยังรอเธอจนใจมันอ่อนแอ\n\n"
        "[Verse 2]\nภาพเธอยังชัด ไม่เคยจางไป\nแม้ว่าวันเวลาจะหมุนผ่านไป\n"
    )
    out = SE.handle_chat_answer("songwriter", _sw("แก้ Chorus และเพิ่ม Verse 2", proposal), SONG, "", False, "ช่วยแต่ง Verse 2 และปรับ Chorus")
    assert out.offered
    assert "ฉันยังรอเธอจนใจมันอ่อนแอ" in out.proposal
    assert "[Verse 2]" in out.proposal and "ภาพเธอยังชัด ไม่เคยจางไป" in out.proposal
    assert "[Bridge]" in out.proposal and "คืนนี้เงียบไป" in out.proposal
    assert "ฉันยังรอเธอตรงที่เดิม" in out.proposal
    assert not any("not added" in n.lower() for n in out.notes)
    plan = AC.plan_apply("all", SONG, out.proposal, SONG, 0, len(SONG), "", 0)
    new = plan["direct"][0]
    assert "ฉันยังรอเธอจนใจมันอ่อนแอ" in new and "[Verse 2]" in new and "คืนนี้เงียบไป" in new
    assert SONG != new and "ฉันยังอยู่ตรงนี้" in SONG
    a = SONG.index(CHORUS)
    sel = SE.handle_chat_answer(
        "songwriter", _sw("สั้นลง", "กลับมา\nฉันยังอยู่"), SONG, CHORUS, True, "ปรับเฉพาะท่อนที่เลือก ให้สั้นลง",
        sel_bounds=(a, a + len(CHORUS)),
    )
    assert sel.offered and "กลับมา" in sel.proposal and "[Verse 1]" not in sel.proposal
    applied = SONG[:a] + sel.proposal + SONG[a + len(CHORUS):]
    assert "ฉันยังรอเธอตรงที่เดิม" in applied and "คืนนี้เงียบไป" in applied and "ฉันยังอยู่ตรงนี้" not in applied


def test_natural_syllable_claims_use_the_program_count():
    reply = (
        '*   "ฉันยังรอเธอตรงที่เดิม": **7** พยางค์\n'
        '*   "ริมหน้าต่างที่ฝนเพิ่งหยุด": **9** พยางค์\n'
        '*   "กลับมาได้ไหม": **5** พยางค์\n'
    )
    notes = SE.syllable_check(reply, SONG)
    assert any("ริมหน้าต่างที่ฝนเพิ่งหยุด" in n and "not 9" in n for n in notes)
    assert any("กลับมาได้ไหม" in n and "not 5" in n for n in notes)
    assert not any("ฉันยังรอเธอตรงที่เดิม" in n and "not 7" in n for n in notes)
    sung = LA.sung_lines(SONG)
    ids = LA.line_ids(sung)
    manual = {ids[0]: LA.Correction(count=4, text=sung[0].text)}
    forced = SE.syllable_check('"ฉันยังรอเธอตรงที่เดิม": 7 พยางค์', SONG, manual)
    assert any("your correction" in n and "not 7" in n for n in forced)
    counted = SE.handle_chat_answer("songwriter", _sw(reply, "[Chorus]\nเปลี่ยนทั้งเพลง\n"), SONG, "", False, "นับพยางค์อย่างเดียว ห้ามแก้")
    assert not counted.offered and counted.proposal == "" and SONG.count("[Chorus]") == 1


def test_an_edit_without_a_proposal_marker_is_kept():
    raw = (
        "=========== REPLY ===========\nFIELD: mine\nKIND: answer\n\n"
        "=========== EDITS ===========\nEDIT\ntarget: Chorus\noriginal: <<<\n"
        + CHORUS + "\n>>>\nreplacement: <<<\nฉันบอกทุกคนว่าลืมปิดไฟ\nไม่ใช่เพราะรอเธอ\n>>>\nreason: เจ็บกว่า\n"
        "=========== END ===========\n"
    )
    out = SE.handle_chat_answer("songwriter", raw, SONG, "", False, "เอาแบบที่สอง")
    assert out.edits and "ลืมปิดไฟ" in out.edits[0]["replacement"]
    assert out.proposal == ""
    assert "ลืมปิดไฟ" in SE.apply_edit(out.edits[0], SONG, "songwriter")[0]
    assert "คืนนี้เงียบไป" in SE.apply_edit(out.edits[0], SONG, "songwriter")[0]


def test_a_selection_edit_is_not_replaced_by_another_section():
    raw = _sw(
        "สั้นลง",
        "[Post-Chorus]\n(โอ้... กลับมา)\nตรงนี้...",
        "EDIT\ntarget: lines 6-7\noriginal: <<<\n" + CHORUS + "\n>>>\nreplacement: <<<\nกลับมาได้ไหม\nยังอยู่ตรงนี้\n>>>\nreason: สั้นลง\n",
    )
    a = SONG.index(CHORUS)
    out = SE.handle_chat_answer("songwriter", raw, SONG, CHORUS, True, "ปรับเฉพาะท่อนที่เลือก ให้สั้นลง", sel_bounds=(a, a + len(CHORUS)))
    assert out.edits and out.proposal == "" and "Post-Chorus" in out.rejected
    applied = SE.apply_edit(out.edits[0], SONG, "songwriter")[0]
    assert "ยังอยู่ตรงนี้" in applied and "ฉันยังรอเธอตรงที่เดิม" in applied and "คืนนี้เงียบไป" in applied
    assert "[Post-Chorus]" not in applied


if __name__ == "__main__":
    test_pending_hook_is_not_a_question_and_a_real_question_still_is()
    test_bridge_is_not_stacked_unless_another_was_asked()
    test_caption_survives_an_empty_reply()
    test_mixed_proposal_keeps_the_edit_and_the_new_section()
    test_natural_syllable_claims_use_the_program_count()
    test_an_edit_without_a_proposal_marker_is_kept()
    test_a_selection_edit_is_not_replaced_by_another_section()
    print("ALL PASS")
