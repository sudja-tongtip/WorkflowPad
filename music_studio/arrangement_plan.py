"""Bar plans and cue checks for AI Music Director.

The model may name sections and propose a whole number of bars. This module is the only place that turns those bars into a length.
It reuses the beat unit in live_insights: 4/4 and 3/4 count quarter notes, and 6/8 counts the dotted quarter (two pulses a bar, not six).
A lyric line is never rewritten here. A cue edit is a new text the caller may write after the user confirms.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from . import cue_library as CL
from . import live_insights as LI
from .compiler import TAG_LINE
from .template_arrange import is_structural

MAX_BARS = 64
_BAR_WORD = re.compile(r"\s*bars?$", re.IGNORECASE)
_HEAD_SPLIT = re.compile(r"\s+(?:\u2014|\u2013|-|:)\s+")
_HEAD_DASH = re.compile(r"\s*[\u2014\u2013]\s*")
_BITS = re.compile(r"\s*,\s*")
_SIG = {"2": "2/4", "2/4": "2", "3": "3/4", "3/4": "3", "4": "4/4", "4/4": "4", "6": "6/8", "6/8": "6"}
_SIG_LABEL = {"2": "2/4", "3": "3/4", "4": "4/4", "6": "6/8"}


def sig_code(value) -> str:
    """'4/4', '4' or '6/8' -> the program's code ('4', '6'). '' when it is not a time signature this program stores."""
    key = str(value or "").strip().lower().replace(" ", "")
    code = _SIG.get(key, "")
    if code in _SIG_LABEL:
        return code
    if key in _SIG_LABEL:
        return key
    return ""


def sig_label(code: str) -> str:
    return _SIG_LABEL.get(sig_code(code) or str(code), "")


def format_clock(seconds: float) -> str:
    """2:45, 0:07.5, or -0:15. A whole second stays without a decimal. A half-second keeps one decimal."""
    if seconds < 0:
        return "-" + format_clock(-seconds)
    whole = abs(seconds - round(seconds)) < 1e-6
    half = abs(seconds * 2 - round(seconds * 2)) < 1e-6
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    if whole:
        return f"{minutes}:{int(round(rest)):02d}"
    if half:
        return f"{minutes}:{rest:04.1f}"
    return f"{minutes}:{rest:04.1f}"


def whole_seconds(seconds: float) -> int:
    """Nearest second, half up. The duration field stores a whole second."""
    if seconds <= 0:
        return 0
    return int(seconds + 0.5)


def bar_seconds(bars: int, bpm: float, timesig: str) -> float:
    """Seconds for this many bars. Same formula as live_insights.seconds_per_bar (6/8 is two pulses, not six). Bars are multiplied before the division so 48 bars at 100 BPM is 115.2."""
    code = sig_code(timesig)
    if bars <= 0 or bpm <= 0 or not code:
        return 0.0
    pulses = LI.pulses_per_bar(code)[0]
    return (bars * pulses * 60.0) / float(bpm)


def duration_value(seconds: float) -> tuple:
    """(seconds to store, '') or (None, why). Nothing is clamped into range."""
    from . import music_options as MO
    n = whole_seconds(seconds)
    lo, hi = MO.DURATION_RANGE
    if n < lo or n > hi:
        return None, f"outside {lo}-{hi} seconds"
    return n, ""


def _norm(name: str) -> str:
    s = (name or "").strip()
    if s.startswith("[") and s.endswith("]") and len(s) >= 2:
        s = s[1:-1].strip()
    s = _HEAD_SPLIT.split(s, maxsplit=1)[0]
    return re.sub(r"\s+", " ", s).strip().casefold()


def split_header(header: str) -> tuple:
    """(section name as written, cue text). 'Pre-Chorus' stays one name. The lyric text is not involved."""
    inner = (header or "").strip()
    if inner.startswith("[") and inner.endswith("]") and len(inner) >= 2:
        inner = inner[1:-1].strip()
    m = _HEAD_SPLIT.search(inner) or _HEAD_DASH.search(inner)
    if not m:
        return inner.strip(), ""
    return inner[:m.start()].rstrip(), inner[m.end():].strip()


