"""Live Lyrics Insights: the numbers that can be worked out from the lyrics and the settings alone, with Python and PyThaiNLP (no LLM, no network).

  analyze(text, corrections)   per sung line: words, syllables, syllable segments, a rhyme group for the words that rhyme, a verification status and warnings
  timing(ins, ...)             the vocal density of every line and section and the song length, from BPM, time signature, target duration and a bar allocation
  songwriter_block / producer_block   the same facts as plain text for the Analyze Song request (nothing here is read back from an AI)

Everything is read-only: nothing here changes the lyrics, the settings or an analysis result of the AI.

Honesty rules (the brief's data integrity): every line says HOW SURE its numbers are.
  manual_corrected  the user's own correction of the syllables (still valid for exactly this line and its neighbours)
  verified          Thai only, every word is in the dictionary, and every dictionary piece is one syllable the spelling rules accept (still not a recording)
  estimated         a word is English, a number or not in the dictionary: the count is a rule-based estimate
  unverified        nothing could be counted
A rhyme is only coloured when the Thai sound check (thai_rhyme: vowel + final consonant of the last syllable) says exact or near. A word whose sound cannot be read is never coloured and never called a rhyme.
Duration is an ESTIMATE from stated assumptions (bars per line, bars of an instrumental section, the meaning of BPM in 6/8); no melody or recording is known.
"""
from __future__ import annotations

import functools
import hashlib
import re
import zlib
from dataclasses import dataclass, field

from . import lyric_analysis as LA
from . import rhyme_map as RM
from . import thai_rhyme as TR
from .compiler import HOLD_SUFFIX, TAG_LINE, section_label

PALETTE = RM.PALETTE
TH = re.compile(r"[฀-๿]+")
_WORD = re.compile(r"[฀-๿]+|[A-Za-z]+(?:['’][A-Za-z]+)*|\d+")
INSTRUMENTAL = ("intro", "outro", "instrumental", "interlude", "solo", "break", "bridge instrumental")
FUNCTION_WORDS = RM.FUNCTION_WORDS                                          # common one-syllable words are not coloured as INTERNAL rhymes (they would colour every line); at the end of a line they still count
_thai_dict = None


def _dictionary() -> frozenset:
    global _thai_dict
    if _thai_dict is None:
        try:
            from pythainlp.corpus.common import thai_words
            _thai_dict = frozenset(thai_words())
        except Exception:
            _thai_dict = frozenset()
    return _thai_dict


# ── data ──────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class RhymeSpan:
    line: int                 # file line (1..)
    start: int                # offsets in the whole lyrics text
    end: int
    text: str
    group: str                # the sound it belongs to, e.g. "โอะ+กน"
    color: int                # index into PALETTE: the same sound always has the same colour
    kind: str                 # exact | near
    role: str                 # end | internal


@dataclass
class LineInsight:
    line_number: int          # file line (1..)
    sung_number: int          # 1.. among the sung lines
    section: str
    text: str                 # the sung text (tags removed)
    text_hash: str
    word_count: int
    syllable_count: int
    syllable_segments: list   # the syllables, in order
    status: str               # manual_corrected | verified | estimated | unverified
    rhyme_spans: list = field(default_factory=list)
    rhyme_group: str = ""     # the sound of the line's end word when it rhymes with another line
    rhyme_status: str = ""    # rhymed | none | unverified
    warnings: list = field(default_factory=list)
    line_id: str = ""
    sec_index: int = -1       # index into Insights.sections of the section the line belongs to
    repeats: list = field(default_factory=list)   # ["หยุด×2"] same written word twice: repetition, not a rhyme
    end_sound: str = ""       # "ใจ ไอ+กา" when the last syllable could be read, even if it rhymes with nothing
    phrases: list = field(default_factory=list)   # syllables of each phrase of the line, split at spaces and punctuation ([7, 6]); one number when the line has no break


@dataclass
class SectionInsight:
    name: str
    first_line: int           # file line of the [tag]
    sung_lines: int
    words: int
    syllables: int
    instrumental: bool
    rhyme_pattern: str = ""   # e.g. "AABB" over the end words of the section ("" when fewer than 2 lines)
    warnings: list = field(default_factory=list)
    tag: str = ""             # the section tag line as written, cues included ("" before the first tag)


@dataclass
class Insights:
    text_hash: str
    lines: list
    sections: list
    spans: list
    groups: dict              # group -> number of words in it
    revision: int = 0
    tag_lines: list = field(default_factory=list)   # file lines where a [tag] sits inside sung words (not a section line)
    rhyme_links: list = field(default_factory=list)  # the Python rhyme links of the Shared Rhyme Map (rhyme_map.Link), for exactly this text
    language: str = ""                              # the lyrics language of the song these links were made for

    def line(self, file_line: int):
        return next((x for x in self.lines if x.line_number == file_line), None)


# ── counting ──────────────────────────────────────────────────────────────────────────────────────────

_en_cache: dict = {}


def english_syllables(word: str) -> int:
    """A rule-based estimate (vowel groups, a silent final e). Always an estimate, never 'verified'."""
    w = word.lower()
    if w in _en_cache:
        return _en_cache[w]
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    if w.endswith("ed") and n > 1 and not w.endswith(("ted", "ded")):
        n -= 1
    _en_cache[w] = max(1, n)
    return _en_cache[w]


_core_cache: dict = {}


def _thai_tokens(run: str) -> list:
    from pythainlp.tokenize import word_tokenize
    return [t for t in word_tokenize(run, engine="newmm") if t.strip()]


def _thai_syllables(token: str) -> list:
    from pythainlp.tokenize import syllable_tokenize
    out = [s for s in syllable_tokenize(token, engine="dict") if s.strip()]
    return out or [token]


_BREAK = re.compile(r"[\s,;:!?…()\[\]\"“”‘’—–/|]|\.{2,}|-{2,}")


def _phrases(masked: str, toks: list, total: int, manual: bool) -> list:
    """Syllables per phrase: a phrase ends at a space or a punctuation mark between two words (the places a singer can breathe or a melody can pause).
    A manual syllable correction knows only the total, so the line is then one phrase of that size."""
    if manual or not toks:
        return [total] if total else []
    out, cur = [], 0
    for i, t in enumerate(toks):
        cur += len(t[4])
        if i + 1 < len(toks) and _BREAK.search(masked[t[1]:toks[i + 1][0]]):
            out.append(cur)
            cur = 0
    if cur:
        out.append(cur)
    return out


def line_core(masked: str) -> dict:
    """The tokens of ONE line (cached by its text): [(start, end, word, kind, syllables)], with offsets in `masked`."""
    hit = _core_cache.get(masked)
    if hit is not None:
        return hit
    toks, sure, split_unsure = [], True, False
    dic = _dictionary()
    for m in _WORD.finditer(masked):
        run = m.group(0)
        if TH.fullmatch(run):
            pos = m.start()
            for t in _thai_tokens(run):
                at = masked.index(t, pos)
                pos = at + len(t)
                syls = _thai_syllables(t)
                if dic and t not in dic:
                    sure = False
                if any(not TR.is_single_syllable(s) for s in syls):
                    sure = False
                    split_unsure = True
                toks.append((at, at + len(t), t, "th", syls))
        elif run.isdigit():
            sure = False
            toks.append((m.start(), m.end(), run, "num", [run]))
        else:
            sure = False
            toks.append((m.start(), m.end(), run, "en", [run] * english_syllables(run)))
    core = {"tokens": toks, "sure": sure and bool(toks) and bool(dic), "split_unsure": split_unsure}
    if len(_core_cache) > 4000:
        _core_cache.clear()
    _core_cache[masked] = core
    return core


@functools.lru_cache(maxsize=8192)
def _rime_of(word: str):
    if not TR.has_thai(word):
        return None
    syl = TR.final_syllable(word)
    return TR._rime(syl) if syl else None


def _key(rime: tuple) -> str:
    return f"{rime[0]}+{rime[1]}"


def color_of(group: str) -> int:
    """A sound keeps its colour while the user types: the colour comes from the sound itself, not from the order the groups appear in."""
    return zlib.crc32(group.encode("utf-8")) % len(PALETTE)


