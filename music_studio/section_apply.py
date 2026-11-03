"""How a proposal or an edit lands inside a section.

A model often sends the section header back with the new lyric lines (`[Breakdown]` plus a new
body). That header is already in the song. Writing it again adds a second section. These functions
decide the range and the text to write:

- lyric lines only: the header that echoes the section around the range is removed, and the song's
  own header stays
- the whole section: one header and the new lines replace that section, inside its bounds
- a header the song does not have at that spot: it is a new section and it is kept
- a Chorus the song already repeats stays, because nothing outside the replaced range is rewritten

Nothing here reads a song file or calls a model.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .compiler import HOLD_PREFIX, TAG_LINE
from . import tag_parser as TP


def section_name(tag: str) -> str:
    """'breakdown' for `[Breakdown]` and for `[Breakdown - soft]`."""
    inner = (tag or "").strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    return re.sub(r"\s+", " ", TP.parse_tag(inner).name).strip().lower()


def _is_section_header(line: str) -> bool:
    return bool(TAG_LINE.match(line or "")) and not HOLD_PREFIX.match(line or "")


def _lines_at(lyrics: str) -> list:
    """(start, end, line) for every line. `end` does not include the newline."""
    rows, pos = [], 0
    lines = (lyrics or "").split("\n")
    for i, line in enumerate(lines):
        rows.append((pos, pos + len(line), line))
        pos += len(line) + (1 if i < len(lines) - 1 else 0)
    return rows


def section_covering(lyrics: str, start: int, end: int):
    """The section that contains the range, or None when the range is before every header.

    `sec_end` is the start of the next section header, or the end of the text. The header offsets
    are the header line only.
    """
    if not lyrics:
        return None
    start = max(0, min(int(start), len(lyrics)))
    end = max(start, min(int(end), len(lyrics)))
    rows = _lines_at(lyrics)
    if not rows:
        return None
    idx = len(rows) - 1
    for i, (a, b, _line) in enumerate(rows):
        if a <= start <= b:
            idx = i
            break
    h = None
    for i in range(idx, -1, -1):
        if _is_section_header(rows[i][2]):
            h = i
            break
    if h is None:
        return None
    header_start, header_end, header_line = rows[h]
    if end <= header_start and start < header_start:
        return None
    nxt = next((i for i in range(h + 1, len(rows)) if _is_section_header(rows[i][2])), None)
    sec_end = rows[nxt][0] if nxt is not None else len(lyrics)
    return {
        "header_start": header_start,
        "header_end": header_end,
        "sec_end": sec_end,
        "header": header_line.strip(),
        "name": section_name(header_line),
    }


def _headers(text: str) -> list:
    return [line.strip() for line in (text or "").split("\n") if _is_section_header(line)]


def _without_section(text: str, name: str) -> str:
    """Drop every header of `name`. Lyric lines and any other section stay."""
    kept = []
    for line in (text or "").split("\n"):
        if _is_section_header(line) and section_name(line) == name:
            continue
        kept.append(line)
    return "\n".join(kept).strip("\n")


def _one_header(text: str, name: str) -> str:
    """Keep the first header of `name` and drop the later copies. Other headers stay."""
    seen = False
    kept = []
    for line in (text or "").split("\n"):
        if _is_section_header(line) and section_name(line) == name:
            if seen:
                continue
            seen = True
        kept.append(line)
    return "\n".join(kept).strip("\n")


def _drop_leading(text: str, name: str) -> str:
    """Drop blank lines and headers of `name` at the start. The first other line stays."""
    lines = (text or "").split("\n")
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        if _is_section_header(lines[i]) and section_name(lines[i]) == name:
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip("\n")


def prepare_replacement(lyrics: str, start: int, end: int, repl: str) -> tuple:
    """(start, end, text to write). The range is the caller's range. Only the text changes.

    An echoed header of the section around the range is not written again. A different header is
    a new section and is kept. Replacing the header itself keeps one header.
    """
    repl = (repl or "").strip("\n")
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return 0, 0, repl
    if start < 0 or end < start or end > len(lyrics or ""):
        return start, end, repl
    sec = section_covering(lyrics, start, end)
    names = [section_name(h) for h in _headers(repl)]
    if sec is None or not names:
        return start, end, repl
    own = sec["name"]
    includes = start <= sec["header_start"] < end
    if own not in names:
        return start, end, repl
    if includes:
        return start, end, _one_header(repl, own)
    return start, end, _without_section(repl, own)


def strip_enclosing_header(lyrics: str, pos: int, repl: str) -> str:
    """New lines inserted at `pos`. A leading copy of the section already around `pos` is removed.

    A header of a different section stays, so a real new section can still be inserted.
    """
    repl = (repl or "").strip("\n")
    try:
        pos = int(pos)
    except (TypeError, ValueError):
        return repl
    sec = section_covering(lyrics or "", pos, pos)
    if sec is None:
        return repl
    return _drop_leading(repl, sec["name"])


def headers_for_tag_check(original: str, proposal: str) -> str:
    """A copy of `original` whose headers use the proposal's wording when a name occurs the same number of times.

    `[Breakdown]` and `[Breakdown - soft]` are the same section, so a cue change is not a dropped tag.
    A name the proposal repeats a different number of times is left as it is, and a dropped Chorus stays visible.
    """
    orig_lines = (original or "").split("\n")
    prop_lines = (proposal or "").split("\n")

    def heads(lines: list) -> list:
        return [(i, section_name(lines[i])) for i, line in enumerate(lines) if _is_section_header(line)]

    orig_h, prop_h = heads(orig_lines), heads(prop_lines)
    orig_n = Counter(name for _i, name in orig_h)
    prop_n = Counter(name for _i, name in prop_h)
    by_name = defaultdict(list)
    for i, name in prop_h:
        by_name[name].append(prop_lines[i].strip())
    out, used = list(orig_lines), defaultdict(int)
    for i, name in orig_h:
        if orig_n[name] != prop_n.get(name, 0):
            continue
        k = used[name]
        used[name] = k + 1
        out[i] = by_name[name][k]
    return "\n".join(out)


def outside_headers(lyrics: str, start: int, end: int) -> list:
    """Section headers that sit completely outside [start, end). Order is the song's order."""
    out = []
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return out
    for a, b, line in _lines_at(lyrics or ""):
        if _is_section_header(line) and (b <= start or a >= end):
            out.append(line.strip())
    return out


