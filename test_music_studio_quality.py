"""Static checks for the Music Studio quality pass.

Run:  python test_music_studio_quality.py
No UI, no ComfyUI, no song generation, and nothing is written to the user's library.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from music_studio import ace_step_knowledge as AK
from music_studio import ai_music_director as AD
from music_studio import basic_caption as BC
from music_studio import lyric_analysis as LA
from music_studio import lyric_rhyme as LR
from music_studio import lyric_rhythm as RT
from music_studio import lyric_thesaurus as TH
from music_studio import template_arrange as TA
from music_studio import thai_rhyme as TR

APP = (ROOT / "music_studio.py").read_text(encoding="utf-8")


def _marks(body: str, start: str, end: str) -> str:
    return f"==========={start}===========\n{body}\n==========={end}==========="


class ThaiSound(unittest.TestCase):
    def test_known_end_rhymes(self):
        good = [("ดาว", "หนาว"), ("ใจ", "ไป"), ("ฟ้า", "มา"), ("รัก", "หนัก"), ("น้ำ", "คำ"),
                ("ไฟ", "ไหม้"), ("ทำ", "กรรม"), ("สรร", "อัน"), ("เคย", "เลย"), ("สวย", "รวย"), ("รวม", "สวม")]
        for a, b in good:
            status, _note = TR.verify_pair("end", a, b)
            self.assertEqual(status, TR.VERIFIED, f"{a}-{b}")

    def test_known_rejections(self):
        bad = [("กาน", "กิน"), ("บ้าน", "ฝัน"), ("ดาว", "ใจ"), ("ดาว", "ไป"), ("คืน", "นี้"), ("ควร", "บวก")]
        for a, b in bad:
            status, _note = TR.verify_pair("end", a, b)
            self.assertEqual(status, TR.REJECTED, f"{a}-{b} {TR.describe_rime(a)} vs {TR.describe_rime(b)}")

    def test_near_is_not_relabeled_exact(self):
        self.assertEqual(TR.verify_pair("near", "บ้าน", "ฝัน")[0], TR.VERIFIED)
        self.assertEqual(TR.verify_pair("near", "กาน", "กิน")[0], TR.REJECTED)
        self.assertEqual(TR.verify_pair("end", "บ้าน", "ฝัน")[0], TR.REJECTED)
        status, note = TR.verify_pair("near", "ดาว", "หนาว")
        self.assertNotEqual(status, TR.VERIFIED)
        self.assertEqual(status, TR.UNVERIFIED)
        self.assertIn("exactly", note)
        self.assertEqual(TR.sound_relation("ดาว", "หนาว")[0], "exact")
        self.assertEqual(TR.sound_relation("บ้าน", "ฝัน")[0], "near")
        self.assertEqual(TR.sound_relation("กาน", "กิน")[0], "different")

    def test_whole_syllables_beside_other_words_are_not_fragments(self):
        kept = (
            ("เธอ", "เธอมา"),
            ("เธอ", "เธอมาแล้ว"),
            ("ใจ", "ในหัวใจฉัน"),
            ("ใจ", "ใจยังคิดถึง"),
            ("เธอ", "ฉันรักเธอมาก"),
            ("เจอ", "วันนี้ฉันเจอ"),
        )
        for word, line in kept:
            self.assertNotEqual(TR.placement(line, word)["best"], "fragment", f"{word} in {line}")
            self.assertEqual(TR.placement(line, word)["best"], "ok", f"{word} in {line}")
        self.assertEqual(TR.placement("ดาว", "าว")["best"], "fragment")
        self.assertEqual(TR.placement("ฉันมองดาว", "าว")["best"], "fragment")
        self.assertEqual(TR.placement("ฉันมองดาว", "ดา")["best"], "fragment")

    def test_final_syllable_and_placement(self):
        self.assertEqual(TR.final_syllable("ความรัก"), "รัก")
        self.assertEqual(TR.final_syllable("ทะเล"), "เล")
        self.assertEqual(TR.final_syllable("กินข้าว"), "ข้าว")
        self.assertEqual(TR.final_syllable("คนเดียว"), "เดียว")
        self.assertIsNone(TR.final_syllable("เสมอ"))
        self.assertIsNone(TR.final_syllable("เสมอมา"))
        self.assertIsNone(TR.final_syllable("สวัสดี"))          # hidden vowels: the splitter does not guess สะ·วัด·ดี
        for word, line in (("ดาว", "ฉันมองดาว"), ("ฟ้า", "เต็มฟ้า")):
            self.assertEqual(TR.placement(line, word)["end"], "ok", word)
        self.assertEqual(TR.placement("เธอยังหนาวอยู่", "หนาว")["other"], "ok")
        self.assertEqual(TR.placement("เธอยังหนาวอยู่", "หนาว")["end"], "missing")
        for word, line in (("าว", "ฉันมองดาว"), ("ดา", "ฉันมองดาว"), ("มอ", "เสมอ"), ("น", "กิน"), ("าน", "บ้าน")):
            self.assertEqual(TR.placement(line, word)["best"], "fragment", word)

    def test_single_syllable_spellings(self):
        for word in ("ไกล", "ใกล้", "ใหญ่", "สวย", "ด้วย", "ช่วย", "อยู่", "หนาว", "หนัก", "ฝน", "พร", "กรรม", "ทำ", "น้ำ", "ควร"):
            self.assertTrue(TR.is_single_syllable(word), word)
        self.assertIn("กา", TR.describe_rime("ไกล"))
        self.assertFalse(TR.is_single_syllable("ว"))
        self.assertFalse(TR.is_single_syllable("น"))

    def test_dict_glue_stays_uncertain_instead_of_a_guess(self):
        """PyThaiNLP's dict split of เสมอมา is เสม+อมา. มา is a real ending, but the
        same token shape is งค์ in องค์ and วัส in สวัสดี, which must not be confirmed."""
        placed = TR.placement("เสมอมา", "มา")
        self.assertEqual(placed["end"], "uncertain")
        self.assertNotEqual(placed["best"], "fragment")
        self.assertNotEqual(TR.placement("องค์", "งค์")["best"], "ok")
        self.assertNotEqual(TR.placement("เธอองค์", "งค์")["best"], "ok")
        self.assertNotEqual(TR.placement("สวัสดี", "วัส")["best"], "ok")
        self.assertNotEqual(TR.placement("ธรรมชาติ", "ติ")["best"], "ok")
        self.assertNotEqual(TR.placement("ธรรมชาติ", "ชา")["best"], "ok")


class RhymeParse(unittest.TestCase):
    def _lines(self):
        text = ["ฉันมองดาว", "เธอยังหนาวอยู่", "ในใจ", "ฉันไป", "กาน", "กิน", "บ้าน", "ฝัน", "The night is bright today"]
        return [LA.SungLine(i, i, t) for i, t in enumerate(text, 1)]

    def test_window_sorts_verified_and_rejected(self):
        body = "\n".join([
            "cross | 1:ดาว | 2:หนาว | high | th | same -aao",
            "end | 7:บ้าน | 8:ฝัน | high | th | same final",
            "near | 7:บ้าน | 8:ฝัน | medium | th | long and short a",
            "end | 3:ใจ | 4:ไป | high | th | same -ai",
            "end | 5:กาน | 6:กิน | high | th | same final",
            "end | 1:ดาว | 4:ไป | high | th | both end",
            "end | 1:าว | 4:ไป | high | th | piece of a syllable",
            "internal | 9:night | 9:bright | high | en | same -ight",
        ])
        pairs, ignored, rejected = LR.parse_window(_marks(body, " RHYMES ", " END "), self._lines())
        got = {(p.type, p.a_word, p.b_word, p.verify) for p in pairs}
        self.assertIn(("cross", "ดาว", "หนาว", "verified"), got)
        self.assertIn(("near", "บ้าน", "ฝัน", "verified"), got)
        self.assertIn(("end", "ใจ", "ไป", "verified"), got)
        self.assertIn(("internal", "night", "bright", "unverified"), got)
        self.assertFalse(any(p.type == "end" and p.a_word == "บ้าน" for p in pairs))
        rej = {(p.a_word, p.b_word) for p in rejected}
        self.assertIn(("กาน", "กิน"), rej)
        self.assertIn(("ดาว", "ไป"), rej)
        self.assertTrue(any("าว" in m for m in ignored))

    def test_gemma_end_label_on_a_cross_position_is_kept_as_cross(self):
        raw = """=========== RHYMES ===========
