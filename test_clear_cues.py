"""Clear Cues: one section loses its cues, the name and the other lines stay. No song library, no LLM, no editor engine.

Run: python test_clear_cues.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
from pathlib import Path

import flet as ft

from music_studio import i18n as I18N
from music_studio import section_cues as SC
from music_studio import tag_parser as TP

OUT = Path(__file__).resolve().parent / "_sel_preview_shots"
ROOT = Path(__file__).resolve().parent
TAG = "[Chorus - Powerful Belting, Harmonies, Build Energy]"
SONG = "[Verse 1]\nhello\n\n" + TAG + "\n[Hold] ooo\nhook line\nฉันรอ\n\n[Bridge - whispered]\nsoft\n[Chorus - powerful]\nhook again\n"
SONG_B = "[Chorus - soft bells]\nชีตสอง\n"
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    line = ("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else "")
    print(line, flush=True)
    if not ok:
        FAILS.append(line)


def edit_history():
    src = (ROOT / "music_studio.py").read_text(encoding="utf-8")
    start = src.index("class EditHistory:")
    end = src.index("\nlog = logging.getLogger", start)
    ns = {"time": time}
    exec(src[start:end], ns)
    return ns["EditHistory"]


def ref_of(text: str, tag: str) -> TP.TagRef:
    i = text.index(tag)
    return TP.TagRef(i, i + len(tag), tag)


def logic() -> None:
    raw = json.loads((ROOT / "music_studio" / "locales" / "th.json").read_text(encoding="utf-8"))
    check("locale file still parses", "Clear Cues" in raw["strings"] and raw["strings"]["Clear Cues"] == "ล้างคิว")
    I18N.set_lang("th")
    check("Thai label is ล้างคิว", I18N.tr("Clear Cues") == "ล้างคิว")
    check("Thai tooltip says the name and the lyrics stay", "ชื่อท่อน" in I18N.tr("Remove every cue from this section. The section name and the lyrics stay.") and "เนื้อเพลง" in I18N.tr("Remove every cue from this section. The section name and the lyrics stay."))
    I18N.set_lang("en")
    check("English label stays Clear Cues", I18N.tr("Clear Cues") == "Clear Cues")

    screen = (ROOT / "music_studio.py").read_text(encoding="utf-8")
    check("Section Options builds the Clear Cues button", 'TextButton("Clear Cues"' in screen and "clear_row" in screen and 'key="clear_cues"' in screen)
    check("the button writes through the existing tag apply", "commit_cleared_cues" in screen and "SC.map_selection" in screen and "SC.target_matches" in screen and "refocus=False" in screen)

    bare = SC.bare_section_tag(TAG)
    check("many cues, including an unknown one, become the bare name", bare == "[Chorus]", bare or "")
    one = SC.clear_one_section(SONG, ref_of(SONG, TAG))
    assert one is not None
    cleared, new_tag = one
    check("the Chorus tag is now [Chorus]", new_tag == "[Chorus]" and cleared.count("\n[Chorus]\n") == 1, cleared)
    check("the section was not duplicated", cleared.count("[Chorus") == 2 and "[Chorus - Powerful" not in cleared)
    check("the second Chorus keeps the cue it already had", "[Chorus - powerful]" in cleared and "hook again" in cleared)
    check("Verse, Bridge, Hold and the sung lines stay", "[Verse 1]\nhello" in cleared and "[Bridge - whispered]" in cleared and "[Hold] ooo" in cleared and "hook line" in cleared and "ฉันรอ" in cleared and "soft" in cleared)

    single = "[Verse 1 - whispered]\nline\n"
    got = SC.clear_one_section(single, ref_of(single, "[Verse 1 - whispered]"))
    check("one standard cue keeps the numbered name", got == ("[Verse 1]\nline\n", "[Verse 1]"), repr(got))
    custom = "[Breakdown - soft bells at dawn]\nstill\n"
    got = SC.clear_one_section(custom, ref_of(custom, "[Breakdown - soft bells at dawn]"))
    check("a custom cue is removed", got == ("[Breakdown]\nstill\n", "[Breakdown]"), repr(got))
    known = "[Chorus - powerful belting, harmonies and building energy]\nhook\n"
    got = SC.clear_one_section(known, ref_of(known, "[Chorus - powerful belting, harmonies and building energy]"))
    check("several library cues are removed together", got == ("[Chorus]\nhook\n", "[Chorus]"), repr(got))

    plain = "[Chorus]\nhook\n"
    check("a section with no cue is left alone", SC.clear_one_section(plain, ref_of(plain, "[Chorus]")) is None and SC.clear_enabled("[Chorus]", "[Chorus]") is False)
    check("a bare preview disables the button even if the draft had been typed", SC.clear_enabled("[Chorus]", "[Chorus]") is False)
    check("a draft that adds a cue enables the button", SC.clear_enabled("[Chorus - soft]", "[Chorus]") is True)
    check("the saved tag enables the button before the preview refreshes", SC.clear_enabled("[Chorus]", TAG) is True)
    check("a standalone cue is not a section to clear", SC.bare_section_tag("[whispered]") is None and SC.bare_section_tag("[Hold]") is None)
    check("a custom tag is not cleared", SC.bare_section_tag("[My Tag - hello]") is None and SC.clear_one_section("[My Tag - hello]\n", ref_of("[My Tag - hello]\n", "[My Tag - hello]")) is None)

    moved = SONG.replace(TAG, "[Chorus - other]", 1)
    try:
        SC.clear_one_section(moved, ref_of(SONG, TAG))
        check("a stale tag is refused", False)
    except TP.StaleTarget:
        check("a stale tag is refused", True)
    check("the other song is a different string", SONG_B == "[Chorus - soft bells]\nชีตสอง\n" and "Build Energy" not in SONG_B)

    tg = {"song": "a", "sheet": "s1", "epoch": 3}
    check("the same song and sheet still match", SC.target_matches(tg, "a", "s1", 3))
    check("another song does not match", SC.target_matches(tg, "b", "s1", 3) is False)
    check("another sheet does not match", SC.target_matches(tg, "a", "s2", 3) is False)
    check("a newer editor epoch does not match", SC.target_matches(tg, "a", "s1", 4) is False)

    ref = ref_of(SONG, TAG)
    caret = SONG.index("hook line")
    mapped = SC.map_selection((caret, caret), ref.start, ref.end, ref.start + len("[Chorus]"))
    check("a caret on the sung line stays on that line", cleared[mapped[0]:mapped[0] + 4] == "hook", repr(mapped))
    inside = SONG.index("Powerful")
    clamped = SC.map_selection((inside, inside), ref.start, ref.end, ref.start + len("[Chorus]"))
    check("a caret in the cue text stops at the end of the name", clamped == (ref.start + len("[Chorus]"), ref.start + len("[Chorus]")))
    whole = SC.map_selection((ref.start, ref.end), ref.start, ref.end, ref.start + len("[Chorus]"))
    check("a selection of the whole tag becomes the whole new tag", whole == (ref.start, ref.start + len("[Chorus]")))

    Hist = edit_history()
    hist = Hist()
    sel = (caret, caret + 4)
    hist.record(SONG, sel, False)
    new_sel = SC.map_selection(sel, ref.start, ref.end, ref.start + len(new_tag))
    back = hist.step_back(cleared, new_sel)
    check("undo restores the cues and the selection", back == (SONG, sel), repr(back[1] if back else None))
    fwd = hist.step_forward(SONG, sel)
    check("redo clears the cues again", fwd == (cleared, new_sel))
    hist.record(cleared, new_sel, False)
    check("a later edit drops the redo of the clear", hist.step_forward(SONG, sel) is None)


def find(root, key: str):
    if getattr(root, "key", None) == key:
        return root
    for child in list(getattr(root, "controls", None) or []):
        got = find(child, key)
        if got is not None:
            return got
    content = getattr(root, "content", None)
    if content is not None and content is not root:
        return find(content, key)
    return None


def main(page: ft.Page):
    page.title = "Clear Cues"
    page.bgcolor = "#F8FAFC"
    page.padding = 16
    page.window.width = 980
    page.window.height = 720
    I18N.install(ft)
    I18N.set_lang("th")

    ref = ref_of(SONG, TAG)
    state = {"song": "a", "sheet": "s1", "epoch": 1, "lyrics": SONG, "sel": (SONG.index("hook line"), SONG.index("hook line"))}
    tg = {"song": "a", "sheet": "s1", "epoch": 1, "ref": ref}
    songs = {"a": SONG, "b": SONG_B}
    Hist = edit_history()
    hist = Hist()
    focus = {"lyrics": 0}

    preview = ft.TextField(label="Tag preview", value=TAG, read_only=True, text_size=14, bgcolor="#FFFFFF", color="#0F172A")
    lyrics = ft.TextField(label="Lyrics", value=SONG, multiline=True, min_lines=8, max_lines=8, text_size=13, bgcolor="#FFFFFF", color="#0F172A")
    lyrics.on_focus = lambda e: focus.__setitem__("lyrics", focus["lyrics"] + 1)
    other = ft.TextField(value="", hint_text="Ask the AI...", bgcolor="#FFFFFF", color="#0F172A")
    status = ft.Text("", size=12, color="#334155")
    bare_note = ft.Text("", size=12, color="#64748B")

    def paint_button(enabled: bool):
        clear_btn.disabled = not enabled

    clear_btn = ft.TextButton("Clear Cues", icon=ft.Icons.CLEAR_ALL_ROUNDED, icon_color="#64748B", height=28, key="clear_cues",
                               style=ft.ButtonStyle(color="#334155", text_style=ft.TextStyle(size=12)),
                               tooltip="Remove every cue from this section. The section name and the lyrics stay.",
                               disabled=not SC.clear_enabled(TAG, TAG))

    def on_clear(e=None):
        if clear_btn.disabled:
            return
        if not SC.target_matches(tg, state["song"], state["sheet"], state["epoch"]):
            status.value = "refused"
            return
        bare = SC.bare_section_tag(tg["ref"].original)
        if bare is None:
            return
        if bare != tg["ref"].original:
            try:
                got = SC.clear_one_section(state["lyrics"], tg["ref"])
            except TP.StaleTarget:
                status.value = "stale"
                return
            if got is None:
                return
            new_text, new_tag = got
            hist.record(state["lyrics"], state["sel"], False)
            new_sel = SC.map_selection(state["sel"], tg["ref"].start, tg["ref"].end, tg["ref"].start + len(new_tag))
            state["lyrics"], state["sel"] = new_text, new_sel
            songs[state["song"]] = new_text
            tg["ref"] = TP.TagRef(tg["ref"].start, tg["ref"].start + len(new_tag), new_tag)
        preview.value = tg["ref"].original
        lyrics.value = state["lyrics"]
        lyrics.selection = ft.TextSelection(base_offset=state["sel"][0], extent_offset=state["sel"][1])
        paint_button(SC.clear_enabled(preview.value, tg["ref"].original))
        status.value = "cleared"

    clear_btn.on_click = on_clear
    no_cue = ft.TextButton("Clear Cues", icon=ft.Icons.CLEAR_ALL_ROUNDED, height=28, key="clear_cues_bare", disabled=not SC.clear_enabled("[Bridge]", "[Bridge]"))

    def on_undo(e=None):
        step = hist.step_back(state["lyrics"], state["sel"])
        if not step:
            return
        text, sel = step
        state["lyrics"], state["sel"] = text, sel
        songs["a"] = text
        tag = TAG if TAG in text else "[Chorus]"
        i = text.index(tag)
        tg["ref"] = TP.TagRef(i, i + len(tag), tag)
        preview.value = tag
        lyrics.value = text
        paint_button(SC.clear_enabled(tag, tag))
        status.value = "undo"

    def on_redo(e=None):
        step = hist.step_forward(state["lyrics"], state["sel"])
        if not step:
            return
        text, sel = step
        state["lyrics"], state["sel"] = text, sel
        songs["a"] = text
        tag = "[Chorus]" if "\n[Chorus]\n" in text else TAG
        i = text.index(tag)
        tg["ref"] = TP.TagRef(i, i + len(tag), tag)
        preview.value = tag
        lyrics.value = text
        paint_button(SC.clear_enabled(tag, tag))
        status.value = "redo"

    undo_btn = ft.OutlinedButton("Undo", on_click=on_undo, height=32)
    redo_btn = ft.OutlinedButton("Redo", on_click=on_redo, height=32)
    chips = ft.Row([
        ft.Container(ft.Text(name, size=12, color="#FFFFFF"), bgcolor="#2563EB", border_radius=12, padding=8)
        for name in ("Powerful Belting", "Harmonies", "Build Energy")
    ], spacing=6)
    panel = ft.Container(ft.Column([
        ft.Text("Section Options", size=18, weight=ft.FontWeight.BOLD, color="#0F172A"),
        ft.Text("Chorus  ·  Line 5  ·  section", size=12, color="#64748B"),
        ft.Row([ft.Text("Selected cues", size=12, color="#64748B"), ft.Container(expand=True), clear_btn], spacing=8),
        chips,
        preview,
        ft.Row([undo_btn, redo_btn, no_cue], spacing=8),
        bare_note,
        lyrics,
        other,
        status,
    ], spacing=8), width=560, bgcolor="#FFFFFF", border_radius=12, padding=14, border=ft.Border.all(1, "#E2E8F0"))
    before = ft.Text(SONG, size=12, color="#334155", selectable=True, font_family="Consolas")
    shot = ft.Screenshot(ft.Row([
        panel,
        ft.Container(ft.Column([
            ft.Text("Before", size=12, color="#64748B"),
            before,
            ft.Text("Song B stays on its own sheet", size=12, color="#64748B"),
            ft.Text(SONG_B, size=12, color="#334155", font_family="Consolas"),
        ], spacing=6), expand=True),
    ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.START))
    page.add(shot)

    async def go():
        try:
            await asyncio.sleep(0.4)
            page.update()
            label = clear_btn.content if isinstance(clear_btn.content, str) else clear_btn.text
            tip = clear_btn.tooltip if isinstance(clear_btn.tooltip, str) else getattr(clear_btn.tooltip, "message", "")
            check("UI button reads ล้างคิว", label == "ล้างคิว", repr(label))
            check("UI tooltip is Thai", "ชื่อท่อน" in str(tip), repr(tip))
            check("UI button starts enabled and the bare section stays disabled", clear_btn.disabled is False and no_cue.disabled is True)
            await other.focus()
            focus["lyrics"] = 0
            clear_btn.on_click(None)
            page.update()
            check("UI one click leaves [Chorus] and the other sections", lyrics.value.count("\n[Chorus]\n") == 1 and "[Chorus - powerful]" in lyrics.value and "[Bridge - whispered]" in lyrics.value and "[Hold] ooo" in lyrics.value and "ฉันรอ" in lyrics.value and "hello" in lyrics.value, lyrics.value)
            check("UI preview and the button update together", preview.value == "[Chorus]" and clear_btn.disabled is True, preview.value)
            check("UI caret stays on the sung line and the lyrics field was not focused", state["sel"][0] == lyrics.value.index("hook line") and focus["lyrics"] == 0, repr(state["sel"]))
            check("UI song B is untouched", songs["b"] == SONG_B)
            status.value = "ล้างคิวแล้ว  ·  [Chorus]  ·  Bridge ยังมี whispered"
            bare_note.value = "ท่อนที่ไม่มีคิว: ปุ่มปิดอยู่"
            page.update()
            OUT.mkdir(parents=True, exist_ok=True)
            png = await shot.capture(pixel_ratio=1.0, delay=ft.Duration(milliseconds=250))
            (OUT / "09_clear_cues.png").write_bytes(png)
            print("shot  09_clear_cues", flush=True)
            undo_btn.on_click(None)
            page.update()
            check("UI undo brings the cues back", TAG in lyrics.value and preview.value == TAG and clear_btn.disabled is False, preview.value)
            redo_btn.on_click(None)
            page.update()
            check("UI redo clears them again", "\n[Chorus]\n" in lyrics.value and preview.value == "[Chorus]" and "[Hold] ooo" in lyrics.value and clear_btn.disabled is True)
            state["epoch"] = 2
            before_b = songs["b"]
            clear_btn.disabled = False
            clear_btn.on_click(None)
            check("UI a switched sheet is refused", status.value == "refused" and songs["b"] == before_b and "soft bells" in songs["b"])
        except Exception:
            FAILS.append(traceback.format_exc())
            print(traceback.format_exc(), flush=True)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "result_clear_cues.txt").write_text("\n".join(FAILS) if FAILS else "ok", encoding="utf-8")
        await page.window.destroy()

    page.run_task(go)


if __name__ == "__main__":
    logic()
    if FAILS:
        print(f"\n{len(FAILS)} logic failure(s); the window was not opened.", flush=True)
        sys.exit(1)
    ft.run(main, port=0, view=ft.AppView.FLET_APP)
    text = (OUT / "result_clear_cues.txt").read_text(encoding="utf-8") if (OUT / "result_clear_cues.txt").exists() else "no result"
    if text != "ok":
        print(text, flush=True)
        sys.exit(1)
    print("ALL PASS", flush=True)
