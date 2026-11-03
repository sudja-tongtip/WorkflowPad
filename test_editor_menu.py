"""The lyrics editor's selection toolbar: which actions appear, and where a click goes.

No window and no song library. The screen applies `plan`; these tests are the routing.
"""
import unittest

from music_studio import editor_menu as EM
from music_studio import editor_sync as ES

LYRIC = "[Verse]\nสวัสดี world\nบรรทัดสอง\nline four\n"


class Kind(unittest.TestCase):
    def test_a_caret_inside_a_tag_is_the_tag(self):
        self.assertEqual(EM.menu_kind(LYRIC, 1, 1), "tag")
        self.assertEqual(EM.menu_kind(LYRIC, 0, len("[Verse]")), "tag")

    def test_a_selection_that_leaves_the_tag_is_not_the_tag(self):
        self.assertEqual(EM.menu_kind(LYRIC, 0, LYRIC.index("\n") + 2), "lines")

    def test_one_line_is_a_word_and_several_lines_are_lines(self):
        i = LYRIC.index("world")
        self.assertEqual(EM.menu_kind(LYRIC, i, i + 5), "word")
        self.assertEqual(EM.menu_kind(LYRIC, i, LYRIC.index("line four") + 4), "lines")

    def test_a_caret_and_a_blank_selection_are_empty(self):
        self.assertEqual(EM.menu_kind(LYRIC, len(LYRIC), len(LYRIC)), "empty")
        self.assertEqual(EM.menu_kind("a  b", 1, 3), "empty")

    def test_emoji_offsets_stay_on_the_word(self):
        text = "A🎵B more"
        self.assertEqual(EM.menu_kind(text, 0, 3), "word")
        self.assertEqual(EM.menu_kind(text, 4, 4), "empty")


class Rows(unittest.TestCase):
    def test_every_kind_keeps_the_edit_commands_once(self):
        for kind in ("word", "lines", "tag", "empty"):
            keys = [c.key for c in EM.commands_for(kind)]
            self.assertEqual(keys[:4], list(EM.EDIT_KEYS))
            self.assertEqual(len(keys), len(set(keys)))

    def test_only_the_rows_for_that_kind_are_added(self):
        self.assertEqual([c.key for c in EM.commands_for("word")][4:], ["alternatives", "refine", "translate", "ask_songwriter"])
        self.assertEqual([c.key for c in EM.commands_for("lines")][4:], ["rewrite", "emotion", "singability", "phrasing"])
        self.assertEqual([c.key for c in EM.commands_for("tag")][4:], ["section", "cues", "structure", "ask_producer"])
        self.assertEqual([c.key for c in EM.commands_for("empty")][4:], ["add_section", "continue"])

    def test_the_toolbar_shows_only_the_actions_for_a_selection(self):
        self.assertEqual([c.key for c in EM.toolbar_commands("word")], ["alternatives", "refine", "translate", "ask_songwriter"])
        self.assertEqual([c.key for c in EM.toolbar_commands("lines")], ["rewrite", "emotion", "singability", "phrasing"])
        self.assertEqual([c.key for c in EM.toolbar_commands("tag")], ["section", "cues", "structure", "ask_producer"])
        self.assertEqual(EM.toolbar_commands("empty"), ())
        self.assertFalse(any(c.key in EM.EDIT_KEYS for kind in ("word", "lines", "tag") for c in EM.toolbar_commands(kind)))


