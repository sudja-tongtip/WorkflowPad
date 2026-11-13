"""AI Recommend (V2, the Creative Music Producer): reads the user's lyrics and PROPOSES three music directions (an ACE-Step 1.5 caption + suggested metadata each). Nothing here touches the UI,
the lyrics or the settings: the proposal is a draft until the user presses Apply, and `plan_apply` is the only place that turns a draft into SongSettings changes.

The creative principles live in the editable prompt file `ai_recommend.md`. This module keeps only the OUTPUT FORMAT, the hard constraints and the validation:

  DIRECTION 1 / 2 / 3         Name / Reason / Music Caption / Suggested Settings (name|value lines)   (best fit / alternative / creative: the prompt says so, no field repeats it)
  END
V2 has no analysis report, no shared METADATA and no section or line vocal cues. An answer WITH a METADATA block is a saved V1 answer: the V1 parser reads it as it always did (its cues are not offered any more).
REJECTED (nothing is offered, the raw answer is kept for the debug view): a missing / repeated direction, a direction without Name / Reason / Music Caption / Suggested Settings, a missing core setting (genre, mood, vocal, energy, bpm, key, time signature),
a BPM / key / time signature that is not a valid value, a caption that is empty, too short, longer than 1200 characters (it is never shortened), or contains markup or a [bracket tag], two equal captions.
WARNED: a genre / mood / vocal / energy / language that is not one of the program's choices (shown, never applied, never converted), a caption that repeats lyric lines, contains BPM / key or reads as an instruction to the model,
captions that are too alike, too many genres or instruments, text outside the markers. Nothing is repaired silently and nothing is clamped.

No LLM transport here: `analyze` takes a function `ask(system, user)`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import arrangement_plan as AP
from . import basic_caption as BC
from . import clear_tags as CG
from . import llm_chat as LC
from . import music_director_context as MX
from . import music_options as MO
from . import template_arrange as TA
from . import lyric_actions as AC
from . import cue_library as CL
from .compiler import TAG_LINE

DIRECTION_COUNT = 3
FIELDS = ("style_prompt", "genre", "mood", "vocal", "energy", "bpm", "keyscale", "timesignature", "duration", "language")     # what a direction may suggest (style_prompt = its caption)
CHIP_FIELDS = ("genre", "mood", "vocal", "energy", "bpm", "keyscale", "timesignature")                                              # the chips shown under the caption
LABELS = {**TA.FIELD_LABELS, "genre": "Genre", "language": "Language"}
MIN_CAPTION_WORDS = 12

M_ANALYSIS, M_META, M_CUES, M_LINES, M_END = "ANALYSIS", "METADATA", "SECTION CUE SUGGESTIONS", "LINE VOCAL CUES", "END"
M_DIRS = tuple(f"DIRECTION {i}" for i in range(1, DIRECTION_COUNT + 1))
MARKERS = (M_ANALYSIS, *M_DIRS, M_META, M_CUES, M_LINES, M_END)
ANALYSIS_KEYS = ("emotion", "theme", "narrative", "energy arc", "vocal character")

CONTRACT = """HARD CONSTRAINTS (the program checks these; the guidance above says how to think)
- The user owns the lyrics. Never rewrite, translate, shorten or extend them. Never copy lyric lines into a caption.
- A Music Caption is English, one natural paragraph of at most 1200 characters. It has no brackets or section tags, no markup, no BPM / key / time signature, and no instruction to the model. The program never shortens or repairs a caption: one that breaks these rules is refused.
- Do not write section cues or line vocal cues. Existing cues in the lyrics are context only.
- Settings use only valid values: BPM a number between 30 and 300; Key like "A minor" or "C major" (never "auto"; it is a recommended starting key, not a measured one); Time Signature one of 2/4, 3/4, 4/4, 6/8; Vocal one of: auto, male vocal, female vocal, duet vocals, choir; Energy one of: auto, low energy, medium energy, high energy; Mood one of: none, Melancholic, Happy, Energetic, Dreamy, Romantic, Dark, Calm, Nostalgic, Epic, Angry, Hopeful; Genre one of the genres the program knows (a common genre name); Language a code such as th, en, ja, ko, zh, or auto. The song's duration is the user's: do not propose one.
- The final output is read by Python: follow the format exactly.

OUTPUT FORMAT. Answer with exactly three [STYLE] tags, numbered 1, 2 and 3, then [DONE]. Nothing before the first tag. No code fences, no extra commentary. Write every field name (Name:, Reason:, Music Caption:, Suggested Settings:) exactly as shown, in English. The Reason follows the reply language of the request. The direction Name, the Music Caption and every Suggested Settings value stay in English.

[STYLE.1]
Name: <short style name>
Reason: <one or two sentences: the key musical decision and why it suits this song>
Music Caption: <the English caption, one paragraph>
Suggested Settings:
genre|<...>
mood|<...>
vocal|<...>
energy|<...>
bpm|<number>
key|<recommended starting key, e.g. A minor>
time_signature|<e.g. 4/4>
language|<code or auto>

[STYLE.2]
(same fields)

[STYLE.3]
(same fields)

[DONE]"""

# V3 adds a bar plan and cue suggestions inside each direction. The production contract above is what a normal Analyze sends.
# The length in seconds is never taken from the model: arrangement_plan calculates it. Lyrics, cues and settings still change only when the user confirms.
CONTRACT_V3 = """HARD CONSTRAINTS (the program checks these; the guidance above says how to think)
- The user owns the lyrics. Never rewrite, translate, shorten or extend them. Never copy lyric lines into a caption.
- A Music Caption is English, one natural paragraph of at most 1200 characters. It has no brackets or section tags, no markup, no BPM / key / time signature, and no instruction to the model. The program never shortens or repairs a caption: one that breaks these rules is refused.
- Settings use only valid values: BPM a number between 30 and 300; Key like "A minor" or "C major" (never "auto"; it is a recommended starting key, not a measured one); Time Signature one of 2/4, 3/4, 4/4, 6/8; Vocal one of: auto, male vocal, female vocal, duet vocals, choir; Energy one of: auto, low energy, medium energy, high energy; Mood one of: none, Melancholic, Happy, Energetic, Dreamy, Romantic, Dark, Calm, Nostalgic, Epic, Angry, Hopeful; Genre one of the genres the program knows (a common genre name); Language a code such as th, en, ja, ko, zh, or auto. genre and genre_enum must each be one of those names. A finer style belongs in the Music Caption. An unknown genre name is shown and is not applied, and it does not clear the user's genre.
- Do not put a duration in Suggested Settings. Do not write seconds, minutes, or a clock time anywhere in the answer. The program calculates the length from the bars, the BPM and the time signature. The preferred duration is not a bar target and not a quota: do not add bars only to make the total match it.
- Arrangement Plan lists every existing section from the request, copied exactly, plus any proposed instrumental row you insert between them. A row is the section name, a vertical bar, and a whole number of bars. After the bars you may add the role, the energy, the texture, the vocal, the next section, and the reason. Do not write a clock in those columns. Do not drop an existing section. Do not invent a sung section. Do not write a closing tag.
- Cue Suggestions are optional. A row is the section name, a vertical bar, the cue, a vertical bar, the reason, a vertical bar, and keep, add, or replace. Use a cue the program already knows when one fits. A cue already on the section is keep. Omit the row when you have no cue. Never write none, null, or a dash as the cue. Do not rewrite the sung words.
- The final output is read by Python: follow the format exactly.

