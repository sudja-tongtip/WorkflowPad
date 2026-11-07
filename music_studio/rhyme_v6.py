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

from . import clear_tags as CG
from . import rhyme_map as RM
from . import thai_rhyme as TR

SCHEMA = "rhyme-map-v6"
WINDOW_MAX = 60                                  # sung lines per request (whole sections are kept together when they fit)
CONTEXT_LINES = 6                                # lines of the previous window repeated as read-only context, so a rhyme across the edge is seen
SEP_MAP = "=========== RHYME MAP ==========="          # older answers still use this marker; new answers use numbered tags
SEP_END = "=========== END ==========="
# The reader still accepts the old JSON object. Its end-group key in source is "end_groups": {{

SOUND_TYPES = ("vowel_rhyme", "consonant_rhyme", "assonance", "consonance", "alliteration", "multisyllabic", "wordplay")
RELATIONS = ("exact", "near", "uncertain")
PLACEMENTS = ("internal", "end_to_end", "end_to_internal", "cross_line", "cross_section")
ROLES = ("สัมผัสใน", "สัมผัสนอก", "")
CONFIDENCE = ("high", "medium", "low", "uncertain")

CONTRACT = f"""
OUTPUT FORMAT (the program only READS it; it does not re-judge your sounds, your groups or your pairs, so they must be right). Version {SCHEMA}. Numbered clear tags only. No JSON and no code fence. A control tag is a whole line. End with [DONE].
NUMBERING: each tag type ([SECTION], [END], [LINK], [REPEAT], [NOTE]) has ONE counter for the WHOLE answer. It starts at 1 and only goes up. It does NOT restart in a new section: if section 1 used [END.1] to [END.4], the first [END] of section 2 is [END.5]. [ENDSECTION.n] repeats the number of its [SECTION.n]. A number used twice makes that block unusable.

[META.1]
languages: th, en

[SECTION.1]
id: s1

[END.1]
sound: a S n
L2|ฝัน
L3|วัน

[END.2]
sound: ai S 0
L4|ไกล
L5|ใจ

[LINK.1]
relation: exact
sound_type: vowel_rhyme
placement: internal
confidence: high
reason: same vowel ai
L4|ไกล|internal
L4|ใจ|internal

[ENDSECTION.1]

[SECTION.2]
id: s2

[END.3]
sound: ae L 0
L7|แม่
L8|แม่

[REPEAT.1]
L7|แม่
L8|แม่

[NOTE.1]
line: 9
text: pronunciation not certain

[ENDSECTION.2]
[DONE]

REQUEST: the lyrics come as lines written `L<line number> | text`. Use these L numbers EVERYWHERE in your answer (the line ids under [END], [LINK] and [REPEAT], the line of a note): there is no other line numbering. `## Section sN: Type` lines
show the sections. Lines marked CONTEXT are only there so a rhyme across the window edge can be seen: report a pair only if at least one of its two words is on a line that is not CONTEXT.

DO THE WORK IN THIS ORDER (it is the order of the tags):
1. For EVERY line (outside parentheses) decide how its LAST SYLLABLE is pronounced as THREE parts: `<vowel> <S or L> <final>`: the vowel in simple Latin letters, S = short or L = long vowel, and the final consonant sound (`0` when the syllable has none). Tone is ignored. Examples: วัน = `a S n`, ยัง = `a S ng`, ดารา = `aa L 0`, น้า = `aa L 0`, ถ้า = `aa L 0`, พา = `aa L 0`, กลัว = `ua L 0`, ไหม = `ai S 0`, ใจ = `ai S 0`, แม่ = `ae L 0`, แล้ว = `ae L w`, แดง = `ae L ng`, เย็น = `e S n`, โอเค = `e L 0`.
2. Write the answer section by section. Between [SECTION.n] and [ENDSECTION.n] put ONLY that section's tags ([END], [LINK], [REPEAT], [NOTE]); never copy the lyrics there as plain lines. Every [END] tag is one end group: its `sound:` line is the key (a three-part end sound) and under it, one per line as `L<number>|<last word>`, are the lines of this section whose last syllable has EXACTLY that sound (all three parts equal). Two lines with the same key are ONE [END] tag, not two. A line whose end sound no other line of the section shares has an [END] tag of its own with one id. Every line of the section outside parentheses is under exactly ONE key. A near rhyme is NOT put under one key: it goes in a [LINK] tag as `near`. Judge the SOUND only: flow, mood and meaning never turn a different sound into the same key.
3. Only then write [LINK] tags: internal rhymes, a rhyme between the end of one line and the inside of another, a NEAR end rhyme, consonant rhymes / alliteration / assonance, multisyllabic rhymes. Look inside the SAME section. A pair across two sections is allowed ONLY for a structural reason (a hook or refrain that comes back, or a sound link that is really prominent): write that reason in `reason`, starting with `across sections:`. Never pair two words only because they are in the same song.
4. CHECK before you answer: COUNT the lines of each section outside parentheses and COUNT the line ids under all its [END] tags: the two numbers must be equal (a forgotten line is a mistake; a line you cannot decide goes under a key of its own); each line is under one key only; every key is written in the same three-part form, so two lines with the same sound are under the SAME key (check every pair of single keys: if two keys are equal, join them); a pair you list as exact is not already one [END] tag; every number is used once in the whole answer.

TAG FIELDS ("{SCHEMA}") - keep it SHORT. sound_type is one of: {" | ".join(SOUND_TYPES)}. relation is exact, near, or uncertain. confidence is high, medium, low, or uncertain.
[LINK]: At most 10, each ONCE. Never an exact end rhyme that an [END] tag already shows. Both words MUST have a line id AND text.
reason: at most 6 words about the SOUND (never flow or mood).
RULES:
- [END] tags are the single source of the end rhyme scheme: do NOT write a scheme string, letters or a scheme name anywhere. The program names the groups A, B, C itself, in line order.
- Repetition (the same word again) is not a rhyme: the repeated lines stay in the group of their sound, and the word goes ONLY in a [REPEAT] tag (never in a [LINK] tag).
- Use a [NOTE] tag for a line whose pronunciation you cannot decide; leave such a line in a group of its own.
- text: the word or the rhyming SYLLABLE, copied exactly from the line. Add |2 (1 = the first) ONLY when the same text appears more than once in that line, as `L4|ใจ|2` or `L4|ใจ|internal|2`. Do not write start, end, sung_line or language.
- role: end when the quoted text is the last word of its line outside the parentheses, otherwise internal. Write the role only when you know it.
- PARENTHESES: everything inside ( ... ) is NOT sung text for this analysis. Never use it as a rhyme word, never as a line ending, never count its syllables; a line that ends in parentheses ends at the last word before them. A line that is ONLY parentheses is in no group.
- Never report a word from a [tag]. A line of the lyric that would look like a control tag is written with one leading backslash. Never write replacement lyrics.
- Leave a tag out when you have nothing for it. Do not invent an empty tag.
""".strip()

