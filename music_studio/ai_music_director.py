"""AI Music Director: reads the user's lyrics and settings and PROPOSES three music directions (an ACE-Step 1.5 caption + suggested metadata each). Nothing here touches the UI,
the lyrics or the settings: the proposal is a draft until the user presses Apply, and `plan_apply` is the only place that turns a draft into SongSettings changes.

  ANALYSIS                    key|value lines (emotion, theme, narrative, energy arc, vocal character)
  DIRECTION 1 / 2 / 3         Name / Reason / Characteristics / Music Caption / Suggested Settings (name|value lines)
  METADATA                    name|value lines: the settings that suit the song as a whole (a direction's own settings win over these)
  SECTION CUE SUGGESTIONS     section|cue|why   (optional; suggestions only, never inserted into the lyrics)
  END
REJECTED (nothing is offered, the raw answer is kept for the debug view): a missing / repeated marker (a missing END only warns), a direction without Name / Reason / Music Caption / settings,
a caption that is empty, too short, too long or contains markup, a wrong number of directions. WARNED: an unusable metadata value (shown, never applied, never converted), a caption that
repeats lyric lines or contains BPM / key, text outside the markers. Nothing is repaired silently and nothing is clamped.

No LLM transport here: `analyze` takes a function `ask(system, user)`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import basic_caption as BC
from . import llm_chat as LC
from . import music_options as MO
from . import template_arrange as TA
from . import lyric_actions as AC
from .compiler import TAG_LINE

DIRECTION_COUNT = 3
FIELDS = ("style_prompt", "genre", "mood", "vocal", "energy", "bpm", "keyscale", "timesignature", "duration", "language")     # what a direction may suggest (style_prompt = its caption)
CHIP_FIELDS = ("genre", "mood", "vocal", "energy", "bpm", "keyscale", "language")                                              # the chips shown under the caption
LABELS = {**TA.FIELD_LABELS, "genre": "Genre", "language": "Language"}
MIN_CAPTION_WORDS = 12

M_ANALYSIS, M_META, M_CUES, M_END = "ANALYSIS", "METADATA", "SECTION CUE SUGGESTIONS", "END"
M_DIRS = tuple(f"DIRECTION {i}" for i in range(1, DIRECTION_COUNT + 1))
MARKERS = (M_ANALYSIS, *M_DIRS, M_META, M_CUES, M_END)
ANALYSIS_KEYS = ("emotion", "theme", "narrative", "energy arc", "vocal character")

SYSTEM = """You are an expert Music Director, arranger, vocal producer, and music prompt designer specializing in ACE-Step 1.5.

You receive the user's lyrics and the settings the user has chosen so far. Your job is to understand the emotional and musical identity of the song and to propose THREE different music directions, each with a rich ACE-Step 1.5 caption and suggested metadata.

RULES
- The user owns the lyrics. NEVER rewrite, translate, shorten, or extend them. Never copy lyric lines into a caption.
- Lyrics may be Thai, English or mixed. Read them in their own language. Do not assume a language; if the vocal language setting is "auto", leave it auto unless the lyrics clearly need otherwise.
- Respect what the user has already chosen (genre, vocal, style prompt, mood, energy). The three directions may differ in arrangement, vocal delivery, instrumentation and production WITHOUT changing the genre the user chose.
- The three directions must differ in at least two of these: instrumentation, groove, vocal delivery, arrangement, production. A different name is not enough.
- BPM, key and duration are proposals. They are not a measurement of a melody you have not heard.
- High energy means the track feels driving. It is not belting, soaring, falsetto, or a high singing register.
- Do not turn rain, night, or fire in the lyrics into sound effects unless the user asked for that.
- For Thai lyrics, describe natural pronunciation, a comfortable range, breath, and controlled peaks, and put impact in the arrangement. Use belting, soaring, falsetto, or operatic only when the user asked. One direction may still be a powerful vocal when that is its point. Do not make all three mid-range, and do not make all three belted.
- A caption is a rich musical description in flowing sentences, not a keyword list. Cover: genre identity, mood, vocal character, instrumentation, groove and rhythm, how the arrangement develops across the song, contrast between sections, and production texture.
- Describe how the song should SOUND, not how the AI should behave. Never write instructions to the model such as "you must" or "make sure".
- Do not put BPM, key or time signature in a caption; they go in the metadata.
- Treat lyrical section cues as suggestions, not guaranteed model commands. Do not claim ACE-Step can enforce exact section-level vocal dynamics, melody, or timing through text alone.
- Metadata must use only valid values: BPM a number between 30 and 300; Key like "A minor" or "C major" (never "auto"); Time Signature one of 2/4, 3/4, 4/4, 6/8; Duration in seconds (10-600); Vocal one of: auto, male vocal, female vocal, duet vocals, choir; Energy one of: auto, low energy, medium energy, high energy; Mood one of: none, Melancholic, Happy, Energetic, Dreamy, Romantic, Dark, Calm, Nostalgic, Epic, Angry, Hopeful; Language a code such as th, en, ja, ko, zh, or auto.
- The final output must be structured, validated, and suitable for use by Python.