OUTPUT FORMAT. Answer with exactly three [STYLE] tags, numbered 1, 2 and 3, then [DONE]. Nothing before the first tag. No code fences, no extra commentary. Write every field name exactly as shown, in English. The Reason, the Vision, the Duration Reasoning and the Coaching sentences follow the reply language of the request. The direction Name, the Music Caption and every Suggested Settings value stay in English.

[STYLE.1]
Name: <short style name>
Vision: <one or two sentences: the picture of the record>
Reason: <one or two sentences: the key musical decision and why it suits this song>
Music Caption: <the English caption, one paragraph>
Suggested Settings:
genre|<one supported genre name>
genre_enum|<the same supported genre name>
mood|<...>
vocal|<...>
energy|<...>
bpm|<number>
key|<recommended starting key, e.g. A minor>
time_signature|<e.g. 4/4>
language|<code or auto>
Arrangement Plan:
<section name>|<bars>|<role>|<energy>|<texture>|<vocal or no vocal>|<next section>|<why this many bars>
Duration Reasoning: <why this bar plan fits the lyric and the request, with no clock time>
Cue Suggestions:
<section name>|<cue>|<why>|<keep, add, or replace>
Coaching:
strength|<what works>
recommendation|<one next step>
tradeoff|<what the choice gives and what it costs>
Trade-offs: <one sentence>

[STYLE.2]
(same fields)

[STYLE.3]
(same fields)

