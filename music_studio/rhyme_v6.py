"""Rhyme map v6: the structured answer of the Rhyme Analyzer AI and the Python that checks it. No LLM call and no UI here.

The AI reports, as JSON, the rhyme scheme of every section and every rhyme pair with its sound type, relation, placement and position (the line numbers and the quoted text, with start / end /
occurrence in the line as the AI was shown it: 0-based, start inclusive, end exclusive, in characters = Unicode code points). The AI never sets a colour and never decides where a word is in
the text. This module:
  * cuts a long song into windows (whole sections together, a few lines of context before each next window) and writes the request;
  * parses the answer strictly (a broken schema is an error, never guessed at);
  * validates every position against the lyrics: the line exists, the text is exactly lyrics[start:end], the word is not in a tag or cue, the syllable is not cut in the middle, and a word that
    appears several times is not mixed up with another one. A wrong index is corrected only when the quoted text is found at exactly ONE place (or the `occurrence` names it) and the correction
    is recorded; otherwise the pair is not placed and the reason is kept. A passed position check says nothing about the sound: `position_validated` and `phonetic_verification` stay apart;
  * derives the placement of each pair from the validated positions, and the end-rhyme scheme of every section from the validated end pairs (the AI's scheme is kept beside it);
  * merges the windows deterministically.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import rhyme_map as RM
from . import thai_rhyme as TR

SCHEMA = "rhyme-map-v6"
WINDOW_MAX = 60                                  # sung lines per request (whole sections are kept together when they fit)
CONTEXT_LINES = 6                                # lines of the previous window repeated as read-only context, so a rhyme across the edge is seen
SEP_MAP = "=========== RHYME MAP ==========="
SEP_END = "=========== END ==========="

SOUND_TYPES = ("vowel_rhyme", "consonant_rhyme", "assonance", "consonance", "alliteration", "multisyllabic", "wordplay")
RELATIONS = ("exact", "near", "uncertain")
PLACEMENTS = ("internal", "end_to_end", "end_to_internal", "cross_line", "cross_section")
ROLES = ("สัมผัสใน", "สัมผัสนอก", "")
CONFIDENCE = ("high", "medium", "low", "uncertain")

CONTRACT = f"""
OUTPUT FORMAT (the program reads it exactly; write nothing outside it):
{SEP_MAP}
one JSON object, then the marker line
{SEP_END}

REQUEST: the lyrics come as lines written `S<sung line> L<file line> | text`. S counts the sung lines of the whole song; L is the line number in the lyrics text. `## Section sN: Type` lines
show the sections. Lines marked CONTEXT are only there so a rhyme across the window edge can be seen: report a pair only if at least one of its two words is on a line that is not CONTEXT.

JSON SCHEMA ("{SCHEMA}"):
{{
  "schema": "{SCHEMA}",
  "languages": ["th", "en"],                      // the languages you found, by ISO code
  "sections": [ {{
      "section_id": "s1", "type": "Verse",
      "scheme": "AABB",                           // end-rhyme scheme of this section: letters per line in order, "-" for a line that rhymes with nothing
      "line_schemes": {{"1": "A", "2": "A", "3": "B", "4": "B"}},   // key = S number
      "summary": "one or two sentences about the rhymes of this section, naming lines",
      "observations": [ {{"line": 3, "kind": "quality", "text": "..."}}, {{"line": 4, "kind": "uncertain", "text": "..."}} ]   // kind: quality | uncertain
  }} ],
  "pairs": [ {{
      "pair_id": "p1", "group_id": "gA",         // the same sound shares a group_id
      "sound_type": "vowel_rhyme",                // {" | ".join(SOUND_TYPES)}
      "relation": "exact",                        // exact | near | uncertain
      "placement": "end_to_end",                  // {" | ".join(PLACEMENTS)}  (the program checks it against the positions)
      "traditional_role": "สัมผัสนอก",             // สัมผัสใน | สัมผัสนอก | "" when it does not apply
      "a": {{"file_line": 2, "sung_line": 1, "text": "ฝัน", "start": 20, "end": 23, "occurrence": 1, "role": "end", "language": "th"}},
      "b": {{"file_line": 3, "sung_line": 2, "text": "วัน", "start": 18, "end": 21, "occurrence": 1, "role": "end", "language": "th"}},
      "reason": "same short vowel a and final n",
      "confidence": "high"                        // high | medium | low | uncertain
  }} ],
  "overview": {{ "text": "the rhyme structure of the whole song, the hook, the variety, the places where a rhyme bends the meaning or the vocal flow", "points": [ {{"line": 5, "text": "..."}} ] }}
}}
- text: the word or the rhyming SYLLABLE, copied exactly from the line. start/end: 0-based, start inclusive, end exclusive, counted in characters of the line text shown after the `|`. If you are not sure
  of the numbers, leave start and end out and give text and occurrence: never guess a number.
