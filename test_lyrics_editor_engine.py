"""Prototype: compare the current Flet TextField with flet-code-editor's CodeEditor as the single-field lyrics editor.

Run:  python test_lyrics_editor_engine.py tf      (TextField inside a scrolling Column, like Music Studio today; port 8561)
      python test_lyrics_editor_engine.py ce      (CodeEditor, plain text, no autocomplete; port 8562)
Uses only generated test lyrics and the unchanged music_studio.lyrics_tools; nothing is read from or written to the user's library, and nothing touches ComfyUI.
Every engine is driven through the same small interface (get_text / set_text / get_selection / set_selection / focus); each one is written the way the control really works,
so a limit shows up as a limit instead of being hidden by a wrapper.  MS_DEBUG-style event log goes to the console and the page."""
import asyncio
import sys
import time

import flet as ft

sys.path.insert(0, r"D:\AI\MyApps\WorkflowPad")
from music_studio import lyrics_tools as LT

ENGINE = (sys.argv[1] if len(sys.argv) > 1 else "tf").lower()
PORT = {"tf": 8561, "ce": 8562}[ENGINE]
T0 = time.perf_counter()


def stamp() -> str:
    return f"{(time.perf_counter() - T0) * 1000:9.0f} ms"


def make_lyrics(lines: int = 250) -> str:
    out, n, sec = [], 0, 0
    names = ["Verse", "Chorus", "Verse", "Bridge", "Chorus"]
    while n < lines:
        name = names[sec % len(names)]
        sec += 1
        out.append(f"[{name} {sec}]" if name != "Chorus" else "[Chorus]")
        for k in range(1, 9):
            if k % 4 == 0:
                out.append(f"line {sec}.{k} \U0001F3B5 summer without you \U0001F496")
            elif k % 3 == 0:
                out.append(f"บรรทัด {sec}.{k} ฤดูร้อนที่ไม่มีเธออยู่ข้างๆ ฉันเดินเดียวดายในสายฝน")
            elif k % 2:
                out.append(f"line {sec}.{k} summer")
            else:
                out.append(f"บรรทัด {sec}.{k} ฝนตก")
        out.append("")
        n = len(out)
    return "\n".join(out) + "\n"


def u16(text: str, i: int) -> int:
    """Python index -> UTF-16 offset (what Flutter uses)."""
    return len(text[:i].encode("utf-16-le")) // 2


def py(text: str, o: int) -> int:
    """UTF-16 offset -> Python index."""
    acc = 0
    for i, ch in enumerate(text):
        if acc >= o:
            return i
        acc += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