# ── analysis ──────────────────────────────────────────────────────────────────────────────────────────

def section_name(tag_line: str) -> str:
    """Section name for the dashboard. A hyphen inside the name stays. The lyrics text is not changed."""
    return section_label(tag_line)


def is_instrumental(name: str) -> bool:
    n = name.lower().strip()
    return any(n.startswith(k) for k in INSTRUMENTAL)


def _hidden_bracket(raw: str) -> bool:
    """A [tag] glued to sung words. A tag on its own line, a leading [Hold], and a trailing [Hold] are not this."""
    st = (raw or "").strip()
    if not st or (TAG_LINE.match(st) and not RM.HOLD_PREFIX.match(st)):
        return False
    rest = RM.HOLD_PREFIX.sub("", st, count=1)
    rest = HOLD_SUFFIX.sub("", rest)
    return bool(_BRACKET.search(rest))


def analyze(text: str, corrections: "dict | None" = None, revision: int = 0, language: str = "") -> Insights:
    """Every sung line with its counts and rhymes. `corrections` is the program's {line id: LA.Correction} (a correction applies only to the exact line it was made for)."""
    corrections = corrections or {}
    rows = LA.original_lines(text or "")
    sung = LA.sung_lines(text or "")
    ids = LA.line_ids(sung)
    by_file = {s.file_line: (s, i) for s, i in zip(sung, ids)}
    lines, sections, cur, tag_lines = [], [], None, []
    for n, start, _end, raw in rows:
        st = raw.strip()
        if _hidden_bracket(raw):
            tag_lines.append(n)
        if st and TAG_LINE.match(st) and not RM.HOLD_PREFIX.match(st):
            name = section_name(st)
            cur = SectionInsight(name, n, 0, 0, 0, is_instrumental(name), tag=st)
            sections.append(cur)
            continue
        if n not in by_file:
            continue
        s, lid = by_file[n]
        if cur is None:
            cur = SectionInsight("", 0, 0, 0, 0, False)
            sections.append(cur)
        masked = RM.mask(raw)
        core = line_core(masked)
        toks = core["tokens"]
        segs = [y for t in toks for y in t[4]]
        corr = corrections.get(lid)
        status = "estimated"
        warns = []
        syl = len(segs)
        if corr is not None and (corr.count is not None or corr.units):
            syl = corr.count if corr.count is not None else len(corr.units)
            segs = list(corr.units) if corr.units else segs
            status = "manual_corrected"
        elif not toks:
            status, syl = "unverified", 0
        elif core["sure"]:
            status = "verified"
        if toks and not core["sure"] and status != "manual_corrected":
            if core.get("split_unsure") and all(t[3] == "th" for t in toks):
                warns.append("A dictionary piece is not one syllable the spelling rules accept, so the count is an estimate.")
            else:
                warns.append("A word is English, a number or not in the Thai dictionary: its syllable count is an estimate.")
        li = LineInsight(n, s.num, cur.name, s.text, hashlib.sha1(s.text.encode("utf-8")).hexdigest()[:10], len(toks), syl, segs, status, [], "", "", warns, lid, len(sections) - 1)
        seen: dict = {}
        for t in toks:
            if t[3] == "th":
                seen[t[2]] = seen.get(t[2], 0) + 1
        li.repeats = [f"{w}×{c}" for w, c in seen.items() if c >= 2]
        li.phrases = _phrases(masked, toks, syl, status == "manual_corrected")
        li._tokens = toks                                                           # (offsets in the line) kept for the rhyme step; not part of the data
        li._base = start
        lines.append(li)
        cur.sung_lines += 1
        cur.words += li.word_count
        cur.syllables += syl
    links = _rhymes(lines, sections, language)
    spans = [sp for li in lines for sp in li.rhyme_spans]
    groups: dict = {}
    for sp in spans:
        groups[sp.group] = groups.get(sp.group, 0) + 1
    for k, sec in enumerate(sections):
        _section_pattern(sec, [li for li in lines if li.sec_index == k])
    for li in lines:
        del li._tokens, li._base
    return Insights(hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12], lines, sections, spans, groups, revision, tag_lines, links, language)


def _rhymes(lines: list, sections: list, declared: str = "") -> list:
    """The rhymes of the Python rules (rhyme_map.python_links: the one place that decides them), turned into this module's spans. Returns the links. A word is coloured only for a Thai pair whose
    sound was read; English endings stay unverified (never coloured). Lines of unrelated sections are not linked only because the sounds match."""
    pls = [RM.PyLine(li.line_number, li.sung_number, li.sec_index, sections[li.sec_index].name if 0 <= li.sec_index < len(sections) else "", li._base, li._tokens) for li in lines]
    links, info = RM.python_links(pls, declared)
    by_line = {li.line_number: li for li in lines}
    for li in lines:
        word, key, status = info.get(li.line_number, ("", "", ""))
        li.rhyme_status = status
        if not status and any(c.isalpha() for c in li.text):
            li.rhyme_status = "unverified"                  # a script the program cannot read (Japanese, Korean ...): "not checked", never "no rhyme"
        li.end_sound = f"{word} {key}" if word and key else ""
        li.rhyme_group = key if status == "rhymed" else ""
    spans: dict = {}
    for k in links:
        if not k.colourable or k.source != "python":
            continue
        role = "end" if k.placement == "end" else "internal"
        for w in (k.a, k.b):
            li = by_line.get(w.file_line)
            if li is None:
                continue
            cur = spans.get((w.start, w.end))
            kind = "exact" if k.sound == "exact" else "near"
            if cur is not None:
                if cur.role == "end" or role == "internal":
                    if kind == "exact" and cur.kind != "exact":
                        cur.kind = "exact"
                    continue
            spans[(w.start, w.end)] = RhymeSpan(w.file_line, w.start, w.end, w.text, k.group, color_of(k.group), kind, role)
    for sp in sorted(spans.values(), key=lambda x: x.start):
        by_line[sp.line].rhyme_spans.append(sp)
    return links


_map_cache: dict = {}


def rhyme_map(text: str, result=None, language: str = "", ins: "Insights | None" = None):
    """The Shared Rhyme Map of these lyrics: the Python links (always) and, only when `result` (the AI's Analyze Rhymes) is for exactly these sung lines, its pairs with their own statuses. An
    outdated result is not used (the map says stale). The AI is never asked here. The last few maps are cached, so a redraw does not analyse again."""
    key = (text, id(result), len(getattr(result, "pairs", ()) or ()), language)
    hit = _map_cache.get(key)
    if hit is not None:
        return hit
    ins = ins if ins is not None and ins.language == language else analyze(text, None, 0, language)
    m = RM.build_map(text, ins.rhyme_links, result, language)
    if len(_map_cache) > 8:
        _map_cache.clear()
    _map_cache[key] = m
    return m


def _section_pattern(sec: SectionInsight, lines: list) -> None:
    labels, seen = [], {}
    for li in lines:
        k = li.rhyme_group if li.rhyme_status == "rhymed" else ""
        if not k:
            labels.append("-")
            continue
        seen.setdefault(k, chr(ord("A") + len(seen)))
        labels.append(seen[k])
    sec.rhyme_pattern = "".join(labels) if len(lines) >= 2 else ""
    if len(lines) >= 4 and labels and all(x == "-" for x in labels) and all(li.rhyme_status != "unverified" for li in lines):
        sec.warnings.append("No line endings of this section rhyme with each other (not an error: only a hint if you wanted rhymes).")