def cue_bits(text: str) -> list:
    return [p.strip() for p in _BITS.split(text or "") if p.strip()]


@dataclass
class SongSection:
    index: int
    line: int
    name: str
    header: str
    sung: int
    instrumental: bool
    cue: str


def _is_section_line(st: str) -> bool:
    """A real section tag. A vocal cue on its own line is not one. A proposed instrumental tag counts even when the compiler does not know that name."""
    if not st or not TAG_LINE.match(st):
        return False
    if is_structural(st):
        return True
    name, _cue = split_header(st)
    return is_proposed_instrumental(name)


def song_sections(lyrics: str) -> list:
    """Section tags in order, with the sung lines under each one. A vocal tag on its own line is not a section."""
    lines = (lyrics or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    found = []
    for n, raw in enumerate(lines, 1):
        st = raw.strip()
        if _is_section_line(st):
            name, cue = split_header(st)
            found.append((n, name or st, st, cue))
    out = []
    for i, (line, name, header, cue) in enumerate(found):
        nxt = found[i + 1][0] if i + 1 < len(found) else len(lines) + 1
        sung = 0
        for raw in lines[line:nxt - 1]:
            st = raw.strip()
            if st and not TAG_LINE.match(st):
                sung += 1
        instrumental = sung == 0 or LI.is_instrumental(name)
        out.append(SongSection(i, line, name, header, sung, instrumental, cue))
    return out


def section_facts(lyrics: str) -> list:
    """One line per real section, for the model to copy. Python counted the sung lines. No seconds."""
    rows = []
    for sec in song_sections(lyrics):
        kind = "instrumental" if sec.instrumental and sec.sung == 0 else f"{sec.sung} sung line" + ("" if sec.sung == 1 else "s")
        cue = sec.cue or "no cue"
        rows.append(f"- {sec.name} | {kind} | cue: {cue}")
    return rows


def parse_bars(text: str) -> tuple:
    """(bars, '') or (None, why). A decimal, a word, zero, or a count above 64 is not a bar plan."""
    s = _BAR_WORD.sub("", (text or "").strip()).strip()
    if not re.fullmatch(r"\d{1,3}", s):
        return None, "not a whole number of bars"
    n = int(s)
    if n < 1 or n > MAX_BARS:
        return None, f"outside 1-{MAX_BARS} bars"
    return n, ""


_TIME_COL = re.compile(r"\d+\s*:\s*\d+|\d+\.\d+|\b(?:sec|secs|second|seconds|min|mins|minute|minutes)\b", re.IGNORECASE)
_NOTE_KEYS = ("role", "energy", "texture", "vocal", "next", "reason")


def _row_notes(parts: list) -> dict:
    """Role, energy, texture, vocal, next section, and reason. A clock in the first extra column is not a note."""
    notes = {}
    loose = []
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            key = key.strip().lower().replace("transition", "next")
            if key in _NOTE_KEYS and value.strip():
                notes[key] = value.strip()[:160]
                continue
        loose.append(part)
    for key, part in zip(_NOTE_KEYS, loose):
        if key not in notes and part:
            notes[key] = part[:160]
    return notes


def parse_bar_row(line: str):
    """(name, bars or None, ignored a model time, why, notes) or None for a blank line."""
    if not (line or "").strip():
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 2 or not parts[0]:
        return "", None, False, "a bar row needs the section name and the number of bars", {}
    bars, why = parse_bars(parts[1])
    rest = [p for p in parts[2:] if p]
    timed = bool(rest) and bool(_TIME_COL.search(rest[0]))
    return parts[0], bars, timed, why, {} if timed else _row_notes(rest)


def unpack_bar_line(line: str) -> list:
    """One section per row. A model that packs several name|bars groups onto one line is split there. A next-section column followed by words stays one row."""
    text = (line or "").strip()
    if not text or text.count("|") < 3:
        return [text] if text else []
    parts = [p.strip() for p in text.split("|")]
    starts = [i for i in range(len(parts) - 1) if parts[i] and parse_bars(parts[i + 1])[0] is not None and parse_bars(parts[i])[0] is None]
    if len(starts) <= 1:
        return [text]
    rows = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(parts)
        piece = "|".join(p for p in parts[start:end] if p)
        if piece:
            rows.append(piece)
    return rows or [text]


_PLACEHOLDER_CUES = {
    "none", "null", "nil", "n/a", "na", "-", "--", "—", "–",
    "no cue", "no suggestion", "nothing", "placeholder", "skip",
}
_PROPOSED_WORD = re.compile(
    r"\b(intro|outro|instrumental|interlude|solo|break|transition|breakdown|build-?up|buildup|fill|post-?chorus|drop)\b",
    re.IGNORECASE,
)
_HARD_DURATION = re.compile(
    r"(hard limit|hard duration|must be exactly|exactly\s+\d+\s*(?:s|sec|seconds)\b|ความยาวตายตัว|ต้องยาวพอดี|โฆษณา)",
    re.IGNORECASE,
)


def is_placeholder_cue(phrase: str) -> bool:
    """A blank or a stand-in word is not a cue. It is not freeform and it is not offered."""
    s = (phrase or "").strip().strip("[]").strip().casefold()
    return not s or s in _PLACEHOLDER_CUES


def is_proposed_instrumental(name: str) -> bool:
    """A section name the lyric does not have, when it names a musical gap rather than a new sung section."""
    if LI.is_instrumental(name):
        return True
    return bool(_PROPOSED_WORD.search(name or ""))


def hard_duration_request(text: str) -> bool:
    """True only when the user's own request keeps the length fixed. A preferred duration is not a hard limit."""
    return bool(_HARD_DURATION.search(text or ""))


def parse_cue_row(line: str):
    """(section, cue, why, action) or None. action is '', 'keep', 'add', 'replace', or 'skip' for a placeholder."""
    if not (line or "").strip():
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 2 or not parts[0]:
        return None
    action = parts[3].strip().lower() if len(parts) > 3 else ""
    if action not in ("keep", "add", "replace"):
        action = ""
    cue = parts[1].strip().strip("[]").strip()
    if is_placeholder_cue(cue):
        return parts[0], cue, "", "skip"
    return parts[0], cue, parts[2] if len(parts) > 2 else "", action


@dataclass
class BarRow:
    name: str
    line: int
    bars: int
    seconds: float
    clock: str
    sung: int
    instrumental: bool
    header: str
    proposed: bool = False
    role: str = ""
    energy: str = ""
    texture: str = ""
    vocal: str = ""
    onward: str = ""
    reason: str = ""
    insert_id: str = ""
    after_line: int = 0


@dataclass
class CueChange:
    id: str
    section: str
    line: int
    original: str
    proposed: str
    reason: str
    status: str
    action: str
    phrase: str
    selectable: bool
    checked: bool
    note: str


@dataclass
class Clock:
    bars: int
    bpm: float
    timesig: str
    label: str
    seconds: float
    clock: str
    target: float
    difference: float
    diff_clock: str


@dataclass
class Plan:
    rows: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    cues: list = field(default_factory=list)
    clock: Clock | None = None
    feasible_low: float = 0.0
    feasible_high: float = 0.0
    feasible_known: bool = False
    feasible_why: str = ""
    fit: str = "unknown"
    advice: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    duration_ok: bool = False
    duration_block: str = ""
    ignored_time: bool = False
    complete: bool = False
    bpm: float = 0.0
    timesig: str = ""
    sig_label: str = ""
    proposed: list = field(default_factory=list)
    hard_limit: bool = False


def _clock(bars: int, bpm: float, timesig: str, target: float) -> Clock | None:
    code = sig_code(timesig)
    if bars <= 0 or bpm <= 0 or not code:
        return None
    seconds = bar_seconds(bars, bpm, code)
    diff = seconds - target if target and target > 0 else 0.0
    return Clock(bars, float(bpm), code, sig_label(code), seconds, format_clock(seconds), float(target or 0), diff, format_clock(diff) if target and target > 0 else "")


def _status(phrase: str) -> tuple:
    cue = CL.find_phrase(phrase)
    if cue is not None:
        if cue.source == "studio":
            return "studio", cue.phrase, "Studio guidance, not an official guaranteed cue."
        return "catalog", cue.phrase, "Catalog cue."
    if CL.is_short_custom(phrase):
        return "freeform", phrase.strip(), "Custom cue, not in the catalog."
    return "rejected", phrase.strip(), "Not a catalog cue, so it is not offered as a confirmed cue."


def _conflicts(bits: list) -> bool:
    low = high = False
    for bit in bits:
        cue = CL.find_phrase(bit)
        label = (cue.phrase if cue else bit).strip().lower()
        if label in LI.CUE_LOW or bit.strip().lower() in LI.CUE_LOW:
            low = True
        if label in LI.CUE_HIGH or bit.strip().lower() in LI.CUE_HIGH:
            high = True
    return low and high


def compose_header(header: str, ops: list) -> str:
    """The section line after the chosen cue actions. The section name stays. A keep action changes nothing."""
    name, cue = split_header(header)
    parts = cue_bits(cue)
    for action, phrase in ops:
        if action == "keep" or not phrase:
            continue
        if action == "replace":
            parts = []
        for bit in cue_bits(phrase) or [phrase]:                  # a phrase of several words-with-commas is added bit by bit, never twice
            if bit.casefold() not in {p.casefold() for p in parts}:
                parts.append(bit)
    if not name:
        return header
    if not parts:
        return f"[{name}]"
    return f"[{name} - {', '.join(parts)}]"


def _advice(clock: Clock | None, fit: str, low: float, high: float, why: str, missing: list, ignored: bool, tight: list, proposed: list | None = None, hard: bool = False) -> list:
    out = []
    if ignored:
        out.append("A model time was ignored. The length is calculated from the bars.")
    if missing:
        names = ", ".join(missing)
        out.append(f"This plan skips {names}, so it is not a full-song length.")
    if clock is None:
        return out
    if clock.target > 0:
        shorter = clock.difference < -0.05
        longer = clock.difference > 0.05
        if shorter or longer:
            way = "shorter" if shorter else "longer"
            out.append(f"The plan is {clock.diff_clock} {way} than the target {format_clock(clock.target)}.")
            if hard:
                out.append("The request treats this length as a hard limit. The bar counts were not rewritten.")
            else:
                out.append("The preferred length did not change the bar counts.")
    if low > 0 and high > 0 and fit != "unknown":
        span = f"{format_clock(low)} to {format_clock(high)}"
        if fit == "long":
            where = (", ".join(tight)) if tight else "the sections that carry the words"
            out.append(f"The lyric estimate runs about {span}. This plan is shorter, so the words may be tight. Add bars on {where} or ease the tempo. The words are not cut.")
        elif fit == "short":
            out.append(f"The lyric estimate runs about {span}. This plan is longer. A shorter intro, outro, or instrumental can bring it in. The words are not cut.")
        else:
            out.append(f"The lyric estimate runs about {span}. This plan sits inside that range.")
    else:
        extra = f" {why}" if why else ""
        out.append("There is not enough counted lyric data for a feasible range. Only the planned length is shown." + extra)
    if proposed:
        out.append("Proposed instrumental sections are not in the lyric: " + ", ".join(proposed) + ". The planned length includes them. They were not added to the lyric.")
    out.append("ACE-Step reads the duration as a target. A bar plan does not guarantee that every line is sung.")
    return out


def build_plan(lyrics: str, bar_lines: list, cue_lines: list, bpm: float, timesig: str, target: float = 0, language: str = "", hard_limit: bool = False) -> Plan:
    """Validate one direction's rows and calculate the length. Existing sections must all appear. An instrumental name that is not in the lyric is a proposal, not a repair, and its bars are not invented."""
    sections = song_sections(lyrics)
    plan = Plan(bpm=float(bpm or 0), timesig=sig_code(timesig), sig_label=sig_label(timesig), hard_limit=bool(hard_limit))
    assigned: dict = {}
    ordered = []
    for line in bar_lines or []:
        pieces = unpack_bar_line(line)
        if not pieces and (line or "").strip():
            pieces = [line]
        for piece in pieces:
            got = parse_bar_row(piece)
            if got is None:
                continue
            name, bars, extra, why, notes = got
            if extra:
                plan.ignored_time = True
            if not name:
                plan.rejected.append(why or "A bar row could not be read.")
                continue
            hit = next((s for s in sections if s.index not in assigned and _norm(s.name) == _norm(name)), None)
            if hit is None:
                if bars is None:
                    plan.rejected.append(f"{name}: {why}.")
                elif any(_norm(s.name) == _norm(name) for s in sections):
                    plan.rejected.append(f"{name}: that section is already in the lyric, so it is not added again.")
                elif is_proposed_instrumental(name):
                    ordered.append(("proposed", name, bars, notes))
                else:
                    plan.rejected.append(f"{name}: the model named a section that is not in the lyric.")
                continue
            if bars is None:
                plan.rejected.append(f"{name}: {why}.")
                continue
            assigned[hit.index] = hit
            ordered.append(("existing", hit, bars, notes))
    plan.missing = [s.name for s in sections if s.index not in assigned]
    if not sections:
        plan.rejected.append("The lyric has no section tags, so there is no bar plan.")
    code = plan.timesig
    last_line = 0
    insert_n = 0
    for kind, item, bars, notes in ordered:
        seconds = bar_seconds(bars, plan.bpm, code) if code and plan.bpm > 0 else 0.0
        clock = format_clock(seconds) if seconds else ""
        note_args = (notes.get("role", ""), notes.get("energy", ""), notes.get("texture", ""), notes.get("vocal", ""), notes.get("next", ""), notes.get("reason", ""))
        if kind == "existing":
            sec = item
            last_line = sec.line
            plan.rows.append(BarRow(sec.name, sec.line, bars, seconds, clock, sec.sung, sec.instrumental, sec.header, False, *note_args))
        elif any(_norm(p) == _norm(item) for p in plan.proposed):
            plan.rejected.append(f"{item}: that proposed section is already in this plan.")
        else:
            insert_n += 1
            plan.proposed.append(item)
            plan.rows.append(BarRow(item, 0, bars, seconds, clock, 0, True, "", True, *note_args, f"s{insert_n}", last_line))
    total = sum(r.bars for r in plan.rows)
    plan.clock = _clock(total, plan.bpm, code, target) if plan.rows else None
    plan.complete = bool(sections) and not plan.missing and plan.clock is not None
    if plan.clock is None:
        plan.duration_block = "This direction has no complete bar plan to time."
    elif not plan.complete:
        plan.duration_block = "The bar plan does not cover every section, so its length is not offered as the song length."
    else:
        stored, why = duration_value(plan.clock.seconds)
        plan.duration_ok = stored is not None
        plan.duration_block = "" if stored is not None else f"The planned length is {why}."
    _feasibility(plan, lyrics, language)
    plan.cues = _cues(sections, cue_lines or [], plan.proposed)
    return plan


def _feasibility(plan: Plan, lyrics: str, language: str) -> None:
    low = high = 0.0
    fit = "unknown"
    why = ""
    tight: list = []
    if plan.clock is not None:
        try:
            ins = LI.analyze(lyrics, language=language or "")
            dn = LI.density(ins, LI.DensitySettings(bpm=plan.bpm, timesig=plan.timesig, duration=plan.clock.seconds))
            low, high, fit = dn.need_low, dn.need_high, dn.fit if dn.need_low and dn.need_high else "unknown"
            why = dn.why_unknown or ""
            tight = [s.name for s in dn.sections if s.density_lines]
            if not (low and high):
                fit = "unknown"
                if not why:
                    why = "The syllable count is not enough to estimate a range."
        except Exception:
            fit = "unknown"
            why = "The lyric count could not be read."
    else:
        why = plan.duration_block
    plan.feasible_low, plan.feasible_high = low, high
    plan.feasible_known = bool(low and high and fit != "unknown")
    plan.feasible_why = "" if plan.feasible_known else why
    plan.fit = fit if plan.feasible_known else "unknown"
    plan.advice = _advice(plan.clock, plan.fit, low, high, plan.feasible_why, plan.missing, plan.ignored_time, tight, plan.proposed, plan.hard_limit)
    risks = []
    if plan.clock is not None:
        risks.append(f"Arrangement: {plan.clock.bars} bars, {plan.clock.bpm:g} BPM, {plan.clock.label}, planned length {plan.clock.clock}.")
    if plan.feasible_known:
        risks.append(f"Lyrics density: the estimate runs about {format_clock(low)} to {format_clock(high)} ({plan.fit}).")
    else:
        risks.append("Lyrics density: no range. " + (plan.feasible_why or "Only the planned length is known."))
    risks.append("Generation: ACE-Step reads the duration as a target. A bar plan does not guarantee that every line is sung.")
    plan.risks = risks


def _cues(sections: list, lines: list, proposed: list | None = None) -> list:
    out = []
    last_key = None
    last_sec = None
    seen: dict = {}
    offered: dict = {}
    checked_bits: dict = {}
    proposed_left = list(proposed or [])
    n = 0
    for raw in lines:
        got = parse_cue_row(raw)
        if got is None:
            if (raw or "").strip():
                n += 1
                out.append(CueChange(f"c{n}", "", 0, "", "", "", "rejected", "", "", False, False, "A cue row needs the section and the cue."))
            continue
        name, phrase, why, action = got
        if action == "skip" or is_placeholder_cue(phrase):
            continue
        key = _norm(name)
        if last_sec is not None and last_key == key:
            sec = last_sec
        else:
            matches = [s for s in sections if _norm(s.name) == key]
            k = seen.get(key, 0)
            if k >= len(matches):
                prop = next((p for p in proposed_left if _norm(p) == key), None)
                if prop is not None:
                    proposed_left.remove(prop)
                    status, canon, note = _status(phrase)
                    if status == "rejected":
                        action = action or "add"
                    else:
                        action = action or "add"
                    note = "This section is only a proposal. It is not in the lyric, so the cue is not written."
                    n += 1
                    out.append(CueChange(f"c{n}", prop, 0, "", f"[{prop} - {canon}]", why, status, action, canon, False, False, note))
                    continue
                n += 1
                out.append(CueChange(f"c{n}", name, 0, "", "", why, "rejected", action or "add", phrase, False, False, f"{name}: that section is not in the lyric."))
                continue
            sec = matches[k]
            seen[key] = k + 1
            last_key, last_sec = key, sec
        status, canon, note = _status(phrase)
        existing = cue_bits(sec.cue)
        have = {p.casefold() for p in existing}
        offered.setdefault(sec.index, set(have))
        cbits = [b.casefold() for b in cue_bits(canon)] or [canon.casefold()]
        on_header = all(b in have for b in cbits)
        already = on_header or all(b in offered[sec.index] for b in cbits)
        if not action:
            action = "keep" if already else "add"
        if already and status != "rejected":
            action = "keep"
            status = "duplicate"
            note = "This cue is already on the section." if on_header else "This cue is already suggested for the section."
        if status == "rejected":
            action = action if action in ("add", "replace") else "add"
        proposed = compose_header(sec.header, [] if action == "keep" else [(action, canon)])
        bits = cue_bits(split_header(proposed)[1])
        # Whatever the Director suggested is offered and ticked: only a cue that changes nothing is left out.
        selectable = action in ("add", "replace") and proposed != sec.header
        checked = selectable
        if selectable or status == "duplicate":
            offered[sec.index].update(cbits)
        n += 1
        out.append(CueChange(f"c{n}", sec.name, sec.line, sec.header, proposed, why, status, action, canon, selectable, checked, note))
    return out


def reclock(plan: Plan, bpm: float, timesig: str, target: float = 0) -> Plan | None:
    """The same bars at another tempo or meter. The stored plan is not changed. None when the tempo or the meter cannot be timed."""
    code = sig_code(timesig)
    bpm_f = float(bpm or 0)
    if plan is None or not plan.rows or bpm_f <= 0 or not code:
        return None
    rows = []
    for row in plan.rows:
        seconds = bar_seconds(row.bars, bpm_f, code)
        rows.append(BarRow(row.name, row.line, row.bars, seconds, format_clock(seconds), row.sung, row.instrumental, row.header, row.proposed,
                           row.role, row.energy, row.texture, row.vocal, row.onward, row.reason, row.insert_id, row.after_line))
    total = sum(r.bars for r in rows)
    clock = _clock(total, bpm_f, code, target)
    if clock is None:
        return None
    out = Plan(rows=rows, rejected=list(plan.rejected), missing=list(plan.missing), cues=list(plan.cues), clock=clock,
               feasible_low=plan.feasible_low, feasible_high=plan.feasible_high, feasible_known=plan.feasible_known,
               feasible_why=plan.feasible_why, fit=plan.fit, ignored_time=plan.ignored_time, complete=plan.complete,
               bpm=bpm_f, timesig=code, sig_label=sig_label(code), proposed=list(plan.proposed), hard_limit=plan.hard_limit)
    stored, why = duration_value(clock.seconds)
    out.duration_ok = bool(out.complete and stored is not None)
    out.duration_block = "" if out.duration_ok else (plan.duration_block or (f"The planned length is {why}." if why else "This length cannot be stored."))
    changed = abs(bpm_f - plan.bpm) > 0.01 or code != plan.timesig
    note = f"The song tempo is now {bpm_f:g} BPM, {out.sig_label}. Confirm uses this recalculated length, not the tempo the direction was written at." if changed else ""
    out.advice = ([note] if note else []) + [a for a in plan.advice if not a.startswith("The plan is ") and not a.startswith("ACE-Step")]
    if clock.target > 0 and abs(clock.difference) > 0.05:
        way = "shorter" if clock.difference < 0 else "longer"
        out.advice.insert(0, f"The plan is {clock.diff_clock} {way} than the target {format_clock(clock.target)}.")
    out.advice.append("ACE-Step reads the duration as a target. A bar plan does not guarantee that every line is sung.")
    out.risks = [
        f"Arrangement: {clock.bars} bars, {clock.bpm:g} BPM, {clock.label}, planned length {clock.clock}.",
        plan.risks[1] if len(plan.risks) > 1 else "Lyrics density: unchanged by the tempo recalculation.",
        "Generation: ACE-Step reads the duration as a target. A bar plan does not guarantee that every line is sung.",
    ]
    return out


@dataclass
class CueApply:
    text: str
    applied: list
    skipped: list


def apply_cues(lyrics: str, cues: list, selected: set) -> CueApply:
    """Write only the section lines the user selected. Every other character, including the line breaks, stays. An empty selection writes nothing."""
    chosen = [c for c in cues or [] if c.id in (selected or set()) and c.selectable and c.line and c.action in ("add", "replace")]
    if not chosen:
        return CueApply(lyrics or "", [], [])
    lines = (lyrics or "").splitlines(keepends=True)
    by_line: dict = {}
    for cue in chosen:
        by_line.setdefault(cue.line, []).append(cue)
    applied, skipped = [], []
    for line_no, group in by_line.items():
        idx = line_no - 1
        if idx < 0 or idx >= len(lines):
            skipped.extend(c.id for c in group)
            continue
        raw = lines[idx]
        nl = raw[len(raw.rstrip("\r\n")):]
        body = raw[:len(raw) - len(nl)] if nl else raw
        if body.strip() != group[0].original.strip():
            skipped.extend(c.id for c in group)
            continue
        name, cue = split_header(body.strip())
        parts = cue_bits(cue)
        used = []
        for cue_row in group:
            if cue_row.action == "replace":
                parts = [cue_row.phrase]
                used = [cue_row.id]
                continue
            have = {p.casefold() for p in parts}
            fresh = [b for b in (cue_bits(cue_row.phrase) or [cue_row.phrase]) if b.casefold() not in have]
            if not fresh:
                skipped.append(cue_row.id)
                continue
            parts.extend(fresh)
            used.append(cue_row.id)
        if not name:
            skipped.extend(c.id for c in group)
            continue
        new = f"[{name}]" if not parts else f"[{name} - {', '.join(parts)}]"
        if not used or new == body.strip():
            skipped.extend(c.id for c in group if c.id not in skipped)
            continue
        lines[idx] = new + nl
        applied.extend(used)
    return CueApply("".join(lines), applied, skipped)


def structure_bars(plan: Plan) -> int:
    """Bars of the sections that are already in the lyric. Proposed rows are not included."""
    if plan is None:
        return 0
    return sum(r.bars for r in plan.rows if not r.proposed)


def pending_names(plan: Plan, lyrics: str) -> list:
    """Proposed names that are still absent. A name already in the lyric is not pending."""
    if plan is None:
        return []
    have = {_norm(s.name) for s in song_sections(lyrics or "")}
    return [name for name in plan.proposed if _norm(name) not in have]


def arrangement_state(plan: Plan, lyrics: str) -> str:
    """none when the plan adds no section, pending until those names are in the lyric, then applied."""
    if plan is None or not plan.proposed:
        return "none"
    return "pending" if pending_names(plan, lyrics) else "applied"


def _section_tag(row: BarRow, cues: list) -> str:
    """The new tag. A catalog or studio cue may sit on it. A freeform or placeholder cue never does."""
    phrase = ""
    for cue in cues or []:
        if _norm(cue.section) != _norm(row.name):
            continue
        if cue.status in ("catalog", "studio") and cue.phrase and not is_placeholder_cue(cue.phrase):
            phrase = cue.phrase
            break
    if phrase:
        return f"[{row.name} - {phrase}]"
    return f"[{row.name}]"


@dataclass
class SectionApply:
    ok: bool
    text: str
    applied: list
    skipped: list
    pending: list
    note: str


def apply_sections(lyrics: str, plan: Plan, selected: set, lyrics_hash: str = "") -> SectionApply:
    """Insert only the proposed instrumental tags the user selected. Existing lines, tags, and cues stay. An empty selection writes nothing."""
    original = lyrics or ""
    pending = pending_names(plan, original)
    if plan is None:
        return SectionApply(False, original, [], [], [], "There is no arrangement plan.")
    if lyrics_hash and hashlib.sha1(original.encode("utf-8")).hexdigest() != lyrics_hash:
        return SectionApply(False, original, [], [], pending, "The lyrics changed. Nothing was inserted.")
    chosen = [r for r in plan.rows if r.proposed and r.insert_id and r.insert_id in (selected or set())]
    if not chosen:
        return SectionApply(True, original, [], [], pending, "Nothing was selected.")
    lines = original.splitlines(keepends=True)
    for row in plan.rows:
        if row.proposed or row.line <= 0:
            continue
        idx = row.line - 1
        if idx >= len(lines) or lines[idx].strip() != (row.header or "").strip():
            return SectionApply(False, original, [], [], pending, "The lyrics changed. Nothing was inserted.")
    have = {_norm(s.name) for s in song_sections(original)}
    nl = "\r\n" if "\r\n" in original else "\n"
    groups: dict = {}
    applied, skipped = [], []
    for row in chosen:
        if not is_proposed_instrumental(row.name) or _norm(row.name) in have:
            skipped.append(row.insert_id)
            continue
        groups.setdefault(row.after_line, []).append(_section_tag(row, plan.cues) + nl)
        have.add(_norm(row.name))
        applied.append(row.insert_id)
    if not applied:
        return SectionApply(True, original, [], skipped, pending_names(plan, original), "Nothing was inserted.")

    def at(after_line: int) -> int:
        if after_line <= 0:
            return 0
        for i in range(after_line, len(lines)):
            if _is_section_line(lines[i].strip()):
                return i
        return len(lines)

    for after_line, blocks in sorted(groups.items(), key=lambda item: at(item[0]), reverse=True):
        idx = at(after_line)
        if idx > 0 and not lines[idx - 1].endswith(("\n", "\r")):
            lines[idx - 1] = lines[idx - 1] + nl
        lines.insert(idx, "".join(blocks))
    return SectionApply(True, "".join(lines), applied, skipped, pending_names(plan, "".join(lines)), "")


def mix_note(style_from, duration_from, cues_from) -> str:
    """A warning when the user combined pieces of different directions. It does not change any value."""
    ids = [x for x in (style_from, duration_from, cues_from) if x]
    if len(set(ids)) > 1:
        return "These choices come from different directions. Nothing else was changed to make them match."
    return ""


def prompt_stale_note(saved: str, current: str) -> str:
    if saved and current and saved != current:
        return "This analysis used an older prompt. Press Analyze for a new reading. The length is still calculated from the bars."
    return ""
