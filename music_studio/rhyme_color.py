"""Rhyme Coloring: turns the Shared Rhyme Map (rhyme_map.py) into exact word positions in the lyrics and one colour per rhyme group.

Nothing here asks an AI and nothing here changes the lyrics. The map holds the rhymes the Python rules found in these lyrics (verified Thai sounds, always for the current text) and, only
while the AI's Analyze Rhymes result is for exactly these sung lines, that result's pairs with their own statuses (a Python-verified pair outranks an AI assessment of the same pair; the two
are never merged into a stronger claim). A word is coloured only on evidence: a verified or AI-assessed vowel rhyme whose sound is exact or near. A pair that was rejected, one that only
has a spelling estimate (English), one that only shares a first consonant, and the pairs of unrelated sections are never coloured. When the AI's result is for other lyrics, `stale` is set and
none of it is used.

Offsets are Python character offsets into the lyrics text. For Thai they are also the UTF-16 offsets a Flet text field uses (every Thai character is one UTF-16 unit); `utf16` converts
a position for text that has characters outside the BMP. A word is never found by guessing: the pair says WHICH line and WHICH word, and the position is chosen by the role its rhyme type
gives it; when that does not decide which occurrence is meant, the word is left uncoloured and the reason is kept (`skipped`).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import live_insights as LI
from . import rhyme_map as RM
from .compiler import HOLD_PREFIX, TAG_LINE  # noqa: F401
from .lyric_analysis import _INLINE_TAG
from .rhyme_map import PALETTE, utf16  # noqa: F401  (utf16 is part of this module's interface)

VERIFIED, UNVERIFIED = "verified", "unverified"
_RANK = {"verified": 0, "llm_exact": 0, "llm_near": 1, "unverified": 2, "uncertain": 2, "rejected": 3}      # the weakest assessment wins on a word that belongs to several links


@dataclass
class Span:
    file_line: int                  # 1.. in the lyrics text
    start: int                      # offsets in the whole lyrics text
    end: int
    text: str
    group: int = -1                 # index into RhymeColors.groups (also the colour index)
    verify: str = ""                # the weakest assessment of every link that touches this word, in the window's words (see _RANK)
    kind: str = "near"              # exact when any link that touches it has an exact sound
    sound: str = ""                 # exact | near: the same as `kind`, in the Shared Rhyme Map's words
    placement: str = ""             # end | internal | cross (the strongest link)
    verification: str = ""          # the weakest verification of the links that touch this word: verified | ai_assessed | unverified
    source: str = ""                # python | llm | python+llm


@dataclass
class RhymeColors:
    spans: list = field(default_factory=list)       # sorted by position
    groups: list = field(default_factory=list)      # each: list of Span
    skipped: list = field(default_factory=list)     # one message per pair that could not be placed exactly
    stale: bool = False
    map: "RM.RhymeMap | None" = None                # the Shared Rhyme Map these colours come from

    def color_of(self, span: Span) -> str:
        return PALETTE[span.group % len(PALETTE)]


def _old_verify(link: "RM.Link") -> str:
    """The window's single status word for a link (what the cards and the highlights already use)."""
    if link.verification == "verified":
        return "verified"
    if link.verification == "ai_assessed":
        return "llm_near" if link.sound == "near" else "llm_exact"
    return "uncertain" if link.sound == "uncertain" else "unverified"


def colorize(lyrics: str, result=None, language: str = "") -> RhymeColors:
    """The rhyme groups of `lyrics`: the Shared Rhyme Map's colourable links. `result` is the AI's Analyze Rhymes (lyric_rhyme.RhymeResult) or None; `language` is the song's lyrics language."""
    m = LI.rhyme_map(lyrics or "", result, language)
    rc = RhymeColors(skipped=list(m.skipped), stale=m.stale, map=m)
    spans: dict = {}                                # (start, end) -> Span
    touching: dict = {}
    for k in m.links:
        if not k.colourable:
            continue
        for w in (k.a, k.b):
            sp = spans.get(w.span)
            if sp is None:
                sp = spans[w.span] = Span(w.file_line, w.start, w.end, w.text)
                touching[w.span] = []
            touching[w.span].append(k)
    for key, sp in spans.items():
        ks = touching[key]
        weakest = max(ks, key=lambda k: _RANK.get(_old_verify(k), 2))
        sp.verify = _old_verify(weakest)
        sp.verification = weakest.verification
        sp.kind = "exact" if any(k.sound == "exact" for k in ks) else "near"
        sp.sound = sp.kind
        sp.placement = next((p for p in ("end_to_end", "end", "end_to_internal", "cross_line", "cross_section", "cross", "internal") if any(k.placement == p for k in ks)), "")
        srcs = sorted({k.source for k in ks})
        sp.source = "+".join(srcs)
    words = {}
    for gi, g in enumerate(RM.groups(m.links)):
        for w in g:
            sp = spans.get(w.span)
            if sp is not None:
                sp.group = gi
                words.setdefault(gi, []).append(sp)
    rc.groups = [words[i] for i in sorted(words)]
    rc.spans = sorted((sp for sp in spans.values() if sp.group >= 0), key=lambda s: s.start)
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
            if lyrics[sp.start:sp.end] != sp.text or sp.start < at or sp.end > pos + len(raw):
                continue
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