STEP_CONTRACT = ("""
ANSWER FORMAT OF THIS CONVERSATION (the program only READS it; it does not re-judge your sounds, groups or pairs, so they must be right). The lyrics come first, as data. After that each request asks
about ONE part of the song: answer ONLY in the format that request asks for. Lyric lines are written `L<number> | text`: use those L numbers everywhere, there is no other line numbering.

When a request asks for the rhymes of ONE section, answer with tags only. Each tag is a whole line, numbered from 1, no number repeated in the answer. End with [DONE]. Example:

[END.1]
sound: a S n
L2|วัน
L3|ฉัน

[END.2]
sound: ai S 0
L4|ไกล
L5|ใจ

[LINK.1]
relation: exact
sound_type: vowel_rhyme
placement: internal
confidence: high
reason: same vowel ai
L4|ไกล|internal
L4|ใจ|internal

[REPEAT.1]
L6|แม่
L7|แม่

[NOTE.1]
line: 9
text: pronunciation not certain
[DONE]

- [END]: one tag per end sound. `sound:` is `<vowel> <S or L> <final>` of the LAST syllable of the line (vowel in simple Latin letters, S = short or L = long, final consonant sound or `0`; tone is ignored). Under it, one line `L<number>|<last word>` for each line of THIS section whose last syllable has EXACTLY that sound (all three parts equal). Two lines with the same sound are ONE [END] tag, never two. Every sung line of the section (outside parentheses) is under exactly one [END]. A line whose sound no other line shares has an [END] of its own. Examples: วัน = `a S n`, ยัง = `a S ng`, ดารา = `aa L 0`, ถ้า = `aa L 0`, กลัว = `ua L 0`, ไหม = `ai S 0`, ใจ = `ai S 0`, แม่ = `ae L 0`, แล้ว = `ae L w`, แดง = `ae L ng`, เย็น = `e S n`, โอเค = `e L 0`.
- Before you answer, check each section: the number of lines equals the number of ids under its [END] tags, and no two of your [END] tags have the same `sound:`.
- [LINK]: at most 5 per section, each once: internal rhymes, a rhyme between the end of one line and the inside of another, a near end rhyme, consonant rhyme, alliteration, assonance. Never an exact end rhyme that an [END] tag already shows. sound_type is one of: """
                 + " | ".join(SOUND_TYPES) + """. relation is exact, near or uncertain. confidence is high, medium, low or uncertain. reason: at most 6 words about the SOUND. Both words need a line id AND text. `|2` after the text only when the same text appears twice in the line.
- [REPEAT]: the same word again (never also a [LINK]). [NOTE]: a line whose pronunciation you cannot decide; leave it in a group of its own.
- Everything inside ( ... ) is not sung text: never a rhyme word, never a line ending. Report no word from a [tag]. Write no replacement lyrics and no scheme letters (the program names the groups A, B, C). Leave a tag out when you have nothing for it.
""").strip()


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
    scheme_unverified: bool = False               # part of the scheme rests on the AI's assessment of a language the program cannot read
    scheme_pattern: str = ""                      # the pattern of the confirmed end rhymes for display: ABCC, AABA ... (a line with no confirmed end rhyme is a letter of its own); "" for fewer than two lines
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
            rows.append(f"{tag}L{ln.file_line} | {ln.text}")
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


