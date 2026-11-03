"""The compact "Selected Lyrics" bar above the Music Studio chat box.

The bar only shows the selection the next question will attach. It does not copy that text into the
message box and it does not build the LLM prompt. ask_ai still attaches the slice once, through the
same bounds it already used (0 <= start < end <= len). Dismissing the bar remembers that exact slice
so the question leaves it out; the lyrics, the caret and the editor selection stay.
"""
from __future__ import annotations

import flet as ft

CLEAR_TIP = "Remove this selection from the next question. The lyrics stay."


def capture_selection(song_id: str, sheet_id: str, lyrics: str, sel) -> dict | None:
    """The selection at the moment the user makes it: song, sheet, offsets and the exact slice.

    None when the range is a caret, out of range, or empty. The chat box does not call this.
    """
    has, text = selection_for_chat(song_id, sheet_id, lyrics, sel, None)
    if not has:
        return None
    try:
        a, b = int(sel[0]), int(sel[1])
    except (TypeError, ValueError, IndexError):
        return None
    return {"song_id": song_id, "sheet_id": sheet_id, "start": a, "end": b, "text": text}


def selection_from_snapshot(song_id: str, sheet_id: str, lyrics: str, snap, dismissed) -> tuple:
    """(attach?, slice, start, end) from a snapshot taken on this song and sheet.

    Another song, another sheet, a range whose text has changed, or the slice the user removed
    with × attaches nothing. The slice is the snapshot's text, once.
    """
    if not isinstance(snap, dict) or snap.get("song_id") != song_id or snap.get("sheet_id") != sheet_id:
        return False, "", 0, 0
    try:
        a, b = int(snap["start"]), int(snap["end"])
    except (KeyError, TypeError, ValueError):
        return False, "", 0, 0
    has, selected = selection_for_chat(song_id, sheet_id, lyrics, (a, b), dismissed)
    if not has or selected != snap.get("text"):
        return False, "", 0, 0
    return True, selected, a, b


def selection_for_chat(song_id: str, sheet_id: str, lyrics: str, sel, dismissed) -> tuple[bool, str]:
    """(attach?, exact slice) for one chat question.

    Same bounds the chat already used: a range that is not inside these lyrics attaches nothing.
    `dismissed` is (song id, sheet id, start, end, slice) of a range the user removed with the bar's
    ×. Another song, another sheet, or a different slice is not that range, so it still attaches.
    """
    try:
        a, b = int(sel[0]), int(sel[1])
    except (TypeError, ValueError, IndexError):
        return False, ""
    text = lyrics or ""
    has = bool(song_id) and 0 <= a < b <= len(text)
    selected = text[a:b] if has else ""
    if selected and dismissed == (song_id, sheet_id, a, b, selected):
        return False, ""
    return has, selected


def chat_selection_quote(selected: str) -> str:
    """The two-line preview. A trailing newline is not given a blank line of its own. The slice ask_ai sends is not this string."""
    body = selected.rstrip("\n")
    if body == "":
        body = selected
    return "“" + body + "”"


def set_user_tooltip(ctl, text: str) -> None:
    """Tooltip holding the user's lyrics. The UI translator must not keep the string or rewrite it."""
    ctl.tooltip = text or ""
    store = ctl.__dict__.get("_i18n")
    if store is not None:
        store.pop("tooltip", None)
    vals = getattr(ctl, "_values", None)
    if isinstance(vals, dict):
        if text:
            vals["tooltip"] = text
        else:
            vals.pop("tooltip", None)
    dirty = getattr(ctl, "_dirty", None)
    if isinstance(dirty, dict):
        dirty["tooltip"] = None


def make_chat_selection_bar(on_clear, *, accent_soft: str, text1: str, text2: str, border: str):
    """The bar and `apply(slice)`. `apply` returns the controls to redraw, or () when nothing changed.

    The slice is the exact text the question will attach, or "" when the bar hides. The preview is at
    most two lines; the tooltip is the whole slice. × calls `on_clear` and does not touch the text.
    """
    title = ft.Text("Selected Lyrics", key="chat_sel_title", size=11, weight=ft.FontWeight.W_600, color=text2)
    body = ft.Text(key="chat_sel_body", spans=[ft.TextSpan("")], size=12, color=text1, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
    closer = ft.Container(
        ft.Text("×", size=16, color=text2),
        key="chat_sel_clear",
        width=22, height=22, alignment=ft.Alignment(0, 0), border_radius=6, ink=True,
        tooltip=CLEAR_TIP,
        on_click=on_clear,
    )
    bar = ft.Container(
        key="chat_sel_bar",
        visible=False,
        bgcolor=accent_soft,
        border=ft.Border.all(1, border),
        border_radius=8,
        padding=ft.Padding(left=8, top=2, right=2, bottom=4),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=ft.Column([
            ft.Row([title, ft.Container(expand=True), closer], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(body, padding=ft.Padding(left=0, top=0, right=8, bottom=0)),
        ], spacing=0, tight=True),
    )
    shown = {"text": None}

    def apply(text: str):
        text = text or ""
        if text == shown["text"]:
            return ()
        shown["text"] = text
        bar.visible = bool(text)
        if text:
            body.spans = [ft.TextSpan(chat_selection_quote(text))]
            set_user_tooltip(body, text)
        else:
            body.spans = [ft.TextSpan("")]
            set_user_tooltip(body, "")
        return (bar, body)

    return bar, apply
