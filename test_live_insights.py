"""Tests of music_studio.live_insights (counting, rhymes, graphs, text for the LLM). Run: python -m pytest test_live_insights.py -q"""
import time

from music_studio import lyric_analysis as LA
from music_studio import live_insights as L

T1 = "[Verse 1]\nฉันยังรอเธอตรงที่เดิม\nริมหน้าต่างที่ฝนเพิ่งหยุด\n\n[Chorus]\nยังมีกุญแจวางอยู่บนโต๊ะ\n"


def one(text):
    return L.analyze(text).lines[0]


# ── A. counting ───────────────────────────────────────────────────────────────────────────────────────
def test_thai_key_word_is_two_syllables():
    li = one("กุญแจ")
    assert li.syllable_count == 2 and li.word_count == 1 and li.syllable_segments == ["กุญ", "แจ"]


def test_thai_words_not_counted_by_spaces():
    li = one("ฉันยังรอเธอตรงที่เดิม")                       # no spaces at all
    assert li.word_count == 7 and li.syllable_count == 7
    assert one("ฉัน ยัง รอ").word_count == 3


def test_english_and_mixed():
    en = one("I love you baby")
    assert en.word_count == 4 and en.syllable_count == 5 and en.status == "estimated"      # ba-by
    mix = one("ฉันรัก baby มาก")
    assert mix.word_count == 4 and mix.status == "estimated" and mix.warnings


def test_tags_blank_and_hold_are_not_sung_lines():
    ins = L.analyze("[Verse 1]\n\n[Hold]\nแม่ชอบรอฝน\n[Chorus - powerful belting]\n")
    assert [x.line_number for x in ins.lines] == [4]
    assert [s.name for s in ins.sections] == ["Verse 1", "Chorus"]


def test_repeated_words_counted_every_time():
    assert one("ฝนตกฝนตกฝนตก").word_count == 3 and one("ฝนตกฝนตกฝนตก").syllable_count == 6


def test_status_levels():
    assert one("ฝนตกหนัก").status == "verified"
    assert one("hello world").status == "estimated"
    assert L.analyze("[Verse]\n").lines == []


def test_manual_correction_applies_only_to_its_line():
    text = "[Verse]\nกุญแจ\nฝนตกหนัก\n"
    sung = LA.sung_lines(text)
    ids = LA.line_ids(sung)
    ins = L.analyze(text, {ids[0]: LA.Correction(count=3, text="กุญแจ")})
    assert ins.lines[0].syllable_count == 3 and ins.lines[0].status == "manual_corrected"
    assert ins.lines[1].status == "verified"
    changed = L.analyze("[Verse]\nกุญแจจ\nฝนตกหนัก\n", {ids[0]: LA.Correction(count=3, text="กุญแจ")})       # the line was edited: the correction is not applied to it
    assert changed.lines[0].status != "manual_corrected"


# ── B. rhymes ─────────────────────────────────────────────────────────────────────────────────────────
def _end_spans(text):
    ins = L.analyze(text)
    return ins, [sp for sp in ins.spans if sp.role == "end"]


def test_fon_khon_rhyme_with_one_colour_and_exact_offsets():
    text = "ในคืนที่มีฝน\nฉันยังเป็นคน\n"
    ins, sp = _end_spans(text)
    assert [s.text for s in sp] == ["ฝน", "คน"] and sp[0].color == sp[1].color and sp[0].kind == "exact"
    assert all(text[s.start:s.end] == s.text for s in ins.spans)                 # the colour lands on the right characters


def test_klai_jai_rhyme():
    ins, sp = _end_spans("ฟ้านั้นไกล\nแต่อยู่ในใจ\n")
    assert [s.text for s in sp] == ["ไกล", "ใจ"] and sp[0].color == sp[1].color


def test_derm_yud_is_not_a_rhyme_without_evidence():
    ins, sp = _end_spans("ฉันยังรออยู่ที่เดิม\nริมหน้าต่างที่ฝนเพิ่งหยุด\n")
    assert sp == [] and [x.rhyme_status for x in ins.lines] == ["none", "none"]


