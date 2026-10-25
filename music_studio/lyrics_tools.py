"""Text edits behind the Lyrics Toolbox: pure functions on (text, selection) -> new text + new selection. Indexes are Python string indexes.

Every tool only inserts markup or, for Hold, stretches the SELECTED word (visibly, so the user can edit it). Nothing here rewords lyrics on its own.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .compiler import TAG_LINE, tag_class_of, HOLD_PREFIX

SECTIONS = ["Intro", "Verse", "Pre-Chorus", "Chorus", "Bridge", "Outro", "Instrumental", "Guitar Solo", "Drum Break"]
# tool key -> (label, phrase, mode); mode: vocal = style phrase, wrap = (parentheses), energy = energy phrase
VOCAL_TOOLS = {
    "whisper": ("Whisper", "whispered", "vocal"), "falsetto": ("Falsetto", "falsetto", "vocal"), "belting": ("Powerful Belting", "powerful belting", "vocal"),
    "spoken": ("Spoken Word", "spoken word", "vocal"), "harmonies": ("Harmonies", "harmonies", "wrap"), "adlib": ("Ad-lib", "ad-lib", "wrap"),
    # inline styles: the [tag] goes exactly where the caret is, on the SAME line (no line break is added, like [Hold]); on a section line they set that section's style instead
    "soft": ("Soft Vocals", "soft vocals", "inline"), "raspy": ("Raspy / Gritty Vocals", "raspy vocals", "inline"), "breathy": ("Breathy Vocals", "breathy vocals", "inline"),
    "choir": ("Choir / Backing Vocals", "choir vocals", "inline"), "emotional": ("Emotional Vocals", "emotional vocals", "inline"), "operatic": ("Operatic Vocals", "operatic vocals", "inline"),
}
OTHER_TOOLS = {"pause": "Pause", "repeat": "Repeat", "linebreak": "Line Break", "build": "Build Energy", "breakdown": "Breakdown"}
_VOCAL_PHRASES = {v[1] for v in VOCAL_TOOLS.values() if v[2] in ("vocal", "inline")}


class ToolError(Exception):
    pass


@dataclass
class Edit:
    text: str
    start: int
    end: int
    message: str = ""


def line_bounds(text: str, idx: int) -> tuple[int, int]:
    idx = max(0, min(idx, len(text)))
    ls = text.rfind("\n", 0, idx) + 1
    le = text.find("\n", idx)
    return ls, (len(text) if le < 0 else le)


def _is_struct_header(line: str) -> bool:
    return bool(TAG_LINE.match(line)) and tag_class_of(line) not in ("cue", "other", "hold")


def find_header(text: str, idx: int) -> tuple[int, int] | None:
    """Bounds of the nearest section header line at or above idx."""
    pos = line_bounds(text, idx)[0]
    while True:
        ls, le = line_bounds(text, pos)
        if _is_struct_header(text[ls:le]):
            return ls, le
        if ls == 0:
            return None
        pos = ls - 1


def insert_section(text: str, pos: int, name: str) -> Edit:
    """The [Section] tag goes on its own line AT the caret (before a selection's start; the selected text is untouched). Text in front of the caret on the same line stays
    above the tag and the rest moves below it; the caret ends up right after the tag's newline, ready for lyrics. Spaces at the cut are dropped, nothing else is."""
    pos = max(0, min(pos, len(text)))
    ls = line_bounds(text, pos)[0]
    label = name
    if name == "Verse":
        n = sum(1 for ln in text.split("\n") if re.match(r"^\s*\[Verse\b", ln, re.IGNORECASE))
        label = f"Verse {n + 1}"
    if text[ls:pos].strip():                                   # text in front of the caret on this line: the tag starts a new line after it
        before, sep = text[:pos].rstrip(" \t"), "\n"
    else:                                                      # caret at the line start, on an empty line or after indentation only: the tag takes this spot
        before, sep = text[:ls], ""
    head = f"{before}{sep}[{label}]\n"
    return Edit(head + text[pos:].lstrip(" \t"), len(head), len(head), f"Inserted [{label}]")


def _split_descriptor(header: str) -> tuple[str, list[str]]:
    inner = header.strip()[1:-1]
    name, _, desc = inner.partition(" - ")
    return name.strip(), [d.strip() for d in desc.split(",") if d.strip()]


def _set_descriptor(text: str, hs: int, he: int, add: str, remove: set[str]) -> Edit:
    name, desc = _split_descriptor(text[hs:he])
    present = add in desc
    desc = [d for d in desc if d not in remove and d != add]
    if not present:
        desc.append(add)
    new_header = f"[{name}" + (f" - {', '.join(desc)}" if desc else "") + "]"
    new = text[:hs] + new_header + text[he:]
    msg = f"Removed “{add}” from {name}" if present else f"{name}: {add}"
    return Edit(new, hs, hs + len(new_header), msg)


def _insert_cue(text: str, pos: int, phrase: str, label: str) -> Edit:
    """A [cue] line at the caret. On an empty line it fills that line; at a line start it goes above it; at the end or in the middle it goes on its own line right there."""
    ls, le = line_bounds(text, pos)
    line = text[ls:le]
    cue = f"[{phrase}]"
    if not line.strip():
        new, p = text[:ls] + cue + text[le:], ls + len(cue)
    elif pos <= ls + (len(line) - len(line.lstrip())):
        new, p = text[:ls] + cue + "\n" + text[ls:], ls + len(cue) + 1
    elif pos >= le:
        new, p = text[:le] + "\n" + cue + text[le:], le + 1 + len(cue)
    else:
        new, p = text[:pos] + "\n" + cue + "\n" + text[pos:], pos + 1 + len(cue) + 1
    return Edit(new, p, p, f"{label}: [{phrase}] cue (experimental)")


def apply_vocal(text: str, start: int, end: int, key: str) -> Edit:
    label, phrase, mode = VOCAL_TOOLS[key]
    if mode == "wrap":
        if start >= end:                                       # no selection: an empty pair at the caret, the caret between the brackets
            new = text[:start] + "()" + text[start:]
            return Edit(new, start + 1, start + 1, f"{label}: type the backing vocal between ( )")
        sel = text[start:end]
        if sel.startswith("(") and sel.endswith(")"):
            new = text[:start] + sel[1:-1] + text[end:]
            return Edit(new, start, end - 2, f"{label} removed")
        new = text[:start] + "(" + sel + ")" + text[end:]
        return Edit(new, start, end + 2, f"{label}: (backing vocal)")
    if mode == "inline":                                       # [tag] right at the caret (before a selection's start), same line, nothing else touched; on a section line: that section's style
        ls, le = line_bounds(text, start)
        if start >= end and _is_struct_header(text[ls:le]):
            return _set_descriptor(text, ls, le, phrase, _VOCAL_PHRASES)
        pos, tag = max(0, min(start, len(text))), f"[{phrase}]"
        if text[:pos].endswith(tag):
            raise ToolError(f"{tag} is already here")
        return Edit(text[:pos] + tag + text[pos:], pos + len(tag), pos + len(tag), f"{label}: {tag} inserted here (a hint for the model; it may not follow it)")
    if start >= end:                                           # caret: on a section line it sets that section's style, anywhere else it inserts a cue line
        ls, le = line_bounds(text, start)
        if _is_struct_header(text[ls:le]):
            return _set_descriptor(text, ls, le, phrase, _VOCAL_PHRASES)
        return _insert_cue(text, start, phrase, label)
    ls, _ = line_bounds(text, start)                           # selection: a cue line before the first selected line
    cue = f"[{phrase}]"
    if ls > 0:
        pls, ple = line_bounds(text, ls - 1)
        if text[pls:ple].strip().lower() == cue:
            raise ToolError(f"{label} is already set on this line")
    new = text[:ls] + cue + "\n" + text[ls:]
    return Edit(new, start + len(cue) + 1, end + len(cue) + 1, f"{label}: cue line before the selected line (experimental)")


def apply_energy(text: str, pos: int) -> Edit:
    ls, le = line_bounds(text, pos)
    if _is_struct_header(text[ls:le]):
        return _set_descriptor(text, ls, le, "building energy", {"high energy", "low energy"})
    return _insert_cue(text, pos, "building energy", "Build Energy")


def _stretch(word: str, n: int = 3) -> str:
    body = word.rstrip()
    tail = word[len(body):]
    if not body:
        return word
    if any("฀" <= c <= "๿" for c in body):
        i = len(body) - 1
        while i > 0 and unicodedata.category(body[i]).startswith("M"):
            i -= 1
        return body + body[i] * n + tail
    for i in range(len(body) - 1, -1, -1):
        if body[i] in "aeiouyAEIOUY":
            return body[:i + 1] + body[i] * n + body[i + 1:] + tail
        if body[i].isspace():
            break
    return body + body[-1] * n + tail


def apply_hold(text: str, start: int, end: int) -> Edit:
    """[Hold] goes exactly where the caret is (before a selection's start), on the same line: nothing is stretched, no line is added, selected text is untouched."""
    pos = max(0, min(start, len(text)))
    if text[:pos].endswith("[Hold]"):
        raise ToolError("[Hold] is already here")
    new = text[:pos] + "[Hold]" + text[pos:]
    return Edit(new, pos + 6, pos + 6, "[Hold] inserted (a hint for a held note: the model may not follow it)")


def insert_text(text: str, pos: int, s: str, msg: str = "") -> Edit:
    new = text[:pos] + s + text[pos:]
    return Edit(new, pos + len(s), pos + len(s), msg)


def repeat_lines(text: str, start: int, end: int) -> Edit:
    ls, _ = line_bounds(text, start)
    _, le = line_bounds(text, max(start, end - 1) if end > start else start)
    block = text[ls:le]
    if not block.strip():
        raise ToolError("Nothing to repeat on this line")
    new = text[:le] + "\n" + block + text[le:]
    return Edit(new, le + 1, le + 1 + len(block), "Repeated the line(s)")


def apply_tool(key: str, text: str, start: int, end: int) -> Edit:
    """One entry point for the toolbox. key: a section name, a VOCAL_TOOLS key, 'hold' or an OTHER_TOOLS key."""
    if key in SECTIONS:
        return insert_section(text, start, key)
    if key in VOCAL_TOOLS:
        return apply_vocal(text, start, end, key)
    if key == "hold":
        return apply_hold(text, start, end)
    if key == "pause":
        return insert_text(text, start if start == end else end, "...", "Pause (…)")
    if key == "repeat":
        return repeat_lines(text, start, end)
    if key == "linebreak":
        return insert_text(text, start, "\n", "Line break")
    if key == "build":
        return apply_energy(text, start)
    if key == "breakdown":
        return insert_section(text, start, "Breakdown")
    raise ToolError(f"Unknown tool {key}")
