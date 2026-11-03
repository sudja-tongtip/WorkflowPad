"""Clear every cue on one section tag. Pure functions. The parser is only called, never changed.

A section tag is `[Name]` or `[Name - cue, cue]`. Clearing keeps the name and replaces that one tag in place.
A standalone cue such as `[Hold]`, a custom tag, and every other section stay as they are.
"""
from __future__ import annotations

from . import tag_parser as TP


def bare_section_tag(tag: str) -> str | None:
    """`[Chorus - powerful belting]` → `[Chorus]`. None when `tag` is not one section tag."""
    raw = (tag or "").strip()
    if not TP.valid_tag_text(raw):
        return None
    parsed = TP.parse_tag(raw[1:-1])
    if parsed.kind != "section":
        return None
    return TP.build_tag(parsed.name, "")


def tag_has_cues(tag: str) -> bool:
    """True when this text is a section tag that still carries cue text."""
    bare = bare_section_tag(tag)
    return bare is not None and bare != (tag or "").strip()


def clear_enabled(preview: str, saved: str) -> bool:
    """The button is on when the preview or the tag in the lyrics still has cues."""
    return tag_has_cues(preview) or tag_has_cues(saved)


def target_matches(tg: dict, song_id: str, sheet_id: str, epoch: int) -> bool:
    """The draft still belongs to the song, the sheet and the editor epoch it was opened on."""
    return bool(song_id) and song_id == tg.get("song") and sheet_id == tg.get("sheet") and epoch == tg.get("epoch")


def clear_one_section(text: str, ref: TP.TagRef) -> tuple[str, str] | None:
    """Replace `ref` with the same section name and no cues. None when there is nothing to clear.

    Raises tag_parser.StaleTarget when the lyrics no longer hold that exact tag at that place.
    """
    bare = bare_section_tag(ref.original)
    if bare is None or bare == ref.original:
        return None
    return TP.replace_tag(text, ref, bare), bare


def map_selection(sel: tuple[int, int], start: int, end: int, new_end: int) -> tuple[int, int]:
    """Keep a selection across a shorter tag: inside the removed cue it stops at the new end; text after the tag stays on the same characters."""
    delta = new_end - end

    def one(p: int) -> int:
        if p <= start:
            return p
        if p >= end:
            return p + delta
        return min(p, new_end)

    return one(int(sel[0])), one(int(sel[1]))
