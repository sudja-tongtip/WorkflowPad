"""Static checks for Music Director caption context and craft.

No LLM call, no audio, no song files, and no write to the user's prompt copy.
"""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from music_studio import ai_music_director as AD
from music_studio import basic_caption as BC
from music_studio import live_insights as LI
from music_studio import music_director_context as MX
from music_studio import specialists as SP

ROOT = Path(__file__).resolve().parent
BUNDLED = ROOT / "music_studio" / "prompts" / "specialists" / "music_director.md"

LYRIC_A = """[Intro — Rhodes piano]
[Verse 1]
WINDOWWORD sits by the glass tonight
[Pre-Chorus - soft]
The room is quiet now
[Chorus]
Stay with me here
[Verse 2]
The street is nearly empty
[Chorus - whispered]
Stay with me here
[Bridge]
Morning finds the window
"""

LYRIC_B = """[Zephyr Bridge]
OTHERWORD under a different moon tonight
"""

WINDOW_LINE = "WINDOWWORD sits by the glass tonight"
CROWDED = ("Electric guitar, piano, upright bass, drums, strings, trumpet, and organ each take a separate lead "
           "while the vocal stays close and quiet.")
CLEAN = ("A fingerpicked acoustic guitar and a quiet upright bass leave the vocal breathy, close, and unforced "
         "from the first verse through the bridge.")
BELTED = "Powerful belting on the chorus over a single piano and nothing else in the room."
SPACIOUS = "A spacious vocal with long held notes drifts over a quiet piano and a soft bass."


def basic_snap(lyrics, **kw):
    data = dict(song_id="song-a", sheet_id="sheet-a", title="Window", lyrics=lyrics, genre="Zephyrcore", mood="Calm",
                vocal="auto", energy="low energy", extra_tags="", language="en", bpm=96, keyscale="F# minor",
                timesignature="4", duration=180)
    data.update(kw)
    return BC.Snapshot(**data)


def director_snap(lyrics, reference=False, **settings):
    base = {"genre": "Zephyrcore", "mood": "Calm", "vocal": "auto", "energy": "low energy", "extra_tags": "",
            "style_mode": "basic", "style_prompt": "", "bpm": 96, "keyscale": "F# minor", "timesignature": "4",
            "duration": 180, "language": "en"}
    base.update(settings)
    return AD.make_snapshot("song-a", "sheet-a", "Sheet A", "Window", lyrics, base, reference=reference)


def _marks(body, start, end=" END "):
    return f"==========={start}===========\n{body}\n==========={end}==========="


def _direction(n, caption, genre="Pop"):
    return (
        f"=========== DIRECTION {n} ===========\n"
        f"Name: Direction {n}\nReason: It fits this lyric.\nCharacteristics: Its own arrangement.\n"
        f"Music Caption: {caption}\nSuggested Settings:\n"
        f"genre|{genre}\nmood|Calm\nvocal|auto\nenergy|low energy\nbpm|96\nkey|C major\ntime_signature|4/4\nlanguage|en\n"
    )


def _proposal(*captions):
    body = "".join(_direction(i, cap) for i, cap in enumerate(captions, 1))
    return (
        "=========== ANALYSIS ===========\nEmotion: quiet\nTheme: waiting\nNarrative: a night by the window\n"
        "Energy Arc: stays close, then opens\nVocal Character: unforced\n"
        + body
        + "=========== METADATA ===========\nbpm|96\nkey|C major\ntime_signature|4/4\nduration|180\nlanguage|en\n"
        "=========== END ==========="
    )