def test_different_sounds_get_different_colours_and_colour_is_stable():
    t = "ในคืนที่มีฝน\nฉันยังเป็นคน\nฟ้านั้นไกล\nแต่อยู่ในใจ\n"
    a = L.analyze(t)
    cols = {s.group: s.color for s in a.spans}
    assert len(cols) == 2
    b = L.analyze(t + "ทุกวัน\n")                                              # typing one more line does not change the existing colours
    assert {s.group: s.color for s in b.spans if s.group in cols} == cols


def test_cross_section_rhyme_and_pattern():
    ins = L.analyze("[Verse]\nคืนนี้ฝน\nฉันเดียวดาย\n[Chorus]\nแต่ยังเป็นคน\nและเธอก็ไกล\n")
    assert {s.text for s in ins.spans} == {"ฝน", "คน"}
    assert ins.sections[0].rhyme_pattern == "A-" and ins.sections[1].rhyme_pattern == "A-"


def test_internal_rhyme_of_one_syllable_words():
    ins = L.analyze("ฟ้าไกลแค่ไหนก็ใจ\n")
    assert {s.text for s in ins.spans} == {"ไกล", "ไหน", "ใจ"} and all(s.role == "internal" for s in ins.spans)


def test_repeated_line_is_repetition_not_rhyme():
    ins = L.analyze("ฉันยังรอเธอตรงที่เดิม\nฉันยังรอเธอตรงที่เดิม\nฝนตกหนัก\nฝนตกหนัก\n")
    assert ins.spans == []


def test_english_end_word_is_unverified_not_a_rhyme():
    ins = L.analyze("I love you baby\nแต่ฉันยังรัก\n")
    assert ins.lines[0].rhyme_status == "unverified" and ins.lines[0].rhyme_spans == []


def test_no_per_line_warning_for_missing_rhyme():
    ins = L.analyze(T1)
    assert all(not li.warnings for li in ins.lines)
    assert ins.sections[0].warnings == []                                       # two lines only: nothing is said


# ── D. vocal density ──────────────────────────────────────────────────────────────────────────────────
LINE = "ฉันยังรอเธอตรงที่เดิมนะ"            # 8 syllables
SHORT = "ฝนตกหนัก"                         # 3 syllables


def song(n_verse=4, n_chorus=3, line=LINE, intro=True, outro=True):
    parts = (["[Intro]"] if intro else []) + ["[Verse 1]"] + [line] * n_verse + ["[Chorus]"] + [line] * n_chorus + (["[Outro]"] if outro else [])
    return L.analyze("\n".join(parts) + "\n")


def dens(ins, bpm=100, dur=120, speed="normal", ts="4"):
    return L.density(ins, L.DensitySettings(bpm=bpm, timesig=ts, duration=dur, speed=speed))


def test_seconds_per_bar_by_time_signature():
    assert L.seconds_per_bar(120, "4") == 2.0 and L.seconds_per_bar(60, "3") == 3.0
    assert L.seconds_per_bar(120, "6") == 1.0 and L.seconds_per_bar(120, "6") != L.seconds_per_bar(120, "4") * 6 / 4        # 6/8 is NOT 6 quarter-note beats
    assert "dotted" in L.pulses_per_bar("6")[1]


def test_short_song_with_few_words_gives_no_warning():
    d = dens(song(2, 2, SHORT), dur=120)
    assert d.known and d.warnings == [] and d.song_level == "comfortable"


def test_dense_lyrics_in_120_seconds_warn():
    d = dens(song(14, 10, "ฉันยังรอเธอตรงที่เดิมนะริมหน้าต่างที่ฝนเพิ่งหยุด"), dur=120)
    assert d.known and d.song_level in ("dense", "very_dense") and d.warnings
    assert all(w.level in ("dense", "very_dense") for w in d.warnings)


def test_more_words_in_the_chorus_raise_the_risk_and_fewer_lower_it():
    base = song(4, 3)
    more = L.analyze("[Verse 1]\n" + (LINE + "\n") * 4 + "[Chorus]\n" + ("ฉันยังรอเธอตรงที่เดิมนะริมหน้าต่างที่ฝนเพิ่งหยุดนะ\n") * 3)
    fewer = L.analyze("[Verse 1]\n" + (LINE + "\n") * 4 + "[Chorus]\n" + (SHORT + "\n") * 3)
    r = lambda ins: [s for s in dens(ins, dur=60).sections if s.name == "Chorus"][0].rate
    assert r(more) > r(fewer)
    assert r(more) > r(base) or r(more) > r(fewer)
    assert [s.level for s in dens(fewer, dur=60).sections if s.name == "Chorus"][0] in ("comfortable", "moderate")


