"""Songwriter chat tools. The model decides what to ask. This module only runs a named function and returns text.

A call is the literal text <heyPY>function_name(arguments)</heyPY>. Names come from the registry below.
Nothing here uses eval or exec, calls an LLM, or edits the lyrics. Counts and rhymes are the Live Insights
and Thai spelling checks the editor already uses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import lyric_analysis as LA
from . import live_insights as LI
from . import thai_rhyme as TR
from .compiler import HOLD_PREFIX, HOLD_SUFFIX, TAG_LINE

MAX_TOOL_ROUNDS = 3
MAX_CALLS = 8
LIMIT_NOTE = "The tool rounds reached their limit, so this answer uses only the measurements already returned."
_OPEN = "<heypy>"
_CLOSE = "</heypy>"
_CALL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$", re.DOTALL)
_LINE_ID = re.compile(r"^(?:L|line\s*)?(\d+)$", re.IGNORECASE)
_SECTION_ID = re.compile(r"^S(\d+)$", re.IGNORECASE)
_OCC = re.compile(r"^(.+?)#(\d+)$")

FOLLOW = (
    "The lines above are tool results for you, not a message the user typed. "
    "Use them in your own words. Do not paste this block, and do not show a <heyPY> tag. "
    "A number you were not given is still unknown. "
    "If you still lack a measurement you need, call a tool again. "
    "If you have enough, answer the user now. "
    "Do not change the lyrics unless the user asked for an edit. "
    "A lyric edit they asked for belongs in that answer, as an action tag, not inside a tool call."
)


class ToolFail(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ToolLoopError(Exception):
    """A later model call in the tool loop failed. `turns` are the exchanges that already returned."""

    def __init__(self, turns: list, steps: list, original: BaseException):
        super().__init__(str(original))
        self.turns = turns
        self.steps = steps
        self.original = original


@dataclass
class SongContext:
    """Facts the open song already has. Tools read this. They do not interpret the user's question."""

    lyrics: str
    title: str = ""
    genre: str = ""
    mood: str = ""
    language: str = ""
    bpm: "float | None" = None
    timesig: str = ""
    duration: "float | None" = None
    vocal_speed: str = ""
    vocal_speed_note: str = ""
    corrections: "dict | None" = None
    _ins: object = field(default=None, repr=False)

    def insights(self):
        if self._ins is None:
            self._ins = LI.analyze(self.lyrics or "", self.corrections or {})
        return self._ins


def _block(label: str, status: str, rows: list) -> str:
    return "\n".join([f"Tool result: {label}", f"Status: {status}", *rows])


def _raw_lines(text: str) -> list:
    return [row[3] for row in LA.original_lines(text or "")]


def _hold_attached(raw: str) -> bool:
    st = (raw or "").strip()
    if not st or TAG_LINE.match(st):
        return False
    return bool(HOLD_PREFIX.match(st) or HOLD_SUFFIX.search(st))


def _norm_sig(value) -> str:
    s = str(value if value is not None else "").strip().lower().replace(" ", "")
    table = {"2": "2", "2/4": "2", "3": "3", "3/4": "3", "4": "4", "4/4": "4", "6": "6", "6/8": "6"}
    if s not in table:
        raise ToolFail(f"Time signature {value!r} is not 2/4, 3/4, 4/4 or 6/8. Nothing was assumed.")
    return table[s]


def _need_str(pos: list, kw: dict, key: str, label: str) -> str:
    if key in kw and pos:
        raise ToolFail(f"Pass {label} once, either as the argument or as {key}=.")
    if key in kw:
        value = kw[key]
    elif len(pos) == 1:
        value = pos[0]
    elif not pos:
        raise ToolFail(f"{label} is required. Nothing was assumed.")
    else:
        raise ToolFail(f"{label} takes one argument. Nothing was guessed.")
    extra = [k for k in kw if k != key]
    if extra:
        raise ToolFail(f"Unknown argument {extra[0]!r}. Nothing was guessed.")
    if not isinstance(value, str) or not value.strip():
        raise ToolFail(f"{label} must be a quoted string. Nothing was guessed.")
    return value.strip()


def _no_args(pos: list, kw: dict, name: str) -> None:
    if pos or kw:
        raise ToolFail(f"{name} takes no arguments. Nothing was guessed.")


def _file_line(ctx: SongContext, n: int) -> str:
    rows = _raw_lines(ctx.lyrics)
    if n < 1 or n > len(rows):
        raise ToolFail(f"There is no line L{n:03d}. Nothing was guessed.")
    return rows[n - 1]


def _parse_line_id(token: str) -> int:
    m = _LINE_ID.fullmatch(token.strip())
    if not m:
        raise ToolFail(f"Line id {token!r} is not L001 (or a line number). Nothing was guessed.")
    return int(m.group(1))


def _occurrence(ins, index: int) -> int:
    name = (ins.sections[index].name or "").casefold()
    return sum(1 for s in ins.sections[:index + 1] if (s.name or "").casefold() == name)