def _endpoint(d: dict, f2s: "dict | None" = None) -> "Endpoint | None":
    """The sung line number is derived from the file line when the answer leaves it out (the short format)."""
    if not isinstance(d, dict):
        return None
    text = d.get("text")
    fl, sl = _int(d.get("file_line")), _int(d.get("sung_line"))
    if sl is None and fl is not None and f2s:
        sl = f2s.get(fl)
    if not isinstance(text, str) or not text.strip() or sl is None:
        return None
    occ = _int(d.get("occurrence"))
    return Endpoint(fl if fl is not None else -1, sl, text, _int(d.get("start")), _int(d.get("end")), occ if occ and occ > 0 else None,
                    str(d.get("role") or "").lower(), str(d.get("language") or "").lower())


def _clip(x, n: int) -> str:
    return str(x if x is not None else "").replace("\n", " ").strip()[:n]


def _ep(d, f2s):
    """(file_line, text, occurrence, role) of a word reference, or None when a required field is missing or has the wrong type. A format check only."""
    if not isinstance(d, dict):
        return None
    fl, text = _int(d.get("file_line")), d.get("text")
    if fl is None and _int(d.get("sung_line")) is not None and f2s:
        fl = next((f for f, n in f2s.items() if n == d.get("sung_line")), None)
    if fl is None or not isinstance(text, str) or not text.strip():
        return None
    occ = _int(d.get("occurrence"))
    return {"file_line": fl, "text": text.strip(), "occurrence": occ if occ and occ > 0 else None, "role": _clip(d.get("role"), 12).lower()}