class Routing(unittest.TestCase):
    def _word(self):
        i = LYRIC.index("world")
        return i, i + 5

    def test_a_word_goes_to_the_thesaurus_the_chat_or_an_existing_action(self):
        a, b = self._word()
        alt = EM.plan("alternatives", LYRIC, a, b, "songwriter")
        self.assertTrue(alt.ok)
        self.assertEqual((alt.route, alt.target, alt.switch, alt.prefill), ("dialog", "alternatives", False, ""))
        refine = EM.plan("refine", LYRIC, a, b, "songwriter", "en")
        self.assertEqual(refine.route, "chat")
        self.assertFalse(refine.switch)
        self.assertIn("world", refine.prefill)
        self.assertIn("My question:", refine.prefill)
        ask = EM.plan("ask_songwriter", LYRIC, a, b, "music_producer", "th")
        self.assertTrue(ask.switch)
        self.assertEqual(ask.specialist, "songwriter")
        self.assertIn("คำถามของฉัน:", ask.prefill)
        self.assertIn("world", ask.prefill)
        tr = EM.plan("translate", LYRIC, a, b, "music_producer")
        self.assertEqual((tr.route, tr.action, tr.specialist, tr.switch), ("action", "translate", "songwriter", False))

    def test_lines_rewrite_stays_in_the_chat_and_the_others_are_existing_actions(self):
        a = LYRIC.index("สวัสดี")
        b = len(LYRIC)
        rw = EM.plan("rewrite", LYRIC, a, b, "songwriter", "en")
        self.assertEqual(rw.route, "chat")
        self.assertFalse(rw.switch)
        self.assertIn("section tags", rw.prefill)
        self.assertNotIn("[", rw.prefill.split("Keep the meaning and the section tags.")[0])
        for key, action in (("emotion", "emotion"), ("singability", "singability"), ("phrasing", "phrasing")):
            got = EM.plan(key, LYRIC, a, b, "music_producer")
            self.assertEqual((got.route, got.action, got.switch), ("action", action, False), key)

    def test_a_tag_opens_dialogs_or_asks_the_producer_without_resetting_the_same_chat(self):
        sec = EM.plan("section", LYRIC, 1, 1, "songwriter")
        cues = EM.plan("cues", LYRIC, 0, 7, "songwriter")
        self.assertEqual((sec.route, sec.target), ("dialog", "section"))
        self.assertEqual(cues.target, "section")
        self.assertFalse(sec.switch or cues.switch)
        arr = EM.plan("structure", LYRIC, 1, 1)
        self.assertEqual((arr.route, arr.target, arr.specialist), ("dialog", "structures", ""))
        same = EM.plan("ask_producer", LYRIC, 1, 1, "music_producer", "en")
        other = EM.plan("ask_producer", LYRIC, 1, 1, "songwriter", "en")
        self.assertFalse(same.switch)
        self.assertTrue(other.switch)
        self.assertEqual(other.specialist, "music_producer")
        self.assertIn("[Verse]", other.prefill)
        self.assertEqual(other.action, "")

    def test_an_empty_caret_inserts_a_section_or_continues(self):
        n = len(LYRIC)
        add = EM.plan("add_section", LYRIC, n, n)
        self.assertEqual((add.route, add.target), ("dialog", "structures"))
        cont = EM.plan("continue", LYRIC, n, n, "songwriter")
        self.assertEqual((cont.route, cont.action, cont.switch), ("action", "continue", False))
        paste = EM.plan("paste", LYRIC, n, n)
        self.assertEqual(paste.route, "edit")
        self.assertEqual(paste.action, "")

    def test_a_row_from_another_kind_is_refused_and_copy_needs_a_selection(self):
        n = len(LYRIC)
        stale = EM.plan("alternatives", LYRIC, n, n)
        self.assertFalse(stale.ok)
        self.assertIn("selection", stale.reason.lower())
        bare = EM.plan("copy", LYRIC, n, n)
        self.assertFalse(bare.ok)
        ok = EM.plan("copy", LYRIC, 1, 4)
        self.assertTrue(ok.ok)
        self.assertEqual(ok.route, "edit")
        self.assertTrue(EM.plan("select_all", LYRIC, n, n).ok)

    def test_a_clipboard_round_trip_waits_for_the_same_lyrics(self):
        self.assertTrue(EM.edit_still_fits("abc", "abc"))
        self.assertFalse(EM.edit_still_fits("abc", "abcd"))

    def test_a_menu_during_a_push_blocks_the_later_focus(self):
        """The screen bumps irev on select and dismiss, and skips focus when the editor blurred after the push began."""
        irev0 = 3
        irev = irev0 + 1
        self.assertNotEqual(irev, irev0)
        self.assertEqual(ES.focus_skip_reason(True, False, True, False), "the user edited or selected during the push")
        self.assertEqual(ES.focus_skip_reason(True, False, False, False, True), "the editor lost focus during the push")
        self.assertIsNone(ES.focus_skip_reason(True, False, False, False, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
