"""Lyrics Analysis, Phase 3: rhymes. The model proposes pairs from pronunciation. Python checks the lines, the word
spans, and — for Thai — the vowel and final consonant.

Thai sound is a separate field from the model's confidence. `verify` is verified, unverified, or rejected.
Rejected pairs are kept apart from the pairs that are shown as rhymes. A Thai span that is only a piece of a
longer syllable is dropped. When the last syllable cannot be read, the pair stays unverified: it is not called
right or wrong. Nothing here touches the editor, the syllable result, or the manual corrections.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from . import rhyme_map as RM
from . import rhyme_v6 as RV
from . import thai_rhyme as TR
from .lyric_analysis import AnalysisError, SEP_END, _is_sep, _norm

RHYME_PROMPT_VERSION = "rhyme-v6"                  # the Rhyme Analyzer AI (rhyme-map-v6). rhyme-v5 (line endings) results can still be read and are shown as an older analysis
LEGACY_VERSION = "rhyme-v5"
LLM_EXACT, LLM_NEAR, UNCERTAIN = "llm_exact", "llm_near", "uncertain"        # what the AI (the Thai Prosody Expert) assessed: no Python sound check stands behind these
KIND = "rhyme"                                  # the analysis type that is part of the cache key
WINDOW_LINES = 120                              # lines per request: the answer is one short row per line, so a whole song normally goes in ONE request
WINDOW_OVERLAP = 0                              # a longer song is cut into windows; a rhyme across a window edge is not seen
TYPES = ("end", "cross", "internal", "near")
CONFIDENCE = ("high", "medium", "low", "uncertain")
TYPE_TITLES = {"end": "End Rhyme", "cross": "Cross-Line Rhyme", "internal": "Internal Rhyme", "near": "Near Rhyme"}
_RH_LANG = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?(?:\+[a-z]{2,3}(?:-[A-Za-z]{2,4})?)*$")

SEP_ENDINGS = "=========== ENDINGS ==========="

SYSTEM_PROMPT = (
    "You are the rhyme ear of a songwriting tool. You judge only by how the words SOUND when sung, never by spelling. "
    "You never rewrite, translate, correct or comment on the lyrics.\n\n"
    "INPUT: numbered lines written as `N| text`. Section tags are already removed. Decide the language of every line yourself; a line may mix languages.\n\n"
    "TASK: for EVERY line find its LAST WORD, say how its last syllable sounds, and say which lines END in the same sound.\n\n"
    "OUTPUT FORMAT: exactly these marker lines and rows, one ENDINGS row for every input line, in the same order:\n"
    + SEP_ENDINGS + "\n"
    "N | last word | sound | group | inner | note\n"
    + SEP_END + "\n\n"
    "- last word: copied EXACTLY as written. For Thai give only the LAST SYLLABLE of the last word (ต่อไป -> ไป, ใจดำ -> ดำ, หัวใจ -> ใจ, เหงาใจ -> ใจ); for other languages the last word.\n"
    "- sound: the vowel and the final consonant of the last syllable in simple Latin letters, tone ignored (for example -aao, -ai, -am, -uea, -ok).\n"
    "- group: a capital letter. Lines whose last syllables share EXACTLY the same vowel and final sound get the SAME letter; every different sound gets its own new letter. "
    "Write - when no other line ends in that sound. When the rhyme is only close and not exact (the same vowel with a similar final consonant, or the reverse) add ~ after the letter, for example A~. "
    "When you are not sure how a word is pronounced, add ? after the letter, for example A?: an honest ? is better than a wrong rhyme. "
    "Repeating the very same word is not a rhyme: write -.\n"
    "- inner: a syllable or word INSIDE this line (not its last word) that rhymes with the last syllable of the line right above it, copied exactly; otherwise -.\n"
    "- note: at most 8 words, or -. Never use the character | inside a field.\n"
    "- Not every line rhymes. Never invent a rhyme to fill the answer.\n\n"
    "SOUND RULES: Thai: compare the vowel INCLUDING its length and the final consonant, ignore tone (ดาว and หนาว rhyme; ใจ and ไป rhyme; บ้าน and ฝัน do not rhyme exactly: the vowel length differs, so near at most; "
    "กาน and กิน do not rhyme). English: the stressed vowel and everything after it as spoken. Japanese: the vowel of the final mora. Other languages: their own pronunciation.\n\n"
    "EXAMPLE\nInput:\n1| ฉันมองดาวบนฟ้า\n2| เธอยังหนาวอยู่ไหม\n3| ฉันคิดถึงเธอเสมอมา\n4| ฝันถึงเธอทุกคืน\n"
    "Output:\n" + SEP_ENDINGS + "\n"
    "1 | ฟ้า | -aa | A | - | -\n"
    "2 | ไหม | -ai | - | - | -\n"
    "3 | มา | -aa | A | - | -\n"
    "4 | คืน | -uen | - | - | -\n" + SEP_END
)


@dataclass
class RhymePair:
    type: str                                   # end | cross | internal | near
    a_line: int                                 # numbers of the SUNG lines (the same numbers the syllable analysis uses)
    a_word: str
    b_line: int
    b_word: str
    confidence: str
    lang: str
    reason: str = ""
    verify: str = TR.UNVERIFIED                    # the single status the window shows: verified / unverified / rejected / llm_exact / llm_near / uncertain (kept for the old saves and the cards)
    verify_note: str = ""
    # The Shared Rhyme Map's three separate facts (rhyme_map.py). `type` and `verify` above are views of them.
    sound: str = ""                                # exact | near | different | uncertain
    placement: str = ""                            # internal | end | cross
    verification: str = ""                         # verified | ai_assessed | unverified | rejected
    source: str = "llm"                            # llm | python
    kind: str = "vowel"                            # vowel | consonant
    claimed: str = ""                              # what the AI said about the sound: exact | near | uncertain
    # rhyme-map-v6: the AI's id / group / sound type, the finer placement, and the position that Python validated (0-based, end exclusive, in the sung line)
    pair_id: str = ""
    group_id: str = ""
    sound_type: str = ""                           # vowel_rhyme | consonant_rhyme | assonance | consonance | alliteration | multisyllabic | wordplay
    placement_detail: str = ""                     # internal | end_to_end | end_to_internal | cross_line | cross_section (derived from the validated positions)
    traditional_role: str = ""                     # สัมผัสใน | สัมผัสนอก | ""
    a_start: int = -1
    a_end: int = -1
    b_start: int = -1
    b_end: int = -1
    position_validated: bool = False               # Python placed both words exactly. This says NOTHING about the sound: that is `verification`
    position_fixed: bool = False                   # the AI's index was wrong and Python corrected it (the text was at exactly one place)
    position_note: str = ""

    @property
    def pos_key(self) -> frozenset:                # the two exact positions: two pairs on the same two spans are one pair
        return frozenset({(self.a_line, self.a_start, self.a_end, self.a_word), (self.b_line, self.b_start, self.b_end, self.b_word)})

    @property
    def key(self) -> frozenset:                 # an unordered pair of (line, word): the same two words are one pair, whatever their order or label
        return frozenset({(self.a_line, _norm(self.a_word)), (self.b_line, _norm(self.b_word))})


@dataclass
class RhymeResult:
    lines: list                                 # the text of every sung line when this was analyzed (index 0 = line 1), so an outdated result still quotes the right lines
    pairs: list                                 # verified and unverified only. A rejected pair is not shown as a rhyme.
    ignored: list = field(default_factory=list)  # one message per row that was dropped because Python could check it and it was wrong
    rejected: list = field(default_factory=list)  # pairs whose Thai sounds were read and do not match the claimed type
    raw: list = field(default_factory=list)     # raw model answers, kept for the log
    sections: list = field(default_factory=list)  # rhyme-map-v6: one dict per section (see rhyme_v6.SectionInfo): the scheme Python derived, the AI's own, its summary, observations and counts
    overview: dict = field(default_factory=dict)  # rhyme-map-v6: {"text": ..., "points": [{"line": n, "text": ...}]}
    languages: list = field(default_factory=list)
    schema: str = ""                              # "rhyme-map-v6" for a result of the Rhyme Analyzer AI, "" for the older line-endings result

    def of_type(self, t: str) -> list:
        return [p for p in self.pairs if p.type == t]


_VERIFY_VIEW = {"verified": TR.VERIFIED, "rejected": TR.REJECTED, "unverified": TR.UNVERIFIED}


def _placement_of(p: "RhymePair") -> str:
    if p.type in RM.PLACEMENTS:
        return p.type
    return "internal" if p.a_line == p.b_line else "end"


def finish_pair(p: "RhymePair", claim: str, declared: str = "") -> None:
    """Set the sound, the placement and the verification of a pair the AI proposed, by the rules of the words' language (rhyme_map.judge): a Thai pair whose sounds Python can read is verified,
    reclassified (exact <-> near) or rejected with the reason; any other pair keeps what the AI said as ai_assessed (or unverified when the language has no check). `verify` is the old single
    view of these facts."""
    rel = RM.relation(p.a_word, p.b_word, declared)
    sound, verification, note = RM.judge(claim, rel)
    p.claimed = claim if claim in ("exact", "near") else "uncertain"
    p.sound, p.verification, p.kind, p.source = sound, verification, rel.kind, "llm"
    p.placement = _placement_of(p)
    p.verify_note = note
    if verification == "ai_assessed":
        p.verify = LLM_NEAR if sound == "near" else LLM_EXACT
    elif verification == "unverified":
        p.verify = UNCERTAIN if sound == "uncertain" else TR.UNVERIFIED
    else:
        p.verify = _VERIFY_VIEW[verification]


def build_request(lines: list) -> tuple:
    """(system prompt, user message) for one window of SungLine. Line numbers are GLOBAL, so the pairs of every window use the same numbering."""
    return SYSTEM_PROMPT, "\n".join(f"{ln.num}| {ln.text}" for ln in lines)


def windows(n: int, size: int = WINDOW_LINES, overlap: int = WINDOW_OVERLAP) -> list:
    """[(start, end)) index ranges. One window when the song fits; otherwise overlapping windows so a pair that crosses an edge is seen whole."""
    if n <= size:
        return [(0, n)]
    step = max(1, size - overlap)
    out, s = [], 0
    while True:
        e = min(n, s + size)
        out.append((s, e))
        if e >= n:
            return out
        s += step


def _place(line: str, word: str) -> dict:
    return TR.placement(re.sub(r"[\s.,!?:;…\"'”’]+$", "", line or ""), word)


def _thai_slot(line: str, word: str, at_end: bool) -> str:
    """ok / uncertain / fragment / missing for a Thai word. Non-Thai returns ok when the old letter check passes, else missing."""
    if not TR.has_thai(word):
        nline, nword = _norm(line), _norm(word)
        if at_end:
            return "ok" if nword and nline.endswith(nword) else "missing"
        return "ok" if nword and nword in nline else "missing"
    found = _place(line, word)
    return found["end"] if at_end else found["best"]


def _position_problem(pair: RhymePair, by_num: dict) -> str:
    """'' when the quoted words sit where this rhyme type requires. A Thai fragment is never accepted."""
    specs = []
    if pair.type == "end":
        specs = [(pair.a_line, pair.a_word, True), (pair.b_line, pair.b_word, True)]
    elif pair.type == "cross":
        earlier, later = ((pair.a_line, pair.a_word), (pair.b_line, pair.b_word)) if pair.a_line < pair.b_line else ((pair.b_line, pair.b_word), (pair.a_line, pair.a_word))
        specs = [(earlier[0], earlier[1], True), (later[0], later[1], False)]
        later_place = _place(by_num[later[0]].text, later[1]) if TR.has_thai(later[1]) else None
        if later_place is not None and later_place["other"] not in ("ok", "uncertain"):
            if later_place["end"] in ("ok", "uncertain") and later_place["other"] == "missing":
                return f"{pair.a_word} ↔ {pair.b_word}: the following line must contain its rhyme word before the end."
            if later_place["best"] == "fragment":
                return f"“{later[1]}” is only part of a longer syllable in line {later[0]}, so the pair was ignored."
            return f"“{later[1]}” is not in line {later[0]}, so the pair was ignored."
    else:
        specs = [(pair.a_line, pair.a_word, False), (pair.b_line, pair.b_word, False)]
    for line_no, word, at_end in specs:
        status = _thai_slot(by_num[line_no].text, word, at_end)
        if status == "fragment":
            return f"“{word}” is only part of a longer syllable in line {line_no}, so the pair was ignored."
        if status == "missing":
            if at_end:
                return f"{pair.a_word} ↔ {pair.b_word}: a word is not at the end of its line, so it is not an end rhyme and was ignored."
            return f"“{word}” is not in line {line_no}, so the pair {pair.a_word} ↔ {pair.b_word} was ignored."
        if status == "uncertain":
            pair.verify = TR.UNVERIFIED
            pair.verify_note = "The syllable boundary was not clear, so Python did not confirm the sound."
    return ""


def _exact_kind(pair: RhymePair, by_num: dict) -> str:
    """end, cross, or internal when both words clearly sit in that position. Empty when they do not."""
    la, wa, lb, wb = pair.a_line, pair.a_word, pair.b_line, pair.b_word
    ta, tb = by_num[la].text, by_num[lb].text

    def end_ok(line: str, word: str) -> bool:
        return _place(line, word)["end"] == "ok"

    def other_ok(line: str, word: str) -> bool:
        return _place(line, word)["other"] == "ok"

    def best_ok(line: str, word: str) -> bool:
        return _place(line, word)["best"] == "ok"

    if la == lb and _norm(wa) != _norm(wb) and best_ok(ta, wa) and best_ok(tb, wb):
        return "internal"
    if la != lb and end_ok(ta, wa) and end_ok(tb, wb):
        return "end"
    if abs(la - lb) == 1:
        earlier, later = ((la, wa), (lb, wb)) if la < lb else ((lb, wb), (la, wa))
        if end_ok(by_num[earlier[0]].text, earlier[1]) and other_ok(by_num[later[0]].text, later[1]):
            return "cross"
    return ""


def classify_sound(pair: RhymePair, by_num: dict) -> None:
    """Set verify from the Thai spelling. A boundary that was already unclear stays unverified.
    A near report whose sounds actually match is moved to end, cross, or internal when the words sit there.
    It is never confirmed as a near rhyme."""
    if pair.verify_note.startswith("The syllable boundary"):
        pair.verify = TR.UNVERIFIED
        return
    unsure = False
    for line_no, word in ((pair.a_line, pair.a_word), (pair.b_line, pair.b_word)):
        if TR.has_thai(word) and _place(by_num[line_no].text, word)["best"] == "uncertain":
            unsure = True
    if unsure:
        pair.verify = TR.UNVERIFIED
        pair.verify_note = "The syllable boundary was not clear, so Python did not confirm the sound."
        return
    if pair.type == "near":
        rel, detail = TR.sound_relation(pair.a_word, pair.b_word)
        if rel == "exact":
            kind = _exact_kind(pair, by_num)
            if kind:
                pair.type = kind
                pair.verify = TR.VERIFIED
                shown = {"end": "an end rhyme", "cross": "a cross-line rhyme", "internal": "an internal rhyme"}[kind]
                pair.verify_note = f"The model called this a near rhyme, but the sounds match exactly, so it is shown as {shown}. Sound check: {detail}"
                return
            pair.verify = TR.UNVERIFIED
            pair.verify_note = (f"The sounds match exactly ({detail}), but these words do not sit as an end, cross-line, or internal rhyme, "
                                "so this is not confirmed as a near rhyme.")
            return
    pair.verify, pair.verify_note = TR.verify_pair(pair.type, pair.a_word, pair.b_word)


def _end_position_is_cross(la: int, wa: str, lb: int, wb: str, by_num: dict) -> bool:
    """True when one word ends its line and the other sits inside the next line, before that line's last word."""
    if abs(la - lb) != 1:
        return False
    earlier, later = ((la, wa), (lb, wb)) if la < lb else ((lb, wb), (la, wa))
    earlier_text, later_text = _norm(by_num[earlier[0]].text), _norm(by_num[later[0]].text)
    earlier_word, later_word = _norm(earlier[1]), _norm(later[1])
    if not earlier_word or not later_word or not earlier_text.endswith(earlier_word) or later_word not in later_text:
        return False
    return later_text.find(later_word) < len(later_text) - len(later_word)


