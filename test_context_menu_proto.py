"""Prototype only (not part of Music Studio): what does a right-click do on a Flet multiline TextField?
  python test_context_menu_proto.py plain   -> TextField alone (port 8571)
  python test_context_menu_proto.py menu    -> TextField inside ft.ContextMenu with a custom secondary menu (port 8572)
Logs every selection event / menu event with the caret offset, so the log shows whether a right-click moves the caret and where."""
import sys
import time

import flet as ft

MODE = sys.argv[1] if len(sys.argv) > 1 else "plain"
PORT = {"plain": 8571, "menu": 8572}[MODE]
T0 = time.perf_counter()
TEXT = "[Intro]\n[Verse 1]\nHello world\n[Chorus]\n"


async def main(page: ft.Page):
    page.theme_mode = ft.ThemeMode.DARK
    log_lines = []
    log_view = ft.Text("", size=11, selectable=True, font_family="Consolas")

    def log(msg):
        line = f"[{(time.perf_counter() - T0) * 1000:8.0f}] {msg}"
        print(line, flush=True)
        log_lines.append(line)
        del log_lines[:-16]
        log_view.value = "\n".join(log_lines)
        try:
            log_view.update()
        except Exception:
            pass

    tf = ft.TextField(value=TEXT, multiline=True, min_lines=8, text_size=16, width=520)

    def on_sel(e):
        s = tf.selection
        log(f"selection event -> {(s.start, s.end) if s else None}")
    tf.on_selection_change = on_sel
    tf.on_focus = lambda e: log("focus")
    tf.on_blur = lambda e: log("blur")

    if MODE == "menu":
        def on_select(e):
            log(f"menu select item_index={e.item_index} global={e.global_position} local={e.local_position} button={e.button} sel_at_that_time={(tf.selection.start, tf.selection.end) if tf.selection else None}")

        def on_dismiss(e):
            log(f"menu dismiss local={e.local_position} button={e.button}")
        body = ft.ContextMenu(content=tf, secondary_items=[ft.PopupMenuItem(content="Section Options..."), ft.PopupMenuItem(content="Other")],
                              on_select=on_select, on_dismiss=on_dismiss)
    else:
        body = tf
    page.add(ft.Text(f"right-click prototype: {MODE}"), body, log_view)
    log("ready")


if __name__ == "__main__":
    ft.app(main, view=ft.AppView.WEB_BROWSER, port=PORT)