def test_low_and_high_bpm_only_change_the_reserved_instrumental_time():
    ins = song(8, 6)
    lo, hi = dens(ins, bpm=60, dur=120), dens(ins, bpm=180, dur=120)
    assert lo.reserve_seconds > hi.reserve_seconds                      # slow bars last longer: more of the 120 s is kept for Intro/Outro
    assert lo.song_rate > hi.song_rate                                  # so a HIGH BPM does not make the lyrics denser: it leaves more room for singing
    assert dens(song(8, 6, intro=False, outro=False), bpm=60).reserve_seconds == 0 == dens(song(8, 6, intro=False, outro=False), bpm=180).reserve_seconds


def test_vocal_speed_changes_the_level_with_a_reason():
    ins = song(10, 8)
    lv = {sp: dens(ins, dur=90, speed=sp).song_level for sp in ("slow", "normal", "fast")}
    order = list(L.LEVELS)
    assert order.index(lv["slow"]) >= order.index(lv["normal"]) >= order.index(lv["fast"]) and lv["slow"] != lv["fast"]
    assert "'fast'" in dens(ins, dur=90, speed="fast").assumptions


def test_intro_instrumental_and_repeated_chorus_counted():
    ins = L.analyze("[Intro]\n[Verse]\n" + LINE + "\n" + LINE + "\n[Chorus]\n" + LINE + "\n" + LINE + "\n[Instrumental]\n[Chorus]\n" + LINE + "\n" + LINE + "\n[Outro]\n")
    d = dens(ins, bpm=120, dur=120)
    spb = L.seconds_per_bar(120, "4")
    assert d.reserve_seconds == (4 + 8 + 4) * spb                         # Intro 4 + Instrumental 8 + Outro 4 bars
    assert d.total_lines == 6 and d.total_syllables == 6 * 8
    assert [(s.name, s.index, s.sung_lines, s.instrumental) for s in d.sections] == [("Intro", 0, 0, True), ("Verse", 0, 2, False), ("Chorus", 0, 2, False), ("Instrumental", 0, 0, True),
                                                                                    ("Chorus", 1, 2, False), ("Outro", 0, 0, True)]


def test_not_enough_data_is_unknown_not_a_guess():
    assert not dens(L.analyze("[Verse]\nฝนตก\n")).known
    assert not dens(song(), dur=0).known and dens(song(), dur=0).song_level == "unknown" and dens(song(), dur=0).warnings == []
    assert not dens(song(3, 3), dur=10, bpm=30).known                       # the instrumental reserve leaves no time for singing
    assert "Unknown" == L.LEVEL_NAME["unknown"]


def test_sheets_do_not_mix():
    a, b = song(2, 2, SHORT), song(14, 10, "ฉันยังรอเธอตรงที่เดิมนะริมหน้าต่างที่ฝนเพิ่งหยุด")
    assert dens(a).warnings == [] and dens(b, dur=60).warnings and dens(a).warnings == []


def test_outlier_line_is_named():
    ins = L.analyze("[Verse]\n" + (SHORT + "\n") * 4 + "ฉันยังรอเธอตรงที่เดิมนะริมหน้าต่างที่ฝนเพิ่งหยุดนะครับ\n")
    d = dens(ins, dur=15)
    assert [x.outlier for x in d.lines] == [False, False, False, False, True]


# ── E. text for the LLM ───────────────────────────────────────────────────────────────────────────────
def test_blocks_carry_facts_warnings_and_assumptions():
    ins = L.analyze("[Verse 1]\nในคืนที่มีฝน\nฉันยังเป็นคน\n[Chorus]\nI love you baby\n" + ("ฉันยังรอเธอตรงที่เดิมนะริมหน้าต่างที่ฝนเพิ่งหยุดนะ\n") * 6)
    d = dens(ins, bpm=90, dur=60)
    sw = L.songwriter_block(ins, d)
    assert "2| " in sw and "ฝน=" in sw and "est" in sw and "not checked" in sw and "do not recount" in sw
    assert "ACE-STEP LYRICS HEALTH" in sw and "Assumptions:" in sw and "melody is not known" in sw
    pr = L.producer_block(ins, d)
    assert "Verse 1" in pr and "Chorus" in pr and "90 BPM" in pr and "NOT known" in pr and "ACE-STEP LYRICS HEALTH" in pr
    unk = L.songwriter_block(L.analyze("[V]\nฝน\n"), dens(L.analyze("[V]\nฝน\n")))
    assert "unknown" in unk


