"""Selected-lyrics bar: what the next chat question attaches, and how the bar above the message box looks.

Nothing here reads or writes the music library, a song file, or an LLM. The prompt checks call the same
builders ask_ai calls. The window is the real bar control at the chat column's narrow width.

Run: python test_chat_selection_preview.py
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

import flet as ft
from flet_code_editor import CodeEditor, CodeLanguage

from music_studio import chat_selection as CS
from music_studio import i18n as I18N
from music_studio import llm_chat as LC
from music_studio import specialist_engine as SE

OUT = Path(__file__).resolve().parent / "_sel_preview_shots"
LYRICS = "[Verse 1]\nฉันยังคงรอเธออยู่ตรงนี้\nในวันที่ฝนตกทั้งคืน\nThe ending of the song.\n"
QUESTION = "ทำไมบรรทัดนี้ถึงซ้ำ"
META = {"genre": "pop", "mood": "warm", "language": "th", "bpm": 80, "energy": "mid", "vocal": "female"}
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    line = ("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else "")
    print(line, flush=True)
    if not ok:
        FAILS.append(line)


def u16(text: str, i: int) -> int:
    return len(text[:i].encode("utf-16-le")) // 2


def py(text: str, o: int) -> int:
    acc = 0
    for i, ch in enumerate(text):
        if acc >= o:
            return i
        acc += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def line_span(text: str, line_no: int) -> tuple[int, int]:
    """1-based line, without the newline that ends it. The next line's start is the end when it is the last line."""
    lines = text.split("\n")
    start = 0
    for i, line in enumerate(lines, 1):
        end = start + len(line)
        if i == line_no:
            return start, end
        start = end + 1
    raise AssertionError(f"no line {line_no}")


def prompt_once(kind: str, lyrics: str, selected: str, where: str, lines) -> None:
    """The question stays the question. The slice is attached once, for the specialist that is open."""
    if kind == "producer":
        msg = SE.architect_user_message("เพลงทดสอบ", META, lyrics, QUESTION, lines)
        check("producer line hint once", msg.count("THE USER HAS LINES") == (1 if lines else 0), msg[msg.find("THE USER HAS"):msg.find("THE USER HAS") + 80] if lines else "no hint")
        tail = msg.split("USER REQUEST:\n", 1)[1]
    else:
        msg = LC.build_user_message("เพลงทดสอบ", META, lyrics, selected, QUESTION, [], where)
        blocks = msg.count("SELECTED TEXT (")
        check("songwriter SELECTED TEXT once", blocks == (1 if selected else 0), f"count={blocks}")
        if selected:
            block = msg.split("SELECTED TEXT (", 1)[1].split("USER REQUEST:", 1)[0]
            check("slice is its own block", block.count(selected) == 1 and "“" not in msg)
        tail = msg.split("USER REQUEST:\n", 1)[1]
    check(f"{kind} question is not rewritten", tail == QUESTION, repr(tail[:80]))


