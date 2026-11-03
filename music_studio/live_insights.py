"""Live Lyrics Insights: the numbers that can be worked out from the lyrics and the settings alone, with Python and PyThaiNLP (no LLM, no network).

  analyze(text, corrections)   per sung line: words, syllables, syllable segments, a rhyme group for the words that rhyme, a verification status and warnings
  timing(ins, ...)             the vocal density of every line and section and the song duration, from BPM, time signature, target duration, vocal speed and a bar allocation
  songwriter_block / producer_block   the same facts as plain text for the Analyze Song request (nothing here is read back from an AI)

Everything is read-only: nothing here changes the lyrics, the settings or an analysis result of the AI.

Honesty rules (the brief's data integrity): every line says HOW SURE its numbers are.
  manual_corrected  the user's own correction of the syllables (still valid for exactly this line and its neighbours)
  verified          Thai only, every word of the line is in the PyThaiNLP dictionary, so the split is dictionary-backed (it is still not a recording of how it is sung)
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
from . import rhyme_color as RC
from . import thai_rhyme as TR
from .compiler import TAG_LINE, section_label

PALETTE = RC.PALETTE
TH = re.compile(r"[฀-๿]+")
_WORD = re.compile(r"[฀-๿]+|[A-Za-z]+(?:['’][A-Za-z]+)*|\d+")
INSTRUMENTAL = ("intro", "outro", "instrumental", "interlude", "solo", "break", "bridge instrumental")
FUNCTION_WORDS = frozenset("ที่ มี ใน และ ก็ จะ แล้ว ไม่ ยัง เป็น ได้ ให้ ของ นะ ค่ะ ครับ อยู่ ไป มา แต่ ว่า ซึ่ง หรือ จน ถึง กับ จาก แค่ เธอ ฉัน ผม เรา มัน".split())    # common one-syllable words are not coloured as INTERNAL rhymes (they would colour every line); at the end of a line they still count
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


def line_core(masked: str) -> dict:
    """The tokens of ONE line (cached by its text): [(start, end, word, kind, syllables)], with offsets in `masked`."""
    hit = _core_cache.get(masked)
    if hit is not None:
        return hit
    toks, sure = [], True
    dic = _dictionary()
    for m in _WORD.finditer(masked):
        run = m.group(0)
        if TH.fullmatch(run):
            pos = m.start()
            for t in _thai_tokens(run):
                at = masked.index(t, pos)
                pos = at + len(t)
                if dic and t not in dic:
                    sure = False
                toks.append((at, at + len(t), t, "th", _thai_syllables(t)))
        elif run.isdigit():
            sure = False
            toks.append((m.start(), m.end(), run, "num", [run]))
        else:
            sure = False
            toks.append((m.start(), m.end(), run, "en", [run] * english_syllables(run)))
    core = {"tokens": toks, "sure": sure and bool(toks) and bool(dic)}
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
    """A [tag] glued to sung words. A tag on its own line, and a leading [Hold], are not this."""
    st = (raw or "").strip()
    if not st or (TAG_LINE.match(st) and not RC.HOLD_PREFIX.match(st)):
        return False
    rest = RC.HOLD_PREFIX.sub("", st, count=1)
    return bool(_BRACKET.search(rest))


def analyze(text: str, corrections: "dict | None" = None, revision: int = 0) -> Insights:
    """Every sung line with its counts and rhymes. `corrections` is the program's {line id: LA.Correction} (a correction applies only to the exact line it was made for)."""
    corrections = corrections or {}
    raw_lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sung = LA.sung_lines(text or "")
    ids = LA.line_ids(sung)
    by_file = {s.file_line: (s, i) for s, i in zip(sung, ids)}
    starts, pos = [], 0
    for r in raw_lines:
        starts.append(pos)
        pos += len(r) + 1
    lines, sections, cur, tag_lines = [], [], None, []
    for n, raw in enumerate(raw_lines, 1):
        st = raw.strip()
        if _hidden_bracket(raw):
            tag_lines.append(n)
        if st and TAG_LINE.match(st) and not RC.HOLD_PREFIX.match(st):
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
        masked = RC._mask(raw)
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
            warns.append("A word is English, a number or not in the Thai dictionary: its syllable count is an estimate.")
        li = LineInsight(n, s.num, cur.name, s.text, hashlib.sha1(s.text.encode("utf-8")).hexdigest()[:10], len(toks), syl, segs, status, [], "", "", warns, lid, len(sections) - 1)
        li._tokens = toks                                                           # (offsets in the line) kept for the rhyme step; not part of the data
        li._base = starts[n - 1]
        lines.append(li)
        cur.sung_lines += 1
        cur.words += li.word_count
        cur.syllables += syl
    _rhymes(lines)
    spans = [sp for li in lines for sp in li.rhyme_spans]
    groups: dict = {}
    for sp in spans:
        groups[sp.group] = groups.get(sp.group, 0) + 1
    for k, sec in enumerate(sections):
        _section_pattern(sec, [li for li in lines if li.sec_index == k])
    for li in lines:
        del li._tokens, li._base
    return Insights(hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12], lines, sections, spans, groups, revision, tag_lines)


