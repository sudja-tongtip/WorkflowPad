"""Static checks for Thai vocal prosody in captions, songwriter knowledge, and live insights.

No LLM call, no audio, no song files, and no write to a user prompt copy.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from music_studio import ace_step_knowledge as AK
from music_studio import ai_music_director as AD
from music_studio import basic_caption as BC
from music_studio import live_insights as L
from music_studio import llm_chat as LC
from music_studio import music_director_context as MX
from music_studio import songwriting_craft as SC
from music_studio import specialist_engine as SE
from music_studio import specialists as SP

from test_caption_intelligence import basic_snap

ROOT = Path(__file__).resolve().parent
DIRECTOR_MD = ROOT / "music_studio" / "prompts" / "specialists" / "music_director.md"
WRITER_MD = ROOT / "music_studio" / "prompts" / "specialists" / "songwriter.md"
SHAPE = (
    "Natural Thai lead vocals with clear native diction, conversational melodic phrasing, "
    "controlled pitch movement and a comfortable vocal register. Preserve Thai word intelligibility "
    "while the instrumental arrangement provides the emotional lift."
)
PITCH = "not a musical pitch"
PROSODY = "THAI LYRIC PROSODY"
EDIT = "EDIT\ntarget: line 2\noriginal: <<<\nhello\n>>>\nreplacement: <<<\nhi there\n>>>\nreason: clearer\n"
ANALYZE = "ช่วยวิเคราะห์การออกเสียงของท่อนนี้"
LONG_LINE = "ในคืนที่ฝนตกฉันยังรอเธอตรงนี้"


def _sw(edits: str) -> str:
    return LC.SEP["REPLY"] + "\nดูบรรทัดนี้\n\n=========== EDITS ===========\n" + edits + "\n" + LC.SEP["PROPOSAL"] + "\n\n" + LC.SEP["END"] + "\n"


def _health(text: str):
    ins = L.analyze(text)
    dn = L.density(ins, L.DensitySettings(bpm=96, timesig="4", duration=180, speed="normal"))
    return ins, dn, L.health(ins, dn)


class CaptionProsodyTests(unittest.TestCase):
    def test_thai_caption_shape_is_one_shared_block(self):
        self.assertEqual(MX.CAPTION_CRAFT.count(SHAPE), 1)
        self.assertEqual(MX.CAPTION_CRAFT.count(PITCH), 1)
        self.assertEqual(BC.SYSTEM.count(SHAPE), 1)
        self.assertEqual(AD.SYSTEM.count(SHAPE), 1)
        self.assertEqual(BC.SYSTEM.count("CAPTION CRAFT"), 1)
        self.assertEqual(AD.SYSTEM.count("CAPTION CRAFT"), 1)
        self.assertEqual(BC.SYSTEM.count("Sound identity"), 1)
        self.assertEqual(AD.SYSTEM.count("Sound identity"), 1)
        self.assertEqual(BC.SYSTEM.count("High energy means the track feels driving"), 1)
        self.assertEqual(AD.SYSTEM.count("High energy means the track feels driving"), 1)
        for genre in ("Ambient pop:", "Acoustic ballad:", "Alternative rock:", "R&B:", "Synth-pop:"):
            self.assertIn(genre, BC.SYSTEM)
            self.assertIn(genre, AD.SYSTEM)
        self.assertIn("only as a shape, not a rule that every genre stays soft", MX.CAPTION_CRAFT)
        self.assertNotIn(SHAPE, DIRECTOR_MD.read_text(encoding="utf-8"))
        self.assertNotIn(SHAPE, Path(BC.__file__).read_text(encoding="utf-8"))
        self.assertNotIn(SHAPE, Path(AD.__file__).read_text(encoding="utf-8"))
        self.assertNotIn(SHAPE, AK.TOPICS["vocal"])
        self.assertEqual(AK.KNOWLEDGE_VERSION, "ace-knowledge-v2")

    def test_a_powerful_vocal_is_still_allowed(self):
        craft = MX.CAPTION_CRAFT
        self.assertIn("A powerful vocal is still available", craft)
        self.assertIn("Do not force every Thai song to be quiet or low", craft)
        self.assertIn("One direction may still be a powerful vocal", AD.SYSTEM)
        self.assertNotIn("every Thai song must", craft.lower())
        self.assertNotIn("always quiet", craft.lower())
        self.assertNotIn("always low", craft.lower())
        self.assertNotIn("must be soft", BC.SYSTEM.lower())
        self.assertNotIn("must be soft", AD.SYSTEM.lower())


class LyricProsodyTests(unittest.TestCase):
    def test_prosody_is_shared_once_and_does_not_assign_a_pitch(self):
        self.assertEqual(SC.SONGWRITING_CRAFT.count(PROSODY), 1)
        self.assertEqual(SC.THAI_LYRIC_PROSODY.count(PITCH), 1)
        self.assertIn("five lexical tones", SC.THAI_LYRIC_PROSODY)
        self.assertIn("Do not treat the mid tone as a low note or the high tone as a high note", SC.THAI_LYRIC_PROSODY)
        self.assertIn("never say a word will be sung on the wrong tone", SC.THAI_LYRIC_PROSODY)
        self.assertIn("do not rewrite the lyrics", SC.THAI_LYRIC_PROSODY)
        self.assertIn("Rhyme still ignores tone", SC.THAI_LYRIC_PROSODY)
        writer = SE.analyze_task("songwriter")
        producer = SE.analyze_task("music_producer")
        self.assertEqual(writer.count(PROSODY), 1)
        self.assertNotIn(PROSODY, producer)
        md = WRITER_MD.read_text(encoding="utf-8")
        self.assertEqual(SP.check(md), [])
        self.assertIn("tone is not a pitch, rhyme still ignores tone", md)
        self.assertNotIn(PROSODY, md)
        self.assertNotIn("five lexical tones", md)
        self.assertEqual((md + "\n\n" + writer).count("five lexical tones"), 1)
        chat = md + "\n\n" + SC.SONGWRITING_CRAFT
        self.assertEqual(chat.count(PROSODY), 1)
        self.assertNotIn(PROSODY, BC.SYSTEM)
        self.assertNotIn(PROSODY, AD.SYSTEM)
        self.assertNotIn("CAPTION CRAFT", SC.SONGWRITING_CRAFT)
        self.assertEqual(LC.SYSTEM_PROMPT.count(PROSODY), 1)
        sources = "\n".join(
            Path(p).read_text(encoding="utf-8")
            for p in (L.__file__, SC.__file__, MX.__file__, SE.__file__, WRITER_MD, DIRECTOR_MD)
        )
        low = sources.lower()
        self.assertNotIn("จัตวาต้อง", sources)
        self.assertNotIn("สามัญต้อง", sources)
        self.assertNotIn("mid tone must", low)
        self.assertNotIn("high tone must", low)
        self.assertNotIn("จัตวา", Path(L.__file__).read_text(encoding="utf-8"))
        self.assertNotIn("สามัญ", Path(L.__file__).read_text(encoding="utf-8"))

    def test_analysis_only_does_not_make_an_edit_card(self):
        self.assertTrue(SE.is_question_only(ANALYZE))
        self.assertEqual(SE.chat_mode(ANALYZE), "talk")
        self.assertTrue(SE.is_question_only("ขอวิเคราะห์วรรณยุกต์ของคำว่าใน"))
        self.assertFalse(SE.is_question_only("แก้คำว่าในให้ธรรมชาติขึ้น"))
        song = "[Verse 1]\n" + LONG_LINE + "\n"
        before = song
        out = SE.handle_chat_answer("songwriter", _sw(EDIT), song, "", False, ANALYZE)
        self.assertEqual(out.edits, [])
        self.assertEqual(out.proposal, "")
        self.assertFalse(out.offered)
        self.assertEqual(song, before)


class LiveInsightTests(unittest.TestCase):
    def test_only_an_isolated_short_word_is_noted(self):
        self.assertEqual(L._lone_function_word("ใน"), "ใน")
        self.assertEqual(L._lone_function_word("ใน."), "ใน")
        self.assertEqual(L._lone_function_word("ใน ที่"), "")
        self.assertEqual(L._lone_function_word("ในคืนที่ฝนตก"), "")
        self.assertEqual(L._lone_function_word("รัก"), "")
        ins, _dn, items = _health("[Verse 1]\nใน\n")
        self.assertEqual(ins.lines[0].syllable_count, 1)
        notes = [h for h in items if h.kind == "short_word"]
        self.assertEqual(len(notes), 1)
        summary = notes[0].summary
        self.assertTrue(summary.startswith("Sung line "))
        self.assertIn('only the short word "ใน"', summary)
        self.assertIn("can be held or stressed", summary)
        self.assertIn("phrasing note", summary)
        self.assertIn("does not say the tone will be sung wrong", summary)
        self.assertIn("melody is not known", summary)
        self.assertNotIn("high note", summary.lower())
        self.assertNotIn("low note", summary.lower())
        self.assertEqual(notes[0].spec, "songwriter")
        self.assertEqual(notes[0].basis, "detected")
        self.assertFalse(notes[0].hedged)
        self.assertEqual(L.health_group("short_word"), "density")
        self.assertNotIn("short_word", L.STRUCTURE_KINDS)
        self.assertEqual(L.health_tone(notes), "line")
        block = L.songwriter_block(ins, _dn)
        self.assertIn("Vocal density: unknown", block)
        self.assertIn("phrasing note", block)
        self.assertIn("melody is not known", block)
        filled = "[Verse 1]\nใน\nฉันยังรอเธอตรงที่เดิม\nริมหน้าต่างที่ฝนเพิ่งหยุด\nยังมีกุญแจวางอยู่บนโต๊ะ\nแต่ไม่มีเธอกลับมา\n"
        ins2, dn2, items2 = _health(filled)
        self.assertTrue(dn2.known)
        self.assertEqual(sum(h.kind == "short_word" for h in items2), 1)
        self.assertIn("phrasing note", L.songwriter_block(ins2, dn2))
        self.assertNotIn("ฉันยังรอเธอตรงที่เดิม", L.songwriter_block(ins2, dn2).split("ACE-STEP LYRICS HEALTH", 1)[1])

    def test_a_longer_line_and_a_hook_are_not_flagged_and_the_note_is_capped(self):
        _ins, _dn, plain = _health("[Verse 1]\n" + LONG_LINE + "\nรัก\n")
        self.assertFalse(any(h.kind == "short_word" for h in plain))
        _ins, _dn, many = _health("[Verse 1]\nใน\nใน\nใน\n")
        self.assertEqual(sum(h.kind == "short_word" for h in many), 2)

    def test_the_caption_context_gets_the_note_without_changing_the_song(self):
        lyrics = "[Verse 1]\nใน\n" + LONG_LINE + "\n"
        snap = basic_snap(lyrics, language="th")
        before = (snap.lyrics, snap.bpm, snap.keyscale, snap.duration)
        ctx = MX.build_music_director_context(snap, "basic")
        self.assertEqual((snap.lyrics, snap.bpm, snap.keyscale, snap.duration), before)
        self.assertIn('only the short word "ใน"', ctx)
        self.assertNotIn(LONG_LINE, ctx)
        self.assertNotIn("WINDOWWORD", ctx)
        self.assertIn("Do not change BPM, key, duration, or the lyrics.", ctx)
        self.assertNotIn("lyrics", AD.FIELDS)
        _system, user = BC.build_messages(snap)
        self.assertEqual(snap.lyrics, lyrics)
        self.assertIn(lyrics.strip(), user)
        self.assertEqual(snap.bpm, before[1])
        self.assertEqual(snap.keyscale, before[2])
        self.assertEqual(snap.duration, before[3])


class PromptFileTests(unittest.TestCase):
    def test_user_prompt_copies_are_not_written(self):
        paths = [SP.USER_DIR / "music_director.md", SP.USER_DIR / "songwriter.md"]
        before = [(p, p.read_bytes() if p.exists() else None) for p in paths]
        MX.build_music_director_context(basic_snap("[Verse 1]\nใน\n", language="th"), "basic")
        SE.analyze_task("songwriter")
        SE.is_question_only(ANALYZE)
        for path, old in before:
            self.assertEqual(path.exists(), old is not None)
            if old is not None:
                self.assertEqual(path.read_bytes(), old)


if __name__ == "__main__":
    unittest.main()