# ── vocal density ─────────────────────────────────────────────────────────────────────────────────────
# Question answered: which LINES of these lyrics may make ACE-Step rush, merge or skip words at this BPM, time signature and target duration, and does the song fit the duration? Nothing here knows the melody,
# so every result is an ESTIMATE worked out only from what is sent to ACE-Step (the lyrics, the BPM, the time signature, the target duration). Three analyses are kept apart, line by line:
#   A. Vocal Density (per line)  the line's sung syllables against its own time. Two times are tried: the AVERAGE slot (the singing time shared by all lines) and the MOST time the line could get
#      (MAX_LINE_BARS bars, or what is left if every other line is sung at a comfortable pace). Pace is syllables per beat (a beat = a quarter note; in 6/8 the BPM counts the dotted quarter), with
#      cut-offs from the ACE-Step 1.5 Guide (6-10 syllables per two 4/4 bars = 0.75-1.25 per beat = comfortable; up to 1.75 moderate; up to 2.25 dense; above very dense) and an absolute pace floor.
#      OK = not dense even at the average slot; Warning = dense at the average slot but fine with more time; High Risk = dense even with the most time (a hedged count never makes High Risk).
#   B. Vocal Phrasing (per line)  the longest phrase with no space or punctuation to breathe at, from the text only (PHRASE_WARN / PHRASE_HIGH). Separate from density.
#   C. Song Duration Fit (song)  the time the lyrics take if sung between a fast and a comfortable pace (plus rests and the instrumental parts), as a RANGE, against the target duration. Lines are not
#      assumed to take equal time and no fixed bars per line is used. This is about the whole song and says nothing about any single line.
# A section or the song is a summary of its lines (the worst line, how many lines to check); an average never hides a risky line. Not enough data is "unknown", never a guess.

LEVELS = ("comfortable", "moderate", "dense", "very_dense")
LEVEL_NAME = {"comfortable": "Comfortable", "moderate": "Moderate", "dense": "Dense", "very_dense": "Very Dense", "unknown": "Unknown"}
RESERVE_BARS = {"intro": 4, "outro": 4, "instrumental": 8, "interlude": 4, "solo": 8, "break": 2}       # bars of music without singing (stated assumptions)
REST_SHARE = 0.12
MIN_SYLLABLES = 8
SYL_PER_BEAT = (1.25, 1.75, 2.25)                # cut-offs in syllables per beat: comfortable / moderate / dense / very dense (see 4)
SYL_PER_SECOND = (5.5, 7.0)                      # at or above: at least dense / very dense (see 4)
LENGTH_LONG, LENGTH_SHORT = 1.10, 0.70           # the lyrics need more than 110 % of the target: long; less than 70 %: short
MIN_SHORT_LINES = 12                             # "short" is only said of lyrics that look finished (a half-written song is always short of its target)


def pulses_per_bar(timesig: str) -> tuple:
    """(pulses per bar, wording). BPM counts quarter notes in 2/4, 3/4 and 4/4. In 6/8 the bar has TWO dotted-quarter pulses: BPM is read as those pulses (a bar of 6 eighth notes is not 6 quarter-note beats)."""
    return {"2": (2, "2/4"), "3": (3, "3/4"), "4": (4, "4/4"), "6": (2, "6/8 (BPM counts the dotted quarter)")}.get(str(timesig), (4, "4/4"))


def seconds_per_bar(bpm: float, timesig: str) -> float:
    return pulses_per_bar(timesig)[0] * 60.0 / max(1.0, float(bpm))


def quarters_per_second(bpm: float, timesig: str) -> float:
    """Quarter-note beats per second. BPM counts quarter notes in 2/4, 3/4 and 4/4; in 6/8 it counts the dotted quarter (1.5 quarter notes)."""
    return max(1.0, float(bpm)) / 60.0 * (1.5 if str(timesig) == "6" else 1.0)


def level_of(rate: float, bpm: float, timesig: str) -> str:
    """The level of a rate in syllables per second at this BPM: by syllables per beat, never lower than the absolute pace allows (see the notes above)."""
    per_beat = rate / quarters_per_second(bpm, timesig)
    i = 0 if per_beat < SYL_PER_BEAT[0] else 1 if per_beat < SYL_PER_BEAT[1] else 2 if per_beat < SYL_PER_BEAT[2] else 3
    if rate >= SYL_PER_SECOND[1]:
        i = 3
    elif rate >= SYL_PER_SECOND[0]:
        i = max(i, 2)
    return LEVELS[i]


STATUS_RANK = {"unknown": -1, "ok": 0, "warning": 1, "high": 2}
STATUS_NAME = {"ok": "OK", "warning": "Warning", "high": "High Risk", "unknown": "Unknown"}
MAX_LINE_BARS = 4                                # the most time one sung line is assumed to take (a phrase longer than about four bars is unusual); a stated assumption, not a measurement
PHRASE_WARN, PHRASE_HIGH = 10, 15                # syllables in ONE unbroken phrase: above the top of the Guide's 6-10 = Warning; 1.5 times that = High Risk (a stated rule of thumb, not a measurement)
SUPPORTED_LANGUAGES = ("th", "en")               # the languages whose syllables are counted by this program ("" / auto = each word by its own script)


def worst_status(statuses) -> str:
    """The worst of several statuses: High Risk > Warning > OK. Unknown only when nothing could be judged."""
    best = "unknown"
    for s in statuses:
        if STATUS_RANK.get(s, -1) > STATUS_RANK[best]:
            best = s
    return best


def language_supported(language: str) -> bool:
    lang = (language or "").strip().lower()
    return lang in ("", "auto") or lang[:2] in SUPPORTED_LANGUAGES


@dataclass
class DensitySettings:
    bpm: float = 120.0
    timesig: str = "4"                      # the values of music_options.TIMESIGS: "2" 2/4, "3" 3/4, "4" 4/4, "6" 6/8
    duration: float = 180.0                 # target seconds


@dataclass
class LineDensity:
    """One sung line, judged on its own. Section and song results are summaries of these, never averages that hide a line."""
    line_number: int
    syllables: int
    rate: float                             # syllables per second if the line gets the average time slot
    level: str                              # density level at the average slot (comfortable | moderate | dense | very_dense | unknown)
    outlier: bool = False                   # the line needs a look (Warning or High Risk)
    line_id: str = ""
    section: str = ""
    section_index: int = 0                  # which occurrence of the section name (0 = the first)
    phrases: list = field(default_factory=list)   # syllables per phrase, split at spaces and punctuation
    longest_phrase: int = 0
    roomy_level: str = "unknown"            # density level if the line may take up to MAX_LINE_BARS (or every other line is sung at a comfortable pace)
    density_status: str = "unknown"         # ok | warning | high | unknown
    phrasing_status: str = "unknown"        # ok | warning | high | unknown (text-based: no melody is known)
    status: str = "unknown"                 # the worse of the two
    reason: str = ""                        # a short reason shown beside the line
    detail: str = ""                        # the numbers behind it
    confidence: str = "medium"              # high (counted from the dictionary or corrected by hand) | medium (estimated count) | low (could not be counted / language not supported)
    slot_seconds: float = 0.0               # the average time slot
    roomy_seconds: float = 0.0              # the most time this line could get (see roomy_level)
    need_seconds: float = 0.0               # the time this line needs to sit at a moderate pace


@dataclass
class SectionDensity:
    name: str
    index: int                              # which occurrence of this name (0 = the first)
    first_line: int
    last_line: int
    sung_lines: int
    words: int
    syllables: int
    rate: float                             # information only: syllables per second over the section (an average; never used for a status)
    ratio: float                            # information only: syllables per beat / the moderate cut-off
    level: str                              # derived from the lines: Very Dense if a line is High Risk, Dense if a line is a Warning, else the worst line level
    instrumental: bool
    reason: str = ""
    per_beat: float = 0.0                   # information only: the average syllables per beat of the section
    status: str = "unknown"                 # ok | warning | high | unknown: the worst of its lines
    risky_lines: list = field(default_factory=list)   # file lines that are a Warning or High Risk, in order
    worst_line: int = 0                     # the file line to jump to
    density_lines: list = field(default_factory=list)  # the risky lines whose reason is density
    phrasing_lines: list = field(default_factory=list)  # the risky lines whose reason is phrasing


@dataclass
class Warning_:
    section: str
    index: int
    level: str
    first_line: int
    last_line: int
    syllables: int
    lines: int
    reason: str
    outliers: list = field(default_factory=list)