async def main(page: ft.Page):
    page.title = f"Lyrics engine test: {ENGINE}"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 10
    log_lines: list[str] = []
    log_view = ft.Text("", size=10, selectable=True, font_family="Consolas")
    status = ft.Text("", size=12)
    scroll_info = ft.Text("scroll: n/a", size=11)

    def log(msg: str):
        line = f"[{stamp()}] {msg}"
        print(line, flush=True)
        log_lines.append(line)
        del log_lines[:-14]
        log_view.value = "\n".join(log_lines)
        try:
            log_view.update()
        except Exception:
            pass

    text0 = make_lyrics()
    state = {"busy": False, "history": [(text0, (0, 0))], "pos": 0, "last_tool": "-"}

    # ── the editor engine ──────────────────────────────────────────────────────────────────────────────────────
    if ENGINE == "tf":
        from flet import TextField
        field = TextField(value=text0, multiline=True, min_lines=18, border=ft.InputBorder.NONE, text_size=15, color="#111111", bgcolor="#ffffff",
                          cursor_color="#1a56ff", selection_color="#9ec1ff")
        scroller = ft.Column([field], scroll=ft.ScrollMode.AUTO, scroll_interval=40, expand=True)
        holder = ft.Container(scroller, bgcolor="#ffffff", border_radius=6, height=430, padding=6)

        def on_scroll(e):
            scroll_info.value = f"scroll: {e.pixels:.0f}/{e.max_scroll_extent:.0f} {e.event_type}"
            try:
                scroll_info.update()
            except Exception:
                pass
        scroller.on_scroll = on_scroll
    else:
        from flet_code_editor import CodeEditor
        field = CodeEditor(value=text0, language=None, autocomplete=False, expand=True,
                           text_style=ft.TextStyle(size=15))
        holder = ft.Container(field, bgcolor="#ffffff", border_radius=6, height=430)

    async def get_text() -> str:
        return field.value or ""

    async def get_selection() -> tuple[int, int]:
        s = field.selection
        if s is None:
            return (0, 0)
        t = field.value or ""
        a, b = py(t, min(s.start, s.end)), py(t, max(s.start, s.end))
        return (a, b)

    def push(text: str, a: int, b: int):
        field.value = text
        field.selection = ft.TextSelection(base_offset=u16(text, a), extent_offset=u16(text, b))

    async def focus():
        await field.focus()

    # ── events ─────────────────────────────────────────────────────────────────────────────────────────────────
    def refresh_status():
        t = field.value or ""
        s = field.selection
        sel = (s.start, s.end) if s else None
        status.value = f"engine={ENGINE}  chars={len(t)}  lines={t.count(chr(10))}  selection(utf16)={sel}  last tool={state['last_tool']}"
        try:
            status.update()
        except Exception:
            pass

    arrival = {"ev": None}

    def on_sel(e):
        s = field.selection
        if arrival["ev"] is not None:
            arrival["ev"].set()
        log(f"selection event -> {(s.start, s.end) if s else None}")
        refresh_status()

    def on_change(e):
        # a user edit: keep the undo stack in sync (the same engine-agnostic stack for both engines)
        t = field.value or ""
        if t != state["history"][state["pos"]][0]:
            del state["history"][state["pos"] + 1:]
            state["history"].append((t, (0, 0)))
            state["pos"] += 1
        refresh_status()

    field.on_selection_change = on_sel
    field.on_change = on_change

    # ── a tool click: the same steps for both engines ──────────────────────────────────────────────────────────
    async def run_tool(key: str, label: str):
        t = await get_text()
        a, b = await get_selection()
        t_start = time.perf_counter()
        try:
            ed = LT.apply_tool(key, t, a, b)
        except LT.ToolError as ex:
            log(f"tool {label}: {ex}")
            return
        state["last_tool"] = label
        log(f"tool {label} at sel=({a},{b}) -> text {len(t)}->{len(ed.text)}, caret ({ed.start},{ed.end})")
        await focus()
        mode = mode_dd.value
        if mode == "one":                                   # text + selection in one update (what a plain engine API would allow)
            push(ed.text, ed.start, ed.end)
        elif mode == "selfirst":                            # selection assigned before the text, one update
            field.selection = ft.TextSelection(base_offset=u16(ed.text, ed.start), extent_offset=u16(ed.text, ed.end))
            field.value = ed.text
        elif mode == "two":                                 # text, update, then selection, update (no waiting)
            field.value = ed.text
            field.update()
            field.selection = ft.TextSelection(base_offset=u16(ed.text, ed.start), extent_offset=u16(ed.text, ed.end))
        else:                                               # text, update, wait for the engine's own selection report (bounded), then selection
            arrival["ev"] = asyncio.Event()
            field.value = ed.text
            field.update()
            try:
                await asyncio.wait_for(arrival["ev"].wait(), 2.0)
                log("engine reported its caret after the text update")
            except asyncio.TimeoutError:
                log("no selection report within 2 s")
            arrival["ev"] = None
            field.selection = ft.TextSelection(base_offset=u16(ed.text, ed.start), extent_offset=u16(ed.text, ed.end))
        state["history"][state["pos"]] = (t, (a, b))
        del state["history"][state["pos"] + 1:]
        state["history"].append((ed.text, (ed.start, ed.end)))
        state["pos"] += 1
        field.update()
        log(f"tool {label}: update() sent in {(time.perf_counter() - t_start) * 1000:.0f} ms")
        refresh_status()

    async def history_step(d: int):
        n = state["pos"] + d
        if not 0 <= n < len(state["history"]):
            return
        state["pos"] = n
        text, (a, b) = state["history"][n]
        if d < 0:                                          # undo: the caret goes back to where the edit was made
            a, b = state["history"][n + 1][1] if False else (a, b)
        await focus()
        push(text, a, b)
        field.update()
        log(f"{'undo' if d < 0 else 'redo'} -> text {len(text)}, caret ({a},{b})")
        refresh_status()

    async def go(pos_name: str):
        t = await get_text()
        idx = {"start": 0, "middle": len(t) // 2, "end": len(t)}[pos_name]
        idx = t.find("\n", idx) + 1 if pos_name == "middle" else idx
        await focus()
        field.selection = ft.TextSelection(base_offset=u16(t, idx), extent_offset=u16(t, idx))
        field.update()
        log(f"go {pos_name}: caret set to {idx}")

    async def select_sample():
        t = await get_text()
        i = t.find("ฤดูร้อน")
        j = t.find("\U0001F496", i) + 1
        await focus()
        field.selection = ft.TextSelection(base_offset=u16(t, i), extent_offset=u16(t, j))
        field.update()
        log(f"selected Thai+emoji chars {i}..{j}: {t[i:j]!r}")

    def tool_btn(key, label):
        async def click(e):
            await run_tool(key, label)
        return ft.OutlinedButton(label, on_click=click)

    async def b_undo(e):
        await history_step(-1)

    async def b_redo(e):
        await history_step(1)

    async def b_start(e):
        await go("start")

    async def b_mid(e):
        await go("middle")

    async def b_end(e):
        await go("end")

    async def b_sel(e):
        await select_sample()

    mode_dd = ft.Dropdown(value=__import__("os").environ.get("LYR_MODE", "one"), width=150, options=[ft.dropdown.Option("one"), ft.dropdown.Option("two"), ft.dropdown.Option("selfirst"), ft.dropdown.Option("event")], label="apply mode")
    tools = ft.Row([mode_dd, tool_btn("Verse", "Verse"), tool_btn("Chorus", "Chorus"), tool_btn("whisper", "Whisper"), tool_btn("harmonies", "Harmonies"),
                    tool_btn("hold", "Hold"), tool_btn("repeat", "Repeat"), tool_btn("linebreak", "Line Break")], wrap=True, spacing=4, run_spacing=4)
    nav = ft.Row([ft.OutlinedButton("Undo", on_click=b_undo), ft.OutlinedButton("Redo", on_click=b_redo), ft.OutlinedButton("Go start", on_click=b_start),
                  ft.OutlinedButton("Go middle", on_click=b_mid), ft.OutlinedButton("Go end", on_click=b_end), ft.OutlinedButton("Select Thai+emoji", on_click=b_sel)],
                 wrap=True, spacing=4, run_spacing=4)
    page.add(ft.Text(f"Lyrics engine test — {ENGINE.upper()}", weight=ft.FontWeight.BOLD), tools, nav, holder, status, scroll_info, log_view)
    refresh_status()
    log("ready")


if __name__ == "__main__":
    ft.app(main, view=ft.AppView.WEB_BROWSER, port=PORT)