def _check_row(row: str, by_num: dict) -> tuple:
    """(RhymePair | None, ignore message | ''). Raises AnalysisError for a row that is not in the format at all (a broken schema is never guessed at)."""
    parts = [p.strip() for p in row.split("|", 5)]
    if len(parts) < 5:
        raise AnalysisError(f"A rhyme row is not in the format `type | line:word | line:word | confidence | lang | reason`: “{row[:60]}”.")
    t, a, b, conf, lang = parts[0].lower(), parts[1], parts[2], parts[3].lower(), parts[4].lower()
    reason = parts[5] if len(parts) > 5 else ""
    if t not in TYPES:
        raise AnalysisError(f"A rhyme row has an unknown type “{parts[0][:20]}” (allowed: end, cross, internal, near).")
    if conf not in CONFIDENCE:
        raise AnalysisError(f"A rhyme row has an unknown confidence “{parts[3][:20]}” (allowed: high, medium, low, uncertain).")
    if not _RH_LANG.match(lang):
        raise AnalysisError(f"A rhyme row has a language code that cannot be read: “{parts[4][:20]}”.")
    ends = []
    for x in (a, b):
        num, colon, word = x.partition(":")
        num, word = num.strip(), word.strip()
        if not colon or not num.isdigit() or not word:
            raise AnalysisError(f"A rhyme row has a part that is not `line:word`: “{x[:40]}”.")
        if int(num) not in by_num:
            raise AnalysisError(f"A rhyme row points to line {num}, which is not in the lyrics that were sent.")
        ends.append((int(num), word))
    (la, wa), (lb, wb) = ends
    pair = RhymePair(t, la, wa, lb, wb, conf, lang, reason.replace("\n", " ").strip())
    for line_no, word in ends:
        if "[" in word or "]" in word:
            return None, f"“{word}” (line {line_no}) looks like a section tag."
        n, text = _norm(word), _norm(by_num[line_no].text)
        if not n or n not in text:
            return None, f"“{word}” is not in line {line_no}, so the pair {wa} ↔ {wb} was ignored."
    if la == lb and _norm(wa) == _norm(wb) and _norm(by_num[la].text).count(_norm(wa)) < 2:
        return None, f"“{wa}” was paired with itself in line {la}."
    if la != lb and _norm(wa) == _norm(wb):
        return None, f"“{wa}” ↔ “{wb}” is the same word repeated, not a rhyme."
    if t == "end":
        if la == lb:
            return None, f"{wa} ↔ {wb}: an end rhyme needs two different lines (line {la}), so it was ignored."
        if not (_norm(by_num[la].text).endswith(_norm(wa)) and _norm(by_num[lb].text).endswith(_norm(wb))):
            # The model often labels a real cross rhyme as end. Keep it when the position is last-word -> middle of the next line.
            # The sound check still runs, so a wrong sound is rejected rather than shown as a rhyme.
            if _end_position_is_cross(la, wa, lb, wb, by_num):
                pair = RhymePair("cross", la, wa, lb, wb, conf, lang, pair.reason)
                t = "cross"
            else:
                return None, f"{wa} ↔ {wb}: a word is not at the end of its line, so it is not an end rhyme and was ignored."
    if t == "cross":
        # Only a final word -> non-final word in the immediately following line.
        # Position is checked structurally, not phonetic correctness (the LLM judges sound).
        if abs(la - lb) != 1:
            return None, f"{wa} ↔ {wb}: a cross-line rhyme needs consecutive lines."
        earlier, later = ((la, wa), (lb, wb)) if la < lb else ((lb, wb), (la, wa))
        earlier_text = _norm(by_num[earlier[0]].text)
        later_text = _norm(by_num[later[0]].text)
        earlier_word, later_word = _norm(earlier[1]), _norm(later[1])
        if not earlier_text.endswith(earlier_word):
            return None, f"{wa} ↔ {wb}: the earlier line must END with its rhyme word."
        # There must be at least one non-final occurrence; repeated words may also
        # occur at the end, but the earlier occurrence still qualifies as internal.
        if later_text.find(later_word) >= len(later_text) - len(later_word):
            return None, f"{wa} ↔ {wb}: the following line must contain its rhyme word before the end."
    if t == "internal" and la != lb:
        return None, f"{wa} ↔ {wb}: an internal rhyme must be inside one line, so it was ignored."
    why = _position_problem(pair, by_num)
    if why:
        return None, why
    return pair, ""