@dataclass
class Density:
    settings: DensitySettings
    known: bool
    why_unknown: str
    sec_per_bar: float
    reserve_seconds: float
    vocal_seconds: float                    # time left for singing after the instrumental reserve and the rests
    slot: float                             # the average seconds one sung line gets (a reference, not a verdict on any line)
    total_syllables: int
    total_lines: int
    song_rate: float                        # information only: the average over the song
    song_level: str                         # derived from the lines (see SectionDensity.level), never from the average
    lines: list
    sections: list
    warnings: list
    assumptions: str
    song_per_beat: float = 0.0              # information only: the average syllables per beat of the song
    need_low: float = 0.0                   # seconds the lyrics and the instrumental parts take if sung at the fast end of the comfortable range
    need_high: float = 0.0                  # ... and at the slow end
    fit: str = "unknown"                    # fits | long | short | unknown: the target duration against that range (this is NOT how dense the lines are)
    risky_lines: list = field(default_factory=list)   # file lines that are a Warning or High Risk
    high_lines: list = field(default_factory=list)
    status: str = "unknown"                 # the worst line status of the song
    language_ok: bool = True
    time_known: bool = False                # the BPM and the target duration allow a time per line
    bpm_known: bool = False


def _line_confidence(x, supported: bool) -> str:
    if not supported or x.status == "unverified" or x.syllable_count == 0:
        return "low"
    return "high" if x.status in ("verified", "manual_corrected") else "medium"


def density(ins: Insights, ts: DensitySettings) -> Density:
    spb = seconds_per_bar(ts.bpm, ts.timesig)
    sung = list(ins.lines)
    total_syl, total_lines = sum(li.syllable_count for li in sung), len(sung)
    supported = language_supported(ins.language)
    reserve = sum(next((v for k, v in RESERVE_BARS.items() if sec.name.lower().startswith(k)), 4) * spb for sec in ins.sections if sec.instrumental and not sec.sung_lines)
    vocal = max(0.0, (ts.duration - reserve) * (1.0 - REST_SHARE))
    why = ""
    if not supported:
        why = f"Vocal density is not judged for the language '{ins.language}'."
    elif ts.duration <= 0 or ts.bpm <= 0:
        why = "The BPM or the target duration is missing."
    elif total_lines < 2 or total_syl < MIN_SYLLABLES:
        why = "Too few sung lines to judge."
    elif vocal < 3.0:
        why = "No time is left for singing after the instrumental parts."
    known = not why
    bpm_known = supported and ts.bpm > 0 and total_lines >= 1
    slot = vocal / total_lines if known else 0.0
    qps = quarters_per_second(ts.bpm, ts.timesig)
    # the time each line needs at a comfortable pace, and what is left for one line if every OTHER line is sung at that pace (never more than MAX_LINE_BARS)
    easy = {id(x): x.syllable_count / (SYL_PER_BEAT[0] * qps) for x in sung} if ts.bpm > 0 else {}
    easy_total = sum(easy.values())
    cap = MAX_LINE_BARS * spb
    sec_lines: dict = {}
    for li in sung:
        sec_lines.setdefault(li.sec_index, []).append(li)
    lines_d, sections_d, warnings = [], [], []
    counter: dict = {}
    for k, sec in enumerate(ins.sections):
        n = counter.get(sec.name, 0)
        counter[sec.name] = n + 1
        ls = sec_lines.get(k, [])
        name = sec.name or "(start)"
        rows = []
        for x in ls:
            syl = x.syllable_count
            phrases = list(x.phrases) or ([syl] if syl else [])
            longest = max(phrases) if phrases else 0
            conf = _line_confidence(x, supported)
            sure = conf == "high"
            est = "" if sure else "about "
            # B. phrasing: the longest unbroken phrase (text only; the melody is not known)
            if not supported:
                p_status = "unknown"
            elif longest >= PHRASE_HIGH:
                p_status = "high" if sure else "warning"
            elif longest > PHRASE_WARN:
                p_status = "warning"
            else:
                p_status = "ok"
            # A. density: the line's own syllables against its own time, at the average slot and at the most time it could get
            tight_s = min(slot, cap) if known else 0.0                   # a line is not assumed to take longer than the cap, so a short lyric is not judged at a minute per line
            roomy_s = 0.0
            if known:
                roomy_s = min(cap, max(slot, vocal - (easy_total - easy[id(x)])))
            elif bpm_known and supported:
                roomy_s = cap
            tight_lv = level_of(syl / tight_s, ts.bpm, ts.timesig) if tight_s > 0 else "unknown"
            roomy_lv = level_of(syl / roomy_s, ts.bpm, ts.timesig) if roomy_s > 0 else "unknown"
            if syl <= 0 or not supported:
                d_status = "unknown"
            elif tight_lv in ("comfortable", "moderate"):
                d_status = "ok"
            elif roomy_lv in ("dense", "very_dense"):
                d_status = "high" if sure else "warning"       # even with the most time it could get: a hedged count never makes it High Risk
            elif tight_lv == "unknown":
                d_status = "unknown"                           # no target duration: only the case "even at the longest phrase" can be said
            else:
                d_status = "warning"                           # depends on how much time this phrase really gets
            status = "unknown" if (d_status == "unknown" and p_status == "ok") else worst_status([d_status, p_status])
            need_s = syl / (SYL_PER_BEAT[1] * qps) if ts.bpm > 0 else 0.0
            bits, nums = [], []
            if p_status in ("warning", "high") and longest:
                bits.append("long phrase without a break" if len(phrases) == 1 else f"a phrase of {est}{longest} syllables")
                nums.append(f"the longest unbroken phrase has {est}{longest} syllables (the Guide's reference is about {LINE_REFERENCE[0]}-{LINE_REFERENCE[1]} per line; Warning above {PHRASE_WARN}, High Risk from {PHRASE_HIGH})")
            if d_status in ("warning", "high"):
                bits.append("many syllables for the time" if d_status == "warning" else "too many syllables even with the most time")
                if tight_s:
                    nums.append(f"{est}{syl} syllables in the average slot of {tight_s:.1f} s is {syl / tight_s / qps:.2f} per beat ({LEVEL_NAME[tight_lv]}); with up to {roomy_s:.1f} s it is {syl / roomy_s / qps:.2f} ({LEVEL_NAME[roomy_lv]})")
                else:
                    nums.append(f"{est}{syl} syllables in the longest assumed phrase of {roomy_s:.1f} s is {syl / roomy_s / qps:.2f} per beat ({LEVEL_NAME[roomy_lv]}); no target duration is set")
                if d_status == "warning" and need_s:
                    nums.append(f"it needs about {need_s:.1f} s ({need_s / spb:.1f} bars) to sit at a moderate pace")
            if not sure and (d_status != "ok" or p_status != "ok") and syl:
                nums.append("the syllable count is an estimate")
            if d_status == "unknown" and syl and supported:
                nums.append("the density depends on the time this line gets, which is not set (no BPM or target duration)")
            ld = LineDensity(x.line_number, syl, (syl / tight_s) if tight_s else 0.0, tight_lv, status in ("warning", "high"), x.line_id, name, n, phrases, longest, roomy_lv,
                             d_status, p_status, status, "; ".join(bits), "; ".join(nums), conf, tight_s, roomy_s, need_s)
            rows.append(ld)
            lines_d.append(ld)
        syl_total = sum(x.syllable_count for x in ls)
        rate = (syl_total / (len(ls) * slot)) if known and ls else 0.0
        risky = [r.line_number for r in rows if r.status in ("warning", "high")]
        sec_status = worst_status(r.status for r in rows) if rows else "unknown"
        dens_lines = [r.line_number for r in rows if r.density_status in ("warning", "high")]
        phr_lines = [r.line_number for r in rows if r.phrasing_status in ("warning", "high")]
        worst = max(rows, key=lambda r: (STATUS_RANK[r.status], r.longest_phrase, r.syllables)) if risky else None
        d_worst = worst_status(r.density_status for r in rows) if rows else "unknown"
        lvl = ("unknown" if d_worst == "unknown" else "very_dense" if d_worst == "high" else "dense" if d_worst == "warning"
               else max((r.level for r in rows if r.level in LEVELS), key=LEVELS.index, default="unknown"))
        reason = ""
        if rows:
            reason = (f"{len(risky)} of {len(rows)} lines to check" if risky else f"no line to check in {len(rows)} lines") + (f": line {worst.line_number} ({worst.reason})" if worst and worst.reason else "")
        sd = SectionDensity(name, n, sec.first_line, max([x.line_number for x in ls] or [sec.first_line]), len(ls), sum(x.word_count for x in ls), syl_total, rate,
                            rate / qps / SYL_PER_BEAT[1] if known and ls else 0.0, lvl, sec.instrumental and not ls, reason, rate / qps if known and ls else 0.0,
                            sec_status, risky, worst.line_number if worst else 0, dens_lines, phr_lines)
        sections_d.append(sd)
        if dens_lines and len(warnings) < 6:
            bad = [r for r in rows if r.line_number in dens_lines]
            warnings.append(Warning_(name, n, "very_dense" if d_worst == "high" else "dense", bad[0].line_number, bad[-1].line_number, sum(r.syllables for r in bad), len(bad),
                                     f"{len(bad)} of {len(rows)} lines are tight for their time: " + ", ".join(f"line {r.line_number}" for r in bad[:6]) + ".", [r.line_number for r in bad]))
    song_rate = total_syl / vocal if known else 0.0
    d_song = worst_status(r.density_status for r in lines_d)
    song_level = ("unknown" if d_song == "unknown" else "very_dense" if d_song == "high" else "dense" if d_song == "warning"
                  else max((r.level for r in lines_d if r.level in LEVELS), key=LEVELS.index, default="unknown"))
    # C. duration fit, separate from the density: the time the lyrics take if every line is sung between a comfortable and a fast pace, plus the instrumental parts
    need_low = need_high = 0.0
    fit = "unknown"
    if bpm_known and total_lines >= 2 and total_syl >= MIN_SYLLABLES:
        pace_lo, pace_hi = SYL_PER_BEAT[2], SYL_PER_BEAT[0]            # fastest and slowest pace in syllables per beat that still counts as comfortable-to-dense
        need_low = total_syl / (pace_lo * qps) / (1.0 - REST_SHARE) + reserve
        need_high = total_syl / (pace_hi * qps) / (1.0 - REST_SHARE) + reserve
        if ts.duration > 0:
            fit = ("long" if ts.duration < need_low else "short" if (ts.duration > need_high / LENGTH_SHORT and total_lines >= MIN_SHORT_LINES) else "fits")
    risky_all = [r.line_number for r in lines_d if r.status in ("warning", "high")]
    high_all = [r.line_number for r in lines_d if r.status == "high"]
    assume = (f"{pulses_per_bar(ts.timesig)[1]}, {ts.bpm:g} BPM → {spb:.1f} s per bar; target {ts.duration:g} s; about {reserve:.0f} s kept for the instrumental parts "
              f"(a set number of bars each); {REST_SHARE * 100:.0f} % of the rest is breath and pauses. Each line is judged on its own syllables against the average time slot and against the most time it could get "
              f"(up to {MAX_LINE_BARS} bars, or what is left if every other line is sung at a comfortable pace). Pace is syllables per beat (comfortable under {SYL_PER_BEAT[0]:g}, moderate under {SYL_PER_BEAT[1]:g}, "
              f"dense under {SYL_PER_BEAT[2]:g}), from the ACE-Step 1.5 Guide's 6-10 syllables per line. These cut-offs and the bar limits are stated assumptions, not measurements. "
              "The melody is not known, so this is an estimated risk, not a measurement, and OK is never a guarantee.")
    return Density(ts, known, why, spb, reserve, vocal, slot, total_syl, total_lines, song_rate, song_level, lines_d, sections_d, warnings, assume,
                   song_rate / qps if known else 0.0, need_low, need_high, fit, risky_all, high_all, worst_status(r.status for r in lines_d), supported, known, bpm_known)