# ── F. speed ──────────────────────────────────────────────────────────────────────────────────────────
def test_speed_of_long_song_and_cache():
    text = "\n".join(f"[Verse {i}]\n" + "\n".join(["ฉันยังรอเธอตรงที่เดิม", "ริมหน้าต่างที่ฝนเพิ่งหยุด", "ยังมีกุญแจวางอยู่บนโต๊ะ", "แต่ไม่มีเธอกลับมา"] * 3) for i in range(1, 8))
    L.analyze(text)
    t = time.perf_counter()
    for _ in range(5):
        dens(L.analyze(text))
    per = (time.perf_counter() - t) / 5
    assert per < 0.25, per


# ── G. ACE-Step lyrics health ─────────────────────────────────────────────────────────────────────────
NORMAL = ["ฉันยังรอเธอตรงที่เดิม", "ริมหน้าต่างที่ฝนเพิ่งหยุด", "ยังมีกุญแจวางอยู่บนโต๊ะ", "แต่ไม่มีเธอกลับมา"]
LONG16 = "ฉันยังรอเธอตรงที่เดิมนะริมหน้าต่างที่ฝนเพิ่งหยุดตกลงมาอีกแล้ว"


def hl(text, bpm=100, dur=180, speed="normal"):
    ins = L.analyze(text)
    d = L.density(ins, L.DensitySettings(bpm=bpm, timesig="4", duration=dur, speed=speed))
    return ins, d, L.health(ins, d)


def kinds(items):
    return [h.kind for h in items]


def test_health_normal_lines_give_no_line_warning():
    ins, d, h = hl("[Verse]\n" + "\n".join(NORMAL) + "\n[Chorus]\n" + "\n".join(NORMAL) + "\n")
    assert all(6 <= x.syllable_count <= 10 for x in ins.lines) and h == []


def test_health_one_long_line_among_normal_ones():
    ins, d, h = hl("[Chorus]\n" + "\n".join(NORMAL[:2] + [LONG16] + NORMAL[3:]) + "\n")
    long_ = [x for x in h if x.kind == "long_line"]
    assert len(long_) == 1 and long_[0].lines == [4] and long_[0].basis == "detected" and not long_[0].hedged
    assert str(ins.lines[2].syllable_count) in long_[0].summary and "uneven" not in kinds(h)         # one finding per line, not two


def test_health_uneven_phrasing_needs_a_real_difference_not_just_unequal_lines():
    a = "[Verse]\nฝนตกหนักมาก\nฟ้าไกลใจเรา\nฝนตกหนักมากมาย\nฟ้าไกลใจเธอ\n"                       # 4-6 syllables each: unequal but close
    assert "uneven" not in kinds(hl(a)[2])
    b = "[Verse]\nฝนตกหนักมาก\nฟ้าไกลใจเรา\nฝนตกหนักมากมายทั้งคืนทั้งวันเลยนะ\nฟ้าไกลใจเธอ\n"
    got = [x for x in hl(b)[2] if x.kind in ("uneven", "long_line")]
    assert got and got[0].lines == [4]


def test_health_estimated_counts_are_hedged_not_confirmed():
    ins, d, h = hl("[Verse]\n" + "\n".join(NORMAL[:3]) + "\nI really really love you more than words can say tonight\n")
    item = [x for x in h if x.lines == [5] and x.kind in ("long_line", "uneven")]
    assert item and item[0].hedged and item[0].basis == "guideline" and "estimate" in item[0].summary


def test_health_rap_is_not_judged_like_a_ballad():
    text = "[Verse]\n" + "\n".join([LONG16] * 4) + "\n"
    ballad = hl(text, dur=240, speed="slow")[2]
    rap = hl(text, dur=240, speed="fast")[2]
    assert len([x for x in rap if x.kind in ("long_line", "uneven")]) == 0                     # equally long lines: nothing stands out
    assert len([x for x in ballad if x.kind in ("long_line", "uneven")]) == 0                  # and uniform lines are not "uneven" either
    mixed = "[Verse]\n" + "\n".join([NORMAL[0], NORMAL[1], "ฉันยังรอเธอตรงที่เดิมนะริมหน้าต่างที่ฝน", NORMAL[3]]) + "\n"        # 7, 7, 13, 6
    assert any(x.kind == "long_line" for x in hl(mixed, speed="normal")[2]) and not any(x.kind == "long_line" for x in hl(mixed, speed="fast")[2])


