"""Lyrics Analysis, Phase 1: syllables, by asking the LLM to split every sung line into the units it is really sung with.

The LLM does the linguistic work (language, pronunciation, mora for Japanese); this module only builds the request, parses a strict line format, checks that the answer
belongs to the lyrics that were sent, and COUNTS the units itself. A count the LLM might write is never read. No dictionary / tokenizer is used.
Nothing here touches the editor: it takes a text and returns an Analysis, or raises AnalysisError (never a made-up result).
"""
from __future__ import annotations

import difflib
import hashlib
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

from .compiler import HOLD_PREFIX, TAG_LINE

PROMPT_VERSION = "syl-v2"
# Starting points, not a tuned optimum. Syllables and rhymes must follow a strict format. Rhythm is a judgement.
# Thesaurus has to offer several wordings. A set analysis_temperature_<kind> wins over the shared analysis_temperature.
# Neither one reads the chat temperature.
ANALYSIS_TEMP_DEFAULTS = {"syl": 0.2, "rhyme": 0.2, "rhythm": 0.4, "thes": 0.7}
BATCH_LINES = 25                                # lines per LLM request (keeps the answer well inside the model's token limit)
UNIT_SEPS = "·•"                                # the separator the prompt asks for (+ a bullet some models write instead)
_INLINE_TAG = re.compile(r"\[[^\[\]\n]*\]")
_LANG_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")
SEP_ANALYSIS = "=========== ANALYSIS ==========="
SEP_END = "=========== END ==========="

SYSTEM_PROMPT = (
    "You split song lyrics into SUNG SYLLABLES for a songwriting tool. You never rhyme, translate, correct, explain or comment.\n\n"
    "INPUT: numbered lines written as `N| text`. Section tags are already removed.\n"
    "For EVERY input line, in the same order, write ONE output line:\n"
    "N | <the input text copied exactly> | <lang>=<unit>·<unit>·<unit>\n"
    "If one line holds words of two languages, give one part per language, in the order the words appear, separated by ` ; `.\n\n"
    "RULES\n"
    "- <lang> is the ISO 639-1 code of the words in that part (th, en, ja, zh, ko, es, fr, de ...). Decide it yourself from the words.\n"
    "- A unit is ONE sung syllable written the way it is PRONOUNCED. Put every unit of the part in one flat list, separated only by the middle dot `·`.\n"
    "- Thai: write each syllable in Thai script as it is SPOKEN, one sung syllable each. This is the spoken syllable count, not the number of notes: you have not heard the melody.\n"
    "  Hidden vowels: สวัสดี -> สะ·วัด·ดี, ฤดู -> รึ·ดู, ทะเล -> ทะ·เล, สนุก -> สะ·หนุก, ตลาด -> ตะ·หลาด.\n"
    "  A leading ห or อ stays inside the syllable: หนาว -> หนาว, อยู่ -> อยู่, หญิง -> หญิง, หนู -> หนู. Do not split them.\n"
    "  A true cluster stays one syllable: กล้า -> กล้า, กลับ -> กลับ, เพราะ -> เพราะ. ความรัก -> ความ·รัก.\n"
    "  ๆ repeats the previous word: เด็กๆ -> เด็ก·เด็ก.\n"
    "  A loanword uses the usual spoken reading (คอมพิวเตอร์ -> คอม·พิว·เต้อ, or the reading you would actually sing). Do not invent extra syllables.\n"
    "- English and other alphabetic languages: split by sound, not by spelling rules (love -> love, beautiful -> beau·ti·ful, sunshine -> sun·shine). Numbers as spoken.\n"
    "- Japanese: one unit per MORA, written in kana (きょう -> きょ·う, がっこう -> が·っ·こ·う, ラーメン -> ラ·ー·メ·ン). Write kanji as their kana reading.\n"
    "- Chinese: one unit per character. Korean: one unit per Hangul syllable block.\n"
    "- Never skip, merge, add or reorder lines. Never write counts. Nothing outside the format.\n\n"
    "OUTPUT FORMAT, exactly these two marker lines around the result lines:\n"
    + SEP_ANALYSIS + "\n<one result line per input line>\n" + SEP_END + "\n\n"
    "EXAMPLE\n"
    "Input:\n1| ฝนตกหนักคืนนี้\n2| Hello darkness my old friend\n3| ありがとう\n4| รักเธอ forever\n"
    "Output:\n" + SEP_ANALYSIS + "\n"
    "1 | ฝนตกหนักคืนนี้ | th=ฝน·ตก·หนัก·คืน·นี้\n"
    "2 | Hello darkness my old friend | en=hel·lo·dark·ness·my·old·friend\n"
    "3 | ありがとう | ja=あ·り·が·と·う\n"
    "4 | รักเธอ forever | th=รัก·เธอ ; en=for·ev·er\n" + SEP_END
)


