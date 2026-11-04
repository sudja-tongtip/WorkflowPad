"""Cover and Repaint jobs for the Kaola ACE-Step nodes. No UI.

The normal Generate workflow is never built here. These jobs load audio_cover_generation.json and
audio_repaint.json, upload the source audio, and submit that graph. They do not run the model by
themselves: execute() only queues when the caller invokes it.

A missing Kaola node raises JobError before anything is queued and before a take is created.
"""
from __future__ import annotations

import copy
import json
import uuid
import urllib.error
import urllib.request
from pathlib import Path

from . import comfy_audio as CA
from . import store as ST
from . import waveform as WF
from .generation import Result
from .model import Take, Run, SongSettings, new_id, now_str
from .player import PlayerService

PROJECT_ID = "library"
COVER_FILE = "audio_cover_generation.json"
REPAINT_FILE = "audio_repaint.json"
COVER_CLASS = "ACE_STEP_Cover"
REPAINT_CLASS = "ACE_STEP_Repaint"
LOAD_CLASS = "LoadAudio"
COVER_PREFIX = "audio/WorkflowPadCover"
REPAINT_PREFIX = "audio/WorkflowPadRepaint"
VOCAL_LANGS = {"unknown", "auto", "en", "zh", "ja", "ko", "es", "fr", "de", "ru", "pt", "it", "bn"}
KAOLA_FORMATS = {"flac", "mp3", "wav"}
NODE_PROBE = (COVER_CLASS, REPAINT_CLASS, LOAD_CLASS, "SaveAudio", "SaveAudioAdvanced", "PreviewAudio")
SAVER_KEYS = {
    "SaveAudio": ("audio", "filename_prefix"),
    "SaveAudioAdvanced": ("audio", "filename_prefix", "format", "format.quality"),
}


class JobError(RuntimeError):
    """A sentence for the user. Nothing was queued."""


def generation_route(settings) -> str:
    """'cover' when Cover is on, otherwise the existing Generate path. The remembered source is not consulted."""
    return "cover" if bool(getattr(settings, "cover_enabled", False)) else "normal"


def vocal_language(code: str) -> str:
    """Kaola's vocal_language combo. Thai and any other code it does not list are sent as auto. The lyrics are still sent."""
    c = (code or "auto").strip().lower()
    return c if c in VOCAL_LANGS else "auto"


def cover_audio_path(settings: SongSettings, resolve_take) -> Path:
    """The file Cover will upload. resolve_take(song_id, take_id) -> path or None, this library only."""
    if (settings.cover_source or "external") == "project":
        if not settings.cover_song_id or not settings.cover_take_id:
            raise JobError("Choose a song and a take from this library.")
        found = resolve_take(settings.cover_song_id, settings.cover_take_id)
        if not found or not Path(found).is_file():
            raise JobError("The chosen take has no audio file.")
        return Path(found)
    raw = (settings.cover_external or "").strip()
    if not raw or not Path(raw).is_file():
        raise JobError("Choose an audio file that exists.")
    return Path(raw)


def installed_nodes(host: str, names=NODE_PROBE) -> set[str]:
    """Class types ComfyUI reports in /object_info. This does not run a model."""
    return {n for n in names if CA._node_exists(host, n)}


def choose_saver(present: set[str]) -> str | None:
    """SaveAudioAdvanced when this ComfyUI has it, else the older SaveAudio, else no saver (PreviewAudio only)."""
    if "SaveAudioAdvanced" in present:
        return "SaveAudioAdvanced"
    if "SaveAudio" in present:
        return "SaveAudio"
    return None


def prefer_audio_file(files: list[dict]) -> dict | None:
    """The history file to download. An output file wins over a temp preview. Does not change ComfyUI's own list."""
    if not files:
        return None
    saved = [f for f in files if (f.get("type") or "output") == "output"]
    return (saved or files)[0]


def _nodes(wf: dict, class_type: str) -> list[str]:
    return [nid for nid, node in wf.items() if isinstance(node, dict) and node.get("class_type") == class_type]


