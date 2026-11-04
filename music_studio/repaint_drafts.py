"""Repaint drafts: one small record per source take. Not part of song.json, and never the waveform samples.

The key is project id + song id + take id. Writing one take does not drop another take's draft.
"""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ID = "library"
_FIELDS = ("caption", "start", "end", "zoom", "view_start", "view_end")


def drafts_path(root) -> Path:
    return Path(root) / "repaint_drafts.json"


def draft_key(project_id: str, song_id: str, take_id: str) -> str:
    return f"{project_id}\n{song_id}\n{take_id}"


def _clean(draft: dict) -> dict:
    out = {}
    if "caption" in draft:
        out["caption"] = str(draft.get("caption") or "")
    for k in _FIELDS:
        if k == "caption" or k not in draft or draft[k] is None:
            continue
        try:
            out[k] = float(draft[k])
        except (TypeError, ValueError):
            continue
    return out


def load_all(root) -> dict:
    p = drafts_path(root)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        bak = p.with_name(p.name + ".bak")
        try:
            p.replace(bak)
        except Exception:
            pass
        return {}
    return data if isinstance(data, dict) else {}


def _save_all(root, data: dict) -> None:
    p = drafts_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def get_draft(root, project_id: str, song_id: str, take_id: str) -> dict | None:
    d = load_all(root).get(draft_key(project_id, song_id, take_id))
    if not isinstance(d, dict):
        return None
    return _clean(d)


def put_draft(root, project_id: str, song_id: str, take_id: str, draft: dict) -> None:
    """Store the editable fields only. Peaks, samples and unknown keys are dropped."""
    data = load_all(root)
    data[draft_key(project_id, song_id, take_id)] = _clean(draft or {})
    _save_all(root, data)