class ContextTests(unittest.TestCase):
    def test_blueprint_density_cues_and_confidence(self):
        snap = basic_snap(LYRIC_A)
        ctx = MX.build_music_director_context(snap, "basic")
        ins, dn = MX._analysis(snap)
        self.assertIn("Section blueprint:", ctx)
        self.assertIn("- Intro | instrumental | cue: Rhodes piano", ctx)
        self.assertIn("- Pre-Chorus | 1 sung line | cue: soft", ctx)
        self.assertIn("- Chorus (return) | 1 sung line | cue: whispered", ctx)
        self.assertLess(ctx.index("- Intro"), ctx.index("- Verse 1"))
        self.assertLess(ctx.index("- Verse 1"), ctx.index("- Pre-Chorus"))
        self.assertLess(ctx.index("- Pre-Chorus"), ctx.index("Chorus (return)"))
        self.assertNotIn(WINDOW_LINE, ctx)
        self.assertNotIn("OTHERWORD", ctx)
        self.assertNotIn("Zephyr", ctx)
        self.assertNotIn("Zephyrcore", ctx)
        self.assertNotIn("F# minor", ctx)
        self.assertNotIn("song-a", ctx)
        self.assertIn("Target duration: 180 s", ctx)
        self.assertIn("Vocal language setting: en", ctx)
        self.assertIn("96 BPM, 4/4", ctx)
        self.assertIn("Do not change BPM", ctx)
        self.assertNotIn("Propose the tempo", ctx)
        self.assertIn("Some syllable totals are estimates", ctx)
        self.assertIn("Density and duration risk are estimates", ctx)
        for sec in dn.sections:
            if sec.name == "Intro":
                self.assertIn("Intro: instrumental", ctx)
            elif sec.syllables:
                self.assertIn(f"{sec.syllables} syllables", ctx)
        self.assertEqual(dn.sections[1].syllables, ins.sections[1].syllables)
        self.assertIn(f"Whole song: {LI.LEVEL_NAME[dn.song_level]}", ctx)
        self.assertLess(len(ctx), 3500)

    def test_songs_do_not_share_a_context(self):
        a = MX.build_music_director_context(basic_snap(LYRIC_A), "basic")
        b = MX.build_music_director_context(basic_snap(LYRIC_B, song_id="song-b", sheet_id="sheet-b", title="Other"), "basic")
        self.assertIn("Pre-Chorus", a)
        self.assertNotIn("Pre-Chorus", b)
        self.assertNotIn("WINDOWWORD", a)
        self.assertNotIn("WINDOWWORD", b)
        self.assertIn("Zephyr Bridge", b)
        self.assertNotIn("Zephyr", a)
        self.assertNotIn(WINDOW_LINE, b)
        self.assertNotIn("OTHERWORD", a)
        self.assertNotIn("OTHERWORD", b)

    def test_recommend_context_is_not_a_basic_settings_block(self):
        snap = director_snap(LYRIC_A, reference=True)
        ctx = MX.build_music_director_context(snap, "recommend")
        self.assertIn("Choose the genre", ctx)
        self.assertIn("Propose the tempo", ctx)
        self.assertIn("96 BPM", ctx)
        self.assertNotIn("Do not change BPM", ctx)
        self.assertNotIn("Zephyrcore", ctx)
        self.assertNotIn("F# minor", ctx)
        self.assertNotIn("REFERENCE", ctx)
        self.assertNotIn(WINDOW_LINE, ctx)

    def test_thai_confidence_is_separated_from_the_estimate(self):
        lyrics = "[Verse 1]\nฉันยังรอเธอตรงที่เดิม\nริมหน้าต่างที่ฝนเพิ่งหยุด\n"
        ctx = MX.build_music_director_context(basic_snap(lyrics, language="th"), "basic")
        self.assertIn("dictionary-backed", ctx)
        self.assertIn("Density and duration risk are estimates", ctx)
        self.assertNotIn("ฉันยังรอเธอตรงที่เดิม", ctx)

    def test_health_names_the_problem_and_not_the_line(self):
        long_line = "I keep counting every quiet hour after midnight while the window stays open"
        lyrics = "[Verse 1]\nGo\nGo\nGo\n" + long_line + "\n"
        ctx = MX.build_music_director_context(basic_snap(lyrics), "basic")
        self.assertIn("is long", ctx)
        self.assertNotIn(long_line, ctx)
        stacked = "[Verse 1]\n[Verse 1]\nhello there friend today\n"
        stacked_ctx = MX.build_music_director_context(basic_snap(stacked), "basic")
        self.assertIn("repeated section", stacked_ctx)
        self.assertIn("(return)", stacked_ctx)
        parts = []
        for name in ("Verse 1", "Chorus", "Verse 2", "Bridge"):
            parts.append(f"[{name}]\nI stay tonight\nI stay tonight")
        tight = MX.build_music_director_context(basic_snap("\n".join(parts), duration=40, bpm=120), "basic")
        self.assertIn("Many sections", tight)
        self.assertNotIn("I stay tonight", tight)

    def test_missing_clock_does_not_invent_a_tempo(self):
        ctx = MX.build_music_director_context(basic_snap(LYRIC_A, bpm=0, duration=0), "recommend")
        self.assertIn("density is unknown", ctx)
        self.assertNotIn("120 BPM", ctx)
        self.assertIsInstance(MX.review_caption(BELTED, basic_snap(LYRIC_A, bpm=0, duration=0)), list)

    def test_a_broken_snapshot_does_not_fail_the_request(self):
        class Bad:
            lyrics = 1
        self.assertIn("could not be counted", MX.build_music_director_context(Bad(), "basic"))