def test_health_duration_pressure_for_many_sections_and_no_percentage():
    secs = "".join(f"[{n}]\n{NORMAL[0]}\n{NORMAL[1]}\n" for n in ("Verse 1", "Chorus", "Verse 2", "Chorus", "Bridge", "Chorus", "Verse 3", "Chorus"))
    ins, d, h = hl(secs, dur=100)
    st = [x for x in h if x.kind == "structure"]
    assert st and st[0].spec == "music_producer" and st[0].basis == "estimate" and "%" not in st[0].summary + st[0].detail and "probab" in st[0].detail
    assert "Chorus" in st[0].detail
    assert not any(x.kind == "structure" for x in hl(secs, dur=400)[2])


def test_health_updates_when_lyrics_change():
    base = "[Chorus]\n" + "\n".join(NORMAL) + "\n"
    assert hl(base)[2] == []
    longer = "[Chorus]\n" + "\n".join(NORMAL[:2] + [LONG16] + NORMAL[3:]) + "\n"
    assert hl(longer)[2] and hl(base)[2] == []


def test_health_blocks_go_to_the_right_ai():
    ins, d, h = hl("[Chorus]\n" + "\n".join(NORMAL[:2] + [LONG16] + NORMAL[3:]) + "\n")
    sw, pr = L.songwriter_block(ins, d), L.producer_block(ins, d)
    assert "ACE-STEP LYRICS HEALTH" in sw and "line 4 is long" in sw and "Detected" in sw and "not predicted" in sw
    assert "ACE-STEP LYRICS HEALTH" in pr and "NOT known" in pr


def test_health_priority_order():
    secs = "".join(f"[{n}]\n{LONG16}\n{LONG16}\n{LONG16}\n{LONG16}\n" for n in ("Verse 1", "Chorus", "Verse 2", "Chorus", "Bridge"))
    h = hl(secs, dur=60)[2]
    assert [x.priority for x in h] == sorted(x.priority for x in h) and h[0].kind == "song_density"


def _long_chorus():
    return "[Chorus]\n" + "\n".join(NORMAL[:2] + [LONG16] + NORMAL[3:]) + "\n"


def test_comfortable_section_does_not_call_one_long_line_a_rush():
    ins, d, h = hl(_long_chorus())
    assert d.known and d.sections[0].level == "comfortable" and d.warnings == []
    long_ = [x for x in h if x.kind == "long_line"]
    assert len(long_) == 1 and long_[0].lines == [4]
    summary = long_[0].summary
    assert "The section overall is Comfortable" in summary
    assert "Only this line is long" in summary
    assert "It may be sung rushed" not in summary
    assert str(ins.lines[2].syllable_count) in summary
    assert L.health_tone(long_) == "line"
    assert L.health_tone(h) == "line"


def test_one_dense_line_stays_apart_from_a_comfortable_section():
    ins, d, h = hl(_long_chorus(), dur=20)
    line = [x for x in d.lines if x.line_number == 4][0]
    assert d.sections[0].level == "comfortable" and d.warnings == [] and line.level == "dense" and line.outlier
    item = [x for x in h if x.kind == "long_line"]
    assert len(item) == 1
    assert "The section overall is Comfortable" in item[0].summary
    assert "Only this line is Dense" in item[0].summary
    assert "not a warning that the whole section is dense" in item[0].summary
    assert "It may be sung rushed" not in item[0].summary
    assert L.health_tone(item) == "line"