- role: "end" when the quoted text is the last word of its line, otherwise "internal".
- Never pair a word with itself or with the same word repeated. Never report a word from a [tag]. Never write replacement lyrics.
- Valid JSON only: double quotes, no comments, no trailing commas. Use an empty list when there is nothing to report.
""".strip()


@dataclass
class Endpoint:
    file_line: int
    sung_line: int
    text: str
    start: "int | None" = None
    end: "int | None" = None
    occurrence: "int | None" = None
    role: str = ""
    language: str = ""
    # filled by the validator
    start_v: int = -1                           # validated start / end in the sung line (0-based, end exclusive)
    end_v: int = -1
    validated: bool = False
    fixed: bool = False                         # the AI's index was wrong and Python corrected it (the text was found at exactly one place)
    note: str = ""


@dataclass
class Pair:
    pair_id: str
    group_id: str
    sound_type: str
    relation: str
    placement: str
    traditional_role: str
    a: Endpoint
    b: Endpoint
    reason: str = ""
    confidence: str = "medium"


@dataclass
class SectionInfo:
    index: int                                   # position among the sections of the lyrics (-1 = before the first tag)
    name: str
    sung_lines: list                              # the S numbers
    scheme: str = ""                              # derived by Python from the validated end pairs (exact only)
    scheme_name: str = ""                         # AABB | ABAB | ABCB | AAAA | ABBA | free | none
    near_lines: list = field(default_factory=list)  # S numbers that are linked only by a NEAR rhyme: never shown as part of the exact scheme
    ai_scheme: str = ""
    scheme_differs: bool = False
    summary: str = ""
    quality: list = field(default_factory=list)   # [(line, text)]
    uncertain: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)    # relation / placement counts of the validated pairs


class SchemaError(ValueError):
    """The answer is not rhyme-map-v6 at all (no JSON, wrong schema). A pair that is merely wrong is dropped with a message instead."""


# ── windows and the request ───────────────────────────────────────────────────────────────────────────
def windows(lines: list, limit: int = WINDOW_MAX, context: int = CONTEXT_LINES) -> list:
    """[(window lines, context lines)] for SungLine objects. Whole sections stay together while they fit in `limit`; a longer section is cut by lines. Every window after the first also gets
    the last `context` lines of the one before as read-only context."""
    if not lines:
        return []
    chunks, cur = [], []
    sections: list = []
    for ln in lines:
        if not sections or sections[-1][0] != ln.section:
            sections.append((ln.section, []))
        sections[-1][1].append(ln)
    for _sec, ls in sections:
        while len(ls) > limit:
            if cur:
                chunks.append(cur)
                cur = []
            chunks.append(ls[:limit])
            ls = ls[limit:]
        if cur and len(cur) + len(ls) > limit:
            chunks.append(cur)
            cur = []
        cur = cur + ls
    if cur:
        chunks.append(cur)
    out = []
    for i, ch in enumerate(chunks):
        ctx = chunks[i - 1][-context:] if i and context else []
        out.append((ch, ctx))
    return out


def build_request(window: list, context: list, language: str = "") -> str:
    """The user message of one window: the language setting and the lines, with the sections marked. `S<n> L<file line> | text`."""
    rows = [f"LANGUAGE SETTING: {language or 'auto (decide from the lyrics)'}",
            "Lines marked CONTEXT are only context for rhymes across the window edge.", ""]
    last = object()
    for tag, group in (("CONTEXT ", context), ("", window)):
        for ln in group:
            if ln.section != last:
                rows.append(f"## Section {section_id(ln.section)}: {ln.section_name or '(before the first tag)'}")
                last = ln.section
            rows.append(f"{tag}S{ln.num} L{ln.file_line} | {ln.text}")
        if group is context and context:
            rows.append("")
            last = object()
    return "\n".join(rows)


def section_id(section: int) -> str:
    return f"s{section + 1}"


# ── parsing ───────────────────────────────────────────────────────────────────────────────────────────
def _extract_json(raw: str) -> str:
    text = re.sub(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$", "", raw or "").strip()
    if not text:
        raise SchemaError("The AI answer is empty.")
    a = [m.start() for m in re.finditer(r"^\W*=+\s*RHYME MAP\s*=+\W*$", text, re.M | re.I)]
    z = [m.start() for m in re.finditer(r"^\W*=+\s*END\s*=+\W*$", text, re.M | re.I)]
    if a:
        body = text[text.index("\n", a[0]) + 1:] if "\n" in text[a[0]:] else ""
        if z and z[-1] > a[0]:
            body = text[text.index("\n", a[0]) + 1:z[-1]]
    else:
        body = text
    body = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", body.strip(), flags=re.I)
    i, j = body.find("{"), body.rfind("}")
    if i < 0 or j <= i:
        raise SchemaError("The AI answer has no JSON object.")
    return body[i:j + 1]


def _int(x):
    return x if isinstance(x, int) and not isinstance(x, bool) else None


def _endpoint(d: dict) -> "Endpoint | None":
    if not isinstance(d, dict):
        return None
    text = d.get("text")
    fl, sl = _int(d.get("file_line")), _int(d.get("sung_line"))
    if not isinstance(text, str) or not text.strip() or sl is None:
        return None
    occ = _int(d.get("occurrence"))
    return Endpoint(fl if fl is not None else -1, sl, text, _int(d.get("start")), _int(d.get("end")), occ if occ and occ > 0 else None,
                    str(d.get("role") or "").lower(), str(d.get("language") or "").lower())


def parse(raw: str) -> tuple:
    """(pairs, sections dict list, overview dict, languages, ignored messages). SchemaError when the answer is not a rhyme-map-v6 object. One bad pair is dropped with a message."""
    try:
        data = json.loads(_extract_json(raw))
    except json.JSONDecodeError as ex:
        raise SchemaError(f"The AI answer is not valid JSON ({ex.msg}).") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise SchemaError(f"The AI answer is not {SCHEMA}.")
    ignored, pairs, seen = [], [], set()
    for i, d in enumerate(data.get("pairs") or [], 1):
        if not isinstance(d, dict):
            ignored.append(f"Pair {i} is not an object.")
            continue
        a, b = _endpoint(d.get("a")), _endpoint(d.get("b"))
        if a is None or b is None:
            ignored.append(f"Pair {d.get('pair_id') or i}: a word or its line is missing.")
            continue
        sound = str(d.get("sound_type") or "vowel_rhyme").lower()
        rel = str(d.get("relation") or "uncertain").lower()
        place = str(d.get("placement") or "").lower()
        role = str(d.get("traditional_role") or "").strip()
        conf = str(d.get("confidence") or "medium").lower()
        if sound not in SOUND_TYPES:
            ignored.append(f"Pair {d.get('pair_id') or i}: unknown sound type “{sound[:24]}”.")
            continue
        if rel not in RELATIONS:
            ignored.append(f"Pair {d.get('pair_id') or i}: unknown relation “{rel[:24]}”.")
            continue
        pid = str(d.get("pair_id") or f"p{i}")
        if pid in seen:
            pid = f"{pid}_{i}"
        seen.add(pid)
        pairs.append(Pair(pid, str(d.get("group_id") or ""), sound, rel, place if place in PLACEMENTS else "", role if role in ROLES else "", a, b,
                          str(d.get("reason") or "").replace("\n", " ").strip(), conf if conf in CONFIDENCE else "medium"))
    secs = [x for x in (data.get("sections") or []) if isinstance(x, dict)]
    ov = data.get("overview") if isinstance(data.get("overview"), dict) else {}
    langs = [str(x).lower() for x in (data.get("languages") or []) if isinstance(x, str)]
    return pairs, secs, ov, langs, ignored


# ── the position validator ────────────────────────────────────────────────────────────────────────────
_sung_map = RM.sung_map


def _find_all(hay: str, needle: str) -> list:
    out, i = [], hay.find(needle)
    while i >= 0:
        out.append(i)
        i = hay.find(needle, i + 1)
    return out


def validate_endpoint(ep: Endpoint, line, lyrics: str, language: str = "") -> str:
    """Check one endpoint against its sung line (a `RM.Line`). Sets start_v / end_v / validated / fixed. Returns '' when it is placed, else the reason it was not."""
    text, fl = ep.text, ep.file_line
    sung, _imap = _sung_map(line.masked)
    if sung == "":
        return f"line {ep.sung_line} has no sung text."
    if "[" in text or "]" in text:
        return f"“{text}” looks like a [tag] or a cue, not a lyric word."
    if fl not in (-1, line.file_line):
        return f"line {ep.sung_line} is line {line.file_line} of the lyrics, not {fl}."
    where = _find_all(sung, text)
    if not where:
        return f"“{text}” is not in line {ep.sung_line}."
    pick, fixed = None, False
    if ep.start is not None and ep.end is not None and 0 <= ep.start <= ep.end <= len(sung) and sung[ep.start:ep.end] == text:
        pick = ep.start
        if ep.occurrence is not None and (where.index(pick) + 1) != ep.occurrence:
            return f"“{text}” in line {ep.sung_line}: the index and the occurrence name different places, so it was not placed."
    elif len(where) == 1:
        pick, fixed = where[0], True                          # a wrong or missing index, but the text is at exactly one place: Python places it and says so
    elif ep.occurrence is not None and 1 <= ep.occurrence <= len(where):
        pick, fixed = where[ep.occurrence - 1], True
    else:
        return f"“{text}” appears {len(where)} times in line {ep.sung_line} and neither the index nor the occurrence says which one, so it was not placed."
    end = pick + len(text)
    # the word must be one real piece of the line: not the middle of a syllable (Thai), and a whole word in a script that has spaces
    if TR.has_thai(text):
        st = TR._span_status(sung, pick, end)
        if st == "fragment":
            return f"“{text}” in line {ep.sung_line} is only part of a longer syllable."
    elif not (RM.occurrences(sung, text) and pick in [o[0] for o in RM.occurrences(sung, text)]):
        return f"“{text}” in line {ep.sung_line} is not a whole word."
    ep.start_v, ep.end_v, ep.validated, ep.fixed = pick, end, True, fixed
    ep.note = "the index was corrected by Python (the text is at exactly one place)" if fixed else ""
    if not ep.language:
        ep.language = RM.language_of(text, language)
    return ""


def absolute(line, ep: Endpoint) -> tuple:
    """(absolute start, absolute end) in the lyrics text of a validated endpoint, or (-1, -1) when the sung text is not one contiguous piece of the editor line."""
    sung, imap = _sung_map(line.masked)
    if not ep.validated or ep.end_v > len(sung) or ep.start_v >= ep.end_v:
        return -1, -1
    a, b = imap[ep.start_v], imap[ep.end_v - 1] + 1
    if b - a != ep.end_v - ep.start_v:
        return -1, -1
    return line.start + a, line.start + b


def derive_placement(a: Endpoint, b: Endpoint, la, lb, ma: "RM.Line", mb: "RM.Line") -> str:
    """The placement of a pair from its VALIDATED positions (the AI's own label is only a claim): internal (one line), end_to_end, end_to_internal, cross_line (two inner words of different
    lines) or cross_section (the lines are in different sections)."""
    if a.sung_line == b.sung_line:
        return "internal"
    a_end = _is_end(a, ma)
    b_end = _is_end(b, mb)
    if la.section != lb.section:
        return "cross_section"
    if a_end and b_end:
        return "end_to_end"
    if a_end or b_end:
        return "end_to_internal"
    return "cross_line"


def _is_end(ep: Endpoint, line: "RM.Line") -> bool:
    sung, imap = _sung_map(line.masked)
    return ep.end_v == len(sung) or (ep.end_v < len(sung) and not sung[ep.end_v:].strip(" .,!?:;…\"'”’"))


# ── the scheme ────────────────────────────────────────────────────────────────────────────────────────
def scheme_name(s: str) -> str:
    """AABB / ABAB / ABCB / AAAA / ABBA for a four-line scheme (each '-' line counts as a letter of its own, so '-B-B' is ABCB), 'free' for any other pattern that has a rhyme, 'none' for a section
    with no rhyme at all."""
    if not s.replace("-", ""):
        return "none"
    fresh, letters = iter("ZYXWVUTSRQPONMLKJIHGFEDCBA"), []
    for c in s:
        letters.append(c if c != "-" else next(fresh))
    seen = {}
    norm = "".join(seen.setdefault(c, chr(ord("A") + len(seen))) for c in letters)
    return norm if len(s) == 4 and norm in ("AABB", "ABAB", "AAAA", "ABBA", "ABCB") else "free"


def derive_schemes(sections: list, links: list) -> None:
    """Fill `scheme`, `scheme_name`, `near_lines` and `counts` of every SectionInfo from the validated pairs. Only END-TO-END exact pairs make the scheme (an internal rhyme never changes it); a line
    that is linked only by a near rhyme is listed apart and stays '-' in the exact scheme."""
    by_line = {}
    for sec in sections:
        for n in sec.sung_lines:
            by_line[n] = sec
    parents: dict = {}
    near_only: dict = {}

    def find(x):
        while parents[x] != x:
            parents[x] = parents[parents[x]]
            x = parents[x]
        return x
    for sec in sections:
        for n in sec.sung_lines:
            parents[n] = n
    for k in links:                                    # lyric_rhyme.RhymePair objects
        pa, pb = k.a_line, k.b_line
        sec = by_line.get(pa)
        if sec is not None and k.verification != "rejected":
            sec.counts[k.sound] = sec.counts.get(k.sound, 0) + 1
            sec.counts[k.placement_detail] = sec.counts.get(k.placement_detail, 0) + 1
        if k.placement_detail != "end_to_end" or pa == pb or by_line.get(pa) is not by_line.get(pb) or k.verification == "rejected":
            continue
        if k.sound == "exact" and k.verification in ("verified", "ai_assessed") and k.kind == "vowel":
            parents[find(pa)] = find(pb)
        elif k.sound == "near" and k.verification in ("verified", "ai_assessed"):
            near_only.setdefault(by_line[pa].index, set()).update((pa, pb))
    for sec in sections:
        letters, out = {}, []
        roots = [find(n) for n in sec.sung_lines]
        size = {r: roots.count(r) for r in roots}
        for n, r in zip(sec.sung_lines, roots):
            if size[r] < 2:
                out.append("-")
            else:
                letters.setdefault(r, chr(ord("A") + len(letters)))
                out.append(letters[r])
        sec.scheme = "".join(out)
        sec.scheme_name = scheme_name(sec.scheme) if len(sec.sung_lines) >= 2 else ""
        sec.near_lines = sorted(n for n in near_only.get(sec.index, set()) if sec.scheme[sec.sung_lines.index(n)] == "-")
        if sec.ai_scheme:
            sec.scheme_differs = sec.ai_scheme.replace("~", "") != sec.scheme and len(sec.ai_scheme) == len(sec.scheme)


def merge_window_pairs(groups: list) -> list:
    """The pairs of every window as ONE list, without duplicates: the same two words (the same two positions, in any order) are one pair, and the one with the stronger verification is kept."""
    best: dict = {}
    for ps in groups:
        for p in ps:
            k = p.pos_key
            cur = best.get(k)
            if cur is None or RM._STRENGTH.get(p.verification, 0) > RM._STRENGTH.get(cur.verification, 0):
                best[k] = p
    return list(best.values())
