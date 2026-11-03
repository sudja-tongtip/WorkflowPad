"""Commands for the lyrics editor's selection toolbar.

The screen builds the toolbar from `toolbar_commands` while text is selected, and when a
button is pressed asks `plan` again with the selection at that moment. A button that no
longer matches the selection is refused. Copy, cut, paste and select-all stay on the
editor's own keys and its own menu.

Nothing here imports the widget toolkit, calls a model, or changes a specialist session.
`route` is one of: edit (Python, the clipboard or the selection), action (an existing AI
Action), dialog (an existing dialog), chat (the note is placed in the chat box; the user
sends it). A chat row names a specialist. `plan` says to show that specialist only when
the chat is not already showing them.
"""
from __future__ import annotations

from dataclasses import dataclass

from .tag_parser import tag_at

EDIT_KEYS = ("copy", "cut", "paste", "select_all")


@dataclass(frozen=True)
class Command:
    key: str
    label: str
    tip: str
    route: str
    specialist: str = ""
    action: str = ""
    target: str = ""


@dataclass(frozen=True)
class Plan:
    ok: bool
    key: str
    route: str
    specialist: str
    action: str
    target: str
    switch: bool
    prefill: str
    reason: str


def clamp(text: str, start, end) -> tuple[int, int]:
    """Python offsets inside `text`. A backwards or oversized range is folded in."""
    text = text or ""
    n = len(text)
    try:
        a = int(start)
    except (TypeError, ValueError):
        a = 0
    try:
        b = a if end is None else int(end)
    except (TypeError, ValueError):
        b = a
    a = max(0, min(a, n))
    b = max(0, min(b, n))
    return (a, b) if a <= b else (b, a)


def menu_kind(text: str, start, end) -> str:
    """tag | lines | word | empty.

    A selection that sits inside one [tag] is the tag, including a caret in that tag.
    Several lines are a line selection. Any other non-blank selection is a word (one
    line, or a phrase on one line). A caret that is not in a tag, and a blank selection,
    are empty.
    """
    text = text or ""
    a, b = clamp(text, start, end)
    if tag_at(text, a, b) is not None:
        return "tag"
    chunk = text[a:b]
    if "\n" in chunk:
        return "lines"
    if chunk.strip():
        return "word"
    return "empty"


_EDIT = (
    Command("copy", "Copy", "Copy the selection", "edit"),
    Command("cut", "Cut", "Cut the selection into the clipboard", "edit"),
    Command("paste", "Paste", "Paste the clipboard in place of the selection", "edit"),
    Command("select_all", "Select All", "Select all of the lyrics", "edit"),
)

_BY_KIND = {
    "word": (
        Command("alternatives", "Find Alternative Words", "Alternatives for the words you selected. Nothing changes until you replace one.", "dialog", target="alternatives"),
        Command("refine", "Refine Wording", "Write a note in the chat about these words. You can edit it before sending.", "chat", "songwriter"),
        Command("translate", "Translate", "Translate the selection. Nothing changes until you press Apply.", "action", "songwriter", "translate"),
        Command("ask_songwriter", "Ask Songwriter", "Ask the Songwriter AI about this wording. The note is placed in the chat for you to edit.", "chat", "songwriter"),
    ),
    "lines": (
        Command("rewrite", "Rewrite", "Ask for a rewrite of these lines. The note is placed in the chat for you to edit. Nothing is sent yet.", "chat", "songwriter"),
        Command("emotion", "Increase Emotion", "Rewrites your SELECTION with more feeling. With no selection: the line your cursor is in (you see it before anything is sent).", "action", "songwriter", "emotion"),
        Command("singability", "Improve Singability", "Rewrites your SELECTION so it is easier to sing. With no selection: the line your cursor is in (you see it before anything is sent).", "action", "songwriter", "singability"),
        Command("phrasing", "Smart Phrasing", "Suggests spaces, line breaks or pause marks (...) so the lyrics read and sing more smoothly. Your words are never changed. You choose which of the three, and what to work on.", "action", "songwriter", "phrasing"),
    ),
    "tag": (
        Command("section", "Section Options", "Open Section Options for the [tag] at the cursor.", "dialog", target="section"),
        Command("cues", "Edit Cues", "Edit the cues of this [tag] (Section Options)", "dialog", target="section"),
        Command("structure", "Song Structures", "Arrange the sections. Inserting tags does not call the AI. Arrange with AI runs only if you choose it.", "dialog", target="structures"),
        Command("ask_producer", "Ask Music Producer", "Ask the Music Producer AI about this section. The note is placed in the chat for you to edit.", "chat", "music_producer"),
    ),
    "empty": (
        Command("add_section", "Add Section", "Insert a section. With no sung lyrics the tags are inserted by the program.", "dialog", target="structures"),
        Command("continue", "Continue from Cursor", "Writes the NEXT lines after the line where your cursor (or the end of your selection) is. Nothing is replaced.", "action", "songwriter", "continue"),
    ),
}