def test_dense_section_line_note_is_not_a_second_copy():
    base = "ฉันยังรอเธอตรงที่เดิมนะริมหน้าต่างที่ฝนเพิ่งหยุด"
    extra = base + "ตกลงมาอีกแล้วจริงๆนะครับ"
    text = "[Chorus]\n" + "\n".join([base] * 10 + [extra]) + "\n"
    ins, d, h = hl(text, dur=45)
    assert d.sections[0].level == "dense" and d.warnings
    sec = [x for x in h if x.kind == "section_density"]
    line = [x for x in h if x.kind == "long_line"]
    assert sec and len(line) == 1
    assert "second warning" in line[0].summary
    assert "The section overall is Dense" in line[0].summary
    assert sec[0].summary not in line[0].summary
    assert "ACE-Step may rush" not in line[0].summary
    assert L.health_tone(sec + line) == "warning"
    assert L.health_tone(line) == "line"


def test_warnings_do_not_follow_another_song():
    busy, calm = _long_chorus(), "[Verse]\n" + "\n".join(NORMAL) + "\n[Chorus]\n" + "\n".join(NORMAL) + "\n"
    a, b = hl(busy)[2], hl(calm)[2]
    assert any(x.kind == "long_line" and x.lines == [4] for x in a)
    assert b == []
    assert any(x.lines == [4] for x in hl(busy)[2]) and hl(calm)[2] == []


def test_line_note_is_clear_in_thai():
    from music_studio import i18n as I18N
    I18N.set_lang("th")
    summary = [x.summary for x in hl(_long_chorus())[2] if x.kind == "long_line"][0]
    thai = I18N.tr(summary)
    assert "บรรทัด 4 มี 20 พยางค์" in thai
    assert "ร้องสบาย" in thai
    assert "เฉพาะบรรทัดนี้ที่ยาว" in thai
    assert "ไม่ได้แปลว่าทั้งท่อนแน่น" in thai
    assert "has 20 syllables" not in thai
    assert "อาจถูกร้องรวบ" not in thai
    assert "It may be sung rushed" not in thai
    I18N.set_lang("en")


def test_health_groups_keep_density_apart_from_structure():
    assert L.DENSITY_KINDS.isdisjoint(L.STRUCTURE_KINDS)
    assert L.health_group("long_line") == "density" and L.health_group("song_density") == "density"
    assert L.health_group("structure") == "structure" and L.health_group("duplicate_section") == "structure"
    assert L.health_group("cue_conflict") == "structure" and L.health_group("tag_complexity") == "structure"


def test_label_keeps_a_hyphen_inside_the_section_name():
    assert L.section_name("[Pre-Chorus]") == "Pre-Chorus"
    assert L.section_name("[Post-Chorus - soft]") == "Post-Chorus"
    assert L.section_name("[Intro — Rhodes piano]") == "Intro"
    assert L._label_of("[Pre-Chorus]") == "Pre-Chorus"
    assert L._label_of("[Pre-Chorus - soft vocals]") == "Pre-Chorus"
    assert L._label_of("[Intro — Rhodes piano]") == "Intro"
    assert L._cue_parts("[Chorus - whispered, anthemic]")[1] == ["whispered", "anthemic"]
    assert L._cue_parts("[Chorus - anthemic, powerful belting]")[1] == ["anthemic", "powerful belting"]
    text = "[Pre-Chorus]\nกลับมาได้ไหม\n[Post-Chorus - soft]\nฉันยังอยู่ตรงนี้\n[Intro — Rhodes piano]\nริมหน้าต่างที่ฝนเพิ่งหยุด\n"
    ins = L.analyze(text)
    assert [s.name for s in ins.sections] == ["Pre-Chorus", "Post-Chorus", "Intro"]
    assert text == "[Pre-Chorus]\nกลับมาได้ไหม\n[Post-Chorus - soft]\nฉันยังอยู่ตรงนี้\n[Intro — Rhodes piano]\nริมหน้าต่างที่ฝนเพิ่งหยุด\n"


def test_intentional_chorus_and_prechorus_are_not_duplicate_warnings():
    body = "\n".join(NORMAL)
    text = f"[Verse 1]\n{body}\n[Pre-Chorus]\n{body}\n[Chorus]\n{body}\n[Verse 2]\n{body}\n[Pre-Chorus]\n{body}\n[Chorus]\n{body}\n"
    h = hl(text, dur=240)[2]
    assert "duplicate_section" not in kinds(h)
    assert "cue_conflict" not in kinds(h) and "tag_complexity" not in kinds(h)