def _match_sections(ins, scope: str) -> list:
    q = (scope or "").strip()
    if not q:
        raise ToolFail("The scope is empty. Nothing was assumed.")
    low = q.casefold()
    if low in ("all", "song", "*"):
        return list(range(len(ins.sections)))
    m = _SECTION_ID.fullmatch(q)
    if m:
        i = int(m.group(1)) - 1
        if not 0 <= i < len(ins.sections):
            raise ToolFail(f"There is no section {q}. Nothing was guessed.")
        return [i]
    occ = None
    m2 = _OCC.fullmatch(q)
    if m2:
        q = m2.group(1).strip()
        occ = int(m2.group(2))
        low = q.casefold()
        if occ < 1:
            raise ToolFail("A section occurrence starts at 1. Nothing was guessed.")
    exact = [i for i, s in enumerate(ins.sections) if (s.name or "").casefold() == low]
    hits = exact or [i for i, s in enumerate(ins.sections) if low and (s.name or "").casefold().startswith(low)]
    if occ is not None:
        hits = [i for i in hits if _occurrence(ins, i) == occ and (ins.sections[i].name or "").casefold() == low]
    if not hits:
        raise ToolFail(f"No section matches {scope!r}. Nothing was guessed.")
    return hits


def _section_label(ins, index: int) -> str:
    sec = ins.sections[index]
    name = sec.name or "(before the first section)"
    return f"{name} (S{index + 1:03d})"


def _lines_of_sections(ins, indexes: list) -> list:
    want = set(indexes)
    return [li for li in ins.lines if li.sec_index in want]


def _scope(ctx: SongContext, token: str):
    """(sung lines, label, note) for a count or a sound check. A line id that is not sung returns no lines and a note."""
    ins = ctx.insights()
    q = token.strip()
    if _LINE_ID.fullmatch(q) or q[:1].upper() == "L" and _LINE_ID.fullmatch(q):
        n = _parse_line_id(q)
        raw = _file_line(ctx, n)
        hit = [li for li in ins.lines if li.line_number == n]
        if hit:
            return hit, f"L{n:03d}", ""
        shown = (raw or "").strip() or "(empty)"
        return [], f"L{n:03d}", f"L{n:03d} is {shown!r}, which is not a sung line. Section tags and vocal cues are not sung words."
    indexes = _match_sections(ins, q)
    lines = _lines_of_sections(ins, indexes)
    labels = [_section_label(ins, i) for i in indexes]
    note = ""
    if q.casefold() not in ("all", "song", "*") and len(indexes) > 1:
        note = f"The name is written {len(indexes)} times. Each written occurrence is listed once. No extra repeat was applied."
    elif q.casefold() in ("all", "song", "*"):
        note = "The whole lyric text, each written line once. No extra repeat was applied."
    else:
        note = "This written occurrence once. No extra repeat was applied."
    return lines, "; ".join(labels) if labels else "(no section)", note


def _confidence(lines: list) -> tuple:
    if not lines:
        return "exact", "No sung line in this scope."
    skipped = [li for li in lines if li.status == "unverified"]
    counted = [li for li in lines if li.status != "unverified"]
    statuses = {li.status for li in counted}
    if skipped and not counted:
        return "unverified", "No line in this scope could be counted. No number was invented."
    if skipped:
        return "estimated", "Some lines could not be counted and are excluded from the total. What remains includes an estimate where the split is not confirmed."
    if statuses <= {"verified"}:
        return "verified", "The dictionary split confirmed these syllables. This is not a recording."
    if statuses <= {"verified", "manual_corrected"}:
        return "manual", "The total uses a count you corrected. This is not a recording."
    return "estimated", "At least one word is English, a number, or a split the spelling rules do not confirm. The total is an estimate, not an exact sung count."


def _totals(ctx: SongContext, lines: list) -> list:
    skipped = [li for li in lines if li.status == "unverified"]
    counted = [li for li in lines if li.status != "unverified"]
    conf, why = _confidence(lines)
    words = sum(li.word_count for li in counted)
    syl = sum(li.syllable_count for li in counted)
    rows = [f"Sung lines: {len(lines)}"]
    if conf == "unverified":
        rows.append("Total words: not counted")
        rows.append("Total syllables: not counted")
    else:
        rows.append(f"Total words: {words}")
        rows.append(f"Total syllables: {syl}")
    if skipped:
        rows.append("Not counted: " + ", ".join(f"L{li.line_number:03d}" for li in skipped))
    holds = []
    raws = _raw_lines(ctx.lyrics)
    for li in lines:
        if 1 <= li.line_number <= len(raws) and _hold_attached(raws[li.line_number - 1]):
            holds.append(f"L{li.line_number:03d}")
    if holds:
        rows.append("[Hold] is attached on " + ", ".join(holds) + " and was not counted as a word.")
    rows.append(f"Counting confidence: {conf}")
    rows.append("Limit: " + why + " Section tags and vocal cues are not sung words. A repeated section that is not written again was not added.")
    return rows


def _get_song_settings(ctx: SongContext, pos, kw, label) -> str:
    _no_args(pos, kw, "get_song_settings")
    bpm = f"{ctx.bpm:g}" if ctx.bpm else "missing"
    dur = f"{ctx.duration:g} seconds (target)" if ctx.duration else "missing"
    if ctx.timesig:
        try:
            sig = LI.pulses_per_bar(_norm_sig(ctx.timesig))[1]
        except ToolFail:
            sig = f"{ctx.timesig} (not 2/4, 3/4, 4/4 or 6/8)"
    else:
        sig = "missing"
    speed = ctx.vocal_speed or "missing"
    rows = [
        f"BPM: {bpm}",
        f"Time signature: {sig}",
        f"Target duration: {dur}",
        f"Genre: {ctx.genre or 'missing'}",
        f"Language: {ctx.language or 'missing'}",
        f"Mood: {ctx.mood or 'missing'}",
        f"Vocal speed: {speed}",
        f"Vocal speed note: {ctx.vocal_speed_note or 'not provided'}",
        "Bars stored: no",
        "Audio timeline stored: no",
        "Limit: these are the song settings and the Live Insights vocal-speed choice when one was passed. They are not a measurement of the sung performance. Sung time cannot be exact without a bar count or a timeline.",
    ]
    return _block(label, "OK", rows)