class CraftAndMessageTests(unittest.TestCase):
    def test_one_craft_block_and_the_old_contracts(self):
        for system, marker in ((BC.SYSTEM, "=========== CAPTION ==========="), (AD.SYSTEM, "=========== ANALYSIS ===========")):
            self.assertEqual(system.count("CAPTION CRAFT"), 1)
            self.assertEqual(system.count("Sound identity"), 1)
            self.assertEqual(system.count("High energy means the track feels driving"), 1)
            self.assertLess(system.index("CAPTION CRAFT"), system.index(marker))
            for genre in ("Ambient pop:", "Acoustic ballad:", "Alternative rock:", "R&B:", "Synth-pop:"):
                self.assertIn(genre, system)
        self.assertIn("differ in at least two", AD.SYSTEM)
        self.assertIn("=========== DIRECTION 1 ===========", AD.SYSTEM)
        self.assertIn("=========== END ===========", BC.SYSTEM)
        self.assertIn("=========== END ===========", AD.SYSTEM)
        text = BUNDLED.read_text(encoding="utf-8")
        self.assertEqual(SP.check(text), [])
        self.assertNotIn("soaring vocals", text)
        self.assertNotIn("comma-separated", text)
        self.assertNotIn("A hushed ambient-pop ballad", text)
        self.assertNotIn("Sound identity", text)
        for sec in SP.REQUIRED:
            self.assertIn(f"# {sec}", text)

    def test_basic_and_recommend_messages_carry_different_jobs(self):
        basic_system, basic_user = BC.build_messages(basic_snap(LYRIC_A))
        self.assertEqual(basic_system, BC.SYSTEM)
        self.assertIn("Zephyrcore", basic_user)
        self.assertIn("F# minor", basic_user)
        self.assertIn(WINDOW_LINE, basic_user)
        self.assertIn("Do not change BPM", basic_user)
        self.assertNotIn("SECTIONS IN THE LYRICS", basic_user)
        self.assertNotIn("OTHERWORD", basic_user)
        analysis = basic_user.split("MUSIC ANALYSIS", 1)[1]
        self.assertNotIn("F# minor", analysis)
        self.assertNotIn(WINDOW_LINE, analysis)

        _sys, recommend = AD.build_messages(director_snap(LYRIC_A, reference=False), "English")
        self.assertNotIn("Zephyrcore", recommend)
        self.assertNotIn("F# minor", recommend)
        self.assertNotIn("REFERENCE (the user", recommend)
        self.assertIn("Propose the tempo", recommend)
        self.assertIn(WINDOW_LINE, recommend)
        self.assertNotIn("Do not change BPM", recommend)
        self.assertNotIn("OTHERWORD", recommend)

        _sys, referenced = AD.build_messages(director_snap(LYRIC_A, reference=True), "English")
        self.assertIn("REFERENCE (the user", referenced)
        self.assertIn("Zephyrcore", referenced)
        self.assertNotIn("Zephyrcore", referenced.split("MUSIC ANALYSIS", 1)[1])
        self.assertNotIn("F# minor", referenced)

    def test_user_prompt_copy_is_not_written(self):
        path = SP.USER_DIR / "music_director.md"
        existed, before = path.exists(), path.read_bytes() if path.exists() else None
        AD.build_messages(director_snap(LYRIC_A), "English")
        BC.build_messages(basic_snap(LYRIC_A))
        self.assertEqual(path.exists(), existed)
        if existed:
            self.assertEqual(path.read_bytes(), before)

    def test_prompt_sizes_stay_single_copies(self):
        ctx = MX.build_music_director_context(basic_snap(LYRIC_A), "basic")
        _s, user = BC.build_messages(basic_snap(LYRIC_A))
        _s, rec = AD.build_messages(director_snap(LYRIC_A), "English")
        self.assertLess(len(ctx), 3500)
        self.assertEqual(user.count("CAPTION CRAFT"), 0)
        self.assertEqual(rec.count("Sound identity"), 0)


