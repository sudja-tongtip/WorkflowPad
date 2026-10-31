"""Data model: Song -> Takes. No UI, no I/O.

The lyrics text is the single source of truth: ACE-Step style text ([Verse - melancholic], [Hold] ..., (harmony)).
Render mode, the compiled copy, the original-words copy and the AI copy are all derived from it (see compiler.py), so nothing can drift apart.
Takes (generated audio) and failed / cancelled runs live in their own file so a generation never touches unsaved lyrics.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict, fields


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class SongSettings:
    workflow: str = ""                      # file name inside comfy_apis/audio ("" = first one found)
    genre: str = "Alternative Rock"
    mood: str = ""
    vocal: str = ""                         # e.g. "male vocal"
    energy: str = ""                        # e.g. "high energy"
    extra_tags: str = ""
    style_mode: str = "basic"               # basic: tags are built from genre / mood / vocal / energy / extra; advanced: tags are exactly style_prompt
    style_prompt: str = ""                  # the Advanced "Music Style Prompt" (kept when the mode is switched back to basic)
    style_prompt_history: list = field(default_factory=list)    # [{"text": ..., "ts": ...}] newest first; saved with the song
    bpm: int = 120
    keyscale: str = "E minor"
    timesignature: str = "4"
    duration: int = 180
    language: str = "auto"                  # "auto" = detected from the script of the lyrics when generating
    seed: int = -1                          # -1 = a new random seed for every Generate
    seed_mode: str = "same"                 # same: the KSampler uses `seed` too / fixed: it uses ksampler_seed
    ksampler_seed: int = 31
    steps: int = 8
    cfg: float = 1.0
    text_cfg: float = 2.0
    temperature: float = 0.85
    top_p: float = 0.9
    top_k: int = 0
    min_p: float = 0.0
    format: str = "mp3"
    audio_quality: str = ""                             # the bitrate / VBR level of mp3 and opus ('' = the format's default); flac has none
    wf_params: dict = field(default_factory=dict)       # steps / CFG remembered per workflow file: {"<workflow>.json": {"steps": n, "cfg": x}} (see quality_presets.py)


def build_basic_style(s: SongSettings) -> str:
    """Basic mode: the tags built from the dropdowns, exactly as before."""
    return ", ".join(x.strip() for x in (s.genre, s.mood, s.vocal, s.energy, s.extra_tags) if x and x.strip())


def style_tags(s: SongSettings) -> str:
    """The ONE source of the `tags` sent to ACE-Step, chosen by the mode. The two modes are never combined."""
    if s.style_mode == "advanced":
        return (s.style_prompt or "").strip()
    return build_basic_style(s)


HISTORY_LIMIT = 100


def add_prompt_history(s: SongSettings, text: str) -> bool:
    """Remember a style prompt (newest first). Returns False and changes nothing when the same text is already in the history or is empty."""
    t = (text or "").strip()
    if not t or any((h.get("text") or "").strip() == t for h in s.style_prompt_history):
        return False
    s.style_prompt_history.insert(0, {"text": t, "ts": now_str()})
    del s.style_prompt_history[HISTORY_LIMIT:]
    return True


def delete_prompt_history(s: SongSettings, text: str) -> bool:
    n = len(s.style_prompt_history)
    s.style_prompt_history = [h for h in s.style_prompt_history if (h.get("text") or "") != text]
    return len(s.style_prompt_history) != n


@dataclass
class Sheet:
    """One lyrics draft of a song (an Excel-like tab under the editor): its own lyrics and its own generation options (a full SongSettings as a dict, so a field added to
    SongSettings later is carried without touching this class). NOT a song: it lives inside one Song project."""
    id: str = field(default_factory=lambda: new_id("sheet_"))
    name: str = "Original"
    lyrics: str = ""
    settings: dict = field(default_factory=dict)
    source_take_id: str | None = None           # set when the sheet was opened from a generated take
    created: str = field(default_factory=now_str)
    updated: str = field(default_factory=now_str)


@dataclass
class SheetBook:
    """All the sheets of one song. The ACTIVE sheet's lyrics / settings are also the Song's own `lyrics` / `settings` (the working copy the whole editor uses): sheets.py moves
    content between the two, so there is exactly one place being edited at a time."""
    sheets: list = field(default_factory=list)
    active_id: str = ""
    open_ids: list = field(default_factory=list)    # the tabs shown (closing a tab only removes its id from here; the sheet stays in `sheets`)
    primary_id: str = ""                            # the sheet made from the song as it was before sheets existed (its analysis keeps living in analysis.json)
    rev: int = 0                                    # counts saves of sheets.json; song.json repeats it so a crash between the two writes can be told apart from an outside edit
    protected: str = ""                             # runtime only: sheets.json was damaged; nothing is written over it
    extra: dict = field(default_factory=dict)       # keys of the file this version does not know: written back unchanged


@dataclass
class Song:
    id: str = field(default_factory=lambda: new_id("song_"))
    title: str = "Untitled Song"
    lyrics: str = ""
    settings: SongSettings = field(default_factory=SongSettings)
    created: str = field(default_factory=now_str)
    updated: str = field(default_factory=now_str)
    book: "SheetBook | None" = field(default=None, repr=False, compare=False)    # runtime link to sheets.json; never part of song.json


@dataclass
class Take:
    id: str
    song_id: str
    file: str                               # file name inside the song's takes folder
    created: str
    duration: float = 0.0                   # seconds, measured from the real file when possible
    favorite: bool = False
    lyrics: str = ""                        # compiled ACE-Step lyrics actually sent
    tags: str = ""
    settings: dict = field(default_factory=dict)    # parameters actually sent (incl. effective seeds)
    seeds: dict = field(default_factory=dict)
    workflow_name: str = ""
    workflow_snapshot: dict = field(default_factory=dict)
    prompt_id: str = ""
    number: int = 0                         # "Take 04": counts completed takes of the song, never reused
    # Generation snapshot (takes made before these existed have them empty; see take_snapshot.py). Written once when the take is made and never edited afterwards.
    sheet_id: str = ""                      # the lyrics sheet Generate was pressed on
    sheet_name: str = ""
    editor_lyrics: str = ""                 # the lyrics exactly as typed in the editor (`lyrics` above is the COMPILED text that was sent)
    song_settings: dict = field(default_factory=dict)   # the sheet's full SongSettings at that moment (seed -1 / language "auto" as the user had them)
    resolved_language: str = ""             # the language code that was really sent when the setting was "auto"


@dataclass
class Run:
    """A generation that produced no audio (failed or cancelled). Kept apart from Takes."""
    id: str
    song_id: str
    status: str                             # failed | cancelled
    created: str
    error: str = ""
    prompt_id: str = ""
    seeds: dict = field(default_factory=dict)


@dataclass
class ChatMessage:
    role: str                               # user | assistant | note
    text: str
    ts: str = field(default_factory=now_str)
    proposal: str = ""                      # assistant: the proposed text (not applied)
    scope: str = ""                         # "selection" | "all" | ""
    snap_text: str = ""                     # the text the proposal replaces, as sent to the AI
    snap_start: int = -1
    snap_end: int = -1
    applied: bool = False
    # AI Actions (Workspace): all optional, so chat history written before them still loads
    action: str = ""                        # "" (a normal chat proposal) | continue | emotion | singability | translate
    action_req: dict = field(default_factory=dict)   # the request the proposal came from (target, options, backend), so Regenerate runs the SAME action on the SAME target
    snap_hash: str = ""                     # scope "insert": the hash of the whole lyrics the insertion point belongs to
    sheet_id: str = ""                      # the lyrics sheet the request was made on ("" = a message from before sheets: no sheet check)
    id: str = field(default_factory=lambda: new_id("msg_"))   # Apply / Regenerate find their message by this, never by its position in the list (older chat files get one when loaded)
    specialist: str = ""                    # the AI specialist this message belongs to (lyric_craft_advisor | song_editor | prosody | song_architect; the ids lyricist and hook of v1 are read as the new roles). "" = a message from before specialists: it is shown with the Lyric Craft Advisor
    edits: list = field(default_factory=list)   # assistant: independent edits of EXISTING lines ({id, first, last, original, replacement, reason, status, applied_hash}); each is previewed, applied or ignored on its own
    notes: list = field(default_factory=list)   # assistant: the program's own remarks about this answer (what it could not use): shown apart from the answer, never part of `text`, never sent back to the AI
    hints: list = field(default_factory=list)   # assistant: free-form music direction that is not a studio cue (not applicable here; it belongs in the Music Caption)
    rejected: str = ""                          # assistant: lyrics the AI wrote that cannot be applied: kept to be read and copied, never part of `text`


def _pick(cls, d: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in (d or {}).items() if k in names})


def song_to_dict(s: Song) -> dict:
    d = {k: v for k, v in asdict(s).items() if k != "book"}
    rev = getattr(s.book, "rev", 0) if s.book is not None else 0
    return {"format": "workflowpad-music-song", "version": 2, **d, **({"sheets_rev": rev} if rev else {})}


def song_from_dict(d: dict) -> Song:
    s = _pick(Song, {k: v for k, v in d.items() if k != "settings"})
    s.settings = _pick(SongSettings, d.get("settings", {}))
    return s


def take_from_dict(d: dict) -> Take:
    return _pick(Take, d)


def run_from_dict(d: dict) -> Run:
    return _pick(Run, d)


def chat_from_dict(d: dict) -> ChatMessage:
    return _pick(ChatMessage, d)


STARTER_LYRICS = "[Verse 1]\n\n[Chorus]\n"
