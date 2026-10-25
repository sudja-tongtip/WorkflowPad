"""Lyrics Analysis, Phase 4: rhythm and singability, judged from the TEXT only (never from audio: no BPM, beats, melody or note lengths are known or claimed).

Two parts, kept apart:
  A. Python, no LLM: the EFFECTIVE syllable count of every sung line (manual count > manual segmentation > AI segmentation, Phase 1-2), grouped by [Section], with how each line
     differs from the middle of its section and how sections of the same kind (Verse 1 / Verse 2, Chorus ...) compare. A different count is information, never an error:
     the melody may give a short line more notes than a long one.
  B. The LLM: phrasing, word flow, breath points, density and singability of the lines, given those counts as FIXED facts. It never writes a count, and its suggested wording is
     stored apart from the lyrics and never applied.
Nothing here touches the editor, the syllable result, the corrections or the rhyme result.
"""
from __future__ import annotations

import difflib
import hashlib
import re
import statistics
from dataclasses import dataclass, field
from typing import Callable

from .compiler import TAG_LINE
from .lyric_analysis import AnalysisError, SEP_END, _is_sep, _norm, sung_lines

RHYTHM_PROMPT_VERSION = "rhythm-v1"
KIND = "rhythm"                                 # the analysis type that is part of the cache key
SEP_RHYTHM = "=========== RHYTHM ==========="
WINDOW_LINES = 60                               # a whole song normally goes in one request; sections are never cut unless one section is longer than this
WINDOW_OVERLAP = 10                             # only used inside a section that is longer than a window
MAX_ITEMS_PER_REQUEST = 24
ISSUES = ("dense_phrase", "sparse_phrase", "uneven_length", "hard_flow", "breath_point", "phrasing", "section_mismatch")
ISSUE_TITLES = {"dense_phrase": "Dense phrase", "sparse_phrase": "Sparse phrase", "uneven_length": "Uneven length", "hard_flow": "Hard to sing smoothly",
                "breath_point": "Possible breath point", "phrasing": "Phrasing", "section_mismatch": "Differs from a similar section"}
SEVERITIES = ("info", "suggestion", "uncertain")
DIFF_MIN, DIFF_RATIO = 3, 0.3                   # a line is "different" from its section when it is >= 3 units AND >= 30% away from the section's middle value


# ── A. Python: structure ─────────────────────────────────────────────────────────────────────────────────────
@dataclass
class RLine:
    num: int                                    # global sung-line number (the same numbers every analysis uses)
    text: str
    section: str                                # e.g. "Verse 1" ("" before the first tag)
    count: int                                  # EFFECTIVE count
    source: str                                 # ai | units | count
    unit: str                                   # syllable | mora | mixed (one line holding mora and syllables)


@dataclass
class SectionStat:
    name: str
    lines: list
    median: float
    flagged: dict                               # line num -> signed difference from the median
    kinds: set                                  # the unit kinds inside this section

    @property
    def total(self) -> int:
        return sum(l.count for l in self.lines)

    @property
    def comparable(self) -> bool:               # mora and syllables are not one unit: no comparison inside a section that mixes them
        return len(self.kinds) <= 1