def _last_thai(toks: list):
    for t in reversed(toks):
        if t[3] == "th":
            return t
    return None


def _rhymes(lines: list) -> None:
    """End rhymes (also across lines and sections) and internal rhymes of single syllable words, by the Thai sound check only."""
    ends = []
    for li in lines:
        t = _last_thai(li._tokens)
        last_tok = li._tokens[-1] if li._tokens else None
        if t is None or t is not last_tok:
            li.rhyme_status = "unverified" if li._tokens else ""            # the line ends in a word whose sound Python does not read (English / number)
            ends.append((li, None, None))
            continue
        r = _rime_of(t[2])
        if r is None:
            li.rhyme_status = "unverified"
        ends.append((li, t, r))
    by_key: dict = {}
    for li, t, r in ends:
        if r is not None:
            by_key.setdefault(_key(r), []).append((li, t, r))
    rimes = {k: v[0][2] for k, v in by_key.items()}
    near_of: dict = {}
    keys = sorted(rimes)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if TR._similar(rimes[a], rimes[b]):
                near_of.setdefault(a, set()).add(b)
                near_of.setdefault(b, set()).add(a)
    for k, members in by_key.items():
        kind, shown = None, k
        if len({t[2] for _li, t, _r in members}) >= 2:                  # the same word again (a repeated chorus line) is repetition, not a rhyme
            kind = "exact"
        elif k in near_of:
            kind = "near"
            shown = min([k] + sorted(near_of[k]))
        if kind is None:
            for li, t, r in members:
                li.rhyme_status = "none"
            continue
        for li, t, r in members:
            col = color_of(shown)
            li.rhyme_spans.append(RhymeSpan(li.line_number, li._base + t[0], li._base + t[1], t[2], k if kind == "exact" else shown, col, kind, "end"))
            li.rhyme_group, li.rhyme_status = k, "rhymed"
    for li in lines:                                                         # internal: a one-syllable word that has the sound of another word of the same line (including its end word)
        words = [t for t in li._tokens if t[3] == "th" and len(t[4]) == 1 and t[2] not in FUNCTION_WORDS]
        got = []
        for t in words:
            r = _rime_of(t[2])
            if r is not None:
                got.append((t, _key(r)))
        end_key = li.rhyme_group if any(sp.role == "end" for sp in li.rhyme_spans) else ""
        used = {(sp.start, sp.end) for sp in li.rhyme_spans}
        keyed: dict = {}
        for t, k in got:
            keyed.setdefault(k, []).append(t)
        for k, ts in keyed.items():
            if len(ts) + (1 if end_key == k and not any((li._base + t[0], li._base + t[1]) in used for t in ts) else 0) < 2:
                continue
            for t in ts:
                if (li._base + t[0], li._base + t[1]) in used:
                    continue
                li.rhyme_spans.append(RhymeSpan(li.line_number, li._base + t[0], li._base + t[1], t[2], k, color_of(k), "exact", "internal"))
        li.rhyme_spans.sort(key=lambda s: s.start)


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
# Question answered: are the lyrics too dense for this BPM, vocal speed and target duration, so that ACE-Step may rush, merge or skip words? Nothing here knows the melody, so it is an ESTIMATE:
#   1. time reserved for music without singing: the instrumental sections (Intro, Outro, Instrumental, Interlude, Solo, Break) take a stated number of bars (RESERVE_BARS) at this BPM and time signature
#   2. rests: REST_SHARE of the rest of the time is breath and pauses between phrases
#   3. what is left is shared equally between the sung lines: every sung line gets the same time slot (a phrase is the unit; the melody could stretch one and squeeze another)
#   4. syllables per second of a line / a section / the whole song = its syllables divided by its slot(s)
#   5. that rate is compared with what the chosen vocal speed manages comfortably (slow / normal / fast = 2.5 / 4 / 6 syllables per second); the levels are RATIOS of that, so the scale moves with the
#      style the user chose and one fixed threshold is never used for every genre. Cut-offs: under 60 % of the comfortable rate = comfortable, under 95 % = moderate, under 125 % = dense, above = very dense.
# BPM alone does not make a song fast: it sets how long the reserved instrumental bars last. The target duration sets how much time the singing has.
# Not enough data (no duration, fewer than 2 sung lines or MIN_SYLLABLES syllables, no time left for singing) is "unknown", never a guess.