def logic() -> None:
    I18N.install(ft)
    I18N.set_lang("th")
    word = LYRICS.index("รอเธอ")
    word_sel = (word, word + len("รอเธอ"))
    line = line_span(LYRICS, 2)
    multi_a = line_span(LYRICS, 2)[0]
    multi_b = line_span(LYRICS, 3)[1]
    multi = (multi_a, multi_b)

    has, selected = CS.selection_for_chat("s1", "sh1", LYRICS, word_sel, None)
    check("word slice", has and selected == "รอเธอ", repr(selected))
    has, selected = CS.selection_for_chat("s1", "sh1", LYRICS, line, None)
    check("line slice", has and selected == "ฉันยังคงรอเธออยู่ตรงนี้", repr(selected))
    has, selected = CS.selection_for_chat("s1", "sh1", LYRICS, multi, None)
    check("multiline slice", has and selected == "ฉันยังคงรอเธออยู่ตรงนี้\nในวันที่ฝนตกทั้งคืน", repr(selected))
    check("caret attaches nothing", CS.selection_for_chat("s1", "sh1", LYRICS, (line[0], line[0]), None) == (False, ""))
    check("range past the end attaches nothing", CS.selection_for_chat("s1", "sh1", LYRICS, (0, 10_000), None) == (False, ""))

    dismissed = ("s1", "sh1", word_sel[0], word_sel[1], "รอเธอ")
    check("× attaches nothing", CS.selection_for_chat("s1", "sh1", LYRICS, word_sel, dismissed) == (False, ""))
    other = "คนละเพลง ไม่เอารอเธอไป"
    has, selected = CS.selection_for_chat("s2", "sh1", other, (0, 4), dismissed)
    check("other song is not the dismissed slice", has and selected == other[:4] and selected != "รอเธอ", repr(selected))
    sheet = "[Chorus]\nชีตสองคนละแผ่น\n"
    has, selected = CS.selection_for_chat("s1", "sh2", sheet, (0, 9), dismissed)
    check("other sheet is not the dismissed slice", has and selected == sheet[:9] and "รอเธอ" not in selected, repr(selected))
    check("same song after a new range attaches", CS.selection_for_chat("s1", "sh1", LYRICS, line, dismissed)[1] == "ฉันยังคงรอเธออยู่ตรงนี้")

    prompt_once("songwriter", LYRICS, "รอเธอ", "line 2, inside [Verse 1]", None)
    prompt_once("songwriter", LYRICS, "", "", None)
    prompt_once("producer", LYRICS, "", "", (2, 2))
    prompt_once("producer", LYRICS, "", "", None)

    tip = ft.Text("x")
    CS.set_user_tooltip(tip, "The ending of the song.")
    check("lyrics tooltip is not translated", tip.tooltip == "The ending of the song.", repr(tip.tooltip))
    check("translator does not keep the lyrics", "tooltip" not in (tip.__dict__.get("_i18n") or {}))

    bar, apply = CS.make_chat_selection_bar(lambda e: None, accent_soft="#173766", text1="#E7EEFA", text2="#98A8C4", border="#3A6294")
    title = find(bar, "chat_sel_title")
    clear = find(bar, "chat_sel_clear")
    check("Thai title", title.value == "เนื้อเพลงที่เลือก", repr(title.value))
    check("Thai × tip", clear.tooltip == "ไม่ส่งข้อความที่เลือกนี้ไปกับคำถามถัดไป เนื้อเพลงยังอยู่", repr(clear.tooltip))
    check("hidden until there is a slice", bar.visible is False)
    apply("The ending of the song.")
    body = find(bar, "chat_sel_body")
    check("preview is two lines at most", body.max_lines == 2 and body.overflow == ft.TextOverflow.ELLIPSIS)
    check("preview quotes the slice", body.spans[0].text == "“The ending of the song.”", repr(body.spans[0].text))
    check("tooltip is the whole slice", body.tooltip == "The ending of the song.", repr(body.tooltip))
    check("bar shows", bar.visible is True)
    apply("")
    check("× hides the bar", bar.visible is False and body.spans[0].text == "")


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
    page.title = "Selected lyrics bar"
    page.bgcolor = "#0A1220"
    page.padding = 12
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width = 640
    page.window.height = 980
    I18N.install(ft)
    I18N.set_lang("th")

    state = {"song": "s1", "sheet": "sh1", "lyrics": LYRICS, "sel": line_span(LYRICS, 2), "dismissed": None}
    focus_log: list[str] = []
    editor = ft.TextField(value=LYRICS, multiline=True, min_lines=4, max_lines=4, text_size=12, read_only=False,
                          bgcolor="#F6F8FC", color="#1A2233", border_color="#223352",
                          on_focus=lambda e: focus_log.append("editor"), on_blur=lambda e: focus_log.append("editor-blur"))
    code = CodeEditor(value=LYRICS, language=CodeLanguage.PLAINTEXT, autocomplete=False, height=70)
    chat = ft.TextField(value="", hint_text="Ask the AI to write or change lyrics...", multiline=True, min_lines=1, max_lines=3,
                        text_size=12, bgcolor="#0C1628", color="#E7EEFA", border_color="#223352",
                        on_focus=lambda e: focus_log.append("chat"), on_blur=lambda e: focus_log.append("chat-blur"))

    def on_clear(e):
        text = state["lyrics"]
        a, b = state["sel"]
        if 0 <= a < b <= len(text):
            state["dismissed"] = (state["song"], state["sheet"], a, b, text[a:b])
        show()

    bar, apply = CS.make_chat_selection_bar(on_clear, accent_soft="#173766", text1="#E7EEFA", text2="#98A8C4", border="#3A6294")

    def show():
        _has, text = CS.selection_for_chat(state["song"], state["sheet"], state["lyrics"], state["sel"], state["dismissed"])
        apply(text)
        return text

    word = LYRICS.index("รอเธอ")
    state["sel"] = (word, word + len("รอเธอ"))
    show()
    before_n = state["sel"][1] - state["sel"][0]
    before = ft.Column([
        ft.Text("Before", size=11, color="#7F90B0"),
        ft.Text(f"Selected: {before_n} chars (the AI changes only these)", size=11, color="#7F90B0"),
        ft.TextField(hint_text="Ask the AI to write or change lyrics...", multiline=True, min_lines=1, max_lines=3, text_size=12,
                     bgcolor="#0C1628", color="#E7EEFA", border_color="#223352"),
    ], spacing=6, tight=True)
    live = ft.Column([
        ft.Text("After", size=11, color="#7F90B0"),
        bar, chat,
    ], spacing=4, tight=True)
    narrow = ft.Container(width=240, bgcolor="#101B30", padding=8, border_radius=12, content=ft.Column([before, live, ft.Container(code, height=70), editor], spacing=10, tight=True))
    shot = ft.Screenshot(narrow)
    page.add(ft.Row([shot], scroll=ft.ScrollMode.AUTO))

    async def go():
        try:
            await asyncio.sleep(0.6)
            attached = show()
            page.update()
            await grab("01_word")
            check("bar shows the word", attached == "รอเธอ" and bar.visible is True, repr(attached))

            code.selection = ft.TextSelection(base_offset=u16(LYRICS, word), extent_offset=u16(LYRICS, word + len("รอเธอ")))
            code.update()
            await chat.focus()
            await asyncio.sleep(0.4)
            chat.value = QUESTION
            chat.update()
            got = code.selection
            py_sel = (py(LYRICS, got.base_offset), py(LYRICS, got.extent_offset)) if got else None
            check("chat focus keeps the editor range", py_sel == (word, word + len("รอเธอ")), repr(py_sel))
            check("typing in chat does not write the lyrics", editor.value == LYRICS and code.value == LYRICS and chat.value == QUESTION)
            check("bar stays while the chat has the question", bar.visible is True and find(bar, "chat_sel_body").spans[0].text.startswith("“รอเธอ"))
            await grab("02_chat_keeps_selection")

            lyrics_before = state["lyrics"]
            sel_before = tuple(state["sel"])
            n_focus = len(focus_log)
            find(bar, "chat_sel_clear").on_click(None)
            check("× leaves the lyrics and the caret range", state["lyrics"] == lyrics_before and state["sel"] == sel_before)
            check("× leaves the question", chat.value == QUESTION)
            check("× does not move focus", focus_log[n_focus:] == [], repr(focus_log[n_focus:]))
            check("× attaches nothing", show() == "" and bar.visible is False)
            msg = LC.build_user_message("เพลงทดสอบ", META, state["lyrics"], "", QUESTION, [], "")
            check("dismissed question has no SELECTED TEXT", "SELECTED TEXT (" not in msg and msg.split("USER REQUEST:\n", 1)[1] == QUESTION)
            await grab("03_cleared")

            state["song"], state["sheet"] = "s1", "sh2"
            state["lyrics"] = "[Chorus]\nชีตสองคนละแผ่น\n"
            state["sel"] = (0, len("[Chorus]"))
            state["dismissed"] = ("s1", "sh1", word, word + len("รอเธอ"), "รอเธอ")
            editor.value = state["lyrics"]
            attached = show()
            editor.update()
            page.update()
            check("other sheet shows its own slice", attached == "[Chorus]" and "รอเธอ" not in (find(bar, "chat_sel_body").spans[0].text), repr(attached))
            await grab("04_other_sheet")

            long = "ฉันยังคงรอเธออยู่ตรงนี้ในวันที่ฝนตกทั้งคืนและไม่มีที่ว่างให้ขึ้นบรรทัด " * 4
            long += "\n" + "\n".join(f"บรรทัดที่ {i} ยังยาวอยู่ตรงนี้เหมือนเดิม" for i in range(1, 8))
            state["song"], state["sheet"], state["dismissed"] = "s3", "sh1", None
            state["lyrics"], state["sel"] = long, (0, len(long))
            attached = show()
            page.update()
            body = find(bar, "chat_sel_body")
            check("long slice is attached once", CS.selection_for_chat("s3", "sh1", long, (0, len(long)), None)[1] == long)
            check("long preview keeps two lines and the full tooltip", body.max_lines == 2 and body.tooltip == long and bar.visible is True)
            prompt_once("songwriter", long, long, "lines 1-8", None)
            await grab("05_long_narrow")

            await editor.focus()
            await asyncio.sleep(0.3)
            editor.value = state["lyrics"] + "x"
            editor.update()
            check("typing in the editor leaves the question", chat.value == QUESTION)
            check("editor focus was reached", "editor" in focus_log, repr(focus_log))
        except Exception:
            FAILS.append(traceback.format_exc())
            print(traceback.format_exc(), flush=True)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "result.txt").write_text("\n".join(FAILS) if FAILS else "ok", encoding="utf-8")
        await page.window.destroy()

    async def grab(name: str):
        OUT.mkdir(parents=True, exist_ok=True)
        page.update()
        png = await shot.capture(pixel_ratio=1.0, delay=ft.Duration(milliseconds=250))
        (OUT / f"{name}.png").write_bytes(png)
        print(f"shot  {name}", flush=True)

    page.run_task(go)


if __name__ == "__main__":
    logic()
    if FAILS:
        print(f"\n{len(FAILS)} logic failure(s); the window was not opened.", flush=True)
        sys.exit(1)
    ft.run(main, port=0, view=ft.AppView.FLET_APP)
    text = (OUT / "result.txt").read_text(encoding="utf-8") if (OUT / "result.txt").exists() else "no result"
    if text != "ok":
        print(text, flush=True)
        sys.exit(1)
    print("ALL PASS", flush=True)
