"""Quality choices for the ACE-Step workflows: steps / CFG per workflow family, and a memory of them per workflow.

Two families, told apart by the model the workflow itself loads (UNETLoader): a Turbo (distilled) model is made for a handful of steps and CFG 1; a full model (SFT / Base / XL)
is made for many steps and a real CFG. The preset numbers are this program's starting points (the user can type any value: that is the Custom choice), not guarantees of quality.
No UI, no I/O except reading the workflow file.
"""
from __future__ import annotations

import json
from pathlib import Path

DISTILLED, FULL = "distilled", "full"
LEVELS = ("draft", "standard", "high")
PRESETS = {
    DISTILLED: {"draft": (4, 1.0), "standard": (8, 1.0), "high": (12, 1.0)},
    FULL: {"draft": (30, 7.0), "standard": (50, 7.0), "high": (70, 7.0)},
}
_cache: dict = {}


def family_of_workflow(wf: dict) -> str:
    """'distilled' when the workflow loads a Turbo model, else 'full'. A workflow without a model loader is treated like the original default (distilled)."""
    names = [str((n.get("inputs") or {}).get("unet_name", "")).lower() for n in (wf or {}).values() if isinstance(n, dict) and n.get("class_type") == "UNETLoader"]
    if not names:
        return DISTILLED
    return DISTILLED if any("turbo" in x for x in names) else FULL


def family_of_file(path: Path) -> str:
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return DISTILLED
    if key not in _cache:
        try:
            _cache[key] = family_of_workflow(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            _cache[key] = DISTILLED
    return _cache[key]


_NEEDED = ("TextEncodeAceStepAudio1.5", "EmptyAceStep1.5LatentAudio", "KSampler", "SaveAudioAdvanced")


class WorkflowError(RuntimeError):
    """No usable ACE-Step workflow can be assigned."""


def _graph(path: Path) -> "dict | None":
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _explicit_turbo(wf: dict) -> bool:
    """True only when a UNETLoader names a Turbo model. A graph with no UNET is not Turbo."""
    names = [str((n.get("inputs") or {}).get("unet_name", "")).lower() for n in wf.values() if isinstance(n, dict) and n.get("class_type") == "UNETLoader"]
    return any("turbo" in x for x in names)


def _tiled(wf: dict) -> bool:
    return any(isinstance(n, dict) and "tiled" in str(n.get("class_type") or "").lower() for n in wf.values())


def _usable(wf: dict) -> bool:
    counts = {c: 0 for c in _NEEDED}
    for n in wf.values():
        if isinstance(n, dict) and n.get("class_type") in counts:
            counts[n["class_type"]] += 1
    return all(v == 1 for v in counts.values())


def pick_default_file(files) -> "Path | None":
    """ACE-Step 1.5 Turbo, then a non-tiled graph. The file name is only a tie-break, never the reason a workflow is chosen."""
    ranked = []
    for raw in files:
        path = Path(raw)
        wf = _graph(path)
        if wf is None or not _usable(wf):
            continue
        ranked.append((0 if _explicit_turbo(wf) else 1, 1 if _tiled(wf) else 0, path.name.lower(), path))
    if not ranked:
        return None
    ranked.sort()
    return ranked[0][3]


def _program_default(steps, cfg) -> bool:
    """The numbers a new song starts with, before any quality choice."""
    try:
        standard = PRESETS[DISTILLED]["standard"]
        return int(steps) == standard[0] and abs(float(cfg) - standard[1]) < 1e-6
    except (TypeError, ValueError):
        return False


def _num(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 1e-6:
        return str(int(round(number)))
    return f"{number:g}"


def fallback_notice(name: str, steps, cfg) -> str:
    """ACE-Step 1.5 Turbo is not in the folder, so another workflow was assigned with the steps and CFG of its model."""
    return f"ACE-Step 1.5 Turbo was not found. This song is using {name}, with {_num(steps)} steps and CFG {_num(cfg)} for that model."


def params(family: str, level: str) -> tuple:
    """(steps, cfg) of a level. Raises KeyError for 'custom'."""
    return PRESETS[family][level]


def level_of(family: str, steps, cfg) -> str:
    """The level whose numbers these are, else 'custom'."""
    try:
        for lv in LEVELS:
            st, cf = PRESETS[family][lv]
            if int(steps) == st and abs(float(cfg) - cf) < 1e-6:
                return lv
    except (TypeError, ValueError):
        pass
    return "custom"


def switch_workflow(s, old: str, new: str, new_family: str) -> None:
    """The workflow was changed: its own steps / CFG are put away and the new workflow's own are brought back (its Standard level the first time), so a Turbo value never stays
    in a full model's settings."""
    if old == new:
        return
    mem = s.wf_params if isinstance(getattr(s, "wf_params", None), dict) else {}
    if old:
        mem[old] = {"steps": int(s.steps), "cfg": float(s.cfg)}
    got = mem.get(new)
    if isinstance(got, dict) and "steps" in got and "cfg" in got:
        s.steps, s.cfg = int(got["steps"]), float(got["cfg"])
    else:
        s.steps, s.cfg = PRESETS[new_family]["standard"]
    s.wf_params = mem


def adopt_workflow(s, old: str, new: str, new_family: str) -> None:
    """Point a song at `new` only because it had no workflow, or the file it named is gone.

    A file that is still on disk is not passed here. A missing name keeps its steps and CFG in wf_params and loads the new file's own memory, or its Standard level.
    An empty name keeps a saved memory of `new`, and keeps numbers the user already changed. Only the untouched 8 / 1 of a new song is replaced when the file is a full model.
    """
    if not new or old == new:
        return
    if old:
        switch_workflow(s, old, new, new_family)
        return
    mem = s.wf_params if isinstance(getattr(s, "wf_params", None), dict) else {}
    got = mem.get(new)
    if isinstance(got, dict) and "steps" in got and "cfg" in got:
        s.steps, s.cfg = int(got["steps"]), float(got["cfg"])
    elif _program_default(s.steps, s.cfg) and new_family == FULL:
        s.steps, s.cfg = PRESETS[FULL]["standard"]
    s.wf_params = mem


def resolve_workflow(s, files) -> str:
    """Make s.workflow name a file from `files`. Returns a notice when Turbo is absent and another file was assigned, else "".

    A name that is already in `files` is left alone, including its steps and CFG. Raises WorkflowError when nothing usable can be assigned.
    """
    names = {Path(p).name for p in files}
    current = str(getattr(s, "workflow", "") or "")
    if current and current in names:
        return ""
    chosen = pick_default_file(files)
    if chosen is None:
        raise WorkflowError("No ACE-Step 1.5 workflow in comfy_apis/audio")
    graph = _graph(chosen) or {}
    adopt_workflow(s, current, chosen.name, family_of_workflow(graph))
    s.workflow = chosen.name
    if _explicit_turbo(graph):
        return ""
    return fallback_notice(chosen.name, s.steps, s.cfg)


def hint(family: str, steps) -> tuple:
    """(text, warn) shown under the quality row."""
    try:
        n = int(steps)
    except (TypeError, ValueError):
        n = 0
    if family == FULL:
        base = "Full model (SFT / Base / XL): made for about 50 steps and CFG 7. Fewer steps are faster but lower quality."
        return (base + " This is too few steps for it." if n < 20 else base), n < 20
    return "Turbo model (distilled): made for about 8 steps and CFG 1.", False