VOCAL_SPEEDS = {"slow": 2.5, "normal": 4.0, "fast": 6.0}                        # syllables per second a singer is assumed to manage comfortably
LEVELS = ("comfortable", "moderate", "dense", "very_dense")
LEVEL_NAME = {"comfortable": "Comfortable", "moderate": "Moderate", "dense": "Dense", "very_dense": "Very Dense", "unknown": "Unknown"}
RESERVE_BARS = {"intro": 4, "outro": 4, "instrumental": 8, "interlude": 4, "solo": 8, "break": 2}       # bars of music without singing (stated assumptions)
REST_SHARE = 0.12
MIN_SYLLABLES = 8
CUTOFFS = (0.6, 0.95, 1.25)


def pulses_per_bar(timesig: str) -> tuple:
    """(pulses per bar, wording). BPM counts quarter notes in 2/4, 3/4 and 4/4. In 6/8 the bar has TWO dotted-quarter pulses: BPM is read as those pulses (a bar of 6 eighth notes is not 6 quarter-note beats)."""
    return {"2": (2, "2/4"), "3": (3, "3/4"), "4": (4, "4/4"), "6": (2, "6/8 (BPM counts the dotted quarter)")}.get(str(timesig), (4, "4/4"))


def seconds_per_bar(bpm: float, timesig: str) -> float:
    return pulses_per_bar(timesig)[0] * 60.0 / max(1.0, float(bpm))


def level_of(rate: float, speed: str) -> str:
    r = rate / VOCAL_SPEEDS.get(speed, 4.0)
    return LEVELS[0] if r < CUTOFFS[0] else LEVELS[1] if r < CUTOFFS[1] else LEVELS[2] if r < CUTOFFS[2] else LEVELS[3]


@dataclass
class DensitySettings:
    bpm: float = 120.0
    timesig: str = "4"                      # the values of music_options.TIMESIGS: "2" 2/4, "3" 3/4, "4" 4/4, "6" 6/8
    duration: float = 180.0                 # target seconds
    speed: str = "normal"                   # slow | normal | fast


@dataclass
class LineDensity:
    line_number: int
    syllables: int
    rate: float
    level: str
    outlier: bool = False                   # much longer than the other lines of its section


@dataclass
class SectionDensity:
    name: str
    index: int                              # which occurrence of this name (0 = the first)
    first_line: int
    last_line: int
    sung_lines: int
    words: int
    syllables: int
    rate: float
    ratio: float                            # rate / the comfortable rate of the chosen vocal speed
    level: str
    instrumental: bool
    reason: str = ""


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
    slot: float                             # seconds one sung line gets
    total_syllables: int
    total_lines: int
    song_rate: float
    song_level: str
    lines: list
    sections: list
    warnings: list
    assumptions: str


