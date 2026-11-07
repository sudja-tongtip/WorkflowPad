"""Vocal cue risk: cues that apply to the same part of a song at the same time and may confuse ACE-Step 1.5.

What is checked (all from the lyrics text and, when given, the caption; no LLM, nothing is rewritten):
  * Section cues       the cues of a section tag, as in [Chorus - powerful belting, whispered]
  * Line vocal cues    a standalone cue line right before a lyric, as in [whispered]; it applies to the next sung line together with the section's cues (the EFFECTIVE set of that line)
  * Caption            the vocal description of the music caption against the cues of the lyrics
  * Backing / harmony  harmonies, choir, call and response, ad-lib are a different job and are never "in conflict" with a lead delivery just for being there

Kinds of finding:
  conflicting       two cues that ask for opposite things at once (whispered + powerful belting; soft vocals + explosive; spoken word + falsetto)
  redundant         two cues that say the same thing (soft vocals + breathy vocals; powerful + anthemic; whisper + whispered)
  excessive         three or more different vocal-control cues in one place, or two different main deliveries without an obvious reason
  caption_mismatch  the caption describes the vocal in the opposite way to the cues of the lyrics

How sure the program is: only cues the studio catalogue KNOWS are judged (cue_library). A custom cue the user wrote is never called wrong; it is left out and reported in `unknown`.
Studio guideline, not an ACE-Step rule: one main vocal delivery per sung section is the usual choice; several are fine when each does a different job. ACE-Step may follow a cue loosely.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import compiler as K
from . import cue_library as CL
from . import tag_parser as TP

# The job of each catalogue cue. Everything not listed (instruments, mood, timbre, arrangement) is not a vocal-control cue and is never part of a vocal conflict.
DELIVERY = frozenset({"whispered", "powerful belting", "spoken word", "soft vocals", "emotional vocals", "chant"})
TEXTURE = frozenset({"raspy vocal", "breathy vocals", "falsetto", "operatic vocals"})
BACKING = frozenset({"harmonies", "choir vocals", "call and response", "ad-lib"})
ENERGY = frozenset({"high energy", "low energy", "building energy", "explosive", "powerful", "anthemic", "soft", "punchy", "energetic", "aggressive"})
VOCAL = DELIVERY | TEXTURE | BACKING
QUIET = frozenset({"whispered", "soft vocals", "breathy vocals", "low energy", "soft"})
LOUD = frozenset({"powerful belting", "explosive", "anthemic", "powerful", "high energy", "aggressive", "punchy", "energetic"})
SUNG = frozenset({"powerful belting", "falsetto", "operatic vocals"})                       # a sung melody, against spoken word
LEAD = DELIVERY | TEXTURE
LOUD_SECTIONS = ("chorus", "hook", "final chorus", "drop", "refrain")

RISK_RANK = {"low": 0, "medium": 1, "high": 2}
KIND_NAME = {"conflicting": "Vocal Cue Conflict", "redundant": "Repeated Vocal Cue", "excessive": "Too Many Vocal Cues", "caption_mismatch": "Caption and Cues Disagree"}

_PROGRESSION = re.compile(r"\b(build|builds|building|grows?|then|becomes?|turns?|evolves?|rises?|opens? up|starts?|ends?|verses?|choruses|chorus|bridge|by the)\b", re.IGNORECASE)
_CAP_LOUD = re.compile(r"\bpowerful\s+(?:lead\s+)?vocals?\b|\bbelt\w*\b|\banthemic\s+vocals?\b|\bexplosive\s+vocals?\b", re.IGNORECASE)
_CAP_QUIET = re.compile(r"\bwhisper\w*\b|\bbreathy\b|\bsoft(?:,\s*\w+)?\s+vocals?\b|\b(?:intimate|gentle|delicate|hushed|tender)\s+(?:lead\s+)?vocals?\b", re.IGNORECASE)


@dataclass
class Finding:
    kind: str                       # conflicting | redundant | excessive | caption_mismatch
    risk: str                       # high | medium | low
    section: str
    section_index: int              # which occurrence of the section name (0 = the first)
    line: int                       # the file line to show first
    lines: list                     # every file line involved
    cues: list                      # the catalogue cues involved, as written in the catalogue
    scope: str                      # section tag | line cue and section | line cues | caption and lyrics
    summary: str
    reason: str
    advice: str
    benefit: str
    sure: bool = True               # decided from catalogue cues (True), or from words found in the caption (False)
    unknown: list = field(default_factory=list)       # cue parts the catalogue does not know: not judged

    @property
    def title(self) -> str:
        name = self.section if self.section_index == 0 else f"{self.section} #{self.section_index + 1}"
        return f"{name or 'Lyrics'} — {KIND_NAME[self.kind]}"


def _cues_of(text: str) -> tuple:
    """(catalogue phrases found in `text`, the parts the catalogue does not know). A part with a known cue counts only for the known cue."""
    known, unknown = [], []
    for part in CL.cue_parts(text):
        hits = CL.mentioned_cues(part)
        if hits:
            for c in hits:
                if c.phrase not in known:
                    known.append(c.phrase)
        else:
            unknown.append(part)
    return known, unknown


def _pole(phrases) -> tuple:
    return [p for p in phrases if p in QUIET], [p for p in phrases if p in LOUD]


def _is_loud_section(name: str) -> bool:
    low = (name or "").lower()
    return any(low.startswith(k) for k in LOUD_SECTIONS)


def _judge(cues: list, where: str, name: str, index: int, line: int, lines: list, scope: str, left: "list | None" = None, right: "list | None" = None,
           unknown: "list | None" = None) -> list:
    """Findings for one effective set of cues. When `left` and `right` are given (a section and a line), only pairs ACROSS them are conflicts: what is inside one of them was judged there."""
    out = []
    uniq = list(dict.fromkeys(cues))
    unknown = list(unknown or [])
    sec = name or "this part"

    def pairs(a_set, b_set):
        res = []
        for a in uniq:
            for b in uniq:
                if a == b or a not in a_set or b not in b_set:
                    continue
                if left is not None and not ((a in left and b in right) or (a in right and b in left)):
                    continue
                res.append((a, b))
        return res

    def add(kind, risk, involved, summary, reason, advice, benefit):
        out.append(Finding(kind, risk, name, index, line, lines, sorted(set(involved), key=uniq.index), scope, summary, reason, advice, benefit, True, unknown))

    quiet_lead = [c for c in uniq if c in QUIET and c in LEAD]
    loud_lead = [c for c in uniq if c in LOUD and c in LEAD]
    quiet_any = [c for c in uniq if c in QUIET]
    loud_any = [c for c in uniq if c in LOUD]
    keep_loud = _is_loud_section(name)
    # 1. opposite lead deliveries (whispered + powerful belting)
    a = pairs(set(quiet_lead), set(loud_lead))
    if a:
        q, l_ = a[0]
        keep = l_ if keep_loud else q
        drop = q if keep_loud else l_
        extra = (f"To get the other side, change the arrangement (for a powerful {sec}: a thinner or fuller instrumentation) instead of asking the voice for both." if keep_loud else
                 f"If a stronger moment is wanted later, give it its own section or a single-line cue instead of {l_} for the whole {sec}.")
        add("conflicting", "high", [q, l_], f"`{q}` and `{l_}` ask for opposite vocal delivery at the same time in {where}.",
            "ACE-Step has to pick one or blend both, and the result may sound unsteady or follow neither. These are two different main deliveries, not two layers.",
            f"Keep `{keep}` for this {sec} and remove `{drop}`. {extra}",
            "One clear vocal direction is easier for the model to follow, and it keeps the section's character steady.")
    # 2. energy against energy (low energy + high energy)
    elif pairs(set(quiet_any) - set(LEAD), set(loud_any) - set(LEAD)):
        q, l_ = pairs(set(quiet_any) - set(LEAD), set(loud_any) - set(LEAD))[0]
        add("conflicting", "high", [q, l_], f"`{q}` and `{l_}` set opposite energy for {where}.",
            "Two energy levels in one place cancel each other, and the section may come out neither calm nor strong.",
            f"Choose the energy this {sec} should have and keep only that cue; a change over time is better written as separate sections.",
            "A single energy direction makes the lift or the calm clearer.")
    # 3. a quiet voice with a loud energy, or the reverse
    else:
        b = pairs(set(quiet_lead), set(loud_any) - set(LEAD)) or pairs(set(loud_lead), set(quiet_any) - set(LEAD))
        if b:
            x, y = b[0]
            add("conflicting", "medium", [x, y], f"`{x}` and `{y}` pull the voice and the energy in opposite directions in {where}.",
                "A quiet vocal under a loud energy cue (or the reverse) can be intended, but ACE-Step may follow only one of them.",
                f"If the contrast is intended, say it in the caption in words and keep one cue here; otherwise keep the cue that matches what the {sec} should feel like.",
                "The vocal and the energy then point the same way, or the contrast is stated once and clearly.")
    # 4. spoken word against a sung melody
    c = pairs({"spoken word"}, set(SUNG))
    if c and not any(f.kind == "conflicting" for f in out):
        s_, m_ = c[0]
        add("conflicting", "medium", [s_, m_], f"`{s_}` and `{m_}` ask for speaking and for a sung melody together in {where}.",
            "Spoken delivery and a sustained melodic delivery are different ways of using the voice.",
            f"Pick the one this {sec} needs. If the passage changes from spoken to sung, put the change on its own line or in its own section.",
            "The model gets one way of using the voice for each part.")
    # 5. redundant: the same cue twice, or two cues of the same pole and job
    if left is None:
        raw = [x for x in cues]
        dup = [x for x in dict.fromkeys(raw) if raw.count(x) > 1]
        same_pole = []
        for group in (QUIET, LOUD):
            inside = [x for x in uniq if x in group and (x in LEAD or x in ENERGY)]
            if len(inside) >= 2 and not out:
                same_pole = inside
        if dup:
            add("redundant", "low", dup, f"`{dup[0]}` is written more than once in {where}.", "Repeating a cue adds no information and makes the tag longer.",
                f"Keep a single `{dup[0]}`.", "A shorter tag is less likely to be sung as words.")
        elif same_pole and not any(f.kind == "conflicting" for f in out):
            add("redundant", "low", same_pole[:3], f"`{'` and `'.join(same_pole[:2])}` ask for the same thing in {where}.",
                "Two cues with the same meaning do not add control; the longer tag only gives the model more to parse.",
                f"Keep the cue that says it most clearly (for example `{same_pole[0]}`) and drop the other.", "Fewer, clearer words in the tag.")
    # 6. excessive: many different vocal-control cues, or two main deliveries
    if left is None and not any(f.kind in ("conflicting", "redundant") for f in out):
        vocal = [c for c in uniq if c in VOCAL]
        deliveries = [c for c in uniq if c in DELIVERY and c != "emotional vocals"]      # emotional vocals colours a delivery, it is not a second one
        if len(vocal) >= 3:
            add("excessive", "medium", vocal[:4], f"{len(vocal)} different vocal cues ({', '.join('`' + v + '`' for v in vocal[:4])}) apply together in {where}.",
                "Each cue is a separate instruction to the voice. With many at once ACE-Step may follow some and ignore the rest, and a long tag can be sung as words.",
                f"Name the one delivery that matters most for this {sec} and keep the others only if each does a different job; say the overall vocal character in the caption.",
                "A simpler instruction is followed more steadily and leaves room for the music.")
        elif len(deliveries) >= 2:
            add("excessive", "low", deliveries[:3], f"Two main vocal deliveries (`{deliveries[0]}`, `{deliveries[1]}`) are asked for in {where}.",
                "The studio guideline is one main vocal delivery per sung section; a second one needs a clear reason.",
                f"If the {sec} really changes, use a line cue for the one line that differs, or split it into two sections; otherwise keep `{deliveries[0]}`.",
                "The main delivery stays clear, and the exception is marked where it happens.")
    if left is not None:                                # a line cue inside a section: the usual way to vary one line, so it can be intended; the risk is at most medium and the advice names both ways out
        for f in out:
            if f.kind == "conflicting":
                f.risk = "medium" if f.risk == "high" else f.risk
                x, y = f.cues[0], f.cues[-1]
                f.advice = (f"If only this one line should differ, keep the line cue and let the section tag carry the general direction; if the whole {sec} should be `{x}` or `{y}`, "
                            f"keep that one and remove the other. A part that really changes can be its own section.")
                f.reason += " A line cue inside a section is a legitimate way to vary one line, so this may be intended; ACE-Step may follow only one of the two."
    return out


def analyze(text: str, legacy_tag_conflict: "Optional[Callable[[list], bool]]" = None) -> list:
    """The vocal cue findings of these lyrics (section tags and line cues). `legacy_tag_conflict(parts)` says that the older section-tag check already reports a conflict for these cue parts."""
    findings: list = []
    sec_name, sec_index, sec_line, sec_cues, sec_unknown = "", 0, 0, [], []
    seen: dict = {}
    pending: list = []                  # (file line, phrases) of standalone cue lines waiting for the next sung line
    done_line_pairs: set = set()
    for n, raw in enumerate((text or "").replace("\r\n", "\n").split("\n"), start=1):
        st = raw.strip()
        if not st:
            continue
        if K.TAG_LINE.match(st):
            inner = st[1:-1]
            parsed = TP.parse_tag(inner)
            if parsed.kind == "section":
                name = re.sub(r"\s+", " ", parsed.name or "").strip()
                sec_index = seen.get(name.casefold(), 0)
                seen[name.casefold()] = sec_index + 1
                sec_name, sec_line = name, n
                sec_cues, sec_unknown = _cues_of(parsed.cue_text or "")
                pending = []
                parts = CL.cue_parts(parsed.cue_text or "")
                if sec_cues and not (legacy_tag_conflict and legacy_tag_conflict(parts)):
                    findings += _judge(_all_written(parts), f"the tag of {name}", name, sec_index, n, [n], "section tag", unknown=sec_unknown)
                continue
            if st.casefold() == "[hold]":
                continue
            known, _unk = _cues_of(inner)
            lead = [c for c in known if c in VOCAL or c in ENERGY]
            if lead:
                pending.append((n, lead))
            continue
        # a sung line: the pending line cues apply to it together with the section's cues
        if pending:
            line_cues = [c for _n, cs in pending for c in cs]
            first = pending[0][0]
            lines = [first, n]
            key = (sec_name, sec_index, tuple(line_cues))
            if key not in done_line_pairs:
                done_line_pairs.add(key)
                if len(pending) >= 2:
                    findings += _judge(line_cues, f"the cues above line {n}", sec_name, sec_index, first, lines, "line cues")
                combined = list(dict.fromkeys(sec_cues + line_cues))
                findings += _judge(combined, f"line {n} of {sec_name or 'the song'}", sec_name, sec_index, first, lines, "line cue and section", left=sec_cues, right=line_cues)
            pending = []
    return _merge(findings)


def _all_written(parts: list) -> list:
    """Every catalogue cue in the order written, with repeats (a repeat is what 'redundant' looks for)."""
    out = []
    for p in parts:
        for c in CL.mentioned_cues(p):
            out.append(c.phrase)
    return out


def _merge(findings: list) -> list:
    """One finding per (section, kind, cues): the same line cue above several lines of a section is reported once, with every line."""
    out: dict = {}
    for f in findings:
        key = (f.section, f.section_index, f.kind, tuple(f.cues), f.scope)
        got = out.get(key)
        if got is None:
            out[key] = f
        else:
            got.lines = sorted(set(got.lines + f.lines))
    return sorted(out.values(), key=lambda f: (-RISK_RANK[f.risk], f.line))


def caption_findings(text: str, caption: str) -> list:
    """The vocal description of the caption against the cues of the lyrics. Only a caption that describes ONE vocal direction is judged; a caption that describes a change over the song is left alone."""
    cap = caption or ""
    quiet, loud = bool(_CAP_QUIET.search(cap)), bool(_CAP_LOUD.search(cap))
    if quiet == loud:
        return []                                      # no vocal direction, or both: nothing to compare
    if _PROGRESSION.search(cap):
        return []                                      # "soft verse building to a powerful chorus" describes a change: no mismatch to report
    out = []
    seen = set()
    sec_name, sec_index, sec_cues, names = "", 0, [], {}
    for n, raw in enumerate((text or "").replace("\r\n", "\n").split("\n"), start=1):
        st = raw.strip()
        if not K.TAG_LINE.match(st):
            continue
        parsed = TP.parse_tag(st[1:-1])
        cues = []
        if parsed.kind == "section":
            sec_name = re.sub(r"\s+", " ", parsed.name or "").strip()
            sec_index = names.get(sec_name.casefold(), 0)
            names[sec_name.casefold()] = sec_index + 1
            cues = _cues_of(parsed.cue_text or "")[0]
        else:
            cues = _cues_of(st[1:-1])[0]
        opposite = [c for c in cues if c in LEAD and (c in (LOUD if quiet else QUIET))]
        for c in opposite:
            key = (sec_name, sec_index, c)
            if key in seen:
                continue
            seen.add(key)
            cap_word = "quiet, soft or whispered" if quiet else "powerful or belted"
            out.append(Finding("caption_mismatch", "medium", sec_name, sec_index, n, [n], [c], "caption and lyrics",
                               f"The caption describes the vocal as {cap_word}, but {sec_name or 'a part'} asks for `{c}`.",
                               "The caption and the lyric cues should describe the voice the same way; when they disagree ACE-Step may follow either one, or blend them.",
                               f"If `{c}` is a deliberate contrast, say in the caption that the vocal changes there; if not, make the caption or the cue match the other.",
                               "The same vocal character in both places is followed more steadily.", sure=False))
    return out


def findings_text(items: list, limit: int = 8) -> list:
    """Plain lines for the Music Producer's facts: where, which cues, the kind, the risk and what is suggested. Facts only: the program never edits a cue."""
    rows = []
    for f in items[:limit]:
        where = f"line {f.line}" if len(f.lines) <= 1 else f"lines {', '.join(str(x) for x in f.lines)}"
        rows.append(f"- {f.title} ({where}; cues: {', '.join(f.cues)}; {f.scope}) | risk: {f.risk} | {f.summary} Why: {f.reason} Suggestion: {f.advice} Benefit: {f.benefit}"
                    + ("" if f.sure else " (judged from the words of the caption, so less certain)"))
    if len(items) > limit:
        rows.append(f"- … and {len(items) - limit} more.")
    return rows


def preflight(text: str, caption: str = "") -> list:
    """Short warnings for the moment before Generate: only conflicts the program is SURE of (two catalogue cues that ask for opposite things at the same time). Never blocks, never edits, no LLM.
    `caption` is accepted so the signature can grow; a caption mismatch is judged from words and is not sure, so it is not part of the preflight."""
    out = []
    for f in analyze(text):
        if f.kind == "conflicting" and f.sure:
            where = f"line {f.lines[-1]}" if f.scope.startswith("line") else f"line {f.line}"
            out.append(f"{f.title} ({where}): {' vs '.join(f.cues[:2])}")
    return out
