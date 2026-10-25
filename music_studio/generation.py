"""One generation, start to finish, without any UI: prepare the workflow, submit it, wait, fetch the audio, and record the result in the song's own folder.

The snapshot (a Song copy) is all it reads: nothing here looks at what is open in the editor, so a result can only ever land in the song it was started for.
Blocking: the UI runs execute() in an executor.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import comfy_audio as CA
from . import compiler as K
from . import store as ST
from .player import PlayerService
from .model import Song, Take, Run, new_id, now_str


@dataclass
class Result:
    status: str                 # completed | failed | cancelled
    take: Take | None = None
    error: str = ""
    prompt_id: str = ""


def prepare(audio_dir: Path, snap: Song, tags: str, ace_text: str) -> tuple[dict, dict]:
    """(workflow, parameters actually sent). Resolves seed -1 on the snapshot. Raises on a missing / unusable workflow."""
    s = snap.settings
    if not s.workflow:                                  # a song that never opened the settings: use the first ACE-Step workflow found
        found = CA.scan_workflows(audio_dir)
        if not found:
            raise CA.ComfyError("No ACE-Step 1.5 workflow in comfy_apis/audio")
        s.workflow = found[0].name
    base = CA.load_workflow(audio_dir / s.workflow)
    if (s.language or "auto").lower() == "auto":              # the snapshot gets the detected code; the song keeps "auto"
        s.language = K.detect_language(ace_text)
    if s.seed < 0:
        s.seed = CA.fresh_seed()
    return CA.build_workflow(base, s, tags, ace_text)


def execute(host: str, snap: Song, wf: dict, used: dict, tags: str, ace_text: str, cancel_fn, on_state=None, on_submitted=None, snapshot: dict | None = None) -> Result:
    take_id, created = new_id("take_"), now_str()
    pid, status, err, take = "", "failed", "", None
    try:
        CA.free_memory(host)                          # always clear ComfyUI's RAM / VRAM before a song (best effort)
        pid = CA.submit(host, wf)
        if on_submitted:
            on_submitted(pid)
        try:
            entry = CA.wait_for_result(host, pid, cancel_fn, on_state)
        except CA.ComfyError as ex:
            entry = None
            if cancel_fn():
                CA.cancel(host, pid)                      # only OUR job
                status = "cancelled"
            else:
                err = str(ex)
        if entry is not None:
            e1, files = CA.job_error(entry), CA.audio_outputs(entry)
            if e1 or not files:
                err = e1 or "ComfyUI finished but returned no audio file"
            else:
                f0 = files[0]
                dest = ST.new_take_file(snap.id, take_id, Path(f0["filename"]).suffix.lstrip(".") or "mp3")
                CA.fetch_file(host, f0, dest)
                ms = PlayerService.probe_length_ms(str(dest))
                # duration = the MEASURED length of the file, or 0.0 = unknown. The requested length (used["duration"], kept in settings) is never stored as if it were the real one.
                take = Take(take_id, snap.id, dest.name, created, duration=(ms / 1000.0) if ms else 0.0, lyrics=ace_text, tags=tags,
                            settings=used, seeds=used["seeds"], workflow_name=snap.settings.workflow, workflow_snapshot=wf, prompt_id=pid, **(snapshot or {}))
                try:
                    ST.add_take(take)
                    status = "completed"
                except Exception as ex:                   # the audio is already on disk: it is kept, only the take list could not be updated
                    take = None
                    err = f"The song was generated and its audio was kept as {dest.name}, but the take list could not be saved: {ex}"
    except Exception as ex:
        err = str(ex)
    if status != "completed":
        try:
            ST.add_run(Run(new_id("run_"), snap.id, status, created, err, pid, used.get("seeds", {})))
        except Exception as ex:                           # a damaged takes.json must not turn a finished generation into a crash
            err = f"{err} (the run could not be recorded either: {ex})"
    return Result(status, take, err, pid)
