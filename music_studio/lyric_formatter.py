"""Stateless lyric formatting. One request, then a decision. Nothing here calls an LLM or edits the song.

The UI sends MODE + LYRICS with the formatter prompt. This module only builds that request and decides whether the
answer may replace the lyrics. It never rewrites a song to "fix" an answer: a bad answer is rejected and the original stays.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import compiler as K
from . import specialists as SP
from . import tag_parser as TP

PROMPT_ID = "lyric_formatter"

# The label is what the model sees after MODE:. The id stays inside the program.
MODES = (
    ("clear_all", "Clear All Cues"),
    ("beauty", "Beauty"),
    ("clean", "Clean Cues"),
    ("ace", "Simplify for ACE-Step"),
    ("format_lyrics", "Format Lyrics"),
)
MODE_IDS = tuple(mode_id for mode_id, _label in MODES)
MODE_LABEL = dict(MODES)

REASON_MODE = "Unknown format mode, so nothing was changed."
REASON_APPLIED = "That formatting result was already applied."
REASON_EXPIRED = "That formatting result is out of date, so it was not applied."
REASON_SONG = "The song changed while formatting, so that result was not applied."
REASON_SHEET = "The sheet changed while formatting, so that result was not applied."
REASON_EDITED = "The lyrics changed while formatting, so that result was not applied."
REASON_ERROR = "The formatter failed, so the lyrics were not changed."
REASON_EMPTY = "The formatter returned nothing, so the lyrics were not changed."
REASON_EXPLAIN = "The formatter answered with an explanation instead of lyrics, so nothing was changed."
REASON_WORDS = "The formatter changed the lyric words, so nothing was applied."
REASON_SECTIONS = "The formatter changed the sections, so nothing was applied."
REASON_HOLD = "The formatter dropped a [Hold], so nothing was applied."
REASON_FORMAT = "The formatter changed more than spacing, so nothing was applied."
REASON_CUES = "The formatter left cues in place, so nothing was applied."
REASON_SAME = "The lyrics were already in that form."

_TAG = re.compile(r"\[[^\[\]\n]*\]")
_HOLD = re.compile(r"\[Hold\]", re.IGNORECASE)
_HEADING = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]+\S")
_JSON_ARRAY = re.compile(r"\[\s*[\"{]")


@dataclass(frozen=True)
class FormatRequest:
    token: int
    song_id: str
    sheet_id: str
    lyrics: str
    mode: str


@dataclass(frozen=True)
class Plan:
    action: str          # apply | unchanged | reject
    text: str
    reason: str


def mode_label(mode: str) -> str:
    if mode not in MODE_LABEL:
        raise ValueError(mode)
    return MODE_LABEL[mode]


def user_message(mode: str, lyrics: str) -> str:
    """The single user turn: the mode name, then the lyrics exactly as they are now."""
    return "MODE: " + mode_label(mode) + "\n\nLYRICS:\n" + (lyrics or "")


def load_prompt(user: Path | None = None, bundled: Path | None = None):
    """The formatter prompt. The user's copy wins when it is valid; otherwise the bundled file. An invalid file is not a prompt."""
    return SP.load(PROMPT_ID, user=user, bundled=bundled)


def commit(before: str, plan: Plan) -> tuple:
    """(text, record_undo). Reject and an unchanged sheet record nothing and leave `before` as it is."""
    if plan.action != "apply" or plan.text == before:
        return before, False
    return plan.text, True


def plan(req: FormatRequest, raw: "str | None", *, now_song_id: str, now_sheet_id: str, now_lyrics: str, now_token: int, error: str = "", already_applied: bool = False) -> Plan:
    """Decide what to do with one finished request. Never returns an apply when the words, sections, or [Hold] did not survive."""
    if req.mode not in MODE_LABEL:
        return _reject(REASON_MODE)
    if already_applied:
        return _reject(REASON_APPLIED)
    if now_token != req.token:
        return _reject(REASON_EXPIRED)
    if now_song_id != req.song_id:
        return _reject(REASON_SONG)
    if now_sheet_id != req.sheet_id:
        return _reject(REASON_SHEET)
    if now_lyrics != req.lyrics:
        return _reject(REASON_EDITED)
    if error:
        return _reject(REASON_ERROR)
    out = _newlines(raw or "").lstrip("\ufeff")
    if not out.strip():
        return _reject(REASON_EMPTY)
    if _explained(out):
        return _reject(REASON_EXPLAIN)
    src = _newlines(req.lyrics)
    if req.mode == "format_lyrics":
        if _squash(out) != _squash(src):
            return _reject(REASON_FORMAT)
    else:
        if lyric_stream(out) != lyric_stream(src):
            return _reject(REASON_WORDS)
        if section_names(out) != section_names(src):
            return _reject(REASON_SECTIONS)
        if not _holds_survived(src, out):
            return _reject(REASON_HOLD)
        if req.mode == "clear_all" and _has_cue(out):
            return _reject(REASON_CUES)
    if out == src:
        return Plan("unchanged", out, REASON_SAME)
    return Plan("apply", out, "")


def lyric_stream(text: str) -> str:
    """Sung words in order, including punctuation and parenthetical ad-libs. Tags and blank lines are not words."""
    parts = []
    for line in _newlines(text).split("\n"):
        if not line.strip() or K.TAG_LINE.match(line):
            continue
        body = re.sub(r"[ \t]+", " ", _TAG.sub("", line)).strip()
        if body:
            parts.append(unicodedata.normalize("NFC", body))
    return "\n".join(parts)


def section_names(text: str) -> list:
    """Catalog section names in order (Verse 1 stays Verse 1). Cue lines are not sections."""
    names = []
    for line in _newlines(text).split("\n"):
        if not K.TAG_LINE.match(line):
            continue
        parsed = TP.parse_tag(line.strip()[1:-1])
        if parsed.kind == "section":
            names.append(re.sub(r"\s+", " ", parsed.name).strip().casefold())
    return names


def _has_cue(text: str) -> bool:
    """A section cue or a whole-line cue. [Hold] and a bare section tag are not cues."""
    for line in _newlines(text).split("\n"):
        if K.TAG_LINE.match(line):
            parsed = TP.parse_tag(line.strip()[1:-1])
            if parsed.kind == "section":
                if parsed.cue_text.strip():
                    return True
            elif line.strip().casefold() != "[hold]":
                return True
            continue
        for tag in _TAG.findall(line):
            if tag.casefold() != "[hold]":
                return True
    return False


def _holds_survived(src: str, out: str) -> bool:
    if len(_HOLD.findall(out)) < len(_HOLD.findall(src)):
        return False
    return not (Counter(_hold_bodies(src)) - Counter(_hold_bodies(out)))


def _hold_bodies(text: str) -> list:
    bodies = []
    for line in _newlines(text).split("\n"):
        if not _HOLD.search(line):
            continue
        body = re.sub(r"[ \t]+", " ", _TAG.sub("", line)).strip()
        if body:
            bodies.append(unicodedata.normalize("NFC", body))
    return bodies


def _explained(text: str) -> bool:
    stripped = text.lstrip()
    return "```" in text or bool(_HEADING.search(text)) or stripped.startswith("{") or bool(_JSON_ARRAY.match(stripped))


def _newlines(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", _newlines(text)).strip()


def _reject(reason: str) -> Plan:
    return Plan("reject", "", reason)