class ReviewTests(unittest.TestCase):
    def test_aliases_do_not_double_count(self):
        self.assertEqual(MX._matches("an ambient pop ballad", MX._GENRE_PHRASES), {"ambient pop", "ballad"})
        self.assertEqual(MX._matches("slow r&b and also rnb", MX._GENRE_PHRASES), {"r&b"})
        self.assertEqual(MX._matches("soft synth pad", MX._INSTRUMENT_PHRASES), {"synth"})
        self.assertEqual(MX._matches("electric guitar and a guitar", MX._INSTRUMENT_PHRASES), {"guitar"})

    def test_warnings_do_not_rewrite_or_reject(self):
        snap = basic_snap(LYRIC_A)
        many = MX.review_caption("This jazz metal folk punk record keeps a close vocal and a quiet drum throughout the night.", snap)
        self.assertTrue(any("4 genres" in n for n in many))
        three = MX.review_caption("This jazz metal folk record keeps a close vocal and a quiet drum throughout the night.", snap)
        self.assertFalse(any("genres" in n for n in three))
        self.assertTrue(any("instrument families" in n for n in MX.review_caption(CROWDED, snap)))
        six = "Electric guitar, piano, upright bass, drums, strings, and trumpet share one small room with a close vocal."
        self.assertFalse(any("instrument families" in n for n in MX.review_caption(six, snap)))
        _cap, warns = BC.check_caption(CROWDED, LYRIC_A, "auto", "low energy", "")
        self.assertFalse(any("instrument families" in w for w in warns))

        found = None
        line = "I wait by the window tonight"
        lyrics = "[Verse 1]\n" + "\n".join([line] * 12)
        for dur in (40, 30, 24, 18, 14):
            trial = basic_snap(lyrics, duration=dur, bpm=120)
            _ins, dn = MX._analysis(trial)
            if dn.known and dn.song_level in ("dense", "very_dense"):
                found = trial
                break
        self.assertIsNotNone(found)
        self.assertTrue(any("spacious or drawn-out" in n for n in MX.review_caption(SPACIOUS, found)))
        easy = basic_snap("[Verse 1]\n" + "\n".join(["I stay tonight"] * 4), duration=180, bpm=96)
        _ins, easy_dn = MX._analysis(easy)
        self.assertEqual(easy_dn.song_level, "comfortable")
        self.assertFalse(any("spacious or drawn-out" in n for n in MX.review_caption(SPACIOUS, easy)))

        whispered = basic_snap("[Chorus - whispered]\nStay with me here tonight\n")
        self.assertTrue(any("cue asks for whispered" in n for n in MX.review_caption(BELTED, whispered)))
        piano = basic_snap("[Chorus - soft piano]\nStay with me here tonight\n")
        self.assertFalse(any("cue asks for" in n for n in MX.review_caption(BELTED, piano)))

    def test_compose_and_analyze_ask_once_and_keep_the_caption(self):
        calls = []

        def ask(system, user):
            calls.append((system, user))
            return _marks(CROWDED, " CAPTION ")

        snap = basic_snap("[Verse 1]\nA short quiet line\n")
        res = BC.compose_basic_caption(snap, ask)
        self.assertEqual(len(calls), 1)
        self.assertEqual(inspect.getsource(BC.compose_basic_caption).count("ask("), 1)
        self.assertEqual(res.caption, CROWDED)
        self.assertTrue(any("instrument families" in w for w in res.warnings))
        self.assertEqual(snap.lyrics, "[Verse 1]\nA short quiet line\n")

        calls.clear()

        def ask_dir(system, user):
            calls.append((system, user))
            return _proposal(CROWDED, BELTED, CLEAN)

        prop = AD.analyze(ask_dir, director_snap("[Verse 1]\nA short quiet line\n"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(inspect.getsource(AD.analyze).count("ask("), 1)
        self.assertEqual(len(prop.directions), 3)
        self.assertEqual(prop.directions[0].caption, CROWDED)
        self.assertIn("organ", prop.directions[0].caption)
        self.assertTrue(any("instrument families" in w for w in prop.directions[0].warnings))
        self.assertTrue(any("belted" in w for w in prop.directions[1].warnings))
        self.assertIn("belting", prop.directions[1].caption)


class ParserAndApplyTests(unittest.TestCase):
    def test_old_parsers_still_own_their_markers(self):
        ok = BC.parse(_marks(CLEAN, " CAPTION "), "[Verse 1]\nA short quiet line\n", "auto", "low energy", "")
        self.assertEqual(ok.caption, CLEAN)
        self.assertFalse(any("belted" in w for w in ok.warnings))
        belt = BC.parse(_marks(BELTED, " CAPTION "), "", "auto", "high energy", "")
        self.assertTrue(any("belted" in w for w in belt.warnings))
        asked = BC.parse(_marks(BELTED, " CAPTION "), "", "auto", "high energy", "belting")
        self.assertFalse(any("belted" in w for w in asked.warnings))
        with self.assertRaises(BC.CaptionError):
            BC.parse(_marks(CLEAN + " " + WINDOW_LINE, " CAPTION "), LYRIC_A, "auto", "low energy", "")
        bpm = BC.parse(_marks(CLEAN + " The pocket is described as 90 bpm.", " CAPTION "), "", "auto", "low energy", "")
        self.assertTrue(any("BPM" in w for w in bpm.warnings))
        self.assertIn("90 bpm", bpm.caption)
        with self.assertRaises(BC.CaptionError):
            BC.parse(_proposal(CLEAN, BELTED, CROWDED), "", "auto", "low energy", "")
        with self.assertRaises(AD.DirectorError):
            AD.parse(_marks(CLEAN, " CAPTION "), LYRIC_A)

    def test_apply_and_restore_do_not_touch_lyrics_or_a_later_edit(self):
        raw = _proposal(CLEAN, BELTED, CROWDED)
        prop = AD.parse(raw, LYRIC_A)
        self.assertEqual(len(prop.directions), 3)
        snap = director_snap(LYRIC_A)
        current = dict(snap.settings)
        plan = AD.plan_apply(snap, prop, prop.directions[0], {"style_prompt", "bpm"}, snap.song_id, snap.sheet_id, LYRIC_A, current)
        self.assertNotIn("lyrics", plan.changes)
        self.assertEqual(plan.changes["style_prompt"], CLEAN)
        self.assertIn("bpm", plan.changes)
        with self.assertRaises(AD.ApplyError):
            AD.plan_apply(snap, prop, prop.directions[0], {"style_prompt"}, "song-b", snap.sheet_id, LYRIC_B, current)
        back, kept = AD.plan_restore({"bpm": 90, "style_prompt": "old"}, {"bpm": 120, "style_prompt": "new cap"}, {"bpm": 100, "style_prompt": "new cap"})
        self.assertEqual(back, {"style_prompt": "old"})
        self.assertEqual(kept, ["bpm"])
        same = AD.settings_fingerprint({"duration": 180, "language": "en", "genre": "Zephyrcore", "bpm": 96}, False)
        other = AD.settings_fingerprint({"duration": 180, "language": "en", "genre": "Other", "bpm": 40}, False)
        self.assertEqual(same, other)
        ref = AD.settings_fingerprint({"duration": 180, "language": "en", "genre": "Zephyrcore"}, True)
        ref2 = AD.settings_fingerprint({"duration": 180, "language": "en", "genre": "Other"}, True)
        self.assertNotEqual(ref, ref2)

    def test_context_module_does_not_read_the_library(self):
        text = Path(MX.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import store", text)
        self.assertNotIn("generation", text)
        self.assertNotIn("music_studio.py", text)
        self.assertEqual(inspect.getsource(BC.check_caption).count("review_caption"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