# ── ACE-Step lyrics health ────────────────────────────────────────────────────────────────────────────
# Warnings for what may make ACE-Step 1.5 rush, merge or skip words, built from numbers the program really has. Three kinds of basis are kept apart:
#   detected   a fact from the counting engine (a line has N syllables, the others about M)
#   guideline  the ACE-Step 1.5 Musician's Guide reference of roughly 6-10 syllables per line (a rough reference, never a rule)
#   estimate   a risk worked out from the duration and the density assumptions (no melody is known)
# A line whose syllable count is itself an estimate (English word, number, word not in the dictionary) is reported with hedged wording ("about N").
# Skipping a section is something ACE-Step may do for several reasons: the program never predicts it and never states a probability.
LINE_REFERENCE = (6, 10)
MAX_HEALTH = 10
STRUCTURE_MIN_SECTIONS = 4
STRUCTURE_SECONDS_PER_SECTION = 12.0
PRIORITY = {"song_density": 1, "length_fit": 1, "section_density": 2, "long_line": 3, "uneven": 4, "structure": 5, "duplicate_section": 6, "cue_conflict": 6, "tag_complexity": 6, "short_word": 7}
DENSITY_KINDS = frozenset({"length_fit", "song_density", "section_density", "long_line", "uneven", "short_word"})
STRUCTURE_KINDS = frozenset({"structure", "duplicate_section", "cue_conflict", "tag_complexity"})
# Same opposite-energy sets the Song Architect already refuses to write into a tag. A warning only: nothing here rewrites a tag.
CUE_LOW = frozenset({"soft", "low energy", "whispered", "minimal", "soft vocals", "breathy vocals", "ambient", "soft piano"})
CUE_HIGH = frozenset({"explosive", "high energy", "powerful", "powerful belting", "anthemic", "aggressive", "energetic", "punchy", "heavy drums", "heavy bass"})
MAX_CUES = 2
MAX_CUE_CHARS = 80
_REPEATABLE = frozenset({"chorus", "hook", "refrain", "pre-chorus", "pre chorus", "prechorus", "post-chorus", "post chorus", "postchorus", "final chorus"})
_CUE_SPLIT = re.compile(r"\s*(?:,|&|\+|\band\b|\bwith\b)\s*", re.IGNORECASE)
_CUE_SEP = re.compile(r"\s+(?:—|–|-|:)\s+")
_EM_SEP = re.compile(r"\s*[—–]\s*")
_BRACKET = re.compile(r"\[[^\[\]]+\]")
# A line that is only one of these short function words can be held or stressed. The set stays closed.
# A short word inside a longer line is left alone. Nothing here maps a Thai tone onto a musical pitch.
_SHORT_FUNCTION = frozenset({"ใน", "บน", "จาก", "ของ", "กับ", "และ", "แต่", "หรือ", "เพื่อ", "ที่", "ซึ่ง", "ว่า", "ถ้า", "เพราะ"})
_SHORT_EDGE = " \t\"'“”‘’()[].,!?…"
_MAX_SHORT_WORDS = 2


def health_group(kind: str) -> str:
    """'density' for syllable pressure and line length. Everything else is section structure."""
    return "density" if kind in DENSITY_KINDS else "structure"


def _section_base(name: str) -> str:
    return re.sub(r"\s+\d+$", "", (name or "").strip().lower())


def _repeatable_section(name: str) -> bool:
    """Chorus, hook, refrain, pre-chorus and post-chorus are meant to come back. Verse 1 is not the same name as Verse 2."""
    return _section_base(name) in _REPEATABLE


def _label_of(tag: str, fallback: str = "") -> str:
    """Section name from the tag line. A hyphen inside Pre-Chorus stays. The cue starts at ' - ' or a dash."""
    return section_name(tag) or (fallback or "")


def _cue_parts(tag: str) -> tuple:
    """(cue text, parts). Parts split on comma / and / with. Empty when the tag has no cue."""
    inner = (tag or "").strip()
    if inner.startswith("[") and inner.endswith("]") and len(inner) >= 2:
        inner = inner[1:-1].strip()
    m = _CUE_SEP.search(inner) or _EM_SEP.search(inner)
    if not m:
        return "", []
    rest = inner[m.end():].strip()
    parts = [p.strip() for p in _CUE_SPLIT.split(rest) if p.strip()]
    return rest, parts


def _shown_name(name: str, index: int) -> str:
    return f"{name}{' #' + str(index + 1) if index else ''}"


