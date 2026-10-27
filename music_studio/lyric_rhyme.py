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

RHYME_PROMPT_VERSION = "rhyme-v3"
KIND = "rhyme"                                  # the analysis type that is part of the cache key
SEP_RHYMES = "=========== RHYMES ==========="
WINDOW_LINES = 60                               # lines per request: the answer is only a short list of pairs, so a whole song normally goes in ONE request
WINDOW_OVERLAP = 15                             # a longer song is sent in overlapping windows (global line numbers), so a pair that crosses a window edge is still seen
MAX_PAIRS_PER_REQUEST = 30
TYPES = ("end", "cross", "internal", "near")
CONFIDENCE = ("high", "medium", "low", "uncertain")
TYPE_TITLES = {"end": "End Rhyme", "cross": "Cross-Line Rhyme", "internal": "Internal Rhyme", "near": "Near Rhyme"}
_RH_LANG = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?(?:\+[a-z]{2,3}(?:-[A-Za-z]{2,4})?)*$")

SYSTEM_PROMPT = (
    "You find RHYMES in song lyrics for a songwriting tool. You judge only by how the words are PRONOUNCED when sung, never by spelling. "
    "You never rewrite, translate, correct or comment on the lyrics.\n\n"
    "INPUT: numbered lines written as `N| text`. Section tags are already removed. Decide the language of every word yourself; a line may mix languages.\n"
    "Not every line rhymes. Report ONLY pairs you are confident about. An answer with no pairs is correct when there are none. Never invent a rhyme to fill the answer.\n\n"
    "TYPES (first column of a row)\n"
    "- end: the LAST word of one line rhymes exactly with the LAST word of a DIFFERENT line.\n"
    "- cross: the LAST word of one line rhymes exactly with a word INSIDE the immediately FOLLOWING line (not its last word).\n"
    "- internal: two words INSIDE THE SAME line rhyme exactly with each other (both line numbers are the same).\n"
    "- near: the sounds are close but not an exact rhyme (same vowel with a different final consonant, similar vowels, ...). Use near whenever the rhyme is not exact, "
    "whatever the position of the words. Never call a near rhyme end or internal.\n\n"
    "SOUND RULES\n"
    "- Thai: compare the spoken vowel, including its length, and the final consonant. Ignore tone. "
    "Quote the whole syllable, never a piece of one (ดาว, not าว; บ้าน, not าน).\n"
    "  Rhyme: ดาว and หนาว (same long -aaw). ใจ and ไป (same -ai).\n"
    "  Not an exact rhyme: บ้าน and ฝัน (long อา versus short อะ, same final น). Call that near, not end.\n"
    "  Not a rhyme at all: กาน and กิน (different vowels). The same final consonant is not enough.\n"
    "- English: compare the stressed vowel and everything after it as spoken, not the letters (love and above rhyme; love and move do not).\n"
    "- Japanese: compare the vowel sounds of the final mora(s); a mora count is not a rhyme.\n"
    "- Other languages: use that language's own pronunciation. Words of different languages rhyme only when they really sound alike.\n"
    "- Repeating the very same word is not a rhyme.\n"
    "- A pair is valid ONLY if the two words SHARE a sound. end / cross / internal: the same final vowel AND the same final consonant sound (or both end in the vowel). "
    "near: the same vowel with a similar final consonant, or the same final consonant with a similar vowel. "
    "Words whose vowels differ AND whose final consonants differ do not rhyme at all, not even as near: leave them out.\n"
    "- Check every row before you write it: the reason must name the sound the two words SHARE. If you can only describe how they DIFFER, delete the row.\n\n"
    "OUTPUT FORMAT: exactly these two marker lines around the rows, one row per pair, at most " + str(MAX_PAIRS_PER_REQUEST) + " rows, strongest pairs first:\n"
    + SEP_RHYMES + "\n"
    "type | line:word | line:word | confidence | lang | reason\n"
    + SEP_END + "\n\n"
    "- line is the number N of the input line; word is copied EXACTLY as written in that line (one word, or a short phrase).\n"
    "- confidence is one of: high, medium, low, uncertain. Use uncertain when you are not sure; if you doubt a pair, leave it out.\n"
    "- lang is the ISO 639-1 code of the words (th, en, ja, ...); write th+en when the two words are in different languages.\n"
    "- reason is at most 12 words naming the shared sound. Do not use the character | inside it.\n"
    "- If nothing rhymes write the two marker lines with nothing between them.\n\n"
    "EXAMPLE\n"
    "Input:\n1| คืนนี้ดาวเต็มฟ้า\n2| ฉันคิดถึงเธอเสมอมา\n3| The night is bright, hold on tight\n4| Hold me in your time\n5| Tell me you are mine\n6| ฉันเดินออกไปคนเดียว\n"
    "Output:\n" + SEP_RHYMES + "\n"
    "end | 1:ฟ้า | 2:มา | high | th | both end in the vowel -aa\n"
    "internal | 3:night | 3:bright | high | en | same vowel and final sound -ight\n"
    "near | 4:time | 5:mine | medium | en | same vowel, final m versus n\n" + SEP_END + "\n\n"
    "EXAMPLE 2 (cross-line exact rhyme: line 1 ends with ดาว, line 2 has หนาว inside)\n"
    "Input:\n1| ฉันมองดาว\n2| เธอยังหนาวอยู่ไหม\n"
    "Output:\n" + SEP_RHYMES + "\n"
    "cross | 1:ดาว | 2:หนาว | high | th | same vowel and final sound -aao\n" + SEP_END + "\n\n"
    "EXAMPLE 3 (nothing rhymes, so the answer is empty)\n"
    "Input:\n1| ฉันกินข้าว\n2| เขานั่งอยู่ริมทะเล\n3| The door is closed\n4| Birds fly over the sea\n"
    "Output:\n" + SEP_RHYMES + "\n" + SEP_END + "\n\n"
    "EXAMPLE 4 (do not report these)\n"
    "กาน with กิน is not a rhyme. บ้าน with ฝัน is near, never end or cross or internal."
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


def parse_window(raw: str, sent: list) -> tuple:
    """(pairs, ignored messages) for one answer, or AnalysisError. An answer with the two markers and nothing between them means 'no rhymes found' (a valid result);
    an empty answer, missing / repeated markers or a row that is not in the format is an error. Nothing is made up."""
    text = re.sub(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$", "", raw or "").strip()
    if not text:
        raise AnalysisError("The AI answer is empty.", raw)
    rows = text.splitlines()
    a = [i for i, r in enumerate(rows) if _is_sep(r, SEP_RHYMES)]
    z = [i for i, r in enumerate(rows) if _is_sep(r, SEP_END)]
    if len(a) != 1 or len(z) != 1 or not a[0] < z[0]:
        raise AnalysisError("The AI answer does not have the RHYMES ... END marker lines exactly once, in order.", raw)
    body = [r.strip() for r in rows[a[0] + 1:z[0]] if r.strip()]
    if len(body) == 1 and body[0].strip(" .").lower() in ("none", "no rhymes", "-"):
        body = []
    if len(body) > MAX_PAIRS_PER_REQUEST * 2:
        raise AnalysisError(f"The AI returned {len(body)} rows, far more than the {MAX_PAIRS_PER_REQUEST} asked for. The answer is not trusted.", raw)
    by_num = {ln.num: ln for ln in sent}
    pairs, rejected, ignored, seen = [], [], [], set()
    for row in body:
        try:
            pair, why = _check_row(row, by_num)
        except AnalysisError as ex:
            ex.raw = raw
            raise
        if pair is None:
            ignored.append(why)
            continue
        classify_sound(pair, by_num)
        if pair.key in seen:
            ignored.append(f"{pair.a_word} ↔ {pair.b_word} was listed twice.")
            continue
        if pair.verify == TR.REJECTED:
            rejected.append(pair)
            continue
        seen.add(pair.key)
        rejected[:] = [p for p in rejected if p.key != pair.key]
        pairs.append(pair)
    return pairs, ignored, rejected


def analyze_rhymes(lines: list, ask: Callable[[str, str], str], size: int = WINDOW_LINES, overlap: int = WINDOW_OVERLAP,
                   progress: Callable[[int, int], None] | None = None) -> RhymeResult:
    """Blocking (call from an executor). `ask(system, user) -> raw answer` is the existing AI backend. Any window that fails stops everything: the result is complete or it does not exist."""
    if not lines:
        raise AnalysisError("There are no sung lines to analyze (only [tags] or empty).")
    spans = windows(len(lines), size, overlap)
    result = RhymeResult([ln.text for ln in lines], [])
    seen = set()
    for k, (s, e) in enumerate(spans, 1):
        if progress:
            progress(k, len(spans))
        system, user = build_request(lines[s:e])
        raw = ask(system, user)
        result.raw.append(raw)
        try:
            pairs, ignored, rejected = parse_window(raw, lines[s:e])
        except AnalysisError as ex:
            ex.raw = raw
            raise
        for p in pairs:
            if p.key not in seen:                # a pair inside the overlap is reported by both windows: keep one
                seen.add(p.key)
                result.pairs.append(p)
        for p in rejected:
            if p.key not in seen and p.key not in {q.key for q in result.rejected}:
                result.rejected.append(p)
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
            classify_sound(checked, numbered)
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