[DONE]"""

REQUIRED_SETTINGS = ("genre", "mood", "vocal", "energy", "bpm", "keyscale", "timesignature")      # a V2 direction must give these (language may be left out: it is the user's)
HARD_SETTINGS = ("bpm", "keyscale", "timesignature")                                              # a wrong TYPE / RANGE here refuses the answer; a value outside the program's choice lists (genre ...) is only shown, never applied


def _bundled_guidance() -> str:
    """The default creative guidance (the bundled ai_recommend.md), read directly: `SYSTEM` is the default effective prompt, for reading and for tests. A running request uses `system_prompt()`."""
    try:
        from . import specialists as SP
        return (SP.BUNDLED / "ai_recommend.md").read_text(encoding="utf-8-sig").replace("\r\n", "\n").rstrip()
    except OSError:
        return ""


SYSTEM = _bundled_guidance() + "\n\n" + CONTRACT


def system_prompt() -> tuple:
    """(the system prompt that is really sent, its version hash): the user's editable ai_recommend.md plus the program's contract. An invalid prompt file is reported, never sent."""
    from . import specialists as SP
    p = SP.load("ai_recommend")
    if not p.ok:
        raise DirectorError("The AI Recommend prompt is not valid: " + " ".join(p.errors), [("The AI Recommend prompt is not valid: " + " ".join(p.errors))])
    return p.text.rstrip() + "\n\n" + CONTRACT, p.hash


def prompt_version() -> str:
    """The hash of the AI Recommend prompt that would be sent now ('' when it is not valid)."""
    try:
        return system_prompt()[1]
    except DirectorError:
        return ""


def candidate_text() -> str:
    """The V3.2 candidate file. It is not the prompt Analyze sends until it is promoted."""
    from pathlib import Path
    path = Path(__file__).resolve().parent / "prompts" / "candidates" / "ai_music_director_v3_2.md"
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").rstrip()


def candidate_system() -> tuple:
    """(system prompt, hash of the candidate file). Raises DirectorError when the file is missing a required section. The production prompt is not read."""
    from . import specialists as SP
    text = candidate_text()
    errors = SP.check(text)
    if errors:
        raise DirectorError("The director candidate is not valid: " + " ".join(errors), errors)
    return text + "\n\n" + CONTRACT_V3, SP.text_hash(text)


# Who sings: the user's own choice (key, label, the `vocal` setting it sets, how the AI is told). 'auto' = the AI decides, as before.
SINGERS = (("auto", "Who sings: the AI decides", "", ""),
           ("male", "Male singer", "male vocal", "one male lead vocal"),
           ("female", "Female singer", "female vocal", "one female lead vocal"),
           ("duet", "Male + female duet", "duet vocals", "a male and a female singer sharing the song as a duet"),
           ("group", "Group / choir", "choir", "a group or choir singing together"),
           ("male_lead", "Male lead, female backing vocals", "male vocal", "a male lead vocal with female backing vocals"),
           ("female_lead", "Female lead, male backing vocals", "female vocal", "a female lead vocal with male backing vocals"))
SINGER_BY_KEY = {k: (label, vocal, tell) for k, label, vocal, tell in SINGERS}


# The same choice in Advanced mode, where the Music Style Prompt is sent exactly as written: the singer goes INTO the prompt as a visible phrase (the user can read and edit it), never added behind their back.
SINGER_TAGS = {"male": "male vocal", "female": "female vocal", "duet": "male and female duet vocals", "group": "choir, group vocals",
               "male_lead": "male lead vocal with female backing vocals", "female_lead": "female lead vocal with male backing vocals"}


def _phrase_re(phrase: str):
    return re.compile(r"(?:,\s*)?(?<![\w-])" + re.escape(phrase) + r"(?![\w-])", re.IGNORECASE)


def singer_in_prompt(prompt: str) -> str:
    """The SINGERS key whose phrase the prompt contains (longest phrase first), else 'auto'."""
    for k, ph in sorted(SINGER_TAGS.items(), key=lambda kv: -len(kv[1])):
        if _phrase_re(ph).search(prompt or ""):
            return k
    return "auto"


def set_singer_in_prompt(prompt: str, key: str) -> str:
    """The prompt without any singer phrase of ours, and with the phrase of `key` added at the end ('auto' only removes). Everything else the user wrote stays as it is."""
    t = prompt or ""
    for ph in sorted(SINGER_TAGS.values(), key=len, reverse=True):
        t = _phrase_re(ph).sub("", t)
    t = re.sub(r"^\s*,\s*", "", t).rstrip()
    ph = SINGER_TAGS.get(key)
    if not ph:
        return t
    if not t:
        return ph
    if t[-1] in ".!":
        return t[:-1] + ", " + ph + t[-1]
    return t + ", " + ph


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
    instructions: str = ""          # the user's own request to the AI (optional)
    reference: bool = False         # True only when the user asked the AI to use their current style settings as a reference
    singer: str = ""                # a key of SINGERS the user chose ('' / 'auto' = the AI decides)

    def setting(self, name: str, default=""):
        return dict(self.settings).get(name, default)


_WATCHED = ("genre", "mood", "vocal", "energy", "bpm", "keyscale", "timesignature", "duration", "language", "style_mode", "style_prompt", "extra_tags")   # kept in the snapshot for APPLY (what the user has now); never sent to the AI
_INPUTS = ("duration", "language")                                                                                          # the only settings the AI reads: the song's length and vocal language
_REFERENCE = ("genre", "mood", "vocal", "energy", "extra_tags", "style_mode", "style_prompt")                              # read only when the user ticked "use my style settings as a reference"


def settings_fingerprint(settings: dict, reference: bool = False) -> str:
    """What the recommendation was made from: the song's length / language, and the style settings only when they were given to the AI as a reference. Any other setting (Basic genre, mood,
    the Advanced prompt, BPM, key, seeds ...) does NOT make a recommendation out of date."""
    keys = _INPUTS + (_REFERENCE if reference else ())
    return AC.text_hash("|".join(f"{k}={settings.get(k)!r}" for k in keys))


def make_snapshot(song_id: str, sheet_id: str, sheet_name: str, title: str, lyrics: str, settings: dict, instructions: str = "", reference: bool = False, singer: str = "") -> Snapshot:
    secs = tuple(l.strip() for l in (lyrics or "").splitlines() if TAG_LINE.match(l) and TA.is_structural(l.strip()))
    return Snapshot(song_id, sheet_id, sheet_name, title, lyrics or "", AC.text_hash(lyrics or ""), settings_fingerprint(settings, reference),
                    tuple((k, settings.get(k)) for k in _WATCHED), secs, (instructions or "").strip()[:600], bool(reference), singer if singer in SINGER_BY_KEY and singer != "auto" else "")


def refresh_settings(snap: Snapshot, settings: dict) -> Snapshot:
    """The same snapshot with what the user has NOW in the settings (for the Apply step: a value the user chose after the analysis is never overwritten by default)."""
    from dataclasses import replace
    return replace(snap, settings=tuple((k, settings.get(k)) for k in _WATCHED))


def wrong_target_reason(snap: Snapshot, song_id: str, sheet_id: str) -> str:
    """"" when the draft still belongs to the open song and sheet. This is the only reason that blocks Apply: the settings would go to a different song or sheet than the one analysed."""
    if song_id != snap.song_id:
        return "The song changed — Reanalyze."
    if sheet_id != snap.sheet_id:
        return "The open sheet changed — Reanalyze."
    return ""


def stale_reason(snap: Snapshot, song_id: str, sheet_id: str, lyrics: str, settings: dict) -> str:
    """"" when the draft still belongs to what is open now, else a sentence for the user."""
    if song_id != snap.song_id:
        return "The song changed — Reanalyze."
    if sheet_id != snap.sheet_id:
        return "The open sheet changed — Reanalyze."
    if AC.text_hash(lyrics or "") != snap.lyrics_hash:
        return "Lyrics changed — Reanalyze."
    if settings_fingerprint(settings, snap.reference) != snap.settings_hash:
        return "The song length / language (or the reference style) changed — Reanalyze."
    return ""


# ── the request ──────────────────────────────────────────────────────────────────────────────────────────────
def build_messages(snap: Snapshot, reply_language: str = "English", variant: str = "production") -> tuple:
    g = lambda k: snap.setting(k)
    ref = ""
    if snap.reference:                                          # only when the user asked for it: their current style as a starting point, not as a limit
        style = (g("style_prompt") or "").strip() if g("style_mode") == "advanced" else ""
        genre = (g("genre") or "").strip()
        keep = (f"THE GENRE IS THE USER'S CHOICE: {genre}. This OVERRIDES the rule that the directions may differ in genre. In ALL THREE directions the Genre setting must be {genre} and the caption must describe a {genre} track; "
                "the directions differ only inside that genre (sub-style, groove, instrumentation, vocal delivery, production). Do not name a direction after another genre. Only THE USER'S REQUEST can change this. "
                "Mood, vocal, energy and extra tags below are the starting feel: keep them unless the lyrics clearly need otherwise (then say why in the Reason).\n") if genre else ""
        ref = (f"REFERENCE (the user ticked 'use my current style settings'; these are their choices): genre {g('genre') or '-'}; mood {g('mood') or '-'}; "
               f"vocal {g('vocal') or 'auto'}; energy {g('energy') or 'auto'}; extra tags {g('extra_tags') or '-'}\n" + keep
               + (f"REFERENCE MUSIC STYLE PROMPT: {style[:800]}\n" if style else ""))
    lang = (f"REPLY LANGUAGE: {reply_language}. Write each Reason in {reply_language}. "
            "The direction Name, the Music Caption (always English: ACE-Step reads English) and every Suggested Settings value stay in English exactly as the format shows.\n") if reply_language != "English" else ""
    user = (f"SONG: {snap.title}\nSONG LENGTH: {g('duration')} s; vocal language: {g('language') or 'auto'}\n" + lang
            + (f"THE SINGER (the user chose this; all three directions use it, each caption names it, and every direction's vocal setting is {SINGER_BY_KEY[snap.singer][1]}): {SINGER_BY_KEY[snap.singer][2]}\n" if snap.singer else "")
            + (f"THE USER'S REQUEST (follow it): {snap.instructions}\n" if snap.instructions else "")
            + ref
            + f"\nLYRICS (read only):\n<<<\n{snap.lyrics.strip() or '(no lyrics yet)'}\n>>>\n\n"
            + MX.build_music_director_context(snap, "recommend") + "\n")
    if variant == "candidate":
        facts = AP.section_facts(snap.lyrics)
        stance = "hard" if AP.hard_duration_request(snap.instructions) else "preferred"
        user += ("ARRANGEMENT FACTS (copy each existing section name exactly; you may insert a proposed instrumental row between them; write a whole number of bars; do not write seconds):\n"
                 + ("\n".join(facts) if facts else "- (no section tags; do not invent a bar plan)") + "\n"
                 + f"PREFERRED DURATION: {g('duration') or '-'} seconds. Stance: {stance}. This number is not a bar target. Do not add or remove bars only to hit it. "
                 + "A hard stance means the request keeps the length fixed, so stay inside it or say why a fitting arrangement cannot.\n"
                 + "GENRE ENUM (genre and genre_enum must each be exactly one of these names; put a finer style in the Music Caption only): "
                 + ", ".join(MO.GENRES) + "\n"
                 + "Think in this order: what each section is for, how the energy moves, whether a new instrumental is earned, then the bars. Saying that no new instrumental is needed is allowed.\n")
        return candidate_system()[0], user
    return system_prompt()[0], user


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
    vision: str = ""
    duration_reasoning: str = ""
    coaching: dict = field(default_factory=dict)
    tradeoff: str = ""
    bar_lines: list = field(default_factory=list)
    cue_lines: list = field(default_factory=list)
    plan: object = None                                          # arrangement_plan.Plan when this answer carried a bar plan or cue rows; None on a legacy answer


@dataclass
class CueSuggestion:
    section: str
    cue: str
    why: str
    line: int = 0                   # the line of the [section] tag in the lyrics the cue is about (0 = no matching tag was found)
    header: str = ""                # that tag line as it is now, e.g. [Chorus - powerful belting]
    new_header: str = ""            # the same tag with this cue as its description, e.g. [Chorus - powerful belting female vocal]; what Apply would write


@dataclass
class LineCue:
    """One lyric that needs a different delivery. `suggestion` is `[cue]` plus that lyric, unchanged. line 0 means it cannot be applied."""
    lyric: str
    cue: str
    why: str
    line: int = 0
    evidence: str = ""
    suggestion: str = ""


@dataclass
class Proposal:
    analysis: dict
    directions: list
    meta: dict                                      # METADATA block: valid values
    meta_problems: list
    cues: list
    warnings: list
    raw: str = ""
    line_cues: list = field(default_factory=list)   # V1 only (saved answers); V2 has none
    version: int = 1                                # 2 = the Creative Music Producer format (no analysis, no cues, no shared METADATA)


_KEY_ALIAS = {"style": "style_prompt", "caption": "style_prompt", "music_caption": "style_prompt", "genre": "genre", "mood": "mood", "vocal": "vocal", "vocals": "vocal",
              "vocal_style": "vocal", "energy": "energy", "bpm": "bpm", "tempo": "bpm", "key": "keyscale", "keyscale": "keyscale", "time_signature": "timesignature",
              "timesignature": "timesignature", "time": "timesignature", "duration": "duration", "language": "language", "lang": "language"}
LANG_NAMES = {"thai": "th", "english": "en", "chinese": "zh", "japanese": "ja", "korean": "ko", "spanish": "es", "french": "fr", "german": "de"}


def _skip_line(line: str) -> bool:
    """A line that is only decoration (`***`, `---`) and not a field."""
    return bool(re.fullmatch(r"[\s*_#=\-.·•]+", line or ""))


def _label_lines(lines) -> int:
    """How many lines are `Label: text`. Gemma often writes the analysis fields and forgets the ANALYSIS heading."""
    return sum(1 for l in (lines or []) if re.match(r"^\s*[-*•]?\s*[^:：\n]{1,40}[:：]\s*\S", l))


def split_blocks(raw: str) -> tuple:
    """({marker: text}, [problems]). Lenient about code fences, end-of-turn tokens, case and the exact decoration of the marker lines; strict about every marker being there once.
    A missing END is a warning. A missing ANALYSIS heading is a warning when the lines before DIRECTION 1 are already the analysis fields."""
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
        if m == M_ANALYSIS and counts[m] == 0 and _label_lines(out.get("pre")) >= 2:
            out[M_ANALYSIS] = list(out.get("pre") or [])                            # Gemma writes Emotion: / อารมณ์: and skips the heading; those lines are the analysis
            out["pre"] = []
            out["_no_analysis"] = ["1"]
            continue
        if counts[m] == 0:
            problems.append(f"The {m} marker is missing.")
        elif counts[m] > 1:
            problems.append(f"The {m} marker appears {counts[m]} times.")
    if counts[M_CUES] > 1:
        problems.append(f"The {M_CUES} marker appears {counts[M_CUES]} times.")
    if counts[M_LINES] > 1:
        problems.append(f"The {M_LINES} marker appears {counts[M_LINES]} times.")
    order = [m for m in MARKERS if counts[m] == 1]
    firsts = [next((i for i, l in enumerate(lines) if LC._is_sep(l, m)), -1) for m in order]
    if firsts != sorted(firsts):
        problems.append("The markers are not in the order ANALYSIS, DIRECTION 1-3, METADATA, SECTION CUE SUGGESTIONS, LINE VOCAL CUES, END.")
    return {k: "\n".join(v).strip() for k, v in out.items()}, problems


def _fields_of(block: str, names: tuple) -> dict:
    """`Name: text` fields; a field runs until the next known field (so a caption may wrap over lines). Unknown lines before the first field are ignored."""
    pat = re.compile(r"^\s*(" + "|".join(re.escape(n) for n in names) + r")\s*:\s*(.*)$", re.IGNORECASE)
    out, cur = {}, None
    for l in block.splitlines():
        if _skip_line(l):
            continue
        m = pat.match(l)
        if m:
            cur = m.group(1).lower()
            out[cur] = m.group(2).strip()
        elif cur is not None and l.strip():
            out[cur] = (out[cur] + " " + l.strip()).strip()
    return out


def _fields_by_position(block: str, names: tuple) -> dict:
    """`Label: text` lines whose labels are not the English ones: the n-th label is the n-th name of the format (the format fixes the order). A line that continues the text of a field is appended to it.
    Used only when none of the English labels was found; nothing is guessed from the words of the label."""
    pat = re.compile(r"^\s*[-*•]?\s*([^:：\n]{1,40})[:：]\s*(.*)$")
    out, cur, n = {}, None, 0
    for l in (block or "").splitlines():
        if _skip_line(l):
            continue
        m = pat.match(l)
        if m and n < len(names):
            cur = names[n]
            n += 1
            out[cur] = m.group(2).strip()
        elif cur is not None and l.strip():
            out[cur] = (out[cur] + " " + l.strip()).strip()
    return {k: v for k, v in out.items() if v}


def _fill_by_position(found: dict, block: str, names: tuple) -> dict:
    """English labels win. A label written in another language (ชื่อ:, อารมณ์:) is taken by its place in the format, which fixes the order. Nothing is guessed from the words of the label."""
    if all(found.get(n) for n in names):
        return found
    pos = _fields_by_position(block, names)
    out = dict(found)
    for n in names:
        if not out.get(n) and pos.get(n):
            out[n] = pos[n]
    return out


def _settings_lines(text: str) -> dict:
    raw = {}
    for l in (text or "").splitlines():
        m = re.match(r"^\s*([A-Za-z_ ]{2,24}?)\s*[|:]\s*(.*)$", l)
        if not m:
            continue
        key = re.sub(r"\s+", "_", m.group(1).strip().lower())
        if key == "genre_enum":
            raw["genre_enum"] = m.group(2).strip()
            continue
        name = _KEY_ALIAS.get(key)
        if name and name != "style_prompt":
            raw[name] = m.group(2).strip()
    return raw


_LIST_SPLIT = re.compile(r"\s*(?:,|;|/|&|\band\b|\+)\s*", re.IGNORECASE)


def check_value(name: str, raw) -> tuple:
    """(value, None) or (None, reason): the rules of the Style / Output controls (template_arrange.check_field) plus Genre and Language. Nothing is clamped and 'auto' never becomes a BPM / Key.
    Genre and Mood are choices from a fixed list. When the AI wrote SEVERAL words ("Ambient, Electronic", "Dark, Atmospheric"), the first of its own words that is a choice is taken (the whole text with the
    separators read as spaces is tried first, so "Alternative, Rock" is "Alternative Rock"); a word of the AI's that is not a choice is never turned into a different one. `_check_values` says what was chosen."""
    one = _check_one(name, raw)
    if one[1] is None or name not in ("genre", "mood"):
        return one
    text = ("" if raw is None else str(raw)).strip()
    if not _LIST_SPLIT.search(text):
        return one
    joined = _check_one(name, _LIST_SPLIT.sub(" ", text))
    if joined[1] is None and joined[0]:
        return joined
    for part in _LIST_SPLIT.split(text):
        got = _check_one(name, part)
        if got[1] is None and got[0]:
            return got
    return one


