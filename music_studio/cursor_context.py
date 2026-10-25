"""What the caret / selection of the lyrics editor is on. Pure functions, no UI: the Lyrics Tools sidebar shows tools that fit this context.

Offsets are Python string indexes (the same as S.sel). The tag is found with tag_parser, so nothing here knows what a tag is.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import tag_parser as TP

SECTION_TAG = "section_tag"          # caret (or a selection) inside [Verse 2], [Chorus - powerful] ...
STANDALONE_TAG = "standalone_tag"    # a tag that is a cue on its own: [Hold], [whispered], [Pause]
CUSTOM_TAG = "custom_tag"            # any other [tag] (the user's own name)
LYRICS = "lyrics"                    # caret on a line with sung text
TEXT_SELECTION = "text_selection"    # a selection that is not inside one tag (also a selection across tags: the safe mode, no single tag is guessed)
EMPTY_LINE = "empty_line"
NO_SONG = "no_song"

TAG_CONTEXTS = (SECTION_TAG, STANDALONE_TAG, CUSTOM_TAG)


@dataclass(frozen=True)
class CursorContext:
    context: str
    selection: tuple                 # (start, end), start <= end
    line_number: int                 # 1-based line of the selection start
    tag_range: tuple | None = None   # (start, end) of the tag, when the context is a tag
    tag_text: str = ""               # "[Verse 2]"
    tag_kind: str = ""               # section | standalone_cue | custom (tag_parser's word)
    tag_name: str = ""               # "Verse 2"
    crosses_tags: bool = False       # a selection that touches a tag but is not inside one

    def signature(self, song_id: str = "", sheet_id: str = "") -> tuple:
        """What the sidebar shows. The caret position is NOT part of it for sung lyrics / empty lines (moving inside a line must not rebuild the sidebar); a tag is, because the
        sidebar edits that very tag."""
        if self.context in TAG_CONTEXTS:
            return (song_id, sheet_id, self.context, self.tag_range, self.tag_text)
        if self.context == TEXT_SELECTION:
            return (song_id, sheet_id, self.context, self.crosses_tags)
        return (song_id, sheet_id, self.context)

    def as_dict(self, song_id: str = "", sheet_id: str = "", editor_epoch: int = 0) -> dict:
        return {"song_id": song_id, "sheet_id": sheet_id, "editor_epoch": editor_epoch, "context": self.context, "selection": list(self.selection),
                "tag_range": list(self.tag_range) if self.tag_range else None, "tag_text": self.tag_text, "line_number": self.line_number}


def resolve(text: str, start: int, end: int | None = None, has_song: bool = True) -> CursorContext:
    text = text or ""
    a = max(0, min(len(text), start))
    b = a if end is None else max(0, min(len(text), end))
    a, b = min(a, b), max(a, b)
    line_no = text.count("\n", 0, a) + 1
    if not has_song:
        return CursorContext(NO_SONG, (a, b), 1)
    ref = TP.tag_at(text, a, b)
    if ref is not None:                                                    # the caret / selection is inside exactly one tag
        p = TP.parse_tag(ref.inner)
        ctx = {"section": SECTION_TAG, "standalone_cue": STANDALONE_TAG}.get(p.kind, CUSTOM_TAG)
        return CursorContext(ctx, (a, b), line_no, (ref.start, ref.end), ref.original, p.kind, p.name)
    if a != b:
        crosses = any(t.start < b and a < t.end for t in TP.find_tags(text))         # touches a tag without being inside one
        return CursorContext(TEXT_SELECTION, (a, b), line_no, crosses_tags=crosses)
    ls = text.rfind("\n", 0, a) + 1
    le = text.find("\n", a)
    le = len(text) if le < 0 else le
    if not text[ls:le].strip():
        return CursorContext(EMPTY_LINE, (a, b), line_no)
    return CursorContext(LYRICS, (a, b), line_no)
