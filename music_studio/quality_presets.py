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