def section_names(text: str) -> dict:
    """{file line number: section name} for every line of the lyrics (the name of the last whole-line [tag] above it, minus any ' - detail'; [Hold] is not a section)."""
    out, cur = {}, ""
    for i, raw in enumerate((text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        if TAG_LINE.match(raw):
            name = raw.strip()[1:-1].strip()
            if name and not name.lower().startswith("hold"):
                cur = re.split(r"\s+-\s+|\s*:\s*", name, maxsplit=1)[0].strip()
        out[i] = cur
    return out


def unit_kind(segments: list) -> str:
    kinds = {"mora" if sg.lang == "ja" else "syllable" for sg in segments}
    return "mixed" if len(kinds) > 1 else kinds.pop()


def build_lines(effs: list, text: str) -> list:
    """RLine for each LA.Effective (Phase 2), using ONLY the effective counts. The text decides the sections."""
    names = section_names(text)
    return [RLine(e.line.num, e.line.text, names.get(e.line.file_line, ""), e.count, e.source, unit_kind(e.segments)) for e in effs]


def counts_hash(lines: list) -> str:
    """Identity of the counts an analysis was made from. A manual change of any count (8 -> 7) changes it although the lyrics did not change."""
    h = hashlib.sha1("\n".join(f"{l.num}\t{l.count}\t{l.unit}\t{l.section}\t{hashlib.sha1(l.text.encode('utf-8')).hexdigest()[:10]}" for l in lines).encode("utf-8"))
    return h.hexdigest()


def group_sections(lines: list) -> list:
    """SectionStat per run of lines under the same section name (a repeated name later in the song is a separate group)."""
    groups, cur = [], None
    for l in lines:
        if cur is None or l.section != cur[0]:
            cur = (l.section, [])
            groups.append(cur)
        cur[1].append(l)
    out = []
    for name, ls in groups:
        kinds = {l.unit for l in ls}
        flagged, med = {}, 0.0
        if len(ls) >= 3 and len(kinds) == 1:
            med = statistics.median(l.count for l in ls)
            for l in ls:
                d = l.count - med
                if abs(d) >= DIFF_MIN and abs(d) >= DIFF_RATIO * max(med, 1):
                    flagged[l.num] = d
        elif ls:
            med = statistics.median(l.count for l in ls)
        out.append(SectionStat(name or "(no section tag)", ls, med, flagged, kinds))
    return out


def stem(name: str) -> str:
    return re.sub(r"[\s\d]+$", "", name.lower()).strip()


def similar_sections(groups: list) -> list:
    """[(a, b, [(line_in_a, line_in_b, difference)], total_difference)] for sections of the same kind (Verse 1 / Verse 2, two Choruses) with the same number of lines and one unit kind.
    Plain information for the user; no verdict."""
    out = []
    for i, a in enumerate(groups):
        for b in groups[i + 1:]:
            if stem(a.name) and stem(a.name) == stem(b.name) and len(a.lines) == len(b.lines) and a.comparable and b.comparable and a.kinds == b.kinds:
                diffs = [(x.num, y.num, y.count - x.count) for x, y in zip(a.lines, b.lines) if y.count != x.count]
                out.append((a, b, diffs, b.total - a.total))
    return out


# ── B. LLM: singability ──────────────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You review song lyrics for SINGABILITY: how easily the WORDS can be phrased and sung. You judge the TEXT only. You have NOT heard any audio and you know nothing about "
    "the tempo, beat, melody or note lengths: never claim to, and never invent them.\n\n"
    "INPUT: one line per lyric line: `N | section | count unit | text`. The count is the number of sung units (syllables, or mora for Japanese) already worked out and "
    "checked: treat it as a FIXED FACT. Never recount, never write a count, never argue with it. A line with a different count from its neighbours is NOT an error: "
    "the melody may give it more or fewer notes. Do not call it wrong just because it is longer or shorter.\n\n"
    "LOOK FOR (the issue column)\n"
    "- dense_phrase: many units in one breath, crowded for a moderate tempo.\n"
    "- sparse_phrase: very few units compared with the lines around it, may feel empty.\n"
    "- uneven_length: the length clearly breaks the pattern of its section.\n"
    "- hard_flow: a word or word sequence that is awkward to pronounce quickly (consonant clusters, repeated similar sounds, a hard transition between words).\n"
    "- breath_point: a good place inside a long line to pause or breathe (say after which word).\n"
    "- phrasing: the line would be clearer or more natural split or grouped differently.\n"
    "- section_mismatch: this line is much longer or shorter than the matching line of a similar section (for example Verse 1 and Verse 2).\n\n"
    "OUTPUT FORMAT: exactly these two marker lines around the rows, one row per remark, at most " + str(MAX_ITEMS_PER_REQUEST) + " rows, most useful first:\n"
    + SEP_RHYTHM + "\n"
    "N | <the lyric line copied exactly> | issue | severity | reason | suggested wording or -\n"
    + SEP_END + "\n\n"
    "- N is the line number from the input. Copy the lyric line exactly as it is in the input.\n"
    "- severity is one of: info (an observation), suggestion (worth considering), uncertain (you are not sure).\n"
    "- reason: one short sentence about the TEXT. No counts. Do not use the character | inside any field.\n"
    "- The last field is OPTIONAL alternative wording in the SAME language as the line, keeping its meaning and mood; write - when you have none. It is only a proposal for the "
    "songwriter and is never applied. For breath_point write the word after which to pause instead of new wording.\n"
    "- Lines that are fine need no row. If every line is fine write the two marker lines with nothing between them. Do not invent problems to fill the answer.\n\n"
    "EXAMPLE\n"
    "Input:\n1 | Verse 1 | 8 syllables | The river runs beside my door\n2 | Verse 1 | 8 syllables | And sings the songs I knew before\n"
    "3 | Verse 1 | 22 syllables | Every single morning I remember everything you said to me before you went away\n"
    "4 | Verse 1 | 7 syllables | Now the house is quiet here\n"
    "Output:\n" + SEP_RHYTHM + "\n"
    "3 | Every single morning I remember everything you said to me before you went away | dense_phrase | suggestion | "
    "Much more text than the lines around it, so it may feel crowded when sung. | Every morning I remember all you said before you went away\n"
    "3 | Every single morning I remember everything you said to me before you went away | breath_point | info | "
    "A natural pause after the first clause. | morning\n" + SEP_END
)


@dataclass
class Item:
    line: int
    issue: str
    severity: str
    reason: str
    suggestion: str = ""                        # LLM wording: a proposal, never applied and never mixed into the lyrics
    section: str = ""                           # filled in by Python from the lyrics, not asked of the LLM


@dataclass
class RhythmResult:
    lines: list                                 # the text of every sung line when this was analyzed (index 0 = line 1)
    items: list
    counts_hash: str
    counts: list = field(default_factory=list)  # the effective counts that were sent, kept so an outdated result can say what it was made from
    raw: list = field(default_factory=list)


def _clean(s: str) -> str:
    return s.replace("|", "/")


def build_request(lines: list) -> tuple:
    """(system prompt, user message) for one window of RLine."""
    return SYSTEM_PROMPT, "\n".join(f"{l.num} | {l.section or '-'} | {l.count} {'mora' if l.unit == 'mora' else 'syllables'} | {_clean(l.text)}" for l in lines)


def plan_windows(groups: list, size: int = WINDOW_LINES, overlap: int = WINDOW_OVERLAP) -> list:
    """List of lists of RLine. Whole sections are packed together up to `size` lines so a section is never cut (the lines of a section are compared with each other); only a section
    longer than `size` is cut, in overlapping pieces."""
    wins, cur = [], []
    for g in groups:
        ls = g.lines
        if len(ls) > size:
            if cur:
                wins.append(cur); cur = []
            step = max(1, size - overlap)
            s = 0
            while True:
                wins.append(ls[s:s + size])
                if s + size >= len(ls):
                    break
                s += step
        elif len(cur) + len(ls) > size:
            wins.append(cur); cur = list(ls)
        else:
            cur = cur + list(ls)
    if cur:
        wins.append(cur)
    return wins


def _check_row(row: str, by_num: dict) -> tuple:
    """(Item | None, ignore message). AnalysisError for a row that is not in the format at all (nothing is guessed)."""
    parts = [p.strip() for p in row.split("|")]
    if len(parts) != 6:
        raise AnalysisError(f"A rhythm row is not in the format `N | line | issue | severity | reason | suggestion`: “{row[:60]}”.")
    num, echo, issue, sev, reason, sugg = parts
    num = num.rstrip(".")
    if not num.isdigit() or int(num) not in by_num:
        raise AnalysisError(f"A rhythm row points to line “{num[:10]}”, which is not in the lyrics that were sent.")
    issue, sev = issue.lower(), sev.lower()
    if issue not in ISSUES:
        raise AnalysisError(f"A rhythm row has an unknown issue “{issue[:24]}”.")
    if sev not in SEVERITIES:
        raise AnalysisError(f"A rhythm row has an unknown severity “{sev[:20]}” (allowed: info, suggestion, uncertain).")
    ln = by_num[int(num)]
    if difflib.SequenceMatcher(None, _norm(echo), _norm(ln.text)).ratio() < 0.6:
        raise AnalysisError(f"Line {num}: the text the AI copied does not match your lyric (“{echo[:40]}”). The answer is not trusted.")
    if not reason:
        return None, f"Line {num}: a remark without a reason was ignored."
    if sugg in ("-", "—", "none", "None"):
        sugg = ""
    if "[" in sugg or "]" in sugg:
        sugg = re.sub(r"\[[^\[\]]*\]", "", sugg).strip()                          # a suggestion never carries a section tag
    if issue == "breath_point" and sugg and _norm(sugg) not in _norm(ln.text):
        sugg = ""                                                                 # a breath point names a word of the line; anything else is not shown as one
    return Item(int(num), issue, sev, reason, sugg, ln.section), ""


def parse_window(raw: str, sent: list) -> tuple:
    """(items, ignored messages) or AnalysisError. Markers with nothing between them = 'nothing to remark' (a valid result); empty answer / missing markers / a broken row = error."""
    text = re.sub(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$", "", raw or "").strip()
    if not text:
        raise AnalysisError("The AI answer is empty.", raw)
    rows = text.splitlines()
    a = [i for i, r in enumerate(rows) if _is_sep(r, SEP_RHYTHM)]
    z = [i for i, r in enumerate(rows) if _is_sep(r, SEP_END)]
    if len(a) != 1 or len(z) != 1 or not a[0] < z[0]:
        raise AnalysisError("The AI answer does not have the RHYTHM ... END marker lines exactly once, in order.", raw)
    body = [r.strip() for r in rows[a[0] + 1:z[0]] if r.strip()]
    if len(body) == 1 and body[0].strip(" .").lower() in ("none", "-"):
        body = []
    if len(body) > MAX_ITEMS_PER_REQUEST * 2:
        raise AnalysisError(f"The AI returned {len(body)} rows, far more than the {MAX_ITEMS_PER_REQUEST} asked for. The answer is not trusted.", raw)
    by_num = {l.num: l for l in sent}
    items, ignored, seen = [], [], set()
    for row in body:
        try:
            it, why = _check_row(row, by_num)
        except AnalysisError as ex:
            ex.raw = raw
            raise
        if it is None:
            ignored.append(why)
        elif (it.line, it.issue) in seen:
            ignored.append(f"Line {it.line}: “{ISSUE_TITLES[it.issue]}” was listed twice.")
        else:
            seen.add((it.line, it.issue))
            items.append(it)
    return items, ignored


def analyze_rhythm(lines: list, ask: Callable[[str, str], str], size: int = WINDOW_LINES, overlap: int = WINDOW_OVERLAP,
                   progress: Callable[[int, int], None] | None = None) -> RhythmResult:
    """Blocking (call from an executor). Any window that fails stops everything: the result is complete or it does not exist."""
    if not lines:
        raise AnalysisError("There are no sung lines to analyze (only [tags] or empty).")
    wins = plan_windows(group_sections(lines), size, overlap)
    result = RhythmResult([l.text for l in lines], [], counts_hash(lines), [l.count for l in lines])
    seen = set()
    for k, w in enumerate(wins, 1):
        if progress:
            progress(k, len(wins))
        system, user = build_request(w)
        raw = ask(system, user)
        result.raw.append(raw)
        try:
            items, _ = parse_window(raw, w)
        except AnalysisError as ex:
            ex.raw = raw
            raise
        for it in items:
            if (it.line, it.issue) not in seen:          # a line inside an overlap is remarked on by two windows: keep one
                seen.add((it.line, it.issue))
                result.items.append(it)
    result.items.sort(key=lambda i: (i.line, ISSUES.index(i.issue)))
    return result


# ── saving the last good result ──────────────────────────────────────────────────────────────────────────────
def rhythm_to_dict(snap, result: RhythmResult, at: str) -> dict:
    return {"text_hash": snap.text_hash, "counts_hash": result.counts_hash, "backend": snap.backend, "model": snap.model, "params": snap.params,
            "prompt_version": snap.prompt_version, "at": at, "lines": list(result.lines), "counts": list(result.counts),
            "items": [{"line": i.line, "issue": i.issue, "severity": i.severity, "reason": i.reason, "suggestion": i.suggestion, "section": i.section} for i in result.items]}


def rhythm_from_dict(d) -> "tuple | None":
    """(info dict, RhythmResult) or None. Only a well formed result is accepted."""
    try:
        lines = [str(x) for x in d["lines"]]
        if not lines:
            return None
        items, seen = [], set()
        for x in d["items"]:
            it = Item(int(x["line"]), str(x["issue"]), str(x["severity"]), str(x["reason"]), str(x.get("suggestion", "")), str(x.get("section", "")))
            if it.issue not in ISSUES or it.severity not in SEVERITIES or not it.reason or not (1 <= it.line <= len(lines)) or (it.line, it.issue) in seen:
                return None
            seen.add((it.line, it.issue))
            items.append(it)
        counts = [int(c) for c in d.get("counts", [])]
        info = {k: str(d[k]) for k in ("text_hash", "counts_hash", "backend", "model", "params", "prompt_version")}
        info["at"] = str(d.get("at", ""))
        return info, RhythmResult(lines, items, info["counts_hash"], counts)
    except Exception:
        return None