def test_stacked_section_and_repeated_verse_number_warn_without_editing():
    stacked = "[Verse]\n" + "\n".join(NORMAL) + "\n[Bridge]\n[Bridge]\n" + "\n".join(NORMAL[:2]) + "\n"
    h = hl(stacked)[2]
    dup = [x for x in h if x.kind == "duplicate_section"]
    assert len(dup) == 1 and dup[0].spec == "music_producer" and "stacked again" in dup[0].summary and "Bridge" in dup[0].summary
    assert L.health_group(dup[0].kind) == "structure"
    repeated = "[Verse 1]\n" + "\n".join(NORMAL) + "\n[Chorus]\n" + "\n".join(NORMAL) + "\n[Verse 1]\n" + "\n".join(NORMAL) + "\n"
    h2 = hl(repeated, dur=180)[2]
    again = [x for x in h2 if x.kind == "duplicate_section"]
    assert again and "Verse 1" in again[0].summary and "not a chorus reprise" in again[0].summary
    assert "song_density" not in kinds(again)


def test_conflicting_and_crowded_cues_are_structure_notes():
    conflict = "[Chorus - whispered, anthemic]\n" + "\n".join(NORMAL) + "\n"
    h = hl(conflict)[2]
    assert [x.kind for x in h if L.health_group(x.kind) == "structure"] == ["cue_conflict"]
    assert "whispered" in h[0].summary and "anthemic" in h[0].summary
    agree = "[Chorus - anthemic, powerful belting]\n" + "\n".join(NORMAL) + "\n"
    assert not any(x.kind in ("cue_conflict", "tag_complexity", "duplicate_section") for x in hl(agree)[2])
    crowded = "[Verse - soft, minimal, ambient]\n" + "\n".join(NORMAL) + "\n"
    got = [x for x in hl(crowded)[2] if x.kind == "tag_complexity"]
    assert len(got) == 1 and "3 cues" in got[0].summary and got[0].kind != "cue_conflict"
    long_cue = "warm rhodes piano under brushed drums plus a muted trumpet trading long phrases across the room"
    assert len(long_cue) > L.MAX_CUE_CHARS
    long = f"[Intro - {long_cue}]\n" + "\n".join(NORMAL) + "\n"
    assert any(x.kind == "tag_complexity" and "character direction" in x.summary for x in hl(long)[2])
    inline = "[Verse]\n[Chorus] กลับมาได้ไหม\nฉันยังอยู่ตรงนี้\n"
    assert any(x.kind == "tag_complexity" and "bracket tag" in x.summary for x in hl(inline)[2])


def test_structure_note_is_kept_when_line_notes_fill_the_list():
    names = [f"Verse {i}" for i in range(1, 11)] + ["Bridge", "Bridge"]
    text = "".join(f"[{name}]\n" + "\n".join(NORMAL[:2] + [LONG16] + NORMAL[3:]) + "\n" for name in names)
    h = hl(text, dur=240)[2]
    assert len([x for x in h if x.kind == "long_line"]) >= L.MAX_HEALTH - 1
    assert any(x.kind == "duplicate_section" and "Bridge" in x.summary for x in h)
    assert h[0].kind in L.DENSITY_KINDS
    assert [x.priority for x in h] == sorted(x.priority for x in h)


def test_structure_note_is_clear_in_thai():
    from music_studio import i18n as I18N
    I18N.set_lang("th")
    stacked = "[Verse]\n" + "\n".join(NORMAL) + "\n[Bridge]\n[Bridge]\n" + "\n".join(NORMAL[:2]) + "\n"
    item = [x for x in hl(stacked)[2] if x.kind == "duplicate_section"][0]
    thai = I18N.tr(item.summary)
    assert "ถูกวางซ้ำที่บรรทัด" in thai and "ไม่แก้เนื้อเพลง" in thai
    assert "stacked again" not in thai
    title = I18N.tr(item.title)
    assert "ท่อนซ้ำ" in title
    detail = I18N.tr(item.detail)
    assert "เนื้อเพลงไม่ได้ถูกเขียนใหม่" in detail
    conflict = "[Chorus - whispered, anthemic]\n" + "\n".join(NORMAL) + "\n"
    cue = [x for x in hl(conflict)[2] if x.kind == "cue_conflict"][0]
    cue_th = I18N.tr(cue.summary)
    assert "ตรงข้ามกับ" in cue_th and "whispered" in cue_th and "puts" not in cue_th
    I18N.set_lang("en")