def _get_song_structure(ctx: SongContext, pos, kw, label) -> str:
    _no_args(pos, kw, "get_song_structure")
    ins = ctx.insights()
    if not ins.sections:
        return _block(label, "INCOMPLETE", ["Sections: none", "Limit: no section tag was found. Nothing was invented."])
    rows = ["Order (each tag once, as written):"]
    for i, sec in enumerate(ins.sections):
        rows.append(f"S{i + 1:03d} | {sec.name or '(before the first section)'} | written occurrence {_occurrence(ins, i)} | sung lines {sec.sung_lines} | tag line {sec.first_line or '-'}")
    names: dict = {}
    for sec in ins.sections:
        key = sec.name or "(before the first section)"
        names[key] = names.get(key, 0) + 1
    again = [f"{name} x{n}" for name, n in names.items() if n > 1]
    rows.append("Written more than once: " + (", ".join(again) if again else "none"))
    rows.append("Limit: a section that appears twice is written twice. No extra performance repeat was assumed.")
    return _block(label, "OK", rows)


def _get_section(ctx: SongContext, pos, kw, label) -> str:
    token = _need_str(pos, kw, "section_id", "section_id")
    ins = ctx.insights()
    indexes = _match_sections(ins, token)
    if len(indexes) != 1:
        names = ", ".join(_section_label(ins, i) + f"#{_occurrence(ins, i)}" for i in indexes)
        raise ToolFail(f"{token!r} matches more than one section ({names}). Ask for one id, such as Chorus#2. Nothing was merged.")
    i = indexes[0]
    sec = ins.sections[i]
    lines = _lines_of_sections(ins, [i])
    rows = [
        f"Section: {_section_label(ins, i)}",
        f"Written occurrence: {_occurrence(ins, i)}",
        f"Tag: {sec.tag or '-'}",
        f"Tag line: {sec.first_line or '-'}",
        f"Instrumental: {'yes' if sec.instrumental and not lines else 'no'}",
        "Sung line ids: " + (", ".join(f"L{li.line_number:03d}" for li in lines) if lines else "none"),
    ]
    rows += _totals(ctx, lines)
    rows.append("Limit: this is one written occurrence. It does not say the section is sung again.")
    return _block(label, "OK", rows)


def _get_line(ctx: SongContext, pos, kw, label) -> str:
    token = _need_str(pos, kw, "line_id", "line_id")
    n = _parse_line_id(token)
    raw = _file_line(ctx, n)
    ins = ctx.insights()
    hit = [li for li in ins.lines if li.line_number == n]
    if not hit:
        shown = (raw or "").strip() or "(empty)"
        return _block(label, "INCOMPLETE", [
            f"Line: L{n:03d}",
            f"Text: {shown}",
            "Sung words: 0",
            "Sung syllables: not counted",
            "Limit: this line is not sung (a section tag, a vocal cue, a blank line, or a [Hold] with no words). It was not turned into a lyric.",
        ])
    li = hit[0]
    conf, why = _confidence([li])
    hold = "yes" if _hold_attached(raw) else "no"
    syl = "not counted" if li.status == "unverified" else str(li.syllable_count)
    words = "not counted" if li.status == "unverified" else str(li.word_count)
    rows = [
        f"Line: L{n:03d}",
        f"Section: {li.section or '-'}",
        f"Text: {li.text}",
        f"Sung words: {words}",
        f"Sung syllables: {syl}",
        f"Count status: {li.status}",
        f"End sound: {li.end_sound or '-'}",
        f"Rhyme status: {li.rhyme_status or '-'}",
        f"[Hold] attached: {hold}",
        f"Counting confidence: {conf}",
        "Limit: " + why + " [Hold] is not a sung word. This line was not rewritten.",
    ]
    return _block(label, "OK" if conf != "unverified" else "INCOMPLETE", rows)


def _count(ctx: SongContext, pos, kw, label, kind: str) -> str:
    token = _need_str(pos, kw, "scope", "scope")
    lines, where, note = _scope(ctx, token)
    rows = [f"Scope: {where}"]
    if note:
        rows.append(note)
    if kind == "lengths":
        conf, why = _confidence(lines)
        rows.append(f"Sung lines: {len(lines)}")
        if not lines:
            rows.append("Counting confidence: exact")
            rows.append("Limit: " + why)
            return _block(label, "OK", rows)
        rows.append("line | characters | words | syllables | count")
        shown = lines[:80]
        for li in shown:
            if li.status == "unverified":
                rows.append(f"L{li.line_number:03d} | {len(li.text)} | not counted | not counted | unverified")
            else:
                rows.append(f"L{li.line_number:03d} | {len(li.text)} | {li.word_count} | {li.syllable_count} | {li.status}")
        if len(lines) > len(shown):
            rows.append(f"{len(lines) - len(shown)} further sung lines were not listed. The totals below still include them.")
        rows += _totals(ctx, lines)
        rows.append("Limit: " + why + " Character length is the sung text after a section tag or a vocal cue is left out. It is not a beauty score.")
        return _block(label, "OK" if conf != "unverified" else "INCOMPLETE", rows)
    rows += _totals(ctx, lines)
    conf = "unverified" if any(r.startswith("Counting confidence: unverified") for r in rows) else "OK"
    status = "INCOMPLETE" if "Counting confidence: unverified" in "\n".join(rows) and "Total syllables: not counted" in "\n".join(rows) else "OK"
    _ = conf
    return _block(label, status, rows)