@dataclass
class HealthItem:
    kind: str                 # length_fit | song_density | section_density | long_line | uneven | short_word | structure | duplicate_section | cue_conflict | tag_complexity
    priority: int
    section: str              # section name, "Whole song" or "Structure"
    index: int                # which occurrence of that section name (0 = the first)
    first_line: int           # file line the warning points at (0 = none: the warning is about the whole song)
    last_line: int
    lines: list               # file lines involved
    title: str
    summary: str
    detail: str
    basis: str                # detected | guideline | estimate
    hedged: bool              # a syllable count used here is itself an estimate
    spec: str                 # who to ask: songwriter | music_producer


def _median(xs: list) -> float:
    xs = sorted(xs)
    n = len(xs)
    return 0.0 if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


def _line_length_summary(line_no: int, est: str, count: int, median: float, in_section: bool, section_level: str, line_level: str) -> str:
    """One line's length, kept apart from the section's overall density.

    The section chip already says whether the section is comfortable. This sentence must not call that section dense, and must not repeat the section warning.
    """
    scope = "of this section " if in_section else ""
    head = f"Lyric line {line_no} has {est}{count} syllables while the other lines {scope}have about {median:g}."
    name = LEVEL_NAME.get(section_level, "")
    status = f" The section overall is {name}." if name and section_level not in ("", "unknown") else ""
    if section_level in ("comfortable", "moderate"):
        if line_level in ("dense", "very_dense"):
            tail = f" Only this line is {LEVEL_NAME[line_level]}. This is a tight spot on this line, not a warning that the whole section is dense."
        else:
            tail = " Only this line is long. This is a phrasing note for this line, not a warning that the section is dense or that it will be sung rushed."
    elif section_level in ("dense", "very_dense"):
        tail = " This note is only this longer line, not a second warning for the whole section."
    else:
        tail = " This note is only this line."
    return head + status + tail


def _lone_function_word(text: str) -> str:
    """The word when the whole sung line is one short function word. Empty otherwise."""
    core = (text or "").strip().strip(_SHORT_EDGE)
    if not core or any(ch.isspace() for ch in core):
        return ""
    return core if core in _SHORT_FUNCTION else ""


def _structure_items(ins: Insights, name_of: dict) -> list:
    """Repeated section names and crowded or conflicting tags. A later Chorus is not a duplicate. Nothing here edits the lyrics."""
    out = []
    grouped: dict = {}
    order = []
    for sec in ins.sections:
        label = _label_of(sec.tag, sec.name)
        key = label.strip().lower()
        if not key:
            continue
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append((sec, label))
    for key in order:
        group = grouped[key]
        if len(group) < 2:
            continue
        label = group[0][1]
        stacked = []
        for (a, _la), (b, _lb) in zip(group, group[1:]):
            between = any(a.first_line < s.first_line < b.first_line for s in ins.sections)
            if a.sung_lines == 0 and not between:
                stacked.append((a, b))
        if stacked:
            a, b = stacked[0]
            idx = name_of.get(ins.sections.index(b), (label, 0))[1]
            out.append(HealthItem(
                "duplicate_section", PRIORITY["duplicate_section"], label, idx, b.first_line, b.first_line, [a.first_line, b.first_line],
                f"{_shown_name(label, idx)} — repeated section",
                f"{label} is stacked again at lines {a.first_line} and {b.first_line} with no sung line between the tags. This note does not change the lyrics.",
                "A second copy of the same section name with nothing sung between the tags is a stacked section. The lyrics were not rewritten.",
                "detected", False, "music_producer"))
            continue
        if _repeatable_section(label) or group[0][0].instrumental:
            continue
        a, b = group[0][0], group[1][0]
        idx = name_of.get(ins.sections.index(b), (label, 0))[1]
        out.append(HealthItem(
            "duplicate_section", PRIORITY["duplicate_section"], label, idx, b.first_line, b.first_line, [a.first_line, b.first_line],
            f"{_shown_name(label, idx)} — repeated section",
            f"{label} is used again at line {b.first_line} after line {a.first_line}. This repeated section is not a chorus reprise. This note does not change the lyrics.",
            "Chorus, hook, refrain, pre-chorus and post-chorus may return later in the song. Another name used twice is reported here. The lyrics were not rewritten.",
            "detected", False, "music_producer"))
    for k, sec in enumerate(ins.sections):
        if not sec.tag:
            continue
        label = _label_of(sec.tag, sec.name) or sec.name or "Structure"
        idx = name_of.get(k, (label, 0))[1]
        rest, parts = _cue_parts(sec.tag)
        low = sorted({p for p in parts if p.lower() in CUE_LOW})
        high = sorted({p for p in parts if p.lower() in CUE_HIGH})
        if low and high:
            out.append(HealthItem(
                "cue_conflict", PRIORITY["cue_conflict"], label, idx, sec.first_line, sec.first_line, [sec.first_line],
                f"{_shown_name(label, idx)} — conflicting cues",
                f"The tag at line {sec.first_line} puts {', '.join(low)} against {', '.join(high)}. Those cues pull the section in opposite directions. This note does not change the lyrics.",
                "Opposite energy in one tag is reported only. The tag was not rewritten.",
                "detected", False, "music_producer"))
        if len(parts) > MAX_CUES:
            out.append(HealthItem(
                "tag_complexity", PRIORITY["tag_complexity"], label, idx, sec.first_line, sec.first_line, [sec.first_line],
                f"{_shown_name(label, idx)} — crowded tag",
                f"The tag at line {sec.first_line} has {len(parts)} cues. One or two short cues are easier for the model to follow. This note does not change the lyrics.",
                "The studio guide suggests at most one or two short hints in a section tag. The tag was not rewritten.",
                "detected", False, "music_producer"))
        elif rest and len(rest) > MAX_CUE_CHARS and not (low and high):
            out.append(HealthItem(
                "tag_complexity", PRIORITY["tag_complexity"], label, idx, sec.first_line, sec.first_line, [sec.first_line],
                f"{_shown_name(label, idx)} — long tag",
                f"The tag at line {sec.first_line} holds a {len(rest)} character direction. A long tag can be sung as words instead of obeyed. This note does not change the lyrics.",
                "Official guidance says a stacked or very long tag can be sung as lyrics. The tag was not rewritten.",
                "detected", False, "music_producer"))
    for n in ins.tag_lines[:4]:
        li = ins.line(n)
        label, idx = name_of.get(li.sec_index, ("Structure", 0)) if li is not None else ("Structure", 0)
        if li is not None and li.sec_index >= 0:
            sec = ins.sections[li.sec_index]
            label = _label_of(sec.tag, sec.name) or label
        out.append(HealthItem(
            "tag_complexity", PRIORITY["tag_complexity"], label or "Structure", idx, n, n, [n],
            f"{_shown_name(label or 'Structure', idx)} — tag inside the line",
            f"Sung words at line {n} still contain a bracket tag. A section tag has to stand alone on its line. This note does not change the lyrics.",
            "Only a bracket line by itself is sent as a section. A tag glued to sung words is sent as words to sing. Nothing was rewritten.",
            "detected", False, "music_producer"))
    return out


def _cap_health(out: list) -> list:
    """Keep section-structure notes even when many line notes would fill the list. Density notes still come first."""
    struct = [h for h in out if h.kind in STRUCTURE_KINDS]
    dens = [h for h in out if h.kind not in STRUCTURE_KINDS]
    if len(struct) >= MAX_HEALTH:
        chosen = struct[:MAX_HEALTH]
    else:
        chosen = dens[:MAX_HEALTH - len(struct)] + struct
    chosen.sort(key=lambda h: (h.priority, h.first_line))
    return chosen


