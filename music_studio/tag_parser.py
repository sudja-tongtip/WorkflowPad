"""Finding, reading and replacing one [tag] in lyrics. Pure functions, no UI.

A tag is a complete [...] on one line. Only the tag the caller points at is ever replaced, and replacing it first checks that the text still holds that exact tag at that
exact place, so a stale target can never change something else.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import cue_library as CL

TAG_RE = re.compile(r"\[[^\[\]\n]*\]")
_CONNECT = re.compile(r"\s*(?:,|&|\+|\band\b|\bwith\b)\s*", re.IGNORECASE)
MANY_CUES = 4


class StaleTarget(Exception):
    """The text no longer holds the tag that was chosen."""


@dataclass(frozen=True)
class TagRef:
    start: int
    end: int
    original: str                  # "[Chorus - powerful]"

    @property
    def inner(self) -> str:
        return self.original[1:-1]


@dataclass(frozen=True)
class ParsedTag:
    kind: str                      # section | standalone_cue | custom
    name: str                      # "Chorus" / "Verse 1" / "whispered" / "My Tag"
    cue_text: str                  # text after the first " - " ("" when there is none)
    section: str | None            # canonical section ("Verse" for "Verse 1") or None


def find_tags(text: str) -> list:
    return [TagRef(m.start(), m.end(), m.group(0)) for m in TAG_RE.finditer(text or "")]


def tag_at(text: str, start: int, end: int | None = None) -> TagRef | None:
    """The tag the caret (or the selection) is in. None when there is none, the selection leaves the tag, or two tags touch at that spot (not sure which one is meant)."""
    end = start if end is None else end
    hits = [t for t in find_tags(text) if t.start <= min(start, end) and max(start, end) <= t.end]
    return hits[0] if len(hits) == 1 else None


def parse_tag(inner: str) -> ParsedTag:
    inner = inner.strip()
    name, sep, cue = inner.partition(" - ")
    name, cue = name.strip(), cue.strip()
    section = CL.section_key(name)
    if section:
        return ParsedTag("section", name, cue, section)
    if not sep and inner.lower() in CL.STANDALONE:
        return ParsedTag("standalone_cue", inner, "", None)
    return ParsedTag("custom", name, cue, None)


def read_cues(cue_text: str):
    """(cues, original): the library cues named in cue_text, in order, when EVERY part of the text is a known cue; otherwise ([], cue_text) so that nothing the user wrote
    is guessed at or lost (the whole text is then kept as it is)."""
    cue_text = (cue_text or "").strip()
    if not cue_text:
        return [], ""
    found = []
    for part in _CONNECT.split(cue_text):
        c = CL.find_phrase(part)
        if c is None:
            return [], cue_text
        if c not in found:
            found.append(c)
    return found, ""


def _join(items: list) -> str:
    if len(items) == 1:
        return items[0]
    glue = " with " if items[-1].lower() in CL.WITH_CUES else " and "
    return ", ".join(items[:-1]) + glue + items[-1]


def compose_cue_text(cues: list, original: str = "") -> str:
    """Selected cues (Cue objects or plain text) plus text the parser could not read. No cue is repeated, the user's own wording comes first and is not changed."""
    items = []
    base = (original or "").strip()
    if base:
        items.append(base)
    for c in cues:
        p = c.phrase if isinstance(c, CL.Cue) else str(c).strip()
        if not p:
            continue
        seen = [x.lower() for x in items]
        if p.lower() in seen or any(re.search(r"(?<!\w)" + re.escape(p.lower()) + r"(?!\w)", s) for s in seen):
            continue
        items.append(p)
    return _join(items) if items else ""


def build_tag(name: str, cue_text: str) -> str:
    return f"[{name} - {cue_text}]" if cue_text else f"[{name}]"


def valid_tag_text(tag: str) -> bool:
    """A tag the user typed into the preview: exactly one [...] with nothing inside that could break the pairing."""
    return bool(TAG_RE.fullmatch(tag.strip())) and tag.strip() != "[]" and tag.strip("[] ") != ""


def _check(text: str, ref: TagRef) -> None:
    if text[ref.start:ref.end] != ref.original:
        raise StaleTarget("the text no longer holds this tag at the place it was chosen")


def replace_tag(text: str, ref: TagRef, new_tag: str) -> str:
    _check(text, ref)
    return text[:ref.start] + new_tag.strip() + text[ref.end:]


def remove_tag(text: str, ref: TagRef) -> str:
    """Remove the tag; when its line holds nothing else the whole line goes with it."""
    _check(text, ref)
    ls = text.rfind("\n", 0, ref.start) + 1
    le = text.find("\n", ref.end)
    le = len(text) if le < 0 else le
    if not (text[ls:ref.start] + text[ref.end:le]).strip():
        return text[:ls] + text[le + 1:] if le < len(text) else text[:ls]      # the line goes with its newline
    return text[:ref.start] + text[ref.end:]