def _one_word(pos, kw) -> str:
    word = _need_str(pos, kw, "word", "word")
    if any(ch.isspace() for ch in word):
        raise ToolFail("Pass one word, not a line. Nothing was split for you.")
    return word


def _get_pronunciation(ctx: SongContext, pos, kw, label) -> str:
    word = _one_word(pos, kw)
    _ = ctx
    core = LI.line_core(word)
    toks = core["tokens"]
    if not toks:
        return _block(label, "INCOMPLETE", [
            f"Word: {word}",
            "Syllables: not counted",
            "Counting confidence: unverified",
            "Limit: the dictionary and the spelling rules could not read this word. No syllable count was invented. This is not a recording.",
        ])
    pieces = []
    est = not core["sure"]
    for t in toks:
        pieces.extend(t[4] or [t[2]])
        if t[3] != "th":
            est = True
    sound = ""
    if TR.has_thai(word):
        sound = TR.describe_rime(word)
    conf = "estimated" if est or not sound and TR.has_thai(word) else ("verified" if TR.has_thai(word) and core["sure"] else "estimated")
    if not TR.has_thai(word):
        conf = "estimated"
    rows = [
        f"Word: {word}",
        "Segments: " + " / ".join(pieces),
        f"Syllables: {len(pieces)}",
        f"End sound: {sound or 'unverified'}",
        "Stress: not available",
        f"Counting confidence: {conf}",
        "Limit: Thai uses the dictionary and the spelling rules. English uses a vowel-group estimate, and stress is not known. Mixed text keeps that limit on each part. This is not a recording, and it is not a rhyme score.",
    ]
    return _block(label, "OK", rows)


def _song_words(ctx: SongContext) -> list:
    found = []
    for li in ctx.insights().lines:
        for t in LI.line_core(li.text)["tokens"]:
            if t[3] == "th" and t[2] not in found:
                found.append(t[2])
    return found


def _get_rhyme_candidates(ctx: SongContext, pos, kw, label) -> str:
    word = _one_word(pos, kw)
    if not TR.has_thai(word) or not TR.describe_rime(word):
        return _block(label, "INCOMPLETE", [
            f"Word: {word}",
            "Candidates: none",
            "Limit: the spelling rules could not read this word, or it is not Thai. No rhyme was invented. This is not a thesaurus and not a beauty score.",
        ])
    got = []
    for other in _song_words(ctx):
        if other == word:
            continue
        rel, detail = TR.sound_relation(word, other)
        if rel in ("exact", "near"):
            got.append((other, rel, detail))
        if len(got) >= 12:
            break
    rows = [f"Word: {word}", f"End sound: {TR.describe_rime(word)}"]
    if not got:
        rows.append("Candidates: none in this lyric text")
    else:
        rows.append("Candidates (other sung words in this text):")
        rows += [f"- {w} | {rel} | {detail}" for w, rel, detail in got]
    rows.append("Limit: only words already sung in this text. Not a thesaurus. Exact and near are sound classes, not a score for how good the rhyme is. No extra word was invented.")
    return _block(label, "OK", rows)


def _analyze_rhyme_sounds(ctx: SongContext, pos, kw, label) -> str:
    token = _need_str(pos, kw, "scope", "scope")
    lines, where, note = _scope(ctx, token)
    rows = [f"Scope: {where}"]
    if note:
        rows.append(note)
    if not lines:
        rows.append("End sounds: none")
        rows.append("Limit: no sung line in this scope. This is not a beauty score.")
        return _block(label, "INCOMPLETE", rows)
    rows.append("line | text | end sound | rhyme status | count")
    for li in lines[:80]:
        rows.append(f"L{li.line_number:03d} | {li.text} | {li.end_sound or '-'} | {li.rhyme_status or '-'} | {li.status}")
    pairs = []
    for li in lines:
        for sp in li.rhyme_spans:
            if sp.role == "end":
                pairs.append(f"L{li.line_number:03d} {sp.text} | {sp.kind} | {sp.group}")
    rows.append("Confirmed end-sound links: " + ("; ".join(pairs) if pairs else "none"))
    rows.append("Limit: sound classes from the Thai spelling rules. A repeated word is a repeat, not a rhyme. English endings stay unverified. This is not a score for beauty, originality, or the song. You have not heard the vocal.")
    return _block(label, "OK", rows)