end | 1:ดาว | 2:หนาว | high | th | same vowel and final sound -aao
end | 3:ใจ | 4:ไป | high | th | same vowel and final sound -ai
end | 5:กาน | 6:กิน | medium | th | same final consonant -n, similar vowel
near | 7:บ้าน | 8:ฝัน | medium | th | same final consonant -n, different vowel
=========== END ==========="""
        pairs, ignored, rejected = LR.parse_window(raw, self._lines())
        got = {(p.type, p.a_word, p.b_word, p.verify) for p in pairs}
        self.assertIn(("cross", "ดาว", "หนาว", "verified"), got)
        self.assertIn(("end", "ใจ", "ไป", "verified"), got)
        self.assertIn(("near", "บ้าน", "ฝัน", "verified"), got)
        self.assertEqual([(p.a_word, p.b_word) for p in rejected], [("กาน", "กิน")])
        self.assertFalse(any("ดาว" in m for m in ignored))

    def test_near_report_of_an_exact_rhyme_is_reclassified(self):
        lines = [
            LA.SungLine(1, 1, "วันนี้ฉันเจอ"),
            LA.SungLine(2, 2, "เธอมาแล้ว"),
            LA.SungLine(3, 3, "ในใจ"),
            LA.SungLine(4, 4, "ฉันไป"),
            LA.SungLine(5, 5, "ใจยังคิดถึง"),
            LA.SungLine(6, 6, "ยังมีไปอีก"),
            LA.SungLine(7, 7, "ฉันมองดาว"),
        ]
        body = "\n".join([
            "near | 1:เจอ | 2:เธอ | high | th | close vowel",
            "near | 3:ใจ | 4:ไป | high | th | called near",
            "near | 5:ใจ | 6:ไป | medium | th | not a line-end pair",
            "end | 7:าว | 1:เจอ | high | th | piece",
        ])
        pairs, ignored, rejected = LR.parse_window(_marks(body, " RHYMES ", " END "), lines)
        got = {(p.type, p.a_word, p.b_word, p.verify) for p in pairs}
        self.assertIn(("cross", "เจอ", "เธอ", "verified"), got)
        self.assertIn(("end", "ใจ", "ไป", "verified"), got)
        self.assertNotIn(("near", "เจอ", "เธอ", "verified"), got)
        self.assertNotIn(("near", "ใจ", "ไป", "verified"), got)
        loose = next(p for p in pairs if p.a_line == 5 and p.b_line == 6)
        self.assertEqual((loose.type, loose.verify), ("near", "unverified"))
        self.assertIn("not confirmed as a near rhyme", loose.verify_note)
        self.assertTrue(any("าว" in m for m in ignored))
        self.assertEqual(rejected, [])
        noted = next(p for p in pairs if {p.a_word, p.b_word} == {"เจอ", "เธอ"})
        self.assertIn("near rhyme", noted.verify_note)
        self.assertIn("cross-line", noted.verify_note)

    def test_old_near_save_of_an_exact_pair_is_reclassified(self):
        old = {"text_hash": "abc", "backend": "local:gemma", "model": "m", "params": "t=0.2", "prompt_version": "rhyme-v3",
               "lines": ["ในใจ", "ฉันไป"],
               "pairs": [{"type": "near", "a_line": 1, "a_word": "ใจ", "b_line": 2, "b_word": "ไป",
                          "confidence": "high", "lang": "th", "reason": "similar", "verify": "verified"}]}
        got = LR.rhyme_from_dict(old)
        self.assertIsNotNone(got)
        _info, res = got
        self.assertEqual([(p.type, p.a_word, p.b_word, p.verify) for p in res.pairs], [("end", "ใจ", "ไป", "verified")])
        self.assertEqual(res.rejected, [])

    def test_old_save_moves_a_false_rhyme_out(self):
        old = {"text_hash": "abc", "backend": "local:gemma", "model": "m", "params": "t=0.2", "prompt_version": "rhyme-v2",
               "lines": ["กาน", "กิน", "The night is bright"],
               "pairs": [
                   {"type": "end", "a_line": 1, "a_word": "กาน", "b_line": 2, "b_word": "กิน", "confidence": "high", "lang": "th", "reason": "same final"},
                   {"type": "internal", "a_line": 3, "a_word": "night", "b_line": 3, "b_word": "bright", "confidence": "high", "lang": "en", "reason": "same sound"},
               ]}
        got = LR.rhyme_from_dict(old)
        self.assertIsNotNone(got)
        _info, res = got
        self.assertEqual([(p.a_word, p.b_word) for p in res.pairs], [("night", "bright")])
        self.assertEqual(res.pairs[0].verify, "unverified")
        self.assertEqual([(p.a_word, p.b_word) for p in res.rejected], [("กาน", "กิน")])
        self.assertIsNone(LR.rhyme_from_dict({"pairs": []}))


class Temperature(unittest.TestCase):
    def test_kind_never_reads_chat_temperature(self):
        self.assertIsNone(LA.analysis_temperature({}, None))
        self.assertIsNone(LA.analysis_temperature({"analysis_temperature": "nope"}, None))
        self.assertEqual(LA.analysis_temperature({}, "syl"), 0.2)
        self.assertEqual(LA.analysis_temperature({}, "rhyme"), 0.2)
        self.assertEqual(LA.analysis_temperature({}, "rhythm"), 0.4)
        self.assertEqual(LA.analysis_temperature({}, "thes"), 0.7)
        self.assertEqual(LA.analysis_temperature({"temperature": "1.5"}, "syl"), 0.2)
        self.assertEqual(LA.analysis_temperature({"analysis_temperature": "0.5"}, "rhythm"), 0.5)
        self.assertEqual(LA.analysis_temperature({"analysis_temperature_syl": "0.3", "analysis_temperature": "0.5"}, "syl"), 0.3)
        self.assertEqual(LA.analysis_temperature({"analysis_temperature": "nope"}, "rhyme"), 0.2)
        self.assertEqual(LA.analysis_temperature({"analysis_temperature_rhythm": "9"}, "rhythm"), 2.0)

    def test_ui_wires_each_analysis_and_leaves_actions_alone(self):
        for kind in ("syl", "rhyme", "rhythm", "thes"):
            self.assertIn(f'an_identity(backend, "{kind}")', APP)
            self.assertIn(f'an_temp("{kind}")', APP)
        actions = APP.split("def an_temp", 1)[0]
        self.assertIn("an_identity(S.backend)", actions)
        self.assertNotIn('an_identity(S.backend, "', actions)


class Rhythm(unittest.TestCase):
    def test_mock_ask_uses_counts_and_drops_a_short_breath(self):
        lines = [
            RT.RLine(1, "short line here", "Verse", 4, "ai", "syllable"),
            RT.RLine(2, "Every single morning I remember everything you said", "Verse", 22, "ai", "syllable"),
        ]
        long = lines[1].text
        raw = _marks("\n".join([
            f"1 | {lines[0].text} | breath_point | info | Pause here. | line",
            f"2 | {long} | dense_phrase | suggestion | Much more text than the line around it. | -",
            f"2 | {long} | breath_point | info | A pause after the first clause. | morning",
        ]), " RHYTHM ", " END ")
        seen = {}

        def ask(system, user):
            seen["system"] = system
            seen["user"] = user
            return raw

        res = RT.analyze_rhythm(lines, ask)
        self.assertIn("4 syllables", seen["user"])
        self.assertIn("22 syllables", seen["user"])
        self.assertIn("never invent a BPM", seen["system"])
        issues = {(i.line, i.issue) for i in res.items}
        self.assertEqual(issues, {(2, "dense_phrase"), (2, "breath_point")})
        self.assertNotEqual(RT.counts_hash(lines), RT.counts_hash([RT.RLine(1, lines[0].text, "Verse", 7, "count", "syllable"), lines[1]]))
        rhythm_fn = APP.split("async def run_rhythm", 1)[1].split("async def run_thesaurus", 1)[0]
        self.assertIn("await run_analysis(False)", rhythm_fn)


class Thesaurus(unittest.TestCase):
    def test_old_rows_and_changed_prefix(self):
        old = _marks("similar_meaning | ว้าเหว่ | ยังเหงา | ใกล้เคียงมาก | -", " SUGGESTIONS ", " END ")
        items, ignored = TH.parse_answer(old, "เหงาใจ")
        self.assertEqual(items[0].difference, "ใกล้เคียงมาก")
        self.assertFalse(TH.meaning_shifted(items[0].difference))
        self.assertEqual(ignored, [])
        new = _marks("emotional | ใจสลาย | หนักกว่า | changed: จากเหงาเป็นเสียใจ | -", " SUGGESTIONS ", " END ")
        items, _ = TH.parse_answer(new, "เหงาใจ")
        self.assertTrue(TH.meaning_shifted(items[0].difference))
        self.assertIn("natural Thai", TH.SYSTEM_PROMPT)


class CaptionAndDirector(unittest.TestCase):
    CAP = ("A mid-tempo Thai pop ballad with soft piano, warm electric guitar, restrained drums, "
           "and a close intimate vocal that stays comfortable through the verse and the chorus.")

    def test_caption_accepts_and_warns(self):
        raw = _marks(self.CAP, " CAPTION ", " END ")
        ok = BC.parse(raw, "ฝนตกทั้งคืน\n", "female vocal", "high energy", "")
        self.assertIn("soft piano", ok.caption)
        self.assertFalse(any("belted" in w for w in ok.warnings))
        belt = BC.parse(_marks(self.CAP + " Powerful belting on the chorus.", " CAPTION ", " END "), "ฝนตกทั้งคืน\n", "female vocal", "high energy", "")
        self.assertTrue(any("belted" in w for w in belt.warnings))
        self.assertIn("piano", belt.caption)
        asked = BC.parse(_marks(self.CAP + " Powerful belting on the chorus.", " CAPTION ", " END "), "", "female vocal", "high energy", "belting")
        self.assertFalse(any("belted" in w for w in asked.warnings))
        self.assertIn("High energy means the track feels driving", BC.SYSTEM)
        self.assertIsNone(TA.check_hint("powerful vocals"))
        self.assertEqual(TA.check_hint("belting"), "powerful belting")

    def _direction(self, n, caption, belt=False):
        cap = caption + (" Powerful belting on the chorus." if belt else "")
        return (
            f"========== DIRECTION {n} ===========\n"
            f"Name: Direction {n}\nReason: It fits this lyric.\nCharacteristics: Its own arrangement.\n"
            f"Music Caption: {cap}\nSuggested Settings:\n"
            "genre|Pop\nmood|Calm\nvocal|female vocal\nenergy|high energy\nbpm|90\nkey|C major\ntime_signature|4/4\nlanguage|th\n"
        )

    def test_restore_keeps_a_later_edit_and_overlap_warns(self):
        back, kept = AD.plan_restore({"bpm": 90, "style_prompt": "old"}, {"bpm": 120, "style_prompt": "new cap"}, {"bpm": 100, "style_prompt": "new cap"})
        self.assertEqual(back, {"style_prompt": "old"})
        self.assertEqual(kept, ["bpm"])
        same = self.CAP
        other = ("A sparse acoustic ballad with nylon guitar, brushed percussion, a low cello drone, "
                 "and a spoken-leaning vocal that leaves space between the phrases.")
        raw = (
            "=========== ANALYSIS ===========\nEmotion: quiet\nTheme: rain\nNarrative: waiting\nEnergy Arc: stays soft\nVocal Character: close\n"
            + self._direction(1, same) + self._direction(2, same + " A little more room in the mix.") + self._direction(3, other)
            + "=========== METADATA ===========\nbpm|90\nkey|C major\ntime_signature|4/4\nlanguage|th\n=========== END ==========="
        )
        prop = AD.parse(raw, "ฝนตกทั้งคืน")
        self.assertEqual(len(prop.directions), 3)
        self.assertTrue(any("share a lot" in w for w in prop.warnings))
        snap = AD.make_snapshot("song", "sheet", "A", "Title", "ฝนตกทั้งคืน",
                                {"vocal": "female vocal", "energy": "high energy", "extra_tags": "", "style_prompt": "", "style_mode": "basic"})
        warned = AD.analyze(lambda _s, _u: raw.replace(same, same + " Powerful belting on the chorus.", 1), snap)
        self.assertTrue(any("belted" in w for d in warned.directions for w in d.warnings))
        self.assertIn("differ in at least two", AD.SYSTEM)
        self.assertEqual(AK.KNOWLEDGE_VERSION, "ace-knowledge-v2")
        self.assertIn("without belting", AK.TOPICS["vocal"])
        self.assertNotIn("emotional chorus -> powerful vocals", AK.TOPICS["vocal"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
