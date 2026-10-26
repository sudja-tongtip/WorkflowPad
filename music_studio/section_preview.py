"""Section Audio Preview: pick ONE section by the caret, build the text of that section with temporary cues / type, run a short ACE-Step generation for it, keep the results as
Preview Takes. No UI here.

A preview is a NEW generation of just that section (not a cut of a full take, not a repaint). Everything a run needs is copied into an immutable request (`make_request`) when
Generate is pressed, so nothing the user does afterwards (typing, moving the caret, switching sheets) can change a run that is on its way, and a result is filed under the
request's own song, never under whatever is open when it arrives.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from dataclasses import dataclass, asdict, fields
from pathlib import Path

from . import comfy_audio as CA
from . import compiler as K
from . import cue_library as CL
from . import model as M
from . import store as ST
from . import tag_parser as TP
from .player import PlayerService

SECTION_TYPES = ["Intro", "Verse", "Pre-Chorus", "Chorus", "Bridge", "Outro", "Instrumental"]
PREVIEW_DURATIONS = (10, 15, 30)        # ComfyUI's TextEncodeAceStepAudio1.5 accepts a duration from 0 s and EmptyAceStep1.5LatentAudio from 1 s (object_info), so these are all valid inputs
DEFAULT_DURATION = 15


# ── sections ─────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SectionTarget:
    """One section of one text. Its identity is its PLACE (tag_start) plus its exact tag, with its ordinal among identical tags as the fallback: the name alone is never an identity
    ([Chorus] may appear many times)."""
    tag_start: int
    tag_end: int
    tag_text: str                 # "[Chorus - powerful]"
    name: str                     # "Chorus" / "Verse 2"
    key: str                      # "Chorus" / "Verse" (canonical type)
    ordinal: int                  # 0-based among the sections whose tag_text is exactly this
    same_count: int               # how many sections had exactly this tag when it was chosen
    body_start: int
    body_end: int
    line_number: int

    @property
    def inner(self) -> str:
        return self.tag_text[1:-1]

    def signature(self) -> tuple:
        return (self.tag_start, self.tag_text)


def find_sections(text: str) -> list:
    """Every section tag that opens a line, with the body that follows it. The body runs to the next section tag, or to the next custom [tag] that stands alone on its line (it opens
    something that is not this section). Cue tags ([Hold], [whispered]) and text around them stay inside the body."""
    text = text or ""
    found, ends = [], []
    for t in TP.find_tags(text):
        ls = text.rfind("\n", 0, t.start) + 1
        if text[ls:t.start].strip():
            continue                                                     # a tag in the middle of a sung line is not a section
        p = TP.parse_tag(t.inner)
        if p.kind == "section":
            found.append((t, p))
            ends.append(t.start)
        elif p.kind == "custom":
            le = text.find("\n", t.end)
            if not text[t.end:len(text) if le < 0 else le].strip():     # nothing else on its line
                ends.append(t.start)
    out = []
    seen: dict = {}
    for t, p in found:
        end = next((e for e in ends if e > t.start), len(text))
        same = sum(1 for u, _ in found if u.original == t.original)
        out.append(SectionTarget(t.start, t.end, t.original, p.name, p.section or p.name, seen.get(t.original, 0), same, t.end, end, text.count("\n", 0, t.start) + 1))
        seen[t.original] = seen.get(t.original, 0) + 1
    return out


def section_at(text: str, pos: int) -> "SectionTarget | None":
    """The section the caret is in: the last section tag at or before it. Before the first tag there is no section (None)."""
    best = None
    for s in find_sections(text):
        if s.tag_start <= pos:
            best = s
        else:
            break
    return best


def relocate(text: str, target: SectionTarget) -> "SectionTarget | None":
    """The same section in a (possibly edited) text, or None when it cannot be told safely. Same place and tag first; else the same ordinal among identical tags, but only when the
    number of identical tags is unchanged. Never a guess."""
    secs = find_sections(text)
    for s in secs:
        if s.tag_start == target.tag_start and s.tag_text == target.tag_text:
            return s
    same = [s for s in secs if s.tag_text == target.tag_text]
    if len(same) == target.same_count and target.ordinal < len(same):
        return same[target.ordinal]
    return None


def section_body(text: str, s: SectionTarget) -> str:
    """The lyrics of the section: after its tag line, up to the next section tag, without the blank lines around it."""
    body = text[s.body_start:s.body_end]
    if body.startswith("\n"):
        body = body[1:]
    return body.strip("\n").rstrip()


def read_tag(s: SectionTarget) -> tuple:
    """(chosen cue phrases, text of the cues the parser could not read): the cues the tag already has are shown as selected, anything else is kept as it is."""
    p = TP.parse_tag(s.inner)
    cues, original = TP.read_cues(p.cue_text)
    return [c.phrase for c in cues], original


def compose_tag(s: SectionTarget, type_: str, chosen: list, unknown: str) -> str:
    """The tag for the chosen type and cues, with the existing parser and composer. The name keeps its number ("Verse 2") while the type is unchanged."""
    name = s.name if CL.section_key(type_) == s.key else type_
    cue_text = TP.compose_cue_text([CL.BY_PHRASE[p] for p in chosen if p in CL.BY_PHRASE], unknown)
    return TP.build_tag(name, cue_text)


def check_tag(tag: str) -> str:
    """"" when the tag is acceptable, else the reason."""
    if not TP.valid_tag_text(tag):
        return "The tag must be one [ ... ] with no brackets or line breaks inside."
    return ""


PREVIEW_CAPTION_VERSION = "vocal-preview-v2"
VOCAL_PREVIEW_HEAD = (
    "a cappella, vocals only, unaccompanied voice, no instruments, no instrumental backing, no drums, no bass, no guitar, no piano, no synth, no strings, no beat, no music. "
    "Only the human voice is heard, close and dry, with natural breath and room silence between the phrases.")
VOCAL_PREVIEW_INSTRUCTIONS = (
    "SECTION VOCAL PREVIEW ONLY:\n\n"
    "This is an a cappella vocal performance: the human voice only, with no instruments and no backing track at all. Every instrument, drum, bass, synth and rhythm part that "
    "the style reference above names is NOT played in this preview; take from it only the genre feel of the singing, the vocal identity and the emotional character.\n\n"
    "Sing this section with natural phrasing, clear pronunciation and expressive vocal dynamics, emphasizing the selected vocal cues.\n\n"
    "Again: a cappella, voice only, unaccompanied, no instrumental accompaniment of any kind.")


def build_preview_caption(original_style: str, vocal_language: str, selected_cues: list, vocal_settings: "dict | None" = None) -> str:
    """The caption of a SECTION PREVIEW (never of a full song). ACE-Step weighs the caption as one overall picture and degrades when it contradicts itself, so the a cappella
    statement comes FIRST and again at the end, and the sheet's own music style prompt (unchanged) sits in between as a reference for genre feel and vocal identity only. The
    guides do not document negative words, so the wording states the wanted result (a cappella, voice only) and also lists what is absent. No genre is added, no language is named
    (the language travels in the node's own `language` input, which keeps its "auto" detection), and nothing says "single voice" or "solo", so harmonies / choir cues do not
    contradict it. `vocal_language` and `vocal_settings` are accepted for the caller's record and deliberately not written into the text."""
    style = (original_style or "").strip()
    cues = [c.strip() for c in (selected_cues or []) if c and c.strip()]
    parts = [VOCAL_PREVIEW_HEAD]
    if style:
        parts.append("Style reference (genre feel and vocal character only, no instruments from it): " + style)
    parts.append(VOCAL_PREVIEW_INSTRUCTIONS)
    if cues:
        parts.append("Selected vocal cues: " + ", ".join(cues) + ".")
    return "\n\n".join(parts)


def text_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


# ── the request (the snapshot of one preview) ────────────────────────────────────────────────────────────
def make_request(song_id: str, sheet_id: str, sheet_name: str, text: str, target: SectionTarget, type_: str, chosen: list, unknown: str, settings: M.SongSettings,
                 bpm_override: "int | None", duration: int, now: str | None = None) -> dict:
    """Everything one run needs, copied now. `text` is the lyrics of the active sheet read at THIS moment; `target` must be a section that was found in exactly this text."""
    tag = compose_tag(target, type_, chosen, unknown)
    why = check_tag(tag)
    if why:
        raise ValueError(why)
    body = section_body(text, target)
    preview_lyrics = tag + ("\n" + body if body else "")
    if len(find_sections(preview_lyrics)) != 1:                            # a preview is ONE section: never send more than the chosen one
        raise ValueError("The preview text contains more than one section. Nothing was sent.")
    ace = K.compile_lyrics(preview_lyrics).ace
    sd = copy.deepcopy(asdict(settings))
    bpm = int(bpm_override) if bpm_override else int(settings.bpm)
    return {"id": M.new_id("pv_"), "song_id": song_id, "sheet_id": sheet_id, "sheet_name": sheet_name, "created": now or M.now_str(),
            "section_pos": target.tag_start, "section_ordinal": target.ordinal, "section_type": type_, "original_tag": target.tag_text, "preview_tag": tag,
            "source_text_hash": text_hash(text), "section_body_hash": text_hash(body), "preview_lyrics": preview_lyrics, "ace_lyrics": ace, "cues": list(chosen),
            "style_prompt": M.style_tags(settings), "preview_caption": build_preview_caption(M.style_tags(settings), settings.language, chosen, {"vocal": settings.vocal}),
            "caption_version": PREVIEW_CAPTION_VERSION, "song_settings": sd, "bpm_override": int(bpm_override) if bpm_override else None, "effective_bpm": bpm,
            "duration": int(duration)}


@dataclass
class PreviewTake:
    id: str
    song_id: str
    file: str
    created: str
    number: int = 0                       # per song and section type, never reused
    label: str = ""                       # "Chorus Preview 03"
    sheet_id: str = ""
    sheet_name: str = ""
    section_pos: int = 0
    section_ordinal: int = 0
    section_type: str = ""
    original_tag: str = ""
    preview_tag: str = ""
    source_text_hash: str = ""
    section_body_hash: str = ""
    preview_lyrics: str = ""
    ace_lyrics: str = ""
    cues: list = None
    style_prompt: str = ""                # the sheet's own music style prompt, as it was when Generate was pressed
    preview_caption: str = ""             # what was really sent as the caption: that prompt + the preview instructions
    caption_version: str = ""
    song_settings: dict = None
    bpm_override: "int | None" = None
    effective_bpm: int = 0
    duration: int = 0                     # requested
    measured_duration: float = 0.0        # of the real file, 0 = unknown
    settings_used: dict = None            # what was really sent (seeds, steps ...)
    seeds: dict = None
    workflow_name: str = ""
    workflow_snapshot: dict = None
    prompt_id: str = ""
    status: str = "completed"


def _take_from_dict(d: dict) -> PreviewTake:
    names = {f.name for f in fields(PreviewTake)}
    return PreviewTake(**{k: v for k, v in (d or {}).items() if k in names})


# ── storage: songs/<id>/previews/previews.json + the audio files next to it ──────────────────────────────
_lock = threading.RLock()


def previews_dir(song_id: str) -> Path:
    return ST.song_dir(song_id) / "previews"


def _file(song_id: str) -> Path:
    return previews_dir(song_id) / "previews.json"


def load(song_id: str) -> list:
    """The preview takes of a song, oldest first. A missing file is an empty list; an unreadable one is kept aside (never overwritten) and reads as empty."""
    p = _file(song_id)
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return [_take_from_dict(x) for x in d.get("previews", [])]
    except Exception:
        try:
            p.replace(p.with_name(f"previews.corrupt-{int(time.time())}.json"))
        except OSError:
            pass
        return []


def _counters(song_id: str) -> dict:
    p = _file(song_id)
    try:
        return dict(json.loads(p.read_text(encoding="utf-8")).get("counters", {}))
    except Exception:
        return {}


def _write(song_id: str, takes: list, counters: dict) -> None:
    data = {"format": "workflowpad-music-previews", "version": 1, "counters": counters, "previews": [asdict(t) for t in takes]}
    ST._atomic(_file(song_id), json.dumps(data, ensure_ascii=False, indent=1))


def new_file(song_id: str, pid: str, ext: str) -> Path:
    d = previews_dir(song_id)
    d.mkdir(parents=True, exist_ok=True)
    f, i = d / f"{pid}.{ext.lstrip('.') or 'mp3'}", 2
    while f.exists():
        f, i = d / f"{pid}_{i}.{ext.lstrip('.') or 'mp3'}", i + 1
    return f


def add(t: PreviewTake) -> PreviewTake:
    """File a finished preview. The number is the next one for this song and section type; an older preview is never touched."""
    with _lock:
        takes = load(t.song_id)
        counters = _counters(t.song_id)
        key = t.section_type or "Section"
        counters[key] = int(counters.get(key, 0)) + 1
        t.number = counters[key]
        t.label = f"{key} Preview {t.number:02d}"
        takes.append(t)
        _write(t.song_id, takes, counters)
    return t


def path_of(t: PreviewTake) -> "Path | None":
    p = previews_dir(t.song_id) / t.file
    return p if p.exists() else None


def delete(song_id: str, take_id: str) -> None:
    with _lock:
        takes = load(song_id)
        gone = [t for t in takes if t.id == take_id]
        _write(song_id, [t for t in takes if t.id != take_id], _counters(song_id))
    for t in gone:
        p = path_of(t)
        if p:
            try:
                p.unlink()
            except OSError:
                pass


# ── running one preview ──────────────────────────────────────────────────────────────────────────────────
@dataclass
class PreviewResult:
    status: str                 # completed | failed | cancelled
    take: "PreviewTake | None" = None
    error: str = ""
    prompt_id: str = ""


def run_preview(host: str, audio_dir: Path, req: dict, cancel_fn, on_state=None, on_submitted=None, ca=CA, probe=PlayerService.probe_length_ms) -> PreviewResult:
    """Blocking (the UI runs it in an executor). Reads ONLY `req`. Does not touch ComfyUI's memory (a full-song job may be using it), the song's takes or its lyrics."""
    pid, status, err = "", "failed", ""
    try:
        s = M._pick(M.SongSettings, req["song_settings"])
        s.bpm, s.duration = int(req["effective_bpm"]), int(req["duration"])
        if not s.workflow:
            found = ca.scan_workflows(audio_dir)
            if not found:
                raise CA.ComfyError("No ACE-Step 1.5 workflow in comfy_apis/audio")
            s.workflow = found[0].name
        base = ca.load_workflow(audio_dir / s.workflow)
        if (s.language or "auto").lower() == "auto":
            s.language = K.detect_language(req["ace_lyrics"])
        if s.seed < 0:
            s.seed = ca.fresh_seed()
        wf, used = ca.build_workflow(base, s, req.get("preview_caption") or req["style_prompt"], req["ace_lyrics"], filename_prefix="audio/WorkflowPadPreview")
        pid = ca.submit(host, wf)
        if on_submitted:
            on_submitted(pid)
        try:
            entry = ca.wait_for_result(host, pid, cancel_fn, on_state)
        except CA.ComfyError as ex:
            entry = None
            if cancel_fn():
                ca.cancel(host, pid)
                status = "cancelled"
            else:
                err = str(ex)
        if entry is not None:
            e1, files = ca.job_error(entry), ca.audio_outputs(entry)
            if e1 or not files:
                err = e1 or "ComfyUI finished but returned no audio file"
            else:
                f0 = files[0]
                dest = new_file(req["song_id"], req["id"], Path(f0["filename"]).suffix.lstrip(".") or "mp3")
                ca.fetch_file(host, f0, dest)
                ms = probe(str(dest))
                t = PreviewTake(req["id"], req["song_id"], dest.name, req["created"], measured_duration=(ms / 1000.0) if ms else 0.0, settings_used=used, seeds=used.get("seeds", {}),
                                workflow_name=s.workflow, workflow_snapshot=wf, prompt_id=pid,
                                **{k: req[k] for k in ("sheet_id", "sheet_name", "section_type", "original_tag", "preview_tag", "source_text_hash", "section_body_hash", "preview_lyrics",
                                                       "ace_lyrics", "cues", "style_prompt", "preview_caption", "caption_version", "song_settings", "bpm_override", "effective_bpm", "duration")},
                                section_pos=req["section_pos"], section_ordinal=req["section_ordinal"])
                try:
                    add(t)
                    status = "completed"
                except Exception as ex:                         # the audio is on disk and stays there
                    err = f"The preview was generated and its audio was kept as {dest.name}, but the preview list could not be saved: {ex}"
                    return PreviewResult("failed", None, err, pid)
                return PreviewResult("completed", t, "", pid)
    except Exception as ex:
        err = str(ex)
    return PreviewResult(status, None, err, pid)
