"""Basic mode -> ONE LLM-written music caption for ACE-Step 1.5.

`Basic settings + the latest lyrics -> LLM -> caption -> ACE-Step`. The LLM only writes the caption: lyrics, BPM, key, language, duration and every setting the user chose stay exactly as they
are (they travel to the workflow as separate parameters). The answer is parsed strictly (markers, a non-empty caption of a sensible length, no markup, no copied lyric lines); a raw
answer that does not pass is never sent to ACE-Step: the caller shows the problem and the user decides.

No LLM transport here: `compose_basic_caption` takes a function `ask(system, user)`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import llm_chat as LC
from . import template_arrange as TA
from .compiler import TAG_LINE

M_CAPTION, M_END = "CAPTION", "END"
MIN_WORDS, MAX_WORDS = 15, 220
MAX_CHARS = 1200                                  # the same limit as the Music Style Prompt (template_arrange.check_field)

SYSTEM = """You are a professional music producer,
arranger, vocal director, and ACE-Step 1.5
music caption specialist.

Your task is to convert the user's Basic
music settings and original lyrics into
one coherent, detailed music caption.

The user is the songwriter.
Never rewrite or modify their lyrics.

Treat the user's explicit genre, mood,
vocal style, energy, and extra style tags
as musical constraints.

Analyze the lyrics to understand:
- Emotional tone and progression
- Song sections and their relationships
- Appropriate instrumentation
- Vocal character and delivery
- Rhythm and groove
- Arrangement and dynamic development
- Production style and atmosphere

Create a musically coherent caption
describing how the finished song should sound.

Be specific about instruments, vocal texture,
groove, and production characteristics.

Avoid generic keyword lists, contradictory
directions, unnecessary instruments, and
excessively long descriptions.

Do not invent a vocal gender when Auto is
selected. Preserve the intended lyric language.

Section instructions are musical suggestions,
not guarantees of exact model behavior.

Do not write lyrics, explanations, role
instructions, system prompts, or metadata
into the caption.

High energy means the track feels driving.
It is not belting, soaring, falsetto, or a
high singing register.
Do not turn rain, night, or fire in the
lyrics into sound effects unless the user
asked for that.
For Thai lyrics, describe natural
pronunciation, a comfortable range, natural
phrasing, breath, and controlled peaks.
Put emotional impact in the arrangement.
Name belting, soaring, falsetto, or an
operatic delivery only when the user's
settings ask for it.
Do not force every song into a mid-range
vocal.

Return exactly:

=========== CAPTION ===========
<one polished English music caption>
=========== END ==========="""


class CaptionError(ValueError):
    """No usable caption. `problems` = sentences for the user, `raw` = what the AI answered (debug view only; never sent to ACE-Step)."""
    def __init__(self, message: str, problems: list | None = None, raw: str = ""):
        super().__init__(message)
        self.problems = problems or [message]
        self.raw = raw


@dataclass(frozen=True)
class Snapshot:
    """What the request is made from, captured when Generate is pressed. Nothing after that reads the editor."""
    song_id: str
    sheet_id: str
    title: str
    lyrics: str
    genre: str
    mood: str
    vocal: str
    energy: str
    extra_tags: str
    language: str
    bpm: int
    keyscale: str
    timesignature: str
    duration: int
    sections: tuple = ()


@dataclass
class Result:
    caption: str
    warnings: list = field(default_factory=list)
    raw: str = ""


def make_snapshot(song_id: str, sheet_id: str, title: str, lyrics: str, settings) -> Snapshot:
    secs = tuple(l.strip() for l in (lyrics or "").splitlines() if TAG_LINE.match(l) and TA.is_structural(l.strip()))
    g = lambda k: getattr(settings, k, "")
    return Snapshot(song_id, sheet_id, title, lyrics or "", (g("genre") or "").strip(), (g("mood") or "").strip(), (g("vocal") or "").strip(), (g("energy") or "").strip(),
                    (g("extra_tags") or "").strip(), (g("language") or "auto").strip(), g("bpm"), g("keyscale"), str(g("timesignature")), g("duration"), secs)


def build_messages(snap: Snapshot) -> tuple:
    vocal = snap.vocal or "Auto (do not choose a gender)"
    user = (f"SONG: {snap.title}\n\nUSER'S BASIC SETTINGS (musical constraints)\n"
            f"Genre: {snap.genre or 'not chosen'}\nMood: {snap.mood or 'not chosen'}\nVocal: {vocal}\nEnergy: {snap.energy or 'Auto'}\n"
            f"Extra style tags: {snap.extra_tags or '-'}\nLyric language setting: {snap.language or 'auto'}\n\n"
            f"FIXED GENERATION PARAMETERS (sent separately; for your understanding only. Do NOT write BPM, key, time signature or duration in the caption): "
            f"BPM {snap.bpm}, key {snap.keyscale}, time signature {snap.timesignature}, duration {snap.duration} s\n\n"
            + (("SECTIONS IN THE LYRICS: " + ", ".join(s.strip("[]") for s in snap.sections) + "\n\n") if snap.sections else "")
            + f"LYRICS (read only):\n<<<\n{snap.lyrics.strip() or '(no lyrics)'}\n>>>\n")
    return SYSTEM, user


_FORCED_HIGH = re.compile(
    r"\b(?:soaring(?:\s+vocals?)?|powerful\s+belting|belting|explosive\s+high(?:\s+notes?)?|falsetto|operatic)\b",
    re.IGNORECASE)
_ASKED_HIGH = re.compile(r"\b(?:soaring|belting|belted|falsetto|operatic|high\s+notes?)\b", re.IGNORECASE)


def vocal_range_warning(caption: str, *settings: str) -> str:
    """A note when the caption asks for a high or belted delivery the user did not request. Never a rejection, and never a reason to rewrite the caption."""
    if _ASKED_HIGH.search(" ".join(s or "" for s in settings)):
        return ""
    if _FORCED_HIGH.search(caption or ""):
        return ("The caption names a high or belted delivery the settings did not ask for. "
                "High energy is not a high singing register. A powerful vocal stays available if you ask for it.")
    return ""


def parse(raw: str, lyrics: str = "", vocal: str = "", energy: str = "", extra: str = "") -> Result:
    """The AI answer -> Result. Raises CaptionError for anything that must not reach ACE-Step. A missing END marker only warns (the real Gemma4 sometimes forgets it)."""
    text = re.sub(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$", "", raw or "").strip()
    lines = [l for l in text.splitlines() if not re.fullmatch(r"\s*(?:```[A-Za-z]*|<<<|>>>)\s*", l)]
    cap_i = [i for i, l in enumerate(lines) if LC._is_sep(l, M_CAPTION)]
    end_i = [i for i, l in enumerate(lines) if LC._is_sep(l, M_END)]
    if not cap_i:
        raise CaptionError("The AI answer has no CAPTION marker.", ["The CAPTION marker is missing."], raw)
    if len(cap_i) > 1 or len(end_i) > 1:
        raise CaptionError("The AI answer repeats a marker.", ["A marker appears more than once."], raw)
    start = cap_i[0] + 1
    stop = end_i[0] if end_i and end_i[0] > cap_i[0] else len(lines)
    if end_i and end_i[0] < cap_i[0]:
        raise CaptionError("The markers are in the wrong order.", ["END comes before CAPTION."], raw)
    body = "\n".join(lines[start:stop]).strip()
    warns = []
    if not end_i:
        warns.append("The AI did not write the END marker; the caption was complete enough to use.")
    if text and lines[:cap_i[0]] and any(l.strip() for l in lines[:cap_i[0]]):
        warns.append("Text before the CAPTION marker was ignored.")
    if "=====" in body:
        raise CaptionError("The caption contains marker text.", ["The caption contains marker lines."], raw)
    cap, why = TA.check_field("style_prompt", body)
    if why:
        msg = f"The caption {'' if why.startswith('contains') else 'is '}{why}."
        raise CaptionError(msg, [msg], raw)
    n = len(cap.split())
    if n < MIN_WORDS:
        raise CaptionError("The caption is too short.", [f"The caption has only {n} words (at least {MIN_WORDS} are needed)."], raw)
    if n > MAX_WORDS:
        raise CaptionError("The caption is too long.", [f"The caption has {n} words (at most {MAX_WORDS})."], raw)
    sung = [re.sub(r"\s+", " ", l.strip()) for l in (lyrics or "").splitlines() if l.strip() and not TAG_LINE.match(l)]
    flat = re.sub(r"\s+", " ", cap)
    if any(len(l) >= 10 and l in flat for l in sung):
        raise CaptionError("The caption contains lyric lines.", ["The caption repeats a line of the lyrics; lyrics must stay out of the caption."], raw)
    if re.search(r"\b\d{2,3}\s*bpm\b|\b[A-G][#b]?\s*(?:major|minor)\b|\b[2346]/[48]\b", cap, re.IGNORECASE):
        warns.append("The caption mentions a BPM, key or time signature; they are sent as separate parameters.")
    note = vocal_range_warning(cap, vocal, energy, extra)
    if note:
        warns.append(note)
    return Result(cap, warns, raw)


def compose_basic_caption(snap: Snapshot, ask) -> Result:
    """One question, one answer, no automatic retry. Raises CaptionError (carrying `raw`) when the answer cannot be used."""
    system, user = build_messages(snap)
    raw = ask(system, user)
    return parse(raw, snap.lyrics, snap.vocal, snap.energy, snap.extra_tags)