class AnalysisError(Exception):
    """The answer could not be used. `raw` keeps what the model wrote, for the log."""
    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw


@dataclass(frozen=True)
class SungLine:
    num: int                                    # 1.. among the sung lines (what the LLM sees)
    file_line: int                              # 1.. in the lyrics text (for showing / later features)
    text: str


@dataclass
class Segment:
    lang: str
    units: list

    @property
    def count(self) -> int:
        return len(self.units)


@dataclass
class LineResult:
    line: SungLine
    segments: list
    note: str = ""                              # e.g. the model copied the text slightly differently

    @property
    def count(self) -> int:                     # COUNTED HERE, from the units
        return sum(s.count for s in self.segments)

    @property
    def langs(self) -> list:
        out = []
        for s in self.segments:
            if s.lang not in out:
                out.append(s.lang)
        return out


@dataclass
class Analysis:
    lines: list
    raw: list = field(default_factory=list)     # raw model answers (one per request), kept for the log

    @property
    def total(self) -> int:
        return sum(r.count for r in self.lines)


@dataclass(frozen=True)
class Snapshot:
    """What an answer must still match to be shown as the current result."""
    song_id: str
    text: str
    epoch: int
    backend: str
    model: str                                  # the real identity: a local workflow's file name + content hash, or the Grok model name
    params: str = ""                            # request parameters that change the answer (temperature, max tokens)
    prompt_version: str = PROMPT_VERSION
    known_hash: str = ""                        # a result loaded from disk only knows the hash of the lyrics it was made for, not the text
    kind: str = "syl"                           # the analysis type ("syl" syllables, "rhyme"): part of the cache key, so two kinds never share an answer

    @property
    def text_hash(self) -> str:
        return self.known_hash or hashlib.sha1(self.text.encode("utf-8")).hexdigest()

    @property
    def cache_key(self) -> tuple:
        return (self.kind, self.text_hash, self.backend, self.model, self.params, self.prompt_version)   # not the song id: the same text gives the same answer


def analysis_temperature(cfg: dict, kind: "str | None" = None) -> "float | None":
    """Temperature for one lyrics analysis. `kind` None: the shared analysis override, or None when it is unset (the chat path is unchanged).
    A real kind never reads the chat temperature."""
    if not isinstance(cfg, dict):
        cfg = {}
    if kind not in ANALYSIS_TEMP_DEFAULTS:
        raw = cfg.get("analysis_temperature")
        if raw in (None, ""):
            return None
        try:
            return max(0.0, min(2.0, float(raw)))
        except (TypeError, ValueError):
            return None
    specific = cfg.get(f"analysis_temperature_{kind}")
    raw = specific if specific not in (None, "") else cfg.get("analysis_temperature")
    if raw in (None, ""):
        return ANALYSIS_TEMP_DEFAULTS[kind]
    try:
        return max(0.0, min(2.0, float(raw)))
    except (TypeError, ValueError):
        return ANALYSIS_TEMP_DEFAULTS[kind]


def text_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


class ResultCache:
    """Small in-memory cache: asking again for unchanged lyrics, backend and prompt does not call the LLM."""
    def __init__(self, size: int = 16):
        self.size, self._d = size, OrderedDict()

    def get(self, key):
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        return None

    def put(self, key, value) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.size:
            self._d.popitem(last=False)


