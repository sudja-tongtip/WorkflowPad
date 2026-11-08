"""Thai live and dead syllables (คำเป็น / คำตาย), per syllable, from the lyrics as they are now. Python only: no LLM, no change to the lyrics, no verdict on the song.

A syllable is classified by how it is SPOKEN, never by the last letter written. The reading of the sound is the one the rhyme rules already use (`thai_rhyme._rime`: the vowel and the final-sound class of a syllable);
the syllables and their offsets are those of Live Insights (`live_insights.line_core`: PyThaiNLP words and their dictionary syllables), so nothing is tokenized twice.

  live   a long vowel with no stopped final, or a final that is sounded m / n / ng / y / w (the classes กม กน กง เกย เกอว), or a vowel that carries a final sound (อำ ไอ ใอ เอา)
  dead   a stopped final k / t / p (กก กด กบ), or a short vowel with no final sound
  unsure the syllable could not be separated or its sound could not be read with confidence. It is NOT forced into either side; the reason is kept in `note`.

A syllable counts as "confident" only when the dictionary gave it as one piece that the syllable validator accepts (a letter silenced by ์ is looked past, so สิทธิ์ is read as สิท) and the sound of the
written form and of the spoken form agree. Everything else is `unsure`. Non-Thai words (English, numbers) are counted as `other`, never classified.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from . import rhyme_map as RM
from . import thai_rhyme as TR

LIVE, DEAD, UNSURE = "live", "dead", "unsure"
MIN_PYTHAINLP = (5, 4, 0)       # pythainlp 5.3.x reads the vowel of many syllables wrongly (an open Cอ such as พอ ทอ ขอ as short โอะ, ตัว หัว, เลย เคย, เป็น เร็ว เล็ก ...): 5.4.0 is the first version checked correct
_SHORT = frozenset({"อะ", "อิ", "อึ", "อุ", "เอะ", "แอะ", "โอะ", "เอาะ", "เออะ", "เอียะ", "เอือะ", "อัวะ"})     # the short vowels the sound reader names (the second of each length pair, plus เออะ)
_LONG = frozenset({"อา", "อี", "อือ", "อู", "เอ", "แอ", "โอ", "ออ", "เออ", "เอีย", "เอือ", "อัว"})
_CARRY = frozenset({"อำ", "ไอ", "ใอ", "เอา"})                          # written without a final, but sounded with one (m, y, w): live
_LIVE_FINAL = frozenset({"กง", "กน", "กม", "เกย", "เกอว"})
_DEAD_FINAL = TR._STOP                                                  # กก กด กบ
_SILENT = re.compile("[ก-ฮ][ัิ-ฺ็-๋]*์")                                # a consonant (with its vowel / tone marks) silenced by the thanthakhat mark


def _spoken(syl: str) -> str:
    """The syllable without the letters that are silenced by ์ (สิทธิ์ -> สิท). Orthography, not a word list."""
    out = _SILENT.sub("", syl)
    return out or syl


@lru_cache(maxsize=8192)
def classify(syl: str) -> tuple:
    """(class, (vowel, final) or None, note) of ONE written syllable."""
    s = unicodedata.normalize("NFC", syl or "")
    if not s or not TR.has_thai(s):
        return UNSURE, None, "not a Thai syllable"
    spoken = _spoken(s)
    if not TR.is_single_syllable(spoken):
        return UNSURE, None, "not confirmed as one syllable"
    r = TR._rime(s)
    r2 = TR._rime(spoken) if spoken != s else r
    if r is None or r2 is None:
        return UNSURE, None, "the sound could not be read"
    if r != r2:
        return UNSURE, r, "the written and the spoken form are read differently"
    vowel, final = r
    if final in _DEAD_FINAL:
        return DEAD, r, "stopped final"
    if final in _LIVE_FINAL:
        return LIVE, r, "sounded final"
    if final == "กา":
        if vowel in _CARRY:
            return LIVE, r, "vowel with a final sound"
        if vowel in _LONG:
            return LIVE, r, "long vowel, no stopped final"
        if vowel in _SHORT:
            return DEAD, r, "short vowel, no final sound"
    return UNSURE, r, "the vowel / final class is not one the rules cover"


@dataclass
class Syl:
    line: int
    start: int                  # offsets in the whole lyrics text (Unicode code points, as the rhyme colours use)
    end: int
    text: str
    cls: str                    # live | dead | unsure
    rime: "tuple | None" = None
    note: str = ""


@dataclass
class LineLD:
    line: int
    syls: list = field(default_factory=list)
    live: int = 0
    dead: int = 0
    unsure: int = 0
    other: int = 0              # words that are not Thai (English, numbers): not classified

    @property
    def total(self) -> int:
        return self.live + self.dead + self.unsure


@dataclass
class SectionLD:
    name: str
    first_line: int
    live: int = 0
    dead: int = 0
    unsure: int = 0
    other: int = 0

    @property
    def total(self) -> int:
        return self.live + self.dead + self.unsure


@dataclass
class LDResult:
    text_hash: str
    lines: dict = field(default_factory=dict)           # file line -> LineLD
    sections: list = field(default_factory=list)        # SectionLD, in the song's order
    live: int = 0
    dead: int = 0
    unsure: int = 0
    other: int = 0
    diagnostics: list = field(default_factory=list)     # one message per word whose syllables were not separated with confidence
    reader_warning: str = ""                            # the installed pythainlp misreads vowels (see reader_warning()): shown above the lines

    @property
    def total(self) -> int:
        return self.live + self.dead + self.unsure


def _pieces(tok: tuple, diag: list, line: int) -> list:
    """[(offset in the line, text)] of the syllables of ONE Thai token, or [(0.., whole token)] marked unsure by the caller when they cannot be separated with confidence."""
    a, _b, text, _k, syls = tok
    if len(syls) == 1:
        return [(a, text, "")]
    if "".join(syls) != text:
        diag.append(f"line {line}: the syllables of “{text}” do not add up to the word, so it is not classified.")
        return [(a, text, "the syllable split does not add up to the word")]
    out, at = [], a
    for sy in syls:
        if sy != "ๆ":                                              # the repeat mark is not a syllable
            out.append((at, sy, ""))
        at += len(sy)
    for i in range(1, len(out)):                                    # a boundary in front of a tone / vowel mark would cut the mark off its syllable
        if unicodedata.category(out[i][1][0]) == "Mn":
            diag.append(f"line {line}: a syllable boundary in “{text}” would cut a tone or vowel mark off, so it is not classified.")
            return [(a, text, "a syllable boundary cuts a mark")]
    return out


def analyze_text(text: str, ins) -> LDResult:
    """The classification of every sung line of `ins` (the Live Insights result for exactly `text`). Uses the cached tokens of Live Insights, so PyThaiNLP is not run again."""
    from . import live_insights as LI
    res = LDResult(ins.text_hash)
    res.reader_warning = reader_warning()
    raw_lines = (text or "").split("\n")
    starts = [0]
    for ln in raw_lines:
        starts.append(starts[-1] + len(ln) + 1)
    secs = [SectionLD(s.name, s.first_line) for s in ins.sections]
    for li in ins.lines:
        n = li.line_number
        if not 1 <= n <= len(raw_lines):
            continue
        raw = raw_lines[n - 1]
        row = LineLD(n)
        for tok in LI.line_core(RM.mask(raw))["tokens"]:
            if tok[3] != "th":
                row.other += 1
                continue
            for off, sy, why in _pieces(tok, res.diagnostics, n):
                cls, rime, note = (UNSURE, None, why) if why else classify(sy)
                row.syls.append(Syl(n, starts[n - 1] + off, starts[n - 1] + off + len(sy), sy, cls, rime, note))
                if cls == LIVE:
                    row.live += 1
                elif cls == DEAD:
                    row.dead += 1
                else:
                    row.unsure += 1
                    if note and not why:
                        res.diagnostics.append(f"line {n}: “{sy}” is not classified ({note}).")
        res.lines[n] = row
        res.live += row.live
        res.dead += row.dead
        res.unsure += row.unsure
        res.other += row.other
        if 0 <= li.sec_index < len(secs):
            s = secs[li.sec_index]
            s.live += row.live
            s.dead += row.dead
            s.unsure += row.unsure
            s.other += row.other
    res.sections = secs
    return res


_cache: dict = {}


def reader_warning() -> str:
    """'' when the installed pythainlp reads syllables correctly; otherwise one sentence for the user. The sound reader is the library's (`khavee`): a version that misreads vowels would show wrong colours and
    wrong rhyme sounds with no error, so the program says it (nothing is corrected by a second reader here)."""
    try:
        import pythainlp
        ver = str(pythainlp.__version__)
        nums = tuple(int(x) for x in re.findall(r"\d+", ver)[:3])
    except Exception:
        return ""
    if nums and nums < MIN_PYTHAINLP:
        return (f"pythainlp {ver} reads the vowel of some syllables wrongly (for example open อ syllables such as พอ ทอ ขอ), so some live / dead labels and rhyme sounds can be wrong. "
                f"Update it: pip install -U \"pythainlp>=" + ".".join(map(str, MIN_PYTHAINLP)) + "\"")
    return ""


def analyze(text: str, ins) -> LDResult:
    """`analyze_text`, cached for the latest few results by the text hash of `ins` (an old text never returns a result made for another)."""
    key = (ins.text_hash, len(text or ""))
    hit = _cache.get(key)
    if hit is not None:
        return hit
    res = analyze_text(text, ins)
    if len(_cache) > 6:
        _cache.clear()
    _cache[key] = res
    return res


def paint_segments(text: str, res: LDResult) -> list:
    """[(file line, [(text, Syl | None)])]: every line of the lyrics cut at the classified syllables. The text is shown exactly as written; a syllable is painted only when it is that exact slice."""
    out, pos = [], 0
    for n, raw in enumerate((text or "").split("\n"), 1):
        base, cur, segs = pos, 0, []
        pos += len(raw) + 1
        row = res.lines.get(n)
        for sy in (row.syls if row else []):
            a, b = sy.start - base, sy.end - base
            if a < cur or b > len(raw) or raw[a:b] != sy.text:
                continue
            if a > cur:
                segs.append((raw[cur:a], None))
            segs.append((raw[a:b], sy))
            cur = b
        if cur < len(raw):
            segs.append((raw[cur:], None))
        out.append((n, segs))
    return out
