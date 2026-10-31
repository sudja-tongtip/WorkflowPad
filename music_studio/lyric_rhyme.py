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

from . import thai_rhyme as TR
from .lyric_analysis import AnalysisError, SEP_END, _is_sep, _norm

RHYME_PROMPT_VERSION = "rhyme-v5"
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
    verify: str = TR.UNVERIFIED                    # Python's sound check. Separate from `confidence`, which is only what the model said.
    verify_note: str = ""

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

    def of_type(self, t: str) -> list:
        return [p for p in self.pairs if p.type == t]


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


def _pair_from(t: str, a_num: int, a_word: str, b_num: int, b_word: str, reason: str, sure: bool, by_num: dict, ignored: list, near: bool = False) -> "RhymePair | None":
    """One pair the AI described. Python checks only that both words are really where the pair says (_check_row). Whether they rhyme is the AI's assessment: `verify` records it as LLM-assessed
    exact, LLM-assessed near, or uncertain. It is never "verified": nothing independent stands behind it. The position (`type`: end / cross) is a separate fact from the sound match."""
    lang = "th" if re.search(r"[\u0E00-\u0E7F]", a_word + b_word) else "en"
    try:
        pair, why = _check_row(f"{t} | {a_num}:{a_word} | {b_num}:{b_word} | {'medium' if sure else 'low'} | {lang} | {reason}", by_num)
    except AnalysisError as ex:
        ignored.append(str(ex))
        return None
    if pair is None:
        ignored.append(why)
        return None
    pair.verify, pair.verify_note = (LLM_NEAR if near else (LLM_EXACT if sure else UNCERTAIN)), ""
    return pair


def endings_to_pairs(ends: list, groups: list, by_num: dict, ignored: list) -> list:
    """The AI's groups as RhymePairs: neighbours inside a group are paired one after another (a chain), the inner words become cross-line pairs."""
    pairs, seen = [], set()

    def add(p):
        if p is not None and p.key not in seen:
            seen.add(p.key)
            pairs.append(p)
    for members in groups:
        for x, y in zip(members, members[1:]):
            reason = " · ".join(s for s in (f"{x.sound} / {y.sound}" if (x.sound or y.sound) else "", y.note) if s)
            add(_pair_from("end", x.num, x.word, y.num, y.word, reason.replace("|", "/"), not (x.unsure or y.unsure), by_num, ignored, near=(x.near or y.near)))
    ends_by = {e.num: e for e in ends}
    for e in ends:
        prev = ends_by.get(e.num - 1)
        if e.inner and prev is not None:
            add(_pair_from("cross", prev.num, prev.word, e.num, e.inner, e.note.replace("|", "/"), not e.unsure, by_num, ignored))
    return pairs


def analyze_rhymes(lines: list, ask: Callable[[str, str], str], size: int = WINDOW_LINES, overlap: int = WINDOW_OVERLAP,
                   progress: Callable[[int, int], None] | None = None) -> RhymeResult:
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
        for p in endings_to_pairs(ends, group_endings(ends), {ln.num: ln for ln in window}, ignored):
            if p.key in seen:
                continue
            seen.add(p.key)
            (result.rejected if p.verify == TR.REJECTED else result.pairs).append(p)       # a pair whose Thai sounds do not match is kept apart, never shown as a rhyme
        result.ignored.extend(i for i in ignored if i not in result.ignored)
    result.pairs.sort(key=lambda p: (min(p.a_line, p.b_line), max(p.a_line, p.b_line), p.type))
    return result


# ── saving the last good result ──────────────────────────────────────────────────────────────────────────────
def _pair_dict(p: RhymePair) -> dict:
    return {"type": p.type, "a_line": p.a_line, "a_word": p.a_word, "b_line": p.b_line, "b_word": p.b_word,
            "confidence": p.confidence, "lang": p.lang, "reason": p.reason, "verify": p.verify, "verify_note": p.verify_note}


def rhyme_to_dict(snap, result: RhymeResult, at: str) -> dict:
    return {"text_hash": snap.text_hash, "backend": snap.backend, "model": snap.model, "params": snap.params, "prompt_version": snap.prompt_version, "at": at,
            "lines": list(result.lines), "ignored": list(result.ignored),
            "pairs": [_pair_dict(p) for p in result.pairs],
            "rejected": [_pair_dict(p) for p in result.rejected]}


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


def rhyme_from_dict(d) -> "tuple | None":
    """(info dict, RhymeResult) or None. A broken file is dropped. One bad pair is dropped; the rest is kept.
    Thai sounds are checked again on load, including saves from before `verify` existed."""
    try:
        lines = [str(x) for x in d["lines"]]
        if not lines:
            return None
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
            if str(d.get("prompt_version", "")) >= "rhyme-v5":        # a result of the LLM-first flow keeps what the AI assessed: nothing is re-judged by Python
                checked.verify, checked.verify_note = str(x.get("verify", UNCERTAIN)), str(x.get("verify_note", ""))
            else:
                classify_sound(checked, numbered)                    # an older result: the Thai sound check is made again on load, as it always was
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
