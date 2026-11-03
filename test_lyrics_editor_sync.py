"""Decisions for the lyrics CodeEditor. No window, no song library.

The handlers live inside main(), so these tests call the same functions the handlers call:
SelectionBook, the UTF-16 conversion, and editor_sync. A temp USERPROFILE is set first so an
import cannot create a folder in the real Music library.
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

os.environ["USERPROFILE"] = tempfile.mkdtemp(prefix="ms_editor_sync_")
os.environ["APPDATA"] = os.environ["USERPROFILE"]

from music_studio import editor_sync as ES

_spec = importlib.util.spec_from_file_location("music_studio_ui", Path(__file__).resolve().parent / "music_studio.py")
_ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ui)
EditHistory, SelectionBook = _ui.EditHistory, _ui.SelectionBook
idx16, idx_from16, map_index = _ui.idx16, _ui.idx_from16, _ui.map_index


class Offsets(unittest.TestCase):
    def test_roundtrip_thai_english_mixed_emoji_multiline(self):
        samples = [
            "ไทยล้วนทั้งบรรทัด",
            "english only",
            "ไทย ABC ผสม",
            "A🎵B",
            "บรรทัดหนึ่ง\nline two\nบรรทัดสาม 🎵",
            "ก" * 4000 + "🎵" + "ข" * 4000,
        ]
        for text in samples:
            step = 1 if len(text) < 80 else 47
            for i in list(range(0, len(text) + 1, step)) + [len(text)]:
                self.assertEqual(idx_from16(text, idx16(text, i)), i, text[:12])

    def test_emoji_is_two_utf16_units(self):
        text = "A🎵B"
        self.assertEqual(text.index("B"), 2)
        self.assertEqual(idx16(text, 2), 3)
        self.assertEqual(idx_from16(text, 3), 2)
        self.assertEqual(idx_from16(text, 2), 2)  # inside the surrogate pair, the caret lands after it


class Classify(unittest.TestCase):
    def test_caret_at_end_of_a_push_is_arrival(self):
        book = SelectionBook()
        text = "กขค"
        book.push_text(text)
        end = (idx16(text, len(text)), idx16(text, len(text)))
        self.assertEqual(ES.push_event_kind(end, book.arrival_pending, book.arrival_at, book.echoes, "", book.stale), "arrival")

    def test_empty_cause_matching_the_last_echo_stays_an_echo(self):
        book = SelectionBook()
        text = "hello"
        book.push_text(text)
        ev = book.push_selection(text, 1, 4)
        self.assertEqual(ES.push_event_kind(ev, book.arrival_pending, book.arrival_at, book.echoes, "", book.stale), "echo")

    def test_a_user_cause_overrides_a_matching_echo(self):
        book = SelectionBook()
        text = "hello"
        book.push_text(text)
        ev = book.push_selection(text, 1, 4)
        for cause in ("tap", "drag", "keyboard", "doubleTap", "toolbar"):
            self.assertEqual(ES.push_event_kind(ev, book.arrival_pending, book.arrival_at, book.echoes, cause, book.stale), "user", cause)

    def test_a_different_offset_during_a_push_is_the_user(self):
        book = SelectionBook()
        text = "กขคง🎵AB"
        book.push_text(text)
        book.push_selection(text, 1, 3)
        user_ev = (idx16(text, 4), idx16(text, 6))
        self.assertEqual(ES.push_event_kind(user_ev, book.arrival_pending, book.arrival_at, book.echoes, "", book.stale), "user")
        sel = book.adopt_user(user_ev, text)
        self.assertEqual(sel, (4, 6))

    def test_stale_echo_is_consumed_once(self):
        book = SelectionBook()
        text = "hello"
        book.push_text(text)
        ev = book.push_selection(text, 1, 3)
        book.adopt_user((4, 4), text)
        self.assertIn(ev, book.stale)
        self.assertEqual(ES.push_event_kind(ev, book.arrival_pending, book.arrival_at, book.echoes, "", book.stale), "stale_echo")
        book.stale = [x for x in book.stale if x != ev]
        self.assertEqual(ES.push_event_kind(ev, book.arrival_pending, book.arrival_at, book.echoes, "", book.stale), "user")

    def test_push_text_and_reset_drop_stale_offsets(self):
        book = SelectionBook()
        book.stale = [(1, 1)]
        book.push_text("ab")
        self.assertEqual(book.stale, [])
        book.stale = [(2, 2)]
        book.reset()
        self.assertEqual(book.stale, [])
        self.assertFalse(book.arrival_pending)

    def test_adopt_user_maps_a_caret_onto_newer_text(self):
        book = SelectionBook()
        book.confirmed = "abcd"
        book.sent = "Xabcd"
        sel = book.adopt_user((2, 2), "Xabcd")
        self.assertEqual(sel, (3, 3))
        self.assertEqual(map_index("abcd", "Xabcd", 2), 3)

    def test_pick_offsets_prefers_the_event(self):
        class Sel:
            def __init__(self, start, end):
                self.start, self.end = start, end

        ev, src = ES.pick_offsets(Sel(5, 2), Sel(0, 0))
        self.assertEqual((ev, src), ((2, 5), "event_not_control"))
        ev, src = ES.pick_offsets(None, Sel(3, 3))
        self.assertEqual((ev, src), ((3, 3), "control"))
        ev, src = ES.pick_offsets(Sel(1, 1), Sel(1, 1))
        self.assertEqual((ev, src), ((1, 1), "event"))
        self.assertEqual(ES.pick_offsets(None, None), (None, "none"))

    def test_cause_name(self):
        self.assertEqual(ES.cause_name(None), "")
        self.assertEqual(ES.cause_name("keyboard"), "keyboard")

        class Cause:
            value = "drag"

        self.assertEqual(ES.cause_name(Cause()), "drag")


class ChangeAndFocus(unittest.TestCase):
    def test_late_echo_is_not_a_user_edit(self):
        self.assertEqual(ES.change_action("pushed", "newer", "pushed", None), "late_echo")
        self.assertEqual(ES.change_action("pushed", "newer", None, "pushed"), "late_echo")
        self.assertEqual(ES.change_action("newer", "newer", "pushed", "pushed"), "arrival")
        self.assertEqual(ES.change_action("typed", "model", None, None), "user")

    def test_focus_is_skipped_for_chat_and_for_a_user_action(self):
        self.assertEqual(ES.focus_skip_reason(False, False, False, False), "not requested")
        self.assertEqual(ES.focus_skip_reason(True, True, False, False), "chat holds the keyboard")
        self.assertEqual(ES.focus_skip_reason(True, False, True, False), "the user edited or selected during the push")
        self.assertEqual(ES.focus_skip_reason(True, False, False, True), "a newer push or another song")
        self.assertIsNone(ES.focus_skip_reason(True, False, False, False))

    def test_a_sheet_switch_does_not_resend_the_selection(self):
        self.assertFalse(ES.selection_needed(True, False, False, True))
        self.assertTrue(ES.selection_needed(True, True, False, True))
        self.assertTrue(ES.selection_needed(True, False, True, True))
        self.assertTrue(ES.selection_needed(True, False, False, False))
        self.assertFalse(ES.selection_needed(False, True, True, False))


class Race(unittest.TestCase):
    def test_a_user_selection_wins_and_a_later_write_is_illegal(self):
        """The sequence on_lyrics_selection and _push_once use while a push is waiting.

        An echo does not move irev. A different offset does. After that, the push's check
        `irev != irev0` makes another selection or focus write illegal. The late programmatic
        offset is one stale echo and does not become the selection.
        """
        book = SelectionBook()
        text = "กขคง🎵AB"
        irev = 5
        irev0 = irev
        book.push_text(text)
        sent_ev = book.push_selection(text, 1, 2)
        kind = ES.push_event_kind(sent_ev, book.arrival_pending, book.arrival_at, book.echoes, "", book.stale)
        self.assertEqual(kind, "echo")
        self.assertEqual(irev, irev0)

        user_ev = (idx16(text, 4), idx16(text, 6))
        kind = ES.push_event_kind(user_ev, book.arrival_pending, book.arrival_at, book.echoes, "", book.stale)
        self.assertEqual(kind, "user")
        irev += 1
        sel = book.adopt_user(user_ev, text)
        self.assertEqual(sel, (4, 6))
        self.assertNotEqual(irev, irev0)

        kind = ES.push_event_kind(sent_ev, book.arrival_pending, book.arrival_at, book.echoes, "", book.stale)
        self.assertEqual(kind, "stale_echo")
        book.stale = [x for x in book.stale if x != sent_ev]
        self.assertEqual(sel, (4, 6))

    def test_typing_during_a_push_is_a_user_change_and_the_old_text_is_a_late_echo(self):
        model = "hello"
        awaiting = "hello"
        typed = "hello!"
        self.assertEqual(ES.change_action(typed, model, awaiting, None), "user")
        model = typed
        discarded = "hello"
        self.assertEqual(ES.change_action("hello", model, None, discarded), "late_echo")
        self.assertEqual(ES.change_action(model, model, None, discarded), "arrival")


class Undo(unittest.TestCase):
    def test_undo_redo_and_a_burst_is_one_step(self):
        # record() is called with the text that is about to be replaced.
        hist = EditHistory()
        self.assertTrue(hist.record("", (0, 0), burst=False, now=1))
        self.assertTrue(hist.record("a", (1, 1), burst=True, now=10))
        self.assertFalse(hist.record("ab", (2, 2), burst=True, now=10.4))
        self.assertEqual(hist.step_back("abc", (3, 3)), ("a", (1, 1)))
        self.assertEqual(hist.step_forward("a", (1, 1)), ("abc", (3, 3)))

    def test_a_new_edit_drops_redo(self):
        hist = EditHistory()
        hist.record("one", (0, 0), burst=False, now=1)
        hist.record("two", (3, 3), burst=False, now=2)
        self.assertEqual(hist.step_back("three", (5, 5)), ("two", (3, 3)))
        hist.record("two", (3, 3), burst=False, now=4)
        self.assertIsNone(hist.step_forward("two-edited", (4, 4)))
        self.assertEqual(hist.step_back("two-edited", (4, 4)), ("two", (3, 3)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