OUTPUT FORMAT. Answer with exactly these marker lines, in this order, and nothing before the first marker or after END. No code fences, no extra commentary.

=========== ANALYSIS ===========
Emotion: <one or two sentences>
Theme: <...>
Narrative: <...>
Energy Arc: <how the energy should move through the song>
Vocal Character: <...>

=========== DIRECTION 1 ===========
Name: <short name>
Reason: <one or two sentences: why this direction suits the song>
Characteristics: <what makes it different from the other two, one sentence>
Music Caption: <the full ACE-Step caption, one paragraph>
Suggested Settings:
genre|<...>
mood|<...>
vocal|<...>
energy|<...>
bpm|<number>
key|<e.g. A minor>
time_signature|<e.g. 4/4>
language|<code or auto>

=========== DIRECTION 2 ===========
(same fields)

=========== DIRECTION 3 ===========
(same fields)

=========== METADATA ===========
genre|<...>
mood|<...>
vocal|<...>
energy|<...>
bpm|<number>
key|<...>
time_signature|<...>
duration|<seconds>
language|<code or auto>

=========== SECTION CUE SUGGESTIONS ===========
<section tag>|<short vocal or arrangement cue>|<why>
(zero to eight lines; write the section tags exactly as they appear in the lyrics)

=========== END ==========="""


class DirectorError(ValueError):
    """The answer cannot be offered. `problems` = sentences for the user, `raw` = what the AI answered (kept for the debug view)."""
    def __init__(self, message: str, problems: list | None = None, raw: str = ""):
        super().__init__(message)
        self.problems = problems or [message]
        self.raw = raw


class ApplyError(ValueError):
    """The draft cannot be applied now (stale, nothing chosen, an invalid value)."""


# ── the immutable snapshot an analysis is made from ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Snapshot:
    song_id: str
    sheet_id: str
    sheet_name: str
    title: str
    lyrics: str
    lyrics_hash: str
    settings_hash: str
    settings: tuple                 # (name, value) pairs of the SongSettings at that moment (a frozen copy)
    sections: tuple                 # section tags in order

    def setting(self, name: str, default=""):
        return dict(self.settings).get(name, default)


_WATCHED = ("genre", "mood", "vocal", "energy", "bpm", "keyscale", "timesignature", "duration", "language", "style_mode", "style_prompt", "extra_tags")


def settings_fingerprint(settings: dict) -> str:
    return AC.text_hash("|".join(f"{k}={settings.get(k)!r}" for k in _WATCHED))


def make_snapshot(song_id: str, sheet_id: str, sheet_name: str, title: str, lyrics: str, settings: dict) -> Snapshot:
    secs = tuple(l.strip() for l in (lyrics or "").splitlines() if TAG_LINE.match(l) and TA.is_structural(l.strip()))
    return Snapshot(song_id, sheet_id, sheet_name, title, lyrics or "", AC.text_hash(lyrics or ""), settings_fingerprint(settings),
                    tuple((k, settings.get(k)) for k in _WATCHED), secs)


def stale_reason(snap: Snapshot, song_id: str, sheet_id: str, lyrics: str, settings: dict) -> str:
    """"" when the draft still belongs to what is open now, else a sentence for the user."""
    if song_id != snap.song_id:
        return "The song changed — Reanalyze."
    if sheet_id != snap.sheet_id:
        return "The open sheet changed — Reanalyze."
    if AC.text_hash(lyrics or "") != snap.lyrics_hash:
        return "Lyrics changed — Reanalyze."
    if settings_fingerprint(settings) != snap.settings_hash:
        return "Settings changed — Reanalyze."
    return ""


# ── the request ──────────────────────────────────────────────────────────────────────────────────────────────
def build_messages(snap: Snapshot) -> tuple:
    g = lambda k: snap.setting(k)
    style = (g("style_prompt") or "").strip() if g("style_mode") == "advanced" else ""
    user = (f"SONG: {snap.title}\nUSER'S CURRENT CHOICES: genre {g('genre') or '-'}; mood {g('mood') or '-'}; vocal {g('vocal') or 'auto'}; energy {g('energy') or 'auto'}; "
            f"extra tags {g('extra_tags') or '-'}; BPM {g('bpm')}; key {g('keyscale')}; time signature {g('timesignature')}; duration {g('duration')} s; vocal language {g('language') or 'auto'}\n"
            + (f"CURRENT MUSIC STYLE PROMPT (the user's own words, respect them): {style[:800]}\n" if style else "")
            + (("SECTIONS IN THE LYRICS: " + ", ".join(s.strip('[]') for s in snap.sections) + "\n") if snap.sections else "")
            + f"\nLYRICS (read only):\n<<<\n{snap.lyrics.strip() or '(no lyrics yet)'}\n>>>\n")
    return SYSTEM, user


# ── the parsed result ────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Direction:
    index: int
    name: str
    reason: str
    characteristics: str
    caption: str
    values: dict = field(default_factory=dict)      # field -> VALID value (what may be applied)
    problems: list = field(default_factory=list)    # [(field, what the AI wrote, why it was not accepted)]
    raw: dict = field(default_factory=dict)         # field -> what the AI wrote
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass
class CueSuggestion:
    section: str
    cue: str
    why: str


@dataclass
class Proposal:
    analysis: dict
    directions: list
    meta: dict                                      # METADATA block: valid values
    meta_problems: list
    cues: list
    warnings: list
    raw: str = ""


_KEY_ALIAS = {"style": "style_prompt", "caption": "style_prompt", "music_caption": "style_prompt", "genre": "genre", "mood": "mood", "vocal": "vocal", "vocals": "vocal",
              "vocal_style": "vocal", "energy": "energy", "bpm": "bpm", "tempo": "bpm", "key": "keyscale", "keyscale": "keyscale", "time_signature": "timesignature",
              "timesignature": "timesignature", "time": "timesignature", "duration": "duration", "language": "language", "lang": "language"}
LANG_NAMES = {"thai": "th", "english": "en", "chinese": "zh", "japanese": "ja", "korean": "ko", "spanish": "es", "french": "fr", "german": "de"}


def split_blocks(raw: str) -> tuple:
    """({marker: text}, [problems]). Lenient about code fences, end-of-turn tokens, case and the exact decoration of the marker lines; strict about every marker being there once."""
    text = re.sub(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$", "", raw or "").strip()
    lines = [l for l in text.splitlines() if not re.fullmatch(r"\s*(?:```[A-Za-z]*|<<<|>>>)\s*", l)]       # code fences and the prompt's own <<< >>> delimiters echoed back
    out: dict = {"pre": [], "post": []}
    counts = {m: 0 for m in MARKERS}
    cur = "pre"
    for l in lines:
        name = next((m for m in MARKERS if LC._is_sep(l, m)), None)
        if name:
            counts[name] += 1
            cur = "post" if name == M_END else name
            if counts[name] == 1:
                out.setdefault(cur, [])
            continue
        out.setdefault(cur, []).append(l)
    problems = []
    for m in (M_ANALYSIS, *M_DIRS, M_META, M_END):
        if m == M_END and counts[m] == 0:
            out["_no_end"] = ["1"]                                                  # only a warning: a model that forgot END still gave every required block (found with the real Gemma4)
            continue
        if counts[m] == 0:
            problems.append(f"The {m} marker is missing.")
        elif counts[m] > 1:
            problems.append(f"The {m} marker appears {counts[m]} times.")
    if counts[M_CUES] > 1:
        problems.append(f"The {M_CUES} marker appears {counts[M_CUES]} times.")
    order = [m for m in MARKERS if counts[m] == 1]
    firsts = [next((i for i, l in enumerate(lines) if LC._is_sep(l, m)), -1) for m in order]
    if firsts != sorted(firsts):
        problems.append("The markers are not in the order ANALYSIS, DIRECTION 1-3, METADATA, SECTION CUE SUGGESTIONS, END.")
    return {k: "\n".join(v).strip() for k, v in out.items()}, problems


def _fields_of(block: str, names: tuple) -> dict:
    """`Name: text` fields; a field runs until the next known field (so a caption may wrap over lines). Unknown lines before the first field are ignored."""
    pat = re.compile(r"^\s*(" + "|".join(re.escape(n) for n in names) + r")\s*:\s*(.*)$", re.IGNORECASE)
    out, cur = {}, None
    for l in block.splitlines():
        m = pat.match(l)
        if m:
            cur = m.group(1).lower()
            out[cur] = m.group(2).strip()
        elif cur is not None and l.strip():
            out[cur] = (out[cur] + " " + l.strip()).strip()
    return out


def _settings_lines(text: str) -> dict:
    raw = {}
    for l in (text or "").splitlines():
        m = re.match(r"^\s*([A-Za-z_ ]{2,24}?)\s*[|:]\s*(.*)$", l)
        if not m:
            continue
        name = _KEY_ALIAS.get(re.sub(r"\s+", "_", m.group(1).strip().lower()))
        if name and name != "style_prompt":
            raw[name] = m.group(2).strip()
    return raw


def check_value(name: str, raw) -> tuple:
    """(value, None) or (None, reason): the rules of the Style / Output controls (template_arrange.check_field) plus Genre and Language. Nothing is clamped and 'auto' never becomes a BPM / Key."""
    s = ("" if raw is None else str(raw)).strip()
    if name == "genre":
        if not s:
            return None, "empty"
        for g in MO.GENRES:
            if g.casefold() == s.casefold():
                return g, None
        return None, "not one of the Genre choices"
    if name == "language":
        low = s.casefold()
        if low in ("auto", ""):
            return "auto", None
        if low in LANG_NAMES:
            return LANG_NAMES[low], None
        if re.fullmatch(r"[a-z]{2,3}", low):
            return low, None
        return None, "not a language code"
    return TA.check_field(name, raw)


def _check_values(raw: dict) -> tuple:
    values, problems, notes = {}, [], []
    for name, text in raw.items():
        v, why = check_value(name, text)
        if why:
            problems.append((name, text, why))
        else:
            values[name] = v
            if name == "keyscale":
                _k, note = TA.normalize_key(text)
                if note:
                    notes.append(note)
    return values, problems, notes


def caption_overlap_warning(captions: list) -> str:
    """A soft note when the three captions are mostly the same words. Identical captions are already rejected. This does not reject a proposal."""
    toks = [set(re.findall(r"[a-z0-9]+", (c or "").lower())) for c in captions]
    worst = 0.0
    for i, a in enumerate(toks):
        for b in toks[i + 1:]:
            if a and b:
                worst = max(worst, len(a & b) / len(a | b))
    if worst >= 0.72:
        return "These captions share a lot of the same words. They may not differ enough in arrangement, instrumentation, or production."
    return ""


def _caption_warnings(caption: str, lyrics: str) -> list:
    out = []
    if re.search(r"\b\d{2,3}\s*bpm\b|\b[A-G][#b]?\s*(?:major|minor)\b|\b[2346]/[48]\b", caption, re.IGNORECASE):
        out.append("The caption mentions a BPM, key or time signature; they belong in the metadata.")
    sung = [re.sub(r"\s+", " ", l.strip()) for l in (lyrics or "").splitlines() if l.strip() and not TAG_LINE.match(l)]
    flat = re.sub(r"\s+", " ", caption)
    if any(len(l) >= 10 and l in flat for l in sung):
        out.append("The caption repeats a line of the lyrics; the lyrics are not changed.")
    return out


def parse(raw: str, lyrics: str = "") -> Proposal:
    """The AI answer -> Proposal. Raises DirectorError (with `raw`) for anything that cannot be offered."""
    blocks, problems = split_blocks(raw)
    if problems:
        raise DirectorError("The AI answer does not follow the required format.", problems, raw)
    analysis = {k: v for k, v in _fields_of(blocks.get(M_ANALYSIS, ""), ANALYSIS_KEYS).items() if v}
    if not analysis:
        raise DirectorError("The AI answer has no analysis.", ["The ANALYSIS block has none of Emotion / Theme / Narrative / Energy Arc / Vocal Character."], raw)
    dir_names = ("name", "reason", "characteristics", "music caption", "suggested settings")
    directions, bad = [], []
    for i, marker in enumerate(M_DIRS, 1):
        block = blocks.get(marker, "")
        f = _fields_of(block, dir_names)
        miss = [lbl for key, lbl in (("name", "Name"), ("reason", "Reason"), ("music caption", "Music Caption")) if not f.get(key)]
        sm = re.search(r"^\s*Suggested Settings\s*:\s*(.*?)(?=\Z)", block, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        settings_raw = _settings_lines(sm.group(1)) if sm else {}
        if not sm:
            miss.append("Suggested Settings")
        elif not settings_raw:
            miss.append("Suggested Settings (no usable lines)")
        if miss:
            bad.append(f"Direction {i} is missing: {', '.join(miss)}.")
            continue
        cap, why = TA.check_field("style_prompt", f["music caption"])
        if why:
            bad.append(f"Direction {i}: the Music Caption is {why}.")
            continue
        if len(cap.split()) < MIN_CAPTION_WORDS:
            bad.append(f"Direction {i}: the Music Caption has only {len(cap.split())} words (a rich description is needed).")
            continue
        values, probs, notes = _check_values(settings_raw)
        d = Direction(i, f["name"][:60], f["reason"], f.get("characteristics", ""), cap, values, probs, settings_raw, notes, _caption_warnings(cap, lyrics))
        directions.append(d)
    if bad:
        raise DirectorError("The AI answer is incomplete.", bad, raw)
    if len({d.caption.casefold() for d in directions}) < len(directions):
        raise DirectorError("Two directions have the same caption.", ["Two directions have the same caption."], raw)
    meta_raw = _settings_lines(blocks.get(M_META, ""))
    meta, meta_problems, meta_notes = _check_values(meta_raw)
    cues, cue_warn = [], []
    for l in blocks.get(M_CUES, "").splitlines():
        if not l.strip():
            continue
        p = [x.strip() for x in l.split("|")]
        if len(p) < 2 or not p[0] or not p[1]:
            cue_warn.append(f"A section cue line was skipped: {l.strip()[:50]}")
            continue
        cues.append(CueSuggestion(p[0], p[1], p[2] if len(p) > 2 else ""))
    cues = cues[:8]
    warns = cue_warn + meta_notes
    if blocks.get("pre"):
        warns.append("Text before the first marker was ignored.")
    if blocks.get("post"):
        warns.append("Text after END was ignored.")
    if blocks.get("_no_end"):
        warns.append("The AI did not write the END marker; every required block was present, so the answer is shown, but the last lines may be cut off.")
    overlap = caption_overlap_warning([d.caption for d in directions])
    if overlap:
        warns.append(overlap)
    return Proposal(analysis, directions, meta, meta_problems, cues, warns, raw)


def analyze(ask, snap: Snapshot) -> Proposal:
    """One question, one answer, no automatic retry. Raises DirectorError (carrying `raw`) when the answer cannot be used."""
    system, user = build_messages(snap)
    raw = ask(system, user)
    prop = parse(raw, snap.lyrics)
    asked = (snap.setting("vocal", ""), snap.setting("energy", ""), snap.setting("extra_tags", ""), snap.setting("style_prompt", ""))
    for d in prop.directions:
        note = BC.vocal_range_warning(d.caption, *asked)
        if note and note not in d.warnings:
            d.warnings.append(note)
    return prop


# ── settings: user-selected / AI-suggested / effective ───────────────────────────────────────────────────────
def suggested(prop: Proposal, d: Direction) -> dict:
    """The AI-suggested valid values of a direction: its own settings over the METADATA block, plus its caption."""
    out = {**prop.meta, **{k: v for k, v in d.values.items()}}
    out["style_prompt"] = d.caption
    return out


def problems_of(prop: Proposal, d: Direction) -> list:
    own = {p[0] for p in d.problems}
    return list(d.problems) + [p for p in prop.meta_problems if p[0] not in d.values and p[0] not in own]


def user_value(snap: Snapshot, name: str):
    """What the user has now for a field, in the form check_value returns it (None = nothing chosen)."""
    v = snap.setting({"style_prompt": "style_prompt", "keyscale": "keyscale"}.get(name, name), None)
    if name in ("mood", "vocal", "energy", "genre") and not v:
        return None
    return v


def default_selection(snap: Snapshot, prop: Proposal, d: Direction) -> set:
    """Fields ticked by default in the Apply step: the caption always; a value only when the user had not chosen something else (a value the user chose is never overridden silently)."""
    sug = suggested(prop, d)
    sel = {"style_prompt"}
    for name, v in sug.items():
        if name == "style_prompt":
            continue
        mine = user_value(snap, name)
        if name in ("vocal", "energy", "mood", "genre") and mine and mine != v:
            continue
        if name == "language" and snap.setting("language", "auto") != v:
            continue                                                  # Language stays as the user has it (Auto by default); the AI's pick is offered, never ticked
        sel.add(name)
    return sel


def effective(snap: Snapshot, prop: Proposal, d: Direction, selected: set) -> dict:
    """The settings the sheet WOULD have after Apply (the user's, overlaid by the ticked AI values). Pure: nothing is written."""
    out = {k: v for k, v in snap.settings}
    sug = suggested(prop, d)
    for name in selected:
        if name in sug:
            out[name] = sug[name]
    if "style_prompt" in selected:
        out["style_mode"] = "advanced"
    return out


@dataclass
class ApplyPlan:
    changes: dict            # SongSettings field -> new value
    previous: dict           # the same fields -> the value they had (for Restore)
    caption: str


def plan_restore(previous: dict, applied: dict, current: dict) -> tuple:
    """(fields to put back, fields kept). A field is put back only while the sheet still holds exactly what Apply wrote: a value the user changed after Apply is theirs and is never
    overwritten by Restore. `previous` = the values before Apply, `applied` = what Apply wrote, `current` = the sheet's settings now."""
    back, kept = {}, []
    for k, old in previous.items():
        if current.get(k) == applied.get(k):
            back[k] = old
        else:
            kept.append(k)
    return back, kept


def plan_apply(snap: Snapshot, prop: Proposal, d: Direction, selected: set, song_id: str, sheet_id: str, lyrics: str, current: dict) -> ApplyPlan:
    """Everything Apply needs, checked. `current` = the open sheet's SongSettings as a dict. Raises ApplyError; never returns a change for the lyrics."""
    why = stale_reason(snap, song_id, sheet_id, lyrics, current)
    if why:
        raise ApplyError(why)
    sug = suggested(prop, d)
    chosen = {n for n in selected if n in sug and n in FIELDS}
    if not chosen:
        raise ApplyError("Nothing is ticked.")
    changes = {}
    for name in chosen:
        v, bad = check_value(name, sug[name])
        if bad:
            raise ApplyError(f"{LABELS.get(name, name)}: {bad}.")
        changes[name] = v
    if "style_prompt" in changes:
        changes["style_mode"] = "advanced"
    return ApplyPlan(changes, {k: current.get(k) for k in changes}, d.caption)