def _one(wf: dict, class_type: str) -> str:
    ids = _nodes(wf, class_type)
    if len(ids) != 1:
        raise JobError(f"The workflow must contain one {class_type} node (found {len(ids)}).")
    return ids[0]


def _new_id(wf: dict) -> str:
    n = 1
    while str(n) in wf:
        n += 1
    return str(n)


def _load_api(audio_dir: Path, filename: str) -> dict:
    path = Path(audio_dir) / filename
    if not path.is_file():
        raise JobError(f"Missing workflow file: {filename}")
    try:
        return CA.load_workflow(path)
    except CA.ComfyError as ex:
        raise JobError(str(ex)) from None


def saver_format(fmt: str, quality: str) -> tuple[str, str]:
    """SaveAudioAdvanced accepts flac, mp3 and opus. A song set to wav is saved as flac."""
    if (fmt or "").lower() == "wav":
        return "flac", ""
    return CA.output_format(fmt, quality)


def _saver_inputs(class_type: str, audio_link, prefix: str, fmt: str, quality: str) -> dict:
    data = {"audio": audio_link, "filename_prefix": prefix}
    if class_type == "SaveAudioAdvanced":
        f, q = saver_format(fmt, quality)
        data["format"] = f
        if q:
            data["format.quality"] = q
    allowed = SAVER_KEYS[class_type]
    return {k: data[k] for k in allowed if k in data}


def stabilize_output(wf: dict, mode: str, present: set[str], prefix: str, fmt: str, quality: str) -> str | None:
    """Make the submitted prompt save a file when this ComfyUI can. The workflow file on disk is not modified.

    Cover: the deprecated SaveAudio node is replaced by SaveAudioAdvanced when that node exists.
    Repaint: PreviewAudio stays, and a saver is added beside it when one exists.
    """
    saver = choose_saver(present)
    if mode == "cover":
        ids = _nodes(wf, "SaveAudio") or _nodes(wf, "SaveAudioAdvanced")
        if not ids:
            if saver is None and not _nodes(wf, "PreviewAudio"):
                raise JobError("ComfyUI has no SaveAudioAdvanced, SaveAudio or PreviewAudio node, so the cover cannot be saved.")
            return None
        nid = ids[0]
        audio = (wf[nid].get("inputs") or {}).get("audio")
        if saver is None:
            if wf[nid].get("class_type") not in present and "PreviewAudio" not in present:
                raise JobError("ComfyUI cannot save or preview the cover audio. Nothing was queued.")
            keep = wf[nid].get("inputs") or {}
            wf[nid]["inputs"] = {k: keep[k] for k in ("audio", "filename_prefix") if k in keep}
            wf[nid]["inputs"]["filename_prefix"] = prefix
            return wf[nid].get("class_type")
        wf[nid]["class_type"] = saver
        wf[nid]["inputs"] = _saver_inputs(saver, audio, prefix, fmt, quality)
        return saver
    if saver is None and not _nodes(wf, "PreviewAudio"):
        raise JobError("ComfyUI has no audio saver and the repaint workflow has no PreviewAudio node.")
    if saver:
        rep = _one(wf, REPAINT_CLASS)
        nid = _new_id(wf)
        wf[nid] = {"inputs": _saver_inputs(saver, [rep, 0], prefix, fmt, quality), "class_type": saver,
                   "_meta": {"title": "WorkflowPad save"}}
    return saver