def health(ins: Insights, dn: Density) -> list:
    """The warnings of this song for these settings, most important first (see the module notes above for what each kind means)."""
    out: list = []
    st = dn.settings
    f = 1.0
    lo, hi = LINE_REFERENCE
    limit = hi
    by_sec: dict = {}
    for li in ins.lines:
        by_sec.setdefault(li.sec_index, []).append(li)
    name_of = {}
    counter: dict = {}
    for k, sec in enumerate(ins.sections):
        n = counter.get(sec.name, 0)
        counter[sec.name] = n + 1
        name_of[k] = (sec.name or "(start)", n)
    shown = lambda name, i: f"{name}{' #' + str(i + 1) if i else ''}"
    # 1 + 2: density (the existing warnings of the section / the whole song)
    for w in dn.warnings:
        whole = w.section == "Whole song"
        msg = ("The lyrics of this section have many syllables for the time estimated for them. ACE-Step may rush, merge or skip some words." if w.level == "dense" else
               "This section may need a faster delivery than its tempo allows. Try fewer words, or split the phrases so there is room to breathe.")
        if whole:
            msg = "Overall the lyrics have many syllables for the target duration. Shorter lines or fewer lines may help, or a longer duration."
        out.append(HealthItem("song_density" if whole else "section_density", PRIORITY["song_density" if whole else "section_density"], w.section, w.index, 0 if whole else w.first_line, w.last_line,
                              list(range(w.first_line, w.last_line + 1)) if not whole else [], f"{shown(w.section, w.index)} — {'Dense' if w.level == 'dense' else 'High risk'}", msg, w.reason, "estimate", False, "songwriter"))
    dense_lines = [x for x in dn.lines if x.density_status in ("warning", "high")]
    if dense_lines:
        out.append(HealthItem("song_density", PRIORITY["song_density"], "Whole song", 0, 0, 0, [], f"Whole song — {len(dense_lines)} of {len(dn.lines)} lines to check for density",
                              "Some lines have many syllables for the time they can get. ACE-Step may rush, merge or skip words on those lines. The rest of the song is not affected by this note.",
                              "Lines: " + ", ".join(str(x.line_number) for x in dense_lines[:12]) + (" …" if len(dense_lines) > 12 else "") + ". The song-level status comes from these lines, never from an average.",
                              "estimate", False, "songwriter"))
    # length: a range of the time the lyrics may take, against the target duration (separate from the density of the lines; no fixed bars per line)
    if dn.fit in ("long", "short"):
        is_long = dn.fit == "long"
        out.append(HealthItem("length_fit", PRIORITY["length_fit"], "Whole song", 0, 0, 0, [], f"Whole song — {'Long' if is_long else 'Short'} for the target duration",
                              (f"Even at a fast pace the lyrics and instrumental parts take about {dn.need_low:.0f} s, more than the target. ACE-Step may cut the end, rush, or skip words. Fewer lines, or a longer duration, may help." if is_long else
                               f"The lyrics and instrumental parts take about {dn.need_low:.0f}-{dn.need_high:.0f} s, much less than the target. ACE-Step may fill the rest with music or repeat parts. A shorter duration, or more sections, may fit better."),
                              f"{dn.total_syllables} sung syllables at {dn.settings.bpm:g} BPM, sung between {SYL_PER_BEAT[2]:g} and {SYL_PER_BEAT[0]:g} syllables per beat (plus {REST_SHARE * 100:.0f} % rests) and about {dn.reserve_seconds:.0f} s of instrumental parts, is about "
                              f"{dn.need_low:.0f}-{dn.need_high:.0f} s; the target is {dn.settings.duration:g} s. Lines are not assumed to take equal time. The ACE-Step guide: slower tempos need more duration for the same lyrics, and when in doubt estimate longer.",
                              "estimate", False, "songwriter"))
    # 3 + 4: long lines and uneven phrasing, inside each section. The section's own level stays on the section chip; a line note names only that line.
    flagged = {x.line_number for x in dn.lines if x.phrasing_status in ("warning", "high")}      # a phrasing note already names the line: it is not reported a second time as "uneven"
    for k, ls in by_sec.items():
        name, idx = name_of.get(k, ("(start)", 0))
        if len(ls) >= 4:
            m = _median([x.syllable_count for x in ls])
            for x in ls:
                if x.line_number in flagged:
                    continue
                if abs(x.syllable_count - m) >= max(5 * f, 0.5 * m):
                    sure = x.status in ("verified", "manual_corrected")
                    longer = x.syllable_count > m
                    others = sorted(y.syllable_count for y in ls if y is not x)
                    out.append(HealthItem("uneven", PRIORITY["uneven"], name, idx, x.line_number, x.line_number, [x.line_number], f"{shown(name, idx)} — uneven phrase lengths",
                                          f"Line {x.line_number} has {'' if sure else 'about '}{x.syllable_count} syllables; the other lines are {others[0]}-{others[-1]}. It is {'much longer' if longer else 'much shorter'} than its section." + ("" if sure else " (The count is an estimate.)"),
                                          "Lines of one section that have a similar job usually sit comfortably when their lengths are close; two lines that simply differ a little are not reported.",
                                          "detected" if sure else "guideline", not sure, "songwriter"))
    for x in dn.lines:
        if x.phrasing_status in ("warning", "high"):
            nm, ix = x.section, x.section_index
            out.append(HealthItem("long_line", PRIORITY["long_line"], nm, ix, x.line_number, x.line_number, [x.line_number], f"{shown(nm, ix)} — line {x.line_number}: long phrase",
                                  f"Line {x.line_number} has a phrase of {'' if x.confidence == 'high' else 'about '}{x.longest_phrase} syllables with no space or punctuation to breathe at. This is a text-based phrasing risk; the melody is not known. A break may help.",
                                  x.detail, "detected" if x.confidence == "high" else "guideline", x.confidence != "high", "songwriter"))
            flagged.add(x.line_number)
    # A one-word line can be held or stressed. This does not predict a pitch, and at most two notes are kept.
    short_n = 0
    for x in ins.lines:
        if short_n >= _MAX_SHORT_WORDS:
            break
        word = _lone_function_word(x.text)
        if not word or x.syllable_count != 1:
            continue
        name, idx = name_of.get(x.sec_index, ("(start)", 0))
        sure = x.status in ("verified", "manual_corrected")
        out.append(HealthItem(
            "short_word", PRIORITY["short_word"], name, idx, x.line_number, x.line_number, [x.line_number],
            f"{shown(name, idx)} — short word on its own line",
            f'Sung line {x.line_number} is only the short word "{word}". A line that is only this word can be held or stressed. This is a phrasing note. It does not say the tone will be sung wrong. The melody is not known.',
            "The line is one short function word, so a hold or a stress can fall on it. This does not predict a pitch.",
            "detected" if sure else "guideline", not sure, "songwriter"))
        short_n += 1
    # 5: many sections for the duration
    sung_secs = [s for s in dn.sections if not s.instrumental and s.sung_lines]
    if dn.known and len(sung_secs) >= STRUCTURE_MIN_SECTIONS:
        per = dn.vocal_seconds / len(sung_secs)
        if per < STRUCTURE_SECONDS_PER_SECTION:
            reps = sorted({s.name for s in sung_secs if sum(1 for t in sung_secs if t.name == s.name) >= 2})
            out.append(HealthItem("structure", PRIORITY["structure"], "Structure", 0, 0, 0, [], f"Many sections for {st.duration:g} seconds",
                                  f"{len(sung_secs)} sung sections and {dn.total_lines} sung lines in {st.duration:g} s: about {per:.0f} s of singing per section. ACE-Step may shorten or skip some sections, for several reasons; the syllable count alone cannot predict it.",
                                  f"After the instrumental parts and rests there are about {dn.vocal_seconds:.0f} s for singing." + (f" Repeated sections: {', '.join(reps)}." if reps else "") +
                                  " The program makes no prediction and gives no probability of a skipped section.", "estimate", False, "music_producer"))
    out.extend(_structure_items(ins, name_of))
    out.sort(key=lambda h: (h.priority, h.first_line))
    return _cap_health(out)


def health_tone(items: list) -> str:
    """How a group of notes should look next to the section chip.

    "warning" — the section or the whole song is dense (the aggregate).
    "line" — one line only. A comfortable section keeps its comfortable chip; this note is not a second section warning.
    "info" — structure, or nothing.
    """
    if any(getattr(x, "kind", "") in ("song_density", "section_density") for x in items):
        return "warning"
    if any(getattr(x, "kind", "") in ("long_line", "uneven", "short_word") for x in items):
        return "line"
    return "info"


