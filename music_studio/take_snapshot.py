"""What a take remembers about the generation that made it, and how to read it back. Pure functions (no UI, no I/O).

A new take stores, at the moment Generate is pressed: the sheet it came from, the lyrics exactly as typed in the editor (`editor_lyrics`), the compiled ACE-Step lyrics that were sent
(`lyrics`), the style tags, the options of the sheet as they were (`song_settings`, a full SongSettings dict: seed -1 / language "auto" stay as the user had them), the resolved language,
the effective seeds and parameters (`settings` / `seeds`) and the workflow that was submitted. Nothing here reads the sheet that is open now.

An older take has fewer of these. It is never filled in from the current sheet: what exists is shown, what does not is shown as "Not saved", and the values that DID reach ComfyUI are
read back from its saved workflow (that is real data about the past generation).
"""
from __future__ import annotations

import copy

from . import comfy_audio as CA
from .model import SongSettings, Take, build_basic_style
from .sheets import settings_from_dict, settings_to_dict

MISSING = "Not saved"


def snapshot_extra(sheet_id: str, sheet_name: str, editor_lyrics: str, settings_before: dict, resolved_language: str) -> dict:
    """The Take fields that describe the generation. Deep copies: later edits of the sheet cannot reach them."""
    return {"sheet_id": sheet_id, "sheet_name": sheet_name, "editor_lyrics": editor_lyrics, "song_settings": copy.deepcopy(settings_before), "resolved_language": resolved_language}


def has_snapshot(t: Take) -> bool:
    return bool(t.song_settings) and bool(t.editor_lyrics or t.sheet_id)


def sent_values(t: Take) -> dict:
    """Values that were actually written into the submitted workflow (empty when the take has no workflow snapshot)."""
    wf = t.workflow_snapshot or {}
    out = {}
    try:
        m = CA.find_nodes(wf)
        te = wf[m["text"][0]]["inputs"]
        ks = wf[m["sampler"][0]]["inputs"]
        sv = wf[m["save"][0]]["inputs"]
        out = {"bpm": te.get("bpm"), "keyscale": te.get("keyscale"), "timesignature": te.get("timesignature"), "duration": te.get("duration"), "language": te.get("language"),
               "seed": te.get("seed"), "text_cfg": te.get("cfg_scale"), "temperature": te.get("temperature"), "top_p": te.get("top_p"), "top_k": te.get("top_k"), "min_p": te.get("min_p"),
               "ksampler_seed": ks.get("seed"), "steps": ks.get("steps"), "cfg": ks.get("cfg"), "format": sv.get("format")}
    except Exception:
        out = {}
    return {k: v for k, v in out.items() if v is not None}


def _fallback(t: Take) -> dict:
    u = t.settings or {}
    out = {k: u[k] for k in ("bpm", "duration", "steps", "cfg", "format", "language") if k in u}
    sd = t.seeds or u.get("seeds") or {}
    if "text_encode" in sd:
        out["seed"] = sd["text_encode"]
    if "ksampler" in sd:
        out["ksampler_seed"] = sd["ksampler"]
    return out


def effective(t: Take) -> dict:
    """The generation parameters that really went to ComfyUI: from the saved workflow when there is one, else from the recorded `settings`."""
    return sent_values(t) or _fallback(t)


def describe(t: Take) -> dict:
    """{"rows": [(label, value | None)], "legacy": bool, "notes": [str]} for the read-only 'View Generation Settings'. None = not saved."""
    eff = effective(t)
    before = t.song_settings or {}
    rows = [("Take", f"Take {t.number:02d}  ·  {t.created}"),
            ("Sheet", t.sheet_name or (None if not t.sheet_id else t.sheet_id)),
            ("Lyrics as written in the editor", t.editor_lyrics or None),
            ("Lyrics sent to ACE-Step (compiled)", t.lyrics or None),
            ("Style / tags sent", t.tags or None),
            ("Workflow", t.workflow_name or None),
            ("BPM", eff.get("bpm")), ("Key", eff.get("keyscale")), ("Time signature", eff.get("timesignature")), ("Duration requested (s)", eff.get("duration")),
            ("Language sent", t.resolved_language or eff.get("language")),
            ("Text-encode seed", eff.get("seed")), ("KSampler seed", eff.get("ksampler_seed")), ("Steps", eff.get("steps")), ("CFG", eff.get("cfg")), ("Text CFG", eff.get("text_cfg")),
            ("Temperature", eff.get("temperature")), ("Top P", eff.get("top_p")), ("Top K", eff.get("top_k")), ("Min P", eff.get("min_p")), ("Audio format", eff.get("format"))]
    if before:
        rows += [("Seed setting in the sheet", before.get("seed")), ("Seed mode", before.get("seed_mode")), ("Style mode", before.get("style_mode"))]
    notes = []
    legacy = not has_snapshot(t)
    if legacy:
        notes.append("This take was made before generation snapshots existed. Only what was recorded is shown; “Not saved” means it was not kept, and nothing is guessed from your current sheet.")
    return {"rows": rows, "legacy": legacy, "notes": notes}


def editable_copy(t: Take) -> tuple:
    """(sheet name, lyrics, settings dict, warnings) for 'Open as Editable Sheet'. A take with a snapshot gives back exactly its editor lyrics and options. An older take gives back
    what it has: its compiled lyrics and the options that were sent (read from its saved workflow), with a warning for each substitution."""
    warns = []
    name = f"Take {t.number:02d} Copy"
    if t.editor_lyrics:
        lyrics = t.editor_lyrics
    else:
        lyrics = t.lyrics
        warns.append("The lyrics as typed in the editor were not saved with this take; the compiled lyrics that were sent to ACE-Step are used instead.")
    if t.song_settings:
        settings = copy.deepcopy(t.song_settings)
    else:
        eff = effective(t)
        s = SongSettings()
        for k in ("bpm", "keyscale", "timesignature", "duration", "language", "seed", "ksampler_seed", "steps", "cfg", "text_cfg", "temperature", "top_p", "top_k", "min_p", "format"):
            if k in eff and eff[k] is not None:
                try:
                    cur = getattr(s, k)
                    setattr(s, k, type(cur)(eff[k]))
                except (TypeError, ValueError):
                    pass
        if t.workflow_name:
            s.workflow = t.workflow_name
        if t.tags:
            s.style_mode, s.style_prompt = "advanced", t.tags            # the tags that were really sent, so the copy sounds like the take
        if "seed" in eff:
            s.seed, s.seed_mode = int(eff["seed"]), "same" if eff.get("seed") == eff.get("ksampler_seed") else "fixed"
        settings = settings_to_dict(s)
        warns.append("The options of the sheet were not saved with this take. They were rebuilt from what was sent to ComfyUI (genre / mood / vocal fields are not recoverable; the style is set as an Advanced prompt).")
    if not lyrics.strip():
        warns.append("This take has no lyrics saved at all: the copy starts empty.")
    return name, lyrics, settings, warns
