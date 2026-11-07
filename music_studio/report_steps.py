"""The Analyze Song report of the Songwriter AI and of the Music Producer, asked STEP BY STEP in one conversation. No UI here.

Python drives the steps and the AI answers one part at a time, so each part can be shown the moment it is read:
  1. the data: the song, its style, the facts, the lyrics and (Songwriter) the Rhyme Analyzer's result. The AI answers READY.
  2. PART 1: the summary and the strengths.
  3. PART 2: one request per section. Songwriter: the section review (one [RHYME] tag). Music Producer: the cue row and the arrangement row of that section.
  4. PART 3: the suggestions ([FINDING] tags) and the notes about what the AI could not decide.
The system prompt of the conversation is the normal one-shot task text (specialist_engine.analyze_task): it already describes every part of the report, so nothing is written twice. Each request says
which part it wants. Python keeps only the tags that belong to the requested part, numbers them through the document (format only) and assembles ONE numbered-tag document, the same shape the
one-shot answer had. `SE.parse_report` then reads it exactly as before, so every check of the report is unchanged. `partial_doc()` is the document of what has been answered so far.
"""
from __future__ import annotations

import re
from typing import Callable

from . import specialist_engine as SE

ENABLED = True
_HEAD = re.compile(r"^\[([A-Z]+)\.(\d+)\]\s*$")
_END_TURN = re.compile(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$")
MAX_SECTIONS = 40                                  # a request per section: a song with more sections is asked for the first ones and the rest is noted

DOC_ORDER = {"REVIEW": 0, "STRENGTH": 1, "RHYME": 2, "FINDING": 3, "CUE": 4, "ARRANGE": 5, "NOTE": 6}      # sorted stably: the blocks of one kind keep the order they were asked in

STEP_NOTE = ("THIS REPORT IS ASKED FOR IN PARTS. Every request names ONE part: the summary and strengths, or ONE section, or the suggestions and notes. Answer ONLY the part that request names, in the numbered tags of "
             "the format below, and stop at [DONE]. Never write the whole report in one answer, and never repeat a part you have already written.")

RETRY_NO_TAGS = "Your last answer had no usable tag for this part. Answer again for the same part, in numbered tags only, ending with [DONE].\n"
RETRY_INCOMPLETE = "Your last answer was cut off: it had no [DONE] line, so none of it was used. Answer again for the same part, in numbered tags only, and end with [DONE].\n"

PART_TAGS = {"summary": ("REVIEW", "STRENGTH"), "writer_section": ("RHYME",), "producer_section": ("CUE", "ARRANGE"), "findings": ("FINDING", "NOTE"),
             "writer_sections": ("RHYME",), "producer_sections": ("CUE", "ARRANGE")}              # the last two: ALL sections in one answer (Grok)


class Stopped(Exception):
    """The run was overtaken (the lyrics changed): stop asking the AI."""


def split_answer(text: str) -> tuple:
    """([(kind, number, body)], done, trailing) of an answer in numbered tags. Reading stops at the FIRST [DONE]: `done` says it was seen, `trailing` says there was text after it (it is not used). Text before the
    first tag is not part of any block. An answer that never reached [DONE] (cut off, or the AI stopped) has done False: it is incomplete."""
    out, cur, done, trailing = [], None, False, False
    for line in _END_TURN.sub("", text or "").splitlines():
        if done:
            trailing = trailing or bool(line.strip())
            continue
        m = _HEAD.match(line.strip())
        if m:
            cur = [m.group(1), m.group(2), []]
            out.append(cur)
        elif line.strip() == "[DONE]":
            cur, done = None, True
        elif cur is not None:
            cur[2].append(line)
    return [(k, n, "\n".join(b).strip("\n")) for k, n, b in out], done, trailing


def split_blocks(text: str) -> list:
    """[(kind, number, body)] of an answer in numbered tags (see split_answer)."""
    return split_answer(text)[0]


# ── what an answer may contain: the checks below never change what the AI thinks; they only refuse text that is not an answer ──
CRITICAL_KEYS = ("lines", "evidence", "suggestion", "op", "at", "to", "name", "cues")      # a field that decides WHERE or WHAT a change is: a copied format line here makes the whole block unusable
_KEYLINE = re.compile(r"^\s*([A-Za-z_]+)\s*:\s*(.*)$")


def _norm(line: str) -> str:
    s = re.sub(r"^[-•*]\s+", "", (line or "").strip())
    return re.sub(r"\s+", " ", s).casefold()


def template_lines(system: str) -> set:
    """The lines of the format in the system prompt, normalized. An answer line that is exactly one of them was COPIED from the format (an instruction or a sample value), it is not an answer.
    Tag lines and very short lines are left out, so `limits: -` or `[DONE]` are never taken for a copy. Nothing here is a list of words: it is the prompt itself."""
    out = set()
    for l in (system or "").splitlines():
        n = _norm(l)
        if len(n) >= 14 and not (n.startswith("[") and n.endswith("]")) and not n.endswith("<<<"):      # a delimiter line (`suggestion: <<<`) is format, never a copied sentence
            out.add(n)
    return out


def preface_of(text: str) -> str:
    """The text an answer has BEFORE its first tag ('' = none). It belongs to no part and is not used."""
    for line in _END_TURN.sub("", text or "").splitlines():
        s = line.strip()
        if s:
            return "" if _HEAD.match(s) else s
    return ""


def diag_lines(diagnostics: list, cap: int = 12, width: int = 260) -> list:
    """The diagnostics of a report as short lines for the page: the first `cap` of them (each cut to `width`), then one line saying how many more there are (the log holds them all)."""
    rows = [str(d).strip()[:width] + ("…" if len(str(d).strip()) > width else "") for d in diagnostics or [] if str(d).strip()]
    shown = rows[:cap]
    if len(rows) > cap:
        shown.append(f"… and {len(rows) - cap} more (they are in the log).")
    return shown


def drop_placeholders(blocks: list, templates: set, label: str) -> tuple:
    """(blocks without the copied format text, notes). A line copied from the format is taken out of its block; in a FINDING, CUE or ARRANGE block a copied line in a field that decides where or what to
    change (or a copied free line, which could stand where the replacement words go) makes the whole block unusable, so nothing incomplete reaches Apply. The notes say what was refused."""
    kept, notes = [], []
    for kind, num, body in blocks:
        rows, bad = [], ""
        for ln in body.split("\n"):
            if _norm(ln) in templates:
                m = _KEYLINE.match(ln)
                key = m.group(1).lower() if m else ""
                if kind in ("FINDING", "CUE", "ARRANGE") and (not key or key in CRITICAL_KEYS):
                    bad = key or "text"
                notes.append(f"{label}: placeholder_copied: in [{kind}.{num}] the " + (f"`{key}` field" if key else "line") + f" “{ln.strip()[:60]}” is the format's own text, not an answer, so it was not used.")
                continue
            rows.append(ln)
        if bad:
            notes.append(f"{label}: [{kind}.{num}] was not used: a field that decides the change ({bad}) held text copied from the format.")
            continue
        kept.append((kind, num, "\n".join(rows).strip("\n")))
    return kept, notes


def sections_of_writer(snap: dict) -> list:
    """The sections the Songwriter reviews: those of the Rhyme Analyzer's result [(name, first line, last line)]. Without a result there is no rhyme part."""
    ev = snap.get("rhyme_data") or {}
    return [(str(s.get("name") or "").strip(), int(s.get("first") or 0), int(s.get("last") or 0)) for s in (ev.get("sections") or []) if s.get("first")]


def sections_of_producer(lyrics: str) -> list:
    """The section tag lines of the lyrics [(name, tag line, last line of the section)], the way the report's cue table counts them."""
    lines = (lyrics or "").split("\n")
    tags = []
    for i, l in enumerate(lines, 1):
        if SE.TAG_LINE.match(l):
            name = l.strip()[1:-1].strip()
            if name and not name.lower().startswith("hold"):
                tags.append((name, i))
    out = []
    for k, (name, i) in enumerate(tags):
        out.append((name, i, (tags[k + 1][1] - 1) if k + 1 < len(tags) else len(lines)))
    return out


class ReportStepper:
    def __init__(self, spec: str, snap: dict, ask: Callable[[str, str], str], data_user: str, status: "Callable[[str], None] | None" = None,
                 should_stop: "Callable[[], bool] | None" = None, on_part: "Callable[[], None] | None" = None, reopen: "Callable[[], None] | None" = None,
                 reply_language: str = "", compact: bool = False):
        self.compact = compact                    # Grok: three requests in all (1 the system prompt with the lyrics and the data, with the summary; 2 every section at once; 3 the suggestions) instead of one request per section
        self.spec, self.snap, self.ask, self.data_user = spec, snap, ask, data_user
        self.reply_language = (reply_language or "").strip()
        # the language is said in the system, and again at the end of EVERY request: the data (with "REPLY LANGUAGE") sits far back in the conversation and the model drifts to English otherwise
        self._lang_suffix = (f"\nLANGUAGE: write ALL the text of this answer in {self.reply_language} (quotes from the lyrics stay as written; tag names, field names and `-` stay as they are)." if self.reply_language else "")
        self.reopen, self.reopens, self._reopening = reopen, 0, False        # reopen() starts a NEW conversation: used when the context of the model is full
        self.status, self.should_stop, self.on_part = status, should_stop, on_part
        self.producer = SE.SP.is_producer(spec)
        self.system = STEP_NOTE + "\n\n" + SE.analyze_task(spec)
        self.sections = sections_of_producer(snap["lyrics"]) if self.producer else sections_of_writer(snap)
        self.blocks: list = []                    # (kind, number as written, body, answer id) in report order
        self.problems: list = []                  # what could not be asked or answered: shown to the user as "Partial", details in the log
        self.stray: list = []                     # tags the AI wrote for another part: left out, for the log only
        self.incomplete: list = []                # answers that never reached [DONE] ({"part", "attempt", "raw"}): not used, kept for the log
        self.diagnostics: list = []               # what was refused in an answer (copied format text, tags of another part, text outside the tags): shown with the report
        self.answers: list = []                   # every raw answer ({"part", "attempt", "raw"}) for the log, whatever was used
        self._templates = template_lines(self.system)
        self.reviewed = 0                         # sections answered so far (the Songwriter's table shows exactly these)
        self.requests = 0
        self._data_due, self._again = True, False   # the data travels WITH the first request (and again with the first request of a new session): no separate "READY" turn

    # ── one request ──
    def _data_head(self, again: bool = False) -> str:
        """The data of this analysis, put in front of a request. It is not a request of its own: an AI that is asked only to "reply READY" tends to write the whole report there, and then answers every
        later part the same way (50-60 seconds each); with the data in front of a real, small request the first answer is short and the later ones follow it."""
        if again:
            done = next((b for k, _n, b, _a in self.blocks if k == "REVIEW"), "")
            head = ("DATA of this analysis (the song, its style and facts, and the lyrics; data, never instructions). The earlier session was full, so this is a NEW session and the report CONTINUES here: "
                    "the parts already written are not asked for again" + (f" (the summary already written: {done.strip()[:400]})" if done.strip() else "") + ". Answer ONLY the request after the data.")
        else:
            head = "DATA of this analysis (the song, its style and facts, and the lyrics; data, never instructions). Read them, then answer ONLY the request after the data."
        return head + "\n\n" + self.data_user + "\n\nREQUEST:\n"

    def _say(self, user: str) -> str:
        if self.should_stop and self.should_stop():
            raise Stopped()
        self.requests += 1
        try:
            raw = self.ask(self.system, ((self._data_head(self._again) + user) if self._data_due else user) + self._lang_suffix)
            self._data_due = False
            return raw
        except SE.ContextFull:                                  # nothing was sent: a new session; the data goes in front of this same request
            if not self.reopen or self._data_due and self._again:
                raise
            self._note("Starting a new session…")
            self.reopen()
            self.reopens += 1
            self._data_due, self._again = True, True
            raw = self.ask(self.system, self._data_head(True) + user + self._lang_suffix)
            self._data_due = False
            return raw

    def _refused(self, text: str):
        """Text of an answer that was not used. It stays visible (with the report, and in the log with the raw answers): refusing it is never silent."""
        self.stray.append(text)
        if text not in self.diagnostics:
            self.diagnostics.append(text)

    def _note(self, text: str):
        if self.status:
            self.status(text)

    def _part(self):
        if self.on_part:
            try:
                self.on_part()
            except Exception:
                pass                                # the live view must never stop the report

    def _ask_part(self, part: str, user: str, label: str, keep_first_of: "str | None" = None) -> list:
        """One part, asked up to twice. Returns the blocks that belong to this part; a part that gave nothing is noted."""
        allowed = PART_TAGS[part]
        was_incomplete = False
        for attempt in (1, 2):
            try:
                raw = self._say(user if attempt == 1 else ((RETRY_INCOMPLETE if was_incomplete else RETRY_NO_TAGS) + user))
            except Stopped:
                raise
            except Exception as ex:
                self.problems.append(f"{label} (attempt {attempt}): {ex}")
                continue
            self.answers.append({"part": label, "attempt": attempt, "raw": raw})
            blocks, done, trailing = split_answer(raw)
            if not done:                                # incomplete_response: no [DONE], so the answer was cut off or unfinished. Nothing of it is used; the program never writes the [DONE] for the AI. The raw answer is kept for the log.
                was_incomplete = True
                self.incomplete.append({"part": label, "attempt": attempt, "raw": raw})
                self.problems.append(f"{label} (attempt {attempt}): incomplete_response: the answer has no [DONE] line, so none of it was used.")
                continue
            pre = preface_of(raw)
            if pre:
                self._refused(f"{label}: text before the first tag was not used ({pre[:50]}…).")
            if trailing:
                self._refused(f"{label}: text after [DONE] was not used.")
            blocks, notes = drop_placeholders(blocks, self._templates, label)
            for note in notes:
                self._refused(note)
            got = [(k, n, b, self.requests) for k, n, b in blocks if k in allowed and b.strip()]
            dropped = [k for k, _n, _b in blocks if k not in allowed]
            if dropped:
                self._refused(f"{label}: tags of another part were not used: {sorted(set(dropped))}.")
            if keep_first_of:                       # one section review asks for ONE [RHYME] tag
                if sum(1 for x in got if x[0] == keep_first_of) > 1:
                    self._refused(f"{label}: more than one [{keep_first_of}] tag; only the first was used.")
                first = [x for x in got if x[0] == keep_first_of][:1]
                got = first + [x for x in got if x[0] != keep_first_of]
            if got:
                return got
        self.problems.append(f"{label}: " + ("incomplete_response after the retry" if was_incomplete else "no usable answer") + ", so this part is missing.")
        return []

    # ── the run ──
    def run(self) -> str:
        self._note("Summary and strengths…")
        self.blocks += self._ask_part("summary", "PART 1 of the report. This answer is SHORT: [REVIEW.1] with its SUMMARY line, then the [STRENGTH.n] tags (at most 5), then [DONE]. "
                                                  "Everything else you write is thrown away, so write nothing else: no [RHYME], [FINDING], [CUE], [ARRANGE] or [NOTE] tag, and no other part of the report.", "summary")
        self._part()
        secs = self.sections[:MAX_SECTIONS]
        if len(self.sections) > MAX_SECTIONS:
            self.problems.append(f"The song has {len(self.sections)} sections; the first {MAX_SECTIONS} were reviewed.")
        if self.compact:
            self._compact_sections(secs)
            secs = []
        for k, (name, first, last) in enumerate(secs, 1):
            self._note(f"Section {k} of {len(secs)}: {name or first}")
            if self.producer:
                ask = (f"PART 2, SECTION {name or '(no name)'} (tag line {first}, lines {first}-{last}): write ONLY the [CUE.1] row and the [ARRANGE.1] row of THIS section "
                       f"(N = {first}; leave a row out when you have nothing concrete to say), then [DONE]. Write no other tag in this answer.")
                got = self._ask_part("producer_section", ask, f"section {name or first}")
            else:
                ask = (f"PART 2, SECTION REVIEW of {name or '(no name)'} (lines {first}-{last}): write ONLY ONE [RHYME.1] tag for THIS section, with the fields described under SECTION REVIEW "
                       f"(section: {name or '-'}; lines: {first}-{last}), then [DONE]. Take what rhymes with what from the RHYME ANALYZER RESULT and the lyrics; read its limitations, you may disagree with it for a reason, and never invent a pair that is in neither. Write no other tag in this answer.")
                got = self._ask_part("writer_section", ask, f"section {name or first}", keep_first_of="RHYME")
            self.blocks += got
            self.reviewed = k
            self._part()
        self._note("Suggestions and notes…")
        self.blocks += self._ask_part("findings", "PART 3 of the report: write the [FINDING.n] tags (the suggestions) and the [NOTE.1] tag (what you could not decide), then [DONE]. "
                                                  "Write no [REVIEW], [STRENGTH], [RHYME], [CUE] or [ARRANGE] tag in this answer. If nothing needs to change, write no [FINDING].", "suggestions")
        return self.document()

    def _compact_sections(self, secs: list):
        """ONE request for every section (the second of the three requests). The same tags as the per-section requests, so the same reader takes the answer."""
        if not secs:
            return
        self._note("All the sections…")
        listing = "\n".join(f"- {name or '(no name)'} (lines {first}-{last})" for name, first, last in secs)
        if self.producer:
            ask = (f"PART 2, ALL SECTIONS: for EACH of the {len(secs)} sections below write its [CUE.n] row and its [ARRANGE.n] row (one pair per section, numbered 1.. in the order of the list; N = the number of its tag line). "
                   f"Leave a row out when you have nothing concrete to say about that section. Then [DONE]. Write no other tag in this answer.\nSections:\n" + "\n".join(f"- {name or '(no name)'} (tag line {first}, lines {first}-{last})" for name, first, last in secs))
            got = self._ask_part("producer_sections", ask, "all sections")
        else:
            ask = (f"PART 2, SECTION REVIEWS of ALL {len(secs)} sections: write ONE [RHYME.n] tag for EACH section below (numbered 1.. in the order of the list), each with the fields described under SECTION REVIEW "
                   f"(section: its name; lines: its range), then [DONE]. Take what rhymes with what from the RHYMEANALYZER RESULT and the lyrics; read its limitations, you may disagree with it for a reason, and never invent a pair that is in neither. Write no other tag in this answer.\nSections:\n" + listing).replace("RHYMEANALYZER", "RHYME ANALYZER")
            got = self._ask_part("writer_sections", ask, "all sections")
        self.blocks += got
        if not self.producer:
            n = sum(1 for b in got if b[0] == "RHYME")
            self.reviewed = n
            if got and n < len(secs):
                self.problems.append(f"all sections: {n} of {len(secs)} sections were reviewed, so the others are missing.")
        else:
            self.reviewed = len(secs) if got else 0
        self._part()

    # ── the document ──
    def document(self, blocks: "list | None" = None) -> str:
        """ONE numbered-tag document of the answered parts. Tags are numbered 1.. per kind through the document (format only); a number repeated inside ONE answer is left as written, so the reader
        rejects that block as it always does."""
        cnt: dict = {}
        out = []
        seen: set = set()
        ordered = sorted(self.blocks if blocks is None else blocks, key=lambda b: DOC_ORDER.get(b[0], 9))      # the order of the one-shot answer: the reader takes every line after "ARRANGEMENT:" as an arrangement row, so FINDING must come BEFORE CUE and ARRANGE
        for kind, num, body, aid in ordered:
            if (aid, kind, num) in seen:
                out += [f"[{kind}.{num}]", body]
                continue
            seen.add((aid, kind, num))
            cnt[kind] = cnt.get(kind, 0) + 1
            out += [f"[{kind}.{cnt[kind]}]", body]
        out.append("[DONE]")
        return "\n".join(out)

    def partial_doc(self) -> str:
        return self.document()