def _check_one(name: str, raw) -> tuple:
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
    raw = dict(raw)
    values, problems, notes = {}, [], []
    enum = raw.pop("genre_enum", None)
    if enum is not None:
        v, why = check_value("genre", enum)
        if why:
            problems.append(("genre", enum, why))
        else:
            values["genre"] = v
            creative = raw.pop("genre", None)
            if creative and creative.casefold() != str(v).casefold():
                notes.append("The caption carries the finer genre. The genre setting uses the supported name.")
    for name, text in raw.items():
        if name == "genre" and values.get("genre"):
            continue
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
    if _INSTRUCTION.search(caption):
        out.append("The caption reads like an instruction to the model. A caption describes how the record sounds.")
    sung = [re.sub(r"\s+", " ", l.strip()) for l in (lyrics or "").splitlines() if l.strip() and not TAG_LINE.match(l)]
    flat = re.sub(r"\s+", " ", caption)
    if any(len(l) >= 10 and l in flat for l in sung):
        out.append("The caption repeats a line of the lyrics; the lyrics are not changed.")
    return out


def _norm_tag(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().strip("[]").strip().lower())


def resolve_cues(cues: list, lyrics: str) -> list:
    """Finds the [section] line of the lyrics every cue is about: the same tag text first, else the same section name (before ' - '). Two cues for equal tags take the tag lines one after the other.
    Nothing is guessed: a cue with no matching tag line keeps line 0 and cannot be applied."""
    heads = [(i + 1, l.strip()) for i, l in enumerate((lyrics or "").split(chr(10))) if re.match(r"^\s*\[[^\]]+\]\s*$", l)]
    used: set = set()
    for c in cues:
        want = _norm_tag(c.section)
        name = want.split(" - ")[0].strip()
        pick = (next((h for h in heads if h[0] not in used and _norm_tag(h[1]) == want), None)
                or next((h for h in heads if h[0] not in used and _norm_tag(h[1]).split(" - ")[0].strip() == name), None))
        if pick is None or not c.cue.strip("[] "):
            continue
        used.add(pick[0])
        c.line, c.header = pick
        sec = pick[1].strip()[1:-1].split(" - ")[0].strip()
        c.new_header = f"[{sec} - {c.cue.strip(chr(91) + chr(93) + chr(32))}]"
    return cues


