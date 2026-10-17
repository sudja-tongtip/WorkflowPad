"""
storage.py — save/load state + story file helpers
"""
__version__ = "20260726_1907"

import json, os, time, uuid
from datetime import datetime
from pathlib import Path
from .constants import SAVE_PATH, STORIES_DIR, CREDS_PATH

def _load() -> dict:
    try:
        if SAVE_PATH.exists():
            return json.loads(SAVE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save(data: dict):
    try:
        SAVE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def load_credentials() -> dict:
    try:
        if CREDS_PATH.exists():
            return json.loads(CREDS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def save_credentials(data: dict):
    try:
        existing = load_credentials()
        existing.update(data)
        CREDS_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def list_stories() -> list[str]:
    return sorted(p.stem for p in STORIES_DIR.glob("*.json"))


# ── Projects ────────────────────────────────────────────────────────────────────────────────────
# A project is one JSON file in STORIES_DIR (the file format is unchanged, "story" stays the internal
# name). Each saved project also carries project_name / created_at / updated_at (ISO, seconds).
# Projects saved before that existed still load; their dates fall back to the file timestamps.

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ts_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def format_project_time(iso: str) -> str:
    """'2026-09-26T14:03:55' -> '2026-09-26 14:03' (display only)."""
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso or "—"


def _atomic_write_json(path: Path, data: dict):
    """Write next to the target first, then replace it — an interrupted save can never leave a
    half-written project file behind (the old file stays intact until the new one is complete)."""
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, indent=2))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def project_exists(name: str) -> bool:
    return (STORIES_DIR / f"{name}.json").is_file()


def list_projects() -> list[dict]:
    """[{"name", "created_at", "updated_at"}], newest updated first. Metadata comes from the file
    when present, otherwise from the file's own timestamps (older projects)."""
    items: list[dict] = []
    for p in STORIES_DIR.glob("*.json"):
        created = updated = ""
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                created = str(d.get("created_at") or "")
                updated = str(d.get("updated_at") or "")
        except Exception:
            pass
        try:
            st = p.stat()
            created = created or _ts_iso(st.st_ctime)
            updated = updated or _ts_iso(st.st_mtime)
        except Exception:
            pass
        items.append({"name": p.stem, "created_at": created, "updated_at": updated})
    items.sort(key=lambda it: it["updated_at"], reverse=True)
    return items


def _existing_created_at(path: Path) -> str:
    """created_at of a project file already on disk: its own metadata, else its creation time."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("created_at"):
            return str(d["created_at"])
    except Exception:
        pass
    try:
        return _ts_iso(path.stat().st_ctime)
    except Exception:
        return ""

def save_story(name: str, story_text: str, char_desc: str,
               shots: list, gallery: dict, num_shots: int,
               scene_desc: str = "", requirement: str = "", story_summary: str = "",
               props_desc: str = "", outline_events: list | None = None,
               outline_clothing_arc: str = "", outline_physical_state: str = "",
               outline_locations: str = "", source_style: str = "",
               source_style_meta: dict | None = None,
               outline_scenario_script: list | None = None,
               outline_source_mode: str = "", outline_source_blocks: list | None = None,
               outline_vnext_a1: dict | None = None, outline_vnext_a2: dict | None = None,
               outline_vnext_a2_manga: dict | None = None,
               fill_design_ledger: str = "", fresh: bool = False) -> dict:
    """Save one project. created_at is written once (first save) and kept on every later save;
    updated_at is set on every save. fresh=True starts the project's history over (used when a New
    Project deliberately replaces an existing one). Returns {"created_at", "updated_at"}."""
    path = STORIES_DIR / f"{name}.json"
    now = _now_iso()
    created = ""
    if not fresh and path.exists():
        created = _existing_created_at(path)
    created = created or now
    data = {
        "name": name, "project_name": name, "created_at": created, "updated_at": now,
        "story_text": story_text, "char_desc": char_desc,
        "scene_desc": scene_desc, "requirement": requirement,
        "story_summary": story_summary,
        "shots": shots, "gallery": gallery, "num_shots": num_shots,
        "saved_at": time.strftime("%Y-%m-%d %H:%M"),
        # Pass A's own structured sheet output (locations/props/events/clothing_arc/physical_state)
        # never had a home in this schema before — only char_desc/scene_desc (the UI textboxes)
        # survived a save. These round-trip through load_story() for regenerate/inspect after reload.
        "props_desc": props_desc, "outline_events": outline_events or [],
        "outline_clothing_arc": outline_clothing_arc,
        "outline_physical_state": outline_physical_state,
        "outline_locations": outline_locations,
        # SOURCE-STYLE-A1: sidecar scalar, may be "" — never embedded in story_text itself.
        "source_style": source_style,
        "source_style_meta": dict(source_style_meta or {}),
        # OUTLINE-A2 / OUTLINE-A1-SOURCE-PRESERVING: A2's scenario_script and A1's new
        # source_mode/source_blocks — missing entirely on an older save (before these existed),
        # never fabricated by Python on load, just left empty.
        "outline_scenario_script": outline_scenario_script or [],
        "outline_source_mode": outline_source_mode or "",
        "outline_source_blocks": outline_source_blocks or [],
        # VNEXT-B-CLOTHING-STATE-FORM-A1 follow-up: A1's production bible, persisted so reopening
        # this story keeps Fill on the vNext engine instead of silently falling back to legacy
        # run_fill_prompts (main_ui.py restores/synthesizes this in _confirm_load).
        "outline_vnext_a1": outline_vnext_a1 or {},
        "outline_vnext_a2": outline_vnext_a2 or {},
        # MANGA-VNEXT-A1 §67: A2 Manga's own schema (comic_beats) persisted
        # separately from outline_vnext_a2 above (micro_beats) — never the same key.
        "outline_vnext_a2_manga": outline_vnext_a2_manga or {},
        # FILL-CREATIVE-DESIGN-LEDGER-A1: this story's Fill Design Ledger — one opaque string
        "fill_design_ledger": fill_design_ledger or "",
    }
    _atomic_write_json(path, data)
    return {"created_at": created, "updated_at": now}

def load_story(name: str) -> dict | None:
    p = STORIES_DIR / f"{name}.json"
    if p.exists():
        try: return json.loads(p.read_text(encoding="utf-8"))
        except Exception: pass
    return None

def delete_story(name: str):
    p = STORIES_DIR / f"{name}.json"
    if p.exists(): p.unlink()

# Same file, project vocabulary.
delete_project = delete_story