_INDEX = {c.key: c for group in (_EDIT, *_BY_KIND.values()) for c in group}


def commands_for(kind: str) -> tuple:
    """The edit commands, then the commands for this kind. Paste is one of the edit commands."""
    return _EDIT + _BY_KIND.get(kind, _BY_KIND["empty"])


def toolbar_commands(kind: str) -> tuple:
    """Actions shown for a text selection. Nothing for a caret or a blank selection."""
    if kind in ("word", "lines", "tag"):
        return _BY_KIND[kind]
    return ()


def command(key: str):
    return _INDEX.get(key)


def edit_still_fits(snapshot: str, current: str) -> bool:
    """False when the lyrics moved while a clipboard call was in flight. The edit must not land."""
    return snapshot == current


def _quote(text: str, a: int, b: int, limit: int) -> str:
    q = text[a:b].strip()
    if len(q) > limit:
        q = q[:limit].rstrip() + "…"
    return q


def _prefill(key: str, text: str, a: int, b: int, lang: str) -> str:
    th = lang == "th"
    if key == "refine":
        q = _quote(text, a, b, 240)
        return (f"ปรับคำนี้ โดยรักษาความหมายเดิม\n\n“{q}”\n\nคำถามของฉัน: " if th
                else f"Refine this wording. Keep the meaning.\n\n“{q}”\n\nMy question: ")
    if key == "ask_songwriter":
        q = _quote(text, a, b, 240)
        return (f"เกี่ยวกับท่อนนี้:\n“{q}”\n\nคำถามของฉัน: " if th
                else f"About this lyric:\n“{q}”\n\nMy question: ")
    if key == "rewrite":
        q = _quote(text, a, b, 800)
        return (f"เขียนบรรทัดเหล่านี้ใหม่ โดยรักษาความหมายและแท็กท่อนไว้\n\n{q}\n\nคำถามของฉัน: " if th
                else f"Rewrite these lines. Keep the meaning and the section tags.\n\n{q}\n\nMy question: ")
    if key == "ask_producer":
        ref = tag_at(text, a, b)
        tag = ref.original if ref is not None else _quote(text, a, b, 80)
        return (f"เกี่ยวกับแท็กท่อน {tag}:\n\nคำถามของฉัน: " if th
                else f"About this section tag {tag}:\n\nMy question: ")
    return ""


def plan(key: str, text: str, start, end, visible_spec: str = "", lang: str = "en") -> Plan:
    """What a click on `key` does for the selection now. `switch` is true only for a chat row whose specialist is not the one already on screen."""
    text = text or ""
    a, b = clamp(text, start, end)
    kind = menu_kind(text, a, b)
    cmd = command(key)
    if cmd is None:
        return Plan(False, key or "", "", "", "", "", False, "", "Unknown command.")
    shown = {c.key for c in commands_for(kind)}
    if cmd.route != "edit" and cmd.key not in shown:
        return Plan(False, cmd.key, cmd.route, cmd.specialist, cmd.action, cmd.target, False, "", "The selection changed. Select the text again.")
    if cmd.key in ("copy", "cut") and a == b:
        return Plan(False, cmd.key, "edit", "", "", "", False, "", "Select text first.")
    switch = bool(cmd.route == "chat" and cmd.specialist and cmd.specialist != (visible_spec or ""))
    prefill = _prefill(cmd.key, text, a, b, lang) if cmd.route == "chat" else ""
    return Plan(True, cmd.key, cmd.route, cmd.specialist, cmd.action, cmd.target, switch, prefill, "")