def resolve_line_cues(rows: list, lyrics: str, warns: list) -> list:
    """A line cue applies only when the lyric text occurs once. The program writes `[phrase]` on the line before it. The lyric characters are copied, not rewritten.
    A repeated lyric, a cue that is not a line vocal, or a vocal cue already on the line before stays a note: nothing is chosen."""
    lines = (lyrics or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    counts: dict = {}
    for i, raw in enumerate(lines):
        if not raw.strip() or TAG_LINE.match(raw):
            continue
        counts.setdefault(raw.strip(), []).append(i)
    out = []
    for lyric, cue, why in rows:
        vocal = CL.line_vocal(cue)
        if vocal is None:
            warns.append(f"A line vocal cue was skipped because it is not a line vocal: {cue.strip()[:40]}")
            out.append(LineCue(lyric.strip(), cue.strip(), why))
            continue
        hits = counts.get(lyric.strip(), [])
        if len(hits) != 1:
            if hits:
                warns.append("A line vocal cue matches more than one lyric line, so it was not placed.")
            else:
                warns.append("A line vocal cue does not match one lyric line exactly, so it was not placed.")
            out.append(LineCue(lyric.strip(), vocal.phrase, why))
            continue
        i = hits[0]
        exact = lines[i].strip()
        p = i - 1
        while p >= 0 and not lines[p].strip():
            p -= 1
        prev = lines[p].strip() if p >= 0 else ""
        if prev.startswith("[") and prev.endswith("]") and CL.line_vocal(prev[1:-1]):
            warns.append("A line vocal cue was skipped because a vocal cue is already on the line before.")
            out.append(LineCue(exact, vocal.phrase, why))
            continue
        if any(CL.line_vocal(bit) for bit in re.findall(r"\[([^\[\]\n]*)\]", exact)):
            warns.append("A line vocal cue was skipped because that lyric already has a vocal cue on it.")
            out.append(LineCue(exact, vocal.phrase, why))
            continue
        out.append(LineCue(exact, vocal.phrase, why, i + 1, exact, f"[{vocal.phrase}]\n{exact}"))
    return out


def is_legacy(raw: str) -> bool:
    """A saved V1 answer: it has the shared METADATA block, which V2 never writes."""
    text = raw or ""
    return any(LC._is_sep(l, M_META) for l in text.splitlines())


_INSTRUCTION = re.compile(r"\b(?:you must|you should|please (?:generate|make|create|write)|make sure|do not forget|generate a|create a song)\b", re.IGNORECASE)
_BRACKET = re.compile(r"\[[^\]\n]{1,80}\]")


def split_blocks_v2(raw: str) -> tuple:
    """({marker: text}, [problems], [warnings]). Directions 1-3 once each and in order; END may be missing (a warning). A stray ANALYSIS / SECTION CUE SUGGESTIONS / LINE VOCAL CUES block is ignored with a warning."""
    text = re.sub(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$", "", raw or "").strip()
    lines = [l for l in text.splitlines() if not re.fullmatch(r"\s*(?:```[A-Za-z]*|<<<|>>>)\s*", l)]
    out: dict = {"pre": [], "post": []}
    counts = {m: 0 for m in MARKERS}
    cur = "pre"
    for l in lines:
        name = next((m for m in MARKERS if LC._is_sep(l, m)), None)
        if name:
            counts[name] += 1
            cur = "post" if name == M_END else ("ignored" if name in (M_ANALYSIS, M_CUES, M_LINES) else name)
            out.setdefault(cur, []) if counts[name] == 1 or cur == "ignored" else None
            continue
        out.setdefault(cur, []).append(l)
    problems, warns = [], []
    for m in M_DIRS:
        if counts[m] == 0:
            problems.append(f"The {m} marker is missing.")
        elif counts[m] > 1:
            problems.append(f"The {m} marker appears {counts[m]} times.")
    firsts = [next((i for i, l in enumerate(lines) if LC._is_sep(l, m)), -1) for m in M_DIRS if counts[m] == 1]
    if firsts != sorted(firsts):
        problems.append("The directions are not in the order DIRECTION 1, 2, 3.")
    if counts[M_END] == 0:
        warns.append("The AI did not write the END marker; every direction was present, so the answer is shown, but the last lines may be cut off.")
    if any(counts[m] for m in (M_ANALYSIS, M_CUES, M_LINES)):
        warns.append("The AI wrote an analysis or cue block. AI Recommend does not use them, so they were ignored.")
    if out.get("pre") and any(l.strip() for l in out["pre"]):
        warns.append("Text before the first marker was ignored.")
    if out.get("post") and any(l.strip() for l in out["post"]):
        warns.append("Text after END was ignored.")
    return {k: "\n".join(v).strip() for k, v in out.items()}, problems, warns


def parse(raw: str, lyrics: str = "") -> Proposal:
    """The AI answer -> Proposal. Raises DirectorError (with `raw`) for anything that cannot be offered. V2 is the format; an answer with a METADATA block is a saved V1 answer and is read by `parse_v1`."""
    return parse_v1(raw, lyrics) if is_legacy(raw) else parse_v2(raw, lyrics)


_EXTRA_HEAD = re.compile(r"^\s*(Arrangement Plan|Duration Reasoning|Cue Suggestions|Coaching|Trade-offs|Tradeoffs)\s*:\s*(.*)$", re.IGNORECASE)
_COACH_KEYS = {"strength": "strength", "concern": "concern", "recommendation": "recommendation", "tradeoff": "tradeoff", "trade-off": "tradeoff", "tradeoffs": "tradeoff"}


def _is_v3(raw: str) -> bool:
    """True when the answer itself carries a bar plan. A legacy answer does not, and stays all-or-nothing."""
    return bool(re.search(r"(?m)^\s*Arrangement Plan\s*:", raw or ""))


def _split_extra(block: str) -> tuple:
    """(the direction block without the V3 sections, {heading: text}). A V2 block has no such heading and comes back unchanged."""
    pre, cur, extra = [], None, {}
    for line in (block or "").splitlines():
        m = _EXTRA_HEAD.match(line)
        if m:
            cur = m.group(1).lower()
            if cur == "tradeoffs":
                cur = "trade-offs"
            extra[cur] = [m.group(2).strip()] if m.group(2).strip() else []
            continue
        if cur is None:
            pre.append(line)
        elif line.strip():
            extra[cur].append(line)
    return "\n".join(pre), {k: "\n".join(v).strip() for k, v in extra.items()}


_CONTROL_LINE = re.compile(r"^\[/?[A-Z][A-Z0-9]*(?:\.\d+)?\]$")
_CONTROL_TOKEN = re.compile(r"\[/?[A-Z][A-Z0-9]*(?:\.\d+)?\]")


def _strip_controls(text: str) -> str:
    """Drop leaked control tags such as [/STYLE.1]. Lyric tags like [Chorus] are not control tags."""
    kept = []
    for line in (text or "").splitlines():
        raw = line.strip()
        if _CONTROL_LINE.match(raw):
            continue
        cleaned = _CONTROL_TOKEN.sub("", line).strip()
        if cleaned:
            kept.append(cleaned)
    return "\n".join(kept).strip()


def _coaching(text: str) -> dict:
    """strength|text on its own line, or several key|text pairs packed onto one line."""
    out = {}
    for line in (text or "").splitlines():
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            i = 0
            while i + 1 < len(parts):
                name = _COACH_KEYS.get(parts[i].lower())
                if name and parts[i + 1]:
                    out[name] = parts[i + 1]
                    i += 2
                else:
                    i += 1
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        name = _COACH_KEYS.get(key.strip().lower())
        if name and val.strip():
            out[name] = val.strip()
    return out


def parse_v2(raw: str, lyrics: str = "") -> Proposal:
    original = raw or ""
    adapted, tag_notes = CG.unwrap(original, "style")
    blocks, problems, warns = split_blocks_v2(adapted)
    if problems:
        raise DirectorError("The AI answer does not follow the required format.", problems, original)
    dir_names = ("name", "reason", "characteristics", "music caption", "suggested settings")
    pos_names = ("name", "reason", "music caption", "suggested settings")
    directions, bad = [], []
    for i, marker in enumerate(M_DIRS, 1):
        block, extra = _split_extra(blocks.get(marker, ""))
        f = _fields_of(block, dir_names + ("vision",))
        if not any(f.get(n) for n in ("name", "reason", "music caption")):               # no English label at all (Thai labels): the format fixes the order, so the n-th label is the n-th field. Never mixed with English labels.
            f = _fill_by_position(f, block, pos_names)
        sm = re.search(r"^\s*Suggested Settings\s*:\s*(.*?)(?=\Z)", block, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        settings_raw = _settings_lines(sm.group(1)) if sm else _settings_lines(block)
        miss = [lbl for key, lbl in (("name", "Name"), ("reason", "Reason"), ("music caption", "Music Caption")) if not f.get(key)]
        if not settings_raw:
            miss.append("Suggested Settings")
        else:
            known = set(settings_raw)
            if "genre_enum" in known:
                known.add("genre")
            miss += [f"setting {LABELS.get(n, n)}" for n in REQUIRED_SETTINGS if n not in known]
        if miss:
            bad.append(f"Direction {i} is missing: {', '.join(miss)}.")
            continue
        cap, why = TA.check_field("style_prompt", f["music caption"])                       # never shortened: a caption over the limit is refused
        if why:
            bad.append(f"Direction {i}: the Music Caption is {why}.")
            continue
        if _BRACKET.search(cap):
            bad.append(f"Direction {i}: the Music Caption contains a [bracket tag]; a caption describes the sound in words and holds no section tags or cues.")
            continue
        if len(cap.split()) < MIN_CAPTION_WORDS and len(cap) < 80:
            bad.append(f"Direction {i}: the Music Caption has only {len(cap.split())} words (a rich description is needed).")
            continue
        values, probs, notes = _check_values(settings_raw)
        hard = [p for p in probs if p[0] in HARD_SETTINGS]
        if hard:
            bad.append(f"Direction {i}: " + "; ".join(f"{LABELS.get(n, n)} '{t}' is {w}" for n, t, w in hard) + ".")
            continue
        d = Direction(i, f["name"][:60], f["reason"], "", cap, values, probs, settings_raw, notes, _caption_warnings(cap, lyrics))
        d.vision = _strip_controls(f.get("vision") or "")[:400]
        d.duration_reasoning = _strip_controls(extra.get("duration reasoning", ""))[:600]
        d.coaching = _coaching(_strip_controls(extra.get("coaching", "")))
        d.tradeoff = _strip_controls(extra.get("trade-offs", ""))[:400]
        d.bar_lines = [ln for ln in _strip_controls(extra.get("arrangement plan", "")).splitlines() if ln.strip()]
        d.cue_lines = [ln for ln in _strip_controls(extra.get("cue suggestions", "")).splitlines() if ln.strip()]
        directions.append(d)
    if bad:
        if _is_v3(original) and directions:
            warns.extend(bad)                                          # one broken direction does not throw away the directions that parsed
        else:
            raise DirectorError("The AI answer is incomplete.", bad, original)
    if len({d.caption.casefold() for d in directions}) < len(directions):
        raise DirectorError("Two directions have the same caption.", ["Two directions have the same caption."], original)
    overlap = caption_overlap_warning([d.caption for d in directions])
    if overlap:
        warns.append(overlap)
    if tag_notes:
        warns = [n for n in tag_notes if n not in warns] + warns
    prop = Proposal({}, directions, {}, [], [], warns, original)
    prop.version = 2
    return prop


def parse_v1(raw: str, lyrics: str = "") -> Proposal:
    """A saved V1 answer (ANALYSIS, METADATA, cues): read exactly as before. Raises DirectorError (with `raw`) for anything that cannot be offered."""
    blocks, problems = split_blocks(raw)
    if problems:
        raise DirectorError("The AI answer does not follow the required format.", problems, raw)
    analysis_block = blocks.get(M_ANALYSIS, "")
    analysis = {k: v for k, v in _fill_by_position(_fields_of(analysis_block, ANALYSIS_KEYS), analysis_block, ANALYSIS_KEYS).items() if v}
    if not analysis:
        raise DirectorError("The AI answer has no analysis.", ["The ANALYSIS block has none of Emotion / Theme / Narrative / Energy Arc / Vocal Character."], raw)
    dir_names = ("name", "reason", "characteristics", "music caption", "suggested settings")
    meta_fallback = _settings_lines(blocks.get(M_META, ""))
    directions, bad, borrowed = [], [], []
    for i, marker in enumerate(M_DIRS, 1):
        block = blocks.get(marker, "")
        f = _fill_by_position(_fields_of(block, dir_names), block, dir_names)
        miss = [lbl for key, lbl in (("name", "Name"), ("reason", "Reason"), ("music caption", "Music Caption")) if not f.get(key)]
        sm = re.search(r"^\s*Suggested Settings\s*:\s*(.*?)(?=\Z)", block, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        settings_raw = _settings_lines(sm.group(1)) if sm else _settings_lines(block)     # a Thai heading, or no heading: the genre|mood lines are still the settings
        took_meta = False
        if not settings_raw and meta_fallback:
            settings_raw = dict(meta_fallback)
            took_meta = True
        if not settings_raw:
            miss.append("Suggested Settings" if not sm else "Suggested Settings (no usable lines)")
        if miss:
            bad.append(f"Direction {i} is missing: {', '.join(miss)}.")
            continue
        cap_txt, _cut = TA.shorten_caption(f["music caption"])
        cap, why = TA.check_field("style_prompt", cap_txt)
        if why:
            bad.append(f"Direction {i}: the Music Caption is {why}.")
            continue
        if len(cap.split()) < MIN_CAPTION_WORDS and len(cap) < 80:                         # Thai captions are one long run of characters, not twelve English words
            bad.append(f"Direction {i}: the Music Caption has only {len(cap.split())} words (a rich description is needed).")
            continue
        values, probs, notes = _check_values(settings_raw)
        d = Direction(i, f["name"][:60], f["reason"], f.get("characteristics", ""), cap, values, probs, settings_raw, notes, _caption_warnings(cap, lyrics))
        directions.append(d)
        if took_meta:
            borrowed.append(i)
    if bad:
        raise DirectorError("The AI answer is incomplete.", bad, raw)
    if len({d.caption.casefold() for d in directions}) < len(directions):
        raise DirectorError("Two directions have the same caption.", ["Two directions have the same caption."], raw)
    meta, meta_problems, meta_notes = _check_values(meta_fallback)
    cues, cue_warn = [], []
    for l in blocks.get(M_CUES, "").splitlines():
        if not l.strip():
            continue
        p = [x.strip() for x in l.split("|")]
        if len(p) < 2 or not p[0] or not p[1]:
            cue_warn.append(f"A section cue line was skipped: {l.strip()[:50]}")
            continue
        cues.append(CueSuggestion(p[0], p[1], p[2] if len(p) > 2 else ""))
    cues = resolve_cues(cues[:8], lyrics)
    line_rows, line_warn = [], []
    for l in blocks.get(M_LINES, "").splitlines():
        if not l.strip() or _skip_line(l):
            continue
        if l.strip().startswith("(") and "|" not in l:
            continue
        p = [x.strip() for x in l.split("|", 2)]
        if len(p) < 2 or not p[0] or not p[1]:
            line_warn.append(f"A line vocal cue was skipped: {l.strip()[:50]}")
            continue
        line_rows.append((p[0], p[1], p[2] if len(p) > 2 else ""))
    if len(line_rows) > 8:
        line_warn.append("Only the first eight line vocal cues were read.")
    line_cues = resolve_line_cues(line_rows[:8], lyrics, line_warn)
    warns = cue_warn + line_warn + meta_notes
    if blocks.get("pre"):
        warns.append("Text before the first marker was ignored.")
    if blocks.get("post"):
        warns.append("Text after END was ignored.")
    if blocks.get("_no_analysis"):
        warns.append("The AI left out the ANALYSIS heading. The lines before DIRECTION 1 were read as the analysis.")
    if borrowed:
        warns.append("Direction " + ", ".join(str(i) for i in borrowed) + " had no Suggested Settings. The shared METADATA values are shown for it.")
    if blocks.get("_no_end"):
        warns.append("The AI did not write the END marker; every required block was present, so the answer is shown, but the last lines may be cut off.")
    overlap = caption_overlap_warning([d.caption for d in directions])
    if overlap:
        warns.append(overlap)
    prop = Proposal(analysis, directions, meta, meta_problems, cues, warns, raw)
    prop.line_cues = line_cues
    return prop


def analyze(ask, snap: Snapshot, reply_language: str = "English", trace=None, variant: str = "production") -> Proposal:
    """One question, one answer, no automatic retry. Raises DirectorError (carrying `raw`) when the answer cannot be used.
    `trace(entry)` (optional) is called once, with what was asked and what came back, for the debug trace; it never changes the result and it is never given a key.
    `variant` 'candidate' sends the V3 candidate. The default is the production prompt."""
    import time
    system, user = build_messages(snap, reply_language, variant=variant)
    t0 = time.time()
    raw, prop, err = "", None, None
    try:
        raw = ask(system, user)
        prop = finish(raw, snap)
        return prop
    except DirectorError as ex:
        err = ex
        raise
    except Exception as ex:
        err = ex
        raise
    finally:
        if trace is not None:
            try:
                trace({"prompt_version": candidate_system()[1] if variant == "candidate" else prompt_version(), "format": 3 if variant == "candidate" else 2,
                       "system": system, "user": user, "lyrics_chars": len(snap.lyrics or ""),
                       "prompt_tokens_estimate": LC.estimate_tokens(system + "\n" + user), "tokens_are_estimate": True, "output_chars": len(raw or ""), "seconds": round(time.time() - t0, 1),
                       "raw": raw, "ok": err is None, "problems": list(getattr(err, "problems", []) or ([str(err)] if err else [])),
                       "caption_warnings": [d.warnings for d in prop.directions] if prop is not None else [], "warnings": list(prop.warnings) if prop is not None else []})
            except Exception:
                pass


def finish(raw: str, snap: Snapshot) -> Proposal:
    """The AI's raw answer -> the Proposal the user sees (also used to show a saved answer again, so it looks exactly as it did)."""
    prop = parse(raw, snap.lyrics)
    if prop.version == 1:                                       # a saved V1 answer: its analysis stays readable, its cues are not offered or applied any more (the raw answer on disk is untouched)
        prop.cues, prop.line_cues = [], []
    if snap.singer:                                             # the user decided who sings: the program makes the setting say so, whatever the AI wrote
        vocal = SINGER_BY_KEY[snap.singer][1]
        for d in prop.directions:
            d.values["vocal"] = vocal
            d.raw["vocal"] = vocal
            d.problems = [p for p in d.problems if p[0] != "vocal"]
        prop.meta["vocal"] = vocal
        prop.meta_problems = [p for p in prop.meta_problems if p[0] != "vocal"]
    asked = (snap.setting("vocal", ""), snap.setting("energy", ""), snap.setting("extra_tags", ""), snap.setting("style_prompt", ""))
    for d in prop.directions:
        note = BC.vocal_range_warning(d.caption, *asked)
        if note and note not in d.warnings:
            d.warnings.append(note)
        for extra in MX.review_caption(d.caption, snap):
            if extra not in d.warnings:
                d.warnings.append(extra)
        if d.bar_lines or d.cue_lines:
            try:
                bpm = float(d.values.get("bpm") or 0)
            except (TypeError, ValueError):
                bpm = 0.0
            try:
                target = float(snap.setting("duration") or 0)
            except (TypeError, ValueError):
                target = 0.0
            d.plan = AP.build_plan(snap.lyrics, d.bar_lines, d.cue_lines, bpm, str(d.values.get("timesignature") or ""), target, str(snap.setting("language") or ""), hard_limit=AP.hard_duration_request(snap.instructions))
            for note in d.plan.rejected:
                if note not in d.warnings:
                    d.warnings.append(note)
    return prop


# ── settings: user-selected / AI-suggested / effective ───────────────────────────────────────────────────────
STYLE_WORD_FIELDS = ("genre", "mood", "energy", "vocal")        # fields whose value must be one of the program's choices; the AI's own words for them are not lost (see full_caption)


def full_caption(prop: Proposal, d: Direction) -> str:
    """EVERY word the AI wrote for the style goes into the caption that is sent. The caption itself is never cut or filtered. The words the AI gave for Genre / Mood / Energy / Vocal that are not one of the
    program's choices (so no field can hold them: "Ambient, Electronic", "Moderate Energy") are added at the end of the caption, unless the caption already has all of their words."""
    cap = (d.caption or "").strip()
    have = {w.casefold() for w in re.findall(r"\w+", cap)}
    extra = []
    for name, text, _why in problems_of(prop, d):
        t = ("" if text is None else str(text)).strip()
        if name not in STYLE_WORD_FIELDS or not t:
            continue
        words = [w.casefold() for w in re.findall(r"\w+", t)]
        if words and not all(w in have for w in words) and t.casefold() not in (e.casefold() for e in extra):
            extra.append(t)
            have.update(words)
    if not extra:
        return cap
    return cap + (" " if cap.endswith((".", "!", "?")) else ", ") + ", ".join(extra) + "."


def suggested(prop: Proposal, d: Direction) -> dict:
    """The AI-suggested valid values of a direction: its own settings over the METADATA block, plus its caption (all of its words: see full_caption)."""
    out = {**prop.meta, **{k: v for k, v in d.values.items()}}
    out["style_prompt"] = full_caption(prop, d)
    plan = getattr(d, "plan", None)
    if plan is not None:
        if plan.duration_ok and plan.clock is not None:
            n, why = AP.duration_value(plan.clock.seconds)
            if n is not None and not why:
                out["duration"] = n
            else:
                out.pop("duration", None)
        else:
            out.pop("duration", None)
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
        if name == "language":
            continue                                                  # Language stays as the user has it. A direction never writes it.
        if name == "duration":
            plan = getattr(d, "plan", None)
            if plan is None or not plan.duration_ok:
                continue                                              # no checked bar plan: do not invent a length, and do not take the model's own seconds
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


def music_direction_plan(snap: Snapshot, prop: Proposal, d: Direction, song_id: str, sheet_id: str, lyrics: str, current: dict, mode: str = "full") -> ApplyPlan:
    """One card's music info, checked before anything is written. `full` includes the Python length when the bar plan is complete. `style` never writes a length.
    Language, seed and the lyrics are never in the result. An incomplete plan raises instead of returning a partial set."""
    plan = getattr(d, "plan", None)
    if mode == "full" and plan is not None and not plan.duration_ok:
        raise ApplyError("This arrangement plan is incomplete, so the music info was not changed.")
    fields = set(default_selection(snap, prop, d))
    sug = suggested(prop, d)
    for name in ("style_prompt", "genre", "mood", "vocal", "energy", "bpm", "keyscale", "timesignature"):
        if sug.get(name) not in (None, ""):
            fields.add(name)                                          # a chosen card replaces these, including a value the user had set before
    fields.discard("language")
    if mode == "full" and plan is not None and plan.duration_ok and sug.get("duration") not in (None, ""):
        fields.add("duration")
    if mode != "full":
        fields.discard("duration")
    got = plan_apply(snap, prop, d, fields, song_id, sheet_id, lyrics, current)
    if "language" in got.changes or "seed" in got.changes or "lyrics" in got.changes:
        raise ApplyError("Language, seed and the lyrics stay as they are.")
    if "duration" in got.changes:
        if plan is None or plan.clock is None or not plan.duration_ok:
            raise ApplyError("There is no checked length to store.")
        n, why = AP.duration_value(plan.clock.seconds)
        if why or n is None:
            raise ApplyError(f"The planned length is {why}.")
        got.changes["duration"] = n
    return got


def plan_apply(snap: Snapshot, prop: Proposal, d: Direction, selected: set, song_id: str, sheet_id: str, lyrics: str, current: dict) -> ApplyPlan:
    """Everything Apply needs, checked. `current` = the open sheet's SongSettings as a dict. Raises ApplyError; never returns a change for the lyrics."""
    why = wrong_target_reason(snap, song_id, sheet_id)               # changed lyrics do not block Apply: the style suggestion is still the user's to use
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