# ── the lines that are sung ──────────────────────────────────────────────────────────────────────────────────
def sung_lines(text: str) -> list:
    """Sung lines in order. Skipped: blank lines, whole-line [tags] ([Verse 1], [Chorus - powerful]). Removed from a line: a leading [Hold] and any inline [tag]."""
    out, n = [], 0
    for i, raw in enumerate((text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        line = raw.strip()
        if not line or TAG_LINE.match(line):
            continue
        m = HOLD_PREFIX.match(line)
        if m:
            line = line[m.end():]
        line = re.sub(r"\s{2,}", " ", _INLINE_TAG.sub(" ", line)).strip()
        if line:
            n += 1
            out.append(SungLine(n, i, line))
    return out


def build_request(lines: list) -> tuple:
    """(system prompt, user message) for one batch of SungLine."""
    return SYSTEM_PROMPT, "\n".join(f"{ln.num}| {ln.text}" for ln in lines)


# ── the answer ───────────────────────────────────────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFC", s).casefold() if unicodedata.category(ch)[0] in "LMN")


def _is_sep(line: str, marker: str) -> bool:
    core = re.sub(r"[\W_]+", " ", marker).strip().lower()
    return re.sub(r"[\W_]+", " ", line).strip().lower() == core and bool(re.search(r"[^\w\s]|_", line))


def _split_units(s: str) -> list:
    return [u.strip() for u in re.split("[" + UNIT_SEPS + "]", s) if u.strip()]


def parse_batch(raw: str, expected: list) -> list:
    """LineResult for each expected SungLine, or AnalysisError. Strict about structure (markers, one result per line, same numbers and order) because a shifted answer would
    put counts on the wrong lines; the counts themselves are taken from the units, never from the model."""
    text = re.sub(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$", "", raw or "").strip()
    if not text:
        raise AnalysisError("The AI answer is empty.", raw)
    rows = text.splitlines()
    a = [i for i, r in enumerate(rows) if _is_sep(r, SEP_ANALYSIS)]
    z = [i for i, r in enumerate(rows) if _is_sep(r, SEP_END)]
    if len(a) != 1 or len(z) != 1 or not a[0] < z[0]:
        raise AnalysisError("The AI answer does not have the ANALYSIS ... END marker lines exactly once, in order.", raw)
    body = [r.strip() for r in rows[a[0] + 1:z[0]] if r.strip()]
    if len(body) != len(expected):
        raise AnalysisError(f"The AI returned {len(body)} result line(s) for {len(expected)} sung line(s). Nothing was shown.", raw)
    out = []
    for ln, row in zip(expected, body):
        parts = row.split("|")
        if len(parts) < 3:
            raise AnalysisError(f"Line {ln.num}: the answer row is not in the format `N | text | lang=units`.", raw)
        try:
            num = int(parts[0].strip().rstrip("."))
        except ValueError:
            raise AnalysisError(f"Line {ln.num}: the answer row does not start with its number.", raw)
        if num != ln.num:
            raise AnalysisError(f"Line {ln.num}: the answer row is numbered {num} (lines are out of order or one is missing).", raw)
        echo, segs = "|".join(parts[1:-1]).strip(), parts[-1].strip()
        sim = difflib.SequenceMatcher(None, _norm(echo), _norm(ln.text)).ratio()
        if sim < 0.6:
            raise AnalysisError(f"Line {ln.num}: the text the AI copied does not match your lyric (“{echo[:40]}”). The answer is not trusted.", raw)
        note = "" if sim >= 0.97 else "The AI copied this line slightly differently."
        segments = []
        for seg in segs.split(";"):
            lang, eq, units = seg.partition("=")
            lang = lang.strip().lower()
            if not eq or not _LANG_RE.match(lang):
                raise AnalysisError(f"Line {ln.num}: cannot read the part “{seg.strip()[:40]}” (expected lang=unit·unit).", raw)
            u = _split_units(units)
            if not u:
                raise AnalysisError(f"Line {ln.num}: the {lang} part has no syllables.", raw)
            segments.append(Segment(lang, u))
        out.append(LineResult(ln, segments, note))
    return out


def parse_batch_partial(raw: str, expected: list) -> tuple:
    """({line number: LineResult}, [problems]) for the rows of an answer that can be trusted. A row is read by ITS OWN NUMBER, so a missing or out-of-order row never shifts the others; a row that is
    not in the format, copies the wrong text or repeats a number is skipped (the first one of a number wins). The markers must be there. Nothing is guessed: the lines that are missing from the
    result are asked for again by analyze()."""
    text = re.sub(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$", "", raw or "").strip()
    if not text:
        raise AnalysisError("The AI answer is empty.", raw)
    rows = text.splitlines()
    a = [i for i, r in enumerate(rows) if _is_sep(r, SEP_ANALYSIS)]
    z = [i for i, r in enumerate(rows) if _is_sep(r, SEP_END)]
    if len(a) != 1 or len(z) != 1 or not a[0] < z[0]:
        raise AnalysisError("The AI answer does not have the ANALYSIS ... END marker lines exactly once, in order.", raw)
    by_num = {ln.num: ln for ln in expected}
    found, problems = {}, []
    for row in (r.strip() for r in rows[a[0] + 1:z[0]] if r.strip()):
        head = row.split("|", 1)[0].strip().rstrip(".")
        if not head.isdigit() or int(head) not in by_num:
            problems.append(f"A row that does not start with the number of a line was skipped: {row[:40]}")
            continue
        n = int(head)
        if n in found:
            problems.append(f"Line {n} was answered twice: the first answer was kept.")
            continue
        try:
            found[n] = parse_batch(SEP_ANALYSIS + chr(10) + row + chr(10) + SEP_END, [by_num[n]])[0]
        except AnalysisError as ex:
            problems.append(str(ex))
    return found, problems


MAX_REASKS = 2           # how many times the lines that are missing from an answer are asked for again


def analyze(lines: list, ask: Callable[[str, str], str], batch: int = BATCH_LINES, progress: Callable[[int, int], None] | None = None) -> Analysis:
    """Blocking (call from an executor). `ask(system, user) -> raw answer` is the existing AI backend. Lines are sent in batches. An answer with some rows missing or unusable is not thrown away: only the
    missing lines are asked for again (up to MAX_REASKS times). The result is complete or it does not exist: if lines are still missing after that, nothing is shown."""
    if not lines:
        raise AnalysisError("There are no sung lines to analyze (only [tags] or empty).")
    result = Analysis([])
    chunks = [lines[i:i + batch] for i in range(0, len(lines), batch)]
    for k, chunk in enumerate(chunks, 1):
        if progress:
            progress(k, len(chunks))
        got: dict = {}
        last_raw, last_problems = "", []
        for attempt in range(1 + MAX_REASKS):
            todo = [ln for ln in chunk if ln.num not in got]
            if not todo:
                break
            system, user = build_request(todo)
            raw = ask(system, user)
            result.raw.append(raw)
            last_raw = raw
            try:
                part, last_problems = parse_batch_partial(raw, todo)
            except AnalysisError as ex:
                ex.raw = raw
                if attempt == MAX_REASKS:
                    raise
                continue
            got.update(part)
        missing = [ln for ln in chunk if ln.num not in got]
        if missing:
            detail = (" " + last_problems[0]) if last_problems else ""
            raise AnalysisError(f"The AI returned {len(chunk) - len(missing)} usable result line(s) for {len(chunk)} sung line(s) after {1 + MAX_REASKS} tries (missing: line {', '.join(str(m.num) for m in missing[:8])}). Nothing was shown.{detail}", last_raw)
        result.lines.extend(got[ln.num] for ln in chunk)
    return result


# ── Phase 2: manual corrections kept APART from the raw AI result ────────────────────────────────────────────
def _h(text: str) -> str:
    return hashlib.sha1(unicodedata.normalize("NFC", text or "").encode("utf-8")).hexdigest()


def line_ids(lines: list) -> list:
    """One identity per sung line, checkable inside a lyrics snapshot: the line's own text + the text of the sung line before and after it + which occurrence of that exact
    triple it is. Two identical lines (a repeated chorus line) get different ids; a correction is therefore never moved to another line. If the line or a neighbour is edited the
    id changes, and the correction becomes inactive (kept, never guessed onto another line). Undoing the edit brings the same id back, so the correction applies again."""
    hs = [_h(ln.text) for ln in lines]
    seen, out = {}, []
    for i, h in enumerate(hs):
        prev = hs[i - 1] if i else "^"
        nxt = hs[i + 1] if i + 1 < len(hs) else "$"
        k = seen.get((h, prev, nxt), 0)
        seen[(h, prev, nxt)] = k + 1
        out.append(f"{h[:10]}.{prev[:6]}.{nxt[:6]}.{k}")
    return out


class CorrectionError(ValueError):
    pass


@dataclass
class Correction:
    units: list | None = None                   # manual segmentation (pronounced units); None = not edited
    count: int | None = None                    # manual count override; None = automatic
    text: str = ""                              # the lyric line it was made for (so an inactive correction can still be described)

    def to_dict(self) -> dict:
        return {"units": self.units, "count": self.count, "text": self.text}


def correction_from_dict(d) -> "Correction | None":
    """Defensive: a damaged entry is dropped, never half-used."""
    try:
        units, count = d.get("units"), d.get("count")
        if units is not None:
            if not isinstance(units, list) or not units or not all(isinstance(u, str) and u.strip() for u in units):
                return None
            units = [u.strip() for u in units]
        if count is not None:
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                return None
        if units is None and count is None:
            return None
        return Correction(units, count, str(d.get("text", "")))
    except Exception:
        return None


def parse_manual_units(text: str) -> "list | None":
    """'สะ | หวัด | ดี' -> ['สะ','หวัด','ดี']. Blank = no manual segmentation (None). An empty unit (a doubled or trailing separator) is an error. The units are NOT compared with the
    spelling of the lyric: how a word is pronounced may differ from how it is written."""
    s = (text or "").strip()
    if not s:
        return None
    parts = [u.strip() for u in re.split(r"[|·•]", s)]
    if any(not u for u in parts):
        raise CorrectionError("A unit is empty: check for a doubled or trailing separator ( | ).")
    if any("[" in u or "]" in u for u in parts):
        raise CorrectionError("Square brackets are not allowed in a unit (they are section tags).")
    return parts


def parse_manual_count(text: str) -> "int | None":
    s = (text or "").strip()
    if not s or s.lower() == "auto":
        return None
    if not re.fullmatch(r"[0-9]+", s):
        raise CorrectionError("The count must be a whole number of 0 or more (or empty / Auto).")
    return int(s)


@dataclass
class Effective:
    line: SungLine
    line_id: str
    ai_count: int
    count: int                                  # what is shown and added to the total
    source: str                                 # "ai" | "units" | "count"
    units: list                                 # the units shown: manual ones if there are any, else the AI's (flat)
    segments: list                              # the AI segments (language + units), unchanged
    correction: "Correction | None"
    note: str = ""
    warn: str = ""

    @property
    def manual(self) -> bool:
        return self.source != "ai"


def effective_count(ai_units: int, corr: "Correction | None") -> tuple:
    """(count, source): 1 manual count override, 2 count of the manual segmentation, 3 count of the AI segmentation."""
    if corr is not None and corr.count is not None:
        return corr.count, "count"
    if corr is not None and corr.units:
        return len(corr.units), "units"
    return ai_units, "ai"


def effective_warning(ai_units: int, corr: "Correction | None") -> str:
    if corr is None or corr.count is None:
        return ""
    shown = len(corr.units) if corr.units else ai_units
    return f"The count override ({corr.count}) differs from the segmentation shown ({shown}); the override is used." if corr.count != shown else ""


def effective_lines(result: Analysis, corrections: dict) -> tuple:
    """(effective lines, inactive corrections). The raw result is never modified. `inactive` = {line_id: Correction} whose line is not in this result."""
    ids = line_ids([r.line for r in result.lines])
    out = []
    for r, lid in zip(result.lines, ids):
        corr = corrections.get(lid)
        ai_units = [u for sg in r.segments for u in sg.units]
        count, source = effective_count(len(ai_units), corr)
        out.append(Effective(r.line, lid, r.count, count, source, list(corr.units) if (corr and corr.units) else ai_units, r.segments, corr, r.note, effective_warning(len(ai_units), corr)))
    present = set(ids)
    inactive = {k: v for k, v in corrections.items() if k not in present}
    return out, inactive


def effective_total(effs: list) -> int:
    return sum(e.count for e in effs)


# ── saving the last good result (so a restart does not need the AI again) ────────────────────────────────────
def analysis_to_dict(snap: Snapshot, result: Analysis, at: str) -> dict:
    return {"text_hash": snap.text_hash, "backend": snap.backend, "model": snap.model, "params": snap.params, "prompt_version": snap.prompt_version, "at": at,
            "lines": [{"num": r.line.num, "file_line": r.line.file_line, "text": r.line.text, "note": r.note,
                       "segments": [{"lang": sg.lang, "units": list(sg.units)} for sg in r.segments]} for r in result.lines]}


def analysis_from_dict(d) -> "tuple | None":
    """(info dict, Analysis) or None. Only a result that is well formed is accepted."""
    try:
        lines = []
        for x in d["lines"]:
            segs = [Segment(str(sg["lang"]), [str(u) for u in sg["units"]]) for sg in x["segments"]]
            if not segs or any(not sg.units or not _LANG_RE.match(sg.lang) for sg in segs):
                return None
            lines.append(LineResult(SungLine(int(x["num"]), int(x["file_line"]), str(x["text"])), segs, str(x.get("note", ""))))
        if not lines:
            return None
        info = {k: str(d[k]) for k in ("text_hash", "backend", "model", "params", "prompt_version")}
        info["at"] = str(d.get("at", ""))
        return info, Analysis(lines)
    except Exception:
        return None