def _body_key(body: list) -> tuple:
    return tuple(x.strip() for x in body if x.strip())


def _blocks(text: str) -> list:
    """[(header or None, [lines])], same cuts as the chat merger: a header line starts a section."""
    out, cur = [], [None, []]
    for line in (text or "").split("\n"):
        if _is_section_header(line):
            out.append(cur)
            cur = [line.strip(), []]
        else:
            cur[1].append(line)
    out.append(cur)
    return [(t, b) for t, b in out if t is not None or any(x.strip() for x in b)]


def _join_blocks(blocks: list) -> str:
    lines = []
    for tag, body in blocks:
        if tag is not None:
            lines.append(tag)
        lines.extend(body)
    return "\n".join(lines).rstrip("\n")


def fold_echoed_sections(original: str, proposal: str) -> str:
    """The proposal with an echoed section removed, or the proposal itself when nothing was echoed.

    A section the proposal repeats more times than the song, and whose body is the song's body, is
    the old section sent back beside the new lines. That copy is removed until the counts match.
    A section the song already repeats (two Choruses) is not removed, because the extra count is
    zero. A section name the song does not have is kept. A new copy whose body is not the old body
    is kept too.
    """
    mine, theirs = _blocks(original), _blocks(proposal)
    if not theirs or not any(tag for tag, _b in theirs):
        return proposal or ""
    orig_bodies = defaultdict(list)
    for tag, body in mine:
        if tag:
            orig_bodies[section_name(tag)].append(_body_key(body))
    orig_n = Counter(section_name(tag) for tag, _b in mine if tag)
    prop_n = Counter(section_name(tag) for tag, _b in theirs if tag)
    drop = set()
    for name, count in prop_n.items():
        extra = count - orig_n.get(name, 0)
        if extra <= 0:
            continue
        left = list(orig_bodies.get(name, []))
        echoes = []
        for i, (tag, body) in enumerate(theirs):
            if not tag or section_name(tag) != name:
                continue
            key = _body_key(body)
            if key in left:
                echoes.append(i)
                left.remove(key)
        drop.update(echoes[:extra])
    if not drop:
        return proposal or ""
    return _join_blocks(block for i, block in enumerate(theirs) if i not in drop)