def llm_data(raw: str, f2s: "dict | None" = None) -> dict:
    """What the Rhyme Analyzer said, as plain data: a FORMAT check only (the JSON parses, the required fields are there, the types are right). Sound, relation, groups, letters and scheme are taken
    exactly as the AI gave them: nothing here judges a rhyme, moves a pair to another group or works a scheme out. `notes` lists what was left out because its format was wrong.
    SchemaError when the answer is not the schema at all."""
    notes: list = []
    if CG.is_rhyme(raw):
        raw, extra = CG.rhyme_as_json(raw)
        notes.extend(extra)
    try:
        data = json.loads(_extract_json(raw))
    except json.JSONDecodeError as ex:
        data = _salvage(raw)
        if data is None:
            raise SchemaError(f"The AI answer is not valid JSON ({ex.msg}).") from None
        notes.append(f"The AI answer was cut off or malformed ({ex.msg}); only the complete pairs were read, the rest is missing.")
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise SchemaError(f"The AI answer is not {SCHEMA}.")
    pairs, seen = [], set()
    dropped = 0
    for i, d in enumerate(data.get("pairs") or [], 1):
        a, b = (_ep(d.get("a"), f2s), _ep(d.get("b"), f2s)) if isinstance(d, dict) else (None, None)
        if a is None or b is None:
            notes.append(f"Pair {i} was left out: a word, its line or its text is missing or has the wrong type.")
            dropped += 1
            continue
        stype = _clip(d.get("sound_type"), 24).lower()
        if stype not in SOUND_TYPES:                                                              # the sound type is the AI's to name: a missing or unknown one is a format error, never filled in here
            notes.append(f"Pair {i} was left out: its sound_type is {'missing' if not stype else 'not one this program reads'}.")
            dropped += 1
            continue
        rel = _clip(d.get("relation"), 12).lower()
        conf = _clip(d.get("confidence"), 12).lower()
        key = frozenset({(a["file_line"], a["text"], a["occurrence"]), (b["file_line"], b["text"], b["occurrence"])})
        if key in seen:
            continue
        seen.add(key)
        pairs.append({"group_id": _clip(d.get("group_id"), 20), "sound_type": stype, "relation": rel if rel in RELATIONS else "uncertain",
                      "a": a, "b": b, "reason": _clip(d.get("reason"), 160), "confidence": conf if conf in CONFIDENCE else "medium"})
    secs = []
    for sd in data.get("sections") or []:
        if not isinstance(sd, dict):
            continue
        ls = sd.get("line_schemes") if isinstance(sd.get("line_schemes"), dict) else {}          # an older answer (rhyme-v8 and before) wrote letters per line
        lines = {}
        for k, v in ls.items():
            n = _int(int(k)) if str(k).isdigit() else None
            lab = re.sub(r"[^A-Za-z~-]", "", str(v or ""))[:3]
            if n is not None and lab:
                lines[str(n)] = lab
        groups, conflicts, seen_in, gsounds = [], [], {}, []
        eg = sd.get("end_groups")
        eg_items = list(eg.items()) if isinstance(eg, dict) else [("", g) for g in (eg or [])]      # an object {end sound: [line ids]} or a list of lists of line ids ("L25", 25, "25")
        for gkey, g in eg_items:
            if not isinstance(g, list):
                notes.append("An end group was left out: it is not a list of line ids.")
                continue
            one = []
            for x in g:
                m = re.fullmatch(r"\s*[Ll]?\s*(\d{1,5})\s*", str(x)) if isinstance(x, (str, int)) and not isinstance(x, bool) else None
                if m is None:
                    notes.append(f"A line id of an end group was left out: {str(x)[:12]!r} is not a line id.")
                    continue
                n = int(m.group(1))
                if n in seen_in:
                    conflicts.append(n)                                                           # a line in two groups: the first group keeps it, the conflict is counted
                    continue
                seen_in[n] = len(groups)
                one.append(n)
            if one:
                groups.append(one)
                gsounds.append(_clip(gkey, 16) if isinstance(gkey, str) else "")
        obs = [{"line": o["line"], "kind": "uncertain", "text": _clip(o.get("text"), 200)} for o in (sd.get("observations") or [])[:8]
               if isinstance(o, dict) and _int(o.get("line")) is not None and o.get("text") and str(o.get("kind")) == "uncertain"]
        secs.append({"section_id": _clip(sd.get("section_id"), 12), "scheme": re.sub(r"[^A-Za-z~-]", "", str(sd.get("scheme") or ""))[:64], "scheme_type": _clip(sd.get("scheme_type"), 16),
                     "line_schemes": lines, "end_groups": groups, "group_sounds": gsounds, "group_conflicts": conflicts, "observations": obs})
    ends = {}
    for k, v in (data.get("endings") or {}).items() if isinstance(data.get("endings"), dict) else []:
        if str(k).isdigit() and isinstance(v, str) and v.strip():
            ends[str(int(k))] = _clip(v, 40)
    sounds = {}
    for k, v in (data.get("sounds") or {}).items() if isinstance(data.get("sounds"), dict) else []:
        if str(k).isdigit() and isinstance(v, str) and v.strip():
            sounds[str(int(k))] = _clip(v, 16)
    for sd_ in secs:                                              # the sound each group was filed under is the line's end sound (shown in a tooltip only; a sound the AI wrote for a line elsewhere is kept)
        for gk, g in zip(sd_["group_sounds"], sd_["end_groups"]):
            for n in g:
                if gk:
                    sounds.setdefault(str(n), gk)
    reps = []
    for d in data.get("repeats") or []:
        a, b = (_ep(d.get("a"), f2s), _ep(d.get("b"), f2s)) if isinstance(d, dict) else (None, None)
        if a is not None and b is not None:
            reps.append({"a": a, "b": b})
    return {"pairs": pairs, "sections": secs, "endings": ends, "sounds": sounds, "repeats": reps, "languages": [str(x).lower()[:6] for x in (data.get("languages") or []) if isinstance(x, str)], "notes": notes, "dropped_pairs": dropped}


