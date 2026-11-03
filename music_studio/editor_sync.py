"""Decisions for the lyrics CodeEditor: which selection event is the user, and which push may still move the caret.

flet-code-editor 0.85 sends the new text and the new selection as their own events, and the control's
`value` / `selection` as a separate property patch. The patch can already be a later edit by the time the
event runs, so the event payload is the one that belongs to that event. This module does not touch the
widget, the clipboard, or the song.
"""

USER_CAUSES = frozenset({
    "tap", "doubleTap", "longPress", "forcePress", "keyboard", "toolbar", "drag", "scribble",
})


def cause_name(cause) -> str:
    """The cause string on a selection event. CodeEditor 0.85 does not send one; the field stays empty."""
    if cause is None:
        return ""
    value = getattr(cause, "value", None)
    if isinstance(value, str):
        return value
    return cause if isinstance(cause, str) else ""


def offsets_of(sel):
    """(start, end) in the order the editor reports them, or None. Offsets are UTF-16, same as Flutter."""
    if sel is None:
        return None
    try:
        a, b = int(sel.start), int(sel.end)
    except (AttributeError, TypeError, ValueError):
        return None
    if a < 0 or b < 0:
        return None
    return (a, b) if a <= b else (b, a)


def pick_offsets(event_sel, control_sel):
    """(offsets, source). The event carries the selection that produced it. The control property is only a fallback:
    it is updated by a different message and can already be the next selection, or still the previous one."""
    ev, ctl = offsets_of(event_sel), offsets_of(control_sel)
    if ev is not None:
        return ev, ("event" if ctl is None or ctl == ev else "event_not_control")
    if ctl is not None:
        return ctl, "control"
    return None, "none"


def push_event_kind(ev, arrival_pending: bool, arrival_at: int, echoes, cause: str, stale=()) -> str:
    """arrival | echo | stale_echo | user.

    Same order as classify_selection. A cause that only a user gesture has (tap, drag, keyboard, …) is the user
    even when the offsets are exactly an echo we are still waiting for. CodeEditor 0.85 leaves the cause empty,
    so that override does not fire until a build actually sends it. `stale` holds echoes the user already
    replaced; one report of those offsets is still that echo."""
    if cause in USER_CAUSES:
        return "user"
    if arrival_pending and ev == (arrival_at, arrival_at):
        return "arrival"
    if echoes and ev == echoes[-1]:
        return "echo"
    if (echoes and ev in list(echoes)[:-1]) or ev in stale:
        return "stale_echo"
    return "user"


def change_action(typed: str, model: str, awaiting, discarded) -> str:
    """arrival | late_echo | user.

    `awaiting` is the text of the push that is still waiting for the field to report it.
    `discarded` is a text we already sent and then abandoned because the model moved on.
    A report of either, once the model holds something else, is the field catching up. It is not a new edit.
    """
    if awaiting is not None and typed == awaiting and typed != model:
        return "late_echo"
    if discarded is not None and typed == discarded and typed != model:
        return "late_echo"
    if typed == model:
        return "arrival"
    return "user"


def selection_needed(requested: bool, text_changed: bool, fresh: bool, keep_view: bool) -> bool:
    """A push sends a selection when one was asked for and the editor cannot keep the caret it already has.

    A sheet that is only shown again (`keep_view`) already has its caret. Sending the selection would scroll to it.
    The selection is sent when the text had to be replaced, or the editor was just created.
    """
    return bool(requested) and (text_changed or fresh or not keep_view)


def focus_skip_reason(requested: bool, chat_focused: bool, user_acted: bool, superseded: bool, editor_blurred: bool = False):
    """None when this push may move the keyboard to the lyrics editor. A reason string when it must not.

    `editor_blurred`: the editor had focus when the push began and lost it before this step (the right-click
    menu, or a click somewhere else). The caret write still happens. Focus is not pulled back.
    """
    if not requested:
        return "not requested"
    if superseded:
        return "a newer push or another song"
    if user_acted:
        return "the user edited or selected during the push"
    if editor_blurred:
        return "the editor lost focus during the push"
    if chat_focused:
        return "chat holds the keyboard"
    return None