def density(ins: Insights, ts: DensitySettings) -> Density:
    spb = seconds_per_bar(ts.bpm, ts.timesig)
    sung = [li for li in ins.lines]
    total_syl, total_lines = sum(li.syllable_count for li in sung), len(sung)
    reserve = sum(next((v for k, v in RESERVE_BARS.items() if sec.name.lower().startswith(k)), 4) * spb for sec in ins.sections if sec.instrumental and not sec.sung_lines)
    vocal = max(0.0, (ts.duration - reserve) * (1.0 - REST_SHARE))
    why = ""
    if ts.duration <= 0 or ts.bpm <= 0:
        why = "The BPM or the target duration is missing."
    elif total_lines < 2 or total_syl < MIN_SYLLABLES:
        why = "Too few sung lines to judge."
    elif vocal < 3.0:
        why = "No time is left for singing after the instrumental parts."
    known = not why
    slot = vocal / total_lines if known else 0.0
    cap = VOCAL_SPEEDS.get(ts.speed, 4.0)
    sec_syl = {}
    for li in sung:
        sec_syl.setdefault(li.sec_index, []).append(li)
    lines_d, sections_d, warnings = [], [], []
    counter: dict = {}
    for k, sec in enumerate(ins.sections):
        n = counter.get(sec.name, 0)
        counter[sec.name] = n + 1
        ls = sec_syl.get(k, [])
        syl = sum(x.syllable_count for x in ls)
        rate = (syl / (len(ls) * slot)) if known and ls else 0.0
        lvl = level_of(rate, ts.speed) if known and ls else "unknown"
        med = sorted(x.syllable_count for x in ls)[len(ls) // 2] if ls else 0
        outs = []
        for x in ls:
            r = x.syllable_count / slot if known else 0.0
            lv = level_of(r, ts.speed) if known else "unknown"
            o = known and len(ls) >= 3 and lv in ("dense", "very_dense") and x.syllable_count >= max(med * 1.4, med + 3)
            lines_d.append(LineDensity(x.line_number, x.syllable_count, r, lv, o))
            if o:
                outs.append(x.line_number)
        reason = ""
        if known and ls:
            reason = (f"{syl} syllables in {len(ls)} lines: about {rate:.1f} syllables per second if every sung line gets the same {slot:.1f} s. "
                      f"A '{ts.speed}' singing speed manages about {cap:g} comfortably ({rate / cap * 100:.0f} %).")
        sd = SectionDensity(sec.name or "(start)", n, sec.first_line, max([x.line_number for x in ls] or [sec.first_line]), len(ls), sum(x.word_count for x in ls), syl, rate, rate / cap if known and ls else 0.0, lvl,
                            sec.instrumental and not ls, reason)
        sections_d.append(sd)
        if lvl in ("dense", "very_dense") and len(warnings) < 6:
            warnings.append(Warning_(sd.name, n, lvl, ls[0].line_number, sd.last_line, syl, len(ls), reason, outs))
    song_rate = total_syl / vocal if known else 0.0
    song_level = level_of(song_rate, ts.speed) if known else "unknown"
    if known and song_level in ("dense", "very_dense") and not warnings:
        warnings.append(Warning_("Whole song", 0, song_level, sung[0].line_number, sung[-1].line_number, total_syl, total_lines,
                                 f"{total_syl} syllables in about {vocal:.0f} s of singing time: about {song_rate:.1f} syllables per second on average ({song_rate / cap * 100:.0f} % of a comfortable '{ts.speed}' speed)."))
    assume = (f"{pulses_per_bar(ts.timesig)[1]}, {ts.bpm:g} BPM → {spb:.1f} s per bar; target {ts.duration:g} s; about {reserve:.0f} s kept for the instrumental parts "
              f"(a set number of bars each); {REST_SHARE * 100:.0f} % of the rest is breath and pauses; every sung line gets the same time slot; vocal speed '{ts.speed}' = {cap:g} syllables per second comfortably. "
              "The melody is not known, so this is an estimated risk, not a measurement.")
    return Density(ts, known, why, spb, reserve, vocal, slot, total_syl, total_lines, song_rate, song_level, lines_d, sections_d, warnings, assume)


# ── ACE-Step lyrics health ────────────────────────────────────────────────────────────────────────────
# Warnings for what may make ACE-Step 1.5 rush, merge or skip words, built from numbers the program really has. Three kinds of basis are kept apart:
#   detected   a fact from the counting engine (a line has N syllables, the others about M)
#   guideline  the ACE-Step 1.5 Musician's Guide reference of roughly 6-10 syllables per line (a rough reference, never a rule; scaled with the chosen vocal speed)
#   estimate   a risk worked out from the duration and the density assumptions (no melody is known)
# A line whose syllable count is itself an estimate (English word, number, word not in the dictionary) is reported with hedged wording ("about N").
# Skipping a section is something ACE-Step may do for several reasons: the program never predicts it and never states a probability.
LINE_REFERENCE = (6, 10)
SPEED_FACTOR = {"slow": 0.9, "normal": 1.0, "fast": 1.4}               # the reference scales with the style the user chose (a rap line is not judged like a ballad line)
MAX_HEALTH = 10
STRUCTURE_MIN_SECTIONS = 4
STRUCTURE_SECONDS_PER_SECTION = 12.0
PRIORITY = {"song_density": 1, "section_density": 2, "long_line": 3, "uneven": 4, "structure": 5, "duplicate_section": 6, "cue_conflict": 6, "tag_complexity": 6, "short_word": 7}
DENSITY_KINDS = frozenset({"song_density", "section_density", "long_line", "uneven", "short_word"})
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
    kind: str                 # song_density | section_density | long_line | uneven | short_word | structure | duplicate_section | cue_conflict | tag_complexity
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
    f = SPEED_FACTOR.get(st.speed, 1.0)
    lo, hi = LINE_REFERENCE
    limit = round(hi * f)
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
               "This section may need a faster delivery than a normal singing speed. Try fewer words, or split the phrases so there is room to breathe.")
        if whole:
            msg = "Overall the lyrics have many syllables for the target duration. Shorter lines or fewer lines may help, or a longer duration."
        out.append(HealthItem("song_density" if whole else "section_density", PRIORITY["song_density" if whole else "section_density"], w.section, w.index, 0 if whole else w.first_line, w.last_line,
                              list(range(w.first_line, w.last_line + 1)) if not whole else [], f"{shown(w.section, w.index)} — {'Dense' if w.level == 'dense' else 'High risk'}", msg, w.reason, "estimate", False, "songwriter"))
    if dn.known and dn.song_level in ("dense", "very_dense") and not any(h.kind == "song_density" for h in out):
        out.append(HealthItem("song_density", 1, "Whole song", 0, 0, 0, [], "Whole song — Dense", "Overall the lyrics have many syllables for the target duration. Shorter lines or fewer lines may help, or a longer duration.",
                              f"{dn.total_syllables} syllables in about {dn.vocal_seconds:.0f} s of singing time: about {dn.song_rate:.1f} syllables per second on average.", "estimate", False, "songwriter"))
    # 3 + 4: long lines and uneven phrasing, inside each section. The section's own level stays on the section chip; a line note names only that line.
    song_med = _median([x.syllable_count for x in ins.lines])
    line_level = {x.line_number: x.level for x in dn.lines}
    flagged: set = set()
    for k, ls in by_sec.items():
        name, idx = name_of.get(k, ("(start)", 0))
        sec_level = dn.sections[k].level if k < len(dn.sections) else "unknown"
        for x in ls:
            others = [y.syllable_count for y in ls if y is not x] if len(ls) >= 4 else [y.syllable_count for y in ins.lines if y is not x]
            m = _median(others)
            sure = x.status in ("verified", "manual_corrected")
            if x.syllable_count > limit * (1.0 if sure else 1.2) and m and x.syllable_count >= 1.25 * m:
                est = "" if sure else "about "
                out.append(HealthItem("long_line", PRIORITY["long_line"], name, idx, x.line_number, x.line_number, [x.line_number], f"{shown(name, idx)} — line {x.line_number} is long",
                                      _line_length_summary(x.line_number, est, x.syllable_count, m, len(ls) >= 4, sec_level, line_level.get(x.line_number, "unknown"))
                                      + ("" if sure else " (The count is an estimate.)"),
                                      f"Guide reference: about {lo}-{hi} syllables per line, a rough reference and not a rule; for the vocal speed '{st.speed}' this program looks at lines above {limit} that are also clearly longer than their neighbours.",
                                      "detected" if sure else "guideline", not sure, "songwriter"))
                flagged.add(x.line_number)
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


def songwriter_block(ins: Insights, dn: "Density | None" = None) -> str:
    """Per sung line: words / syllables, how sure the count is, the rhyme the PROGRAM verified, the density level; then the ACE-Step lyrics health and the assumptions. Program facts: the model must not recount them."""
    rows = ["PROGRAM FACTS (counted by Python from the lyrics; do not recount; est = estimate, manual = the user's correction, dict = dictionary-backed split):",
            "line | words/syllables | count | rhyme (program sound check) | vocal density (estimate)"]
    by_line = {x.line_number: x for x in dn.lines} if dn else {}
    for li in ins.lines:
        r = ""
        if li.rhyme_spans:
            r = ", ".join(f"{sp.text}={sp.group} {sp.kind}{'' if sp.role == 'end' else ' internal'}" for sp in li.rhyme_spans)
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
            f"Settings: {dn.settings.bpm:g} BPM, {pulses_per_bar(dn.settings.timesig)[1]}, target {dn.settings.duration:g} s, vocal speed {dn.settings.speed}.",
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
