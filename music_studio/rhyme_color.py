"""Rhyme Coloring: turns a RhymeResult (the analysis that already exists) into exact word positions in the lyrics and one colour per rhyme group.

Nothing here asks an AI and nothing here changes the lyrics. A word is never found by guessing: the pair says WHICH line and WHICH word, and the position is chosen by the role the
rhyme type gives it (end = the last word of the line, cross = the earlier line's last word and a word inside the next line, internal / near = a word of that line). When a word sits in
its line more than once and the role does not decide which one, the pair is left uncoloured and the reason is kept (`skipped`), because colouring the wrong word is worse than none.

Offsets are Python character offsets into the lyrics text. For Thai they are also the UTF-16 offsets a Flet text field uses (every Thai character is one UTF-16 unit); `utf16` converts
a position for text that has characters outside the BMP.
A result is only coloured while the sung lines are exactly the ones that were analysed; otherwise `stale` is set and nothing is coloured.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .compiler import HOLD_PREFIX, TAG_LINE
from .lyric_analysis import _INLINE_TAG, sung_lines

PALETTE = ("#60A5FA", "#F472B6", "#34D399", "#FBBF24", "#A78BFA", "#FB923C", "#22D3EE", "#F87171")
_TRAIL = re.compile(r"[\s.,!?:;…\"'”’]+$")
_EXACT = ("end", "cross", "internal")
VERIFIED, UNVERIFIED = "verified", "unverified"
_RANK = {"verified": 0, "llm_exact": 0, "llm_near": 1, "unverified": 2, "uncertain": 2, "rejected": 3}      # the weakest assessment wins on a word that belongs to several pairs                         # here: the AI was sure / the AI was not sure (nothing in this module listens to a sound)


@dataclass
class Span:
    file_line: int                  # 1.. in the lyrics text
    start: int                      # offsets in the whole lyrics text
    end: int
    text: str
    group: int = -1                 # index into RhymeColors.groups (also the colour index)
    verify: str = ""                # the weakest assessment of every pair that touches this word (see _RANK)
    kind: str = "near"              # exact when any pair that touches it is end / cross / internal


@dataclass
class RhymeColors:
    spans: list = field(default_factory=list)       # sorted by position
    groups: list = field(default_factory=list)      # each: list of Span
    skipped: list = field(default_factory=list)     # one message per pair that could not be placed exactly
    stale: bool = False

    def color_of(self, span: Span) -> str:
        return PALETTE[span.group % len(PALETTE)]


def utf16(text: str, pos: int) -> int:
    """The UTF-16 offset of character offset `pos` (what Flet's selection uses)."""
    return len(text[:pos].encode("utf-16-le")) // 2


def _mask(raw: str) -> str:
    """The editor line with a leading [Hold] and every inline [tag] replaced by spaces of the same length, so offsets in it are offsets in the editor line."""
    lead = len(raw) - len(raw.lstrip())
    m = HOLD_PREFIX.match(raw[lead:])
    s = raw
    if m:
        s = s[:lead] + " " * m.end() + s[lead + m.end():]
    return _INLINE_TAG.sub(lambda t: " " * len(t.group(0)), s)


def _clean(masked: str) -> str:
    return re.sub(r"\s{2,}", " ", masked).strip()


def _occurrences(masked: str, core_end: int, word: str) -> list:
    """[(start, end, status)] of `word` in the masked line. Thai has no spaces, so any occurrence counts; another script must stand as a whole word."""
    out = []
    if not word:
        return out
    thai = bool(re.search(r"[\u0E00-\u0E7F]", word))
    hay, needle = (masked, word) if thai else (masked.lower(), word.lower())
    if len(hay) != len(masked):
        return out
    i = hay.find(needle)
    while i >= 0:
        j = i + len(needle)
        if thai or ((hay[i - 1] if i > 0 else " ").isalpha() is False and (hay[j] if j < len(hay) else " ").isalpha() is False):
            out.append((i, j, "ok"))
        i = hay.find(needle, i + 1)
    return out


class _Line:
    def __init__(self, file_line: int, start: int, raw: str):
        self.file_line, self.start, self.raw = file_line, start, raw
        self.masked = _mask(raw)
        self.core_end = len(_TRAIL.sub("", self.masked))           # the end of the line without trailing space / punctuation


def _pick(line: _Line, word: str, role: str, taken: "tuple | None" = None):
    """((start, end) in the editor line, '') or (None, reason). `role`: end | inner | any."""
    found = _occurrences(line.masked, line.core_end, word)
    if role == "end":
        found = [f for f in found if f[1] == line.core_end]
    elif role == "inner":
        found = [f for f in found if f[1] != line.core_end]
    if taken:
        found = [f for f in found if f[1] <= taken[0] or f[0] >= taken[1]]
    if not found:
        return None, f"“{word}” was not found where the pair needs it in line {line.file_line}."
    if len(found) > 1:
        good = [f for f in found if f[2] == "ok"]
        if len(good) == 1:
            found = good
    if len(found) > 1:
        return None, f"“{word}” appears {len(found)} times in line {line.file_line} and the rhyme type does not say which one, so it was not coloured."
    return (found[0][0], found[0][1]), ""


def colorize(lyrics: str, result=None) -> RhymeColors:
    """The rhyme groups of `lyrics`: (1) Thai line endings that end in the same sound, found here from the sound of each line's last syllable, always for the current lyrics;
    (2) the AI's pairs of `result` (internal, cross-line, near ...) when `result` is for exactly these lyrics, and only the Thai ones whose sound Python confirmed."""
    rc = RhymeColors()
    if "\r" in lyrics or unicodedata.normalize("NFC", lyrics) != lyrics:
        rc.stale = result is not None
        rc.skipped.append("The lyrics contain characters that cannot be located exactly, so nothing was coloured.")
        return rc
    sung = sung_lines(lyrics)
    use_result = result is not None
    if use_result and [ln.text for ln in sung] != list(result.lines):
        rc.stale, use_result = True, False
    raw_lines = lyrics.split("\n")
    starts, pos = [], 0
    for r in raw_lines:
        starts.append(pos)
        pos += len(r) + 1
    lines = {}
    for ln in sung:
        L = _Line(ln.file_line, starts[ln.file_line - 1], raw_lines[ln.file_line - 1])
        if _clean(L.masked) != ln.text:
            rc.skipped.append(f"Line {ln.file_line} could not be matched to the analysed text, so it was not coloured.")
            continue
        lines[ln.num] = L
    spans: dict = {}                                                # (file_line, start, end) -> Span
    parent: dict = {}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def span(L, a, b, verify, kind):
        k = (L.file_line, L.start + a, L.start + b)
        sp = spans.get(k)
        if sp is None:
            sp = spans[k] = Span(L.file_line, k[1], k[2], lyrics[k[1]:k[2]])
            parent[k] = k
        if _RANK.get(verify, 2) > _RANK.get(sp.verify, -1):
            sp.verify = verify
        if kind in _EXACT and verify != "llm_near":
            sp.kind = "exact"
        return k

    # the AI's pairs
    for p in (result.pairs if use_result else []):
        la, lb = lines.get(p.a_line), lines.get(p.b_line)
        if la is None or lb is None:
            rc.skipped.append(f"{p.a_word} ↔ {p.b_word}: a line of this pair could not be located.")
            continue
        if p.type == "end":
            ra, rb = "end", "end"
        elif p.type == "cross":
            ra, rb = ("end", "inner") if p.a_line < p.b_line else ("inner", "end")
        else:
            ra = rb = "any"
        ga, why = _pick(la, p.a_word, ra)
        if ga is None:
            rc.skipped.append(f"{p.a_word} ↔ {p.b_word}: {why}")
            continue
        gb, why = _pick(lb, p.b_word, rb, taken=ga if la is lb else None)
        if gb is None:
            rc.skipped.append(f"{p.a_word} ↔ {p.b_word}: {why}")
            continue
        keys = [span(L, a, b, p.verify, p.type) for L, (a, b) in ((la, ga), (lb, gb))]
        if keys[0] == keys[1]:
            continue
        parent[find(keys[0])] = find(keys[1])
    members: dict = {}
    for k, sp in spans.items():
        members.setdefault(find(k), []).append(sp)
    groups = sorted((sorted(g, key=lambda s: s.start) for g in members.values()), key=lambda g: g[0].start)      # colour order follows the song, so it is the same every time
    for gi, g in enumerate(groups):
        for sp in g:
            sp.group = gi
    rc.groups = groups
    rc.spans = sorted(spans.values(), key=lambda s: s.start)
    return rc


def related(rc: RhymeColors, start: int, end: int) -> list:
    """The words that rhyme with a word inside [start, end): [(partner Span, the Span inside the range)]."""
    out = []
    for sp in rc.spans:
        if sp.start >= start and sp.end <= end:
            out += [(o, sp) for o in rc.groups[sp.group] if o is not sp and not (o.start >= start and o.end <= end)]
    return out


def line_segments(lyrics: str, rc: RhymeColors, hide_tags: bool = True) -> list:
    """[(file_line, [(text, Span | None), ...])] for every line of the lyrics: the text as written, cut at the coloured words. `hide_tags`: a sung line is shown without its [Hold] and
    other inline [tags] (they are not lyrics); a whole-line [section tag] stays."""
    by_line: dict = {}
    for sp in rc.spans:
        by_line.setdefault(sp.file_line, []).append(sp)
    out, pos = [], 0
    for n, raw in enumerate(lyrics.split("\n"), 1):
        segs, at = [], pos
        for sp in by_line.get(n, []):
            if sp.start > at:
                segs.append((lyrics[at:sp.start], None))
            segs.append((lyrics[sp.start:sp.end], sp))
            at = sp.end
        if at < pos + len(raw):
            segs.append((lyrics[at:pos + len(raw)], None))
        if hide_tags and not TAG_LINE.match(raw):
            segs = [((_INLINE_TAG.sub("", t) if sp is None else t), sp) for t, sp in segs]
            if segs and segs[0][1] is None:
                segs[0] = (segs[0][0].lstrip(), None)
            if segs and segs[-1][1] is None:
                segs[-1] = (segs[-1][0].rstrip(), None)
            segs = [(t, sp) for t, sp in segs if t or sp is not None]
        out.append((n, segs))
        pos += len(raw) + 1
    return out


def is_tag_line(raw: str) -> bool:
    return bool(TAG_LINE.match(raw))