def upload_audio(host: str, path) -> str:
    """Upload into ComfyUI's input folder. Returns the name LoadAudio should use. The source file is only read."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as ex:
        raise JobError(f"Cannot read the source audio: {ex}") from None
    boundary = "----WorkflowPad" + uuid.uuid4().hex
    file_part = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data
    def field(name, value):
        return (
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}"
        ).encode()
    body = file_part + field("overwrite", "true") + field("type", "input") + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        CA._url(host, "/upload/image"), data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            info = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as ex:
        raise JobError(f"ComfyUI rejected the audio upload (HTTP {ex.code}).") from None
    except urllib.error.URLError as ex:
        raise JobError(f"Cannot reach ComfyUI at {host}: {ex.reason}") from None
    name = str(info.get("name") or "")
    sub = str(info.get("subfolder") or "")
    if not name:
        raise JobError("ComfyUI accepted the upload but did not return a file name.")
    return f"{sub}/{name}" if sub else name


def _require(present: set[str], *names: str) -> None:
    missing = [n for n in names if n not in present]
    if missing:
        raise JobError(
            "ComfyUI is missing " + " and ".join(missing) + ". Nothing was queued and no take was created. "
            "Install the Kaola ACE-Step nodes (and LoadAudio) and start ComfyUI.")


def _apply_cover(wf: dict, settings: SongSettings, caption: str, lyrics: str, instrumental: bool, audio_name: str) -> dict:
    """Write only the values Cover sends. audio_cover_strength, guidance_scale and the model paths stay as the workflow file left them."""
    cover = wf[_one(wf, COVER_CLASS)]["inputs"]
    load = wf[_one(wf, LOAD_CLASS)]["inputs"]
    lang = vocal_language(settings.language)
    cover["caption"] = caption
    cover["lyrics"] = lyrics or ""
    cover["seed"] = int(settings.seed)
    cover["inference_steps"] = int(settings.steps)
    cover["bpm"] = int(settings.bpm) if int(settings.bpm) > 0 else 0
    cover["keyscale"] = settings.keyscale or ""
    cover["timesignature"] = str(settings.timesignature or "")
    cover["vocal_language"] = lang
    text = (lyrics or "").strip().lower()
    cover["instrumental"] = bool(instrumental) or text in ("", "[instrumental]")
    fmt = (settings.format or "").lower()
    if fmt in KAOLA_FORMATS:
        cover["audio_format"] = fmt
    load["audio"] = audio_name
    return {"language": lang, "seed": int(settings.seed), "steps": int(settings.steps),
            "bpm": int(cover["bpm"]), "format": cover.get("audio_format") or fmt or "flac"}


def _apply_repaint(wf: dict, caption: str, start: float, end: float, audio_name: str) -> None:
    """Caption, the two times, and the uploaded file name. Seed, steps, format and every other widget stay as the file shipped them. No lyrics input."""
    node = wf[_one(wf, REPAINT_CLASS)]["inputs"]
    node["caption"] = caption
    node["repainting_start"] = float(start)
    node["repainting_end"] = float(end)
    node.pop("lyrics", None)
    wf[_one(wf, LOAD_CLASS)]["inputs"]["audio"] = audio_name


def _output_format(wf: dict, fallback: str) -> str:
    for node in wf.values():
        if isinstance(node, dict) and node.get("class_type") == "SaveAudioAdvanced":
            return str((node.get("inputs") or {}).get("format") or fallback)
    return fallback


def prepare_cover(audio_dir, settings: SongSettings, *, host: str, audio_path, caption: str, lyrics: str,
                  instrumental: bool = False, present: set[str] | None = None, upload_fn=None) -> tuple[dict, dict]:
    """(workflow, parameters actually sent). Uploads the source audio. Does not submit the prompt."""
    caption = (caption or "").strip()
    if not caption:
        raise JobError("Write a music caption before Cover.")
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise JobError("The cover source audio file is missing.")
    present = set(present) if present is not None else installed_nodes(host)
    _require(present, COVER_CLASS, LOAD_CLASS)
    name = (upload_fn or upload_audio)(host, audio_path)
    wf = copy.deepcopy(_load_api(audio_dir, COVER_FILE))
    meta = _apply_cover(wf, settings, caption, lyrics or "", instrumental, name)
    stabilize_output(wf, "cover", present, COVER_PREFIX, settings.format, settings.audio_quality)
    external = "" if (settings.cover_source or "") == "project" else str(audio_path)
    used = {"mode": "cover", "seeds": {"seed": meta["seed"]}, "source_project_id": PROJECT_ID,
            "source_song_id": settings.cover_song_id if settings.cover_source == "project" else "",
            "source_take_id": settings.cover_take_id if settings.cover_source == "project" else "",
            "source_external": external, "caption": caption, "lyrics_snapshot": lyrics or "",
            "workflow": COVER_FILE, "generated_at": now_str(), "bpm": meta["bpm"], "steps": meta["steps"],
            "format": _output_format(wf, meta["format"]), "language": meta["language"]}
    return wf, used


def prepare_repaint(audio_dir, settings: SongSettings, *, host: str, audio_path, caption: str, start: float, end: float,
                    duration: float, source_song_id: str = "", source_take_id: str = "",
                    present: set[str] | None = None, upload_fn=None) -> tuple[dict, dict]:
    """(workflow, parameters actually sent). Refuses invalid times before any upload."""
    err = WF.validate_repaint(path_exists=Path(audio_path).is_file(), duration=duration, start=start, end=end, caption=caption)
    if err:
        raise JobError(err)
    present = set(present) if present is not None else installed_nodes(host)
    _require(present, REPAINT_CLASS, LOAD_CLASS)
    name = (upload_fn or upload_audio)(host, audio_path)
    wf = copy.deepcopy(_load_api(audio_dir, REPAINT_FILE))
    _apply_repaint(wf, caption.strip(), float(start), float(end), name)
    stabilize_output(wf, "repaint", present, REPAINT_PREFIX, settings.format, settings.audio_quality)
    seed = int(wf[_one(wf, REPAINT_CLASS)]["inputs"].get("seed", -1))
    used = {"mode": "repaint", "seeds": {"seed": seed}, "source_project_id": PROJECT_ID,
            "source_song_id": source_song_id or "", "source_take_id": source_take_id or "", "source_external": "",
            "caption": caption.strip(), "repaint_start": float(start), "repaint_end": float(end),
            "workflow": REPAINT_FILE, "generated_at": now_str(), "format": _output_format(wf, settings.format or "")}
    return wf, used


def execute(host: str, snap, wf: dict, used: dict, *, cancel_fn, on_state=None, on_submitted=None,
            snapshot: dict | None = None, lyrics: str = "", tags: str = "") -> Result:
    """Submit, wait, and store a new take on the destination song. The source audio is never written.

    Same shape as generation.execute, without the native-workflow check. A failure records a run and creates no take.
    """
    take_id, created = new_id("take_"), now_str()
    pid, status, err, take = "", "failed", "", None
    try:
        if cancel_fn():
            return Result("cancelled", None, "cancelled", "")
        CA.free_memory(host)
        if cancel_fn():
            return Result("cancelled", None, "cancelled", "")
        pid = CA.submit(host, wf)
        if on_submitted:
            on_submitted(pid)
        try:
            entry = CA.wait_for_result(host, pid, cancel_fn, on_state)
        except CA.ComfyError as ex:
            entry = None
            if cancel_fn():
                CA.cancel(host, pid)
                status = "cancelled"
            else:
                err = str(ex)
        if entry is not None:
            e1, files = CA.job_error(entry), CA.audio_outputs(entry)
            chosen = prefer_audio_file(files)
            if e1 or not chosen:
                err = e1 or "ComfyUI finished but returned no audio file"
            else:
                dest = ST.new_take_file(snap.id, take_id, Path(chosen["filename"]).suffix.lstrip(".") or "mp3")
                CA.fetch_file(host, chosen, dest)
                ms = PlayerService.probe_length_ms(str(dest))
                take = Take(take_id, snap.id, dest.name, created, duration=(ms / 1000.0) if ms else 0.0,
                            lyrics=lyrics or "", tags=tags or used.get("caption") or "", settings=used,
                            seeds=used.get("seeds") or {}, workflow_name=str(used.get("workflow") or ""),
                            workflow_snapshot=wf, prompt_id=pid, **(snapshot or {}))
                try:
                    ST.add_take(take)
                    status = "completed"
                except Exception as ex:
                    take = None
                    err = f"The song was generated and its audio was kept as {dest.name}, but the take list could not be saved: {ex}"
    except Exception as ex:
        err = str(ex)
    if status != "completed":
        try:
            ST.add_run(Run(new_id("run_"), snap.id, status, created, err, pid, (used or {}).get("seeds", {})))
        except Exception as ex:
            err = f"{err} (the run could not be recorded either: {ex})"
    return Result(status, take, err, pid)