class _Missing(Exception):
    """The song does not have a number this call needs. The call itself was readable."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _timing_args(ctx: SongContext, pos: list, kw: dict) -> tuple:
    extra = [k for k in kw if k not in ("bpm", "time_signature")]
    if extra:
        raise ToolFail(f"Unknown argument {extra[0]!r}. Nothing was guessed.")
    if len(pos) > 2:
        raise ToolFail("calculate_bar_duration takes bpm and time_signature. Nothing was guessed.")
    bpm, sig, src = None, "", []
    passed_sig = False
    if "bpm" in kw:
        bpm = kw["bpm"]
        src.append("bpm argument")
    if "time_signature" in kw:
        sig = kw["time_signature"]
        src.append("time_signature argument")
        passed_sig = True
    nums = [p for p in pos if isinstance(p, (int, float)) and not isinstance(p, bool)]
    strs = [p for p in pos if isinstance(p, str)]
    if len(nums) + len(strs) != len(pos):
        raise ToolFail("bpm must be a number and time_signature a quoted string. Nothing was guessed.")
    if nums:
        if bpm is not None:
            raise ToolFail("bpm was passed twice. Nothing was guessed.")
        bpm = nums[0]
        src.append("bpm argument")
    if strs:
        if sig != "":
            raise ToolFail("time_signature was passed twice. Nothing was guessed.")
        sig = strs[0]
        src.append("time_signature argument")
        passed_sig = True
    if bpm is None:
        bpm = ctx.bpm
        src.append("song BPM")
    if not sig:
        sig = ctx.timesig
        src.append("song time signature")
    try:
        bpm_num = float(bpm) if bpm is not None else None
    except (TypeError, ValueError):
        raise ToolFail("bpm must be a number. Nothing was guessed.") from None
    if bpm_num is None or bpm_num <= 0:
        raise _Missing("BPM is missing. Seconds per bar were not calculated.")
    if not sig:
        raise _Missing("Time signature is missing. Seconds per bar were not calculated.")
    try:
        norm = _norm_sig(sig)
    except ToolFail as ex:
        if passed_sig:
            raise
        raise _Missing(ex.message) from ex
    return bpm_num, norm, ", ".join(src)


def _calculate_bar_duration(ctx: SongContext, pos, kw, label) -> str:
    try:
        bpm, sig, source = _timing_args(ctx, pos, kw)
    except _Missing as ex:
        return _block(label, "INCOMPLETE", [
            "Seconds per bar: not calculated",
            f"Why: {ex.message}",
            "Limit: no number was invented. No bar count and no audio timeline are stored, so sung time is not exact.",
        ])
    pulses, wording = LI.pulses_per_bar(sig)
    seconds = pulses * 60.0 / bpm
    return _block(label, "OK", [
        f"BPM: {bpm:g}",
        f"Time signature: {wording}",
        f"Pulses per bar: {pulses}",
        f"Seconds per bar: {seconds:.3f}",
        f"Source: {source}",
        "Limit: this is the length of one bar at this BPM. It is not the sung length of the song. No bar count and no audio timeline are stored, so sung time is not exact.",
    ])


def _speed(ctx: SongContext) -> str:
    if not ctx.vocal_speed:
        return ""
    if ctx.vocal_speed not in LI.VOCAL_SPEEDS:
        raise ToolFail(f"Vocal speed {ctx.vocal_speed!r} is not slow, normal or fast. The density level was not calculated.")
    return ctx.vocal_speed


def _calculate_vocal_density(ctx: SongContext, pos, kw, label) -> str:
    token = _need_str(pos, kw, "scope", "scope")
    if not ctx.bpm or ctx.bpm <= 0 or not ctx.duration or ctx.duration <= 0 or not ctx.timesig:
        return _block(label, "INCOMPLETE", [
            "Vocal density: unknown",
            "Why: BPM, time signature or target duration is missing.",
            "Syllables per second: not calculated",
            "Sung time: not calculated",
            "Limit: no number was invented. No bar count and no audio timeline are stored, so sung time would not be exact even when the settings exist.",
        ])
    speed = _speed(ctx)
    try:
        sig = _norm_sig(ctx.timesig)
    except ToolFail as ex:
        return _block(label, "INCOMPLETE", [
            "Vocal density: unknown",
            f"Why: {ex.message}",
            "Syllables per second: not calculated",
            "Sung time: not calculated",
            "Limit: no number was invented. No bar count and no audio timeline are stored, so sung time is not exact.",
        ])
    ins = ctx.insights()
    lines, where, note = _scope(ctx, token)
    dn = LI.density(ins, LI.DensitySettings(bpm=float(ctx.bpm), timesig=sig, duration=float(ctx.duration), speed=speed or "normal"))
    rows = [f"Scope: {where}"]
    if note:
        rows.append(note)
    if not dn.known:
        rows += [
            "Vocal density: unknown",
            f"Why: {dn.why_unknown}",
            "Syllables per second: not calculated",
            "Sung time: not calculated",
            "Limit: " + _assumption(ctx, dn, speed, judged=bool(speed)),
        ]
        return _block(label, "INCOMPLETE", rows)
    indexes = []
    if token.strip().casefold() not in ("all", "song", "*"):
        indexes = _match_sections(ins, token) if not _LINE_ID.fullmatch(token.strip()) else []
    rows.append(f"Song syllables (written sung lines): {dn.total_syllables}")
    rows.append(f"Song sung lines: {dn.total_lines}")
    rows.append(f"Estimated singing time under the assumptions: {dn.vocal_seconds:.1f} s")
    rows.append(f"Estimated song rate: {dn.song_rate:.2f} syllables per second")
    if speed:
        rows.append(f"Song comfort level: {LI.LEVEL_NAME.get(dn.song_level, dn.song_level)} (a ratio against vocal speed '{speed}', not a verdict)")
    else:
        rows.append("Song comfort level: not judged (vocal speed was not provided, and normal was not assumed)")
    if indexes:
        for i in indexes:
            sd = dn.sections[i]
            rows.append(
                f"{_section_label(ins, i)} | sung lines {sd.sung_lines} | syllables {sd.syllables} | "
                + (f"about {sd.rate:.2f} syllables per second" if dn.known and sd.sung_lines else "no sung lines")
            )
            if speed and sd.sung_lines:
                rows.append(f"Comfort level for {_section_label(ins, i)}: {LI.LEVEL_NAME.get(sd.level, sd.level)} (estimate, not a verdict)")
    elif _LINE_ID.fullmatch(token.strip()) and lines:
        li = lines[0]
        slot = dn.slot
        if slot > 0 and li.status != "unverified":
            rate = li.syllable_count / slot
            rows.append(f"L{li.line_number:03d} estimated rate: {rate:.2f} syllables per second if it receives the same slot as every other sung line")
        else:
            rows.append(f"L{li.line_number:03d} rate: not calculated")
    rows.append("Limit: " + _assumption(ctx, dn, speed, judged=bool(speed)))
    rows.append("This density is not a reason by itself to cut words, and it is not a score for the song.")
    return _block(label, "OK", rows)


def _assumption(ctx: SongContext, dn, speed: str, judged: bool) -> str:
    _ = ctx
    text = (
        f"{LI.pulses_per_bar(dn.settings.timesig)[1]}, {dn.settings.bpm:g} BPM, about {dn.sec_per_bar:.1f} s per bar; "
        f"target {dn.settings.duration:g} s; about {dn.reserve_seconds:.0f} s kept for empty instrumental sections; "
        f"{LI.REST_SHARE * 100:.0f}% of the remainder is breath and pauses; every sung line is given the same slot."
    )
    if judged and speed:
        text += f" Vocal speed '{speed}' is compared at about {LI.VOCAL_SPEEDS[speed]:g} syllables per second."
    else:
        text += " No vocal speed was provided, so no comfort level was chosen."
    text += " No bar count and no audio timeline are stored. The seconds are an estimate under these assumptions, not a measured sung time. The melody is not known."
    return text


@dataclass(frozen=True)
class Tool:
    name: str
    purpose: str
    arguments: str
    returns: str
    limits: str
    fn: object


_SCOPE = 'scope: a quoted "all", a section id "S002", a section name "Chorus" or "Chorus#2", or a line id "L004"'
_LINE = 'line_id: a quoted line id such as "L004"'
_SECTION = 'section_id: a quoted section id "S002", a name "Chorus", or "Chorus#2" when that name is written more than once'
_WORD = "word: one quoted word"

TOOLS = (
    Tool("get_song_settings", "Read BPM, time signature, target duration, genre, language and vocal speed.", "none", "Those settings, or missing when the song does not have them.", "Does not measure a performance. Sung time is not exact.", _get_song_settings),
    Tool("get_song_structure", "Read the section order and which names are written more than once.", "none", "One row per written section.", "Does not invent a repeat that is not written.", _get_song_structure),
    Tool("get_section", "Read one written section.", _SECTION, "Tag, sung line ids, word and syllable totals, confidence.", "Refuses an ambiguous name instead of merging sections.", _get_section),
    Tool("get_line", "Read one lyric line.", _LINE, "Sung text, words, syllables, end sound, Hold.", "A tag or a cue is not a sung line. The line is not rewritten.", _get_line),
    Tool("count_words", "Count sung words in a scope.", _SCOPE, "Word total, sung lines, confidence.", "Section tags, vocal cues and [Hold] are not words. Same tokens as Live Insights.", _count),
    Tool("count_syllables", "Count sung syllables in a scope.", _SCOPE, "Syllable total, sung lines, confidence.", "Uncertain splits stay estimated. Unreadable lines are excluded instead of a fake zero-only total.", _count),
    Tool("get_line_lengths", "Length of each sung line in a scope.", _SCOPE, "Characters, words and syllables per line, plus totals.", "Not a beauty score. Same counts as Live Insights.", _count),
    Tool("get_pronunciation", "Sound and syllable pieces of one word.", _WORD, "Segments, syllable count, end sound, confidence.", "Thai dictionary and spelling rules. English is an estimate. Stress is unknown.", _get_pronunciation),
    Tool("get_rhyme_candidates", "Other sung words in this text that share a Thai end sound.", _WORD, "Exact and near matches already in the lyrics, or none.", "Not a thesaurus. Not a beauty score. No invented rhyme.", _get_rhyme_candidates),
    Tool("analyze_rhyme_sounds", "End sounds and confirmed sound links in a scope.", _SCOPE, "Per-line end sound and status. Not a score.", "Thai spelling rules only. A repeated word is a repeat. You have not heard the vocal.", _analyze_rhyme_sounds),
    Tool("calculate_bar_duration", "Seconds in one bar.", "bpm (number, optional), time_signature (quoted, optional). Omitted values come from the song settings.", "Pulses and seconds for one bar.", "Not the sung length of the song. A missing BPM or time signature is INCOMPLETE and no number is invented. A time signature outside 2/4, 3/4, 4/4 and 6/8, when passed as an argument, is an error.", _calculate_bar_duration),
    Tool("calculate_vocal_density", "Numeric vocal density for a scope.", _SCOPE, "An estimated rate, the assumptions, and a comfort level only when vocal speed was provided.", "Uses Live Insights density. No measured timeline. Not a reason by itself to cut words.", _calculate_vocal_density),
)

BY_NAME = {t.name: t for t in TOOLS}


def catalog_text() -> str:
    """The catalog the model sees. Built from TOOLS, which is also the list execute() will run."""
    scope = (
        'Scope values: "all"; a section id "S002"; a section name "Chorus" or "Verse 1"; "Chorus#2" for the second written Chorus; a line id "L004". '
        "A name that is written more than once is returned occurrence by occurrence. An unstated repeat is never added."
    )
    rows = ["CATALOG (the only functions that run; this list is the registry, not a separate description):", scope]
    for t in TOOLS:
        rows.append(f"- {t.name}")
        rows.append(f"  Purpose: {t.purpose}")
        rows.append(f"  Arguments: {t.arguments}")
        rows.append(f"  Returns: {t.returns}")
        rows.append(f"  Limits: {t.limits}")
    return "\n".join(rows)


def system_block() -> str:
    """Chat-only instructions. They follow the shared conversation rules so headings, tables and <heyPY> are allowed here."""
    return (
        "SONGWRITER TOOLS AND REPORTS\n"
        "The shared lines that forbid headings, tables, backticks and every tag except an action tag do not apply to the two points below. "
        "They apply in this chat only. A structured report task still follows that task's own format and does not put a tool call in a field. "
        "The songwriter prompt's ban on XML has one extra tag in this chat: <heyPY>...</heyPY>. It is not an action tag. It is how a measurement is requested. It is not shown to the user, and a structured report does not contain it.\n"
        "The shared craft notes still apply. Do not repeat them back.\n\n"
        "HOW YOU WRITE\n"
        "Talk like a songwriter sitting with the user. Answer in their language. Match the length to the question.\n"
        "Quick (a hook, a feel, a yes-or-no): answer it, name what works and what does not, and stop. No score. No table.\n"
        "Detailed (rhyme, a hard-to-sing section, a line): a short heading, the actual lyric in quotation marks, the cause and the effect. "
        "A Markdown table only when several lines are being compared.\n"
        "Full critique, only when they asked about the whole song: overview, strengths, what to develop, then hook, story, prosody or singability where you have something to say, "
        "duration and density only when a tool returned them, then a short conclusion and what to do first. Leave a heading out when it has nothing to say.\n"
        "Use ## or ### and **bold** for the point. Do not use a colour mark such as {red}. Do not show a <heyPY> call. "
        "Do not paste a raw tool result unless they asked for the raw numbers. Do not invent a score, a grade, or a precision the tool did not give. "
        "A syllable count or a rhyme count is never the whole verdict. If you only have the lyrics, you have not heard the vocal, the melody or the music.\n"
        "Read-only stays read-only. Analysis, criticism, a count, a rhyme check, a length check, or a hook opinion has no change_lyric and no insert_lyrics. "
        "An edit they asked for still uses one action tag, with no Markdown inside the tag.\n\n"
        "WHEN YOU NEED A MEASUREMENT\n"
        "Python does not choose the question and does not hand you an analysis in advance. You decide. "
        "For a real BPM, duration, word count, syllable count, line length, end sound or density, call a tool and stop. The first reply that needs data is only the calls:\n"
        "<heyPY>get_song_settings()</heyPY>\n"
        "<heyPY>count_syllables(\"chorus\")</heyPY>\n"
        "You may call several functions in that reply, and you may call again after the results. "
        "The program runs only a name in the catalog, in this same session, and sends the result back as text. "
        "A bad call comes back as a short error. The name is not guessed and an unknown call is not run. "
        "Then you answer the user. The call and the raw result are not for them.\n"
        "Do not call a tool for a greeting, an opinion you can give from the lines, or a lyric change you were asked to write.\n\n"
        + catalog_text()
    )


def _unescape(text: str) -> str:
    out, esc = [], False
    for ch in text:
        if esc:
            out.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        else:
            out.append(ch)
    if esc:
        out.append("\\")
    return "".join(out)


def _split_commas(text: str) -> list:
    parts, buf, quote = [], [], ""
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            continue
        if ch == ",":
            parts.append("".join(buf))
            buf = []
            continue
        if ch in "()":
            raise ValueError("nested calls are not allowed")
        buf.append(ch)
    if quote:
        raise ValueError("a quote was not closed")
    if buf or parts:
        parts.append("".join(buf))
    return parts


def _parse_value(token: str):
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return _unescape(token[1:-1])
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    if re.fullmatch(r"-?\d+\.\d+", token):
        return float(token)
    raise ValueError("an argument must be a quoted string or a number")


def _parse_args(text: str) -> tuple:
    if not text.strip():
        return [], {}
    pos, kw, keyed = [], {}, False
    for part in _split_commas(text):
        part = part.strip()
        if not part:
            raise ValueError("an argument is empty")
        m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", part, re.DOTALL)
        if m:
            keyed = True
            kw[m.group(1)] = _parse_value(m.group(2))
            continue
        if keyed:
            raise ValueError("a positional argument follows a keyword")
        pos.append(_parse_value(part))
    return pos, kw


def parse_invocation(inner: str) -> dict:
    raw = (inner or "").strip()
    m = _CALL.fullmatch(raw)
    if not m:
        return {"ok": False, "name": "", "pos": [], "kw": {}, "raw_inner": raw, "error": "The tool call is not function_name(arguments). Nothing was run."}
    try:
        pos, kw = _parse_args(m.group(2))
    except ValueError as ex:
        return {"ok": False, "name": m.group(1), "pos": [], "kw": {}, "raw_inner": raw, "error": f"Arguments could not be read ({ex}). Nothing was guessed."}
    return {"ok": True, "name": m.group(1), "pos": pos, "kw": kw, "raw_inner": raw, "error": ""}


def execute(ctx: SongContext, call: dict) -> str:
    label = (call.get("raw_inner") or call.get("name") or "tool").strip()
    if not call.get("ok"):
        return _block(label or "tool", "ERROR", [f"Error: {call.get('error') or 'The tool call could not be read.'}", "Nothing was run."])
    tool = BY_NAME.get(call.get("name") or "")
    if tool is None:
        return _block(label, "ERROR", [
            "Error: Unknown function. Nothing was run and the name was not guessed.",
            "Allowed: " + ", ".join(t.name for t in TOOLS),
        ])
    kind = {"count_words": "words", "count_syllables": "syllables", "get_line_lengths": "lengths"}.get(tool.name)
    try:
        if kind:
            return tool.fn(ctx, call["pos"], call["kw"], label, kind)
        return tool.fn(ctx, call["pos"], call["kw"], label)
    except ToolFail as ex:
        return _block(label, "ERROR", [f"Error: {ex.message}", "Nothing was guessed."])


class StreamGate:
    """Hide a <heyPY> tag, including one that arrives split across chunks. Other text passes through."""

    def __init__(self):
        self.pending = ""
        self.visible: list = []
        self.calls: list = []

    def push(self, chunk: str) -> str:
        self.pending += chunk or ""
        out = []
        while self.pending:
            low = self.pending.lower()
            at = low.find("<")
            if at < 0:
                out.append(self.pending)
                self.pending = ""
                break
            if at:
                out.append(self.pending[:at])
                self.pending = self.pending[at:]
                continue
            if _OPEN.startswith(low) or _CLOSE.startswith(low):
                break
            if low.startswith(_CLOSE):
                self.pending = self.pending[len(_CLOSE):]
                self.calls.append({"ok": False, "name": "", "pos": [], "kw": {}, "raw_inner": "", "error": "A closing tool tag had no opening tag. Nothing was run."})
                continue
            if low.startswith(_OPEN):
                end = low.find(_CLOSE, len(_OPEN))
                if end < 0:
                    break
                inner = self.pending[len(_OPEN):end]
                self.calls.append(parse_invocation(inner))
                self.pending = self.pending[end + len(_CLOSE):]
                continue
            out.append(self.pending[0])
            self.pending = self.pending[1:]
        shown = "".join(out)
        self.visible.append(shown)
        return shown

    def finish(self) -> str:
        if not self.pending:
            return ""
        low = self.pending.lower()
        if low.startswith(_OPEN) or _OPEN.startswith(low) or _CLOSE.startswith(low):
            self.calls.append({"ok": False, "name": "", "pos": [], "kw": {}, "raw_inner": "", "error": "The tool tag was cut off before it closed. Nothing was run."})
            self.pending = ""
            return ""
        rest = self.pending
        self.pending = ""
        self.visible.append(rest)
        return rest

    def text(self) -> str:
        body = "".join(self.visible)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        return body


def _step(call: dict, result: str) -> str:
    name = call.get("name") or "tool"
    status = "ERROR"
    for line in result.splitlines():
        if line.startswith("Status:"):
            status = line.split(":", 1)[1].strip()
            break
    return f"{name}: {status}"


@dataclass
class LoopResult:
    visible: str
    turns: list
    rounds: int
    steps: list


def run_tool_loop(ask, ctx: SongContext, user: str, history: "list | None" = None) -> LoopResult:
    """ask(user, history) returns the model text. history is the prior turns, not including `user`.

    Tool results stay inside this loop. The visible string has no tool tag. The lyrics object is not edited.
    """
    history = list(history or [])
    turns, steps = [], []
    rounds = 0
    before = ctx.lyrics
    while True:
        try:
            raw = ask(user, history)
        except ToolLoopError:
            raise
        except BaseException as ex:
            raise ToolLoopError(turns, steps, ex) from ex
        if ctx.lyrics != before:
            ctx.lyrics = before
            raise ToolFail("A tool changed the lyrics.")
        gate = StreamGate()
        gate.push(raw or "")
        gate.finish()
        turns.append((user, raw or ""))
        calls = gate.calls
        if not calls:
            return LoopResult(gate.text(), turns, rounds, steps)
        if rounds >= MAX_TOOL_ROUNDS:
            visible = gate.text() or LIMIT_NOTE
            steps.append("tool loop stopped at the limit")
            return LoopResult(visible, turns, rounds, steps)
        run, rest = calls[:MAX_CALLS], calls[MAX_CALLS:]
        blocks = []
        for call in run:
            result = execute(ctx, call)
            if ctx.lyrics != before:
                ctx.lyrics = before
                raise ToolFail("A tool changed the lyrics.")
            blocks.append(result)
            steps.append(_step(call, result))
        if rest:
            blocks.append(_block("(not run)", "ERROR", [f"Error: Only {MAX_CALLS} calls run in one reply. The rest were not run and no name was guessed."]))
            steps.append("extra calls: ERROR")
        history = history + [{"role": "user", "content": user}, {"role": "assistant", "content": raw or ""}]
        user = "\n\n".join(blocks) + "\n\n" + FOLLOW
        rounds += 1


def prepare_chat_markdown(text: str) -> str:
    """Turn the older colour marks into Markdown. The lyric words stay."""
    text = text or ""
    for src, dst in (("\\rightarrow", "→"), ("\\leftarrow", "←"), ("$\\rightarrow$", "→"), ("$\\leftarrow$", "←")):
        text = text.replace(src, dst)

    def repl(m):
        return f"**{m.group(2)}**" if m.group(1) == "b" else m.group(2)

    return re.sub(r"\{(red|green|amber|blue|purple|pink|gray|b)\}(.*?)\{/\}", repl, text, flags=re.DOTALL)