def _salvage(raw: str) -> "dict | None":
    """The complete objects of the `pairs` array of an answer whose JSON is broken or cut off. None when there is not even one complete pair (then the answer is not usable)."""
    text = re.sub(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$", "", raw or "")
    k = text.find('"pairs"')
    if k < 0 or SCHEMA not in text:
        return None
    i = text.find("[", k)
    if i < 0:
        return None
    dec, pairs = json.JSONDecoder(), []
    i += 1
    while i < len(text):
        while i < len(text) and text[i] in " \t\r\n,":
            i += 1
        if i >= len(text) or text[i] != "{":
            break
        try:
            obj, i = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            break
        if isinstance(obj, dict):
            pairs.append(obj)
    out = {"schema": SCHEMA, "pairs": pairs, "sections": [], "languages": []}
    for key in ("sections", "endings", "sounds", "repeats"):                    # the other parts that arrived whole (the contract puts sections and endings BEFORE the pairs, so a cut answer usually still has them)
        j = text.find(f'"{key}"')
        if j < 0:
            continue
        j = text.find(":", j)
        if j < 0:
            continue
        j += 1
        while j < len(text) and text[j].isspace():
            j += 1
        try:
            val, _end = dec.raw_decode(text, j)
        except json.JSONDecodeError:
            continue
        if isinstance(val, (list, dict)):
            out[key] = val
    return out if (pairs or out["sections"] or out.get("endings")) else None


def parse(raw: str, f2s: "dict | None" = None) -> tuple:
    """(pairs, sections dict list, overview dict, languages, ignored messages). SchemaError when the answer is not a rhyme-map-v6 object. One bad pair is dropped with a message."""
    ignored = []
    if CG.is_rhyme(raw):
        raw, extra = CG.rhyme_as_json(raw)
        ignored.extend(extra)
    try:
        data = json.loads(_extract_json(raw))
    except json.JSONDecodeError as ex:
        data = _salvage(raw)                                      # a long answer that was cut off or has one broken place: the pairs that are complete are still real pairs
        if data is None:
            raise SchemaError(f"The AI answer is not valid JSON ({ex.msg}).") from None
        ignored.append(f"The AI answer was cut off or malformed ({ex.msg}); {len(data.get('pairs') or [])} complete pair(s) were recovered, the rest is missing.")
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise SchemaError(f"The AI answer is not {SCHEMA}.")
    pairs, seen = [], set()
    for i, d in enumerate(data.get("pairs") or [], 1):
        if not isinstance(d, dict):
            ignored.append(f"Pair {i} is not an object.")
            continue
        a, b = _endpoint(d.get("a"), f2s), _endpoint(d.get("b"), f2s)
        if a is None or b is None:
            ignored.append(f"Pair {d.get('pair_id') or i}: a word or its line is missing.")
            continue
        sound = str(d.get("sound_type") or "").lower()                      # a missing sound type is not filled in: it fails the check below
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


def _pattern(s: str) -> str:
    """ABCC from '--CC': the letters in order of first appearance, a '-' line being a letter of its own. 'Free' when no two lines share a confirmed end sound, and for a long section only the first 8."""
    if not s.replace("-", ""):
        return "Free"
    fresh, letters = iter("ZYXWVUTSRQPONMLKJIHGFEDCBA"), []
    for c in s:
        letters.append(c if c != "-" else next(fresh))
    seen = {}
    norm = "".join(seen.setdefault(c, chr(ord("A") + len(seen) % 26)) for c in letters)
    return norm if len(norm) <= 8 else norm[:8] + "…"


def derive_schemes(sections: list, links: list) -> None:
    """Fill `scheme`, `scheme_name`, `near_lines` and `counts` of every SectionInfo from the validated pairs. Only END-TO-END exact pairs make the scheme (an internal rhyme never changes it); a line
    that is linked only by a near rhyme is listed apart and stays '-' in the exact scheme."""
    by_line = {}
    for sec in sections:
        for n in sec.sung_lines:
            by_line[n] = sec
    parents: dict = {}
    near_only: dict = {}
    ai_used: set = set()

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
        thai = TR.has_thai(k.a_word) or TR.has_thai(k.b_word)
        # The scheme is worked out from what the program CONFIRMED. A Thai pair that only the AI called a rhyme (the program could not read it) is not used; a pair in a language the program cannot read
        # is used as the AI's assessment and the section is marked so (`scheme_unverified`).
        usable = k.verification == "verified" or (k.verification == "ai_assessed" and not thai)
        if k.sound == "exact" and usable and k.kind == "vowel":
            parents[find(pa)] = find(pb)
            if k.verification != "verified":
                ai_used.add(by_line[pa].index)
        elif k.sound == "near" and usable:
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
        sec.scheme_unverified = sec.index in ai_used
        sec.scheme_name = scheme_name(sec.scheme) if len(sec.sung_lines) >= 2 else ""
        sec.scheme_pattern = _pattern(sec.scheme) if len(sec.sung_lines) >= 2 else ""
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