BASIS_NAME = {"detected": "Detected", "guideline": "Guideline", "estimate": "Estimate"}


def health_text(items: list, dn: Density) -> list:
    if not dn.known:
        rows = [f"Vocal density: unknown ({dn.why_unknown})"]
        # A one-word phrasing note does not need the density clock. The other notes do.
        rows += [f"[{BASIS_NAME[h.basis]}{', estimated count' if h.hedged else ''}] {h.title}. {h.summary}" for h in items if h.kind == "short_word"]
        return rows
    if not items:
        return ["No section, line or structure looks risky for these settings."]
    return [f"[{BASIS_NAME[h.basis]}{', estimated count' if h.hedged else ''}] {h.title}. {h.summary}" for h in items]


# ── text for the Analyze Song request ─────────────────────────────────────────────────────────────────

_STATUS_NOTE = {"manual_corrected": "manual", "verified": "dict", "estimated": "est", "unverified": "?"}
HEALTH_NOTE = ("Basis: Detected = counted by the program; Guideline = the ACE-Step 1.5 guide reference of roughly 6-10 syllables per line (a rough reference, not a rule); Estimate = a risk worked out from the target "
               "duration and stated assumptions. The melody is NOT known; skipping a section has several possible causes and is not predicted or given a probability.")


def paint_segments(text: str, ins: Insights) -> list:
    """[(file line, [(text, span or None)])]. A span is painted only when it is that exact slice of the original line."""
    by_line: dict = {}
    for sp in ins.spans:
        by_line.setdefault(sp.line, []).append(sp)
    out, pos = [], 0
    for n, raw in enumerate((text or "").split("\n"), 1):
        base, cur, segs = pos, 0, []
        pos += len(raw) + 1
        for sp in sorted(by_line.get(n, []), key=lambda x: x.start):
            a, b = sp.start - base, sp.end - base
            if a < cur or b > len(raw) or raw[a:b] != sp.text:
                continue
            if a > cur:
                segs.append((raw[cur:a], None))
            segs.append((raw[a:b], sp))
            cur = b
        if cur < len(raw):
            segs.append((raw[cur:], None))
        out.append((n, segs))
    return out


def lyric_report(text: str, corrections: "dict | None" = None) -> str:
    """Compact evidence for the Songwriter AI. The same Insights the highlight uses. Not a verdict and not an instruction to edit."""
    ins = analyze(text or "", corrections)
    if not ins.lines:
        return ""
    rows = [
        "LYRIC ANALYSIS (Python, from the Thai dictionary and spelling rules. Not a recording. Not a judgement of the song or of what the user wants.)",
        "verified = the syllable pieces are real syllables. est = the count is not fully confirmed. unverified = the ending sound could not be read.",
        "You may disagree with a row. If you do, say why. Do not state an unverified sound as a fact. A repeated word is a repeat, not a rhyme.",
        "line | syllables | count | end | other confirmed rhyme | repeated words",
    ]
    endings: dict = {}
    for li in ins.lines:
        if li.rhyme_status == "unverified" or not li.end_sound:
            end = "unverified" if li.rhyme_status == "unverified" else "-"
        else:
            end = li.end_sound
            word = li.end_sound.split(" ", 1)[0]
            endings.setdefault(word, []).append(li.line_number)
        if any(sp.role == "end" for sp in li.rhyme_spans):
            sp = next(sp for sp in li.rhyme_spans if sp.role == "end")
            end = f"{li.end_sound or (sp.text + ' ' + sp.group)} {sp.kind}"
            if sp.kind == "near":
                mates = [f"L{o.line_number:03d}" for o in ins.lines if o.line_number != li.line_number and any(p.role == "end" and p.group == sp.group for p in o.rhyme_spans)]
                if mates:
                    end += " with " + ",".join(mates)
        internal = [sp for sp in li.rhyme_spans if sp.role != "end"]
        other = ", ".join(f"{sp.text} {sp.group} {sp.kind}" for sp in internal) if internal else "-"
        reps = ", ".join(li.repeats) if li.repeats else "-"
        rows.append(f"L{li.line_number:03d} | {li.syllable_count} syl | {_STATUS_NOTE[li.status]} | {end} | {other} | {reps}")
    repeated_ends = [f"{w} on " + ", ".join(f"L{n:03d}" for n in nums) for w, nums in endings.items() if len(nums) >= 2]
    if repeated_ends:
        rows.append("Same word ends more than one line (a repeat, not a rhyme): " + "; ".join(repeated_ends))
    return "\n".join(rows)


def songwriter_block(ins: Insights, dn: "Density | None" = None) -> str:
    """Per sung line: words / syllables, how sure the count is, the rhyme the PROGRAM verified, the density level; then the ACE-Step lyrics health and the assumptions. Program facts: the model must not recount them."""
    rows = ["PROGRAM FACTS (Python. dict = dictionary split confirmed as syllables, est = not fully confirmed, manual = the user's correction. Not audio. You may disagree; say why. Do not state an unverified sound as a fact, and do not call a repeated word a rhyme.):",
            "line | words/syllables | count | rhyme (program sound check) | vocal density (estimate)"]
    by_line = {x.line_number: x for x in dn.lines} if dn else {}
    for li in ins.lines:
        r = ""
        if li.rhyme_spans:
            bits = []
            for sp in li.rhyme_spans:
                sound = sp.group
                if sp.role == "end" and " " in (li.end_sound or ""):
                    sound = li.end_sound.split(" ", 1)[1]
                bits.append(f"{sp.text}={sound} {sp.kind}{'' if sp.role == 'end' else ' internal'}")
            r = ", ".join(bits)
        elif li.rhyme_status == "unverified":
            r = "not checked (sound not readable)"
        elif li.rhyme_status == "none":
            r = "no rhyme found"
        d = by_line.get(li.line_number)
        rows.append(f"{li.line_number}| {li.word_count}/{li.syllable_count} | {_STATUS_NOTE[li.status]} | {r or '-'} | "
                    f"{(LEVEL_NAME[d.level] + (f' {d.rate:.1f} syl/s' if d.level != 'unknown' else '') + (' (much longer than its section)' if d.outlier else '')) if d else '-'}")
    if dn:
        items = [h for h in health(ins, dn) if h.spec == "songwriter" or h.kind in STRUCTURE_KINDS]
        rows.append("ACE-STEP LYRICS HEALTH (an estimated risk that ACE-Step rushes, merges or skips words; not a measurement):")
        rows += ["- " + t for t in health_text(items, dn)]
        rows.append(HEALTH_NOTE)
        rows.append("Assumptions: " + dn.assumptions)
    return chr(10).join(rows)


def producer_block(ins: Insights, dn: Density) -> str:
    """Per section: syllables and the density estimate, then the structure/duration warnings and what the estimate assumes."""
    rows = ["PROGRAM FACTS (estimates by Python from the lyrics and the settings; the melody is NOT known and the real timing of the music generator is not controlled by these numbers):",
            f"Settings: {dn.settings.bpm:g} BPM, {pulses_per_bar(dn.settings.timesig)[1]}, target {dn.settings.duration:g} s.",
            "section | sung lines | syllables | vocal density (estimate)"]
    for s in dn.sections:
        if s.instrumental:
            rows.append(f"{s.name}{'' if s.index == 0 else ' #' + str(s.index + 1)} | 0 | 0 | instrumental (no sung lines)")
        else:
            rows.append(f"{s.name}{'' if s.index == 0 else ' #' + str(s.index + 1)} | {s.sung_lines} | {s.syllables} | "
                        f"{LEVEL_NAME[s.level]}{(f' {s.rate:.1f} syl/s') if s.level != 'unknown' else ''}")
    if dn.known:
        rows.append(f"Whole song: {dn.total_syllables} syllables in about {dn.vocal_seconds:.0f} s of singing time → {dn.song_rate:.1f} syl/s on average: {LEVEL_NAME[dn.song_level]}.")
    rows.append("ACE-STEP LYRICS HEALTH:")
    rows += ["- " + t for t in health_text(health(ins, dn), dn)]
    rows.append(HEALTH_NOTE)
    rows.append("Assumptions: " + dn.assumptions)
    return chr(10).join(rows)