@dataclass
class Ending:
    num: int                                    # number of the SUNG line
    word: str                                   # its last word, as the AI quoted it (checked to sit at the end of the line)
    sound: str = ""                             # the AI's own description of the sound ("-ai"): shown, never judged
    group: str = ""                             # same letter = the AI says these lines end in the same sound ('' = no rhyme partner)
    near: bool = False                          # the AI marked the rhyme as close but not exact (A~)
    unsure: bool = False                        # the AI said it is not sure of the pronunciation (A?)
    inner: str = ""                             # a word inside the line that rhymes with the last word of the line above
    note: str = ""


def parse_endings(raw: str, sent: list) -> tuple:
    """(endings, [], ignored messages) for one answer, or AnalysisError when the markers or every row are missing. A row that cannot be used is dropped with a message:
    the answer is never guessed at, and nothing here judges the sounds (that is the AI's job)."""
    text = re.sub(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$", "", raw or "").strip()
    if not text:
        raise AnalysisError("The AI answer is empty.", raw)
    rows = text.splitlines()
    a = [i for i, r in enumerate(rows) if _is_sep(r, SEP_ENDINGS)]
    k = [i for i, r in enumerate(rows) if _is_sep(r, "=========== ASK ===========")]          # an old-style ASK part is tolerated and ignored
    z = [i for i, r in enumerate(rows) if _is_sep(r, SEP_END)]
    if len(a) != 1 or len(z) != 1 or len(k) > 1 or not a[0] < z[0] or (k and not a[0] < k[0] < z[0]):
        raise AnalysisError("The AI answer does not have the ENDINGS ... END marker lines exactly once, in order.", raw)
    mid = k[0] if k else z[0]
    by_num = {ln.num: ln for ln in sent}
    ends, ignored, seen = [], [], set()
    for row in (r.strip() for r in rows[a[0] + 1:mid] if r.strip()):
        parts = [p.strip() for p in row.split("|")]
        if len(parts) < 4 or not parts[0].isdigit() or int(parts[0]) not in by_num:
            ignored.append(f"A row of the answer was not usable: “{row[:50]}”.")
            continue
        num, word = int(parts[0]), parts[1]
        if num in seen:
            continue
        line = _norm(by_num[num].text)
        if not _norm(word) or not line.endswith(_norm(word)):
            ignored.append(f"“{word}” is not at the end of line {num}, so that row was ignored.")
            continue
        seen.add(num)
        group = parts[3].replace(" ", "")
        near, unsure = "~" in group, "?" in group
        letter = group.replace("~", "").replace("?", "")
        letter = letter if re.fullmatch(r"[A-Za-z]{1,2}", letter) else ""
        inner = parts[4] if len(parts) > 4 and parts[4] not in ("", "-", "—") and _norm(parts[4]) in line[:max(0, len(line) - len(_norm(word)))] else ""
        ends.append(Ending(num, word, parts[2] if parts[2] != "-" else "", letter.upper(), near and bool(letter), unsure and bool(letter), inner,
                           parts[5] if len(parts) > 5 and parts[5] not in ("", "-", "—") else ""))
    if not ends:
        raise AnalysisError("The AI answer has no readable ending rows.", raw)
    return ends, [], ignored


REACH = 4                                       # lines that the AI says end in the same sound are one group when they are at most this many sung lines apart


def group_endings(ends: list) -> list:
    """The groups the AI describes: lines with the same group letter, and lines it describes with the same sound label ("-ai") that are close to each other. A model often labels the sound of a
    line right and forgets its group letter, so both are used. Nothing here listens to a sound: it only compares what the AI wrote. A group of one line is not a group."""
    parent = {e.num: e.num for e in ends}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    by_letter, by_sound = {}, {}
    for e in sorted(ends, key=lambda e: e.num):
        if e.group:
            if e.group in by_letter:
                parent[find(e.num)] = find(by_letter[e.group])
            by_letter[e.group] = e.num
        key = re.sub(r"[^a-z]", "", e.sound.lower())
        if key and key in by_sound and e.num - by_sound[key] <= REACH:
            parent[find(e.num)] = find(by_sound[key])
        if key:
            by_sound[key] = e.num
    members = {}
    for e in ends:
        members.setdefault(find(e.num), []).append(e)
    return [sorted(g, key=lambda e: e.num) for g in sorted(members.values(), key=lambda g: min(e.num for e in g)) if len(g) > 1]


def _pair_from(t: str, a_num: int, a_word: str, b_num: int, b_word: str, reason: str, sure: bool, by_num: dict, ignored: list, near: bool = False, declared: str = "") -> "RhymePair | None":
    """One pair the AI described. Python first checks that both words are really where the pair says (_check_row). Then the sound: a Thai pair Python can read is verified, reclassified or
    rejected; any other pair stays the AI's assessment (ai_assessed: nothing independent stands behind it) or unverified. The position (`type`: end / cross) is a separate fact."""
    lang = "th" if re.search(r"[\u0E00-\u0E7F]", a_word + b_word) else "en"
    try:
        pair, why = _check_row(f"{t} | {a_num}:{a_word} | {b_num}:{b_word} | {'medium' if sure else 'low'} | {lang} | {reason}", by_num)
    except AnalysisError as ex:
        ignored.append(str(ex))
        return None
    if pair is None:
        ignored.append(why)
        return None
    finish_pair(pair, "near" if near else ("exact" if sure else "uncertain"), declared)
    if reason and pair.verification in ("ai_assessed", "unverified"):
        pair.reason = reason
    return pair


def _related(x: int, y: int, by_num: dict) -> bool:
    """Lines of the same section, of sections of the same kind, or neighbouring lines. Two lines of unrelated sections are not linked only because their endings sound alike."""
    lx, ly = by_num.get(x), by_num.get(y)
    if lx is None or ly is None:
        return True
    return RM.related(getattr(lx, "section", -1), getattr(lx, "section_name", ""), x, getattr(ly, "section", -1), getattr(ly, "section_name", ""), y)


def endings_to_pairs(ends: list, groups: list, by_num: dict, ignored: list, declared: str = "") -> list:
    """The AI's groups as RhymePairs. Inside a group each line is linked to the NEAREST earlier line it may rhyme with (a related section, a different word) whose sound Python does not refuse; a
    refused pair is kept apart as rejected, with the reason. The inner words become cross-line pairs."""
    pairs, seen = [], set()

    def add(p):
        if p is not None and p.key not in seen:
            seen.add(p.key)
            pairs.append(p)
    for members in groups:
        for idx, y in enumerate(members):
            blocked, linked = False, False
            for x in reversed(members[:idx]):
                if _norm(x.word) == _norm(y.word):
                    continue                                                  # the same word again is repetition, not a rhyme
                if not _related(x.num, y.num, by_num):
                    blocked = True
                    continue
                reason = " · ".join(s for s in (f"{x.sound} / {y.sound}" if (x.sound or y.sound) else "", y.note) if s)
                p = _pair_from("end", x.num, x.word, y.num, y.word, reason.replace("|", "/"), not (x.unsure or y.unsure), by_num, ignored, near=(x.near or y.near), declared=declared)
                add(p)
                if p is not None and p.verification != "rejected":
                    linked = True
                    break
            if blocked and not linked:
                ignored.append(f"{y.word}: line {y.num} is in a section that is not related to the lines it sounds like, so it was not linked.")
    ends_by = {e.num: e for e in ends}
    for e in ends:
        prev = ends_by.get(e.num - 1)
        if e.inner and prev is not None:
            add(_pair_from("cross", prev.num, prev.word, e.num, e.inner, e.note.replace("|", "/"), not e.unsure, by_num, ignored, declared=declared))
    return pairs


def analyze_rhymes_v5(lines: list, ask: Callable[[str, str], str], size: int = WINDOW_LINES, overlap: int = WINDOW_OVERLAP,
                      progress: Callable[[int, int], None] | None = None, language: str = "") -> RhymeResult:
    """Blocking (call from an executor). `ask(system, user) -> raw answer` is the existing AI backend. The AI answers one row per line (last word, sound, group) in ONE request per window and decides
    every rhyme itself: Python only checks that the quoted words are really at those places. The result is complete or it does not exist."""
    if not lines:
        raise AnalysisError("There are no sung lines to analyze (only [tags] or empty).")
    spans = windows(len(lines), size, overlap)
    result = RhymeResult([ln.text for ln in lines], [])
    seen = set()
    for k, (s, e) in enumerate(spans, 1):
        if progress:
            progress(k, len(spans))
        window = lines[s:e]
        system, user = build_request(window)
        raw = ask(system, user)
        result.raw.append(raw)
        try:
            ends, _asks, ignored = parse_endings(raw, window)
        except AnalysisError as ex:
            ex.raw = raw
            raise
        for p in endings_to_pairs(ends, group_endings(ends), {ln.num: ln for ln in window}, ignored, language):
            if p.key in seen:
                continue
            seen.add(p.key)
            (result.rejected if p.verify == TR.REJECTED else result.pairs).append(p)       # a pair whose Thai sounds do not match is kept apart, never shown as a rhyme
        result.ignored.extend(i for i in ignored if i not in result.ignored)
    result.pairs.sort(key=lambda p: (min(p.a_line, p.b_line), max(p.a_line, p.b_line), p.type))
    return result


# ── Rhyme Analyzer AI: rhyme-map-v6 ────────────────────────────────────────────────────────────────────────────
_SOUND_KIND = {"vowel_rhyme": "vowel", "assonance": "vowel", "multisyllabic": "vowel", "consonant_rhyme": "consonant", "consonance": "consonant", "alliteration": "consonant", "wordplay": "consonant"}
_TYPE_OF = {"internal": "internal", "end_to_end": "end", "end_to_internal": "cross", "cross_line": "cross"}


def system_prompt() -> str:
    """The system prompt that is really sent: the user's editable rhyme_analyzer.md plus the program's OUTPUT FORMAT. AnalysisError when the prompt file is not valid (nothing is sent)."""
    from . import specialists as SP
    try:
        return SP.effective_system("rhyme_analyzer", RV.CONTRACT)[0]
    except ValueError as ex:
        raise AnalysisError(str(ex)) from None


def prompt_hash() -> str:
    from . import specialists as SP
    try:
        return SP.load("rhyme_analyzer").hash
    except Exception:
        return ""


def _consonant_facts(p: "RhymePair", p6, declared: str) -> None:
    """A consonant rhyme / alliteration / consonance / wordplay: not a vowel rhyme, so it is never coloured as one. Thai pairs are checked by their first consonant sound."""
    both_thai = TR.has_thai(p.a_word) and TR.has_thai(p.b_word)
    p.kind, p.source, p.claimed = "consonant", "llm", p6.relation
    p.sound = p6.relation
    if both_thai and p6.sound_type in ("consonant_rhyme", "alliteration"):
        ia, ib = RM.thai_initial(p.a_word), RM.thai_initial(p.b_word)
        if ia is not None and ib is not None:
            p.verification = "verified" if ia == ib else "rejected"
            p.sound = "exact" if ia == ib else "different"
            p.verify_note = "The first consonant sounds are the same." if ia == ib else "The first consonant sounds differ, so this is not a consonant rhyme."
            p.verify = _VERIFY_VIEW[p.verification]
            return
    p.verification = "ai_assessed" if RM.language_of(p.a_word, declared) != RM.OTHER else "unverified"
    p.verify = LLM_NEAR if p.sound == "near" else (LLM_EXACT if p.verification == "ai_assessed" and p.sound == "exact" else UNCERTAIN)
    p.verify_note = "The AI's assessment of the consonant sounds: nothing independent verified it."


def _v6_pair(p6, by_num: dict, declared: str, ignored: list) -> "RhymePair | None":
    la, lb = by_num.get(p6.a.sung_line), by_num.get(p6.b.sung_line)
    label = f"{p6.a.text} ↔ {p6.b.text}"
    if la is None or lb is None:
        ignored.append(f"{label}: a line of this pair is not in the lyrics that were sent.")
        return None
    mla = RM.Line(la.file_line, 0, la.text, la.num, la.section, la.section_name)
    mlb = RM.Line(lb.file_line, 0, lb.text, lb.num, lb.section, lb.section_name)
    for ep, ln, ml in ((p6.a, la, mla), (p6.b, lb, mlb)):
        why = RV.validate_endpoint(ep, ml, "", declared)
        if why:
            ignored.append(f"{label}: {why}")
            return None
    a, b = p6.a, p6.b
    if (a.sung_line, a.start_v, a.end_v) == (b.sung_line, b.start_v, b.end_v):
        ignored.append(f"{label}: both words are the same word at the same place.")
        return None
    if _norm(a.text) == _norm(b.text):
        ignored.append(f"{label}: the same word repeated is repetition, not a rhyme.")
        return None
    if (a.sung_line, a.start_v) > (b.sung_line, b.start_v):                    # always in text order
        p6.a, p6.b, a, b, la, lb, mla, mlb = b, a, b, a, lb, la, mlb, mla
    detail = RV.derive_placement(a, b, la, lb, mla, mlb)
    both_end = RV._is_end(a, mla) and RV._is_end(b, mlb)
    t = "end" if detail == "cross_section" and both_end else ("cross" if detail == "cross_section" else _TYPE_OF[detail])
    langs = []
    for code in (a.language, b.language):
        code = code if _RH_LANG.match(code or "") and code != "other" else "und"
        if code not in langs:
            langs.append(code)
    p = RhymePair(t, a.sung_line, a.text, b.sung_line, b.text, p6.confidence, "+".join(langs), p6.reason)
    p.pair_id, p.group_id, p.sound_type, p.placement_detail, p.traditional_role = p6.pair_id, p6.group_id, p6.sound_type, detail, p6.traditional_role
    p.a_start, p.a_end, p.b_start, p.b_end = a.start_v, a.end_v, b.start_v, b.end_v
    p.position_validated, p.position_fixed = True, bool(a.fixed or b.fixed)
    p.position_note = "; ".join(x.note for x in (a, b) if x.note)
    if _SOUND_KIND.get(p6.sound_type, "vowel") == "vowel":
        finish_pair(p, p6.relation, declared)
    else:
        _consonant_facts(p, p6, declared)
    p.placement = _placement_of(p)
    p.pair_id, p.group_id, p.sound_type, p.placement_detail = p6.pair_id, p6.group_id, p6.sound_type, detail
    if not RM.related(la.section, la.section_name, la.num, lb.section, lb.section_name, lb.num):
        p.verification, p.verify, p.verify_note = "rejected", TR.REJECTED, "These lines are in sections that are not related, so the sounds alone do not link them."
    return p


def _clip(x, n: int) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip()[:n]


def _section_infos(lines: list) -> list:
    out: dict = {}
    for ln in lines:
        sec = out.setdefault(ln.section, RV.SectionInfo(ln.section, ln.section_name or "", []))
        sec.sung_lines.append(ln.num)
    return [out[k] for k in sorted(out)]


def analyze_rhymes(lines: list, ask: Callable[[str, str], str], size: int = RV.WINDOW_MAX, overlap: int = RV.CONTEXT_LINES,
                   progress: Callable[[int, int], None] | None = None, language: str = "", system: "str | None" = None) -> RhymeResult:
    """Blocking (call from an executor). The Rhyme Analyzer AI (`ask(system, user) -> raw`, stateless: no session, no history) is asked once per window of at most `size` sung lines (whole sections
    together; `overlap` lines of context before each next window). Python parses the answer strictly, places every word on the lyrics (rhyme_v6.validate_endpoint), derives each pair's placement
    and each section's scheme, re-checks the sounds it can read, drops duplicates across windows, and keeps what it could not place, with the reason. A window that is not rhyme-map-v6 at all
    raises: the result is complete or it does not exist."""
    if not lines:
        raise AnalysisError("There are no sung lines to analyze (only [tags] or empty).")
    system = system if system is not None else system_prompt()
    wins = RV.windows(lines, size, overlap)
    result = RhymeResult([ln.text for ln in lines], [], schema=RV.SCHEMA)
    by_num_all = {ln.num: ln for ln in lines}
    secs = _section_infos(lines)
    ai_sec: dict = {}
    per_window, ov_text, ov_points, langs = [], [], [], []
    for k, (window, ctx) in enumerate(wins, 1):
        if progress:
            progress(k, len(wins))
        raw = ask(system, RV.build_request(window, ctx, language))
        result.raw.append(raw)
        try:
            pairs6, sec6, ov, found, ignored = RV.parse(raw)
        except RV.SchemaError as ex:
            raise AnalysisError(str(ex), raw) from None
        real = {ln.num for ln in window}
        mine = []
        for p6 in pairs6:
            if p6.a.sung_line not in real and p6.b.sung_line not in real:
                continue                                                          # both words lie in the context: that pair belongs to the previous window
            p = _v6_pair(p6, by_num_all, language, ignored)
            if p is not None:
                mine.append(p)
        per_window.append(mine)
        for sd in sec6:
            ai_sec.setdefault(str(sd.get("section_id") or ""), sd)
        if ov.get("text"):
            ov_text.append(_clip(ov["text"], 1200))
        for pt in (ov.get("points") or [])[:12]:
            if isinstance(pt, dict) and isinstance(pt.get("line"), int) and pt.get("text"):
                ov_points.append({"line": pt["line"], "text": _clip(pt["text"], 300)})
        langs += [x for x in found if x not in langs]
        result.ignored.extend(i for i in ignored if i not in result.ignored)
    for p in RV.merge_window_pairs(per_window):
        (result.rejected if p.verification == "rejected" else result.pairs).append(p)
    result.pairs.sort(key=lambda p: (p.a_line, p.a_start, p.b_line, p.b_start))
    for sec in secs:
        sd = ai_sec.get(RV.section_id(sec.index)) or {}
        ls = sd.get("line_schemes") if isinstance(sd.get("line_schemes"), dict) else {}
        sec.ai_scheme = re.sub(r"[^A-Za-z~-]", "", str(sd.get("scheme") or "")) or "".join(str(ls.get(str(n), "-"))[:2] for n in sec.sung_lines if ls)
        sec.summary = _clip(sd.get("summary"), 600)
        for ob in (sd.get("observations") or [])[:8]:
            if isinstance(ob, dict) and isinstance(ob.get("line"), int) and ob.get("text"):
                (sec.uncertain if str(ob.get("kind")) == "uncertain" else sec.quality).append((ob["line"], _clip(ob["text"], 300)))
    RV.derive_schemes(secs, result.pairs)
    from dataclasses import asdict
    result.sections = [dict(asdict(sec), id=RV.section_id(sec.index)) for sec in secs]
    result.overview = {"text": " ".join(ov_text), "points": ov_points[:24]}
    result.languages = langs
    return result


def rhyme_map_dict(lyrics: str, result: RhymeResult, meta: "dict | None" = None) -> dict:
    """The rhyme map of `lyrics` as the schema of the brief (rhyme-map-v6): sections with their schemes, and every pair with its validated local AND absolute positions. `meta` (song_id,
    sheet_id, lyrics_hash, prompt_version, model_identity ...) is copied in. Pairs that cannot be placed on these lyrics are left out."""
    from .lyric_analysis import original_lines, sung_lines
    sung = sung_lines(lyrics)
    by_file = {n: (start, content) for n, start, _end, content in original_lines(lyrics or "")}
    lines = {}
    for ln in sung:
        f = by_file.get(ln.file_line)
        if f is not None:
            lines[ln.num] = RM.Line(ln.file_line, f[0], f[1], ln.num, ln.section, ln.section_name)

    def side(num, word, a, b):
        L = lines.get(num)
        if L is None:
            return None
        got, _why = RM._local_span(L, word, a, b)
        if got is None:
            return None
        return {"file_line": L.file_line, "sung_line": num, "text": lyrics[L.start + got[0]:L.start + got[1]], "start": a, "end": b,
                "absolute_start": L.start + got[0], "absolute_end": L.start + got[1]}
    pairs = []
    for p in result.pairs:
        if not p.position_validated:
            continue
        sa, sb = side(p.a_line, p.a_word, p.a_start, p.a_end), side(p.b_line, p.b_word, p.b_start, p.b_end)
        if sa is None or sb is None:
            continue
        pairs.append({"pair_id": p.pair_id, "group_id": p.group_id, "sound_type": p.sound_type, "relation": p.sound, "placement": p.placement_detail,
                      "traditional_role": p.traditional_role, "a": sa, "b": sb, "reason": p.reason, "confidence": p.confidence,
                      "phonetic_verification": p.verification, "position_validated": p.position_validated, "position_fixed": p.position_fixed})
    out = {"schema": RV.SCHEMA, "index_unit": "unicode_codepoint", "end_exclusive": True, "languages": list(result.languages),
           "sections": [{"section": x.get("name") or "", "section_id": x.get("id"), "scheme": x.get("scheme"), "scheme_name": x.get("scheme_name"),
                         "line_schemes": {str(n): c for n, c in zip(x.get("sung_lines", []), x.get("scheme", ""))}} for x in result.sections],
           "pairs": pairs, "overview": result.overview}
    out.update(meta or {})
    return out


# ── saving the last good result ──────────────────────────────────────────────────────────────────────────────
def _pair_dict(p: RhymePair) -> dict:
    return {"type": p.type, "a_line": p.a_line, "a_word": p.a_word, "b_line": p.b_line, "b_word": p.b_word,
            "confidence": p.confidence, "lang": p.lang, "reason": p.reason, "verify": p.verify, "verify_note": p.verify_note,
            "sound": p.sound, "placement": p.placement, "verification": p.verification, "source": p.source, "kind": p.kind, "claimed": p.claimed,
            "pair_id": p.pair_id, "group_id": p.group_id, "sound_type": p.sound_type, "placement_detail": p.placement_detail, "traditional_role": p.traditional_role,
            "a_start": p.a_start, "a_end": p.a_end, "b_start": p.b_start, "b_end": p.b_end, "position_validated": p.position_validated,
            "position_fixed": p.position_fixed, "position_note": p.position_note}


def rhyme_to_dict(snap, result: RhymeResult, at: str) -> dict:
    return {"text_hash": snap.text_hash, "backend": snap.backend, "model": snap.model, "params": snap.params, "prompt_version": snap.prompt_version, "at": at,
            "analyzer_version": RM.ANALYZER_VERSION, "schema": result.schema, "lines": list(result.lines), "ignored": list(result.ignored),
            "sections": list(result.sections), "overview": dict(result.overview), "languages": list(result.languages),
            "pairs": [_pair_dict(p) for p in result.pairs],
            "rejected": [_pair_dict(p) for p in result.rejected]}


def finish_old(p: "RhymePair") -> None:
    """The Shared Rhyme Map fields of a pair that the older flow (classify_sound) already judged: its verify is the source of the three facts."""
    p.placement = _placement_of(p)
    p.source = "python" if p.verify in (TR.VERIFIED, TR.REJECTED) else "llm"
    p.verification = {TR.VERIFIED: "verified", TR.REJECTED: "rejected", LLM_EXACT: "ai_assessed", LLM_NEAR: "ai_assessed"}.get(p.verify, "unverified")
    p.sound = {"near": "near"}.get(p.type, "exact") if p.verification in ("verified", "ai_assessed") else ("different" if p.verification == "rejected" else "uncertain")
    p.claimed = p.claimed or ("near" if p.type == "near" else "exact")


def _pair_from_dict(x, n_lines: int) -> "RhymePair | None":
    try:
        p = RhymePair(str(x["type"]), int(x["a_line"]), str(x["a_word"]), int(x["b_line"]), str(x["b_word"]),
                      str(x["confidence"]), str(x["lang"]), str(x.get("reason", "")))
    except Exception:
        return None
    if (p.type not in TYPES or p.confidence not in CONFIDENCE or not _RH_LANG.match(p.lang) or not p.a_word or not p.b_word
            or not (1 <= p.a_line <= n_lines and 1 <= p.b_line <= n_lines)):
        return None
    return p


def _v6_from_dict(d: dict, lines: list) -> "tuple | None":
    """A rhyme-map-v6 result: each pair keeps its validated position (re-checked here against the saved lines) and its sound is judged again by the rules of the words' language."""
    pairs, rejected, seen = [], [], set()
    ignored = [str(i) for i in d.get("ignored", [])]
    from .lyric_analysis import SungLine
    for x in list(d.get("pairs") or []) + list(d.get("rejected") or []):
        try:
            p = RhymePair(str(x["type"]), int(x["a_line"]), str(x["a_word"]), int(x["b_line"]), str(x["b_word"]), str(x["confidence"]), str(x["lang"]), str(x.get("reason", "")))
            p.pair_id, p.group_id, p.sound_type = str(x.get("pair_id", "")), str(x.get("group_id", "")), str(x.get("sound_type", ""))
            p.placement_detail, p.traditional_role = str(x.get("placement_detail", "")), str(x.get("traditional_role", ""))
            p.a_start, p.a_end, p.b_start, p.b_end = int(x["a_start"]), int(x["a_end"]), int(x["b_start"]), int(x["b_end"])
            p.position_validated, p.position_fixed, p.position_note = bool(x.get("position_validated")), bool(x.get("position_fixed")), str(x.get("position_note", ""))
        except Exception:
            continue
        ok = (1 <= p.a_line <= len(lines) and 1 <= p.b_line <= len(lines) and p.position_validated and p.confidence in CONFIDENCE
              and lines[p.a_line - 1][p.a_start:p.a_end] == p.a_word and lines[p.b_line - 1][p.b_start:p.b_end] == p.b_word)
        if not ok or p.pos_key in seen:
            continue
        seen.add(p.pos_key)
        claim = str(x.get("claimed") or ("near" if x.get("sound") == "near" else "exact"))
        if _SOUND_KIND.get(p.sound_type, "vowel") == "vowel":
            finish_pair(p, claim)
        else:
            _consonant_facts(p, type("P6", (), {"relation": claim, "sound_type": p.sound_type})(), "")
        p.placement = _placement_of(p)
        (rejected if p.verification == "rejected" else pairs).append(p)
    info = {k: str(d[k]) for k in ("text_hash", "backend", "model", "params", "prompt_version")}
    info["at"] = str(d.get("at", ""))
    res = RhymeResult(lines, pairs, ignored, rejected, [], [dict(s) for s in d.get("sections") or [] if isinstance(s, dict)],
                      dict(d.get("overview") or {}), [str(x) for x in d.get("languages") or []], str(d.get("schema") or RV.SCHEMA))
    return info, res


def rhyme_from_dict(d) -> "tuple | None":
    """(info dict, RhymeResult) or None. A broken file is dropped. One bad pair is dropped; the rest is kept.
    Thai sounds are checked again on load, including saves from before `verify` existed."""
    try:
        lines = [str(x) for x in d["lines"]]
        if not lines:
            return None
        if str(d.get("prompt_version", "")) >= RHYME_PROMPT_VERSION and d.get("schema") == RV.SCHEMA:
            return _v6_from_dict(d, lines)
        from .lyric_analysis import SungLine
        numbered = {i: SungLine(i, i, line) for i, line in enumerate(lines, 1)}
        pairs, rejected, seen = [], [], set()
        ignored = [str(i) for i in d.get("ignored", [])]
        for x in list(d.get("pairs") or []) + list(d.get("rejected") or []):
            p = _pair_from_dict(x, len(lines))
            if p is None or p.key in seen:
                continue
            checked, why = _check_row(
                f"{p.type} | {p.a_line}:{p.a_word} | {p.b_line}:{p.b_word} | {p.confidence} | {p.lang} | {p.reason}", numbered)
            if checked is None:
                if why and why not in ignored:
                    ignored.append(why)
                continue
            if str(d.get("prompt_version", "")) >= "rhyme-v5":        # a result of the LLM-first flow: what the AI said is the claim; the sound is checked again here by the rules of the words' language
                stored = str(x.get("claimed") or {"llm_exact": "exact", "llm_near": "near"}.get(str(x.get("verify", "")), ""))
                finish_pair(checked, stored if stored in ("exact", "near") else "uncertain")
            else:
                classify_sound(checked, numbered)                    # an older result: the Thai sound check is made again on load, as it always was
                finish_old(checked)
            seen.add(checked.key)
            if checked.verify == TR.REJECTED:
                rejected.append(checked)
            else:
                pairs.append(checked)
        info = {k: str(d[k]) for k in ("text_hash", "backend", "model", "params", "prompt_version")}
        info["at"] = str(d.get("at", ""))
        return info, RhymeResult(lines, pairs, ignored, rejected)
    except Exception:
        return None
