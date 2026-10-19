"""
main_ui.py — Flet main(page): state, logic, layout

Pipeline terminology (see constants.STAGE_*). Legacy shorthand still used in module names, saved-project keys
and machine identifiers is kept for compatibility:
    Story Editor (Groom) -> Story Bible (A1) -> Beat Sheet (A2) -> Shot List (B)
    -> Image Prompts, Normal / Manga (Fill) -> Consistency Check (Checker) -> Visual Direction (Director)
"""
__version__ = "20260925_checker_director_split_a1"

import asyncio, copy, hashlib, json, logging, os, random, re, shutil, subprocess, sys, threading, time, uuid
if sys.stdout is not None:                       # a windowed (no-console) EXE has no stdout
    sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path

# One session log next to this package. Wiped every program start.
if getattr(sys, "frozen", False):
    _LOG_FILE = Path(sys.executable).resolve().parent / "session.log"
else:
    _LOG_FILE = Path(__file__).resolve().parent / "session.log"
_log_lock = threading.Lock()

def _log_to_file(msg: str):
    """Append one record to session.log. Used for LLM outline/fill logs too."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    body = (msg if isinstance(msg, str) else str(msg)).rstrip()
    try:
        with _log_lock:
            with open(_LOG_FILE, "a", encoding="utf-8", newline="\n") as f:
                f.write(f"{ts} {body}\n")
                f.flush()
    except Exception as ex:
        print(f"[session.log write failed] {ex}", flush=True)

try:
    _LOG_FILE.unlink(missing_ok=True)
except OSError:
    pass
_log_to_file(f"=== session start ===  file={_LOG_FILE}")

log = logging.getLogger("workflowpad")
log.setLevel(logging.DEBUG)
log.propagate = False
for _h in list(log.handlers):
    log.removeHandler(_h)
    try:
        _h.close()
    except Exception:
        pass

class _SessionLogHandler(logging.Handler):
    def emit(self, record):
        try:
            _log_to_file(self.format(record))
        except Exception:
            self.handleError(record)

_sh = _SessionLogHandler()
_sh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
log.addHandler(_sh)

def _clog(msg: str):
    """Log to session file + print to console."""
    _log_to_file(msg)
    print(msg)
    if sys.stdout is not None: sys.stdout.flush()

# ── Outline phase ────────────────────────────────────────────────────────────
# เดิมสถานะของ Review Outline ถูกคุมด้วยการตั้ง .visible/.disabled กระจายอยู่หลายจุด ผลคือปุ่ม
# Confirm ใช้ได้ตลอดเวลา แม้ตอน Pass A/B ยังวิ่งอยู่ — กดแล้ว Fill เริ่มบนช็อตที่ยังไม่มีจริง
# ตารางนี้เป็นแหล่งเดียวที่บอกว่า "เฟสไหนทำอะไรได้" และเป็น pure function จึงเทสต์ได้ตรง ๆ
OUTLINE_PHASES = ("IDLE", "PASS_A", "REVIEW_A", "WAITING_COUNT", "PASS_B", "READY", "FAILED")


def outline_phase_caps(phase: str) -> dict:
    """เฟสหนึ่ง → ปุ่ม/ตัวบ่งชี้ที่ใช้ได้ในเฟสนั้น

    loading  = โชว์วงหมุน
    editor / regen / save / fill = ปุ่มนั้นใช้ได้ไหม
    fields   = ช่องแก้ไขช็อตใน Review เปิดให้พิมพ์ได้ไหม
    create   = ปุ่ม Create shot/pages (รัน B จาก A1/A2 ที่ผู้ใช้ตรวจแล้ว) ใช้ได้ไหม
    ระหว่างสร้าง outline ไม่มีอะไรให้ commit — Cancel/Stop เป็นทางออกเดียวเสมอ

    REVIEW_A = Story Bible + Beat Sheet เสร็จแล้ว หยุดรอให้ผู้ใช้แก้ Character/Events ก่อนกด Create — save ได้
    (เก็บ A1/A2) แต่ยังไม่มีช็อตจึงสร้าง render prompt ไม่ได้"""
    p = (phase or "IDLE").upper()
    if p in ("PASS_A", "WAITING_COUNT", "PASS_B"):
        return {"loading": True, "editor": False, "regen": False,
                "save": False, "fill": False, "fields": False, "create": False}
    if p == "REVIEW_A":
        return {"loading": False, "editor": False, "regen": False,
                "save": True, "fill": False, "fields": False, "create": True}
    if p == "READY":
        return {"loading": False, "editor": True, "regen": True,
                "save": True, "fill": True, "fields": True, "create": True}
    if p == "FAILED":
        # ล้มแล้วมีทางเดียวคือสร้างใหม่หรือยกเลิก — ห้าม save/fill ราวกับ outline ใช้ได้
        return {"loading": False, "editor": False, "regen": True,
                "save": False, "fill": False, "fields": False, "create": False}
    return {"loading": False, "editor": False, "regen": True,
            "save": False, "fill": False, "fields": False, "create": False}


_EVENT_ROW_RE = re.compile(r"<(E\d+)>(.*?)</\1>", re.DOTALL)
_EVENT_OPEN_RE = re.compile(r"<E\d+>")


def parse_events_edit(text: str, macro_ids: list[str]) -> tuple[list[dict] | None, str]:
    """Read the user-edited Events tab back into A2 micro beats. Pure function.

    Only `<E1>text</E1>` rows count; every other line (the macro-event context text, headers)
    is ignored. A row's tag name is its macro parent and its position is its identity: the n-th
    `<E1>` row becomes `E1.n`, so B keeps getting the same `E1.1`-style ids as before, and the
    sequence follows the line order. Returns (beats, "") or (None, English error message)."""
    text = text or ""
    opens = len(_EVENT_OPEN_RE.findall(text))
    rows = [(m.group(1), m.group(2).strip()) for m in _EVENT_ROW_RE.finditer(text)]
    if opens != len(rows):
        n = opens - len(rows)
        return None, f"{n} <E..> tag(s) are not closed — every <E1> row needs its own </E1>."
    rows = [(t, c) for t, c in rows if c]
    known = list(macro_ids)
    unknown = list(dict.fromkeys(t for t, _ in rows if t not in known))
    if unknown:
        return None, (f"<{unknown[0]}> has no matching macro event (valid: {', '.join(known)}). "
                      f"Fix or delete that row.")
    used = {t for t, _ in rows}
    missing = [m for m in known if m not in used]
    if missing:
        return None, f"Every macro event needs at least one row — missing: {', '.join(missing)}."
    counters: dict[str, int] = {}
    beats: list[dict] = []
    for tag, content in rows:
        counters[tag] = counters.get(tag, 0) + 1
        beats.append({"id": f"{tag}.{counters[tag]}", "parent": tag, "content": content})
    return beats, ""


def can_continue_to_fill(phase: str, shot_count: int, llm_busy: bool) -> bool:
    """ปุ่มที่ disabled ไว้ยังถูกเรียกได้จากจังหวะ event ของ Flet — handler ต้องกันเองด้วย"""
    return bool(outline_phase_caps(phase)["fill"]) and shot_count > 0 and not llm_busy


def _props_to_str(v) -> str:
    """props ควรเป็น string เสมอ แต่บาง LLM/บาง state.json เก่าเคยเก็บมาเป็น list — coerce กลับเป็น
    string ทุกจุดที่รับค่ามาจากภายนอก (LLM output, raw JSON import, saved state) ไม่งั้น
    props_field.value.strip() ที่ Image Prompts จะพังด้วย AttributeError"""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "\n".join(str(x) for x in v)
    return str(v) if v else ""

import flet as ft

from .constants  import (APP_ROOT, APP_ID, APP_NAME, APP_VERSION, C, STYLE_MAP, _SIZE_MAP, _ALL_SIZES, TEMP_DIR, API_DIR, IMAGE_EDIT_DIR,
                          LLM_DIR, CAPTION_DIR, GENRE_MAP, DIALOGUE_STYLE_MAP, DIALOGUE_LANG_MAP,
                          LANG_MAP, style_string_for_scale)
from .comfy_api  import run_one, run_identity_pass, apply_dialogue_overlay, apply_dual_text_overlay, run_joycaption, run_img2img, pick_reference_image
# LLM-ENGINE-SPLIT-A1 / CLEAN-ENGINE-ROUTING-A2-B-FILL-A1: llm_engine.py (the original 20.7k-line
# monolith) was split by static reachability trace into stage-specific live-code-only modules —
# nothing imports the original file any more (archived at archive_unused/llm_engine.py). A1/A2/B/
# Fill each have their own module; Manga reuses A1/A2 and has only dedicated B Manga / Fill Manga
# modules; Groom lives in llm_engine_groom.py; Checker and Director now have their own clean engines;
# shared workflow/chat/vision/format helpers remain in llm_engine_tools.py.
# llm_engine_vn.py itself is no longer imported (archived alongside it).
from .llm_engine_tools import (
    scan_llm_workflows,
    run_llm_chat, _call_llm_node,
    run_outline_chat_message,
    run_vision_check, run_insert_shot,
    _build_prompt_format_rule, ANIMAL_SAFETY_RULE, set_llm_params, set_llm_debug_logging,
    _assign_shot_scale,
    chat_camera_semantics_rule,
    FILL_ENGINE_MINIMAL_V3,
    _normalize_v3_prompt_block_spacing,
    _director_format_structure_ok, _LOCK_CONTRACT_ENGINES, normalize_fill_engine, _detect_prompt_format,
)
from .llm_engine_checker import run_consistency_check
from .llm_engine_director import run_visual_direction
from .llm_engine_a1 import run_production_reference as _run_a1_legacy
from .llm_engine_a1_novel import run_a1_routed as _run_a1_routed
# A1 engine: Story/Static auto-routing (llm_engine_a1_novel -> llm_engine_a1_static). Set environment A1_ENGINE=legacy to use the old A1.
run_production_reference = _run_a1_legacy if os.environ.get("A1_ENGINE", "").strip().lower() == "legacy" else _run_a1_routed
from .llm_engine_a2 import run_beat_decomposition
from .llm_engine_static_pose import run_static_pose_fill
from .llm_engine_a1_router import classify_source as _classify_source
from .llm_engine_b import run_shot_planning
from .llm_engine_fill import run_normal_render_prompt
from .llm_engine_b_manga import run_manga_shot_planning, manga_vnext_page_to_legacy_dict
from .llm_engine_fill_manga import run_manga_render_prompt, FILL_ENGINE_MANGA_VNEXT
from .llm_engine_groom import (
    run_story_editing, run_story_chat_edit,
    outline_cast_review_rows, apply_cast_review_outfits, detect_sheet_field_leaked_as_name,
    groom_final_check_issues,
)

from .visual_direction import (
    VISUAL_PRESETS, VISUAL_PRESET_OPTIONS, resolve_visual_preset, normalize_preset,
    visual_direction_for_preset,
)
from .help_docs import load_help_tabs
from . import comfy_api as _comfy_api_mod
from . import llm_overrides as _llm_overrides
from . import fill_conversation as _fc_tokens
from .storage    import (_load, _save, list_stories, list_projects, project_exists, format_project_time,
                         save_story, load_story, delete_story, load_credentials, save_credentials)
from .ui_helpers import po, br, divider, _dlg_to_str, _pil_overlay_text, _pil_overlay_texts, pil_overlay_applies, block_prompt_format_rule, runtime_dialogue_prompt, assemble_runtime_prompt, section, collapsible_section, group_header
from .ui_lightbox import build_lightbox
from .ui_shot_row import build_shot_col, set_fill_ledger_getter

logging.getLogger("asyncio").setLevel(logging.CRITICAL)

def _launch_image_editor(image_path: str):
    """Launch image_editor as a new process with the given image path.
    Works both in dev (.py) and when built as .exe (sys.frozen)."""
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        exe = next((p for p in (here / "ImageEditor.exe", here / "image_editor.exe", here / "ImageEditor", here / "image_editor")
                    if p.exists()), None)
        if exe is None:
            raise FileNotFoundError(f"ImageEditor.exe not found next to {sys.executable}")
        subprocess.Popen([str(exe), image_path], cwd=str(here))
    else:
        script = Path(__file__).parent.parent / "image_editor.py"
        subprocess.Popen([sys.executable, str(script), image_path])


def _print_versions():
    import importlib, sys
    mods = ["story_generator.llm_engine_tools", "story_generator.llm_engine_checker",
            "story_generator.llm_engine_director", "story_generator.llm_engine_a1",
            "story_generator.llm_engine_a1_novel", "story_generator.llm_engine_a1_static",
            "story_generator.llm_engine_a2", "story_generator.llm_engine_b", "story_generator.llm_engine_fill",
            "story_generator.llm_engine_b_manga", "story_generator.llm_engine_fill_manga",
            "story_generator.llm_engine_groom", "story_generator.comfy_api",
            "story_generator.ui_shot_row", "story_generator.constants"]
    lines = ["=" * 50, f"Story Generator — Python {sys.version.split()[0]}"]
    for mod in mods:
        try:
            m = importlib.import_module(mod)
            lines.append(f"  {mod.split('.')[-1]}: v{getattr(m, '__version__', '?')}")
        except Exception:
            pass
    from . import main_ui as _self
    lines.append(f"  main_ui: v{getattr(_self, '__version__', '?')}")
    lines.append("=" * 50)
    _clog("\n".join(lines))

_print_versions()


def main(page: ft.Page):
    page.title = f"{APP_NAME} {APP_VERSION}"
    _icon = APP_ROOT / "icons" / "story_generator.ico"
    if _icon.exists():
        page.window.icon = str(_icon)
    page.bgcolor = C["bg"]
    page.fonts = {}
    page.theme_mode = ft.ThemeMode.LIGHT

    saved = _load()
    _creds = load_credentials()
    from .constants import SAVE_PATH as _SP
    print(f"[STATE] SAVE_PATH={_SP} exists={_SP.exists()}")

    # ── State ─────────────────────────────────────────────────────────
    shots_data: list[dict]  = saved.get("shots", [])
    _outline_shots_backup: list[dict] = []   # snapshot of shots_data before an outline (re)generation, restored on Cancel
    _outline_review_is_new: list[bool] = [True]  # True=สร้างใหม่, False=แก้ไขเก่า
    gallery_images: dict    = {i: saved.get("gallery", {}).get(str(i), [])
                                for i in range(len(shots_data))}
    selected_api: list[str] = [saved.get("selected_api", "")]
    selected_llm: list[str] = [saved.get("selected_llm", "")]
    sel_size    : list[str] = [saved.get("sel_size", "[16:9] 2304 × 1296")]
    output_dir  : list[str] = [str(TEMP_DIR)]
    _is_running   : list[bool] = [False]
    _seed_lock    : list[bool] = [saved.get("seed_lock", False)]
    _locked_seed  : list[int]  = [saved.get("locked_seed", random.randint(0, 2**53))]
    _append_scene_desc: list[bool] = [saved.get("append_scene_desc", False)]
    _manga_page_mode  : list[bool] = [saved.get("manga_page_mode", False)]
    # Manga vNext shares A1 + A2 Sequential with the normal pipeline, then branches only at
    # B Manga -> Fill Manga. _manga_page_mode is the older independent UI flag and remains separate.
    _manga_vnext_mode : list[bool] = [saved.get("manga_vnext_mode", False)]
    _manga_vnext_final_pages: list[int | None] = [saved.get("manga_vnext_final_pages") or None]
    _id_pass      : list[bool] = [saved.get("id_pass", False)]
    _id_ref_image : list[str]  = [saved.get("id_ref_image", "")]
    _id_workflow  : list[str]  = [saved.get("id_workflow", "")]
    _inpaint_mode      : list[bool] = [False]  # ตั้งใจไม่จำค่า "เปิด" ข้ามเซสชัน — เปิดค้างไว้ตั้งแต่ก่อนหน้าโดยไม่รู้ตัว
                                                # แล้วมา Run All ทับหลายๆ shot โดยไม่ได้ตั้งใจ ต้องกดเปิดเองทุกครั้งที่จะใช้
    _inpaint_workflow  : list[str]  = [saved.get("inpaint_workflow", "")]
    _inpaint_ref_source: list[str]  = [saved.get("inpaint_ref_source", "prev_shot")]  # "prev_shot" | "shot1"
    _cur_story_name: list[str] = [saved.get("story_name", "")]
    _custom_sys   : list[str]  = [saved.get("custom_sys", "")]
    _grok_api_key : list[str]  = [_creds.get("grok_api_key", "") or saved.get("grok_api_key", "")]
    # migrate key เก่าจาก saved_story.json → credentials ถ้ายังไม่มีใน credentials
    if not _creds.get("grok_api_key") and saved.get("grok_api_key"):
        save_credentials({"grok_api_key": saved["grok_api_key"]})
    _regen_sys_custom: list[str] = [saved.get("regen_sys_custom", "")]
    _grok_model   : list[str]  = [saved.get("grok_model", "grok-4-latest")]
    _story_summary: list[str]  = [saved.get("story_summary", "")]
    # events จาก outline Pass A — เดิมเป็นตัวแปร local ใน _do_generate_outline ใช้เสร็จแล้วทิ้ง
    # เก็บไว้เพื่อโชว์ในแท็บ Events ของ Review Outline และให้รอดข้ามการปิด/เปิดแอป
    _outline_events: list[list] = [list(saved.get("outline_events") or [])]
    _outline_clothing_arc: list[str] = [str(saved.get("outline_clothing_arc") or "")]
    _outline_physical_state: list[str] = [str(saved.get("outline_physical_state") or "")]
    # ข้อเท็จจริงของสถานที่จาก Pass A — Fill ใช้เป็นความจริงของฉาก แทนการเสกเอง
    _outline_locations: list[str] = [str(saved.get("outline_locations") or "")]
    # OUTLINE-A2: Scenario Script จาก Pass A2 — list ของ {"id","parent","content","shot_intent"},
    # 1 unit = 1 shot พอดี ส่งต่อให้ run_outline_shots_pass เป็น scenario_units แทน Full Story
    # โปรเจกต์เก่าไม่มีคีย์นี้ = list ว่าง = รอบ B กลับไปใช้ Full Story ตามพฤติกรรมเดิมทุกอย่าง
    _outline_scenario_script: list[list] = [list(saved.get("outline_scenario_script") or [])]
    # OUTLINE-A1-SOURCE-PRESERVING: new A1 canonical representation — source_mode ("STORY"/"SCENE",
    # LLM-decided) plus the ordered, verbatim source blocks A2 actually consumes (replaces the old
    # terse [SPINE]/[COVERAGE] events as the Events-tab-and-A2 authority on the NEW path). Empty
    # string/list = legacy project (no A1 segmentation ever ran for it) — Events tab and A2 both
    # fall back to the old _outline_events representation exactly as before this round.
    _outline_source_mode: list[str] = [str(saved.get("outline_source_mode") or "")]
    _outline_source_blocks: list[list] = [list(saved.get("outline_source_blocks") or [])]
    # Outline Chat: (sys_p, user_p, raw response) จริงของ Outline B ครั้งล่าสุด — เก็บไว้ให้ปุ่ม
    # "Outline Chat" คุยต่อจาก conversation จริง ไม่ใช่ session ใหม่ที่มี system prompt ของตัวเอง
    # ไม่ persist ข้ามการปิด/เปิดแอป (หายไปพร้อม session เดิมของ backend อยู่แล้ว คุยต่อไม่ได้จริง)
    _outline_chat_capture: list[dict] = [{}]
    _outline_chat_session_id: list[str] = [""]
    _loops_per_shot : list[int] = [saved.get("loops_per_shot", 1)]
    _loops_per_story: list[int] = [saved.get("loops_per_story", 1)]
    # default เป็น "none" ไม่ใช่ "realistic" — เดิมผู้ใช้ที่ไม่เคยแตะ dropdown เลยจะโดนใส่
    # "photorealistic, a photograph of, cinematic film grain, sharp focus" ให้ทุกช็อตโดยไม่รู้ตัว
    # RENDER-STYLE-PERSIST-A1: Style is a cross-project user preference (like the Grok key), not
    # per-story content, so it lives in credentials.json and survives new projects/new outlines —
    # _creds takes priority over the per-session saved_story.json snapshot.
    _sel_style    : list[str]  = [_creds.get("render_style") or saved.get("image_style", "none")]
    _source_style : list[str]  = [saved.get("source_style", "")]   # SOURCE-STYLE-A1 (see Groom/Outline A capture below)
    # Where _source_style came from, so Outline A1 knows whether it may replace it:
    # {"origin": "groom"|"a1"|"user"|"", "src": sha1 of the raw source it was derived from}.
    # No meta (older file) = origin "" = unknown provenance → A1 may refresh it.
    _source_style_meta : list[dict] = [dict(saved.get("source_style_meta") or {})]
    _sel_genre    : list[str]  = [saved.get("genre", "none")]
    _sel_genre2   : list[str]  = ["none"]      # Secondary Genre is hidden in every page and always None (single genre); a saved value is ignored
    _outline_mode   : list[str]  = [saved.get("outline_mode", "cinematic")]
    _scene_detail   : list[str]  = [saved.get("scene_detail", "standard")]
    _dialogue_style : list[str] = [saved.get("dialogue_style", "speech_bubble")]
    _dialogue_dd_ref: list = [None]   # filled with dialogue_style_dd once it exists
    _yolo_choice: list[str] = [saved.get("yolo_choice", "")]     # "" = ask, "no" = never ask again
    _yolo_session_skip: list[bool] = [False]
    _yolo_notice_shown: list[bool] = [False]
    _dialogue_lang  : list[str] = [saved.get("dialogue_lang", "en") if saved.get("dialogue_lang") in LANG_MAP else "en"]
    _desc_lang      : list[str] = [saved.get("desc_lang") or saved.get("dialogue_lang") or "en"]
    _source_lang    : list[str] = [saved.get("source_lang") or _desc_lang[0]]
    if _desc_lang[0] not in LANG_MAP:
        _desc_lang[0] = "en"
    if _source_lang[0] not in LANG_MAP:
        _source_lang[0] = _desc_lang[0]
    _narration_type : list[str] = [saved.get("narration_type", "caption")]  # "caption" | "bridge"
    _test_steps   : list[int]  = [3]
    _run_steps    : list[int]  = [saved.get("run_steps", -1)]
    _ext_editor   : list[str]  = [saved.get("ext_editor", "")]
    # "sectioned" ถูกยุบเข้า "sectioned_detailed" — map ค่าเก่าตอนโหลด ไม่งั้น dropdown จะว่าง
    # The Prompt Format dropdown is hidden (only Narrative is used), so a style saved by an older build must not stay in force unseen.
    _prompt_style : list[str]  = ["narrative"]   # "narrative" | "tag_style"
    # VISUAL PRESET: the single first-pass visual-direction authority, shared by Outline Settings and the
    # Fill dialog, used by Normal Fill and Manga Fill. A project saved before it existed maps its old
    # Shot Scale preset to the nearest preset; nothing else from the old Fill controls is read.
    _visual_preset: list[str] = [
        saved.get("visual_preset") if saved.get("visual_preset") in VISUAL_PRESETS
        else (saved.get("shot_scale_preset") if saved.get("shot_scale_preset") in ("epic", "intimate") else "natural")]
    # Derived from the preset (never user-set): the Outline-assigned per-shot distance categories
    # (_assign_shot_scale) and the Director still read a scale keyword.
    _shot_scale_preset: list[str] = [resolve_visual_preset(_visual_preset[0])["shot_scale"] or "balanced"]
    # FILL-VNEXT-LOCK-CONTRACT-A1: this used to be hardcoded to FILL_ENGINE_MINIMAL_V3 — a lie, since
    # the only Fill path that actually runs is run_fill_vnext, which stamps its real engine/form onto
    # each shot itself (shot["_fill_engine"]/shot["prompt_style"]). Leave this "" so every downstream
    # caller (Director, vision-check) that still reads _fill_engine[0] falls through to each shot's
    # own real stamp instead of asserting an engine that never ran.
    _fill_engine: list[str] = [""]
    # "new" = write each shot from scratch (default) · "continue" = edit previous prompt when the scene did not change
    # UI hides Fill Mode and forces "new". Dropdowns + continue path stay for rollback:
    # show the Fill Mode fields again and set _FILL_MODE_FORCE_NEW = False.
    _FILL_MODE_FORCE_NEW = True
    _fill_mode: list[str] = [
        "new" if _FILL_MODE_FORCE_NEW else (
            saved.get("fill_mode", "new") if saved.get("fill_mode") in ("new", "continue") else "new"
        )
    ]
    # เดินรอบ Prompt Checker / Director ต่อท้าย fill อัตโนมัติ (สวิตช์ใน dialog ของ Fill)
    _auto_check_after_fill: list[bool] = [bool(saved.get("auto_check_after_fill", False))]
    _auto_director_after_fill: list[bool] = [bool(saved.get("auto_director_after_fill", False))]
    # ต่อท้ายรอบข้อความ ให้ลงมือวาดต่อได้เลย: "none" = จบแค่ prompt · "test" = Test All Shots
    # (พรีวิวเร็ว 1 รูป/ช็อต) · "run" = Run All (ของจริงเต็มรอบ) — ตั้งไว้เพื่อสั่ง fill แล้วเดินออกจากเครื่องได้
    _after_fill_images: list[str] = [saved.get("after_fill_images", "none")
                                     if saved.get("after_fill_images") in ("none", "test", "run") else "none"]
    _director_lighting: list[str] = [saved.get("director_lighting", "enhance")]
    # ชื่อตัวเลือกเปลี่ยนไป (light/push → balanced/cinematic, on → natural, scene/sharpen/freeze →
    # natural/dynamic/peak) แต่ state ที่ user เซฟไว้เป็นชื่อเก่า — แปลงตอนอ่าน จะได้ไม่เด้งกลับ default
    _DIR_BEAUTY_ALIAS = {"light": "balanced", "push": "cinematic"}
    _DIR_ENV_ALIAS = {"on": "natural", "strong": "emphasized"}
    _DIR_MOTION_ALIAS = {"scene": "natural", "sharpen": "dynamic", "freeze": "peak"}
    _director_beauty: list[str] = [_DIR_BEAUTY_ALIAS.get(saved.get("director_beauty", "balanced"),
                                                         saved.get("director_beauty", "balanced"))]
    _director_environment: list[str] = [_DIR_ENV_ALIAS.get(saved.get("director_environment", "natural"),
                                                           saved.get("director_environment", "natural"))]
    _director_motion: list[str] = [_DIR_MOTION_ALIAS.get(saved.get("director_motion", "natural"),
                                                         saved.get("director_motion", "natural"))]
    _director_camera: list[str] = [saved.get("director_camera_energy", "natural")]
    _director_preset: list[str] = [saved.get("director_preset", "natural")]
    _director_extra_dds: list = []   # lighting/beauty/environment/motion — เติมหลังสร้าง widget
    _comfy_host   : list[str]  = [saved.get("comfy_host", "127.0.0.1:8188")]
    _groom_creativity  : list[str] = [saved.get("groom_creativity", "balanced")]  # faithful|balanced|creative
    # Groom Chat workspace: the original source CREATE/New starts from, the working draft, and the visible chat.
    # Old projects without these keys simply start in the initial Story Editor state.
    _groom_text  : list[str]  = [saved.get("groom_draft", "") or ""]   # the working draft (latest editor contents)
    _groom_orig  : list[str]  = [saved.get("groom_original", "") or ""]
    _groom_chat  : list[dict] = [{"role": m.get("role", "groom"), "text": str(m.get("text", ""))}
                                 for m in (saved.get("groom_chat") or []) if isinstance(m, dict)]
    _groom_session: list[str] = [saved.get("groom_session", "") or ""]   # Comfy chat session started by New; Groom Chat continues it
    _groom_session_story: list[str] = [saved.get("groom_session_story", "") or ""]   # the story the session last saw (to detect manual edits)
    _groom_session_age: list[int] = [int(saved.get("groom_session_age", 0) or 0)]    # chat-only turns since that story appeared
    _groom_grok_msgs: list[dict] = [m for m in (saved.get("groom_grok_messages") or []) if isinstance(m, dict)]   # Grok's conversation (no session on xAI)
    _groom_active: list[bool] = [False]   # a Groomed Story exists in the dialog (Chat + New are available)
    _groom_busy  : list[bool] = [False]   # a Groom CREATE / chat edit is running
    # ภาษาของ Groom เท่านั้น — แยกจาก desc_lang กลาง เพราะ user อยากลอง groom เป็นอีกภาษาโดยไม่
    # กระทบ dropdown ภาษาอื่นทั้งหน้า Outline/Fill ค่าเริ่มต้นตาม desc_lang ไว้ก่อนถ้ายังไม่เคยเลือก
    _groom_lang  : list[str] = [saved.get("groom_lang") if saved.get("groom_lang") in LANG_MAP else _desc_lang[0]]
    _llm_tokens_groom  : list[int] = [saved.get("llm_tokens_groom",   12288)]
    _llm_tokens_outline: list[int] = [saved.get("llm_tokens_outline", 6144)]
    # n_ctx ที่บังคับให้ workflow ใช้ — workflow json บางไฟล์ตั้งไว้แค่ 16384 ซึ่งเป็น input+output
    # รวมกัน พอ input โต output ก็หดจนช็อตหาย (ดู _llm_n_ctx ใน llm_engine)
    _llm_n_ctx: list[int] = [saved.get("llm_n_ctx", 0)]
    _llm_tokens_fill   : list[int] = [saved.get("llm_tokens_fill",    8192)]
    _llm_tokens_chat   : list[int] = [saved.get("llm_tokens_chat",    8000)]
    _llm_tokens_story_a: list[int] = [saved.get("llm_tokens_story_a", 8192)]
    # จำนวนช็อตต่อ 1 call ในรอบ B — 0 = ปิด (ขอทั้งเรื่องในคำตอบเดียวเหมือนเดิม)
    # มีไว้เพราะเพดานช็อตไม่ได้อยู่ที่ prompt แต่อยู่ที่ output ต้องอยู่ใน n_ctx ของโมเดล
    _outline_batch: list[int] = [saved.get("outline_batch", 0)]
    # รอบ B แบบ ledger: บันทึกสั้น ๆ ต่อช็อต (เหตุการณ์/สถานะ) แล้วให้ fill เป็นคนบรรยายภาพ
    # ยิงคำขอเดียวไม่แบ่งชุด จึงไม่มีปัญหาช็อตหายจาก output ถูกตัด
    # ตอนนี้ใช้โหมดนี้อย่างเดียว — สวิตช์กับ Scene Detail ถูกซ่อนไว้ (ยังคงตัว widget ไว้
    # เพราะโค้ด save/sync อ้างถึง และเส้นทาง detailed ในเอนจินยังอยู่ครบถ้าจะกลับมาใช้)
    _outline_compact: list[bool] = [True]
    _llm_timeout       : list[int] = [saved.get("llm_timeout", 120)]
    _grok_temperature  : list[float] = [saved.get("grok_temperature", 0.7)]
    _llm_debug_logging : list[bool] = [bool(saved.get("llm_debug_logging", False))]
    _llm_use_program   : list[bool] = [saved.get("llm_settings_source", "program") != "workflow"]      # True = the program's tested LLM values, False = the workflow file's own (llm_overrides)
    _caption_wf        : list[str] = [saved.get("caption_workflow", "")]      # "" = Auto: a JoyCaption workflow in comfy_apis/caption (the short one first)
    # apply saved settings to modules
    _comfy_api_mod.set_comfy_host(_comfy_host[0])
    set_llm_params(_llm_tokens_outline[0], _llm_tokens_fill[0],
                   _llm_timeout[0], _grok_temperature[0], _llm_tokens_groom[0],
                   _llm_tokens_chat[0], _llm_tokens_story_a[0],
                   _llm_n_ctx[0], _custom_sys[0])
    set_llm_debug_logging(_llm_debug_logging[0])
    _llm_overrides.set_mode(_llm_overrides.PROGRAM if _llm_use_program[0] else _llm_overrides.WORKFLOW)
    _stop_event = asyncio.Event()
    llm_busy: list[bool] = [False]
    # True ระหว่างลูป fill→check→director ที่เดินทีละช็อต: runner ของ check/director ถูก
    # เขียนไว้ให้เป็นงานเดี่ยว จบแล้วคืนสถานะ UI ทุกครั้ง (ซ่อน spinner, เปิดปุ่ม, รีเซ็ต Stop)
    # พอถูกเรียกในลูป การคืนสถานะของช็อตแรกจึงดับ spinner ของทั้งรอบ และปลดล็อกปุ่มกลางคัน
    _chained_run: list[bool] = [False]

    # ── Input fields (สร้างก่อนเพราะ shot_row ต้องการ) ─────────────────
    story_field = ft.TextField(
        value=saved.get("story_text", ""),
        hint_text="Enter your story here...",
        multiline=True, expand=True,
        text_size=12,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=10, right=10, top=8, bottom=8),
    )
    story_summary_field = ft.TextField(
        value=saved.get("story_summary", ""),
        hint_text="AI-generated summary (auto-filled on Outline)...",
        multiline=True, min_lines=2, max_lines=4,
        text_size=11,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=10, right=10, top=8, bottom=8),
    )
    char_field = ft.TextField(
        value=saved.get("char_desc", ""),
        hint_text="Main character descriptions (optional)",
        multiline=True, min_lines=3, max_lines=5,
        text_size=12,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=10, right=10, top=8, bottom=8),
    )
    scene_field = ft.TextField(
        value=saved.get("scene_desc", ""),
        hint_text="Main scene or environment...",
        multiline=True, min_lines=3, max_lines=5,
        text_size=12,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=10, right=10, top=8, bottom=8),
    )
    props_field = ft.TextField(
        value=_props_to_str(saved.get("props_desc", "")),
        hint_text="Recurring objects/props (optional) — locked canonical description per item...",
        multiline=True, min_lines=3, max_lines=5,
        text_size=12,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=10, right=10, top=8, bottom=8),
    )
    requirement_field = ft.TextField(
        value=saved.get("requirement", ""),
        hint_text="Special instructions (optional)...",
        multiline=True, min_lines=2, max_lines=4,
        text_size=12,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=10, right=10, top=8, bottom=8),
    )

    def _on_summary_change(e):
        _story_summary[0] = story_summary_field.value
        _save_state()
    story_summary_field.on_change = _on_summary_change

    # ── Status widgets ────────────────────────────────────────────────
    # one line, ellipsized: a long error (for example a B format fault) must never push the toolbar icons or the Stop button around; the full text is in AI Logs
    status_lbl    = ft.Text("Ready", size=11, color=C["text2"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.RIGHT, expand=True)
    progress_ring = ft.ProgressRing(width=16, height=16, color=C["accent"], visible=False)
    llm_prog      = ft.ProgressRing(width=14, height=14, color=C["accent"], visible=False)

    # B-TRUNCATION-SALVAGE-A1: separate from status_lbl (which gets overwritten constantly by
    # every other in-progress status line) — this sticks until a fresh Outline run or Retry B
    # explicitly clears it, so the user doesn't miss that B's shot count was silently repaired.
    _b_salvage_notice: list[str] = [""]
    # one line, ellipsized, and it takes only the free toolbar space — a long message must never push
    # the status / Stop button off the toolbar (full text is in the tooltip)
    b_salvage_lbl = ft.Text("", size=11, color=C["amber"], weight=ft.FontWeight.W_600,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True)
    b_salvage_banner = ft.Row(
        [ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=14, color=C["amber"]), b_salvage_lbl],
        spacing=4, visible=False, expand=True, tooltip="Cleared by starting a new Outline or Retry B",
    )

    def _set_b_salvage_notice(msg: str):
        _b_salvage_notice[0] = msg
        b_salvage_lbl.value = msg
        b_salvage_banner.visible = bool(msg)
        b_salvage_banner.tooltip = (msg + "\n\n" if msg else "") + "Cleared by starting a new Outline or Retry B"

    def _clear_b_salvage_notice():
        _set_b_salvage_notice("")

    def _toast(msg: str, color: str = C["amber"], duration: int = 3000) -> None:
        """A SnackBar (a box at the bottom of the window) that disappears by itself after `duration` ms. For notices that need a glance, not a decision."""
        snack = ft.SnackBar(content=ft.Text(msg, color="#111827", size=12, weight=ft.FontWeight.W_600), bgcolor=color, duration=duration)
        def _gone(e, _s=snack):
            try: page.overlay.remove(_s)
            except ValueError: pass
        snack.on_dismiss = _gone
        page.overlay.append(snack)
        snack.open = True
        page.update()

    # ── LLM selector ─────────────────────────────────────────────────
    _GROK_OPTION = "[Grok API]"
    _llm_list = [_GROK_OPTION] + scan_llm_workflows()
    _llm_init = selected_llm[0] if selected_llm[0] in _llm_list else _llm_list[0]
    if _llm_init: selected_llm[0] = _llm_init

    llm_dd = ft.Dropdown(
        value=_llm_init,
        options=[ft.dropdown.Option(m) for m in _llm_list],
        hint_text="Select LLM",
        text_size=11, height=38, expand=True,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    # GROOM-PATCH-B1: groom_backend_hint_lbl/_update_groom_backend_hint are defined later (inside
    # the Groom dialog section) — this mutable cell lets llm_changed call into it without a
    # NameError, since llm_dd can change before the Groom dialog has ever been built.
    _groom_backend_hint_updater: list = [None]
    def llm_changed(e):
        selected_llm[0] = llm_dd.value or ""
        _save_state()
        if _groom_backend_hint_updater[0]:
            _groom_backend_hint_updater[0]()
    # Flet 0.85.3: Dropdown.on_change ไม่ค่อยยิงเสมอไป (เหตุผลเดียวกับที่ shot-scale dropdown อื่นๆ
    # ต้องใช้ on_select) — ตัวนี้เป็นสาเหตุที่ค่า LLM ที่เลือกไม่ถูกจำข้าม session
    llm_dd.on_select = llm_changed

    # ── Style dropdown ────────────────────────────────────────────────
    style_dd = ft.Dropdown(
        value=_sel_style[0],
        options=[ft.dropdown.Option(key=k, text=label) for k, (label, _) in STYLE_MAP.items()],
        text_size=12, height=36, expand=True,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def _style_changed(e):
        _sel_style[0] = style_dd.value or "none"; _save_state()
        save_credentials({"render_style": _sel_style[0]})
        _update_style_desc()
        # Mirror into the Outline dialog's dropdown now: _sync_style() treats a dropdown that differs from
        # _sel_style as "just changed", so a stale outline_style_dd would otherwise win at the next Run.
        try:
            outline_style_dd.value = style_dd.value
            if outline_style_dd.page:
                outline_style_dd.update()
        except Exception:
            pass
    style_dd.on_change = _style_changed
    style_dd.on_select = _style_changed

    # Explanation of the selected Style, shown under the dropdown (the words a style adds come from STYLE_MAP).
    _STYLE_SPECIAL_DESC = {
        "none": "No style words are added to the image prompt.",
    }
    style_desc_lbl = ft.Text("", size=10, color=C["text3"])

    def _style_desc_text(key: str) -> str:
        if key == "as_source":
            _ss = (_source_style[0] or "").strip()
            if _ss:
                return "Uses the style captured from your story: " + (_ss if len(_ss) <= 140 else _ss[:137] + "…")
            return "Uses the art style your story states. None captured yet, so no style is added."
        if key in _STYLE_SPECIAL_DESC:
            return _STYLE_SPECIAL_DESC[key]
        kw = (STYLE_MAP.get(key) or ("", ""))[1]
        return f"Adds: {kw}" if kw else _STYLE_SPECIAL_DESC["none"]

    def _update_style_desc():
        style_desc_lbl.value = _style_desc_text(style_dd.value or _sel_style[0] or "none")
        try:
            if style_desc_lbl.page:
                style_desc_lbl.update()
        except RuntimeError:
            pass
    _update_style_desc()

    # ── Size dropdown ─────────────────────────────────────────────────
    size_dd = ft.Dropdown(
        value=sel_size[0],
        options=[ft.dropdown.Option(lbl) for lbl, *_ in _ALL_SIZES],
        text_size=11, height=38, expand=True,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def size_changed(e):
        sel_size[0] = size_dd.value or "[16:9] 2304 × 1296"; _save_state()
    size_dd.on_change = size_changed

    # ── API selector ──────────────────────────────────────────────────
    def scan_apis() -> list[str]:
        return sorted(p.name for p in API_DIR.glob("*.json")
                      if "story" not in p.name and "saved" not in p.name
                      and "llm" not in p.name.lower())

    def scan_edit_apis() -> list[str]:
        """Workflows that need a source image (comfy_apis/image_edit): Inpaint / Img2Img and the Identity Pass."""
        return sorted(p.name for p in IMAGE_EDIT_DIR.glob("*.json")
                      if "story" not in p.name and "saved" not in p.name and "llm" not in p.name.lower())

    _api_list = scan_apis()
    _api_init = selected_api[0] if selected_api[0] in _api_list else (_api_list[0] if _api_list else None)
    if _api_init: selected_api[0] = _api_init

    api_dd = ft.Dropdown(
        value=_api_init,
        options=[ft.dropdown.Option(a) for a in _api_list],
        hint_text="Select Image Workflow",
        text_size=11, height=38, expand=True,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def api_changed(e):
        selected_api[0] = api_dd.value or ""; _save_state()
    api_dd.on_change = api_changed

    def rescan(e):
        new_list = scan_apis()
        api_dd.options = [ft.dropdown.Option(a) for a in new_list]
        page.update()

    # ── Save state ────────────────────────────────────────────────────
    def _safe_num_shots() -> int:
        try: return int(num_shots_field.value or "5")
        except ValueError: return 5

    def _save_state():
        _save({
            "shots": shots_data,
            "gallery": {str(i): v for i, v in gallery_images.items()},
            "selected_api": selected_api[0],
            "selected_llm": selected_llm[0],
            "story_name": _cur_story_name[0],
            "custom_sys": _custom_sys[0],
            "loops_per_shot": _loops_per_shot[0],
            "loops_per_story": _loops_per_story[0],
            "story_text": story_field.value,
            "story_summary": story_summary_field.value,
            "outline_events": _outline_events[0],
            "outline_clothing_arc": _outline_clothing_arc[0],
            "outline_physical_state": _outline_physical_state[0],
            "outline_locations": _outline_locations[0],
            "outline_scenario_script": _outline_scenario_script[0],
            "outline_source_mode": _outline_source_mode[0],
            "outline_source_blocks": _outline_source_blocks[0],
            "source_style": _source_style[0],
            "source_style_meta": _source_style_meta[0],
            "outline_vnext_a1": _outline_vnext_a1[0],
            "outline_vnext_a2": _outline_vnext_a2[0],
            "outline_vnext_a2_manga": None,  # legacy key intentionally cleared; A2 is shared
            "fill_design_ledger": _fill_design_ledger[0],
            "manga_vnext_mode": _manga_vnext_mode[0],
            "manga_vnext_final_pages": _manga_vnext_final_pages[0],
            "char_desc": char_field.value,
            "scene_desc": scene_field.value,
            "props_desc": props_field.value,
            "requirement": requirement_field.value,
            "groom_req": groom_req_field.value,
            "groom_creativity": _groom_creativity[0],
            "groom_lang": _groom_lang[0],
            "groom_original": _groom_orig[0],
            "groom_draft": _groom_text[0],
            "groom_chat": list(_groom_chat),
            "groom_session": _groom_session[0],
            "groom_session_story": _groom_session_story[0],
            "groom_session_age": _groom_session_age[0],
            "groom_grok_messages": list(_groom_grok_msgs),
            "outline_req": _outline_review_req.value,
            "sel_size": sel_size[0],
            "seed_lock": _seed_lock[0],
            "locked_seed": _locked_seed[0],
            "append_scene_desc": _append_scene_desc[0],
            "manga_page_mode": _manga_page_mode[0],
            "manga_page_forced": _manga_page_forced[0],
            "prompt_style_pre_manga": _prompt_style_pre_manga[0],
            "id_pass": _id_pass[0],
            "id_ref_image": _id_ref_image[0],
            "id_workflow": _id_workflow[0],
            "inpaint_mode": _inpaint_mode[0],
            "inpaint_workflow": _inpaint_workflow[0],
            "inpaint_ref_source": _inpaint_ref_source[0],
            "image_style": _sel_style[0],
            "genre": _sel_genre[0],
            "genre2": _sel_genre2[0],
            "outline_mode": _outline_mode[0],
            "scene_detail": _scene_detail[0],
            "dialogue_style": _dialogue_style[0],
            "yolo_choice": _yolo_choice[0],
            "dialogue_lang": _dialogue_lang[0],
            "desc_lang": _desc_lang[0],
            "source_lang": _source_lang[0],
            "narration_type": _narration_type[0],
            "run_steps": _run_steps[0],
            "ext_editor": _ext_editor[0],
            "grok_model": _grok_model[0],
            "prompt_style": _prompt_style[0],
            "shot_scale_preset": _shot_scale_preset[0],
            "visual_preset": _visual_preset[0],
            "fill_engine": _fill_engine[0],
            "fill_mode": _fill_mode[0],
            "auto_check_after_fill": _auto_check_after_fill[0],
            "auto_director_after_fill": _auto_director_after_fill[0],
            "after_fill_images": _after_fill_images[0],
            "director_lighting": _director_lighting[0],
            "director_beauty": _director_beauty[0],
            "director_environment": _director_environment[0],
            "director_motion": _director_motion[0],
            "director_camera_energy": _director_camera[0],
            "director_preset": _director_preset[0],
            "comfy_host": _comfy_host[0],
            "llm_tokens_groom": _llm_tokens_groom[0],
            "llm_tokens_outline": _llm_tokens_outline[0],
            "llm_n_ctx": _llm_n_ctx[0],
            "llm_tokens_fill": _llm_tokens_fill[0],
            "llm_tokens_chat": _llm_tokens_chat[0],
            "llm_tokens_story_a": _llm_tokens_story_a[0],
            "outline_batch": _outline_batch[0],
            "outline_compact": _outline_compact[0],
            "llm_timeout": _llm_timeout[0],
            "grok_temperature": _grok_temperature[0],
            "llm_debug_logging": _llm_debug_logging[0],
            "llm_settings_source": "program" if _llm_use_program[0] else "workflow",
            "caption_workflow": _caption_wf[0],
            "num_shots": _safe_num_shots(),
        })

    def _sheets_fallback(with_scene: bool = True) -> str:
        """[Detail] text for a shot that has no prompt of its own: the character / location / props sheets.
        with_scene=False leaves the location sheet out (the shot's own [Story] already carries its Setting / Light)."""
        parts = []
        for tf in ((char_field, scene_field) if with_scene else (char_field,)):
            v = (getattr(tf, "value", "") or "").strip()
            if v: parts.append(v)
        pv = _props_to_str(props_field.value).strip()
        if pv: parts.append(pv)
        return "\n\n".join(parts)

    async def _gen_shot_image(shot_idx: int, prompt: str, api: str, w: int, h: int,
                               index: int, fixed_seed: int = -1, override_steps: int = -1,
                               stop_event=None, style_kw: str = ""):
        """เลือกใช้ Inpaint Mode (img2img จากรูปก่อนหน้า) หรือ text-to-image ปกติ — ใช้แทน
        run_one(...) ตรงๆ ทุกจุดที่สร้างภาพจริง (Run/Test/Fix ทั้งแบบทีละ shot และ All)"""
        _shot_now = shots_data[shot_idx] if 0 <= shot_idx < len(shots_data) else {}
        prompt = assemble_runtime_prompt(prompt, _shot_now, _cur_dlg_style(), style_kw,
                                         bool(_append_scene_desc[0]),
                                         _sheets_fallback(not ((_shot_now.get("story") or "").strip() or (_shot_now.get("shot_desc") or "").strip())))
        # log the prompt exactly as Krea2 receives it (anatomy guard is appended at send time in comfy_api.build_workflow)
        print(f"[PROMPT shot {shot_idx+1}]\n{_comfy_api_mod._with_anatomy_guard(prompt)}\n{'='*60}")
        # Dropdown.on_change ไม่ fire เสมอ (เจอปัญหานี้ซ้ำๆ กับ dropdown อื่นมาแล้วในเซสชันนี้) —
        # อ่านค่าจริงจาก inpaint_wf_dd/inpaint_ref_dd ตรงๆ ทุกครั้งก่อนใช้ ไม่พึ่ง on_change อย่างเดียว
        if inpaint_wf_dd.value: _inpaint_workflow[0] = inpaint_wf_dd.value
        if inpaint_ref_dd.value: _inpaint_ref_source[0] = inpaint_ref_dd.value
        print(f"[INPAINT] shot={shot_idx+1} mode={_inpaint_mode[0]} wf={_inpaint_workflow[0]!r} ref_source={_inpaint_ref_source[0]!r}")
        if _inpaint_mode[0]:
            ref_img = pick_reference_image(gallery_images, shot_idx, _inpaint_ref_source[0])
            print(f"[INPAINT] shot={shot_idx+1} ref_img={ref_img!r}")
            if ref_img:
                if not _inpaint_workflow[0]:
                    print(f"[INPAINT] shot={shot_idx+1} ⚠ ไม่ได้เลือก Inpaint workflow — ใช้ {api!r} แทน (อาจไม่มี image input node)")
                _wf_used = _inpaint_workflow[0] or api
                _out, _seed = await run_img2img(
                    prompt, ref_img, Path(output_dir[0]), _wf_used,
                    width=w, height=h, index=index, fixed_seed=fixed_seed,
                    override_steps=override_steps, stop_event=stop_event,
                )
                # run_img2img prepends this exact sentence internally when the prompt doesn't
                # already have it — mirror that here so the persisted trace is the true
                # renderer-facing prompt, not the pre-prepend one this function was called with
                _true_prompt = prompt
                _ref_instruction = "Reference the character and environment from the image exactly."
                if _ref_instruction not in _true_prompt:
                    _true_prompt = f"{_ref_instruction}\n{_true_prompt}"
                return _out, _seed, {
                    "generation_mode": "img2img", "workflow": _wf_used,
                    "reference_image": ref_img, "final_prompt": _true_prompt,
                }
            else:
                print(f"[INPAINT] shot={shot_idx+1} ไม่มีรูป reference ให้ใช้เลย — fallback text-to-image")
        _out, _seed = await run_one(
            prompt, Path(output_dir[0]), api, width=w, height=h, index=index,
            fixed_seed=fixed_seed, override_steps=override_steps, stop_event=stop_event,
        )
        return _out, _seed, {
            "generation_mode": "text2img", "workflow": api,
            "reference_image": None, "final_prompt": prompt,
        }

    # ── LLM Log dialog ────────────────────────────────────────────────
    log_list = ft.ListView(spacing=4, expand=True, auto_scroll=True)
    # Flet ใส่ control ตัวเดียวกันลงสองที่ในต้นไม้ไม่ได้ — แท็บ Logs ใน Review Outline จึงต้องมี
    # ListView ของตัวเอง แล้วให้ _add_log_entry ป้อนทั้งสองอันพร้อมกัน (auto_scroll = เห็นบรรทัดล่าสุดเสมอ)
    _outline_log_list = ft.ListView(spacing=4, expand=True, auto_scroll=True)
    # UI-UNFREEZE-A1: 400 widgets/list ร่วมกับ page.update() ทุก 0.25s ระหว่าง Fill (~268 ครั้ง/รอบ 27
    # ช็อตจาก audit เดิม) ทำให้ Flet ต้อง serialize ทั้งหน้า (storyboard ใหญ่ + log ที่โต) ซ้ำๆ จนหน้าค้าง
    # ลดเพดานจอ (ไฟล์ session.log ยังเก็บครบเสมอ ไม่ผูกกับเพดานนี้) และเลิกเรียก page.update() ในจุดนี้
    _OUTLINE_LOG_KEEP = 80
    _LOG_LIST_KEEP = 80
    _LOG_UI_MIN_INTERVAL = 0.25  # วินาที — throttle การ sync จอตอน log รัวๆ ยังคงไว้เหมือนเดิม
    _log_last_ui_sync: list = [0.0]

    def _add_log_entry(msg: str):
        ts = time.strftime("%H:%M:%S")
        lines = msg.split("\n", 1)
        header = f"[{ts}] {lines[0]}"
        body   = lines[1] if len(lines) > 1 else None

        def _entry():
            """สร้าง control ชุดใหม่ทุกครั้ง — ใช้ instance เดียวกันสอง list ไม่ได้"""
            children = [ft.Text(header, size=10, selectable=True, color=C["text1"],
                                weight=ft.FontWeight.W_600)]
            if body:
                children.append(ft.Text(body, size=10, selectable=True, color=C["text2"]))
            return ft.Container(
                content=ft.Column(children, spacing=2, tight=True),
                bgcolor=C["panel"], border_radius=br(4),
                padding=po(left=8, top=6, right=8, bottom=6),
                border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
            )

        _log_to_file(msg)  # session.log ได้ทุกบรรทัดเสมอ ไม่ผูกกับ throttle/เพดานจอด้านล่างเลย

        # UI-UNFREEZE-A1: สร้าง+ต่อ entry เฉพาะ list ที่ตัว mount อยู่บน page จริงตอนนี้ — ถ้าทั้งคู่ปิดอยู่
        # (เช่น Fill กำลังรันแต่ไม่มีใครเปิด Logs เลย) ก็ไม่ต้องสร้าง control tree อะไรขึ้นมาทิ้งไว้เฉยๆ
        # .page ไม่คืน None เฉยๆ เมื่อ control ยังไม่เคยถูก mount เลย — Flet เวอร์ชันนี้ raise RuntimeError
        # ("Control must be added to the page first") แทน ต้องกันด้วย try/except ไม่ใช่ truthy check ตรงๆ
        def _mounted(ctl) -> bool:
            try:
                return bool(ctl.page)
            except Exception:
                return False

        _touched = []
        if _mounted(log_list):
            log_list.controls.append(_entry())
            if len(log_list.controls) > _LOG_LIST_KEEP:
                del log_list.controls[:-_LOG_LIST_KEEP]
            _touched.append(log_list)
        if _mounted(_outline_log_list):
            _outline_log_list.controls.append(_entry())
            if len(_outline_log_list.controls) > _OUTLINE_LOG_KEEP:
                del _outline_log_list.controls[:-_OUTLINE_LOG_KEEP]
            _touched.append(_outline_log_list)
        if not _touched:
            return
        _now = time.monotonic()
        if _now - _log_last_ui_sync[0] < _LOG_UI_MIN_INTERVAL:
            return
        _log_last_ui_sync[0] = _now
        # ห้าม page.update() ตรงนี้เด็ดขาด — ทั้งหน้ารวม storyboard ใหญ่ต้อง sync ทุกครั้งที่ log ไหล
        # ทำให้ค้างระหว่าง LLM รัน sync แค่ list control ที่เพิ่งแก้จริงพอ
        for lv in _touched:
            try: lv.update()
            except Exception: pass

    async def _llm_log(msg: str):
        _add_log_entry(msg)

    log_dialog = ft.AlertDialog(
        modal=False,
        title=ft.Row([
            ft.Text("AI Logs", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
            ft.Container(expand=True),
            ft.IconButton(ft.Icons.DELETE_SWEEP_ROUNDED, icon_size=16, icon_color=C["text3"],
                          on_click=lambda e: (log_list.controls.clear(), log_list.update()),
                          width=30, height=30, padding=0),
        ]),
        bgcolor=C["surface"],
        content=ft.Container(width=640, expand=True,
                             content=ft.Column([log_list], expand=True, spacing=4)),
        actions=[ft.TextButton("Close", on_click=lambda e: setattr(log_dialog, "open", False) or page.update(),
                               style=ft.ButtonStyle(color=C["text2"]))],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(log_dialog)

    # ── forward-declare ฟังก์ชันที่ shot_row ต้องการ ──────────────────
    # (จะ define จริงหลัง build_shot_col)
    _do_director_run_ref: list = [None]
    _do_check_run_ref:    list = [None]
    _do_test_all_ref:     list = [None]
    _do_run_all_ref:      list = [None]
    _run_vision_ref:      list = [None]

    async def _director_run_proxy(single_idx=None):
        # MANGA-VNEXT-A1 §46: guard at the proxy too (lightbox/per-shot callers reach the runner
        # only through this proxy) — same runtime guard also lives in _do_director_run itself.
        if _manga_vnext_mode[0]:
            await _llm_log("⏭ Visual Direction skipped (proxy) — unavailable in Manga vNext mode")
            return
        _open_director_dialog(single_idx)

    async def _check_run_proxy(single_idx=None):
        if _manga_vnext_mode[0]:
            await _llm_log("⏭ Consistency Check skipped (proxy) — unavailable in Manga vNext mode")
            return
        _open_check_dialog(single_idx)

    async def _vision_proxy(mode, single_idx=None):
        if _manga_vnext_mode[0]:
            await _llm_log("⏭ Vision Check skipped (proxy) — unavailable in Manga vNext mode")
            return
        if _run_vision_ref[0]: await _run_vision_ref[0](mode, single_idx)

    # ── Build lightbox ────────────────────────────────────────────────
    _thumb_registry_ref = [{}]   # จะถูกแทนที่ด้วย registry จริงหลัง build_shot_col สร้างเสร็จ
    lb_overlay, open_lb = build_lightbox(page, gallery_images, thumb_actions_ref=_thumb_registry_ref,
                                          on_open_editor=_launch_image_editor, llm_busy_ref=llm_busy)
    page.data = {"open_lb": open_lb}

    # ── Build shots col ───────────────────────────────────────────────
    # ป้ายสองตัวนี้เปลี่ยนตามโหมด (Shots/shot ↔ Pages/Auto) — สร้างไว้ก่อน _sync_outline_mode
    _shots_lbl = ft.Text("Shots", size=11, color=C["text2"], weight=ft.FontWeight.W_600)
    _shots_unit_lbl = ft.Text("shot", size=11, color=C["text3"])
    num_shots_field = ft.TextField(
        value=str(saved.get("num_shots", 5)),
        keyboard_type=ft.KeyboardType.NUMBER,
        text_size=12, width=80, height=40,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=8, right=8, top=6, bottom=6),
    )

    test_steps_field = ft.Dropdown(
        value="3",
        options=[ft.dropdown.Option(str(s)) for s in [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20]],
        height=36, text_size=12, expand=True,
        border_color=C["border"], focused_border_color="#7C3AED",
    )
    test_steps_field.on_change = lambda e: _test_steps.__setitem__(0, int(e.control.value or "3"))

    _run_steps_init = str(_run_steps[0]) if _run_steps[0] > 0 else "default"
    run_steps_field = ft.Dropdown(
        value=_run_steps_init if _run_steps_init in ["default"] + [str(s) for s in [8, 10, 12, 16, 20, 25, 30, 40, 50]] else "default",
        options=[ft.dropdown.Option("default", "default (workflow)")] +
                [ft.dropdown.Option(str(s)) for s in [8, 10, 12, 16, 20, 25, 30, 40, 50]],
        height=36, text_size=12, expand=True,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def _run_steps_changed(e):
        v = run_steps_field.value or "default"
        _run_steps[0] = int(v) if v != "default" else -1
        _save_state()
    run_steps_field.on_change = _run_steps_changed

    # ── External editor setting ───────────────────────────────────────
    ext_editor_field = ft.TextField(
        value=_ext_editor[0],
        hint_text="path to .exe (optional)",
        text_size=10, height=34, expand=True,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=8, right=4, top=0, bottom=0),
    )

    def _clear_ext_editor(e):
        _ext_editor[0] = ""
        ext_editor_field.value = ""; ext_editor_field.update()
        _save_state()

    def _on_ext_editor_changed(e):
        _ext_editor[0] = e.control.value.strip()
        _save_state()
    ext_editor_field.on_blur = _on_ext_editor_changed

    _loop_shot_field_ref = [None]  # จะ assign หลัง loop_shot_field สร้าง
    _set_running_ref = [None]      # จะ assign หลัง _set_running defined
    _id_wf_dd_ref = [None]         # จะ assign หลัง id_wf_dd สร้าง

    (shots_col, _shot_rows, _shot_prompt_fields,
     _build_shot_row, add_thumb_to_shot,
     rebuild_shots_col, _inject_dialogue, _thumb_registry) = build_shot_col(
        page=page,
        shots_data=shots_data,
        gallery_images=gallery_images,
        _dialogue_style=_dialogue_style,
        _dialogue_lang=_dialogue_lang,
        _regen_sys_custom=_regen_sys_custom,
        _story_summary=_story_summary,
        _id_pass=_id_pass,
        _id_ref_image=_id_ref_image,
        _id_workflow=_id_workflow,
        _seed_lock=_seed_lock,
        _locked_seed=_locked_seed,
        _loops_per_shot=_loops_per_shot,
        _stop_event=_stop_event,
        output_dir=output_dir,
        selected_api=selected_api,
        style_dd=style_dd,
        llm_dd=llm_dd,
        sel_size=sel_size,
        _SIZE_MAP=_SIZE_MAP,
        status_lbl=status_lbl,
        progress_ring=progress_ring,
        llm_busy=llm_busy,
        llm_prog=llm_prog,
        save_state_fn=_save_state,
        rebuild_shots_col_fn=lambda: rebuild_shots_col(),
        story_field=story_field,
        char_field=char_field,
        scene_field=scene_field,
        requirement_field=requirement_field,
        story_summary_field=story_summary_field,
        _GROK_OPTION=_GROK_OPTION,
        _grok_api_key=_grok_api_key,
        _grok_model=_grok_model,
        _prompt_style=_prompt_style,
        sync_prompt_style_fn=lambda: _sync_prompt_style(),
        sync_style_fn=lambda: _sync_style(),
        resolve_style_kw_fn=lambda shot_scale="medium": _resolve_style_kw(shot_scale),
        gen_shot_image_fn=_gen_shot_image,
        do_director_run_fn=_director_run_proxy,
        do_check_run_fn=_check_run_proxy,
        run_vision_fn=_vision_proxy,
        add_thumb_fn=None,
        _llm_log=_llm_log,
        _test_steps=_test_steps,
        _run_steps=_run_steps,
        run_steps_field=run_steps_field,
        api_dd=api_dd,
        size_dd=size_dd,
        test_steps_field=test_steps_field,
        id_wf_dd_ref=_id_wf_dd_ref,
        _loop_shot_field_ref=_loop_shot_field_ref,
        _set_running_ref=_set_running_ref,
        _is_running=_is_running,
        _ext_editor=_ext_editor,
        _sel_genre=_sel_genre,
        _sel_genre2=_sel_genre2,
        _manga_page_mode=_manga_page_mode,
        open_fill_dialog_fn=lambda idx: asyncio.create_task(do_fill_prompts(None, single_idx=idx)),
        current_story_id_fn=lambda: _cur_story_name[0],
        _dialogue_dd_ref=_dialogue_dd_ref,
        ensure_yolo_fn=lambda: _maybe_offer_yolo(),
    )
    _thumb_registry_ref[0] = _thumb_registry

    # ── Loop settings ─────────────────────────────────────────────────
    loop_shot_field = ft.Dropdown(
        tooltip="Iterations per shot — images generated for each shot",
        value=str(_loops_per_shot[0]),
        options=[ft.dropdown.Option(str(i)) for i in range(1, 11)],
        text_size=12, height=36, expand=True,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    loop_story_field = ft.Dropdown(
        tooltip="Iterations per story — times the whole storyboard is run",
        value=str(_loops_per_story[0]),
        options=[ft.dropdown.Option(str(i)) for i in range(1, 11)],
        text_size=12, height=36, expand=True,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def _sync_loops(e=None):
        try: _loops_per_shot[0]  = int(loop_shot_field.value or "1")
        except: pass
        try: _loops_per_story[0] = int(loop_story_field.value or "1")
        except: pass
        _save_state(); page.update()
    loop_shot_field.on_change = _sync_loops
    loop_story_field.on_change = _sync_loops
    _loop_shot_field_ref[0] = loop_shot_field


    # ── Seed lock ─────────────────────────────────────────────────────
    seed_display = ft.Text(str(_locked_seed[0]), size=10, color=C["text3"],
                           selectable=True, expand=True, max_lines=1,
                           overflow=ft.TextOverflow.ELLIPSIS)

    def _new_seed(e=None):
        _locked_seed[0] = random.randint(0, 2**53)
        seed_display.value = str(_locked_seed[0]); _save_state(); page.update()

    seed_lock_switch = ft.Switch(value=_seed_lock[0], active_color=C["accent"],
                                 label="Seed Lock",
                                 label_text_style=ft.TextStyle(size=11, color=C["text2"]))
    seed_row = ft.Container(
        visible=_seed_lock[0],
        content=ft.Row([seed_display,
                         ft.IconButton(ft.Icons.REFRESH_ROUNDED, icon_size=14, icon_color=C["text3"],
                                       on_click=_new_seed, width=28, height=28, padding=0)],
                        spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
    )
    def seed_lock_changed(e):
        _seed_lock[0] = seed_lock_switch.value
        seed_row.visible = _seed_lock[0]; _save_state(); page.update()
    seed_lock_switch.on_change = seed_lock_changed

    append_scene_desc_switch = ft.Switch(
        value=_append_scene_desc[0], active_color=C["accent"],
        label="Scene Description",
        label_text_style=ft.TextStyle(size=11, color=C["text2"]),
        tooltip="Include scene description for generation — prepends it to the prompt",
    )
    def append_scene_desc_changed(e):
        _append_scene_desc[0] = append_scene_desc_switch.value
        _save_state(); page.update()
    append_scene_desc_switch.on_change = append_scene_desc_changed

    # ── Identity Pass UI ─────────────────────────────────────────────
    id_ref_lbl = ft.Text(
        Path(_id_ref_image[0]).name if _id_ref_image[0] else "No image selected",
        size=10, color=C["text2"] if _id_ref_image[0] else C["text3"],
        expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
    )
    id_ref_preview = ft.Container(
        width=60, height=60, border_radius=br(6),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        visible=bool(_id_ref_image[0]),
        content=ft.Image(src=_id_ref_image[0] or "", fit="cover", width=60, height=60),
    )

    async def pick_id_ref(e):
        fp = ft.FilePicker()
        files = await fp.pick_files(dialog_title="Select Reference Image", allow_multiple=False,
                                    allowed_extensions=["jpg", "jpeg", "png", "webp"])
        if files and files[0].path:
            p = files[0].path
            _id_ref_image[0] = p
            id_ref_lbl.value = Path(p).name; id_ref_lbl.color = C["text2"]
            id_ref_preview.content = ft.Image(src=p, fit="cover", width=60, height=60)
            id_ref_preview.visible = True; _save_state(); page.update()

    id_wf_dd = ft.Dropdown(
        value=_id_workflow[0] if _id_workflow[0] in scan_edit_apis() else None,
        options=[ft.dropdown.Option(a) for a in scan_edit_apis()],
        hint_text="Identity Workflow", text_size=10, height=36,
        border_color=C["border"], focused_border_color=C["purple"],
        bgcolor=C["surface"],
        content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def id_wf_changed(e):
        _id_workflow[0] = id_wf_dd.value or ""; _save_state()
    id_wf_dd.on_change = id_wf_changed
    _id_wf_dd_ref[0] = id_wf_dd

    id_pass_container = ft.Container(
        visible=_id_pass[0],
        content=ft.Column([
            ft.Text("Reference Image:", size=10, color=C["text3"]),
            ft.Row([id_ref_preview,
                    ft.Column([id_ref_lbl,
                                ft.OutlinedButton(content=ft.Row([
                                    ft.Icon(ft.Icons.PHOTO_LIBRARY_OUTLINED, size=13, color=C["purple"]),
                                    ft.Text("Select image", size=10, color=C["purple"])], tight=True, spacing=4),
                                    on_click=pick_id_ref, height=30,
                                    style=ft.ButtonStyle(side=ft.BorderSide(1, C["purple"])))],
                               spacing=4, tight=True)],
                   spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text("Identity Workflow:", size=10, color=C["text3"]),
            id_wf_dd,
            ft.Text("💡 Requires krea2_identity_edit.json", size=9, color=C["text3"], italic=True),
        ], spacing=6),
    )
    id_pass_switch = ft.Switch(value=_id_pass[0], active_color=C["purple"],
                                label="Identity Pass (consistent face every shot)",
                                label_text_style=ft.TextStyle(size=11, color=C["text2"]))
    def id_pass_changed(e):
        _id_pass[0] = id_pass_switch.value
        if id_wf_dd.value: _id_workflow[0] = id_wf_dd.value
        id_pass_container.visible = _id_pass[0]
        # Inpaint Mode กับ Identity Pass ทำงานพร้อมกันไม่ได้ (คุยกับ user แล้ว) — เปิดอันนึงต้องปิดอีกอัน
        if _id_pass[0] and _inpaint_mode[0]:
            _inpaint_mode[0] = False
            inpaint_switch.value = False
            inpaint_container.visible = False
        _save_state(); page.update()
    id_pass_switch.on_change = id_pass_changed

    # ── Inpaint Mode UI (ใช้รูปก่อนหน้าเป็น reference แทน text-to-image ทั้งหมด) ──────
    inpaint_wf_dd = ft.Dropdown(
        value=_inpaint_workflow[0] if _inpaint_workflow[0] in scan_edit_apis() else None,
        options=[ft.dropdown.Option(a) for a in scan_edit_apis()],
        hint_text="Inpaint/Img2Img Workflow", text_size=10, height=36,
        border_color=C["border"], focused_border_color=C["purple"],
        bgcolor=C["surface"],
        content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def inpaint_wf_changed(e):
        _inpaint_workflow[0] = inpaint_wf_dd.value or ""; _save_state()
    inpaint_wf_dd.on_change = inpaint_wf_changed

    inpaint_ref_dd = ft.Dropdown(
        value=_inpaint_ref_source[0],
        options=[
            ft.dropdown.Option(key="prev_shot", text="Latest image of previous shot (fallback: shot 1 first)"),
            ft.dropdown.Option(key="shot1", text="First image of shot 1 always (fixed anchor)"),
        ],
        text_size=10, height=36,
        border_color=C["border"], focused_border_color=C["purple"],
        bgcolor=C["surface"],
        content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def inpaint_ref_changed(e):
        _inpaint_ref_source[0] = inpaint_ref_dd.value or "prev_shot"; _save_state()
    inpaint_ref_dd.on_change = inpaint_ref_changed

    inpaint_container = ft.Container(
        visible=_inpaint_mode[0],
        content=ft.Column([
            ft.Text("Inpaint/Img2Img Workflow:", size=10, color=C["text3"]),
            inpaint_wf_dd,
            ft.Text("Reference source:", size=10, color=C["text3"]),
            inpaint_ref_dd,
            ft.Text("💡 Shot 1 reuses its existing image for img2img; otherwise falls back to text-to-image.",
                    size=9, color=C["text3"], italic=True),
        ], spacing=6),
    )
    inpaint_switch = ft.Switch(value=_inpaint_mode[0], active_color=C["purple"],
                                label="Inpaint Mode",
                                label_text_style=ft.TextStyle(size=11, color=C["text2"]))
    def inpaint_changed(e):
        _inpaint_mode[0] = inpaint_switch.value
        if inpaint_wf_dd.value: _inpaint_workflow[0] = inpaint_wf_dd.value
        inpaint_container.visible = _inpaint_mode[0]
        if _inpaint_mode[0] and _id_pass[0]:
            _id_pass[0] = False
            id_pass_switch.value = False
            id_pass_container.visible = False
        _save_state(); page.update()
    inpaint_switch.on_change = inpaint_changed

    # ── Fill Prompts ──────────────────────────────────────────────────
    fill_prompts_btn = ft.IconButton(
        icon=ft.Icons.PERSON_OUTLINE, icon_color="white", icon_size=20,
        tooltip="Generate Prompts", bgcolor=C["purple"],
        width=40, height=38,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
    )

    _fill_single_idx: list = [None]  # ถ้าไม่ None → fill เฉพาะ shot นี้ (จากปุ่ม regen รายช็อต)

    async def _do_fill_prompts_run(clear_first: bool = False):
        try:
            await _do_fill_prompts_run_inner(clear_first)
        except Exception:
            import traceback
            _tb = traceback.format_exc()
            print(f"[main_ui] _do_fill_prompts_run exception:\n{_tb}")
            await _llm_log(f"❌ _do_fill_prompts_run exception:\n{_tb}")
            _fill_single_idx[0] = None
            llm_busy[0] = False; llm_prog.visible = False
            fill_prompts_btn.disabled = False; gen_shots_btn.disabled = False; _stop_btn_state(False)
            status_lbl.value = "❌ Render prompt error — see log"; page.update()

    async def _do_fill_prompts_run_inner(clear_first: bool = False):
        # Dropdown.on_change ไม่ fire บางครั้ง (พบแล้วกับ Steps/Style ก่อนหน้านี้) — อ่านค่าจริงจาก
        # dropdown ตรงๆ ตอนจะรันเสมอ ไม่พึ่ง _prompt_style[0] ที่อาจไม่ sync จาก on_change
        _sync_prompt_style()
        _sync_fill_mode()
        _sync_style()
        _sync_genre()
        _single_idx = _fill_single_idx[0]
        if clear_first and _single_idx is None:
            for s in shots_data: s["prompt"] = ""; s.pop("_fill_failed", None)
            rebuild_shots_col(); page.update()
        llm_wf = llm_dd.value
        # DIRECTOR-C1: Grok's Director batch call already exists (run_director_prompts one-call mode) —
        # the auto-chain loop below used to call Director once PER SHOT regardless of backend, which
        # for Grok meant N separate one-shot "batches" instead of one real batch seeing the sequence.
        # Local keeps its existing per-shot behavior (it has no batch mode to lose).
        _is_grok_run = (llm_wf == _GROK_OPTION)
        _stop_event.clear(); llm_busy[0] = True; llm_prog.visible = True
        fill_prompts_btn.disabled = True; gen_shots_btn.disabled = True; _stop_btn_state(True)
        status_lbl.value = (f"✨ Generating render prompts..." if _single_idx is None
                            else f"✨ Regen prompt shot {_single_idx+1}...")
        page.update()

        async def on_shot_fill(idx, shot_data):
            # V3-PROMPT-BLOCK-SPACING-NORMALIZATION-A1: this is the primary ingress every Fill run
            # goes through — the Raw JSON ingress (_norm_merge_shot_field) never sees a fresh Fill
            # result at all, so it needs the same presentation-only pass applied here directly.
            if shot_data.get("prompt"):
                shot_data["prompt"] = _normalize_v3_prompt_block_spacing(shot_data["prompt"])
            if idx < len(shots_data): shots_data[idx] = shot_data
            else: shots_data.append(shot_data)
            # Filter dead files from the LIVE gallery, not a pre-run snapshot — a snapshot-based restore
            # would silently undo any image the user deleted while this shot was being filled (the deleted
            # entry would get resurrected here since it's the snapshot, not current state, being read).
            if idx in gallery_images: gallery_images[idx] = [img for img in gallery_images[idx] if Path(img["path"]).exists()]
            # เดิม rebuild_shots_col() เรียก page.update() ของตัวเอง (do_update default True) แล้ว
            # โดนเรียกซ้ำอีกทีข้างล่าง — สองรอบต่อช็อตทั้งที่เป็นการซิงก์สถานะเดียวกัน รวมเป็นครั้งเดียว
            rebuild_shots_col(do_update=False)
            status_lbl.value = f"✨ Render prompt generated: shot {idx+1}/{len(shots_data)}"
            page.update()

        async def on_err_fill(msg):
            await _llm_log(f"❌ {msg}"); status_lbl.value = f"❌ {msg}"; page.update()

        def on_prog(cur, tot):
            status_lbl.value = f"✨ Shot {cur}/{tot}..."; page.update()

        async def _run_fill_vnext_compat(target_idx, force_flag):
            """NEXT: vNext outline generation no longer runs Fill automatically — the user now
            triggers it from this same Fill dialog. Keeps run_fill_vnext (A1 character/world sheet +
            per-shot brief) as the engine for a vNext outline instead of falling through to the
            legacy engine, while reusing all of this function's surrounding chaining/UI plumbing
            (Empty only/Rebuild all, single-shot regen, auto-check/auto-director) unchanged — those
            only ever call _fill_once, unaware of which engine ran underneath."""
            _sync_char_sheet_to_a1()
            a1 = _outline_vnext_a1[0]
            # AUTOCHAIN-GROK-FILL-BATCH-A1: target_idx may now also be a list of indices (mirrors
            # run_director_prompts's existing single_shot_idx: int | list[int] | set[int] | None
            # convention) — the Grok auto-chain path below passes every shot it needs filled in ONE
            # call so run_fill_vnext sees the whole batch and issues exactly one Grok HTTP request,
            # instead of looping this function once per shot (which would build a length-1 vshots
            # list per call and defeat run_fill_vnext's one-call batch design).
            if isinstance(target_idx, list):
                indices = target_idx
            elif target_idx is not None:
                indices = [target_idx]
            else:
                indices = [i for i, s in enumerate(shots_data)
                           if force_flag or not s.get("prompt", "").strip()]
            if not indices:
                return

            # NORMAL-VNEXT-SCHEMA-CLEANUP-A1 §2: shots_data is native-shaped since §1 (B's own
            # schema) — no legacy→vnext reconstruction needed any more. run_fill_vnext already
            # only reads the native keys (visible_people/location/shot_desc/clothing/objects/
            # camera/lighting) via .get(), so passing the native dict straight through is enough;
            # this is still a shallow-copy view (never the live shots_data dict itself) so Fill's
            # own added keys (_fill_engine/prompt_style/prompt) never leak back before on_shot
            # merges them in explicitly below.
            vshots = [dict(shots_data[i]) for i in indices]

            async def _on_shot_vnext(done_n, total_n):
                real_idx = indices[done_n - 1]
                merged = dict(shots_data[real_idx])
                merged["prompt"] = vshots[done_n - 1].get("prompt", "")
                # FILL-VNEXT-LOCK-CONTRACT-A1: propagate the real engine/form vNext Fill actually
                # used onto the shot itself — Checker/Director/vision-check read shot["_fill_engine"]
                # (fallback when their own fill_engine= arg is empty) instead of trusting a hardcoded
                # constant, and shot["prompt_style"] is the same per-shot field Director already
                # reads to know which form to preserve.
                merged["_fill_engine"] = vshots[done_n - 1].get("_fill_engine", "")
                merged["prompt_style"] = vshots[done_n - 1].get("prompt_style", "")
                # FILL-FAILED-PROPAGATE-A1: run_fill_vnext just set/cleared _fill_failed on the
                # vshots[...] dict it was actually given (success -> popped, missing-from-batch or
                # structurally-invalid -> True) — `merged` starts from the OLD shots_data[real_idx]
                # snapshot, which never had this run's outcome, so copy vshots' verdict across
                # instead of unconditionally popping it (that used to silently erase a real failure
                # signal the moment this callback fired, before the UI ever saw it).
                if vshots[done_n - 1].get("_fill_failed"):
                    merged["_fill_failed"] = True
                else:
                    merged.pop("_fill_failed", None)
                # FILL-PYTHON-CHECK-REMOVAL-A1: generated-but-not-renderer-ready (non-English script) state.
                if vshots[done_n - 1].get("_fill_renderer_ready") is False:
                    merged["_fill_renderer_ready"] = False
                else:
                    merged.pop("_fill_renderer_ready", None)
                # FILL-VNEXT-REGEN-SESSION-CONTINUITY-A1: propagate the Local session this shot's
                # prompt was actually written in (absent for Grok, which has no continuable session)
                # so the shot-card "Regen Prompt" chat can continue that exact conversation instead
                # of always starting a fresh one.
                if vshots[done_n - 1].get("_fill_session_id"):
                    merged["_fill_session_id"] = vshots[done_n - 1]["_fill_session_id"]
                else:
                    merged.pop("_fill_session_id", None)
                status_lbl.value = f"✨ Shot {done_n}/{total_n}..."; page.update()
                await on_shot_fill(real_idx, merged)

            # FILL-VNEXT2-ONLY-ROUTING-A1: Normal Fill runs vNext2 unconditionally — no engine preference,
            # no fallback. (run_fill_vnext stays in llm_engine.py as dormant rollback code.) Local runs
            # per shot and Grok runs one batch call, both decided inside run_fill_vnext2.
            if _llm_log:
                await _llm_log("NORMAL RENDER PROMPT engine=vnext2")
            # FILL-CONTEXT-DIRECTOR-KREA2-A1: micro_beats lets Fill resolve each shot's own PARENT MACRO
            # EVENT (structural id lookup only, both backends); story_text (full source) and b_sequence
            # (the FULL project shot list, not just vshots — the ones actually being filled now) are
            # Grok-only context, routed inside run_fill_vnext2/_build_fill_vnext2_batch_sys. Local never
            # receives story_text or b_sequence.
            _a2_for_fill = _outline_vnext_a2[0] or {}
            await run_normal_render_prompt(
                vshots, a1, llm_wf, on_err_fill, on_log=_llm_log,
                # SOURCE-STYLE-A1: "As Source" resolved ONLY at Fill, never at any other stage.
                image_style=((_source_style[0] or "") if _sync_style() == "as_source"
                             else STYLE_MAP.get(_sync_style() or "none", ("", ""))[1]),
                genre=_sel_genre[0], genre2=_sel_genre2[0],
                grok_api_key=_grok_api_key[0], grok_model=_grok_model[0],
                on_shot=_on_shot_vnext,
                # PROPS-SOURCE-OF-TRUTH-A1: the live, user-editable props_field value.
                props_desc=_props_to_str(props_field.value).strip(),
                # VISUAL DIRECTION: the shared Visual Preset resolved in Python (no extra LLM call).
                visual_direction=_visual_direction(),
                stop_event=_stop_event,
                micro_beats=_a2_for_fill.get("micro_beats") or [],
                story_text=story_field.value.strip(),
                b_sequence=shots_data,
            )

        async def _run_fill_manga_vnext_compat(target_idx, force_flag):
            """MANGA-VNEXT-A1 §50: Fill routing for Manga vNext — calls run_fill_manga_vnext using
            shot["_manga_page_spec"] (the full nested B Manga page object, §39), never reconstructed
            from the flat legacy scene_desc adapter fields. Never calls run_fill_vnext."""
            _sync_char_sheet_to_a1()
            a1 = _outline_vnext_a1[0]
            if target_idx is not None:
                indices = [target_idx]
            else:
                indices = [i for i, s in enumerate(shots_data)
                           if force_flag or not s.get("prompt", "").strip()]
            if not indices:
                return
            mshots = [{"shot": shots_data[i].get("shot"),
                       "_manga_page_spec": shots_data[i].get("_manga_page_spec") or {}}
                      for i in indices]

            async def _on_page(done_n, total_n):
                real_idx = indices[done_n - 1]
                merged = dict(shots_data[real_idx])
                merged["prompt"] = mshots[done_n - 1].get("prompt", "")
                merged["_fill_engine"] = mshots[done_n - 1].get("_fill_engine", "")
                merged["prompt_style"] = mshots[done_n - 1].get("prompt_style", "")
                if mshots[done_n - 1].get("_fill_failed"):
                    merged["_fill_failed"] = True
                else:
                    merged.pop("_fill_failed", None)
                if mshots[done_n - 1].get("_fill_renderer_ready") is False:
                    merged["_fill_renderer_ready"] = False
                else:
                    merged.pop("_fill_renderer_ready", None)
                status_lbl.value = f"✨ Page {done_n}/{total_n}..."; page.update()
                await on_shot_fill(real_idx, merged)

            await run_manga_render_prompt(
                mshots, a1, llm_wf, on_err_fill, on_log=_llm_log,
                image_style=((_source_style[0] or "") if _sync_style() == "as_source"
                             else STYLE_MAP.get(_sync_style() or "none", ("", ""))[1]),
                genre=_sel_genre[0], genre2=_sel_genre2[0],
                grok_api_key=_grok_api_key[0], grok_model=_grok_model[0],
                on_page=_on_page,
                # PROPS-SOURCE-OF-TRUTH-A1 (§12 of the Manga vNext continuity patch): same live
                # props_field routing Normal Fill already uses — takes priority over
                # a1["important_objects"], which is only A1's initial seed.
                props_desc=_props_to_str(props_field.value).strip(),
                stop_event=_stop_event,
                visual_direction=_visual_direction(),
                # outfit carry-over: the page before the first page of this run (its finished prompt holds the IDENTITY LOCK that Fill hands on)
                previous_prompt=(shots_data[indices[0] - 1].get("prompt", "") if indices and indices[0] > 0 else ""),
            )

        async def _fill_once(target_idx, force_flag):
            # VNEXT-PRODUCTION-PROMOTION-A1 follow-up: _outline_vnext_a1[0] is always a non-empty
            # dict (synthesized on app start / story load if nothing was generated/loaded — see
            # _synthesize_vnext_a1_from_fields), so this gate is unconditionally true and the legacy
            # run_fill_prompts(fill_engine=FILL_ENGINE_MINIMAL_V3) fallback that used to live here is
            # dead from this call site and has been removed from llm_engine.py.
            # MANGA-VNEXT-A1 §50: Manga vNext routes to Fill Manga instead — checked first so Manga
            # vNext production never falls through to normal Fill.
            if _manga_vnext_mode[0]:
                await _run_fill_manga_vnext_compat(target_idx, force_flag)
                return
            await _run_fill_vnext_compat(target_idx, force_flag)

        # ── รอบต่อท้าย (Check / Visual Direction): regen ช็อตเดียว = fill → check → director ของช็อตนั้น;
        # ทั้งชุด = fill ทุกช็อตให้เสร็จก่อน แล้วค่อย check ทุกช็อต แล้วค่อย director ทุกช็อต (Local และ Grok เรียงเหมือนกัน) ──
        # fill ทั้งชุดจึงทำต่อเนื่องในรอบเดียว (previous-layout ของ Fill ต่อกันจาก output ของ Fill เอง) ก่อนค่อยเข้ารอบตรวจ/กำกับ
        # MANGA-VNEXT-A1 §43/§44: effective_auto_check/effective_auto_director = saved preference
        # AND not manga_vnext_mode — the saved Normal preference is preserved untouched so it comes
        # right back when the user turns Manga vNext off again; only the EFFECTIVE (runtime) value
        # is gated here.
        _chain_check = bool(_auto_check_after_fill[0]) and _do_check_run_ref[0] is not None and not _manga_vnext_mode[0]
        _chain_dir = bool(_auto_director_after_fill[0]) and _do_director_run_ref[0] is not None and not _manga_vnext_mode[0]
        if _single_idx is not None:
            await _fill_once(_single_idx, True)
            _chained_run[0] = _chain_check or _chain_dir
            try:
                if _chain_check and not _stop_event.is_set() and shots_data[_single_idx].get("prompt", "").strip():
                    await _llm_log(f"🔍 Auto Consistency Check — shot {_single_idx+1}")
                    await _do_check_run_ref[0](_single_idx)
                if _chain_dir and not _stop_event.is_set() and shots_data[_single_idx].get("prompt", "").strip():
                    await _llm_log(f"🎬 Auto Visual Direction — shot {_single_idx+1}")
                    await _do_director_run_ref[0](_single_idx)
            finally:
                _chained_run[0] = False
        elif _chain_check or _chain_dir:
            _total = len(shots_data)
            # Grok: Fill/Checker/Director ทั้งสาม stage ยิงเป็น 1 batch call ต่อ stage เท่านั้น (ดู
            # AUTOCHAIN-GROK-FULL-PIPELINE-BATCH-A1 ด้านล่าง) Local ไม่แตะ ยังเรียกทีละช็อตเหมือนเดิม
            # ตลอดลูปนี้ สถานะ UI (spinner/ปุ่ม/Stop) เป็นของลูป ไม่ใช่ของ runner แต่ละตัว
            _chained_run[0] = True
            try:
                if _is_grok_run:
                    # AUTOCHAIN-GROK-FULL-PIPELINE-BATCH-A1: the whole Grok auto-chain is exactly
                    # 3 steps, each AT MOST one Grok HTTP request total, no matter how many shots —
                    # STEP A: fill every target shot in ONE call (unchanged from
                    # AUTOCHAIN-GROK-FILL-BATCH-A1 below). STEP B: check every shot that came out of
                    # Fill usable, in ONE call (previously this looped `_do_check_run_ref[0](_i)`
                    # per shot — N real HTTP requests for N shots — now Checker's own
                    # CHECKER-C1 subset-target support lets it take the whole list at once). STEP C:
                    # direct every shot that is still usable after Checker, in ONE call. Grok's batch
                    # prompts already carry shared CHARACTER IDENTITY/WORLD/props/macro_events once up
                    # front and per-shot SHOT AUTHORITY payloads, so — unlike Local — no shot's
                    # fill/check/direct needs to read a PRECEDING shot's already-processed prompt as
                    # context; nothing about correctness depends on doing this shot-by-shot.
                    _fill_targets = [i for i in range(_total)
                                      if clear_first or not shots_data[i].get("prompt", "").strip()
                                      or shots_data[i].get("_fill_failed")]
                    if _fill_targets:
                        status_lbl.value = f"✨ Generating render prompts for {len(_fill_targets)} shots (Grok batch)..."
                        page.update()
                        await _fill_once(_fill_targets, True)
                    # STEP A result: shots that came out of Fill with a usable prompt and no
                    # _fill_failed marker (set by run_fill_vnext when that shot was missing from the
                    # single Fill batch response) — a failed Fill shot must NOT reach Checker/Director.
                    _post_fill_targets = [i for i in _fill_targets
                                           if shots_data[i].get("prompt", "").strip()
                                           and not shots_data[i].get("_fill_failed")]
                    for _i in _fill_targets:
                        if _i not in _post_fill_targets:
                            await _llm_log(f"⚠ Shot {_i+1}: fill ไม่ได้ prompt หรือ fill ล้มเหลว — ข้ามรอบตรวจ/กำกับของช็อตนี้")
                    # STEP B — Checker: ONE batch call for every STEP-A-usable shot at once.
                    if _chain_check and _post_fill_targets and not _stop_event.is_set():
                        await _llm_log(f"🔍 Auto Consistency Check — Grok batch ({len(_post_fill_targets)} shots)")
                        await _do_check_run_ref[0](_post_fill_targets)
                    # STEP C — Director: recompute the usable subset from the same shots (Checker may
                    # have left a shot's prompt blank on its own failure path — same condition the old
                    # per-shot loop checked after each single Checker call), then ONE batch call.
                    _director_targets = [i for i in _post_fill_targets
                                          if shots_data[i].get("prompt", "").strip()]
                    if _chain_dir and _director_targets and not _stop_event.is_set():
                        await _llm_log(f"🎬 Auto Visual Direction — Grok batch ({len(_director_targets)} shots)")
                        await _do_director_run_ref[0](_director_targets)
                else:
                    # Local: Fill EVERY target shot first (one pass, so Fill's own previous-layout chaining runs over the
                    # raw Fill output exactly as it does without the extras), THEN run the Consistency Check over all of
                    # them, THEN Visual Direction over all of them. (Same order the Grok batch path already uses.)
                    # "Empty only" (clear_first=False): shots that already have a prompt are skipped by every phase.
                    _fill_targets = [i for i in range(_total)
                                     if clear_first or not shots_data[i].get("prompt", "").strip() or shots_data[i].get("_fill_failed")]
                    if _fill_targets and not _stop_event.is_set():
                        await _fill_once(None, clear_first)

                    def _usable(i):
                        return bool(shots_data[i].get("prompt", "").strip()) and not shots_data[i].get("_fill_failed")

                    _post_fill = [i for i in _fill_targets if _usable(i)]
                    for _i in _fill_targets:
                        if _i not in _post_fill:
                            await _llm_log(f"⚠ Shot {_i+1}: fill ไม่ได้ prompt หรือล้มเหลว — ข้ามรอบตรวจ/กำกับของช็อตนี้")
                    if _chain_check:
                        for _n, _i in enumerate(_post_fill, 1):
                            if _stop_event.is_set(): break
                            status_lbl.value = f"🔍 Consistency Check {_n}/{len(_post_fill)} (shot {_i+1})..."; page.update()
                            await _llm_log(f"🔍 Auto Consistency Check — shot {_i+1} ({_n}/{len(_post_fill)})")
                            await _do_check_run_ref[0](_i)
                    if _chain_dir:
                        _director_targets = [i for i in _post_fill if shots_data[i].get("prompt", "").strip()]
                        for _n, _i in enumerate(_director_targets, 1):
                            if _stop_event.is_set(): break
                            status_lbl.value = f"🎬 Visual Direction {_n}/{len(_director_targets)} (shot {_i+1})..."; page.update()
                            await _llm_log(f"🎬 Auto Visual Direction — shot {_i+1} ({_n}/{len(_director_targets)})")
                            await _do_director_run_ref[0](_i)
            finally:
                _chained_run[0] = False
            # สถานะปิดปุ่มค้างไว้ต่อจนถึง teardown ร่วมด้านล่าง (ยังอยู่ในรอบ fill เดียวกัน)
            llm_busy[0] = True; llm_prog.visible = True
            fill_prompts_btn.disabled = True; gen_shots_btn.disabled = True; _stop_btn_state(True)
        else:
            await _fill_once(None, clear_first)
        _fill_single_idx[0] = None
        _save_state(); llm_busy[0] = False; llm_prog.visible = False
        fill_prompts_btn.disabled = False; gen_shots_btn.disabled = False; _stop_btn_state(False)
        # a chained auto Check/Director leaves its own button disabled (they skip their teardown while _chained_run)
        director_prompts_btn.disabled = False; check_prompts_btn.disabled = _manga_vnext_mode[0]
        if _single_idx is not None:
            status_lbl.value = ("⏹ Stopped" if _stop_event.is_set() else
                                (f"✅ Regen shot {_single_idx+1} done" if shots_data[_single_idx].get("prompt","").strip()
                                 else f"⚠ Regen shot {_single_idx+1}: empty prompt"))
        else:
            failed_at = next((i + 1 for i, s in enumerate(shots_data) if s.get("_fill_failed")), None)
            failed_shots = [i+1 for i, s in enumerate(shots_data) if not s.get("prompt","").strip()]
            if _stop_event.is_set(): status_lbl.value = "⏹ Stopped"
            elif failed_at: status_lbl.value = f"⚠ Render prompt generation stopped at shot {failed_at} — later shots were not generated"
            elif failed_shots: status_lbl.value = f"⚠ Render prompts done — shots {failed_shots} have empty prompts, run Image Prompts again"
            else: status_lbl.value = f"✅ Render prompts complete — {len(shots_data)} shots"
        page.update()

        # ── วาดต่อทันทีที่ prompt ครบ ────────────────────────────────────────────
        # เดินต่อเฉพาะรอบที่ fill ทั้งเรื่อง (ไม่ใช่ regen ช็อตเดียว) ไม่ได้กด Stop และมี prompt ให้วาดจริง
        # ตั้งใจให้สั่งทีเดียวแล้วเดินออกจากเครื่องได้ — จบ fill แล้วเครื่องว่างรอคนมากดต่อคือเวลาที่เสียเปล่า
        _after_img = _sync_after_fill_images() if _single_idx is None else "none"
        if _after_img != "none" and not _stop_event.is_set():
            _runner = _do_test_all_ref[0] if _after_img == "test" else _do_run_all_ref[0]
            if _runner is None:
                await _llm_log("⚠ ต่อรอบวาดไม่ได้ — ปุ่ม Run All/Test All ยังไม่ถูกผูกไว้")
            elif not any(s.get("prompt", "").strip() for s in shots_data):
                status_lbl.value = "⚠ No prompts to render — skipping the image pass"; page.update()
            elif not (api_dd.value or selected_api[0]):
                status_lbl.value = "⚠ No Image Workflow selected — skipping the image pass"; page.update()
            else:
                await _llm_log("🧪 Auto Test All Shots — fill เสร็จแล้ว เริ่มวาด"
                               if _after_img == "test" else
                               "🎬 Auto Run All — fill เสร็จแล้ว เริ่มวาด")
                await _runner(None)

    def _prompt_style_options():
        return [
            ft.dropdown.Option(key="narrative", text="📖 Narrative (flowing prose)"),
            ft.dropdown.Option(key="tag_style", text="🏷️ Comma-separated (short tags)"),
            ft.dropdown.Option(key="sectioned_detailed", text="📋 Sectioned (Camera / Subject / Outfit / Environment / Lighting / Climax)"),
        ]

    def _sync_prompt_style() -> str:
        """จุดเดียวที่ resync _prompt_style[0] จริง — มี dropdown 2 ตัว (outline/fill dialog) ที่ต้อง
        mirror ค่าเดียวกันเสมอ แต่ Dropdown.on_change ไม่ fire บางครั้ง เดิมแก้แบบ patch แยกจุดต่อจุด
        ("if fill_prompt_style_dd.value: ...") ทำให้ตกหล่นง่าย (เช่น dialog Outline ไม่เคย resync เลย)
        รวมมาไว้ที่ฟังก์ชันเดียว ทุกจุดที่จะใช้ prompt_style เรียกฟังก์ชันนี้แทนอ่าน _prompt_style[0] ตรงๆ —
        ถ้า dropdown ตัวไหนค่าต่างจาก _prompt_style[0] ที่จำไว้ แปลว่า user เพิ่งเปลี่ยนตัวนั้นล่าสุด ให้ยึดตามนั้น
        แล้ว sync กลับเข้าอีกตัวให้ตรงกันเสมอ"""
        if outline_prompt_style_dd.value and outline_prompt_style_dd.value != _prompt_style[0]:
            _prompt_style[0] = outline_prompt_style_dd.value
        elif fill_prompt_style_dd.value and fill_prompt_style_dd.value != _prompt_style[0]:
            _prompt_style[0] = fill_prompt_style_dd.value
        outline_prompt_style_dd.value = _prompt_style[0]
        fill_prompt_style_dd.value = _prompt_style[0]
        return _prompt_style[0]

    def _sync_style() -> str:
        """เหมือน _sync_prompt_style แต่สำหรับ image style (Realistic/Anime/...) — มี dropdown 3 ตัว
        (panel หลัก + outline dialog + fill dialog) ที่ต้อง mirror กันเสมอ แต่ Dropdown.on_change ไม่
        fire บางครั้ง ทำให้เลือก style ใหม่ใน dialog หนึ่งแล้ว dialog อื่นไม่ตามด้วย"""
        if style_dd.value and style_dd.value != _sel_style[0]:
            _sel_style[0] = style_dd.value
        elif outline_style_dd.value and outline_style_dd.value != _sel_style[0]:
            _sel_style[0] = outline_style_dd.value
        style_dd.value = _sel_style[0]
        outline_style_dd.value = _sel_style[0]
        _update_style_desc()
        return _sel_style[0]

    def _style_src_hash(text: str) -> str:
        return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:16]

    def _a1_source_text() -> str:
        """The raw source a style should be judged against: Groom's untouched original while a
        Groom session is active (that is what Groom extracted from), otherwise the story box."""
        return (_groom_orig[0] if _groom_active[0] else story_field.value) or ""

    def _set_source_style(value: str, origin: str, src_text: str):
        _source_style[0] = (value or "").strip()
        _source_style_meta[0] = {"origin": origin, "src": _style_src_hash(src_text)} if _source_style[0] else {}
        _update_style_desc()

    def _adopt_a1_source_style(a1_style: str):
        """Called after every A1 run. Keeps the current style only when it was set by the user or
        captured by Groom AND the source is still the one it was set against; otherwise (older file, A1's own earlier guess, or
        a different source than before) A1's fresh reading replaces it — even with empty."""
        m = _source_style_meta[0] or {}
        same_src = m.get("src") == _style_src_hash(_a1_source_text())
        if _source_style[0] and same_src and m.get("origin") in ("user", "groom"):
            return
        _set_source_style(a1_style, "a1", _a1_source_text())

    def _resolve_style_kw(shot_scale: str = "medium") -> str:
        """เหมือน style_string_for_scale(_sync_style(), shot_scale) แต่ special-case "as_source"
        ก่อน — STYLE_MAP["as_source"][1] เป็น "" เสมอ (ไม่มี static keyword) เพราะค่าจริงมาจาก
        source_style ของเรื่องนี้เท่านั้น ต้อง resolve ที่นี่ จุดเดียวที่ image generation จริงเรียกใช้
        style_kw — ไม่งั้น "As Source" จะส่ง [Style] ว่างเปล่าเข้ารูปเสมอ (SOURCE-STYLE-A1 gap)"""
        _key = _sync_style() or "none"
        if _key == "as_source":
            return _source_style[0] or ""
        return style_string_for_scale(_key, shot_scale)

    _fill_desc_lbl = ft.Text("AI will generate a full prompt for every shot from scene_desc",
                             size=12, color=C["text2"])
    fill_prompt_style_dd = ft.Dropdown(
        value=_prompt_style[0], options=_prompt_style_options(),
        text_size=12, height=36, border_color=C["border"], focused_border_color=C["purple"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def _fill_prompt_style_changed(e):
        _prompt_style[0] = fill_prompt_style_dd.value or "narrative"
        outline_prompt_style_dd.value = _prompt_style[0]
        _save_state()
    fill_prompt_style_dd.on_change = _fill_prompt_style_changed

    # Fill ใช้ Minimal V3 อย่างเดียว — ไม่มีทั้งตัวเลือกและป้ายบอกสถานะใน UI แล้ว
    # (เอนจินที่มีตัวเดียวไม่ใช่ข้อมูลที่ผู้ใช้ต้องอ่านทุกครั้งที่เปิดหน้านี้)

    # Visual Preset dropdowns (Outline Settings + Fill dialog) mirror ONE state, _visual_preset.
    def _preset_dd(**kw) -> ft.Dropdown:
        return ft.Dropdown(
            value=_visual_preset[0],
            options=[ft.dropdown.Option(key=k, text=t) for k, t in VISUAL_PRESET_OPTIONS],
            text_size=12, border_color=C["border"], bgcolor=C["surface"],
            content_padding=po(left=8, right=4, top=0, bottom=0),
            tooltip="One preset sets framing, performance, composition, camera, lighting, environment and "
                    "motion for the first render-prompt pass. It never changes what happens in the story.", **kw)

    fill_preset_dd = _preset_dd(height=40, expand=True, focused_border_color=C["purple"])
    _preset_summary_lbl = ft.Text("", size=10, color=C["text3"])

    def _preset_summary_text() -> str:
        v = resolve_visual_preset(_visual_preset[0])
        return (f"Scale {v['shot_scale'] or 'open'}  ·  Expression {v['expression']}  ·  "
                f"Composition {v['composition']}  ·  Camera {v['camera_energy']}\n"
                f"Lighting {v['lighting']}  ·  Environment {v['environment']}  ·  Motion {v['motion']}")

    def _apply_visual_preset(key: str):
        _visual_preset[0] = normalize_preset(key)
        _shot_scale_preset[0] = resolve_visual_preset(_visual_preset[0])["shot_scale"] or "balanced"
        fill_preset_dd.value = _visual_preset[0]
        outline_preset_dd.value = _visual_preset[0]
        _preset_summary_lbl.value = _preset_summary_text()

    def _sync_visual_preset() -> str:
        """Same mirror pattern as _sync_style: whichever dropdown differs from the stored state was
        changed last (Dropdown events are not always delivered), so it wins and the other follows."""
        for _dd in (outline_preset_dd, fill_preset_dd):
            if _dd.value and _dd.value != _visual_preset[0]:
                _apply_visual_preset(_dd.value)
                break
        else:
            _apply_visual_preset(_visual_preset[0])
        return _visual_preset[0]

    def _visual_direction() -> str:
        """The VISUAL DIRECTION block for Fill / Fill Manga — deterministic, from the shared module."""
        return visual_direction_for_preset(_sync_visual_preset())

    def _visual_preset_changed(e):
        _sync_visual_preset()
        _save_state()
        if _preset_summary_lbl.page: _preset_summary_lbl.update()
    fill_preset_dd.on_select = _visual_preset_changed

    FILL_MODE_OPTIONS = [
        ("new",      "🆕 New each shot — write a fresh prompt"),
        ("continue", "🔗 Continue — edit the previous prompt if the scene did not change"),
    ]
    fill_mode_dd = ft.Dropdown(
        value=_fill_mode[0],
        options=[ft.dropdown.Option(key=k, text=t) for k, t in FILL_MODE_OPTIONS],
        text_size=12, height=36, border_color=C["border"], focused_border_color=C["purple"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    outline_fill_mode_dd = ft.Dropdown(
        value=_fill_mode[0],
        options=[ft.dropdown.Option(key=k, text=t) for k, t in FILL_MODE_OPTIONS],
        text_size=12, height=36, expand=True, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def _sync_fill_mode() -> str:
        if _FILL_MODE_FORCE_NEW:
            _fill_mode[0] = "new"
            fill_mode_dd.value = "new"
            outline_fill_mode_dd.value = "new"
            return "new"
        if fill_mode_dd.value and fill_mode_dd.value != _fill_mode[0]:
            _fill_mode[0] = fill_mode_dd.value
        elif outline_fill_mode_dd.value and outline_fill_mode_dd.value != _fill_mode[0]:
            _fill_mode[0] = outline_fill_mode_dd.value
        if _fill_mode[0] not in ("new", "continue"):
            _fill_mode[0] = "new"
        fill_mode_dd.value = _fill_mode[0]
        outline_fill_mode_dd.value = _fill_mode[0]
        return _fill_mode[0]
    def _fill_mode_changed(e):
        _fill_mode[0] = fill_mode_dd.value or "new"
        outline_fill_mode_dd.value = _fill_mode[0]
        _save_state()
    def _outline_fill_mode_changed(e):
        _fill_mode[0] = outline_fill_mode_dd.value or "new"
        fill_mode_dd.value = _fill_mode[0]
        _save_state()
    fill_mode_dd.on_change = _fill_mode_changed
    fill_mode_dd.on_select = _fill_mode_changed
    outline_fill_mode_dd.on_change = _outline_fill_mode_changed
    outline_fill_mode_dd.on_select = _outline_fill_mode_changed

    _manga_switch_outline = ft.Switch(
        value=_manga_page_mode[0], active_color=C["purple"],
        label="📦 Manga Page",
        label_text_style=ft.TextStyle(size=11, color=C["text2"]),
        tooltip=("Turn story events into multi-panel manga pages with automatic panel "
                 "breakdown and page layout."),
        visible=False,
    )
    _manga_switch_fill = ft.Switch(
        value=_manga_page_mode[0], active_color=C["purple"],
        label="📦 Manga Page (prompt as a manga page with multiple panels)",
        label_text_style=ft.TextStyle(size=11, color=C["text2"]),
        tooltip="Each shot is independent, but the prompt describes a manga page with multiple panels in one image",
        visible=False,
    )
    # MANGA-PAGE-ORPHAN-AUTHORITY-CLEANUP-A1: this switch's own producer (the old per-shot
    # run_fill_prompts(manga_page=True) multi-panel Fill engine) was already deleted as dead code
    # (see the comment a few lines below on the removed "Manga vNext" switch). With that producer
    # gone, a user could still flip this switch on (it stayed independently reachable/visible here,
    # regardless of Outline Style or Manga vNext) and get plain single-image Normal-vNext Fill
    # output while _manga_page_mode[0]=True was still passed into Checker/Director/Vision, which DO
    # branch on it (see _minimal_v3_format / the "MULTI-PANEL FORMAT LOCK" / "N-panel page..." rules
    # in llm_engine.run_check_prompts/run_director_prompts/run_vision_check) — corrupting a real
    # single-image prompt with multi-panel-only instructions. Manga vNext shots do not need this
    # flag at all: they self-identify per-shot via shot["prompt_style"] == "manga_page" (stamped by
    # run_fill_manga_vnext), which those three functions already check independently. So this
    # toggle has no live, legitimate use left — hidden from production UI; state var/on_change left
    # in place (old saves may still carry manga_page_mode=True for historical display, and
    # _sync_outline_mode()/_apply_manga_prompt_lock() still read/write .value on this widget).
    # MANGA-VNEXT-A1 follow-up: the separate "Manga vNext" switch was removed per user request — it
    # duplicated the pre-existing "Outline Style" dropdown's own "Manga" option and confused users
    # into picking the wrong one (the OLD "Outline Style: Manga" pathway's underlying Fill engine
    # (run_fill_prompts(manga_page=True)) was deleted earlier this session as dead legacy code, so
    # selecting it alone silently fell through to normal single-image vNext Fill instead of any
    # panel-based output). outline_mode_dd's own "manga" option now drives _manga_vnext_mode[0]
    # directly (see _sync_outline_mode below) — one control, one meaning. เดินรอบต่อท้าย fill อัตโนมัติ — fill รอบเดียวแบกกฎไว้เยอะจนกฎท้าย ๆ ถูกละเลย (คนหายจากฉาก,
    # แสงไร้แหล่งกำเนิด, สถานที่ปนข้ามฉาก) การเดิน checker/director ต่อจึงเก็บงานได้ดีกว่ายัดกฎเพิ่ม
    _auto_check_switch = ft.Switch(
        value=_auto_check_after_fill[0], active_color=C["amber"],
        label="🔍 Run Consistency Check after render prompts",
        label_text_style=ft.TextStyle(size=11, color=C["text2"]),
        tooltip="After generating render prompts, run the Consistency Check over the same shots automatically",
    )
    _auto_director_switch = ft.Switch(
        value=_auto_director_after_fill[0], active_color="#0F766E",
        label="🎬 Run Visual Direction after render prompts",
        label_text_style=ft.TextStyle(size=11, color=C["text2"]),
        tooltip="After generating render prompts (and checking, if that switch is on), run the Visual Direction pass automatically",
    )
    AFTER_FILL_IMAGE_OPTIONS = [
        ("none", "⏹ Stop after prompts"),
        ("test", "🧪 Test All Shots — quick preview, 1 image each"),
        ("run",  "🎬 Run All — full render"),
    ]
    _after_fill_images_dd = ft.Dropdown(
        value=_after_fill_images[0],
        options=[ft.dropdown.Option(key=k, text=t) for k, t in AFTER_FILL_IMAGE_OPTIONS],
        text_size=12, height=36, border_color=C["border"], focused_border_color=C["purple"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
        tooltip="Start drawing as soon as every prompt is written — needs an Image Workflow selected",
    )
    def _sync_after_fill_images() -> str:
        """อ่านค่าจาก dropdown ตรง ๆ ตอนจะใช้ ไม่ใช่พึ่ง event — Dropdown ของ Flet รุ่นนี้ไม่ยิง event
        ทุกครั้งที่เลือก (ดูเหตุผลเดียวกันใน _sync_fill_mode) ถ้าพึ่ง event อย่างเดียว ผู้ใช้เลือก
        "Run All" ไว้แล้วแต่ค่าที่โค้ดอ่านยังเป็น "none" — fill จบแล้วเงียบ ไม่มีอะไรเกิดขึ้น"""
        _v = _after_fill_images_dd.value
        if _v and _v != _after_fill_images[0]:
            _after_fill_images[0] = _v
        if _after_fill_images[0] not in ("none", "test", "run"):
            _after_fill_images[0] = "none"
        return _after_fill_images[0]

    def _after_fill_images_changed(e):
        _sync_after_fill_images()
        _save_state()
    _after_fill_images_dd.on_change = _after_fill_images_changed
    _after_fill_images_dd.on_select = _after_fill_images_changed

    def _auto_check_switch_changed(e):
        _auto_check_after_fill[0] = bool(_auto_check_switch.value); _save_state()
    def _auto_director_switch_changed(e):
        _auto_director_after_fill[0] = bool(_auto_director_switch.value); _save_state()
    _auto_check_switch.on_change = _auto_check_switch_changed
    _auto_director_switch.on_change = _auto_director_switch_changed
    # ค่า Prompt Format ที่ user เลือกไว้ก่อนโดน manga ล็อกเป็น narrative — เดิมโดนเขียนทับแล้วหายถาวร
    # ต่อให้ปลดล็อกแล้ว dropdown ก็ยังค้างเป็น narrative ไม่กลับไปเป็นค่าที่เลือกไว้เอง
    _MANGA_LOCK_TIP = ("Manga Page is ON — it uses its own multi-panel prompt format, so Prompt Format "
                       "is locked to Narrative. Turn Manga Page off to change it.")
    _prompt_style_pre_manga: list[str] = [saved.get("prompt_style_pre_manga", "")]
    # True = _manga_page_mode[0] ถูกบังคับเปิดโดย Outline Style "manga" ไม่ใช่ user กด toggle เอง —
    # ต้องแยกให้ออก เพราะตอนออกไปโหมด cinematic/sequential ต้องปลดคืนเฉพาะตัวที่ถูกบังคับ ส่วนตัวที่ user
    # เปิดเองต้องคงไว้ (Manga Page เป็น toggle อิสระของสองโหมดนั้น)
    # โหลดจากไฟล์: ถ้า outline_mode เป็น manga แปลว่าโหมดเป็นคนเปิด ไม่ใช่ toggle อิสระ
    # เดิมเดาใหม่จาก outline_mode ทุกครั้งตอนเปิดแอป ซึ่งขัดกับ manga_page_mode ที่เซฟแยก:
    # ถ้าไฟล์มี manga_page_mode=True แต่ outline_mode ไม่ใช่ manga จะได้ forced=False เสมอ
    # แปลว่า "user เปิดเอง" แล้ว _sync_outline_mode() จะไม่ยอมปลดล็อกให้ → Prompt Format ค้าง
    # เทาถาวรข้ามการเปิดแอด โดยไม่มีปุ่มให้ปลดใน dialog นี้ (สวิตช์ถูกซ่อนไว้)
    # เซฟค่าจริงลงไฟล์แทน — ไฟล์เก่าที่ยังไม่มี key นี้ค่อย fallback ไปใช้วิธีเดาแบบเดิม
    _manga_page_forced: list[bool] = [
        saved.get("manga_page_forced", saved.get("outline_mode") == "manga")]

    def _apply_manga_prompt_lock():
        # MANGA-PAGE-HIDDEN-STATE-LOCK-CLEANUP-A1: _manga_page_mode[0] has no legitimate live
        # producer left — both _manga_switch_fill and _manga_switch_outline are now permanently
        # visible=False (see their definitions above), so no real user interaction can set this
        # True any more. The only remaining way it can be True at runtime is a stale
        # manga_page_mode=true loaded from an old save file (see saved.get(...) at module init),
        # and this function used to react to that live — forcing Prompt Format to narrative and
        # disabling it, disabling Director Shot Scale, relabeling Shots->Pages, and disabling the
        # shot-count field — with no visible switch left for the user to undo it. _manga_page_mode
        # itself is deliberately left untouched (old-file compatibility / grep-zero elsewhere); this
        # function's own effects are neutralized by reading an always-False local instead of the
        # flag directly, so a hidden/loaded-true legacy value can no longer alter current UI/runtime.
        _manga_locked = False
        if _manga_locked:
            # จำค่าเดิมเฉพาะครั้งแรกที่ล็อก — ฟังก์ชันนี้ถูกเรียกซ้ำได้หลายรอบ ถ้าจำทุกครั้งรอบที่สอง
            # จะจำ "narrative" (ค่าที่เพิ่งทับไป) แล้วกู้ของจริงไม่ได้อีกเลย
            if not _prompt_style_pre_manga[0] and _prompt_style[0] != "narrative":
                _prompt_style_pre_manga[0] = _prompt_style[0]
            _prompt_style[0] = "narrative"
            fill_prompt_style_dd.value = "narrative"
            fill_prompt_style_dd.disabled = True
            fill_prompt_style_dd.tooltip = _MANGA_LOCK_TIP
            outline_prompt_style_dd.value = "narrative"
            outline_prompt_style_dd.disabled = True
            outline_prompt_style_dd.tooltip = _MANGA_LOCK_TIP
        else:
            if _prompt_style_pre_manga[0]:
                _prompt_style[0] = _prompt_style_pre_manga[0]
                fill_prompt_style_dd.value = _prompt_style[0]
                outline_prompt_style_dd.value = _prompt_style[0]
                _prompt_style_pre_manga[0] = ""
            fill_prompt_style_dd.disabled = False
            fill_prompt_style_dd.tooltip = None
            outline_prompt_style_dd.disabled = False
            outline_prompt_style_dd.tooltip = None
        # manga มีระบบ multi-panel framing ของตัวเอง (หลายระยะในภาพเดียว) ขัดกับ shot scale โดยธรรมชาติ —
        # ปิด dropdown ทิ้งไปเลย ไม่ปล่อยให้เลือกได้แล้วไม่มีอะไรเกิดขึ้น (จะงงกว่าไม่มีปุ่มให้เลือก)
        _manga_tip = ("Manga mode uses its own panel system — this dropdown is disabled while Manga Page is on"
                      if _manga_locked else None)
        # โหมด manga บังคับเปิด = ปลดผ่าน Outline Style ได้อยู่แล้ว ไม่ต้องโชว์สวิตช์ให้ซ้ำ
        # แต่ถ้าเปิดค้างโดยโหมดไม่ได้บังคับ นี่คือปุ่มเดียวที่ปลด Prompt Format ได้ ต้องเห็น
        # จำนวนหน้า Manga เป็นอำนาจของ B Manga ไม่ใช่ค่าที่ผู้ใช้พิมพ์
        if _manga_locked:
            _shots_lbl.value = "Pages"
            _shots_unit_lbl.value = "Auto"
            num_shots_field.disabled = True
            num_shots_field.tooltip = ("Page List chooses the final page count from the shared Beat Sheet sequential events — "
                                       "this field is not used while Manga mode is on")
        else:
            _shots_lbl.value = "Shots"
            _shots_unit_lbl.value = "shot"
            num_shots_field.disabled = False
            num_shots_field.tooltip = ("Static Pose Fill: how many shots (poses) to make from the prompt. It is used when the input is one still-picture prompt; "
                                       "a story is planned as usual." if _outline_mode[0] == "static_pose" else None)
        # MANGA-PAGE-ORPHAN-AUTHORITY-CLEANUP-A1: was `_manga_page_mode[0] and not _manga_page_forced[0]`
        # (the only way to reveal a "turn it off" switch when it happened to be stuck on). Now
        # unconditionally hidden along with _manga_switch_fill above — see that widget's comment for
        # why the underlying toggle has no live legitimate authority left to expose in the UI.
        _manga_switch_outline.visible = False
        director_scale_dd.disabled = _manga_locked
        director_scale_dd.tooltip = _manga_tip
        for _dd in _director_extra_dds:
            _dd.disabled = _manga_locked
            _dd.tooltip = _manga_tip
        if _manga_locked:
            director_scale_dd.value = ""
            _director_shot_scale[0] = ""

    def _manga_switch_fill_changed(e):
        _manga_page_mode[0] = _manga_switch_fill.value
        _manga_page_forced[0] = False   # user กดเอง ไม่ใช่โหมดบังคับ
        _manga_switch_outline.value = _manga_page_mode[0]
        _apply_manga_prompt_lock()
        _save_state(); page.update()
    def _manga_switch_outline_changed(e):
        _manga_page_mode[0] = _manga_switch_outline.value
        _manga_page_forced[0] = False   # user กดเอง ไม่ใช่โหมดบังคับ
        _manga_switch_fill.value = _manga_page_mode[0]
        _apply_manga_prompt_lock()
        _save_state(); page.update()
    _manga_switch_fill.on_change = _manga_switch_fill_changed
    _outline_compact_switch = ft.Switch(
        value=_outline_compact[0], active_color=C["accent"], visible=False,
        label="⚡ Compact outline (short shots, one request — Image Prompt writes the picture)",
        label_text_style=ft.TextStyle(size=11, color=C["text2"]),
        tooltip="Shot List records only what happens and who is in what state; Image Prompt writes the picture. One request instead of batches, so no shots get lost to a truncated response.",
    )
    def _outline_compact_changed(e):
        _outline_compact[0] = bool(_outline_compact_switch.value); _save_state()
    _outline_compact_switch.on_change = _outline_compact_changed
    _manga_switch_outline.on_change = _manga_switch_outline_changed

    # ── ตัวช่วยจัดหน้า dialog (Outline Settings / Fill Prompts) ── ลดการเขียน Text(label)+control ซ้ำ 11 รอบ และทำให้
    # ระยะห่าง/ขนาดตัวอักษรของทุกช่องเท่ากันเองโดยไม่ต้องมี Container(height=10) คั่นมือ
    def _og_field(label: str, ctrl, hint: str = ""):
        col = [ft.Text(label, size=11, color=C["text2"], weight=ft.FontWeight.W_600)]
        if hint:
            col.append(ft.Text(hint, size=9, color=C["text3"]))
        col.append(ctrl)
        return ft.Container(content=ft.Column(col, spacing=3, tight=True),
                            padding=po(left=0, right=0, top=0, bottom=12))

    def _og_group(title: str, *fields):
        return ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Text(title, size=10, weight=ft.FontWeight.W_700, color=C["text3"]),
                    padding=po(left=0, right=0, top=0, bottom=8),
                    border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
                ),
                ft.Container(height=8),
                *fields,
            ], spacing=0, tight=True),
            padding=po(left=0, right=0, top=0, bottom=10),
        )

    fill_confirm_title = ft.Text("✨ Image Prompts", size=14, weight=ft.FontWeight.W_700, color=C["text1"])
    _fill_regen_btn = ft.TextButton(
        "Regenerate",
        on_click=lambda e: (setattr(fill_confirm_dialog, "open", False), page.update(),
                            asyncio.create_task(_do_fill_prompts_run(clear_first=True))),
        style=ft.ButtonStyle(color=C["red"]), visible=False,
    )
    _fill_empty_btn = ft.TextButton("Empty only",
        on_click=lambda e: (setattr(fill_confirm_dialog, "open", False), page.update(),
                            asyncio.create_task(_do_fill_prompts_run(clear_first=False))),
        style=ft.ButtonStyle(color=C["accent"]))
    _fill_rebuild_btn = ft.TextButton("Rebuild all",
        on_click=lambda e: (setattr(fill_confirm_dialog, "open", False), page.update(),
                            asyncio.create_task(_do_fill_prompts_run(clear_first=True))),
        style=ft.ButtonStyle(color=C["red"]))

    # Single column, top to bottom: what to write (Visual Preset), then what runs afterwards. The
    # AFTER FILL section is job-level, so it is hidden for a single-shot regen.
    _fill_after_col = ft.Column([
        _og_group("AFTER RENDER PROMPTS",
            ft.Row([_auto_check_switch, _auto_director_switch], spacing=24, wrap=True, run_spacing=4),
            ft.Container(height=10),
            _og_field("Then, when every prompt is written", _after_fill_images_dd,
                      "needs an Image Workflow selected in the panel"),
        ),
    ], spacing=0, tight=True)

    _fill_content = ft.Column([
        _fill_desc_lbl,
        ft.Container(height=14),
        _og_group("VISUAL DIRECTION",
            ft.Row([fill_preset_dd]),
            ft.Container(content=_preset_summary_lbl, padding=po(left=2, right=0, top=6, bottom=0)),
            _manga_switch_fill,
        ),
        _fill_after_col,
    ], spacing=0, tight=True)

    def _fill_fit_to_window():
        """Content-sized dialog: one fixed-width column, no scroll area, so there is no empty block
        under the last section (the height follows the content)."""
        _w = page.width or page.window.width or 1200
        _fill_content.width = max(420, min(560, int(_w) - 140))

    fill_confirm_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=fill_confirm_title,
        content=_fill_content,
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: (setattr(fill_confirm_dialog, "open", False), page.update()),
                         style=ft.ButtonStyle(color=C["text2"])),
            _fill_empty_btn, _fill_rebuild_btn, _fill_regen_btn,
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(fill_confirm_dialog)

    async def do_fill_prompts(e, single_idx=None):
        if llm_busy[0]: return
        if not shots_data: status_lbl.value = "⚠ Run 📐 Outline first"; page.update(); return
        llm_wf = llm_dd.value
        if not llm_wf:
            status_lbl.value = "⚠ Select LLM workflow"; page.update(); return
        _fill_single_idx[0] = single_idx
        if single_idx is not None:
            fill_confirm_title.value = f"✨ Regen Prompt — Shot {single_idx+1}"
            _fill_desc_lbl.value = "AI will regenerate the prompt for this shot only, using the full outline as context"
            _fill_empty_btn.visible = False; _fill_rebuild_btn.visible = False; _fill_regen_btn.visible = True
            _fill_after_col.visible = False
        else:
            fill_confirm_title.value = "✨ Image Prompts"
            _has_existing = any(s.get("prompt", "").strip() for s in shots_data)
            _fill_desc_lbl.value = ("Some shots already have prompts — choose \"Empty only\" or \"Rebuild all\""
                                    if _has_existing else "AI will generate a full prompt for every shot from scene_desc")
            _fill_empty_btn.visible = True; _fill_rebuild_btn.visible = True; _fill_regen_btn.visible = False
            _fill_after_col.visible = True
        _sync_visual_preset()   # Fill shows whatever preset Outline (or a previous Fill) last set
        _sync_style()
        _sync_fill_mode()
        _fill_fit_to_window()
        _sync_outline_mode()   # เหตุผลเดียวกับใน do_gen_shots — กัน _manga_page_mode[0] ค้างแล้ว lock ไม่ปลด
        fill_confirm_dialog.open = True; page.update()
    fill_prompts_btn.on_click = do_fill_prompts

    # ── Check Prompts ─────────────────────────────────────────────────
    check_prompts_btn = ft.IconButton(
        icon=ft.Icons.PERSON_SEARCH, icon_color="white", icon_size=20,
        tooltip="Check Prompts", bgcolor=C["amber"],
        width=40, height=38,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
    )

    _check_extra_req = ft.TextField(
        hint_text="Additional instructions (optional)...",
        multiline=True, min_lines=2, max_lines=3, text_size=11,
        border_color=C["border"], focused_border_color=C["amber"],
        bgcolor=C["surface"], content_padding=po(left=8, right=8, top=6, bottom=6),
    )

    async def _do_check_prompts_run(single_idx=None):
        # MANGA-VNEXT-A1 §46: runtime guard — the current Prompt Checker is not valid for Manga
        # vNext's page/panel contract. UI disabled/hidden state alone is not enough; block execution
        # here too so no proxy/lightbox/auto-chain call site can bypass it.
        if _manga_vnext_mode[0]:
            await _llm_log("⏭ Consistency Check skipped — Manga vNext mode has no Consistency Check (dedicated Manga "
                           "Consistency Check not built yet)")
            return
        _sync_prompt_style()
        _sync_style()
        _sync_genre()
        llm_wf = llm_dd.value
        if not llm_wf:
            status_lbl.value = "⚠ Select LLM workflow first"; page.update(); return
        if not _chained_run[0]:
            _stop_event.clear()     # ในลูป: Stop ที่ผู้ใช้เพิ่งกดต้องไม่ถูกล้างทิ้ง
        llm_busy[0] = True; llm_prog.visible = True
        check_prompts_btn.disabled = True; fill_prompts_btn.disabled = True
        gen_shots_btn.disabled = True; _stop_btn_state(True)
        # CHECKER-C1 (mirrors DIRECTOR-C1): single_idx may be a single int (manual single-shot), a
        # list/set/tuple of indices (Grok auto-chain batch — see the call site in
        # _do_fill_prompts_run_inner), or None (all shots — manual "Check All").
        if isinstance(single_idx, (list, set, tuple)):
            targets = list(single_idx)
        elif single_idx is not None:
            targets = [single_idx]
        else:
            targets = range(len(shots_data))
        await _llm_log(f"🔍 CHECKER TARGETS: single_idx={single_idx!r} -> shots {[int(t) + 1 for t in targets]}")
        for idx in targets:
            for key in ("_check_status", "_check_issues", "_check_reason", "_dir_status", "_dir_issues", "_dir_reason"):
                shots_data[idx].pop(key, None)
        # filter dead files from LIVE gallery state (not a pre-run snapshot — see _do_fill_prompts_run for why)
        for k in list(gallery_images.keys()): gallery_images[k] = [img for img in gallery_images[k] if Path(img["path"]).exists()]
        rebuild_shots_col(skip_gallery_filter=True, do_update=False); page.update()

        async def on_shot_check(idx, shot_data):
            # V3-PROMPT-BLOCK-SPACING-NORMALIZATION-A1: same ingress-level pass as Fill above —
            # Checker output reaches shots_data here directly, never through Raw JSON.
            if shot_data.get("prompt"):
                shot_data["prompt"] = _normalize_v3_prompt_block_spacing(shot_data["prompt"])
            if idx < len(shots_data): shots_data[idx] = shot_data
            else: shots_data.append(shot_data)
            if idx < len(shots_col.controls):
                _shot_rows.pop(idx, None); _shot_prompt_fields.pop(idx, None)
                _build_shot_row(idx, shot_data)
                shots_col.controls[idx] = shots_col.controls.pop()
            status_lbl.value = f"🔍 Checking shot {idx+1}/{len(shots_data)}"; page.update()

        async def on_err_check(msg):
            await _llm_log(f"❌ {msg}"); status_lbl.value = f"❌ {msg}"; page.update()

        def on_prog_check(cur, tot):
            status_lbl.value = f"🔍 Checking shot {cur}/{tot}..."; page.update()

        # FILL-VNEXT-LOCK-CONTRACT-A1: was hardcoded to FILL_ENGINE_MINIMAL_V3 regardless of what
        # Fill actually ran. "" lets run_check_prompts fall through to each shot's own
        # shot["_fill_engine"] (stamped by run_fill_vnext) instead of asserting a stale engine.
        _ck_eng = ""
        await run_consistency_check(
            list(shots_data), llm_wf,
            char_field.value.strip(), scene_field.value.strip(), requirement_field.value.strip(),
            story_field.value.strip(), _story_summary[0] or story_summary_field.value.strip(),
            on_shot_check, on_err_check, on_log=_llm_log, on_progress=on_prog_check,
            stop_event=_stop_event, single_shot_idx=single_idx,
            extra_req=_check_extra_req.value.strip(),
            image_style=STYLE_MAP.get(_sync_style() or "none", ("", ""))[1],
            genre=_sel_genre[0], genre2=_sel_genre2[0],
            dialogue_style=_dialogue_style[0],
            dialogue_lang=_dialogue_lang[0], desc_lang=_desc_lang[0], source_lang=_source_lang[0],
            grok_api_key=_grok_api_key[0], grok_model=_grok_model[0],
            prompt_style=_prompt_style[0],
            # MANGA-PAGE-ORPHAN-AUTHORITY-CLEANUP-A1: was manga_page=_manga_page_mode[0]. That old
            # per-shot toggle's own Fill engine is dead, and Manga vNext shots already self-identify
            # per-shot via shot["prompt_style"] == "manga_page" (which run_check_prompts checks
            # independently) — so this global flag had no legitimate live effect left, only the
            # ability to corrupt a Normal-vNext single-image prompt with multi-panel-only
            # instructions ("MULTI-PANEL FORMAT LOCK", "N-panel page...") if a user had flipped the
            # (now-hidden) old Manga Page switch. Always False: the flag can no longer change
            # Checker behavior for any call.
            manga_page=False,
            outline_mode=_outline_mode_for_engines(),
            props_desc=_props_to_str(props_field.value).strip(),
            fill_engine=_ck_eng,
            narration_type=_narration_type[0],
            clothing_arc=_outline_clothing_arc[0],
            a1=_outline_vnext_a1[0] or None,
        )
        _save_state()
        if not _chained_run[0]:
            llm_busy[0] = False; llm_prog.visible = False
            check_prompts_btn.disabled = False; fill_prompts_btn.disabled = False
            gen_shots_btn.disabled = False; _stop_btn_state(False)
        for k in list(gallery_images.keys()): gallery_images[k] = [img for img in gallery_images[k] if Path(img["path"]).exists()]
        rebuild_shots_col(skip_gallery_filter=True, do_update=False)
        if not _chained_run[0]:
            status_lbl.value = "⏹ Stopped" if _stop_event.is_set() else "✅ Check complete"
        page.update()

    _do_check_run_ref[0] = _do_check_prompts_run

    _check_pending_idx: list = [None]
    _check_desc_lbl = ft.Text("AI will check every prompt against scene_desc\nand auto-fix inconsistencies",
                               size=12, color=C["text2"])

    check_confirm_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("🔍 Consistency Check", size=14, weight=ft.FontWeight.W_700, color=C["text1"]),
        content=ft.Column([_check_desc_lbl, _check_extra_req], spacing=10, tight=True),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: (setattr(check_confirm_dialog, "open", False), page.update()),
                         style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("Run Check",
                on_click=lambda e: asyncio.create_task(_check_confirm_and_run(e)),
                style=ft.ButtonStyle(color=C["amber"])),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(check_confirm_dialog)

    async def _check_confirm_and_run(e):
        check_confirm_dialog.open = False; page.update()
        try:
            await _do_check_prompts_run(_check_pending_idx[0])
        except Exception as _ex:
            import traceback
            _tb = traceback.format_exc()
            status_lbl.value = f"❌ Consistency Check error: {_ex}"; page.update()
            # on_log ไม่มีในสโคปนี้ — การเช็คมันจะโยน NameError ระหว่างกำลังรายงาน error อื่นอยู่
            # แล้วกลบต้นเหตุจริงทิ้ง ตัวที่ล็อกได้จริงคือ _llm_log ซึ่งอยู่ในสโคป
            await _llm_log(f"❌ Consistency Check exception:\n{_tb}")

    def _open_check_dialog(single_idx=None):
        if llm_busy[0]: return
        if not shots_data: status_lbl.value = "⚠ No shots yet"; page.update(); return
        llm_wf = llm_dd.value
        if not llm_wf:
            status_lbl.value = "⚠ Select LLM workflow"; page.update(); return
        if not any(s.get("prompt", "").strip() for s in shots_data):
            status_lbl.value = "⚠ No prompts yet — run ✨ Image Prompts first"; page.update(); return
        _check_pending_idx[0] = single_idx
        if single_idx is not None:
            _check_desc_lbl.value = f"Checking Shot {single_idx+1} only"
        else:
            _check_desc_lbl.value = "AI will check every prompt against scene_desc\nand auto-fix inconsistencies"
        # Checker ยังไม่รู้จัก shot_scale — ถ้า Director ปรับระดับรายละเอียดตามระยะไปแล้ว (ตัด eyes/lips/fingers
        # ออกจากช็อต wide) Checker อาจเห็นว่า prompt สั้นกว่า scene_desc แล้วเติมกลับเข้าไป ทำให้งานของ Director เสีย
        _targets = [single_idx] if single_idx is not None else range(len(shots_data))
        if any(shots_data[i].get("_dir_status") for i in _targets if i < len(shots_data)):
            _check_desc_lbl.value += ("\n⚠ Visual Direction already trimmed detail to match Shot Scale — "
                                      "Consistency Check may put those details back")
        check_confirm_dialog.open = True; page.update()

    async def do_check_prompts(e): _open_check_dialog()
    check_prompts_btn.on_click = do_check_prompts

    # ── Director ──────────────────────────────────────────────────────
    director_prompts_btn = ft.IconButton(
        icon=ft.Icons.RECORD_VOICE_OVER, icon_color="white", icon_size=20,
        tooltip="Direct Prompts — camera & lighting", bgcolor="#0F766E",
        width=40, height=38,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
    )

    _director_extra_req = ft.TextField(
        hint_text="Special instructions (optional), e.g. dramatic lighting...",
        multiline=True, min_lines=2, max_lines=3, text_size=11,
        border_color=C["border"], focused_border_color="#0F766E",
        bgcolor=C["surface"], content_padding=po(left=8, right=8, top=6, bottom=6),
    )

    # ค่าเริ่มต้นต้องเป็น "" (ตามที่ outline/fill กำหนดไว้แล้ว) ไม่งั้นกด Run Director ทุกครั้งจะเขียนทับ
    # แผนระยะทั้งซีเควนซ์โดยผู้ใช้ไม่ได้ตั้งใจ — คนละหน้าที่กับ dropdown ใน Fill/Outline: อันนี้ปรับทีหลังได้
    # โดยไม่ต้องรัน fill ใหม่
    _director_shot_scale: list[str] = [""]
    DIRECTOR_SCALE_OPTIONS = [
        ("",         "📋 Follow Outline — keep the assigned distances"),
        ("balanced", "⚖️ Balanced — mixed distances, action stays readable"),
        ("epic",     "🏔️ Expansive — wide, the location dominates"),
        ("intimate", "🔍 Intimate — medium, reactions dominate"),
        ("chamber",  "🚪 Chamber — close, compressed depth, faces and hands"),
    ]
    director_scale_dd = ft.Dropdown(
        value="",
        options=[ft.dropdown.Option(key=k, text=t) for k, t in DIRECTOR_SCALE_OPTIONS],
        text_size=12, height=42, dense=True,
        border_color=C["border"], focused_border_color="#0F766E",
        bgcolor=C["surface"], content_padding=po(left=10, right=4, top=0, bottom=0),
        disabled=False,
        tooltip="Camera distance across the sequence. Follow Outline keeps the scales Outline already assigned.",
    )
    def _director_scale_changed(e):
        _director_shot_scale[0] = director_scale_dd.value or ""
        _director_mark_custom()
    director_scale_dd.on_select = _director_scale_changed

    # ไม่ใช้ label= ของ Dropdown แล้ว — floating label ต้องการที่แนวตั้ง พอ height=36 มันเลยทับเส้นขอบ
    # และดันข้อความที่เลือกจนถูกตัด ("Enhance — same source, riche") ใช้ Text ด้านบนแทนผ่าน _dir_field()
    def _dir_dd(label: str, value: str, options: list[tuple[str, str]], on_pick) -> ft.Dropdown:
        dd = ft.Dropdown(
            value=value,
            options=[ft.dropdown.Option(key=k, text=t) for k, t in options],
            text_size=12, height=42, dense=True, expand=True,
            border_color=C["border"], focused_border_color="#0F766E",
            bgcolor=C["surface"], content_padding=po(left=10, right=4, top=0, bottom=0),
            disabled=False,
        )
        dd.on_select = on_pick
        return dd

    # expand ต้องเป็น False เมื่อวางตรงๆ ใน Column — ไม่งั้น Column ลูกจะยืดกินพื้นที่ว่างที่เหลือ
    # ทำให้เกิดช่องโหว่ใหญ่ระหว่างช่อง (เคยพังมาแล้ว) เปิด expand เฉพาะตอนวางเรียงกันใน Row เท่านั้น
    def _dir_field(label: str, ctrl, expand: bool = False):
        return ft.Column([
            ft.Text(label, size=11, color=C["text2"], weight=ft.FontWeight.W_600),
            ctrl,
        ], spacing=4, tight=True, expand=expand)

    director_light_dd = _dir_dd(
        "Lighting",
        _director_lighting[0] if _director_lighting[0] in ("scene", "enhance", "dramatic", "soft") else "enhance",
        [
            ("scene",     "🌙 Follow scene — existing source only"),
            ("enhance",   "💡 Enhance — same source, richer"),
            ("dramatic",  "🎭 Dramatic — stronger light / shadow separation"),
            ("soft",      "🌤 Soft — diffused, gentle falloff"),
        ],
        lambda e: (_director_lighting.__setitem__(0, director_light_dd.value or "enhance"),
                   _director_mark_custom(), _save_state()),
    )
    director_beauty_dd = _dir_dd(
        "Composition",
        # PROFESSIONAL-AUTONOMY-CLEANUP-A2 §8: "bold" is a valid option below but was missing from
        # this acceptance tuple — a saved/current value of "bold" silently fell back to "balanced"
        # every time this control rebuilt. Fixed by including it, matching the options list.
        _director_beauty[0] if _director_beauty[0] in ("off", "balanced", "cinematic", "bold") else "balanced",
        [
            ("off",       "🚫 Preserve — keep the framing as it is"),
            ("balanced",  "✨ Balanced — clear, deliberate visual organization"),
            ("cinematic", "🖼 Cinematic — stronger visual hierarchy and depth"),
            ("bold",      "◼ Bold — a strong, graphically confident frame"),
        ],
        lambda e: (_director_beauty.__setitem__(0, director_beauty_dd.value or "balanced"),
                   _director_mark_custom(), _save_state()),
    )
    director_env_dd = _dir_dd(
        "Environment Response",
        _director_environment[0] if _director_environment[0] in ("off", "natural", "emphasized") else "natural",
        [
            ("off",     "🚫 Off — leave hair/cloth as written"),
            ("natural", "💨 Natural — existing weather acts on surfaces"),
            ("emphasized", "🌪 Emphasized — make the existing environment's effect more visually prominent"),
        ],
        lambda e: (_director_environment.__setitem__(0, director_env_dd.value or "natural"),
                   _director_mark_custom(), _save_state()),
    )
    director_motion_dd = _dir_dd(
        "Motion",
        _director_motion[0] if _director_motion[0] in ("natural", "dynamic", "peak") else "natural",
        [
            ("natural", "📋 Natural — keep the resolved pose"),
            ("dynamic", "⚡ Dynamic — energetic treatment of the same action"),
            ("peak",    "🧊 Peak Moment — strongest readable phase of the same action"),
        ],
        lambda e: (_director_motion.__setitem__(0, director_motion_dd.value or "natural"),
                   _director_mark_custom(), _save_state()),
    )
    director_camera_dd = _dir_dd(
        "Camera Energy",
        _director_camera[0] if _director_camera[0] in ("stable", "natural", "tense", "chaotic") else "natural",
        [
            ("stable",  "🧱 Stable — level, composed, restrained"),
            ("natural", "🎥 Natural — angle chosen to suit the beat"),
            ("tense",   "⚠️ Tense — increased visual pressure"),
            ("chaotic", "💥 Chaotic — strong visual instability"),
        ],
        lambda e: (_director_camera.__setitem__(0, director_camera_dd.value or "natural"),
                   _director_mark_custom(), _save_state()),
    )
    _director_lighting[0] = director_light_dd.value or "enhance"
    _director_beauty[0] = director_beauty_dd.value or "balanced"
    _director_environment[0] = director_env_dd.value or "natural"
    _director_motion[0] = director_motion_dd.value or "natural"
    _director_camera[0] = director_camera_dd.value or "natural"
    _director_extra_dds[:] = [director_light_dd, director_beauty_dd, director_env_dd,
                              director_motion_dd, director_camera_dd]

    # preset เป็นแค่ชุดค่าของ 6 dropdown ไม่ใช่ logic Director ชุดที่สอง — พอ user ขยับ dropdown เอง
    # ป้ายก็เด้งเป็น Custom เพื่อไม่ให้หน้าจอโกหกว่ากำลังรัน preset ที่ไม่ตรงกับค่าจริง
    # (Expression is Fill-time performance — the Director has no such control, so it is dropped here.)
    DIRECTOR_PRESETS = {
        _k: (_v["shot_scale"], _v["composition"], _v["camera_energy"], _v["lighting"],
             _v["environment"], _v["motion"])
        for _k, _v in VISUAL_PRESETS.items()
    }

    def _director_mark_custom():
        """เรียกทุกครั้งที่ user ขยับ dropdown เอง — ป้าย Preset ต้องบอกความจริงเสมอ

        ถ้าค่าหกตัวกลับไปตรงกับ preset ไหนพอดี ก็แสดงชื่อ preset นั้น ไม่ใช่ค้างที่ Custom
        เพราะสองสถานะนี้สั่ง Visual Direction เหมือนกันเป๊ะ"""
        _combo = (director_scale_dd.value or "", director_beauty_dd.value or "balanced",
                  director_camera_dd.value or "natural", director_light_dd.value or "enhance",
                  director_env_dd.value or "natural", director_motion_dd.value or "natural")
        _match = next((k for k, v in DIRECTOR_PRESETS.items() if v == _combo), "custom")
        if _director_preset[0] != _match:
            _director_preset[0] = _match
            director_preset_dd.value = _match
            director_preset_dd.update()

    def _director_apply_preset(e):
        _key = director_preset_dd.value or "natural"
        _director_preset[0] = _key
        _combo = DIRECTOR_PRESETS.get(_key)
        if not _combo:                      # "custom" ไม่มีชุดค่าของตัวเอง ปล่อยค่าที่ตั้งไว้ตามเดิม
            _save_state(); return
        (_sc, _comp, _cam, _light, _env, _mot) = _combo
        _director_shot_scale[0] = _sc;      director_scale_dd.value = _sc
        _director_beauty[0] = _comp;        director_beauty_dd.value = _comp
        _director_camera[0] = _cam;         director_camera_dd.value = _cam
        _director_lighting[0] = _light;     director_light_dd.value = _light
        _director_environment[0] = _env;    director_env_dd.value = _env
        _director_motion[0] = _mot;         director_motion_dd.value = _mot
        for _dd in (director_scale_dd, director_beauty_dd, director_camera_dd,
                    director_light_dd, director_env_dd, director_motion_dd):
            _dd.update()
        _save_state()

    director_preset_dd = ft.Dropdown(
        value=_director_preset[0] if _director_preset[0] in
              (list(DIRECTOR_PRESETS) + ["custom"]) else "natural",
        options=[ft.dropdown.Option(key=k, text=t) for k, t in
                 list(VISUAL_PRESET_OPTIONS) + [("custom", "🎛 Custom — your own combination")]],
        text_size=12, height=42, dense=True,
        border_color=C["border"], focused_border_color="#0F766E",
        bgcolor=C["surface"], content_padding=po(left=10, right=4, top=0, bottom=0),
        tooltip="A preset only sets the six controls below — it adds no separate behaviour",
    )
    director_preset_dd.on_select = _director_apply_preset

    async def _do_director_run(single_idx=None):
        # Manga Fill already owns per-panel camera/framing, so the Normal Director must not add a
        # second camera authority on top of a completed Manga page prompt.
        if _manga_vnext_mode[0]:
            await _llm_log("⏭ Visual Direction skipped — Manga vNext mode: Manga Prompts owns panel camera/framing")
            return
        _sync_prompt_style()
        _sync_style()
        _sync_genre()
        llm_wf = llm_dd.value
        if not llm_wf:
            status_lbl.value = "⚠ Select LLM workflow first"; page.update(); return
        if not _chained_run[0]:
            _stop_event.clear()     # ในลูป: Stop ที่ผู้ใช้เพิ่งกดต้องไม่ถูกล้างทิ้ง
        llm_busy[0] = True; llm_prog.visible = True
        director_prompts_btn.disabled = True; check_prompts_btn.disabled = True
        fill_prompts_btn.disabled = True; gen_shots_btn.disabled = True; _stop_btn_state(True)
        status_lbl.value = f"🎬 Visual Direction..." ; page.update()
        # DIRECTOR-C1: single_idx อาจเป็น int เดียว (manual single-shot), list ของ index (Grok auto-chain
        # batch — ดูจุดเรียกใน _do_fill_prompts_run_inner), หรือ None (ทุกช็อต — manual "Run Director")
        if isinstance(single_idx, (list, set, tuple)):
            targets = list(single_idx)
        elif single_idx is not None:
            targets = [single_idx]
        else:
            targets = range(len(shots_data))
        # visible in the log so "it ran every shot" can be told apart from "it was asked to run every shot"
        await _llm_log(f"🎬 DIRECTOR TARGETS: single_idx={single_idx!r} -> shots {[int(t) + 1 for t in targets]}")
        for idx in targets:
            for key in ("_dir_status", "_dir_issues", "_dir_reason", "_check_status", "_check_issues", "_check_reason"):
                shots_data[idx].pop(key, None)
        # filter dead files from LIVE gallery state (not a pre-run snapshot — see _do_fill_prompts_run for why)
        for k in list(gallery_images.keys()): gallery_images[k] = [img for img in gallery_images[k] if Path(img["path"]).exists()]
        rebuild_shots_col(skip_gallery_filter=True, do_update=False); page.update()

        async def on_shot_dir(idx, shot_data):
            # V3-PROMPT-BLOCK-SPACING-NORMALIZATION-A1: Director's own prompt is already normalized
            # inside run_director_prompts; this call is a defensive no-op there (idempotent) and the
            # only real effect for any other future caller of this callback shape.
            if shot_data.get("prompt"):
                shot_data["prompt"] = _normalize_v3_prompt_block_spacing(shot_data["prompt"])
            if idx < len(shots_data): shots_data[idx] = shot_data
            else: shots_data.append(shot_data)
            if idx < len(shots_col.controls):
                _shot_rows.pop(idx, None); _shot_prompt_fields.pop(idx, None)
                _build_shot_row(idx, shot_data)
                shots_col.controls[idx] = shots_col.controls.pop()
            status_lbl.value = f"🎬 Visual Direction shot {idx+1}/{len(shots_data)}"; page.update()

        async def on_err_dir(msg):
            await _llm_log(f"❌ {msg}"); status_lbl.value = f"❌ {msg}"; page.update()

        def on_prog_dir(cur, tot):
            status_lbl.value = f"🎬 Visual Direction shot {cur}/{tot}..."; page.update()

        # อ่านค่าจาก control จริงๆ ณ วินาทีที่กดปุ่ม แล้ว log ทั้งข้อความที่ user เห็นและค่าที่จะส่งไป
        # ถ้าสองอย่างนี้ไม่ตรงกัน แปลว่า dropdown ตัวนั้นไม่ได้ push ค่าใหม่เข้ามา — เห็นได้ทันทีจาก log
        def _dd_display(dd) -> str:
            for _o in (dd.options or []):
                if _o.key == dd.value:
                    return (_o.text or _o.key or "").strip()
            return str(dd.value)

        await _llm_log(
            "🎬 VISUAL DIRECTION UI SNAPSHOT\n"
            + "\n".join(
                f"   {_lbl:<12} value={_dd.value!r:<14} display={_dd_display(_dd)}"
                for _lbl, _dd in (("preset", director_preset_dd), ("scale", director_scale_dd),
                                  ("composition", director_beauty_dd), ("camera", director_camera_dd),
                                  ("lighting", director_light_dd), ("environment", director_env_dd),
                                  ("motion", director_motion_dd))
            )
        )

        await run_visual_direction(
            list(shots_data), llm_wf,
            char_field.value.strip(), scene_field.value.strip(), requirement_field.value.strip(),
            story_field.value.strip(), _story_summary[0] or story_summary_field.value.strip(),
            on_shot_dir, on_err_dir, on_log=_llm_log, on_progress=on_prog_dir,
            stop_event=_stop_event, single_shot_idx=single_idx,
            extra_req=_director_extra_req.value.strip(),
            image_style=STYLE_MAP.get(_sync_style() or "none", ("", ""))[1],
            image_size=sel_size[0], genre=_sel_genre[0], genre2=_sel_genre2[0],
            dialogue_style=_dialogue_style[0],
            grok_api_key=_grok_api_key[0], grok_model=_grok_model[0],
            prompt_style=_prompt_style[0],
            # MANGA-PAGE-ORPHAN-AUTHORITY-CLEANUP-A1: see run_check_prompts call above — same orphan
            # flag, same fix. Manga vNext shots self-identify via shot["prompt_style"] ==
            # "manga_page" inside run_director_prompts already; this global flag is always False now.
            manga_page=False,
            props_desc=_props_to_str(props_field.value).strip(),
            # ทุกค่ามาจาก dropdown ที่ user เห็นอยู่ ไม่ใช่ state ที่เซฟไว้ — preset ไม่ได้ถูกส่งไปเลย
            # มันเป็นแค่มาโครที่ตั้งค่าหกตัวนี้ให้ ไม่ใช่คำสั่งสร้างสรรค์ซ้อนอีกชั้น
            shot_scale_override=director_scale_dd.value or "",
            shot_scale_preset=_shot_scale_preset[0] or "balanced",
            lighting_pass=director_light_dd.value or "enhance",
            composition_pass=director_beauty_dd.value or "balanced",
            camera_energy=director_camera_dd.value or "natural",
            environment_pass=director_env_dd.value or "natural",
            motion_pass=director_motion_dd.value or "natural",
            fill_engine=_fill_engine[0],
            a1=_outline_vnext_a1[0] or None,
        )
        _save_state()
        if not _chained_run[0]:
            llm_busy[0] = False; llm_prog.visible = False
            director_prompts_btn.disabled = False; check_prompts_btn.disabled = False
            fill_prompts_btn.disabled = False; gen_shots_btn.disabled = False; _stop_btn_state(False)
        for k in list(gallery_images.keys()): gallery_images[k] = [img for img in gallery_images[k] if Path(img["path"]).exists()]
        rebuild_shots_col(skip_gallery_filter=True, do_update=False)
        if _stop_event.is_set(): status_lbl.value = "⏹ Stopped"
        elif isinstance(single_idx, int):
            s = shots_data[single_idx]
            status_lbl.value = (f"✅ Visual Direction shot {single_idx+1}: looks good"
                                if s.get("_dir_status") == "ok" else f"🎬 Visual Direction shot {single_idx+1}: revised")
        elif isinstance(single_idx, (list, set, tuple)):
            edited = sum(1 for i in single_idx if shots_data[i].get("_dir_status") == "edited")
            status_lbl.value = f"✅ Visual Direction batch done — revised {edited}/{len(single_idx)} shots"
        else:
            edited = sum(1 for s in shots_data if s.get("_dir_status") == "edited")
            status_lbl.value = f"✅ Visual Direction done — revised {edited}/{len(shots_data)} shots"
        page.update()

    _do_director_run_ref[0] = _do_director_run

    _director_pending_idx: list = [None]
    _director_desc_lbl = ft.Text("Visual Direction re-stages camera, composition, lighting, environment response\nand motion readability — story facts, character identity, clothing state,\nprops and the resolved action stay exactly as they are",
                                  size=12, color=C["text2"])

    director_confirm_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("🎬 Visual Direction", size=14, weight=ft.FontWeight.W_700, color=C["text1"]),
        content=ft.Column([
            ft.Container(
                content=_director_desc_lbl,
                padding=po(left=0, right=0, top=0, bottom=12),
                border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
            ),
            _dir_field("Preset", director_preset_dd),
            _dir_field("Shot Scale", director_scale_dd),
            ft.Row([
                _dir_field("Composition", director_beauty_dd, expand=True),
                _dir_field("Camera Energy", director_camera_dd, expand=True),
            ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.START),
            ft.Row([
                _dir_field("Lighting", director_light_dd, expand=True),
                _dir_field("Environment Response", director_env_dd, expand=True),
            ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.START),
            _dir_field("Motion", director_motion_dd),
            # _director_extra_req ไม่ได้แสดงแล้ว (ไม่ได้ใช้) แต่คง widget ไว้เพราะ _do_director_run
            # ยังอ่าน .value ของมันอยู่ — ซ่อนแทนการลบ ค่าที่ได้จะเป็น "" ตามที่ควรเป็น
            ft.Container(content=_director_extra_req, visible=False),
        ], spacing=14, tight=True, width=560,
           horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: (setattr(director_confirm_dialog, "open", False), page.update()),
                         style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("Run Visual Direction",
                on_click=lambda e: (setattr(director_confirm_dialog, "open", False), page.update(),
                                    asyncio.create_task(_do_director_run(_director_pending_idx[0]))),
                style=ft.ButtonStyle(color="#0F766E")),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(director_confirm_dialog)

    def _open_director_dialog(single_idx=None):
        if llm_busy[0]: return
        if not shots_data: status_lbl.value = "⚠ No shots yet"; page.update(); return
        llm_wf = llm_dd.value
        if not llm_wf:
            status_lbl.value = "⚠ Select LLM workflow"; page.update(); return
        _director_pending_idx[0] = single_idx
        if single_idx is not None:
            _director_desc_lbl.value = f"Reviewing Shot {single_idx+1} only"
        else:
            _director_desc_lbl.value = "Visual Direction reviews camera, lighting, and image polish\nwithout touching characters, clothing, or story facts"
        director_confirm_dialog.open = True; page.update()

    async def do_director(e): _open_director_dialog()
    director_prompts_btn.on_click = do_director

    # ── Vision Check ──────────────────────────────────────────────────
    vision_check_btn = ft.IconButton(
        icon=ft.Icons.MANAGE_SEARCH_ROUNDED, icon_color="white", icon_size=20,
        tooltip="Check All Images — review generated images", bgcolor="#B45309",
        visible=False,   # ซ่อนไว้ ยังต่อสายเดิมไว้ครบ
        width=40, height=38,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
    )
    vision_director_btn = ft.IconButton(
        icon=ft.Icons.MOVIE_FILTER_ROUNDED, icon_color="white", icon_size=20,
        tooltip="Direct All Images — camera review", bgcolor="#065F46",
        visible=False,   # ซ่อนไว้ ยังต่อสายเดิมไว้ครบ
        width=40, height=38,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
    )

    async def _run_vision(mode: str, single_idx=None):
        # MANGA-VNEXT-A1 §45/§46: Vision Check is not adapted for Manga vNext's multi-panel page
        # contract in this task — block it here (proxy/lightbox/toolbar all funnel through this
        # function) rather than only hiding the toolbar buttons.
        if _manga_vnext_mode[0]:
            await _llm_log("⏭ Vision Check skipped — unavailable in Manga vNext mode")
            return
        _sync_prompt_style()
        _sync_style()
        _sync_genre()
        llm_wf = llm_dd.value
        if not llm_wf:
            status_lbl.value = "⚠ Select LLM workflow first"; page.update(); return
        # เลือก joycaption workflow — prefer short version (JoyCaption รันโลคอลเสมอ ไม่เกี่ยวกับ llm_wf/Grok)
        if _caption_wf[0] and (CAPTION_DIR / _caption_wf[0]).is_file():             # the workflow chosen in Settings > LLM
            joycaption_wf = _caption_wf[0]
        else:                                                                         # Auto
            _jc_all = sorted(CAPTION_DIR.glob("*joycaption*.json"), key=lambda p: p.name)
            _jc_short = [p for p in _jc_all if "short" in p.name.lower()]
            joycaption_wf = (_jc_short[0] if _jc_short else (_jc_all[0] if _jc_all else None))
            joycaption_wf = joycaption_wf.name if joycaption_wf else None
        if not joycaption_wf:
            status_lbl.value = "⚠ JoyCaption workflow not found in comfy_apis/caption/"; page.update(); return

        _stop_event.clear(); llm_busy[0] = True; llm_prog.visible = True
        check_prompts_btn.disabled = True; director_prompts_btn.disabled = True
        vision_check_btn.disabled = True; vision_director_btn.disabled = True; _stop_btn_state(True)
        icon = "👁" if mode == "editor" else "🎬"
        targets = [single_idx] if single_idx is not None else range(len(shots_data))
        done = 0

        for i in targets:
            if _stop_event.is_set(): break
            shot = shots_data[i]
            imgs = gallery_images.get(i, [])
            if not imgs: await _llm_log(f"⚠ Shot {i+1}: no image — skipping"); continue
            latest = sorted(imgs, key=lambda x: x.get("ts", 0), reverse=True)
            img_path = latest[0]["path"]
            if not Path(img_path).exists(): await _llm_log(f"⚠ Shot {i+1}: file not found — skipping"); continue
            status_lbl.value = f"{icon} Shot {i+1}: JoyCaption analyzing..."; page.update()
            caption = await run_joycaption(img_path, joycaption_wf, stop_event=_stop_event)
            if not caption: await _llm_log(f"⚠ Shot {i+1}: JoyCaption failed"); continue
            await _llm_log(f"👁 Shot {i+1} caption:\n{caption[:400]}...")
            status_lbl.value = f"{icon} Shot {i+1}: LLM {mode} refining prompt..."; page.update()
            result = await run_vision_check(
                shot, caption, llm_wf, mode,
                char_field.value.strip(), scene_field.value.strip(), requirement_field.value.strip(),
                _outline_story[0] or story_field.value.strip(),
                image_style=STYLE_MAP.get(_sync_style() or "none", ("", ""))[1],
                image_size=sel_size[0], genre=_sel_genre[0],
                on_log=_llm_log, stop_event=_stop_event,
                grok_api_key=_grok_api_key[0], grok_model=_grok_model[0],
                prompt_style=_prompt_style[0],
                # MANGA-PAGE-ORPHAN-AUTHORITY-CLEANUP-A1: see run_check_prompts call above — same
                # orphan flag, same fix, applied here to run_vision_check.
                manga_page=False,
                fill_engine=_fill_engine[0],
            )
            if not result: await _llm_log(f"⚠ Shot {i+1}: vision {mode} failed"); continue
            keep = result.get("keep_original", True)
            new_prompt = result.get("prompt", "").strip()
            issues = [iss[:200] for iss in result.get("issues", []) if isinstance(iss, str)]
            reason = result.get("reason", "")[:200]
            if keep or not new_prompt or new_prompt.upper() == "KEEP":
                shot["_vision_status"] = "ok"; shot["_vision_issues"] = []; shot["_vision_reason"] = "Looks good"
            else:
                # OBJECT-LOCK-STATIC-AUDIT-A1: Vision Check was a 4th prompt-mutating path (besides
                # Fill/Checker/Director) writing shot["prompt"] with zero structural guard — found
                # while auditing every live prompt-mutating call site for the vNext OBJECT LOCK task.
                # Reuses the same shared structural guard Checker/Director use, so a lock-contract
                # engine's prompt can't silently lose/duplicate a lock or change format family here
                # either.
                _vc_old_prompt = shot.get("prompt", "")
                _vc_engine = normalize_fill_engine(_fill_engine[0] or shot.get("_fill_engine") or "")
                _vc_ok, _vc_reason = True, ""
                if _vc_engine in _LOCK_CONTRACT_ENGINES:
                    _vc_in_fmt = (shot.get("prompt_style") or "").strip() or _detect_prompt_format(_vc_old_prompt)
                    _vc_ok, _vc_reason = _director_format_structure_ok(
                        _vc_old_prompt, new_prompt, _vc_in_fmt, minimal_v3=True)
                if _vc_ok:
                    shot["prompt"] = new_prompt; shot["_vision_status"] = "edited"
                    shot["_vision_issues"] = issues; shot["_vision_reason"] = reason
                else:
                    await _llm_log(f"🚫 VISION CHECK FORMAT REJECT: shot={i+1} reason={_vc_reason}")
                    shot["_vision_status"] = "ok"; shot["_vision_issues"] = []
                    shot["_vision_reason"] = f"Vision check format reject — kept original ({_vc_reason})"
            if i in _shot_prompt_fields:
                pf = _shot_prompt_fields[i]; pf.on_change = None
                pf.value = shot.get("prompt", "")
                def _mk(_ii=i, _ff=pf):
                    def _fn(ev): shots_data[_ii]["prompt"] = _ff.value; _save_state()
                    return _fn
                pf.on_change = _mk()
                if pf.page: pf.update()
            if i < len(shots_col.controls):
                _shot_rows.pop(i, None); _shot_prompt_fields.pop(i, None)
                _build_shot_row(i, shot)
                shots_col.controls[i] = shots_col.controls.pop()
            _save_state(); done += 1; status_lbl.value = f"{icon} Done {done} shot..."; page.update()

        llm_busy[0] = False; llm_prog.visible = False
        check_prompts_btn.disabled = False; director_prompts_btn.disabled = False
        vision_check_btn.disabled = False; vision_director_btn.disabled = False; _stop_btn_state(False)
        status_lbl.value = ("⏹ Stopped" if _stop_event.is_set()
                            else f"✅ Vision {mode.title()} done — {done} shots")
        page.update()

    _run_vision_ref[0] = _run_vision
    vision_check_btn.on_click    = lambda e: asyncio.create_task(_run_vision("editor"))   if not llm_busy[0] else None
    vision_director_btn.on_click = lambda e: asyncio.create_task(_run_vision("director")) if not llm_busy[0] else None

    _MANGA_VNEXT_CHECKER_TIP = ("Unavailable in Manga vNext mode — Manga vNext uses a separate "
                                "page/panel contract. A dedicated Manga Consistency Check will be added later.")
    _MANGA_VNEXT_DIRECTOR_TIP = ("Hidden in Manga vNext mode — the Comic Storyboard Artist (B "
                                 "Manga) already owns page/panel camera, hierarchy and layout.")
    _MANGA_VNEXT_VISION_TIP = "Unavailable in Manga vNext mode."
    _check_prompts_btn_tooltip_normal = [check_prompts_btn.tooltip]
    _director_prompts_btn_tooltip_normal = [director_prompts_btn.tooltip]

    def _sync_manga_agent_availability():
        """MANGA-VNEXT-A1 §47: single central function deriving Checker/Director/Vision UI state
        from Manga vNext mode alone — never touched from any other call site, so Normal behavior is
        restored exactly by simply turning Manga vNext off again."""
        _mv = _manga_vnext_mode[0]
        # Checker: visible but disabled (§42) — never hidden, so its presence/unavailability is
        # obvious rather than looking like the feature vanished.
        check_prompts_btn.visible = True
        check_prompts_btn.disabled = _mv or llm_busy[0]
        check_prompts_btn.tooltip = _MANGA_VNEXT_CHECKER_TIP if _mv else _check_prompts_btn_tooltip_normal[0]
        _auto_check_switch.disabled = _mv
        # Director: hidden entirely (§44).
        director_prompts_btn.visible = not _mv
        director_prompts_btn.tooltip = _director_prompts_btn_tooltip_normal[0] if not _mv else _MANGA_VNEXT_DIRECTOR_TIP
        _auto_director_switch.disabled = _mv
        # Vision: unavailable (§45) — keep hidden the same way it is already hidden in Normal mode
        # elsewhere in the app; only actively hide here when Manga vNext turns it off, never force
        # it visible when Manga vNext turns off (Normal's own existing visibility rule wins).
        if _mv:
            vision_check_btn.visible = False
            vision_director_btn.visible = False
            vision_check_btn.tooltip = _MANGA_VNEXT_VISION_TIP
            vision_director_btn.tooltip = _MANGA_VNEXT_VISION_TIP
        # §48: Fill Manga has its own dedicated renderer form (prompt_style="manga_page", stamped by
        # run_fill_manga_vnext itself) — the normal Prompt Format dropdown is irrelevant to it, so it
        # is fine to disable it while Manga vNext is on, but its own value is never overwritten (it
        # is simply not read by the Manga vNext Fill path), so the user's normal format is exactly
        # what they left it at when Manga vNext turns back off.
        fill_prompt_style_dd.disabled = _mv
        outline_prompt_style_dd.disabled = _mv
        # BUGFIX-A1: check_prompts_btn/director_prompts_btn/vision_*_btn live on the main page (added
        # via page.add() at startup), but fill_prompt_style_dd/outline_prompt_style_dd live inside the
        # Fill/Outline-generation dialogs, which Flet only attaches once the dialog is actually
        # opened. In this installed Flet version, merely READING `.page` on a control not yet added
        # to the page tree raises RuntimeError itself (base_control.py's `page` property) — it is not
        # just None — so an `if control.page:` guard still crashes. try/except is the only reliable
        # way to make a .update() call safe on a control that may or may not be on the page yet.
        def _safe_update(*_controls):
            for _c in _controls:
                try:
                    if _c.page:
                        _c.update()
                except RuntimeError:
                    pass
        _safe_update(check_prompts_btn, director_prompts_btn, vision_check_btn, vision_director_btn,
                     fill_prompt_style_dd, outline_prompt_style_dd)

    # ── Generate Outline ──────────────────────────────────────────────
    gen_shots_btn = ft.FilledButton(
        content=ft.Row([ft.Icon(ft.Icons.PERSON_OUTLINE, size=16, color="white"),
                         ft.Text("Generate Outline", size=13, color="white", weight=ft.FontWeight.W_600)],
                        tight=True, spacing=6),
        bgcolor=C["purple"], height=42,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)), expand=True,
    )

    genre_dd = ft.Dropdown(
        value=_sel_genre[0],
        options=[ft.dropdown.Option(key=k, text=label) for k, (label, _, _) in GENRE_MAP.items()],
        text_size=12, height=40, expand=True, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    _genre2_options = [ft.dropdown.Option(key="none", text="🚫 None (single genre)")] + \
                      [ft.dropdown.Option(key=k, text=label) for k, (label, _, _) in GENRE_MAP.items() if k != "none"]
    genre2_dd = ft.Dropdown(
        value=_sel_genre2[0],
        options=_genre2_options,
        text_size=12, height=40, expand=True, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def _genre_key_from_pick(v: str) -> str:
        v = (v or "").strip()
        if v in GENRE_MAP:
            return v
        for k, (label, *_rest) in GENRE_MAP.items():
            if v == label:
                return k
        return ""

    def _genre_event_value(e, fallback: str) -> str:
        v = None
        if e is not None:
            ctrl = getattr(e, "control", None)
            v = getattr(ctrl, "value", None) if ctrl is not None else None
            if not v:
                v = getattr(e, "data", None)
        return _genre_key_from_pick(v or fallback)

    def _sync_genre() -> tuple[str, str]:
        """Keep _sel_genre in sync with Outline Settings dropdowns.
        Flet 0.85.3 often paints the new option (Erotic) while .value is still
        the previous key (none) if the user clicks Groom / Create Outline right
        away — same class of bug as Scene Detail / LLM. on_select writes the
        pick into _sel_genre; this only fills from .value when state is still
        empty/none. It never copies a leftover 'none' over a real genre."""
        g = (genre_dd.value or "").strip()
        if _sel_genre[0] in ("", "none") and g in GENRE_MAP:
            _sel_genre[0] = g
        g2 = (genre2_dd.value or "").strip()
        if _sel_genre2[0] in ("", "none") and g2 in GENRE_MAP:
            _sel_genre2[0] = g2
        if not _sel_genre[0]:
            _sel_genre[0] = "none"
        if not _sel_genre2[0]:
            _sel_genre2[0] = "none"
        return _sel_genre[0], _sel_genre2[0]

    def _genre_changed(e=None):
        v = _genre_event_value(e, genre_dd.value)
        if v:
            _sel_genre[0] = v
            groom_genre_dd.value = v  # groom_genre_dd ถูกสร้างทีหลังในฟังก์ชันเดียวกัน — closure อ่านค่าตอนถูกเรียก ไม่ใช่ตอนนิยาม
        _save_state()

    def _genre2_changed(e=None):
        v = _genre_event_value(e, genre2_dd.value) or "none"
        if v:
            _sel_genre2[0] = v
            groom_genre2_dd.value = v
        _save_state()

    # on_change อย่างเดียวไม่ยิงรอบแรก — ใช้ on_select เป็นตัวเขียนจริง (แบบ llm_dd / scene_detail)
    genre_dd.on_select = _genre_changed
    genre_dd.on_change = _genre_changed
    genre2_dd.on_select = _genre2_changed
    genre2_dd.on_change = _genre2_changed
    dialogue_style_dd = ft.Dropdown(
        value=_dialogue_style[0],
        options=[ft.dropdown.Option(key=k, text=label) for k, label in DIALOGUE_STYLE_MAP.items()],
        text_size=12, height=40, expand=True, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    _dialogue_dd_ref[0] = dialogue_style_dd

    def _cur_dlg_style() -> str:
        """The Dialogue Style dropdown as it is RIGHT NOW. Every Render / Render Missing / Preview / Fix
        reads this (not the value B was authored under) together with the shot's CURRENT narration /
        speech_char / subtitle."""
        if (dialogue_style_dd.value or "") in DIALOGUE_STYLE_MAP:
            _dialogue_style[0] = dialogue_style_dd.value
        return _dialogue_style[0]

    def _dialogue_style_changed(e):
        _cur_dlg_style()
        try: _save_state()
        except Exception: pass
    dialogue_style_dd.on_change = _dialogue_style_changed
    dialogue_style_dd.on_select = _dialogue_style_changed

    _yolo_dlg_open: list[bool] = [False]

    async def _show_yolo_dialog(auto: bool) -> str:
        """Person & Face Detection dialog (model status + Install Models). auto=True is the offer made before a PIL
        narration render (adds "Don't ask again"); auto=False is opened from Settings. Returns "never" | "later" |
        "installed" | "closed". Nothing is downloaded unless the user presses Install Models."""
        if _yolo_dlg_open[0]:
            return "closed"
        _yolo_dlg_open[0] = True
        from .comfy_api import yolo_runtime_status, install_yolo_model, YOLO_WEIGHTS_NAME, YOLO_POSE_WEIGHTS_NAME
        loop = asyncio.get_event_loop()
        done = asyncio.Event()
        answer = ["closed"]
        busy = [False]
        failed: dict = {}                       # kind -> last error message (cleared when that model becomes ready)
        progress: dict = {}                     # live download progress written by the worker thread
        names = {"person": YOLO_WEIGHTS_NAME, "face": YOLO_POSE_WEIGHTS_NAME}

        person_lbl = ft.Text("…", size=12, color=C["text2"], expand=True)
        face_lbl = ft.Text("…", size=12, color=C["text2"], expand=True)
        prog_bar = ft.ProgressBar(value=0, visible=False, color=C["accent"])
        prog_lbl = ft.Text("", size=11, color=C["text3"], visible=False)
        lib_note = ft.Text("", size=11, color=C["amber"], visible=False)
        install_txt = ft.Text("Install Models", size=13, color="white", weight=ft.FontWeight.W_600)
        install_btn = ft.FilledButton(content=install_txt, bgcolor=C["accent"], disabled=True,
                                      style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)))
        not_now_btn = ft.TextButton("Not now" if auto else "Close", style=ft.ButtonStyle(color=C["text2"]))
        never_btn = ft.TextButton("Don't ask again", style=ft.ButtonStyle(color=C["text3"]), visible=auto)
        state: dict = {}

        def _label(kind, st):
            s = st.get(kind)
            if kind in progress and busy[0]:
                p = progress[kind]
                return ("Validating…" if p["total"] and p["done"] >= p["total"]
                        else f"Downloading {names[kind]}… {p['done'] / 1048576:.1f}"
                             + (f" / {p['total'] / 1048576:.1f} MB" if p["total"] else " MB")), C["accent"]
            if failed.get(kind) and s != "ready":
                return f"Failed — {failed[kind]}", C["red"]
            return {"ready": ("Ready", C["green"]), "missing": ("Not installed", C["text2"]),
                    "invalid": ("Invalid file — install again", C["amber"]),
                    "unavailable": ("Unavailable", C["text3"])}.get(s, ("…", C["text3"]))

        def _paint():
            st = state
            for kind, lbl in (("person", person_lbl), ("face", face_lbl)):
                lbl.value, lbl.color = _label(kind, st)
            lib_ok = st.get("library") == "ready"
            lib_note.visible = bool(st) and not lib_ok
            lib_note.value = "The detection library is not available in this installation — narration uses image analysis only."
            todo = [k for k in ("person", "face") if st.get(k) in ("missing", "invalid")]
            all_ready = bool(st) and st.get("person") == "ready" and st.get("face") == "ready"
            install_btn.disabled = busy[0] or not lib_ok or not todo
            install_btn.visible = lib_ok and not all_ready
            install_txt.value = "Retry" if any(failed.get(k) for k in todo) else "Install Models"
            not_now_btn.disabled = busy[0]; never_btn.disabled = busy[0]
            prog_bar.visible = prog_lbl.visible = busy[0]
            page.update()

        async def _refresh():
            st = await loop.run_in_executor(None, yolo_runtime_status)
            state.clear(); state.update(st)
            for k in ("person", "face"):
                if st.get(k) == "ready":
                    failed.pop(k, None)
            _paint()

        def _close(v):
            def _h(e):
                answer[0] = v
                page.pop_dialog(); page.update()
                done.set()
            return _h

        async def _install(e=None):
            if busy[0]:
                return
            todo = [k for k in ("person", "face") if state.get(k) in ("missing", "invalid")]
            if not todo:
                return
            busy[0] = True
            _paint()
            for kind in todo:                  # a failure never stops the other model; each is retried on its own
                progress[kind] = {"done": 0, "total": 0}
                failed.pop(kind, None)

                def _cb(d, t, _k=kind):
                    progress[_k] = {"done": d, "total": t}
                fut = loop.run_in_executor(None, install_yolo_model, kind, _cb)
                while not fut.done():
                    p = progress[kind]
                    prog_bar.value = (p["done"] / p["total"]) if p["total"] else None
                    prog_lbl.value = names[kind]
                    _paint()
                    await asyncio.sleep(0.25)
                ok, msg = await fut
                progress.pop(kind, None)
                if not ok:
                    failed[kind] = msg
            busy[0] = False
            await _refresh()
            if state.get("person") == "ready" and state.get("face") == "ready":
                status_lbl.value = "✅ Person & Face detection ready"
            elif failed:
                status_lbl.value = "⚠ Some detection models could not be installed — narration still works without them"
            page.update()

        install_btn.on_click = _install
        not_now_btn.on_click = _close("later" if auto else "closed")
        never_btn.on_click = _close("never")

        def _row(title, lbl):
            return ft.Row([ft.Text(title, size=12, weight=ft.FontWeight.W_600, color=C["text1"], width=130), lbl],
                          vertical_alignment=ft.CrossAxisAlignment.CENTER)

        dlg = ft.AlertDialog(
            modal=True, bgcolor=C["surface"],
            title=ft.Text("Person & Face Detection", size=15, weight=ft.FontWeight.W_700, color=C["text1"]),
            content=ft.Container(width=460, content=ft.Column([
                ft.Text("Optional AI detection for safer narration box placement.", size=13, color=C["text2"]),
                ft.Container(height=4),
                _row("Person Detection", person_lbl),
                _row("Face Detection", face_lbl),
                prog_bar, prog_lbl, lib_note,
                ft.Container(height=2),
                ft.Text("Narration placement still works without YOLO. Models (about 13 MB) are downloaded from GitHub "
                        "(ultralytics/assets) into your user folder only when you press Install Models.",
                        size=11, color=C["text3"]),
            ], spacing=8, tight=True)),
            actions=[never_btn, not_now_btn, install_btn],
            actions_alignment=ft.MainAxisAlignment.END,
            actions_padding=po(left=20, right=20, top=4, bottom=16),
        )
        page.show_dialog(dlg)
        page.update()
        try:
            await _refresh()
            await done.wait()
        finally:
            _yolo_dlg_open[0] = False
        if answer[0] == "closed" and state.get("person") == "ready" and state.get("face") == "ready":
            return "installed"
        return answer[0]

    async def _maybe_offer_yolo():
        """Before a PIL narration render: if the optional detection models are not both ready, offer the install
        dialog — unless the user chose "Don't ask again" or "Not now" for this session. Never downloads by itself."""
        if not any(pil_overlay_applies(_s) and (str(_s.get("narration") or "").strip() or str(_s.get("subtitle") or _s.get("speech_text") or "").strip())
                   for _s in shots_data if isinstance(_s, dict)):
            return   # nothing will be drawn with PIL
        if _yolo_choice[0] == "no" or _yolo_session_skip[0]:
            return
        from .comfy_api import yolo_runtime_status
        st = await asyncio.get_event_loop().run_in_executor(None, yolo_runtime_status)
        if st["library"] != "ready":
            if not _yolo_notice_shown[0]:
                _yolo_notice_shown[0] = True
                status_lbl.value = "ℹ Narration box: person/face avoidance is off — the detection library is unavailable"
                page.update()
            return
        if st["person"] == "ready" and st["face"] == "ready":
            return
        res = await _show_yolo_dialog(auto=True)
        if res == "never":
            _yolo_choice[0] = "no"; _save_state()
        elif res == "later":
            _yolo_session_skip[0] = True

    def _open_detection_settings(e=None):
        """Settings → Detection: reopen the install dialog at any time, even after "Don't ask again"."""
        sys_dialog.open = False
        page.update()
        asyncio.create_task(_show_yolo_dialog(auto=False))


    _lang_opts = [ft.dropdown.Option(key=k, text=lab) for k, (lab, _) in LANG_MAP.items()]
    def _mk_lang_dd(value: str) -> ft.Dropdown:
        v = value if value in LANG_MAP else "en"
        return ft.Dropdown(
            value=v, options=_lang_opts,
            text_size=12, height=40, expand=True, border_color=C["border"], focused_border_color=C["accent"],
            bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
        )
    source_lang_dd = _mk_lang_dd(_source_lang[0])
    desc_lang_dd = _mk_lang_dd(_desc_lang[0])
    dialogue_lang_dd = _mk_lang_dd(_dialogue_lang[0])
    narration_type_dd = ft.Dropdown(
        value=_narration_type[0],
        options=[
            ft.dropdown.Option(key="caption", text="📷 Visual Caption — who/what/where"),
            ft.dropdown.Option(key="bridge",  text="✍️ Literary Bridge — feeling/stakes/cost"),
        ],
        text_size=12, height=40, expand=True, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    # สไตล์ภาพในหน้าตั้งค่า Outline — dropdown แยก แต่ sync กับ style_dd ของ panel ด้านนอกเสมอ
    outline_style_dd = ft.Dropdown(
        value=style_dd.value,
        options=[ft.dropdown.Option(key=k, text=label) for k, (label, _) in STYLE_MAP.items()],
        text_size=12, height=36, expand=True, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def _outline_style_changed(e):
        style_dd.value = outline_style_dd.value
        style_dd.update()
        _update_style_desc()
        _sel_style[0] = outline_style_dd.value or "none"
        _save_state()
        save_credentials({"render_style": _sel_style[0]})
    outline_style_dd.on_change = _outline_style_changed

    # UI label only — the internal mode value stays "cinematic" (routing, saved projects, A2 prompt
    # building, comparisons like `_outline_mode[0] == "sequential"` are all untouched by this rename).
    _OUTLINE_MODE_OPTS = [
        ("cinematic",  "📖 Visual Novel — broader dramatic / story moments"),
        ("sequential", "📋 Sequential — chronological order"),
        ("manga",      "📦 Manga — page / panel beats"),
        ("static_pose", "🎭 Static Pose Fill (If possible)"),
    ]
    # VN ถูกตัดออก — ต่างจาก cinematic แค่มีช่อง narration ซึ่ง Narration Type คุมอยู่แล้ว
    # เรื่องเก่าที่เซฟเป็น "vn" ถูก map เป็น cinematic ตอนโหลด ไม่งั้นจะค้างในโหมดที่ไม่มีใน dropdown
    if _outline_mode[0] == "vn":
        _outline_mode[0] = "cinematic"
    _OUTLINE_MODE_KEYS = {k for k, _ in _OUTLINE_MODE_OPTS}

    def _outline_mode_for_engines() -> str:
        """The Story Bible / Beat Sheet / Shot List engines know three modes. Static Pose Fill has its own path for a still-picture prompt; when the
        input turns out to be a story, the normal pipeline runs it exactly as Visual Novel (cinematic)."""
        return "cinematic" if _outline_mode[0] == "static_pose" else _outline_mode[0]
    outline_mode_dd = ft.Dropdown(
        value=_outline_mode[0] if _outline_mode[0] in _OUTLINE_MODE_KEYS else "cinematic",
        options=[ft.dropdown.Option(key=k, text=label) for k, label in _OUTLINE_MODE_OPTS],
        tooltip="Visual Novel: broader dramatic/story moments — a continuous interaction may stay one beat. "
                "Sequential: finer chronological moments — preserves more meaningful intermediate moments. "
                "Manga: each shot is a multi-panel manga page. "
                "Static Pose Fill: when the input is ONE still-picture prompt (the check decides), make the number of shots you set from it, each with a new pose, "
                "expression, emotion and camera angle; the style words are removed. A story is planned as Visual Novel.",
        text_size=12, height=40, expand=True, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def _outline_mode_key_from_pick(v: str) -> str:
        v = (v or "").strip()
        if v in _OUTLINE_MODE_KEYS:
            return v
        for k, label in _OUTLINE_MODE_OPTS:
            if v == label:
                return k
        return ""

    def _outline_mode_event_value(e, fallback: str) -> str:
        v = None
        if e is not None:
            ctrl = getattr(e, "control", None)
            v = getattr(ctrl, "value", None) if ctrl is not None else None
            if not v:
                v = getattr(e, "data", None)
        return _outline_mode_key_from_pick(v or fallback)

    def _sync_outline_mode() -> str:
        """Apply manga/VN side-effects from _outline_mode[0].

        Source of truth is _outline_mode, written by on_select. Flet 0.85.3
        often paints a new option (Cinematic) while .value is still the old
        key (manga). Copying .value back here then page.update() snapped the
        dropdown back to Manga. Same class of bug as Genre / Scene Detail."""
        mode = _outline_mode[0] if _outline_mode[0] in _OUTLINE_MODE_KEYS else "cinematic"
        _outline_mode[0] = mode
        if outline_mode_dd.value != mode:
            outline_mode_dd.value = mode
        # MANGA-VNEXT-A1 follow-up: "Outline Style: Manga" is now the single control that turns Manga
        # vNext on/off — the old separate switch was removed (see its definition site for why). The
        # old per-panel _manga_page_mode legacy toggle is left alone here (never forced by this
        # dropdown any more): its underlying Fill engine no longer exists, so forcing it on would only
        # mislead, not do anything useful.
        _manga_vnext_mode[0] = (mode == "manga")
        _sync_manga_agent_availability()
        if mode == "vn":
            _manga_page_mode[0] = False
            _manga_page_forced[0] = False
            if _narration_type[0] != "bridge":
                _narration_type[0] = "bridge"
                narration_type_dd.value = "bridge"
        else:
            # cinematic/sequential: Manga Page เป็น toggle อิสระ user เปิดเองได้ แต่ถ้า True ตัวนี้มาจากการ
            # บังคับของโหมด manga ต้องปลดคืน ไม่งั้นเลือก manga แล้วสลับมาโหมดนี้ ค่าจะค้างเปิดข้ามโหมด
            # แล้วลาก Prompt Format ให้ค้าง disable ตามไปด้วยทั้งที่ user ไม่ได้เลือกเอง
            if _manga_page_forced[0]:
                _manga_page_mode[0] = False
                _manga_page_forced[0] = False
        _manga_switch_outline.value = _manga_page_mode[0]
        _manga_switch_fill.value = _manga_page_mode[0]
        _manga_switch_outline.disabled = mode in ("manga", "vn")
        _manga_switch_fill.disabled = _manga_switch_outline.disabled
        _apply_manga_prompt_lock()
        return mode

    def _outline_mode_changed(e=None):
        v = _outline_mode_event_value(e, outline_mode_dd.value)
        if v:
            _outline_mode[0] = v
            outline_mode_dd.value = v
        _sync_outline_mode()
        _save_state()
        page.update()
    outline_mode_dd.on_select = _outline_mode_changed
    outline_mode_dd.on_change = _outline_mode_changed

    scene_detail_dd = ft.Dropdown(
        value=_scene_detail[0],
        options=[
            ft.dropdown.Option(key="concise",  text="✂ Concise — 2-3 sentences"),
            ft.dropdown.Option(key="standard", text="📝 Standard — 4-6 sentences"),
            ft.dropdown.Option(key="rich",     text="📖 Rich — 7-10 sentences"),
        ],
        text_size=12, height=36, expand=True, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def _scene_detail_changed(e):
        _scene_detail[0] = scene_detail_dd.value or "standard"
        _save_state()
    # Dropdown.on_change ไม่ fire บางครั้งใน Flet เวอร์ชันนี้ (บั๊กเดียวกับ llm_dd ที่เจอมาก่อน) —
    # ทำให้เลือก Scene Detail ใน UI แล้ว _scene_detail[0] ไม่อัปเดตจริง ค่าที่ส่งเข้า outline
    # เลยยังเป็นค่าเก่า/default ทั้งที่หน้าจอโชว์ค่าใหม่ — สลับไปใช้ on_select เหมือนตัวอื่นที่แก้ไปแล้ว
    scene_detail_dd.on_select = _scene_detail_changed

    # รูปแบบ prompt ในหน้าตั้งค่า Outline — dropdown แยก แต่ sync กับ fill_prompt_style_dd เสมอ (state เดียวกัน _prompt_style)
    outline_prompt_style_dd = ft.Dropdown(
        value=_prompt_style[0], options=_prompt_style_options(),
        text_size=12, height=40, expand=True, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def _outline_prompt_style_changed(e):
        _prompt_style[0] = outline_prompt_style_dd.value or "narrative"
        fill_prompt_style_dd.value = _prompt_style[0]
        _save_state()
    outline_prompt_style_dd.on_change = _outline_prompt_style_changed

    outline_preset_dd = _preset_dd(height=40, expand=True, focused_border_color=C["accent"])

    def _outline_preset_changed(e):
        _sync_visual_preset()
        _save_state()
    outline_preset_dd.on_select = _outline_preset_changed

    # ── Groom Dialog ──────────────────────────────────────────────────
    # (_groom_text — the working draft — is defined with the other Groom state near _groom_creativity)
    _groom_card: list[dict] = [{}]     # การ์ดจาก groom ขั้น 1 — ส่ง events/ตัวละครให้ outline A
    # SOURCE-STYLE-A1: the source material's own visual register (scalar, may be "") — extracted once
    # by Groom (if used) or Outline A as fallback, never both. Validity is structural: it stays valid
    # only as long as the story text Outline A is about to run on equals the text it was captured
    # against (see _use_card's own exact-snapshot check below, reused for this same purpose).
    _outline_story: list[str] = [""]   # story ที่จะใช้สร้าง outline (อาจเป็นผล Story Editor)
    groom_text_field = ft.TextField(
        multiline=True, expand=True,
        value="", text_size=13, bgcolor=C["surface"],
        border=ft.InputBorder.NONE, focused_border_color=C["accent"],
        border_radius=br(8), color=C["text1"],
        content_padding=po(left=10, right=10, top=10, bottom=10),
    )
    groom_count_lbl = ft.Text("", size=11, color=C["text3"])

    def _groom_update_count():
        _txt = (groom_text_field.value or "").strip()
        if not _txt:
            groom_count_lbl.value = ""
            return
        _paras = len([p for p in _txt.replace("\r\n", "\n").split("\n\n") if p.strip()])
        _words = len(_txt.split())
        groom_count_lbl.value = f"{_paras} paragraph{'s' if _paras != 1 else ''}  •  {_words:,} words"

    def _groom_text_edited(e):
        """แก้เนื้อเรื่องเองแล้ว ผลตรวจเดิมไม่ได้อธิบายเรื่องก้อนนี้อีกต่อไป — ทำให้เห็นว่า "หมดอายุ"
        แทนที่จะโชว์ ✓ ต่อไปเหมือนไม่มีอะไรเกิดขึ้น"""
        _groom_text[0] = groom_text_field.value or ""
        _groom_update_count()
        _groom_update_action_state()
        if _groom_card[0] and not _groom_check_stale[0]:
            _groom_check_stale[0] = True
            _groom_show_check(_groom_card[0], stale=True)
        page.update()
    groom_text_field.on_change = _groom_text_edited

    groom_req_field = ft.TextField(
        value=saved.get("groom_req", ""),
        multiline=True, min_lines=2, max_lines=3,
        hint_text="e.g. more dialogue, remove scene n...",
        text_size=12, bgcolor=C["surface"],
        border=ft.InputBorder.NONE, focused_border_color=C["accent"],
        border_radius=br(8), color=C["text1"],
        content_padding=po(left=10, right=10, top=8, bottom=8),
    )
    groom_req_field.on_change = lambda e: _save_state()
    groom_status_lbl = ft.Text("", size=11, color=C["text2"])
    groom_loading = ft.ProgressRing(width=14, height=14, stroke_width=2, visible=False)
    # ป้ายผลรวม Final Check ค้างไว้ที่หัว dialog เสมอ (มุมขวาบน) แยกจาก groom_status_lbl ที่ใช้บอก
    # สถานะชั่วคราวระหว่างกำลัง groom — badge นี้คงค่าไว้จนกว่าจะ groom รอบใหม่/แก้เรื่องเอง
    groom_header_badge = ft.Text("", size=12, weight=ft.FontWeight.W_600)
    # visual detailที่ Groom เก็บไว้ได้ — ห้อง แสง บรรยากาศ ของ การรีทัช ถ้าอันไหนว่าง
    # แปลว่าต้นฉบับไม่ได้บอก หรือ Groom ทำหาย ซึ่งเป็นสิ่งเดียวที่มองไม่เห็นจากกล่อง "Groomed story"
    # เปลี่ยนจาก TextField ก้อนเดียวเป็น Column ของ Row ที่มีสี/ไอคอนต่อบรรทัด อ่านง่ายกว่าข้อความยาว
    groom_check_col = ft.Column(spacing=6, tight=True)
    groom_facts_card = ft.Container(visible=False, content=groom_check_col)

    # หมวดของ Final Check — คีย์เป็นตัวระบุคงที่ฝั่งเอนจิน ป้ายเป็นภาษาอังกฤษตามสัญญา UI
    _GROOM_CHECK_LABELS = {
        "characters": "Characters & permanent traits",
        "opening_outfits": "Opening outfits",
        "props": "Props",
        "environment": "Environment",
        "physical_lighting": "Physical lighting",
        "events": "Events",
        "continuity": "Continuity",
        "genre_payoff": "Genre Payoff",
    }
    # ผลตรวจล่าสุดเป็นของ "เนื้อเรื่องก้อนที่ groom เพิ่งเขียน" ถ้าผู้ใช้แก้เรื่องเอง ผลเดิมใช้ไม่ได้แล้ว
    _groom_check_stale = [False]

    def _groom_issue_row(key: str, st: str, msg: str) -> ft.Column:
        _color = C["red"] if st == "FAIL" else C["amber"]
        _icon = "✕" if st == "FAIL" else "⚠"
        return ft.Column([
            ft.Text(_GROOM_CHECK_LABELS.get(key, key), size=12, weight=ft.FontWeight.W_600, color=C["text1"]),
            ft.Row([
                ft.Text(_icon, size=11, color=_color),
                ft.Text(msg or "No detail given.", size=11, color=C["text2"], expand=True),
            ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
        ], spacing=2)

    def _groom_show_check(card: dict, stale: bool = False):
        """Legacy Final Check display. Clean Groom intentionally returns story only."""
        card = card if isinstance(card, dict) else {}
        if card.get("clean_groom"):
            groom_header_badge.value = ""
            groom_check_col.controls = []
            groom_facts_card.visible = False
            return
        check = card.get("final_check")
        note = str(card.get("final_check_note") or "").strip()
        rows: list = []
        badge_text, badge_color = "", C["text3"]
        if stale and check:
            badge_text, badge_color = "Final Check: Outdated", C["text3"]
            rows.append(ft.Text("○ Check outdated — the story has been edited since the last Story Editor check.",
                                size=11, color=C["text3"]))
        elif not check:
            badge_text, badge_color = "Final Check: Unavailable", C["text3"]
            rows.append(ft.Text(
                "⚠ Final Check unavailable — the story was edited successfully, but the "
                f"verification result could not be read ({note or 'not returned'}).",
                size=11, color=C["text2"]))
        else:
            issues = groom_final_check_issues(check)
            status = str(check.get("status") or "").upper()
            if status == "FAIL":
                badge_text, badge_color = "Final Check: Failed", C["red"]
                rows.append(ft.Row([ft.Text("✕", size=13, color=C["red"]),
                                     ft.Text("Final Check Failed", size=13, weight=ft.FontWeight.W_700,
                                            color=C["red"])], spacing=6))
                rows.append(ft.Text("Important story information may have been lost during Story Editor.",
                                    size=11, color=C["text2"]))
            elif issues or status == "WARNING":
                badge_text = f"Final Check: {len(issues)} issue" + ("s" if len(issues) != 1 else "")
                badge_color = C["amber"]
                rows.append(ft.Row([ft.Text("⚠", size=13, color=C["amber"]),
                                     ft.Text(badge_text, size=13, weight=ft.FontWeight.W_700,
                                            color=C["amber"])], spacing=6))
            else:
                badge_text, badge_color = "Final Check: Passed", C["green"]
                rows.append(ft.Row([ft.Text("✓", size=13, color=C["green"]),
                                     ft.Text("Final Check Passed", size=13, weight=ft.FontWeight.W_700,
                                            color=C["green"])], spacing=6))
                rows.append(ft.Text("All important story facts are present in the edited story.",
                                    size=11, color=C["text2"]))
            if issues:
                for key, st, msg in issues:
                    rows.append(ft.Container(height=2))
                    rows.append(_groom_issue_row(key, st, msg))
            else:
                for key, label in _GROOM_CHECK_LABELS.items():
                    st = str((((check.get("checks") or {}).get(key)) or {}).get("status") or "NA")
                    rows.append(ft.Row([
                        ft.Text("✓" if st == "PASS" else "○", size=11,
                               color=C["green"] if st == "PASS" else C["text3"]),
                        ft.Text(label + ("  (not defined in the source)" if st == "NA" else ""),
                               size=11, color=C["text2"]),
                    ], spacing=6))
        groom_check_col.controls = rows
        groom_facts_card.visible = bool(rows)
        groom_header_badge.value = badge_text
        groom_header_badge.color = badge_color

    _GROOM_LENGTH_OPTS = [
        ("✨ Auto", "auto"),
        ("📄 Short  (3-5 paragraphs)", "short"),
        ("📖 Medium  (6-10 paragraphs)", "medium"),
        ("📚 Long  (12-20 paragraphs)", "long"),
    ]
    groom_length_dd = ft.Dropdown(
        value="auto",
        options=[ft.dropdown.Option(key=k, text=t) for t, k in _GROOM_LENGTH_OPTS],
        text_size=12, height=42, dense=True, expand=True,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], color=C["text1"],
        hint_style=ft.TextStyle(color=C["text3"]),
    )
    _GROOM_CREATIVITY_OPTS = [
        ("faithful", "📌 Faithful — keep the source close"),
        ("balanced", "⚖️ Balanced — enrich, no new plot"),
        ("creative", "🎨 Creative — freer voice, small new beats"),
        ("genre_max", "🔥 Write to the genre — same cast, places, outfits"),
    ]
    _cre_init = _groom_creativity[0] if _groom_creativity[0] in ("faithful", "balanced", "creative", "genre_max") else "balanced"
    _groom_creativity[0] = _cre_init
    groom_creativity_dd = ft.Dropdown(
        value=_cre_init,
        options=[ft.dropdown.Option(key=k, text=t) for k, t in _GROOM_CREATIVITY_OPTS],
        text_size=12, height=42, dense=True, expand=True,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], color=C["text1"],
        hint_style=ft.TextStyle(color=C["text3"]),
    )
    # GROOM-PATCH-B1: Local (Comfy) reliably weakens at the two highest creativity tiers — see
    # groom_backend_hint_lbl below. Guidance only: never auto-switches, blocks, or disables Local.
    _GROOM_LOCAL_HINT_CREATIVITIES = {"creative", "genre_max"}
    groom_backend_hint_lbl = ft.Text("", size=11, color=C["amber"], visible=False)

    def _update_groom_backend_hint():
        _backend = llm_dd.value or selected_llm[0] or ""
        _cre = groom_creativity_dd.value or _groom_creativity[0] or "balanced"
        if _backend and _backend != _GROK_OPTION and _cre in _GROOM_LOCAL_HINT_CREATIVITIES:
            groom_backend_hint_lbl.value = (
                "💡 Grok tends to deliver stronger story progression and genre payoff at this "
                "creativity level — Local still works, just weaker here."
            )
            groom_backend_hint_lbl.visible = True
        else:
            groom_backend_hint_lbl.value = ""
            groom_backend_hint_lbl.visible = False
        # merely READING .page on a control not yet attached to the page tree raises RuntimeError
        # in this Flet version (see BUGFIX-A1 near _safe_update above) — llm_dd's on_select can
        # fire before the Groom dialog has ever been opened once, so this must not assume mounted.
        try:
            if groom_backend_hint_lbl.page:
                groom_backend_hint_lbl.update()
        except RuntimeError:
            pass

    def _groom_creativity_changed(e):
        _groom_creativity[0] = groom_creativity_dd.value or "balanced"
        _save_state()
        _update_groom_backend_hint()
    groom_creativity_dd.on_select = _groom_creativity_changed
    _groom_backend_hint_updater[0] = _update_groom_backend_hint

    # ภาษาเฉพาะของ Groom — ใช้ _mk_lang_dd ตัวเดียวกับ dropdown ภาษาอื่น แต่ผูกกับ _groom_lang
    # ของตัวเอง ไม่แตะ _desc_lang/_dialogue_lang ที่หน้าอื่นใช้ร่วมกัน
    groom_lang_dd = _mk_lang_dd(_groom_lang[0])
    def _groom_lang_changed(e):
        _groom_lang[0] = groom_lang_dd.value or "en"
        _save_state()
    groom_lang_dd.on_select = _groom_lang_changed

    # Genre/Genre2 ซ้ำจาก Outline Settings — ให้ปรับแนวได้ตรงนี้เลยตอน groom โดยไม่ต้องย้อนกลับไป
    # หน้า Outline Settings ค่าเริ่มต้น mirror genre_dd/genre2_dd (ตัวเดียวกับที่ _sync_genre() อ่าน)
    # แล้วเขียนกลับเข้า _sel_genre/_sel_genre2 + genre_dd.value/genre2_dd.value ทันทีที่เปลี่ยน — สอง
    # ฝั่ง sync กันสองทาง ไม่ใช่แค่ groom ใช้ค่าตาม outline ทางเดียวเหมือนเดิม
    groom_genre_dd = ft.Dropdown(
        value=_sel_genre[0],
        options=[ft.dropdown.Option(key=k, text=label) for k, (label, _, _) in GENRE_MAP.items()],
        text_size=12, height=40, expand=True, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    groom_genre2_dd = ft.Dropdown(
        value=_sel_genre2[0],
        options=_genre2_options,
        text_size=12, height=40, expand=True, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    def _groom_genre_changed(e=None):
        v = _genre_event_value(e, groom_genre_dd.value)
        if v:
            _sel_genre[0] = v
            genre_dd.value = v
        _save_state()
    def _groom_genre2_changed(e=None):
        v = _genre_event_value(e, groom_genre2_dd.value) or "none"
        if v:
            _sel_genre2[0] = v
            genre2_dd.value = v
        _save_state()
    groom_genre_dd.on_select = _groom_genre_changed
    groom_genre_dd.on_change = _groom_genre_changed
    groom_genre2_dd.on_select = _groom_genre2_changed
    groom_genre2_dd.on_change = _groom_genre2_changed

    async def _do_groom(e=None):
        if _groom_busy[0]:
            return
        # CREATE / New always starts from the ORIGINAL source (kept from the first run), never from the draft.
        _story = (_groom_orig[0] if _groom_active[0] else story_field.value).strip()
        _llm = llm_dd.value or selected_llm[0] or ""
        if not _story:
            groom_status_lbl.value = "⚠ Enter a story first"
            groom_status_lbl.color = C["text2"]
            page.update()
            return
        if not _llm:
            groom_status_lbl.value = "⚠ Select LLM first"
            groom_status_lbl.color = C["text2"]
            page.update()
            return
        if _llm == _GROK_OPTION and not _grok_api_key[0]:
            groom_status_lbl.value = "⚠ Enter Grok API Key first"
            groom_status_lbl.color = C["text2"]
            page.update()
            return
        groom_loading.visible = True
        groom_status_lbl.color = C["text2"]
        groom_status_lbl.value = (
            "✨ Story Editor via Grok API..."
            if _llm == _GROK_OPTION
            else f"✨ Story Editor via Comfy ({_llm})..."
        )
        _groom_set_busy(True)
        page.update()
        try:
            _sync_genre()
            groomed, card = await run_story_editing(
                raw_story=_story,
                llm_wf_file=_llm,
                lang=groom_lang_dd.value or _groom_lang[0] or _desc_lang[0],
                genre=_sel_genre[0],
                genre2=_sel_genre2[0],
                requirement=groom_req_field.value.strip(),
                on_log=_llm_log,
                grok_api_key=_grok_api_key[0], grok_model=_grok_model[0],
                outline_mode=_outline_mode_for_engines(),
                story_length=groom_length_dd.value or "auto",
                dialogue_style=_dialogue_style[0],
                creativity=groom_creativity_dd.value or _groom_creativity[0] or "balanced",
            )
        except Exception:
            import traceback
            _tb = traceback.format_exc()
            print(f"[main_ui] groom exception:\n{_tb}")
            await _llm_log(f"❌ Story Editor exception:\n{_tb}")
            groomed, card = _story, {"error": "exception — see log"}
        _groom_set_busy(False)
        card = card if isinstance(card, dict) else {}
        _err = (card.get("error") or "").strip()
        if _err:
            if not _groom_active[0]:   # a failed New must never overwrite the existing draft
                _groom_text[0] = groomed or _story
                _groom_card[0] = card
                if groomed:
                    groom_text_field.value = groomed
                    _groom_update_count()
                    _groom_update_action_state()
            groom_status_lbl.value = f"❌ Story Editor failed — {_err}"
            page.update()
            return
        _groom_text[0] = groomed
        _groom_card[0] = card
        _groom_session[0] = card.get("session_id", "") or ""    # New = a new session (empty for Grok: chat then falls back to stateless)
        _groom_session_story[0] = groomed                        # the session just produced this story
        _groom_session_age[0] = 0
        _groom_grok_msgs[:] = list(card.get("grok_messages") or [])    # New = a new Grok conversation too (empty for local)
        groom_text_field.value = groomed
        _groom_update_count()
        _groom_update_action_state()
        _groom_check_stale[0] = False
        _groom_show_check(card)
        _groom_orig[0] = _story
        _groom_active[0] = True
        _groom_chat.clear()
        _groom_chat.append({"role": "groom", "text": "Draft created. Tell me what you want to change."})
        _groom_render_chat()
        _groom_refresh_mode(select_chat=True)
        _save_state()
        # Clean Groom intentionally does not classify/extract a source visual style.
        if card.get("clean_groom"):
            _set_source_style("", "groom", _story)
            _src_style_msg = "🎬 source_style: not extracted by Story Editor"
        else:
            _set_source_style(card.get("source_style") or "", "groom", _story)
            _src_style_msg = (
                f"🎬 source_style captured: \"{_source_style[0]}\""
                if _source_style[0] else
                "🎬 source_style: (empty — legacy Story Editor returned none)"
            )
        print(f"[GROOM] {_src_style_msg}")
        await _llm_log(_src_style_msg)
        _unchanged = (groomed or "").strip() == _story
        if card.get("clean_groom"):
            if _unchanged:
                groom_status_lbl.value = "✓ Done — model kept the source unchanged"
            else:
                groom_status_lbl.value = "✓ Done"
        else:
            # Legacy Groom compatibility only.
            _check = (card or {}).get("final_check")
            _issues = groom_final_check_issues(_check) if _check else []
            if not _check:
                _check_txt = "Final Check unavailable"
            elif str(_check.get("status") or "").upper() == "FAIL":
                _check_txt = "Final Check failed"
            elif _issues:
                _check_txt = f"Final Check found {len(_issues)} issue" + ("s" if len(_issues) != 1 else "")
            else:
                _check_txt = "Final Check passed"
            if _unchanged:
                groom_status_lbl.value = f"⚠ Done but the story is unchanged — {_check_txt}"
                await _llm_log(
                    "⚠ Story Editor result equals the source. The main Story field does not change "
                    "until you press Create Outline — check the Edited story box in this dialog."
                )
            else:
                groom_status_lbl.value = (
                    ("✕ " if _check and str(_check.get("status") or "").upper() == "FAIL"
                     else "⚠ " if (_issues or not _check) else "✓ ")
                    + f"Done — {_check_txt}"
                )
        page.update()

    def _groom_click(e):
        asyncio.create_task(_do_groom())

    # ── Groom Chat (EDIT CURRENT DRAFT) ───────────────────────────────
    def _groom_continuing() -> bool:
        """Re-open with the previous draft + chat only while the main Story field still holds that story
        (the original, or the draft that "Use & Create Outline" wrote there); otherwise it is a new story."""
        if not (_groom_orig[0].strip() and _groom_text[0].strip()):
            return False
        return story_field.value.strip() in (_groom_orig[0].strip(), _groom_text[0].strip())

    def _groom_bubble(role: str, text: str, busy: bool = False) -> ft.Container:
        is_user = role == "user"
        return ft.Container(
            content=ft.Column([
                ft.Text("You" if is_user else "Groom", size=9, color=C["text3"], weight=ft.FontWeight.W_600),
                ft.Text(text, size=11, selectable=True, italic=busy, color=C["text3"] if busy else C["text1"]),
            ], spacing=2, tight=True),
            bgcolor=C["accent_lt"] if is_user else C["panel"],
            border_radius=br(8), padding=po(left=10, right=10, top=6, bottom=6),
            margin=ft.Margin(left=36 if is_user else 0, right=0 if is_user else 36, top=0, bottom=0),
        )

    def _groom_render_chat(busy: bool = False):
        ctrls = [_groom_bubble(m.get("role", "groom"), m.get("text", "")) for m in _groom_chat]
        if busy:
            ctrls.append(_groom_bubble("groom", "Groom is editing...", busy=True))
        groom_chat_list.controls = ctrls
        try:
            if groom_chat_list.page:
                groom_chat_list.update()
        except RuntimeError:
            pass

    def _groom_refresh_mode(select_chat: bool = False):
        """Story Editor <-> New button, and whether the chat can be used, from the current state."""
        _a = _groom_active[0]
        _ic = groom_main_btn.content.controls
        _ic[0].icon = ft.Icons.ADD_ROUNDED if _a else ft.Icons.AUTO_FIX_HIGH_ROUNDED
        _ic[1].value = "New" if _a else "Story Editor"
        groom_main_btn.tooltip = "Start over from the original story" if _a else None
        groom_main_btn.disabled = _groom_busy[0]
        groom_chat_input.disabled = (not _a) or _groom_busy[0]
        groom_chat_send_btn.disabled = (not _a) or _groom_busy[0]
        groom_chat_hint.visible = not _a
        if select_chat:
            try:
                groom_tabs.selected_index = 0
                groom_tabs.update()
            except Exception:
                pass
        try:
            if groom_main_btn.page:
                page.update()
        except RuntimeError:
            pass

    def _groom_set_busy(v: bool):
        _groom_busy[0] = v
        groom_loading.visible = v
        groom_text_field.disabled = v
        _groom_update_action_state()
        _groom_refresh_mode()

    async def _groom_chat_send(e=None):
        if _groom_busy[0] or not _groom_active[0]:
            return
        _msg = (groom_chat_input.value or "").strip()
        if not _msg:
            return
        _llm = llm_dd.value or selected_llm[0] or ""
        if not _llm:
            groom_status_lbl.value = "⚠ Select LLM first"; groom_status_lbl.color = C["text2"]; page.update(); return
        if _llm == _GROK_OPTION and not _grok_api_key[0]:
            groom_status_lbl.value = "⚠ Enter Grok API Key first"; groom_status_lbl.color = C["text2"]; page.update(); return
        # The working draft is whatever is in the editor RIGHT NOW (manual edits included).
        _current = groom_text_field.value or ""
        if not _current.strip():
            groom_status_lbl.value = "⚠ The Groomed Story is empty"; groom_status_lbl.color = C["text2"]; page.update(); return
        _groom_chat.append({"role": "user", "text": _msg})
        groom_chat_input.value = ""
        _groom_set_busy(True)
        _groom_render_chat(busy=True)
        groom_status_lbl.color = C["text2"]
        groom_status_lbl.value = "💬 Groom is editing..."
        page.update()
        try:
            _sync_genre()
            res = await run_story_chat_edit(
                original_source=_groom_orig[0], current_story=_current, user_request=_msg,
                llm_wf_file=_llm,
                lang=groom_lang_dd.value or _groom_lang[0] or _desc_lang[0],
                genre=_sel_genre[0], genre2=_sel_genre2[0],
                creativity=groom_creativity_dd.value or _groom_creativity[0] or "balanced",
                on_log=_llm_log, grok_api_key=_grok_api_key[0], grok_model=_grok_model[0],
                session_id=_groom_session[0],
                session_story=_groom_session_story[0], session_age=_groom_session_age[0],
                grok_messages=list(_groom_grok_msgs),
            )
        except Exception:
            import traceback
            _tb = traceback.format_exc()
            await _llm_log(f"❌ Story Editor chat exception:\n{_tb}")
            res = {"ok": False, "error": "exception — see log", "revised_story": None, "chat_reply": ""}
        _groom_set_busy(False)
        if res.get("ok") and "session_story" in res:      # keep track of what the chat session holds (token saving)
            _groom_session_story[0] = res.get("session_story") or ""
            _groom_session_age[0] = int(res.get("session_age") or 0)
            if "grok_messages" in res:
                _groom_grok_msgs[:] = list(res["grok_messages"])
        if res.get("ok") and res.get("revised_story"):
            if res.get("changed"):
                groom_text_field.value = res["revised_story"]
                _groom_text[0] = res["revised_story"]
                _groom_update_count()
                _groom_update_action_state()
                if _groom_card[0] and not _groom_check_stale[0]:
                    _groom_check_stale[0] = True
                    _groom_show_check(_groom_card[0], stale=True)
                _groom_chat.append({"role": "groom", "text": res.get("chat_reply") or
                                    "The story text was changed, but Groom did not explain what changed. Please compare the draft."})
                groom_status_lbl.value = "✓ Updated"
            else:   # the model returned the same story: say so instead of claiming an update
                _txt = res.get("chat_reply") or "I didn't change the story."
                if not res.get("chat_only"):
                    _txt += " (The story text is unchanged.)"
                _groom_chat.append({"role": "groom", "text": _txt})
                groom_status_lbl.value = ("💬 Groom replied — story unchanged" if res.get("chat_only")
                                          else "ℹ No change to the story — try a more specific request")
        else:
            _groom_chat.append({"role": "groom", "text":
                                "That reply wasn't in the expected format, so your story is unchanged. Try rephrasing, or open AI Logs to see the raw reply."})
            groom_status_lbl.value = f"⚠ Chat edit not applied — {res.get('error') or 'unknown error'}"
        _groom_render_chat()
        _save_state()
        page.update()

    def _groom_new_close(e=None):
        _groom_new_dlg.open = False
        page.update()

    def _groom_new_confirm(e=None):
        _groom_new_close()
        asyncio.create_task(_do_groom())

    _groom_new_dlg = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("Start over from the original story?", size=15, weight=ft.FontWeight.W_700, color=C["text1"]),
        content=ft.Container(width=380, content=ft.Text(
            "This will create a new story from the original source.\n"
            "Your current Groomed Story and Groom Chat will be replaced.", size=12, color=C["text2"])),
        actions=[
            ft.TextButton("Cancel", on_click=_groom_new_close, style=ft.ButtonStyle(color=C["text2"])),
            ft.FilledButton("New", on_click=_groom_new_confirm, bgcolor=C["accent"]),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(_groom_new_dlg)

    def _groom_main_click(e):
        if _groom_busy[0]:
            return
        if _groom_active[0]:
            _groom_new_dlg.open = True
            page.update()
        else:
            _groom_click(e)

    _flow_dialogs: list = []   # populated after dialogs are defined
    _active_flow_dialog: list = [None]   # tracks which of the 3 flow dialogs is currently shown

    # groom_dialog / outline_genre_dialog / outline_review_dialog are managed through
    # page.show_dialog()/page.pop_dialog(), Flet's own dialog stack — this actually pops
    # the client-side Navigator route. Manually toggling `.open` + page.update() left the
    # server thinking the dialog was closed while the client kept it stuck on screen.
    # NOTE: page.pop_dialog() only sets open=False; the dialog isn't removed from Flet's
    # internal stack until the client confirms the dismiss animation finished (async
    # round-trip). Calling show_dialog() again on that SAME instance too soon raises
    # RuntimeError("Dialog is already opened") — so re-opening the dialog that's already
    # showing (e.g. the outline-review "refresh" flow) must be a no-op, not pop+reshow.
    # _dialog_closing_until: monotonic loop time ก่อนหน้านั้นถือว่า client ยังเล่น dismiss
    # animation ของ dialog ก่อนหน้าอยู่ — ใช้แทนการเช็คแค่ _active_flow_dialog[0] is not None
    # เพราะ _close_dialog อาจถูกเรียกแยกจาก _open_dialog คนละจุด (เช่น groom confirm ปิด
    # groom_dialog ไปก่อนแล้ว ค่อยมาเปิด outline_review_dialog ทีหลังผ่าน task อื่น) ทำให้ตอน
    # _open_dialog ถูกเรียก _active_flow_dialog[0] เป็น None ไปแล้ว เลย skip การรอ ทั้งที่ client
    # ยังไม่ได้ dismiss dialog เก่าจริงๆ เป็นสาเหตุที่ dialog ใหม่โผล่มาแวบเดียวหรือไม่โผล่เลย
    _dialog_closing_until: list[float] = [0.0]

    def _close_dialog(dlg):
        if _active_flow_dialog[0] is not None:
            page.pop_dialog()
            _active_flow_dialog[0] = None
            _dialog_closing_until[0] = asyncio.get_event_loop().time() + 0.3

    async def _open_dialog_swap(dlg, already_popped: bool):
        if not already_popped:
            page.pop_dialog()
            _active_flow_dialog[0] = None
        remaining = _dialog_closing_until[0] - asyncio.get_event_loop().time()
        if remaining > 0:
            await asyncio.sleep(remaining)
        if _active_flow_dialog[0] is dlg:
            return   # โดน _open_dialog อื่นแซงเปิดไปแล้วระหว่างรอ
        page.show_dialog(dlg)
        _active_flow_dialog[0] = dlg

    def _open_dialog(dlg):
        if _active_flow_dialog[0] is dlg:
            return   # already showing this exact dialog — just update its content instead
        now = asyncio.get_event_loop().time()
        if _active_flow_dialog[0] is not None:
            asyncio.create_task(_open_dialog_swap(dlg, already_popped=False))
            return
        if now < _dialog_closing_until[0]:
            asyncio.create_task(_open_dialog_swap(dlg, already_popped=True))
            return
        page.show_dialog(dlg)
        _active_flow_dialog[0] = dlg

    def _groom_apply_text():
        """Shared by _groom_confirm/_groom_confirm_fast_lane: commit the groomed (or original)
        story text into story_field/_outline_story before either pipeline starts."""
        _sync_genre()
        groomed = groom_text_field.value.strip() or _groom_text[0]
        if groomed:
            story_field.value = groomed
            story_field.update()
            _outline_story[0] = groomed
            _groom_text[0] = groomed
            _save_state()
        else:
            _outline_story[0] = story_field.value.strip()

    def _groom_confirm(e):
        _groom_apply_text()
        _close_dialog(groom_dialog)
        _clear_outline_sheets()
        asyncio.create_task(_do_gen_shots_run())

    def _groom_confirm_fast_lane(e):
        # FAST-LANE-A1: same groomed-text handoff as "Use & Create Outline", but straight into
        # the same no-stops Outline->Fill->Render orchestrator the Outline Settings dropdown uses.
        _groom_apply_text()
        _close_dialog(groom_dialog)
        _clear_outline_sheets()
        asyncio.create_task(_fast_lane_run())

    def _groom_cancel(e):
        _save_state()
        _close_dialog(groom_dialog)
        _open_dialog(outline_genre_dialog)

    def _groom_plain_field(label, control):
        """ป้าย+ช่องแบบเบา ไม่มีกรอบการ์ด — ใช้ในแผงตั้งค่าฝั่งขวาที่วางซ้อนกันหลายช่อง
        STRETCH บังคับให้ control กว้างเท่า column แม่เสมอ ไม่ว่า control เองจะตั้ง expand ไว้หรือไม่
        (dropdown บางตัวไม่ได้ตั้ง expand เดิมเลยแคบกว่าตัวอื่น) ทำให้ทุกช่องกว้างเท่ากันแน่นอน"""
        return ft.Column([
            ft.Text(label, size=11, color=C["text2"], weight=ft.FontWeight.W_600),
            ft.Container(height=4),
            control,
        ], spacing=0, tight=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    def _groom_fit_to_window():
        """ปรับขนาดเนื้อหาให้พอดีหน้าต่างก่อนเปิด — เลย์เอาต์ 2 คอลัมน์ (เรื่อง ~75% / ตั้งค่า ~25%)
        กว้างกว่าเดิมที่เป็นคอลัมน์เดียว จึงขยายเพดานความกว้างขึ้น
        AlertDialog ของ Flet ไม่ย่อเนื้อหาให้เอง ต้องคำนวณเองตอนเปิด"""
        _w = page.width or page.window.width or 1200
        _h = page.height or page.window.height or 800
        _groom_content.width = max(680, min(1300, int(_w) - 140))
        # เผื่อที่ให้ title + แถวปุ่ม + padding ของ dialog รวม ~230px
        _groom_content.height = max(340, min(760, int(_h) - 230))
        # แถวปุ่มต้องกว้างเท่า content เอง ไม่งั้น spacer (Container expand=True) ตรงกลางไม่มีที่ให้ขยาย
        # แล้ว Cancel จะไปติดกับปุ่มขวาแทนที่จะอยู่ชิดซ้าย — AlertDialog ไม่ stretch actions ให้เอง
        _groom_actions_row.width = _groom_content.width

    # ── ฝั่งซ้าย: เรื่องที่ groom แล้ว (กว้างสุด — นี่คือผลลัพธ์หลักที่ผู้ใช้อ่าน/แก้) ──
    _groom_left_col = ft.Column([
        ft.Text("GROOMED STORY", size=11, weight=ft.FontWeight.W_700, color=C["text3"]),
        ft.Container(height=8),
        ft.Container(
            bgcolor=C["panel"], border_radius=br(10),
            border=ft.Border.all(1, C["border"]),
            padding=po(left=4, right=4, top=4, bottom=4),
            expand=True,
            content=groom_text_field,
        ),
        ft.Container(height=6),
        groom_count_lbl,
    ], spacing=0, expand=5, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    # ── ฝั่งขวา: แท็บ Chat | Settings — ตั้งค่า groom + Final Check อยู่ในแท็บ Settings ──
    _groom_settings_col = ft.Column([
        ft.Text("STORY EDITING SETTINGS", size=11, weight=ft.FontWeight.W_700, color=C["text3"]),
        ft.Container(height=10),
        _groom_plain_field("Primary Genre", groom_genre_dd),
        ft.Container(height=12),
        _groom_plain_field("Story Length", groom_length_dd),
        ft.Container(height=12),
        _groom_plain_field("Creativity", groom_creativity_dd),
        groom_backend_hint_lbl,
        ft.Container(height=12),
        _groom_plain_field("Output Language", groom_lang_dd),
        ft.Container(height=12),
        _groom_plain_field("Special Instructions", groom_req_field),
        ft.Container(height=6),
        ft.Text("Changes apply to New and may influence future Groom edits.", size=10, color=C["text3"]),
        ft.Container(height=16),
        divider(),
        ft.Container(height=12),
        ft.Text("FINAL CHECK", size=11, weight=ft.FontWeight.W_700, color=C["text3"]),
        ft.Container(height=8),
        groom_facts_card,
    ], spacing=0, expand=True, scroll=ft.ScrollMode.AUTO,
       horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    groom_chat_list = ft.ListView(spacing=6, expand=True, auto_scroll=True)
    groom_chat_hint = ft.Text("Groom Chat opens after the first Story Editor run.", size=11, color=C["text3"], italic=True)
    groom_chat_input = ft.TextField(
        hint_text="Ask Groom...", multiline=True, min_lines=1, max_lines=4, expand=True, disabled=True,
        text_size=12, bgcolor=C["surface"], border_color=C["border"], focused_border_color=C["accent"],
        border_radius=br(8), color=C["text1"], content_padding=po(left=10, right=10, top=8, bottom=8),
        shift_enter=True, on_submit=lambda e: asyncio.create_task(_groom_chat_send()),
    )
    groom_chat_send_btn = ft.FilledButton(
        content=ft.Row([ft.Icon(ft.Icons.SEND_ROUNDED, size=14), ft.Text("Send", size=11, weight=ft.FontWeight.W_600)],
                       tight=True, spacing=4),
        bgcolor=C["accent"], height=36, disabled=True,
        on_click=lambda e: asyncio.create_task(_groom_chat_send()),
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
    )
    _groom_chat_col = ft.Column([
        ft.Text("GROOM CHAT", size=11, weight=ft.FontWeight.W_700, color=C["text3"]),
        ft.Container(height=8),
        groom_chat_hint,
        groom_chat_list,
        ft.Container(height=8),
        ft.Row([groom_chat_input, groom_chat_send_btn], spacing=6, vertical_alignment=ft.CrossAxisAlignment.END),
    ], spacing=0, expand=True)

    groom_tabs = ft.Tabs(
        length=2, selected_index=1, expand=True,
        content=ft.Column([
            ft.TabBar(tabs=[ft.Tab(label="Chat"), ft.Tab(label="Settings")]),
            ft.Container(height=8),
            ft.TabBarView(controls=[_groom_chat_col, _groom_settings_col], expand=True),
        ], spacing=0, expand=True),
    )
    _groom_right_col = ft.Container(content=groom_tabs, expand=2)

    _groom_content = ft.Row([
        _groom_left_col,
        ft.VerticalDivider(width=25, color=C["border"]),
        _groom_right_col,
    ], spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)

    # Cancel ชิดซ้าย ปุ่ม Groom/Use & Create Outline ชิดขวา — ต้องตั้ง .width เอง (ดู
    # _groom_fit_to_window) เพราะ AlertDialog ไม่ stretch actions row ให้เต็มความกว้าง dialog เอง
    _GROOM_ACTION_BTN_HEIGHT = 38

    groom_use_btn = ft.FilledButton(
        content=ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, size=14, color="white"),
                         ft.Text("Use & Create Outline", size=12, color="white", weight=ft.FontWeight.W_600)],
                        tight=True, spacing=4),
        on_click=_groom_confirm,
        disabled=True,
        height=_GROOM_ACTION_BTN_HEIGHT,
        bgcolor=C["accent"],
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(
            radius=ft.BorderRadius(top_left=8, bottom_left=8, top_right=0, bottom_right=0))),
    )
    # FAST-LANE-A1: same hidden-dropdown pattern as the Outline Settings dialog's Create Outline
    # button — Groom -> Fast Lane skips Outline Settings too, straight to Outline->Fill->Render.
    groom_use_fast_lane_btn = ft.PopupMenuButton(
        icon=ft.Icons.ARROW_DROP_DOWN_ROUNDED, icon_color="white", icon_size=22,
        tooltip="More ways to start", height=_GROOM_ACTION_BTN_HEIGHT, disabled=True,
        style=ft.ButtonStyle(
            bgcolor=C["accent"],
            shape=ft.RoundedRectangleBorder(
                radius=ft.BorderRadius(top_left=0, bottom_left=0, top_right=8, bottom_right=8)),
        ),
        items=[
            ft.PopupMenuItem(
                content=ft.Text("⚡ Fast Lane — Outline → Image Prompts → Render, no stops"),
                on_click=_groom_confirm_fast_lane,
            ),
        ],
    )

    def _groom_update_action_state():
        """ปุ่ม Use & Create Outline (+ Fast Lane dropdown) กดได้ก็ต่อเมื่อกล่องเรื่องที่ groom
        แล้วมีข้อความจริง — ป้องกันกดข้ามไป Outline ทั้งที่ยังไม่เคย Groom หรือ text field ถูกล้างว่างไว้"""
        _ready = bool((groom_text_field.value or "").strip()) and not _groom_busy[0]
        groom_use_btn.disabled = not _ready
        groom_use_fast_lane_btn.disabled = not _ready

    groom_main_btn = ft.OutlinedButton(
        content=ft.Row([ft.Icon(ft.Icons.AUTO_FIX_HIGH_ROUNDED, size=14, color=C["accent"]),
                         ft.Text("Story Editor", size=12, color=C["accent"], weight=ft.FontWeight.W_600)],
                        tight=True, spacing=4),
        on_click=_groom_main_click,
        height=_GROOM_ACTION_BTN_HEIGHT,
        style=ft.ButtonStyle(side=ft.BorderSide(1, C["accent"]), shape=ft.RoundedRectangleBorder(radius=8)),
    )

    _groom_actions_row = ft.Row([
        ft.TextButton("Cancel", on_click=_groom_cancel, style=ft.ButtonStyle(color=C["text2"])),
        ft.Container(expand=True),
        groom_main_btn,
        ft.Container(width=8),
        ft.Row([groom_use_btn, groom_use_fast_lane_btn], spacing=1, tight=True),
    ], vertical_alignment=ft.CrossAxisAlignment.CENTER)

    groom_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        shape=ft.RoundedRectangleBorder(radius=16),
        title_padding=po(left=24, right=24, top=20, bottom=0),
        content_padding=po(left=24, right=24, top=16, bottom=8),
        title=ft.Column([
            ft.Row([
                ft.Container(
                    content=ft.Icon(ft.Icons.AUTO_FIX_HIGH_ROUNDED, size=18, color=C["accent"]),
                    bgcolor=C["accent_lt"], border_radius=br(8), padding=8,
                ),
                ft.Text("Story Editor", size=16, weight=ft.FontWeight.W_700, color=C["text1"]),
                ft.Container(expand=True),
                groom_loading,
                groom_status_lbl,
                groom_header_badge,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(height=14),
            divider(),
        ], spacing=0, tight=True),
        content=_groom_content,
        actions=[_groom_actions_row],
        actions_alignment=ft.MainAxisAlignment.END,
        actions_padding=po(left=24, right=24, top=4, bottom=20),
    )
    # groom_dialog / outline_genre_dialog / outline_review_dialog are managed via
    # page.show_dialog()/page.pop_dialog() (see _open_dialog/_close_dialog below) —
    # do NOT also add them to page.overlay, that caused the dialog to report
    # closed server-side while staying stuck open on screen.

    def _outline_genre_cancel(e):
        _close_dialog(outline_genre_dialog)

    def _outline_genre_confirm(e):
        _sync_genre()
        _sync_outline_mode()
        _sync_visual_preset()
        _dialogue_style.__setitem__(0, dialogue_style_dd.value or "speech_bubble")
        _dialogue_lang.__setitem__(0, dialogue_lang_dd.value or "en")
        _desc_lang.__setitem__(0, desc_lang_dd.value or "en")
        _source_lang.__setitem__(0, source_lang_dd.value or "en")
        _narration_type.__setitem__(0, narration_type_dd.value or "caption")
        _sync_prompt_style()
        _sync_fill_mode()
        _sync_style()
        _outline_story[0] = story_field.value.strip()
        _close_dialog(outline_genre_dialog)
        _clear_outline_sheets()
        asyncio.create_task(_do_gen_shots_run())

    def _outline_genre_groom(e):
        _sync_genre()
        groom_genre_dd.value = _sel_genre[0]
        groom_genre2_dd.value = _sel_genre2[0]
        _sync_outline_mode()
        _dialogue_style.__setitem__(0, dialogue_style_dd.value or "speech_bubble")
        _narration_type.__setitem__(0, narration_type_dd.value or "caption")
        _dialogue_lang.__setitem__(0, dialogue_lang_dd.value or "en")
        _desc_lang.__setitem__(0, desc_lang_dd.value or "en")
        _source_lang.__setitem__(0, source_lang_dd.value or "en")
        _sync_prompt_style()
        _sync_style()
        _close_dialog(outline_genre_dialog)
        _groom_busy[0] = False
        if _groom_continuing():
            groom_text_field.value = _groom_text[0]
            _groom_active[0] = True
            groom_status_lbl.value = "Edit the story directly, or tell Groom what to change."
        else:
            groom_text_field.value = ""
            _groom_text[0] = ""; _groom_orig[0] = ""; _groom_chat.clear(); _groom_active[0] = False
            groom_status_lbl.value = "Choose length + creativity, then run Story Editor ✨"
        groom_status_lbl.color = C["text3"]
        groom_header_badge.value = ""
        groom_count_lbl.value = ""
        groom_check_col.controls = []
        groom_facts_card.visible = False
        groom_loading.visible = False
        groom_text_field.disabled = False
        _groom_update_count()
        _groom_render_chat()
        _groom_update_action_state()
        _groom_refresh_mode(select_chat=_groom_active[0])
        _groom_fit_to_window()
        _update_groom_backend_hint()
        _open_dialog(groom_dialog)

    async def _fast_lane_run():
        """FAST-LANE-A1: one click, no stops — A1->A2->B (count dialog skipped, uses A2's own
        recommended count) -> Fill every shot -> Render every shot. Reuses the exact same
        awaitable stage functions the manual buttons call (_outline_generate_dispatch,
        _do_fill_prompts_run, and Fill's own existing after-fill auto-render chain) so nothing
        about A1/A2/B/Fill/render semantics is duplicated or reimplemented here — this is pure
        sequencing glue."""
        if llm_busy[0]:
            return
        if not story_field.value.strip():
            status_lbl.value = "⚠ Enter a story first"; page.update(); return
        if not llm_dd.value:
            status_lbl.value = "⚠ Select LLM first"; page.update(); return
        if llm_dd.value == _GROK_OPTION and not _grok_api_key[0]:
            status_lbl.value = "⚠ Enter Grok API Key first"; page.update(); return
        if not (api_dd.value or selected_api[0]):
            status_lbl.value = "⚠ Select an Image Workflow first — Fast Lane renders images too"
            page.update(); return

        await _llm_log("⚡ Fast Lane: Outline → Image Prompts → Render, no manual stops")
        try:
            await _outline_generate_dispatch(ask_shot_count=False, reuse_pass_a=False)
            if _b_run_state[0] != "FINISHED_SUCCESS" or not shots_data:
                return  # the failed stage already reported its own error via _on_err

            _outline_review_apply_edits()
            _close_dialog(outline_review_dialog)
            rebuild_shots_col()
            _save_state()
            page.update()

            # Borrow Fill's own existing "auto-render when done" chain (see _do_fill_prompts_run_inner)
            # instead of calling do_run_all separately — one shared render path, no duplication.
            _prev_after_fill = _after_fill_images_dd.value
            _after_fill_images_dd.value = "run"
            try:
                await _do_fill_prompts_run(clear_first=True)
            finally:
                _after_fill_images_dd.value = _prev_after_fill
        except Exception:
            import traceback
            _tb = traceback.format_exc()
            print(f"[main_ui] _fast_lane_run exception:\n{_tb}")
            await _llm_log(f"❌ Fast Lane exception:\n{_tb}")
            llm_busy[0] = False; llm_prog.visible = False
            gen_shots_btn.disabled = False; fill_prompts_btn.disabled = False
            status_lbl.value = "❌ Fast Lane error — see log"; page.update()

    def _outline_genre_fast_lane(e):
        _sync_genre()
        _sync_outline_mode()
        _sync_visual_preset()
        _dialogue_style.__setitem__(0, dialogue_style_dd.value or "speech_bubble")
        _dialogue_lang.__setitem__(0, dialogue_lang_dd.value or "en")
        _desc_lang.__setitem__(0, desc_lang_dd.value or "en")
        _source_lang.__setitem__(0, source_lang_dd.value or "en")
        _narration_type.__setitem__(0, narration_type_dd.value or "caption")
        _sync_prompt_style()
        _sync_fill_mode()
        _sync_style()
        _outline_story[0] = story_field.value.strip()
        _close_dialog(outline_genre_dialog)
        _clear_outline_sheets()
        asyncio.create_task(_fast_lane_run())

    # widget ที่ยังต้องอยู่ในต้นไม้เพื่อให้ตัว sync ทำงาน แต่ไม่ต้องให้ผู้ใช้เห็นแล้ว:
    #  • outline_style_dd — outline ส่ง image_style="" เสมอ (เลือก style จริงตอน Run All) แต่
    #    _sync_style() ยัง mirror dropdown 3 บานถึงกันอยู่ ถ้าลบจะพัง
    #  • _manga_switch_outline — โหมด Manga เลือกได้จาก "Outline Style" แล้ว toggle นี้ซ้ำซ้อน
    #    และเคยเป็นต้นเหตุ state ค้างข้ามโหมด ยังคง widget ไว้เพราะ _sync_outline_mode() เขียน
    #    .value/.disabled ของมันทุกครั้ง
    _og_hidden = ft.Column(
        [outline_style_dd, scene_detail_dd, _outline_compact_switch, fill_mode_dd, outline_fill_mode_dd],
                           spacing=0, tight=True, visible=False)

    outline_genre_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("🎭 Outline Settings", size=14, weight=ft.FontWeight.W_700, color=C["text1"]),
        content=ft.Column([
            ft.Row([
                ft.Column([
                    _og_group("STORY",
                        _og_field("Primary Genre", genre_dd),
                        _og_field("Description Language", desc_lang_dd,
                                  "shot_desc, character, scene, props, narration"),
                        ft.Container(content=ft.Column([
                            _shots_lbl,
                            ft.Row([num_shots_field, _shots_unit_lbl], spacing=6,
                                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        ], spacing=3, tight=True), padding=po(left=0, right=0, top=0, bottom=12)),
                    ),
                ], spacing=0, tight=True, expand=True),
                ft.Column([
                    _og_group("SHOT DESIGN",
                        _og_field("Outline Style", outline_mode_dd),
                        _og_field("Visual Preset", outline_preset_dd),
                    ),
                    _og_group("DIALOGUE & NARRATION",
                        _og_field("Dialogue Style", dialogue_style_dd),
                        _og_field("Dialogue Language", dialogue_lang_dd,
                                  "subtitle only"),
                        _og_field("Narration Type", narration_type_dd),
                    ),
                ], spacing=0, tight=True, expand=True),
            ], spacing=28, vertical_alignment=ft.CrossAxisAlignment.START),
            _og_hidden,
        ], spacing=0, tight=True, scroll=ft.ScrollMode.AUTO, height=560, width=820),
        actions=[
            ft.TextButton("Cancel", on_click=_outline_genre_cancel,
                         style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("✨ Story Editor First", on_click=_outline_genre_groom,
                         style=ft.ButtonStyle(color=ft.Colors.PURPLE_300)),
            ft.Row([
                ft.FilledButton("Create Outline", on_click=_outline_genre_confirm,
                               bgcolor=C["accent"], height=_GROOM_ACTION_BTN_HEIGHT,
                               style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(
                                   radius=ft.BorderRadius(top_left=8, bottom_left=8, top_right=0, bottom_right=0)))),
                # FAST-LANE-A1: hidden behind this dropdown so the default one-click flow
                # (review each stage) never changes — Fast Lane is an opt-in extra, not a swap.
                ft.PopupMenuButton(
                    icon=ft.Icons.ARROW_DROP_DOWN_ROUNDED, icon_color="white", icon_size=22,
                    tooltip="More ways to start", height=_GROOM_ACTION_BTN_HEIGHT,
                    style=ft.ButtonStyle(
                        bgcolor=C["accent"],
                        shape=ft.RoundedRectangleBorder(
                            radius=ft.BorderRadius(top_left=0, bottom_left=0, top_right=8, bottom_right=8)),
                    ),
                    items=[
                        ft.PopupMenuItem(
                            content=ft.Text("⚡ Fast Lane — Outline → Image Prompts → Render, no stops"),
                            on_click=_outline_genre_fast_lane,
                        ),
                    ],
                ),
            ], spacing=1, tight=True),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    # (not added to page.overlay — see note above groom_dialog)

    # ── Outline Review Dialog ──────────────────────────────────────────
    _outline_review_fields: list[ft.TextField] = []
    _outline_review_story_fields: list[ft.TextField] = []
    _outline_review_narration_fields: list[ft.TextField] = []
    _outline_review_speech_fields: list[ft.TextField] = []
    # MANGA-REVIEW-CANONICAL-EDIT-A1: Manga vNext canonical truth is shot["_manga_page_spec"]
    # (page locks + panels[].region_pct/desc) — Fill Manga reads that directly, never the flat
    # DISPLAY-ONLY fields manga_vnext_page_to_legacy_dict() synthesizes for the rest of the Review
    # Outline UI. One inner list of TextFields per shot/page, one TextField per panel, editing
    # panels[i]["desc"] only — never scene_desc/shot_desc, which are display artifacts for Manga
    # rows. Empty/unused (stays []) whenever _manga_vnext_mode[0] is False, so Normal review is
    # completely untouched by this list's existence.
    _outline_review_panel_fields: list[list[ft.TextField]] = []
    _outline_review_req = ft.TextField(
        value=saved.get("outline_req", ""),
        multiline=True, min_lines=3, max_lines=5,
        hint_text="Additional instructions for regeneration (optional)...",
        text_size=12, bgcolor=C["surface"], border_color=C["border"], color=C["text1"],
        expand=True,
    )
    _outline_review_req.on_change = lambda e: _save_state()
    _outline_review_status = ft.Text("", size=11, color=C["text2"])
    _outline_review_loading = ft.ProgressRing(width=14, height=14, stroke_width=2, visible=False)
    _outline_timer_task: list = [None]
    _outline_start_time: list[float] = [0.0]

    async def _outline_tick(label: str):
        while True:
            await asyncio.sleep(1)
            elapsed = int(time.time() - _outline_start_time[0])
            _outline_review_status.value = f"{label} ({elapsed}s)"
            try: page.update()
            except: break

    def _outline_timer_start(label: str):
        if _outline_timer_task[0]: _outline_timer_task[0].cancel()
        _outline_start_time[0] = time.time()
        _outline_timer_task[0] = asyncio.create_task(_outline_tick(label))

    def _outline_timer_stop(final: str):
        if _outline_timer_task[0]: _outline_timer_task[0].cancel(); _outline_timer_task[0] = None
        _outline_review_status.value = final
    # ── ถามจำนวนช็อตหลังรอบ A จบ ก่อนรอบ B เริ่ม (แทนปุ่ม "Use AI recommendation" เดิมที่โผล่หลัง
    # generate เสร็จทั้งหมดแล้วต้องสร้างซ้ำทั้งก้อนถ้าจะเปลี่ยนเลข) — เป็น AlertDialog แยกต่างหาก เพิ่มเข้า
    # page.overlay ตอนใช้จริง (ตามแบบ fill_confirm_dialog/check_confirm_dialog/director_confirm_dialog)
    # ไม่ใช่ผ่านระบบ dialog stack ของแอป (page.show_dialog()) เพราะระบบนั้นรองรับแค่ dialog เดียวพร้อมกัน —
    # แต่ ft.AlertDialog ผ่าน page.overlay ซ้อนบน dialog ที่เปิดอยู่แล้วได้ตามปกติของ Flet เอง
    # เดิมเคยลองทำเป็น section ฝังใน outline_review_dialog พร้อม widget ที่ตั้ง .text ทีหลัง แต่ปุ่มไม่แสดง
    # ข้อความ (สร้างใหม่ทุกครั้งแทนเพื่อกันปัญหานี้)
    _ask_shot_pending: list = [None]   # (asyncio.Event, result_box, dialog) ระหว่างรอ user ตอบ — ให้ Cancel ปลุกได้
    _pass_a_fail_pending: list = [None]  # asyncio.Event ตอนโชว์ dialog รอบ A ล้มเหลว
    _audit_gate_pending: list = [None]  # (done, result_box, dlg) Outline Python-audit FAIL choices
    _cast_review_pending: list = [None]  # (done, result_box, dlg) Step 1 character review
    # A2-MICRO-COUNT-INSTEAD-OF-SHOT-RECOMMENDATION-A1: A2 unit count is decomposition observability
    # only. It is not a one-unit-per-shot contract. Final image count is user authority; B owns
    # final selection.
    _last_a2_micro_count: list[int] = [0]
    def _synthesize_vnext_a1_from_fields() -> dict:
        """NEXT: a project saved/reopened before this fix (or restored from session autosave without
        a persisted vNext bible) had no A1 macro-event bible in memory — Fill would silently fall
        back to the legacy engine (main_ui.py _fill_once gate: `if _VNEXT_DEFAULT and
        _outline_vnext_a1[0]`). character_sheet/world_sheet are the only two fields
        _build_fill_vnext_prompt actually reads, so a best-effort sheet from the already-loaded UI
        fields is enough to keep Fill on the vNext engine always; macro_events/important_objects stay
        empty since nothing here can reconstruct Shot List's actual decomposition — Shot List only ever reruns from a
        fresh Story Bible / Beat Sheet generation, never from this synthesized stand-in."""
        return {
            "character_sheet": char_field.value or "",
            "world_sheet": scene_field.value or "",
            "locations": _outline_locations[0] or "",
            "important_objects": props_field.value or "",
            "continuity_notes": "",
            "macro_events": [],
        }

    # VNEXT-PRODUCTION-PROMOTION-A1 / VNEXT-WORKFLOW-REALIGN-PHASE1-A1: A1's macro-event bible and
    # A2's micro-beat inventory, kept across a "regenerate shots only" / count-only change so B+Fill
    # can rerun alone without re-reading the story or re-decomposing it.
    # VNEXT-B-CLOTHING-STATE-FORM-A1 follow-up: restore a persisted bible from session autosave if
    # present; otherwise synthesize one so this is never None/empty and Fill never falls back to the
    # legacy engine, regardless of whether this project ever ran vNext outline generation.
    _saved_vnext_a1 = saved.get("outline_vnext_a1")
    _outline_vnext_a1: list[dict | None] = [
        _saved_vnext_a1 if isinstance(_saved_vnext_a1, dict) and _saved_vnext_a1
        else _synthesize_vnext_a1_from_fields()
    ]
    _saved_vnext_a2 = saved.get("outline_vnext_a2")
    _outline_vnext_a2: list[dict | None] = [
        _saved_vnext_a2 if isinstance(_saved_vnext_a2, dict) and _saved_vnext_a2 else None
    ]

    # FILL-CREATIVE-DESIGN-LEDGER-A1: the current story's Normal Fill Design Ledger — ONE opaque string
    # (visual-design memory written by Fill), its own state, never merged into A1/props/scene/B data.
    # Restored with the story's saved state, saved/loaded with projects, reset with a new outline.
    _saved_fill_ledger = saved.get("fill_design_ledger")
    _fill_design_ledger: list[str] = [_saved_fill_ledger if isinstance(_saved_fill_ledger, str) else ""]
    set_fill_ledger_getter(lambda: _fill_design_ledger[0])

    async def _ask_shot_count(micro_n: int, micro_label: str, requested_n: int,
                              unit: str = "shots", recommended: bool = False) -> int | None:
        """หยุดรอ user เลือกจำนวนช็อตก่อนรอบ B — คืนเลขที่เลือก หรือ None ถ้าโดน Cancel ระหว่างรอ
        (ดู _outline_review_cancel ที่ปลุก event นี้เพื่อกันค้างรอปุ่มที่ไม่มีทางถูกกดแล้ว)

        A2-MICRO-COUNT-INSTEAD-OF-SHOT-RECOMMENDATION-A1: micro_n is only how many micro units A2
        found. A2-UNIT-1TO1-DEFAULT: that structural count IS the recommended shot count for BOTH
        Sequential and Cinematic — choosing exactly it makes B map A2 units 1:1; it is not a model
        judgment. requested_n (the dialog's own default/custom-field seed) is always that same count."""
        result: list[int | None] = [requested_n]
        done = asyncio.Event()

        def _close_ask():
            # อย่า remove จาก overlay พร้อมปิด — Flet ส่ง open=False ไม่ถึง client
            # แล้ว modal ค้างทั้งที่รอบ B ไป queue ที่ Comfy แล้ว (แบบเดียวกับ fill_confirm_dialog)
            dlg.open = False

        def _pick(n: int):
            def _h(e):
                result[0] = n
                _close_ask()
                page.update()
                done.set()
            return _h

        _custom_field = ft.TextField(
            # No fixed height: an explicit height made the visible box sit at the top of a taller slot,
            # so the field floated above the buttons. Natural height (text + padding) is ~40 and the
            # Row's CENTER alignment then lines it up with the two 40px buttons.
            value=str(requested_n), hint_text="Custom", width=80, text_size=13, dense=True,
            content_padding=po(left=10, right=10, top=11, bottom=11),
            border_radius=8,
            keyboard_type=ft.KeyboardType.NUMBER, color=C["text1"],
            border_color=C["border"], focused_border_color=C["amber"],
        )
        _custom_err = ft.Text("", size=11, color=C["red"])

        def _pick_custom(_e=None):
            raw = (_custom_field.value or "").strip()
            try:
                n = int(raw)
                if n <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                _custom_err.value = "Enter a whole number greater than 0"
                page.update()
                return
            result[0] = n
            _close_ask()
            page.update()
            done.set()

        _custom_field.on_submit = _pick_custom

        def _cancel(_e=None):
            result[0] = None
            _close_ask()
            # เหมือน _outline_review_cancel — ถ้า Review dialog เปิดค้างอยู่ข้างล่างด้วย ต้องปิดมันด้วย
            # ไม่งั้นเช็ค "picked is None and not outline_review_dialog.open" ฝั่งผู้เรียกจะไม่ true
            # แล้ว flow จะไม่หยุดจริง กลายเป็นเดินหน้าต่อด้วยเลขเดิมแทนที่จะกลับไปหน้า outline settings
            if outline_review_dialog.open:
                _close_dialog(outline_review_dialog)
            page.update()
            done.set()

        dlg = ft.AlertDialog(
            modal=True, bgcolor=C["surface"],
            title=ft.Text(f"Final {unit} count", size=15, weight=ft.FontWeight.W_700, color=C["text1"]),
            content=ft.Container(
                width=440,
                content=ft.Column(
                    [
                        ft.Text(
                            f"Beat Sheet found {micro_n} {micro_label}.\n"
                            + (f"Recommended {unit}: {micro_n} (one per {micro_label[:-1]}).\n"
                               if recommended else "")
                            + f"Choose how many final {unit} B should design.\n\n"
                            f"Pick now — the shot pass has not started yet.",
                            size=13, color=C["text2"],
                        ),
                        _custom_err,
                    ],
                    spacing=10, tight=True,
                ),
            ),
            actions=[
                ft.Row(
                    [
                        ft.TextButton(content=ft.Text("Cancel", size=13, color=C["text2"]),
                                      on_click=_cancel),
                        _custom_field,
                        ft.OutlinedButton(content=ft.Text("Use custom", size=13, color=C["text1"]),
                                          height=40, on_click=_pick_custom),
                        ft.FilledButton(
                            height=40,
                            content=ft.Text((f"Use recommended {micro_n} {unit}" if recommended
                                             else f"Use {requested_n} {unit}"), size=13, color="white",
                                            weight=ft.FontWeight.W_600),
                            bgcolor=C["amber"],
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
                            on_click=_pick(micro_n if recommended else requested_n),
                        ),
                    ],
                    spacing=10, alignment=ft.MainAxisAlignment.END,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            actions_padding=po(left=20, right=20, top=4, bottom=16),
        )
        _ask_shot_pending[0] = (done, result, dlg)
        # overlay ซ้อนบน Review dialog ได้ — page.show_dialog() รองรับแค่ใบเดียว จะกลืน Review
        # แล้วดูเหมือนถามหลังจบ ทั้งที่ตั้งใจถามก่อนรอบ B เพื่อไม่เสีย token เขียนช็อต
        page.overlay.append(dlg)
        dlg.open = True
        _outline_timer_stop(f"Waiting — choose final {unit} count (Beat Sheet found {micro_n} {micro_label})")
        page.update()

        await done.wait()

        dlg.open = False
        page.update()
        await asyncio.sleep(0.05)
        if dlg in page.overlay:
            page.overlay.remove(dlg)
        _ask_shot_pending[0] = None
        return result[0]

    async def _ask_outline_audit_action(pass_label: str, audit: dict, issues: list,
                                        allow_repair: bool = True) -> str | None:
        """Pause on the model's own final check. Returns continue_anyway|regenerate|llm_repair|python_repair, or None if cancelled.

        ผลตรวจมาจาก LLM ในคำตอบเดียวกับผลลัพธ์ ไม่ใช่การเดาด้วยคีย์เวิร์ดฝั่ง Python

        allow_repair=False (Outline Step 1): เป็น review gate ล้วน ๆ ไม่มีปุ่มซ่อม — ผู้ใช้เลือกได้แค่
        ทำต่อ/generate ใหม่ ป้องกัน Python audit (ที่อ่าน "Outfit: ..." เป็นตัวละครอีกคนแล้วฟ้องเท็จ)
        ไม่ให้มีทางเปิด repair pass ใด ๆ จากจุดนี้"""
        result: list[str | None] = [None]
        done = asyncio.Event()
        n = len(issues or [])

        def _pick(action: str):
            def _h(_e):
                result[0] = action
                dlg.open = False
                page.update()
                done.set()
            return _h

        _chip_style = {
            "Clothing continuity": ("Clothing", C["red_lt"], C["red"]),
            "Presence/spacing": ("Presence", C["accent_lt"], C["accent"]),
            "Canonical names": ("Canonical names", C["purple_lt"], C["purple"]),
            "Place continuity": ("Place", C["panel"], C["text2"]),
            "Environment continuity": ("Env", C["panel"], C["text2"]),
            "Opening outfits": ("Outfits", C["red_lt"], C["red"]),
            "Ages": ("Ages", C["panel"], C["text2"]),
        }
        grouped: dict[str, list] = {}
        for iss in issues or []:
            g = str((iss or {}).get("group") or (iss or {}).get("check") or "Issue")
            grouped.setdefault(g, []).append(iss)

        chips = []
        for g, rows in grouped.items():
            label, bg, fg = _chip_style.get(g, (g.replace("_", " "), C["panel"], C["text2"]))
            chips.append(ft.Container(
                content=ft.Text(f"{label}  {len(rows)}", size=12,
                                weight=ft.FontWeight.W_600, color=fg),
                bgcolor=bg, padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                border_radius=8, border=ft.Border.all(1, C["border"]),
            ))
        chip_row = ft.Row(chips, spacing=8, wrap=True) if chips else ft.Container()

        cards = []
        for g, rows in grouped.items():
            header = _chip_style.get(g, (g, None, None))[0]
            lines = []
            for iss in rows:
                det = str((iss or {}).get("detail") or "").strip() or "validation failed"
                shot = (iss or {}).get("shot")
                prefix = f"Shot {shot}: " if shot not in (None, "") else ""
                lines.append(ft.Text(f"⚠  {prefix}{det}", size=12.5, color=C["text1"],
                                     selectable=True))
            cards.append(ft.Container(
                content=ft.Column(
                    [ft.Text(header, size=13, weight=ft.FontWeight.W_600, color=C["text1"]),
                     *lines],
                    spacing=4, tight=True,
                ),
                bgcolor=C["red_lt"], padding=12, border_radius=10,
                border=ft.Border.all(1, C["border"]),
            ))
        _need_scroll = n > 3
        cards_col = ft.Column(cards, spacing=8, tight=True,
                              scroll=ft.ScrollMode.AUTO if _need_scroll else None)
        if _need_scroll:
            cards_col = ft.Container(content=cards_col, height=200)

        _btn_shape = ft.RoundedRectangleBorder(radius=9)
        _pad = ft.Padding.symmetric(horizontal=14, vertical=8)

        def _outlined(label, tooltip, action, color, side):
            return ft.OutlinedButton(
                content=ft.Text(label, size=13, color=color, weight=ft.FontWeight.W_500),
                tooltip=tooltip, height=40, on_click=_pick(action),
                style=ft.ButtonStyle(shape=_btn_shape, padding=_pad,
                                     side=ft.BorderSide(1, side)),
            )

        _action_buttons = [
            _outlined("Continue anyway", "Keep current result", "continue_anyway",
                      C["text1"], C["border2"]),
            _outlined("Regenerate", "Generate this step again", "regenerate",
                      C["text1"], C["border2"]),
        ]
        if allow_repair:
            _action_buttons += [
                ft.FilledButton(
                    content=ft.Text("Fix by LLM", size=13, color="white",
                                    weight=ft.FontWeight.W_600),
                    tooltip="Repair using validation results",
                    height=40, bgcolor=C["purple"], on_click=_pick("llm_repair"),
                    style=ft.ButtonStyle(shape=_btn_shape, padding=_pad),
                ),
                _outlined("Python fix", "Apply deterministic fixes", "python_repair",
                          C["amber"], C["amber"]),
            ]
        actions_row = ft.Row(
            _action_buttons,
            spacing=8, wrap=True, alignment=ft.MainAxisAlignment.END,
        )

        _h = page.height or getattr(page.window, "height", None) or 800
        dlg = ft.AlertDialog(
            modal=True, bgcolor=C["surface"],
            shape=ft.RoundedRectangleBorder(radius=16),
            title=ft.Row([
                ft.Container(
                    content=ft.Text("⚠", size=14, text_align=ft.TextAlign.CENTER),
                    width=32, height=32, bgcolor=C["red_lt"],
                    border_radius=16, alignment=ft.Alignment.CENTER,
                    border=ft.Border.all(1, C["border"]),
                ),
                ft.Column([
                    ft.Text(pass_label, size=15, weight=ft.FontWeight.W_600, color=C["text1"]),
                    ft.Text(
                        f"Final Check found {n} issue" if n == 1
                        else f"Final Check found {n} issues",
                        size=12, color=C["text2"],
                    ),
                ], spacing=1, tight=True),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            title_padding=po(left=22, right=22, top=18, bottom=8),
            content=ft.Container(
                width=590,
                content=ft.Column(
                    [
                        chip_row,
                        cards_col,
                        ft.Text("Reported by the model that wrote this step. Choose an action.",
                                size=12, color=C["text3"]),
                    ],
                    spacing=14, tight=True,
                ),
            ),
            content_padding=po(left=22, right=22, top=4, bottom=8),
            actions=[actions_row],
            actions_alignment=ft.MainAxisAlignment.END,
            actions_padding=po(left=20, right=20, top=4, bottom=16),
            inset_padding=po(left=24, right=24, top=24, bottom=24),
        )
        # Cap overall content so many-issue lists scroll inside the dialog, not the screen.
        _max_body = max(220, int(float(_h) * 0.55))
        if n > 3:
            dlg.content.height = min(_max_body, 420)
        _audit_gate_pending[0] = (done, result, dlg)
        page.overlay.append(dlg)
        dlg.open = True
        _outline_timer_stop(f"Waiting — Final Check found {n} issue{'s' if n != 1 else ''}")
        page.update()
        await done.wait()
        dlg.open = False
        page.update()
        await asyncio.sleep(0.05)
        if dlg in page.overlay:
            page.overlay.remove(dlg)
        _audit_gate_pending[0] = None
        return result[0]

    async def _ask_cast_review(char_desc: str) -> tuple[str | None, str]:
        """Step 1 character + outfit review. Returns (continue|regenerate|None, sheet)."""
        # เจอจริง: LLM เผลอขึ้นบรรทัดใหม่ก่อน "Outfit:" ทำให้ parser อ่าน "Outfit" เป็นตัวละครปลอม
        # (การ์ดที่ 2 ในภาพหน้าจอ) — เตือนผู้ใช้ตรงๆ แทนที่จะซ่อมเงียบๆ แล้วปล่อยให้ Fill ใช้ชีตที่ยังพัง
        _leaked_word = detect_sheet_field_leaked_as_name(char_desc or "")
        if _leaked_word:
            # ชีตยังพังอยู่ (การ์ดปลอมจะโผล่ใน Review Characters ถ้าเปิดต่อ) — ยกเลิก dialog
            # ตรวจตัวละครไปเลย ให้ alert นี้เป็นทางเลือกเดียว: Regenerate หรือ Continue anyway
            _warn_done = asyncio.Event()
            _warn_result: list = ["regenerate"]
            _warn_dlg = ft.AlertDialog(
                modal=True, bgcolor=C["surface"],
                shape=ft.RoundedRectangleBorder(radius=18),
                title=ft.Text("⚠ Character Sheet Issue", size=17,
                               weight=ft.FontWeight.W_600, color=C["text1"]),
                content=ft.Text(
                    f"There's a problem with the LLM's character sheet formatting — "
                    f"\"{_leaked_word}\" was read as a fake character. This may lead to bad "
                    "images. Please Regenerate, or Continue anyway if you've reviewed it.",
                    size=13, color=C["text2"],
                ),
                actions=[
                    ft.OutlinedButton(
                        content=ft.Text("Continue anyway", size=13, color=C["text1"]),
                        style=ft.ButtonStyle(
                            shape=ft.RoundedRectangleBorder(radius=9),
                            padding=ft.Padding.symmetric(horizontal=14, vertical=8),
                            side=ft.BorderSide(1, C["border2"])),
                        on_click=lambda e: (
                            _warn_result.__setitem__(0, "continue"),
                            _warn_dlg.__setattr__("open", False),
                            page.update(), _warn_done.set(),
                        ),
                    ),
                    ft.FilledButton(
                        content=ft.Text("Regenerate", size=13, color="white",
                                        weight=ft.FontWeight.W_600),
                        bgcolor=C["purple"],
                        style=ft.ButtonStyle(
                            shape=ft.RoundedRectangleBorder(radius=9),
                            padding=ft.Padding.symmetric(horizontal=14, vertical=8)),
                        on_click=lambda e: (
                            _warn_result.__setitem__(0, "regenerate"),
                            _warn_dlg.__setattr__("open", False),
                            page.update(), _warn_done.set(),
                        ),
                    ),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            page.overlay.append(_warn_dlg)
            _warn_dlg.open = True
            page.update()
            await _warn_done.wait()
            if _warn_dlg in page.overlay:
                page.overlay.remove(_warn_dlg)
            return _warn_result[0], (char_desc or "")
        result: list = [("continue", char_desc or "")]
        done = asyncio.Event()
        sheet_box = [char_desc or ""]
        mode = ["view"]
        outfit_fields: dict = {}
        # การ์ดที่ผู้ใช้กด "แก้ไข" อยู่ตอนนี้ — โหมด review เป็นข้อความล้วน ช่องกรอกโผล่เฉพาะการ์ดนี้
        editing = [""]
        body = ft.Column([], spacing=12, tight=True)
        # กว้างเต็มไดอะล็อกและสูงพอจะเห็นชีตทั้งก้อน (ของเดิมแคบจนเหลือที่ว่างข้าง ๆ เป็นแถบใหญ่)
        edit_tf = ft.TextField(
            value=sheet_box[0], multiline=True, min_lines=16, max_lines=18,
            text_size=12, bgcolor=C["panel"], border_color=C["border"],
            color=C["text1"], text_style=ft.TextStyle(font_family="Consolas, monospace"),
            border_radius=9, content_padding=po(left=12, right=12, top=10, bottom=10),
        )
        _btn_shape = ft.RoundedRectangleBorder(radius=9)
        _pad = ft.Padding.symmetric(horizontal=14, vertical=8)
        # ~780 ตามสเปก แต่หดตามหน้าต่างเล็ก (สูงสุด ~88% ของความกว้างจอ)
        _win_w = page.width or getattr(page.window, "width", None) or 1200
        _dlg_w = max(560, min(780, int(_win_w * 0.88)))

        def _collect_outfits() -> dict:
            return {k: (tf.value or "").strip() for k, tf in outfit_fields.items()}

        def _apply_card_outfits():
            sheet_box[0] = apply_cast_review_outfits(sheet_box[0], _collect_outfits())

        def _finish(action: str):
            if action == "continue":
                _apply_card_outfits()
            result[0] = (action, sheet_box[0])
            dlg.open = False
            page.update()
            done.set()

        def _outfit_field(name: str, value: str) -> ft.TextField:
            prev = outfit_fields.get(name)
            if prev is not None and prev.value is not None:
                value = prev.value
            tf = ft.TextField(
                value=value or "", multiline=True, min_lines=2, max_lines=3,
                text_size=12, bgcolor=C["surface"], border_color=C["border"],
                color=C["text1"], border_radius=9, autofocus=True,
                content_padding=po(left=10, right=10, top=8, bottom=8),
            )
            outfit_fields[name] = tf
            return tf

        def _outfit_value(name: str, fallback: str) -> str:
            """ค่าที่ผู้ใช้พิมพ์ไว้ (ถ้าเคยแก้การ์ดนี้) ไม่งั้นใช้ค่าจากชีต"""
            tf = outfit_fields.get(name)
            if tf is not None and tf.value is not None:
                return (tf.value or "").strip()
            return fallback

        def _start_edit(name: str):
            editing[0] = name
            _render()

        def _stop_edit():
            editing[0] = ""
            _render()

        def _render():
            rows = outline_cast_review_rows(sheet_box[0])
            n = len(rows)
            warn_n = sum(1 for r in rows if not r.get("outfit_ok"))
            # จำนวนตัวละครโผล่ที่เดียว: subtitle ของหัวไดอะล็อก
            _sub.value = f"Outline Step 1 · ตัวละคร {n} คน" + (
                f" · ชุดอาจไม่สมบูรณ์ {warn_n} คน" if warn_n else "")
            _sub.color = C["amber"] if warn_n else C["text2"]
            cards = []
            if mode[0] == "edit":
                edit_tf.value = sheet_box[0]
                body.controls = [
                    ft.Text("Character Sheet", size=13, weight=ft.FontWeight.W_600,
                            color=C["text1"]),
                    ft.Text("Add, remove, rename or edit outfits, then save — one line per character",
                            size=12, color=C["text2"]),
                    ft.Container(content=edit_tf, width=_dlg_w),
                ]
                dlg.actions = [
                    ft.OutlinedButton(
                        content=ft.Text("Cancel", size=13, color=C["text1"]),
                        height=40, tooltip="Discard changes and return",
                        style=ft.ButtonStyle(shape=_btn_shape, padding=_pad,
                                             side=ft.BorderSide(1, C["border2"])),
                        on_click=lambda e: (mode.__setitem__(0, "view"), _render()),
                    ),
                    ft.FilledButton(
                        content=ft.Text("Save", size=13, color="white",
                                        weight=ft.FontWeight.W_600),
                        height=40, bgcolor=C["purple"], tooltip="Save roster and review again",
                        style=ft.ButtonStyle(shape=_btn_shape, padding=_pad),
                        on_click=_save_edit,
                    ),
                ]
            else:
                keep = {r.get("name") for r in rows}
                for k in list(outfit_fields):
                    if k not in keep:
                        outfit_fields.pop(k, None)
                for r in rows:
                    meta = "   ·   ".join(
                        x for x in (r.get("gender") or "", r.get("age") or "",
                                    r.get("nationality") or "") if x)
                    ok = bool(r.get("outfit_ok"))
                    name = r.get("name") or "?"
                    mark = "✓" if ok else "⚠"
                    _sheet_outfit = r.get("outfit_text") or r.get("outfit_line") or ""
                    _is_editing = editing[0] == name
                    _shown = _outfit_value(name, _sheet_outfit)
                    # แถวหัวการ์ด: ชื่อซ้าย ปุ่มแก้ไขของตัวละครคนนี้ขวา
                    head_row = ft.Row([
                        ft.Row([
                            ft.Text(mark, size=13,
                                    color=C["purple"] if ok else C["amber"]),
                            ft.Text(name, size=14, weight=ft.FontWeight.W_600,
                                    color=C["text1"]),
                        ], spacing=8, tight=True),
                        ft.TextButton(
                            content=ft.Text("Done" if _is_editing else "Edit", size=12,
                                            color=C["purple"]),
                            height=30, tooltip="Edit this character's opening outfit",
                            style=ft.ButtonStyle(
                                shape=_btn_shape,
                                padding=ft.Padding.symmetric(horizontal=10, vertical=2)),
                            on_click=(lambda e: _stop_edit()) if _is_editing
                            else (lambda e, _n=name: _start_edit(_n)),
                        ),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER)
                    bits = [
                        head_row,
                        ft.Text(meta or "—", size=11, color=C["text3"]),
                        ft.Container(height=6),
                        ft.Text("Opening outfit", size=11, color=C["text3"]),
                    ]
                    if _is_editing:
                        bits.append(_outfit_field(name, _shown))
                    elif _shown:
                        # อ่านอย่างเดียว: ข้อความธรรมดา ไม่ใช่กล่องกรอก
                        bits.append(ft.Text(_shown, size=13, color=C["text1"],
                                            selectable=True, max_lines=3,
                                            overflow=ft.TextOverflow.ELLIPSIS))
                    else:
                        bits.append(ft.Text("⚠ Missing or incomplete",
                                            size=12, color=C["amber"]))
                        bits.append(ft.Container(
                            content=ft.TextButton(
                                content=ft.Text("Add outfit", size=12, color=C["purple"]),
                                height=30,
                                style=ft.ButtonStyle(
                                    shape=_btn_shape,
                                    padding=ft.Padding.symmetric(horizontal=10, vertical=2)),
                                on_click=lambda e, _n=name: _start_edit(_n),
                            ),
                            padding=po(top=4), alignment=ft.Alignment.CENTER_LEFT,
                        ))
                    cards.append(ft.Container(
                        content=ft.Column(bits, spacing=3, tight=True),
                        bgcolor=C["panel"], padding=16, border_radius=12,
                        border=ft.Border.all(1, C["amber"] if not ok else C["border"]),
                    ))
                # 1-4 คนให้ไดอะล็อกสูงตามเนื้อหา เกินกว่านั้นให้เฉพาะลิสต์การ์ดเลื่อน ปุ่มท้ายยังเห็นอยู่
                list_col = ft.Column(cards, spacing=10, tight=True,
                                     scroll=ft.ScrollMode.AUTO if n > 4 else None)
                if n > 4:
                    list_col = ft.Container(content=list_col, height=440)
                body.controls = [
                    list_col if cards else ft.Text("No characters found in this sheet",
                                                   size=12, color=C["text2"]),
                ]
                dlg.actions = [
                    # ซ้าย: สร้างใหม่ — ขวา: แก้ไขทั้งหมด + ดำเนินการต่อ (primary)
                    ft.Row([
                        ft.OutlinedButton(
                            content=ft.Text("Regenerate", size=13, color=C["text1"]),
                            height=40, tooltip="Generate this step again",
                            style=ft.ButtonStyle(shape=_btn_shape, padding=_pad,
                                                 side=ft.BorderSide(1, C["border2"])),
                            on_click=lambda e: _finish("regenerate"),
                        ),
                        ft.Row([
                            ft.TextButton(
                                content=ft.Text("Edit all", size=13, color=C["text2"]),
                                height=40, tooltip="Edit the character sheet",
                                style=ft.ButtonStyle(shape=_btn_shape, padding=_pad),
                                on_click=lambda e: (
                                    _apply_card_outfits(),
                                    editing.__setitem__(0, ""),
                                    mode.__setitem__(0, "edit"),
                                    _render(),
                                ),
                            ),
                            ft.FilledButton(
                                content=ft.Text("Continue", size=13, color="white",
                                                weight=ft.FontWeight.W_600),
                                height=40, bgcolor=C["purple"],
                                tooltip="Confirm roster and continue",
                                style=ft.ButtonStyle(shape=_btn_shape, padding=_pad),
                                on_click=lambda e: _finish("continue"),
                            ),
                        ], spacing=8, tight=True),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, width=_dlg_w),
                ]
            page.update()

        def _save_edit(e):
            sheet_box[0] = (edit_tf.value or "").strip() or sheet_box[0]
            outfit_fields.clear()
            editing[0] = ""
            mode[0] = "view"
            _render()

        # subtitle เป็นที่เดียวที่บอกจำนวนตัวละคร _render() เขียนทับทุกครั้งที่ชีตเปลี่ยน
        _sub = ft.Text("Outline Step 1", size=12, color=C["text2"])
        dlg = ft.AlertDialog(
            modal=True, bgcolor=C["surface"],
            shape=ft.RoundedRectangleBorder(radius=18),
            title=ft.Row([
                ft.Container(
                    content=ft.Text("✓", size=16, text_align=ft.TextAlign.CENTER,
                                    color=C["purple"]),
                    width=38, height=38, bgcolor=C["purple_lt"],
                    border_radius=19, alignment=ft.Alignment.CENTER,
                    border=ft.Border.all(1, C["border"]),
                ),
                ft.Column([
                    ft.Text("Review Characters", size=21,
                            weight=ft.FontWeight.W_600, color=C["text1"]),
                    _sub,
                ], spacing=1, tight=True),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            title_padding=po(left=24, right=24, top=20, bottom=10),
            content=ft.Container(width=_dlg_w, content=body),
            content_padding=po(left=24, right=24, top=4, bottom=8),
            actions=[],
            actions_alignment=ft.MainAxisAlignment.END,
            actions_padding=po(left=24, right=24, top=16, bottom=20),
        )
        _cast_review_pending[0] = (done, result, dlg)
        page.overlay.append(dlg)
        dlg.open = True
        _render()
        _outline_timer_stop("Waiting — review characters and outfits")
        page.update()
        await done.wait()
        dlg.open = False
        page.update()
        await asyncio.sleep(0.05)
        if dlg in page.overlay:
            page.overlay.remove(dlg)
        _cast_review_pending[0] = None
        act, sheet = result[0] if isinstance(result[0], tuple) else (None, char_desc)
        return act, sheet or char_desc or ""

    _outline_review_regen_btn = ft.TextButton(
        "🔄 Regenerate",
        on_click=lambda e: asyncio.create_task(_outline_review_refresh()),
        style=ft.ButtonStyle(color=C["text2"]),
        visible=True,
    )

    # ปุ่มท้าย Review Outline — ยกออกมาจาก actions=[...] เพื่อให้ _set_outline_phase สั่งเปิด/ปิดได้
    # ป้ายบอกสิ่งที่ปุ่มทำจริง: "Continue to Image Prompts…" เปิดหน้าตั้งค่า Fill ไม่ได้เริ่ม Fill ทันที
    _outline_save_btn = ft.TextButton(
        "Save Outline", on_click=lambda e: _outline_review_confirm_only(e),
        style=ft.ButtonStyle(color=C["text2"]),
    )
    _outline_continue_btn = ft.FilledButton(
        "Continue to Image Prompts…", on_click=lambda e: _outline_review_confirm(e),
        bgcolor=C["green"],
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
    )
    # Runs B from the A1/A2 the user has just reviewed/edited (Character + Events). Shown once A
    # finishes (REVIEW_A) and stays after B (READY) so shots can be re-created after further edits.
    _outline_create_btn = ft.FilledButton(
        "Create shot", on_click=lambda e: asyncio.create_task(_outline_create_shots()),
        bgcolor=C["accent"], visible=False,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
        tooltip="Design shots from the reviewed character and events",
    )
    _outline_phase: list[str] = ["IDLE"]

    # B-ACTION-CONTINUITY-DEBUG-REFRESH-A1: explicit lifecycle of the CURRENT Normal vNext outline
    # run's B stage — NOT_STARTED / RUNNING / FINISHED_SUCCESS / FINISHED_FAILED. Deliberately not
    # inferred from shots_data / status text / chat existence. _b_final_n keeps the user-confirmed
    # final image count so Refresh B reuses it without asking again.
    _b_run_state: list[str] = ["NOT_STARTED"]
    _b_final_n: list[int] = [0]
    _outline_refresh_b_btn = ft.TextButton(
        "🔄 Refresh B",
        on_click=lambda e: asyncio.create_task(_outline_refresh_b()),
        style=ft.ButtonStyle(color=C["amber"]),
        tooltip="Debug: rerun ONLY Shot List from the current Story Bible / Beat Sheet and the same image count",
        visible=False,
    )

    def _outline_refresh_b_visibility() -> None:
        """Debug-only capability: visible iff Debug logging is ON, B has terminally finished (success
        or failure) for this outline run, nothing is running, and the Normal vNext upstream results
        still exist. Pure property mutation — never triggers generation."""
        # Hidden for good: the Create shot/pages button now covers "rerun B" (with a count dialog),
        # and it stays available after B finishes.
        _outline_refresh_b_btn.visible = False

    def _set_outline_phase(phase: str, status: str = "") -> None:
        """แหล่งเดียวที่เปลี่ยนสถานะหน้า Review — ปุ่มทุกตัวและวงหมุนเดินตามตารางเดียวกัน

        status ที่ส่งมาจะถูกตั้งผ่าน _outline_timer_stop เสมอ (หยุด tick ก่อน) ไม่ตั้ง .value ตรง ๆ
        ไม่งั้นข้อความจะถูก tick loop เขียนทับในวินาทีถัดไป"""
        _outline_phase[0] = (phase or "IDLE").upper()
        caps = outline_phase_caps(_outline_phase[0])
        _outline_review_loading.visible = caps["loading"]
        _outline_review_regen_btn.visible = caps["regen"]
        _outline_save_btn.disabled = not caps["save"]
        _outline_continue_btn.disabled = not caps["fill"]
        # Continue to Fill only exists once B has produced shots (READY); before that the user is
        # still reviewing A (or B is running) and the button would be misleading.
        _outline_continue_btn.visible = _outline_phase[0] == "READY"
        _has_a = bool(_outline_vnext_a1[0]) and bool(_outline_vnext_a2[0])
        _outline_create_btn.content = "Create pages" if _manga_vnext_mode[0] else "Create shot"
        _outline_create_btn.visible = (
            _outline_phase[0] in ("REVIEW_A", "READY")
            or (_outline_phase[0] == "FAILED" and _has_a)
        )
        _outline_create_btn.disabled = not (caps["create"] or (_outline_phase[0] == "FAILED" and _has_a))
        if bool(shots_data) and all(isinstance(_sp, dict) and _sp.get("_static_pose") for _sp in shots_data):
            _outline_create_btn.visible = False         # Static Pose Fill shots are complete: B and Fill have nothing to add
            _outline_continue_btn.visible = False
        # Events (A2 micro beats) are editable only while the user can still act on them.
        _outline_events_tf.read_only = not (
            _outline_phase[0] in ("REVIEW_A", "READY", "FAILED") and _has_a
        )
        _outline_refresh_b_visibility()
        for _tf in (_outline_review_fields
                    + _outline_review_story_fields + _outline_review_narration_fields
                    + _outline_review_speech_fields
                    + [_ptf for _plist in _outline_review_panel_fields for _ptf in _plist]):
            _tf.disabled = not caps["fields"]
        if status:
            _outline_timer_stop(status)

    async def _alert_pass_a_failed(detail: str):
        """Pass A produced nothing usable — stop before pass B, hide Editor, leave Regenerate."""
        _set_outline_phase("FAILED", "Story Bible failed — click Regenerate")
        llm_busy[0] = False
        llm_prog.visible = False
        gen_shots_btn.disabled = False
        fill_prompts_btn.disabled = False
        status_lbl.value = "❌ Outline pass A failed"
        await _llm_log(f"❌ Outline pass A failed: {detail}")
        done = asyncio.Event()
        _pass_a_fail_pending[0] = done

        def _ok(_e):
            page.pop_dialog()
            done.set()
            page.update()

        dlg = ft.AlertDialog(
            modal=True, bgcolor=C["surface"],
            title=ft.Text("Outline pass A failed", size=15, weight=ft.FontWeight.W_700, color=C["text1"]),
            content=ft.Container(
                width=440,
                content=ft.Text(
                    f"{detail}\n\nThe shot pass was not started. Click Regenerate to try again.",
                    size=13, color=C["text2"],
                ),
            ),
            actions=[
                ft.FilledButton(
                    content=ft.Text("OK", size=13, color="white", weight=ft.FontWeight.W_600),
                    bgcolor=C["amber"],
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
                    on_click=_ok,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            actions_padding=po(left=20, right=20, top=4, bottom=16),
        )
        page.show_dialog(dlg)
        page.update()
        await done.wait()
        _pass_a_fail_pending[0] = None

    _outline_view_narrow: list[bool] = [False]
    _outline_view_speech: list[bool] = [True]
    _outline_review_col = ft.Column([], spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)

    # ── Outline Review: Character / Env tabs ────────────────────────────
    _outline_char_tf = ft.TextField(
        value=char_field.value, multiline=True, min_lines=16, max_lines=30,
        text_size=12, bgcolor=C["surface"], border_color=C["border"], color=C["text1"],
        expand=True,
    )
    _outline_props_tf = ft.TextField(
        value=props_field.value, multiline=True, min_lines=16, max_lines=30,
        text_size=12, bgcolor=C["panel"], border_color=C["border"], color=C["text1"],
        expand=True,
        # ENGLISH ONLY และเป็นข้อมูลที่ขั้นวาดภาพใช้ตรง ๆ — แก้ด้วยมือแล้วมัก
        # ทำให้ผิดรูปแบบ ให้แก้ผ่าน Outline Editor / Chat แทน
        read_only=True,
    )
    # Pass A สร้าง locations (per-node route geography) มาใช้จริงอยู่แล้ว (ส่งเข้า Fill ผ่าน
    # canonical_location) แต่ไม่เคยมีที่ให้ผู้ใช้เห็นค่าจริงเลยสักจุด — โชว์รวมกับ scene ในแท็บ Env
    # เดียวกัน (ผู้ใช้ขอให้รวม ไม่ใช่แยกแท็บ) scene อยู่บนสุด ตามด้วย per-node breakdown
    def _outline_env_tab_text() -> str:
        loc = (_outline_locations[0] or "").strip()
        scene = (scene_field.value or "").strip()
        if not loc:
            return f"— Route Overview —\n{scene}" if scene else ""
        if not scene:
            return f"— Locations —\n{loc}"
        return f"— Route Overview —\n{scene}\n\n— Locations —\n{loc}"

    _outline_env_tf = ft.TextField(
        value=_outline_env_tab_text(), multiline=True, min_lines=16, max_lines=30,
        text_size=12, bgcolor=C["panel"], border_color=C["border"], color=C["text1"],
        expand=True,
        # ENGLISH ONLY และเป็นข้อมูลที่ขั้นวาดภาพใช้ตรง ๆ — แก้ด้วยมือแล้วมัก
        # ทำให้ผิดรูปแบบ ให้แก้ผ่าน Outline Editor / Chat แทน
        read_only=True,
    )
    # Events มาจาก Pass A และถูกล็อกส่งให้ Pass B ไปแล้วตอน dialog นี้เปิด — แก้ที่นี่ไม่ย้อนกลับไป
    # เปลี่ยนช็อตที่สร้างเสร็จแล้ว จึงเป็น read-only กันเข้าใจผิดว่าแก้แล้วมีผล
    _outline_events_tf = ft.TextField(
        value="", multiline=True, min_lines=16, max_lines=30, read_only=True,
        text_size=12, bgcolor=C["panel"], border_color=C["border"], color=C["text1"],
        expand=True,
    )

    def _sync_char_sheet_to_a1():
        """VNEXT-A1-CHARACTER-DESIGN-A2-EDITORIAL-CLEANUP-A1: char_field.value is the user-editable
        Character Sheet the Outline UI shows — once the user edits it, it must become the
        character_sheet Normal/Manga vNext B and Fill actually read from cached A1
        (_outline_vnext_a1), not whatever A1 originally returned. Transport only: never parses or
        rewrites the user's text, and never touches any other A1 field (macro_events/world_sheet/
        locations/important_objects/continuity_notes)."""
        if isinstance(_outline_vnext_a1[0], dict):
            _outline_vnext_a1[0]["character_sheet"] = char_field.value or ""

    def _outline_char_tf_changed(e):
        char_field.value = _outline_char_tf.value
        _sync_char_sheet_to_a1()
        _save_state()

    def _outline_props_tf_changed(e):
        props_field.value = _outline_props_tf.value
        _save_state()

    _outline_char_tf.on_change = _outline_char_tf_changed
    _outline_props_tf.on_change = _outline_props_tf_changed

    # SOURCE-STYLE tab: the story-wide style that image_style == "As Source" sends as [Style].
    # Editable — the text IS _source_style[0] (the value Fill/image generation resolve and the
    # story file saves), so an edit takes effect on the next image with no extra sync step.
    _outline_style_tf = ft.TextField(
        value=_source_style[0] or "", multiline=True, min_lines=16, max_lines=30,
        text_size=12, bgcolor=C["surface"], border_color=C["border"], color=C["text1"],
        expand=True,
        hint_text="Overall visual style used when Style is set to \"As Source\" (English, no characters or events). Empty = no style added.",
    )

    def _outline_style_tf_changed(e):
        _set_source_style(_outline_style_tf.value or "", "user", _a1_source_text())
        _save_state()

    _outline_style_tf.on_change = _outline_style_tf_changed

    def _outline_events_text() -> str:
        # OUTLINE-A1-SOURCE-PRESERVING: a project with the new source_mode/source_blocks (any
        # regenerated project, on the non-manga path) shows exactly those canonical blocks — the
        # SAME representation A2 actually consumes, never a second UI-only rendering of it. A
        # legacy project with no source_blocks yet (not regenerated since this round) keeps
        # showing its old [SPINE]/[COVERAGE] events list untouched.
        _blocks = _outline_source_blocks[0] or []
        if _blocks:
            _mode = (_outline_source_mode[0] or "").strip().upper() or "?"
            # A2's scenario units, grouped under the A1 block they belong to (parent id) — shown
            # directly under that block's own text so the whole chain (A1 source → A2 shots) reads
            # top to bottom in one place.
            _units_by_parent: dict[str, list[dict]] = {}
            for u in (_outline_scenario_script[0] or []):
                _units_by_parent.setdefault(str(u.get("parent", "")), []).append(u)

            def _block_text(b: dict) -> str:
                _id = b.get("id", "?")
                _txt = f"[{_id}]\n{b.get('source_text', '')}"
                _children = _units_by_parent.get(str(_id)) or []
                for u in _children:
                    _txt += (
                        f"\n  [{u.get('id', '?')}] {u.get('content', '')}"
                        + (f"  (shot_intent: {u.get('shot_intent')})" if u.get("shot_intent") else "")
                    )
                return _txt

            body = f"MODE: {_mode}\n\n" + "\n\n".join(_block_text(b) for b in _blocks)
        else:
            # EVENTS-TAB-A1-A2-INDENT: vNext has no source_blocks of its own (that representation is
            # legacy-only, see the comment above) — for a vNext project, show the SAME structure B
            # actually reads (A1 macro_events, each followed by its own A2 micro/comic beats indented
            # beneath it), matching _build_b_vnext_prompt/_build_b_manga_vnext_prompt's own
            # "STORY MATERIAL" block exactly, rather than the flat numbered micro-beat-only list this
            # used to show (which dropped the macro event text and any indentation entirely).
            _a1 = _outline_vnext_a1[0] or {}
            _macro = _a1.get("macro_events") or []
            _beat_src = _outline_vnext_a2[0] or {}
            _beats = _beat_src.get("micro_beats") or []
            if _macro:
                _beats_by_parent: dict = {}
                for _b in _beats:
                    _beats_by_parent.setdefault(_b.get("parent"), []).append(_b)
                # Editable view: the plain lines are macro-event context (edits there are ignored);
                # each <E1>…</E1> row is one micro beat you can rewrite, delete, add or reorder.
                # The tag name is the macro event it belongs to; the row position is its identity.
                _cap_a1 = _a1_chat_capture[0] or {}
                _rt = str(((_cap_a1.get("routing") or {}).get("route")) or _cap_a1.get("route") or "").lower()
                _route_label = {"static": "Static Image Expansion", "narrative": "Narrative Story Development", "novel": "Narrative Story Development"}.get(_rt, "")
                _lines = ["MACRO EVENTS (context, not editable) with their micro beats — "
                          "edit only the tagged rows below:"]
                if _route_label:
                    _lines.append(f"Story Bible route: {_route_label}")
                _lines.append("")
                for _e in _macro:
                    _eid = _e.get("id")
                    _lines.append(f"{_e.get('content', '')}")
                    for _b in _beats_by_parent.get(_eid, []):
                        _lines.append(f"  <{_eid}>{_b.get('content', '')}</{_eid}>")
                    _lines.append("")
                body = "\n".join(_lines).rstrip()
            else:
                evs = [str(e).strip() for e in (_outline_events[0] or []) if str(e).strip()]
                if not evs:
                    body = "(no events — run Outline to generate them)"
                else:
                    body = "\n".join(f"{i}. {e}" for i, e in enumerate(evs, 1))
        arc = (_outline_clothing_arc[0] or "").strip()
        if arc:
            body += "\n\n--- Clothing Arc ---\n" + arc
        phys = (_outline_physical_state[0] or "").strip()
        if phys:
            body += "\n\n--- Physical State Plan ---\n" + phys
        return body

    def _outline_apply_events_edit() -> bool:
        """Write the Events tab back into A2's micro_beats (validated). On a problem: says why in
        the dialog header + status line and returns False so the caller stops without side effects."""
        a1, a2 = _outline_vnext_a1[0], _outline_vnext_a2[0]
        if not (isinstance(a1, dict) and isinstance(a2, dict)):
            return True
        macro_ids = [str(e.get("id")) for e in (a1.get("macro_events") or []) if isinstance(e, dict)]
        beats, err = parse_events_edit(_outline_events_tf.value or "", macro_ids)
        if beats is None:
            _outline_review_status.value = f"⚠ Events: {err}"
            status_lbl.value = f"⚠ Events: {err}"
            page.update()
            return False
        a2["micro_beats"] = beats
        _outline_events[0] = [f"{m['id']} ({m['parent']}): {m['content']}" for m in beats]
        _last_a2_micro_count[0] = len(beats)
        return True

    def _outline_sync_char_env_tabs():
        _outline_char_tf.value = char_field.value
        _outline_env_tf.value = _outline_env_tab_text()
        _outline_props_tf.value = props_field.value
        _outline_style_tf.value = _source_style[0] or ""
        _outline_events_tf.value = _outline_events_text()

    def _clear_outline_sheets():
        """Wipe character/scene/props once, before a brand-new Pass A.

        Only the Outline Settings (and Groom) Create Outline buttons call this.
        Regen that reuses Pass A, Fill, Check, and Director must not.

        OUTLINE-NEW-RUN-STATE-RESET-A1: a brand-new Outline must not inherit ANY
        Outline-derived result from the previous story — A1/A2/A2-Manga results,
        Events/clothing/physical/location/source state, and the Outline Chat B
        conversation all belong to the run that produced them. Without this, a
        failed new A1/A2 could leave the previous story's stale results (e.g. an
        old character) sitting around for a later Regenerate to reuse, or the
        Outline Chat button could keep continuing a conversation from a story the
        user has already left behind. User inputs/preferences (story text, LLM/
        genre/language selection, Outline Style, Shot Scale, etc.) and shots_data
        (snapshotted separately for Cancel-to-restore) are deliberately untouched
        here — this clears AI-derived Outline state only."""
        char_field.value = ""
        scene_field.value = ""
        props_field.value = ""
        story_summary_field.value = ""
        _story_summary[0] = ""
        _outline_locations[0] = ""
        _outline_scenario_script[0] = []
        _outline_source_mode[0] = ""
        _outline_source_blocks[0] = []
        # 1. Old vNext stage results (Normal + Manga share A1) must never survive into
        # a new Pass A — otherwise a failed new A1/A2 could leave a stale old result
        # available for a later Regenerate (new A1 + old A2, or vice versa).
        _outline_vnext_a1[0] = None
        _outline_vnext_a2[0] = None
        _fill_design_ledger[0] = ""       # FILL-CREATIVE-DESIGN-LEDGER-A1: new story -> new ledger
        _b_run_state[0] = "NOT_STARTED"
        _b_final_n[0] = 0
        # 2. Other Outline-derived story state from the previous run.
        _outline_events[0] = []
        _outline_clothing_arc[0] = ""
        _outline_physical_state[0] = ""
        # 3. A new Outline is a new B conversation — do not let "Ask AI" continue a
        # chat that belongs to a story the user has already left.
        _outline_chat_capture[0] = {}
        _outline_chat_session_id[0] = ""
        try:
            outline_chat_btn.disabled = True
            outline_chat_btn.update()
        except Exception:
            pass
        # REVIEW-OUTLINE-DEBUG-CHAT-TABS-A1: same reasoning as #3 above — A1/A2 debug chat belongs
        # to the run that produced them, never carried into a brand-new story's Outline. Also clear
        # the VISIBLE bubbles/dropdown here, not just the underlying capture dicts — otherwise the
        # old story's chat stays on screen until the new A1/A2/B call finishes (or forever, if the
        # new run fails before reaching that stage).
        try:
            _a1_chat_capture[0] = {}
            _a2_chat_sessions[0] = []
            _a2_chat_selected[0] = 0
            _outline_reseed_a1_chat()
            _outline_refresh_a2_sessions()
            _b_chat_sessions[0] = []
            _b_chat_selected[0] = 0
            _outline_refresh_b_sessions()
        except Exception:
            pass
        # 4. Previous-story AI recommendation / Manga final-page result — derived
        # result state, not the user's own shot-count input.
        _last_a2_micro_count[0] = 0
        _manga_vnext_final_pages[0] = None
        _outline_sync_char_env_tabs()
        try:
            char_field.update()
            scene_field.update()
            story_summary_field.update()
        except Exception:
            pass
        page.update()

    def _outline_review_rebuild_manga():
        """MANGA-REVIEW-CANONICAL-EDIT-A1: Manga vNext's own Review Outline rendering — shows each
        page's panels structurally (panel number, region_pct and the three page-level locks as
        read-only context, one editable TextField per panel bound to panels[i]["desc"] only).
        Never parses/infers anything from text; locks/region_pct/panel numbers are preserved
        untouched and simply redisplayed from shot["_manga_page_spec"], the Manga canonical truth
        Fill Manga reads directly. Normal Review Outline (_outline_review_rebuild's own loop) is a
        separate code path and is not touched by this function existing."""
        for i, s in enumerate(shots_data):
            _spec = s.get("_manga_page_spec") or {}
            _panels = _spec.get("panels") or []
            _panel_fields: list[ft.TextField] = []
            _panel_rows = []
            for p in _panels:
                _desc_tf = ft.TextField(
                    value=p.get("desc", ""),
                    multiline=True, min_lines=2, max_lines=5,
                    text_size=12, bgcolor=C["surface"], border_color=C["border"], color=C["text1"],
                    expand=True,
                )
                _panel_fields.append(_desc_tf)
                _panel_rows.append(ft.Column([
                    ft.Text(f"Panel {p.get('panel')}  ·  region {p.get('region_pct')}",
                            size=11, color=C["text3"]),
                    _desc_tf,
                ], spacing=2, tight=True))
            _outline_review_panel_fields.append(_panel_fields)
            _locks_txt = "  ·  ".join(x for x in [
                f"identity: {_spec.get('identity_lock', '')}".strip(),
                f"location: {_spec.get('location_lock', '')}".strip(),
                (f"objects: {_spec.get('object_lock', '')}".strip() if _spec.get("object_lock") else ""),
            ] if x and not x.endswith(":"))
            _outline_review_col.controls.append(ft.Column([
                ft.Row([
                    ft.Text(f"Page {s.get('shot', i + 1)}", size=11,
                            color=C["accent"], weight=ft.FontWeight.W_600, expand=True),
                ], spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text(_locks_txt or "(no locks recorded)", size=10, color=C["text3"]),
                *_panel_rows,
            ], spacing=4, tight=True))

    def _outline_review_rebuild():
        _outline_review_fields.clear()
        _outline_review_story_fields.clear()
        _outline_review_narration_fields.clear()
        _outline_review_speech_fields.clear()
        _outline_review_panel_fields.clear()
        _outline_review_col.controls.clear()
        if _manga_vnext_mode[0]:
            _outline_review_rebuild_manga()
            _outline_refresh_leak_warning()
            _outline_review_col.update()
            return
        for i, s in enumerate(shots_data):
            # NORMAL-VNEXT-SCHEMA-CLEANUP-A1 §5: native field names displayed/edited directly —
            # shot_desc (was scene_desc), objects (was prop_state), subtitle (was speech_text).
            # The old "expression" edit box is gone — that field is obsolete (no honest native
            # source; B's compact schema never produced it) rather than silently editing a key
            # that no longer means anything.
            # DISPLAY-FALLBACK-A1: a project saved before this schema migration still has the old
            # key names on disk (scene_desc/prop_state/speech_text) and is never auto-upgraded —
            # read the native key first, falling back to the old one so an old project's real
            # content is still visible for review instead of showing blank boxes. Editing and
            # saving always writes back under the native key only (see the Save handler below),
            # so the shot silently upgrades to native shape the moment it's touched.
            tf = ft.TextField(
                value=s.get("shot_desc") or s.get("scene_desc", ""),
                multiline=True, min_lines=3, max_lines=6,
                text_size=12, bgcolor=C["surface"], border_color=C["border"], color=C["text1"],
                expand=True,
            )
            _outline_review_fields.append(tf)

            # The objects box was replaced by story (editable, shown first). `objects` itself is
            # left untouched in shots_data — it is still sent to Fill, just no longer edited here.
            story_tf = ft.TextField(
                value=s.get("story", ""),
                hint_text="story — poetic fusion of the shot_desc blocks (sent to Image Prompt)",
                multiline=True, min_lines=2, max_lines=5,
                text_size=12, bgcolor=C["surface"], border_color=C["border"], color=C["text1"],
                prefix_icon=ft.Icons.AUTO_STORIES_OUTLINED,
                expand=True,
            )
            _outline_review_story_fields.append(story_tf)

            narration_tf = ft.TextField(
                value=s.get("narration", ""),
                hint_text="(no narration)",
                multiline=True, min_lines=1, max_lines=3,
                text_size=11, bgcolor=C["surface"], border_color=C["border"], color=C["text2"],
                prefix_icon=ft.Icons.MENU_BOOK_ROUNDED,
                expand=True,
            )
            _outline_review_narration_fields.append(narration_tf)

            _speech_char = s.get("speech_char", "").strip()
            speech_tf = ft.TextField(
                value=s.get("subtitle") or s.get("speech_text", ""),
                hint_text="(no dialogue)",
                prefix=f"{_speech_char}: " if _speech_char else None,
                multiline=True, min_lines=1, max_lines=2,
                text_size=11, bgcolor=C["surface"], border_color=C["border"], color=C["text2"],
                prefix_icon=ft.Icons.CHAT_BUBBLE_OUTLINE_ROUNDED if not _speech_char else None,
                expand=True,
            )
            _outline_review_speech_fields.append(speech_tf)

            narration_tf.visible = not _outline_view_narrow[0]
            speech_tf.visible = _outline_view_speech[0] and not _outline_view_narrow[0]
            _btn_style_sm = ft.ButtonStyle(
                color=C["text3"], padding=ft.Padding(4, 0, 4, 0),
                text_style=ft.TextStyle(size=11),
            )
            _btn_style_del = ft.ButtonStyle(
                color="#e57373", padding=ft.Padding(4, 0, 4, 0),
                text_style=ft.TextStyle(size=11),
            )
            _outline_review_col.controls.append(ft.Column([
                ft.Row([
                    ft.Text(f"Shot {s.get('shot', i+1)}", size=11,
                            color=C["accent"], weight=ft.FontWeight.W_600, expand=True),
                    ft.TextButton("+ above", style=_btn_style_sm,
                                  on_click=lambda e, _i=i: _do_insert_shot(_i, True)),
                    ft.Text("·", size=10, color=C["text3"]),
                    ft.TextButton("+ below", style=_btn_style_sm,
                                  on_click=lambda e, _i=i: _do_insert_shot(_i, False)),
                    ft.Text("·", size=10, color=C["text3"]),
                    ft.TextButton("delete", style=_btn_style_del,
                                  on_click=lambda e, _i=i: _do_delete_shot(_i)),
                ], spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                story_tf,
                tf,
                narration_tf,
                speech_tf,
            ], spacing=4, tight=True))
        _apply_outline_view()
        _outline_refresh_leak_warning()
        _outline_review_col.update()

    def _pass_a_is_complete() -> bool:
        """รอบ A ให้ผลครบพอที่รอบ B จะทำงานต่อได้เอง — ใช้เกณฑ์เดียวกับที่ _do_generate_outline
        ใช้ตัดสินว่ารอบ A ล้มเหลว (ต้องมี character sheet และมี events หรือ source_blocks
        อย่างใดอย่างหนึ่ง — เส้นทางใหม่ไม่มี events แล้ว ใช้ source_blocks แทน)"""
        return (
            bool(_outline_vnext_a1[0])
            or (bool(_outline_events[0]) or bool(_outline_source_blocks[0]))
        ) and bool((char_field.value or "").strip())

    async def _outline_review_refresh(e=None):
        # รอบ A สำเร็จแล้ว (มี events + character sheet) แต่รอบ B ไม่ออก — ยิงใหม่เฉพาะรอบ B
        # ไม่ต้องเสีย token อ่านเรื่องทั้งเรื่องซ้ำ และผู้ใช้ไม่เสีย character/events ที่ตรวจไปแล้ว
        if _pass_a_is_complete():
            await _llm_log("🔄 Regenerate: ใช้ผล Story Bible เดิม ทำเฉพาะ Shot List (shots)")
            await _outline_generate_dispatch(ask_shot_count=False, reuse_pass_a=True)
            return
        # ถามจำนวนช็อตหลังรอบ A เหมือน generate ใหม่ — ถ้ารอถามหลังจบรอบ B จะเสีย token ช็อตไปแล้ว
        await _outline_generate_dispatch(ask_shot_count=True)

    def _outline_review_apply_edits():
        # MANGA-REVIEW-CANONICAL-EDIT-A1: when Manga vNext is active, _outline_review_rebuild built
        # the panel-structural UI (_outline_review_rebuild_manga) instead of the flat legacy fields
        # above — _outline_review_fields/_prop_fields/_narration_fields/_speech_fields are empty in
        # that case (rebuild never populated them), so falling into the Normal loop below would
        # silently no-op. Authority direction must stay _manga_page_spec -> display adapter, never
        # the reverse, so edits are written into panels[i]["desc"] only (panel/region_pct/
        # identity_lock/location_lock/object_lock left byte-for-byte untouched), then the flat
        # DISPLAY fields (scene_desc/visible_people/narration/speech_text/etc., read by every other
        # UI surface that still expects the legacy flat shape) are regenerated FROM the edited spec
        # via manga_vnext_page_to_legacy_dict — never written to directly.
        if _manga_vnext_mode[0]:
            for i, _panel_fields in enumerate(_outline_review_panel_fields):
                if i >= len(shots_data):
                    continue
                _spec = shots_data[i].get("_manga_page_spec") or {}
                _panels = _spec.get("panels") or []
                for j, tf in enumerate(_panel_fields):
                    if j < len(_panels):
                        _panels[j]["desc"] = tf.value.strip()
                shots_data[i]["_manga_page_spec"] = _spec
                _refreshed = manga_vnext_page_to_legacy_dict(shots_data[i])
                shots_data[i].update(_refreshed)
            return
        # REVIEW-OLD-SHOT-UPGRADE-A1: writing the native key alone leaves a stale legacy alias
        # (scene_desc/prop_state/speech_text) sitting on an old pre-migration shot next to the
        # fresh native one — shared display/Regen code reads native-first, so it would keep
        # preferring the just-edited value today, but the stale alias is still wrong data at rest
        # and can win again anywhere that hasn't been updated to native-first reads. Pop the legacy
        # alias for exactly the field just touched, on this shot only — never a bulk migration of
        # untouched shots/fields (clothing_state/place/etc. are left alone; this dialog never
        # edited them). Manga branch above is untouched — its own authority stays _manga_page_spec
        # -> display adapter, never native/legacy flat-field writes.
        for i, tf in enumerate(_outline_review_fields):
            if i < len(shots_data):
                shots_data[i]["shot_desc"] = tf.value.strip()
                shots_data[i].pop("scene_desc", None)
        for i, tf in enumerate(_outline_review_story_fields):
            if i < len(shots_data):
                shots_data[i]["story"] = tf.value.strip()
        for i, tf in enumerate(_outline_review_narration_fields):
            if i < len(shots_data):
                shots_data[i]["narration"] = tf.value.strip()
        for i, tf in enumerate(_outline_review_speech_fields):
            if i < len(shots_data):
                shots_data[i]["subtitle"] = tf.value.strip()
                shots_data[i].pop("speech_text", None)

    def _do_insert_shot(idx: int, above: bool):
        _outline_review_apply_edits()
        new_shot = {"shot": 0, "uses": [], "visible_people": "", "location": "",
                    "shot_desc": "", "story": "", "clothing": "", "objects": "", "camera": "", "lighting": "",
                    "narration": "", "speech_char": "", "subtitle": "", "prompt": ""}
        shots_data.insert(idx if above else idx + 1, new_shot)
        for j, s in enumerate(shots_data):
            s["shot"] = j + 1
            # ช็อตที่แทรกเองเดิมไม่มี _dir_scale_category เลย พอถึงตอนสร้างภาพ
            # shot.get("_dir_scale_category","medium") จะ fallback เป็น medium ทั้งที่ preset อาจเป็น
            # epic — ภาพเลยไม่กว้างตามที่ตั้งไว้
            if not s.get("_dir_scale_category"):
                s["_dir_scale_category"] = _assign_shot_scale(j, len(shots_data), _shot_scale_preset[0])
        _outline_review_rebuild()
        page.update()

    def _do_delete_shot(idx: int):
        shot_num = shots_data[idx].get("shot", idx + 1)

        def _confirm(e):
            page.pop_dialog()
            shots_data.pop(idx)
            for j, s in enumerate(shots_data):
                s["shot"] = j + 1
            _outline_review_rebuild()
            page.update()

        def _cancel(e):
            page.pop_dialog()

        _confirm_dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Delete Shot {shot_num}?", size=14),
            content=ft.Text("This cannot be undone.", size=12, color=C["text2"]),
            actions=[
                ft.TextButton("Cancel", on_click=_cancel,
                              style=ft.ButtonStyle(color=C["text2"])),
                ft.TextButton("Delete", on_click=_confirm,
                              style=ft.ButtonStyle(color="#e57373")),
            ],
        )
        page.show_dialog(_confirm_dlg)
        page.update()

    async def _continue_to_fill():
        """ปิด Review แล้วเปิดหน้าตั้งค่า Fill ตัวเดียวกับปุ่ม Fill Prompts ปกติ

        outline ถูกรับและบันทึกไปแล้วก่อนถึงจุดนี้ กด Cancel ในหน้า Fill จึงไม่ย้อน outline กลับ
        หน่วงสั้น ๆ ก่อนเปิด เพราะ dialog ตัวเก่ายังเล่น dismiss animation อยู่ (บั๊กเดียวกับ Raw/Merge)"""
        await asyncio.sleep(_DIALOG_SWAP_DELAY)
        await do_fill_prompts(None)

    def _outline_review_confirm(e):
        # ปุ่มที่ disabled ไว้ยังถูกยิงได้จากจังหวะ event ของ Flet — กันซ้ำที่ handler เสมอ
        if not can_continue_to_fill(_outline_phase[0], len(shots_data), llm_busy[0]):
            status_lbl.value = "⚠ Outline is not ready yet — wait for it to finish"
            page.update()
            return
        _outline_review_apply_edits()
        _close_dialog(outline_review_dialog)
        rebuild_shots_col()
        _save_state()
        page.update()
        asyncio.create_task(_continue_to_fill())

    def _outline_review_confirm_only(e):
        if _outline_phase[0] == "REVIEW_A":
            # No shots yet — saving here keeps the reviewed A1 (character) + A2 (events) only.
            if not _outline_apply_events_edit():
                return
            _sync_char_sheet_to_a1()
            _save_state()
            _close_dialog(outline_review_dialog)
            page.update()
            return
        if not outline_phase_caps(_outline_phase[0])["save"] or not shots_data:
            status_lbl.value = "⚠ Outline is not ready yet — nothing to save"
            page.update()
            return
        _outline_review_apply_edits()
        _close_dialog(outline_review_dialog)
        rebuild_shots_col()
        _save_state()
        page.update()

    def _outline_review_cancel(e):
        # ถ้ากำลังรอ user เลือกจำนวนช็อตอยู่ (_ask_shot_count) ต้องปลุก event นี้ด้วย ไม่งั้น coroutine
        # ที่ await done.wait() จะค้างรอปุ่มที่ไม่มีทางถูกกดอีกแล้วตลอดไป (dialog ปิดไปแล้ว)
        if _ask_shot_pending[0] is not None:
            _done, _result, _dlg = _ask_shot_pending[0]
            _result[0] = None
            _dlg.open = False
            _done.set()
        if _pass_a_fail_pending[0] is not None:
            page.pop_dialog()
            _pass_a_fail_pending[0].set()
            _pass_a_fail_pending[0] = None
        if _audit_gate_pending[0] is not None:
            _done, _result, _dlg = _audit_gate_pending[0]
            _result[0] = None
            _dlg.open = False
            _done.set()
            _audit_gate_pending[0] = None
        if _cast_review_pending[0] is not None:
            _done, _result, _dlg = _cast_review_pending[0]
            _result[0] = (None, _result[0][1] if isinstance(_result[0], tuple) else "")
            _dlg.open = False
            _done.set()
            _cast_review_pending[0] = None
        if _outline_review_is_new[0]:
            # คืนค่า shots เดิมก่อนหน้าที่ generate ทับไปแล้ว
            shots_data.clear()
            shots_data.extend(dict(s) for s in _outline_shots_backup)
        _set_outline_phase("IDLE", "")
        _close_dialog(outline_review_dialog)
        rebuild_shots_col()
        page.update()

    def _apply_outline_view():
        narrow = _outline_view_narrow[0]
        speech = _outline_view_speech[0]
        for tf in _outline_review_narration_fields:
            tf.visible = not narrow
        for tf in _outline_review_speech_fields:
            tf.visible = speech and not narrow

    def _outline_toggle_narrow(e):
        _outline_view_narrow[0] = e.control.value
        _apply_outline_view()
        page.update()

    def _outline_toggle_speech(e):
        _outline_view_speech[0] = e.control.value
        _apply_outline_view()
        page.update()

    _outline_narrow_cb = ft.Checkbox(label="Scene only", value=False,
                                     on_change=_outline_toggle_narrow,
                                     label_style=ft.TextStyle(size=11, color=C["text2"]))

    # ซ่อนถาวร — ช่อง "Additional instructions" ไม่ได้ใช้จริง แต่ยังคง _outline_review_req ไว้ในต้นไม้
    # เพราะมีอีก 3 จุดอ่าน/เขียนค่ามัน (_save_state, _clear_extra_req_fields, _do_generate_outline)
    # — ลบ widget ทิ้งจะพังทั้งหมด ค่าที่อ่านได้จะเป็น "" ตามที่ควรเป็น
    _outline_req_section = ft.Column([
        ft.Text("Additional instructions (optional)", size=11,
                color=C["text2"], weight=ft.FontWeight.W_600),
        ft.Container(content=_outline_review_req, expand=True),
    ], spacing=4, visible=False)

    # โผล่เฉพาะตอน detect_sheet_field_leaked_as_name() เจอปัญหา (Outfit ฯลฯ หลุดเป็นชื่อคน) —
    # ปุ่มคู่นี้แยกจาก _outline_review_regen_btn ("🔄 Regenerate" ปกติ) เพราะให้เลือกได้ว่าจะรัน
    # เฉพาะ Pass A (Redo Info) หรือ Pass B (Build Shots) แทนที่จะรันทั้งคู่เสมอ
    _outline_leak_redo_info_btn = ft.TextButton(
        "Redo Info",
        on_click=lambda e: asyncio.create_task(
            _outline_generate_dispatch(ask_shot_count=False, reuse_pass_a=False)),
        style=ft.ButtonStyle(color=C["amber"]),
        tooltip="Re-run Story Bible (character/story) to fix the character sheet",
    )
    _outline_leak_build_shots_btn = ft.TextButton(
        "Build Shots",
        on_click=lambda e: asyncio.create_task(
            _outline_generate_dispatch(ask_shot_count=False, reuse_pass_a=True)),
        style=ft.ButtonStyle(color=C["amber"]),
        tooltip="Keep current character sheet, regenerate final sequence + shot details",
    )
    _outline_regen_row = ft.Row(
        [_outline_leak_redo_info_btn, _outline_leak_build_shots_btn],
        spacing=4, visible=False,
    )
    _outline_leak_warning_banner = ft.Container(
        content=ft.Text(
            "⚠ Character sheet issue may have carried into these shots.",
            size=12, color=C["amber"], weight=ft.FontWeight.W_600,
        ),
        padding=po(left=0, right=0, top=0, bottom=6),
        visible=False,
    )

    def _outline_refresh_leak_warning():
        """เช็คหลังทุกครั้งที่ Character tab อัปเดต — โผล่ banner + ปุ่มกู้ ถ้าเจอ field หลุดเป็นชื่อ"""
        leaked = detect_sheet_field_leaked_as_name(_outline_char_tf.value or "")
        _outline_leak_warning_banner.visible = bool(leaked)
        if leaked:
            _outline_leak_warning_banner.content.value = (
                f"⚠ Character sheet issue may have carried into these shots — "
                f"\"{leaked}\" was read as a fake character."
            )
        _outline_regen_row.visible = bool(leaked)

    _outline_tab_outline_content = ft.Container(
        padding=po(left=0, right=0, top=8, bottom=0),
        content=ft.Column([
            _outline_review_col,
        ], spacing=6, horizontal_alignment=ft.CrossAxisAlignment.STRETCH, expand=True),
        expand=True,
    )
    _outline_tab_char_content = ft.Container(
        padding=po(left=0, right=0, top=8, bottom=0),
        content=_outline_char_tf, expand=True,
    )
    _outline_tab_env_content = ft.Container(
        padding=po(left=0, right=0, top=8, bottom=0),
        content=_outline_env_tf, expand=True,
    )
    _outline_tab_props_content = ft.Container(
        padding=po(left=0, right=0, top=8, bottom=0),
        content=_outline_props_tf, expand=True,
    )
    _outline_tab_style_content = ft.Container(
        padding=po(left=0, right=0, top=8, bottom=0),
        content=_outline_style_tf, expand=True,
    )
    _outline_tab_events_content = ft.Container(
        padding=po(left=0, right=0, top=8, bottom=0),
        content=_outline_events_tf, expand=True,
    )
    _outline_tab_logs_content = ft.Container(
        padding=po(left=0, right=0, top=8, bottom=0),
        content=ft.Column([
            ft.Row([
                ft.Container(expand=True),
                ft.TextButton(
                    "Clear",
                    on_click=lambda e: (_outline_log_list.controls.clear(), page.update()),
                    style=ft.ButtonStyle(color=C["text2"]),
                ),
            ], spacing=4),
            _outline_log_list,
        ], spacing=0, expand=True),
        expand=True,
    )

    # ── Debug Chat tabs (A1 chat / A2 chat / B chat) ────────────────────────────────────────────
    # REVIEW-OUTLINE-DEBUG-CHAT-TABS-A1: visible only when LLM Debug Logging is ON. Local continues the real ComfyUI session; Grok has no server-side session, so
    # run_outline_chat_message replays the captured turns client-side as history (one history per stage + capture) - the answers come from the same system prompt
    # and the same conversation the generation used. Shared A2 may run as several independent per-macro Local calls, so the Session dropdown selects which A2
    # session to inspect/continue. Manga uses the same A2 Sequential path.
    _a1_chat_capture: list[dict] = [{}]

    def _a1_live_logger(cap: dict):
        """on_log for the A1 call: logs as before and, while A1 Static runs its step-by-step conversation, redraws the A1 chat tab after every step / attempt."""
        seen = [-1]
        async def _log(msg):
            await _llm_log(msg)
            n = len(cap.get("turns") or [])
            if n != seen[0] or "(static conversation)" in str(msg)[:140]:
                seen[0] = n
                try: _outline_reseed_a1_chat()
                except Exception: pass
        return _log
    _a2_chat_sessions: list[list[dict]] = [[]]
    _a2_chat_selected: list[int] = [0]
    _debug_chat_busy: dict[str, bool] = {"a1": False, "a2": False, "b": False}
    _DEBUG_CHAT_DISPLAY_CAP = 4000

    _chat_clipboard = ft.Clipboard()
    page.services.append(_chat_clipboard)

    def _debug_chat_bubble(list_view: ft.ListView, text: str, is_user: bool):
        list_view.controls.append(ft.Container(
            content=ft.Text(text, size=12.5, color=C["text1"] if is_user else C["text2"],
                            selectable=True),
            bgcolor=C["accent"] + "22" if is_user else C["surface"],
            border_radius=br(8), padding=po(left=10, right=10, top=8, bottom=8),
            margin=ft.Margin(left=40 if is_user else 0, right=0 if is_user else 40, top=0, bottom=0),
        ))
        if list_view.page: list_view.update()

    def _debug_chat_seed(list_view: ft.ListView, capture: dict, label: str):
        list_view.controls.clear()
        turns = capture.get("turns") or []
        if not turns:
            list_view.controls.append(ft.Text(
                f"No {label} call captured yet — generate (or regenerate) this outline with "
                f"LLM Debug Logging ON.",
                size=11, color=C["text3"], italic=True))
            if list_view.page: list_view.update()
            return
        list_view.controls.append(ft.Text(
            f"🔗 {label} — {'Grok' if capture.get('backend') == 'grok' else 'local'}, {len(turns)} call{'s' if len(turns) != 1 else ''}",
            size=10, color=C["text3"], italic=True))
        for t in turns:
            if t.get("stage"):
                list_view.controls.append(ft.Text(f"▸ {t['stage']}", size=10, color=C["text3"], italic=True))
            _u = t.get("user_prompt", "")
            _a = t.get("raw_response", "")
            _u_show = _u if len(_u) <= _DEBUG_CHAT_DISPLAY_CAP else (
                _u[:_DEBUG_CHAT_DISPLAY_CAP] + f"\n…[{len(_u)} chars total — sent in full]")
            _a_show = _a if len(_a) <= _DEBUG_CHAT_DISPLAY_CAP else (
                _a[:_DEBUG_CHAT_DISPLAY_CAP] + f"\n…[{len(_a)} chars total — sent in full]")
            _debug_chat_bubble(list_view, _u_show, is_user=True)
            _debug_chat_bubble(list_view, _a_show, is_user=False)
        if list_view.page: list_view.update()

    def _build_debug_chat_panel(stage_key: str, label: str, get_capture, header: ft.Control | None = None):
        """Self-contained inline chat panel (list + input + send) for one debug-chat tab. get_capture()
        returns the live chat_capture dict to seed/send from (called fresh every time, so it always
        reflects the latest generation/session selection). Returns (container, reseed_fn)."""
        chat_list = ft.ListView(spacing=6, expand=True, auto_scroll=True)
        status_txt = ft.Text("", size=10, color=C["text3"])
        input_tf = ft.TextField(
            hint_text=f"Ask {label} why it wrote what it wrote...", multiline=True, min_lines=1,
            max_lines=4, text_size=13, border_color=C["border"], focused_border_color=C["accent"],
            content_padding=po(left=10, right=10, top=8, bottom=8), expand=True,
        )

        _extra: list[tuple[str, str]] = []          # messages typed in this panel after the generation (copied too)

        def _chat_text() -> str:
            """The whole conversation as plain text, untruncated: system prompt once, then every request / answer, then the follow-up chat."""
            cap = get_capture() or {}
            turns = cap.get("turns") or []
            out = []
            if turns:
                out.append("SYSTEM PROMPT:\n" + str(turns[0].get("system_prompt", "")))
            for t_ in turns:
                out.append(("=== " + str(t_["stage"]) + " ===\n" if t_.get("stage") else "") + "--- USER ---\n" + str(t_.get("user_prompt", ""))
                           + "\n\n--- ASSISTANT ---\n" + str(t_.get("raw_response", "")))
            for who, txt in _extra:
                out.append("--- " + who + " (follow-up) ---\n" + txt)
            return "\n\n".join(out)

        async def _copy_all(e=None):
            txt = _chat_text()
            if not txt.strip():
                status_txt.value = "⚠ Nothing to copy yet."
            else:
                try:
                    await _chat_clipboard.set(txt)
                    status_txt.value = f"✅ Copied the whole chat ({len(txt):,} characters)"
                except Exception as ex:
                    status_txt.value = f"❌ Could not copy: {ex}"
            status_txt.update()

        def _reseed(e=None):
            _extra.clear()
            _debug_chat_seed(chat_list, get_capture(), label)

        async def _send(e=None):
            if _debug_chat_busy[stage_key]: return
            msg = (input_tf.value or "").strip()
            if not msg: return
            cap = get_capture()
            if not cap.get("turns"):
                status_txt.value = "⚠ Nothing generated yet in this session."
                status_txt.update(); return
            input_tf.value = ""; input_tf.update()
            _debug_chat_bubble(chat_list, msg, is_user=True)
            _debug_chat_busy[stage_key] = True
            status_txt.value = "⏳ Thinking..."; status_txt.update()
            try:
                reply = await run_outline_chat_message(cap, f"dbg-{stage_key}-{id(cap)}", msg, grok_api_key=_grok_api_key[0],      # Grok history key: one per stage and per capture (Local ignores it)
                                                        on_log=_llm_log)
                _debug_chat_bubble(chat_list, reply if reply else "⚠ No response", is_user=False)
                _extra.extend([("USER", msg), ("ASSISTANT", reply or "")])
            except Exception as ex:
                _debug_chat_bubble(chat_list, f"❌ {ex}", is_user=False)
            _debug_chat_busy[stage_key] = False
            status_txt.value = ""; status_txt.update()

        input_tf.on_submit = lambda e: asyncio.create_task(_send(e))
        _col_children = ([header] if header is not None else []) + [
            ft.Row([ft.Container(expand=True),
                    ft.TextButton("Copy all", icon=ft.Icons.CONTENT_COPY, on_click=lambda e: asyncio.create_task(_copy_all(e)),
                                  style=ft.ButtonStyle(color=C["text2"]))], spacing=4),
            chat_list,
            status_txt,
            ft.Row([input_tf,
                    ft.IconButton(icon=ft.Icons.SEND_ROUNDED, icon_color=C["accent"],
                                 on_click=lambda e: asyncio.create_task(_send(e)))],
                   vertical_alignment=ft.CrossAxisAlignment.END),
        ]
        container = ft.Container(
            padding=po(left=0, right=0, top=8, bottom=0),
            content=ft.Column(_col_children, spacing=6, expand=True),
            expand=True,
        )
        return container, _reseed

    _outline_tab_a1chat_content, _outline_reseed_a1_chat = _build_debug_chat_panel(
        "a1", "Story Bible", lambda: _a1_chat_capture[0])

    _a2_session_dd = ft.Dropdown(
        label="Session", dense=True, text_size=12, border_color=C["border"],
        width=260, options=[],
        tooltip="Beat Sheet (Local) runs one LLM call per macro event — "
                "pick which one to continue chatting with.",
    )

    def _a2_active_capture() -> dict:
        sessions = _a2_chat_sessions[0]
        if not sessions:
            return {}
        idx = _a2_chat_selected[0] if 0 <= _a2_chat_selected[0] < len(sessions) else 0
        return sessions[idx]

    _outline_tab_a2chat_content, _outline_reseed_a2_chat = _build_debug_chat_panel(
        "a2", "Beat Sheet", _a2_active_capture, header=_a2_session_dd)

    def _outline_refresh_a2_sessions():
        sessions = _a2_chat_sessions[0]
        _a2_session_dd.options = [
            ft.dropdown.Option(
                key=str(i),
                text=f"{s.get('label', '?')} · {len(s.get('turns') or [])} call"
                     f"{'s' if len(s.get('turns') or []) != 1 else ''}",
            )
            for i, s in enumerate(sessions)
        ]
        if sessions:
            _a2_chat_selected[0] = max(0, min(_a2_chat_selected[0], len(sessions) - 1))
            _a2_session_dd.value = str(_a2_chat_selected[0])
        else:
            _a2_chat_selected[0] = 0
            _a2_session_dd.value = None
        if _a2_session_dd.page: _a2_session_dd.update()
        _outline_reseed_a2_chat()

    def _on_a2_session_change(e=None):
        # Flet 0.85's Dropdown fires on_select (not reliably on_change) and its control .value can
        # still hold the previous key at that moment — same class of issue as outline_mode_dd —
        # so prefer the event's own data when it is a valid session index.
        _n = len(_a2_chat_sessions[0])
        _pick = None
        for _cand in (getattr(e, "data", None) if e is not None else None,
                      getattr(getattr(e, "control", None), "value", None) if e is not None else None,
                      _a2_session_dd.value):
            try:
                _i = int(_cand)
            except (TypeError, ValueError):
                continue
            if 0 <= _i < _n:
                _pick = _i
                break
        _a2_chat_selected[0] = _pick if _pick is not None else 0
        _a2_session_dd.value = str(_a2_chat_selected[0])
        _outline_reseed_a2_chat()

    _a2_session_dd.on_select = _on_a2_session_change
    _a2_session_dd.on_change = _on_a2_session_change

    # B (Local) runs one call sequence per macro event, exactly like A2 — same Session dropdown.
    # _outline_chat_capture[0] stays the whole capture; the ACTIVE session (selected macro) is what
    # the debug chat panel shows and continues. Grok/whole-story has no "sessions" -> no dropdown.
    _b_chat_sessions: list[list[dict]] = [[]]
    _b_chat_selected: list[int] = [0]

    _b_session_dd = ft.Dropdown(
        label="Session", dense=True, text_size=12, border_color=C["border"],
        width=260, options=[],
        tooltip="Shot List (Local) runs one LLM session per macro event — "
                "pick which one to read and continue chatting with.",
    )

    def _b_active_capture() -> dict:
        sessions = _b_chat_sessions[0]
        if not sessions:
            return _outline_chat_capture[0]
        idx = _b_chat_selected[0] if 0 <= _b_chat_selected[0] < len(sessions) else 0
        return sessions[idx]

    _outline_tab_bchat_content, _outline_reseed_b_chat = _build_debug_chat_panel(
        "b", "Shot List", _b_active_capture, header=_b_session_dd)

    def _outline_refresh_b_sessions():
        sessions = _b_chat_sessions[0]
        _b_session_dd.options = [
            ft.dropdown.Option(
                key=str(i),
                text=f"{s.get('label', '?')} · {len(s.get('turns') or [])} call"
                     f"{'s' if len(s.get('turns') or []) != 1 else ''}",
            )
            for i, s in enumerate(sessions)
        ]
        if sessions:
            _b_chat_selected[0] = max(0, min(_b_chat_selected[0], len(sessions) - 1))
            _b_session_dd.value = str(_b_chat_selected[0])
        else:
            _b_chat_selected[0] = 0
            _b_session_dd.value = None
        # the Outline Chat dialog continues whatever session is active, never the final macro's
        _act = _b_active_capture()
        _outline_chat_session_id[0] = _act.get("session_id") or ""
        if _b_session_dd.page: _b_session_dd.update()
        _outline_reseed_b_chat()

    def _b_set_chat_capture(cap: dict):
        """Store a finished B capture. Local B carries cap["sessions"] (one per macro); each is made
        self-contained so the chat continues exactly that macro's session_id and turns."""
        _outline_chat_capture[0] = cap
        _b_chat_sessions[0] = [
            {"label": s.get("macro", "?"), "turns": s.get("turns") or [],
             "backend": cap.get("backend", "local"), "llm_wf_file": cap.get("llm_wf_file", ""),
             "session_id": s.get("session_id", ""), "grok_model": cap.get("grok_model", "")}
            for s in (cap.get("sessions") or []) if isinstance(s, dict)
        ]
        _b_chat_selected[0] = 0
        _outline_chat_session_id[0] = cap.get("session_id") or ""
        _outline_refresh_b_sessions()

    def _on_b_session_change(e=None):
        _n = len(_b_chat_sessions[0])
        _pick = None
        for _cand in (getattr(e, "data", None) if e is not None else None,
                      getattr(getattr(e, "control", None), "value", None) if e is not None else None,
                      _b_session_dd.value):
            try:
                _i = int(_cand)
            except (TypeError, ValueError):
                continue
            if 0 <= _i < _n:
                _pick = _i
                break
        _b_chat_selected[0] = _pick if _pick is not None else 0
        _b_session_dd.value = str(_b_chat_selected[0])
        _outline_chat_session_id[0] = _b_active_capture().get("session_id") or ""
        _outline_reseed_b_chat()

    _b_session_dd.on_select = _on_b_session_change
    _b_session_dd.on_change = _on_b_session_change

    def _outline_debug_chat_visible() -> bool:
        return bool(_llm_debug_logging[0])          # Local and Grok: both backends keep their turns in the capture (Grok chat replays that history client-side)

    # Built ONCE and never replaced — a Tab's "visible" property can be toggled live (Flet supports
    # this as a plain property mutation), unlike swapping the Tabs/TabBar/TabBarView control tree
    # itself, which re-parents already-mounted child controls and breaks ("Control must be added to
    # the page first"). length stays fixed at 10 for the lifetime of the control; hidden tabs just
    # render no header and are unreachable, their TabBarView page simply never gets selected.
    def _outline_icon_tab(icon, tip: str, color=None, **kw):
        """Icon-only tab header (the 9-10 text headers overflowed the dialog); the name shows as a
        hover tooltip. The label is a Container because Tab itself has no tooltip property.
        `color` tints the icon (the chat / log tabs are orange to set them apart)."""
        return ft.Tab(label=ft.Container(content=ft.Icon(icon, size=20, color=color), tooltip=tip,
                                         padding=po(left=6, right=6, top=0, bottom=0)), **kw)

    _outline_tab_a1chat_header = _outline_icon_tab(ft.Icons.MENU_BOOK, "Reference chat", color=ft.Colors.ORANGE, visible=False)
    _outline_tab_a2chat_header = _outline_icon_tab(ft.Icons.FORMAT_LIST_NUMBERED, "Beats chat", color=ft.Colors.ORANGE, visible=False)
    _outline_tab_bchat_header = _outline_icon_tab(ft.Icons.CAMERA_ALT, "Shots chat", color=ft.Colors.ORANGE, visible=False)

    def _outline_refresh_tabs_visibility():
        """Call before opening Review Outline — debug logging / backend selection can change between
        generations, so tab visibility is recomputed fresh each time rather than cached. Pure
        property mutation only (mirrors how _outline_req_section.visible etc. are toggled elsewhere
        in this file) — the page.update() that already runs right after opening the dialog picks up
        this change; calling .update() here directly (even .page-guarded) raised "Control must be
        added to the page first" for these dialog-only controls before the dialog's first open."""
        _outline_style_tf.value = _source_style[0] or ""
        _show = _outline_debug_chat_visible()
        _outline_tab_a1chat_header.visible = _show
        _outline_tab_a2chat_header.visible = _show
        _outline_tab_bchat_header.visible = _show
        _outline_refresh_b_visibility()

    _outline_left_pane = ft.Container(
        width=760,
        content=ft.Column([
            _outline_leak_warning_banner,
            # toolbar — always visible above the tabs, regardless of active tab
            ft.Container(
                content=ft.Row([
                    _outline_review_regen_btn,
                    _outline_regen_row,
                    _outline_refresh_b_btn,
                    ft.Container(expand=True),
                    _outline_narrow_cb,
                ], spacing=4),
                border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
                padding=po(left=0, right=0, top=0, bottom=6),
            ),
            _outline_req_section,
            ft.Tabs(
                length=10,
                selected_index=0,
                content=ft.Column([
                    ft.TabBar(tabs=[
                        _outline_icon_tab(ft.Icons.ARTICLE, "Outline"),
                        _outline_icon_tab(ft.Icons.PERSON, "Character"),
                        _outline_icon_tab(ft.Icons.PLACE, "Locations"),
                        _outline_icon_tab(ft.Icons.INVENTORY_2, "Props"),
                        _outline_icon_tab(ft.Icons.PALETTE, "Style"),
                        _outline_icon_tab(ft.Icons.EVENT_NOTE, "Events"),
                        _outline_tab_a1chat_header,
                        _outline_tab_a2chat_header,
                        _outline_tab_bchat_header,
                        _outline_icon_tab(ft.Icons.RECEIPT_LONG, "Logs", color=ft.Colors.ORANGE),
                    ]),
                    ft.TabBarView(controls=[
                        _outline_tab_outline_content,
                        _outline_tab_char_content,
                        _outline_tab_env_content,
                        _outline_tab_props_content,
                        _outline_tab_style_content,
                        _outline_tab_events_content,
                        _outline_tab_a1chat_content,
                        _outline_tab_a2chat_content,
                        _outline_tab_bchat_content,
                        _outline_tab_logs_content,
                    ], expand=True),
                ], spacing=0, expand=True),
                expand=True,
            ),
        ], spacing=6, expand=True),
    )

    outline_review_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Row([
            ft.Text("📋 Review Outline", size=14, weight=ft.FontWeight.W_700, color=C["text1"]),
            _outline_review_loading,
            ft.Container(expand=True),
            _outline_review_status,
        ], spacing=8),
        content=ft.Container(
            width=800, height=680,
            content=_outline_left_pane,
        ),
        actions=[
            ft.Row([
                ft.TextButton("Cancel", on_click=_outline_review_cancel,
                             style=ft.ButtonStyle(color=C["text2"])),
                _outline_save_btn,
                _outline_create_btn,
                _outline_continue_btn,
            ], spacing=4),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        actions_padding=po(left=24, right=24, top=4, bottom=20),
    )
    # (not added to page.overlay — see note above groom_dialog)
    _flow_dialogs.extend([outline_genre_dialog, groom_dialog, outline_review_dialog])

    # VNEXT-PRODUCTION-PROMOTION-A1: vNext (A1/A2/B/Fill, see task4.md) is the production pipeline.
    # This used to be gated by a `_VNEXT_DEFAULT = True` switch with a legacy Outline fallback
    # (_do_generate_outline). That fallback had no UI toggle and was confirmed unreachable dead
    # code, so it — and the `if _VNEXT_DEFAULT` branches in _outline_generate_dispatch and
    # _fill_once that only ever took the True arm — were removed; _VNEXT_DEFAULT itself was then
    # unused and was removed too.

    def _cap_b_material(macro_events, micro_beats, cap):
        """B-UNIT-CAP-A1: keep whole macro events, in order, until adding the next one would push
        the running micro_beats count over `cap` — a macro event's own micro_beats are never
        split. Always keeps at least the first macro event, even if it alone exceeds cap. Returns
        (kept_macro_events, kept_micro_beats, truncated: bool)."""
        by_parent: dict[str, list] = {}
        for m in micro_beats or []:
            if isinstance(m, dict):
                by_parent.setdefault(m.get("parent"), []).append(m)
        kept_macros: list = []
        kept_micro: list = []
        running = 0
        for e in macro_events or []:
            group = by_parent.get(e.get("id"), [])
            if kept_macros and running + len(group) > cap:
                break
            kept_macros.append(e)
            kept_micro.extend(group)
            running += len(group)
        truncated = len(kept_micro) < len(micro_beats or [])
        return kept_macros, kept_micro, truncated

    async def _call_b_vnext(a1, a2, final_n, llm_wf, on_err, b_chat_capture):
        """The ONE Normal vNext B call — shared by initial generation and Debug Refresh B so both
        use identical arguments/settings. Returns run_b_vnext's result (list or None).

        B-UNIT-CAP-A1: the full story is tried first, exactly as before — no pre-emptive cut. Only
        if that attempt comes up short (fails outright, or returns fewer shots than requested,
        e.g. B's own truncation-salvage kicked in) does a SECOND attempt run: the story capped to
        whole macro events (40 units, 80 for Manga) and final_n reduced to match exactly, so the
        retry gets a clean 1:1 B call on a smaller slice instead of straining the same way twice."""
        _sync_char_sheet_to_a1()

        async def _one_b_attempt(macro_events, micro_beats, n, cap):
            _a2_count = len(micro_beats)
            _strict_1to1 = bool(_a2_count > 0 and int(n) == _a2_count)
            await _llm_log(f"B mode={'STRICT_A2_1TO1' if _strict_1to1 else 'EDITORIAL'} "
                           f"a2_count={_a2_count} final_count={int(n)}")
            return await run_shot_planning(
                a1, macro_events, micro_beats, n,
                llm_wf, on_err, on_log=_llm_log, dialogue_lang=_dialogue_lang[0],
                genre=_sel_genre[0], genre2=_sel_genre2[0], desc_lang=_desc_lang[0],
                # Fill-only presentation settings (Shot Scale, Expression) are not routed to B — they
                # reach Fill. Dialogue Style / Language / Narration Type ARE routed: B authors the text
                # fields with them; how they are drawn is decided later at render time.
                dialogue_style=_cur_dlg_style(), narration_type=_narration_type[0],
                grok_api_key=_grok_api_key[0], grok_model=_grok_model[0],
                chat_capture=cap, mode=_outline_mode_for_engines(),
                a2_count=_a2_count, strict_a2_1to1=_strict_1to1,
            )

        _full_macro = a1.get("macro_events") or []
        _full_micro = a2.get("micro_beats") or []
        _cap1: dict = {}
        _b_res = await _one_b_attempt(_full_macro, _full_micro, final_n, _cap1)
        _final_cap = _cap1

        # LOCAL B is per-macro and already retries only the failed macro; a failure there is final —
        # never cap/truncate the story (that would rerun finished macros and drop later ones).
        if llm_wf == _GROK_OPTION and (_b_res is None or len(_b_res) < int(final_n)):
            # This helper is Normal-only (Manga has its own retry block below in
            # _do_generate_outline_manga_vnext, using 80) — always 40 here.
            _capped_macro, _capped_micro, _truncated = _cap_b_material(_full_macro, _full_micro, 40)
            if _truncated:
                _reduced_n = len(_capped_micro)
                await _llm_log(f"⚠ Shot List: first pass got {len(_b_res or [])}/{final_n} — retrying "
                               f"capped to {_reduced_n}/{len(_full_micro)} beats for a clean 1:1 pass")
                _cap2: dict = {}
                _cap_res = await _one_b_attempt(_capped_macro, _capped_micro, _reduced_n, _cap2)
                if _cap_res is not None and len(_cap_res) > len(_b_res or []):
                    _b_res = _cap_res
                    _final_cap = _cap2
                    _set_b_salvage_notice(
                        f"⚠ Shot List couldn't complete {final_n} shots from the full story — retried "
                        f"with the story capped to {_reduced_n} beats (through macro "
                        f"{_capped_macro[-1].get('id')}) for a clean 1:1 pass. The rest of the "
                        f"story was not sent to B this run."
                    )

        b_chat_capture.clear()
        b_chat_capture.update(_final_cap)
        # B-TRUNCATION-SALVAGE-A1: B truncated mid-response and Python recovered a leading
        # prefix of complete shots instead of the full requested count — tell the user, and keep
        # telling them (sticky banner) until a fresh Outline run or Retry B clears it.
        _salvage = b_chat_capture.get("salvage")
        if _salvage:
            _got = f"{_salvage['recovered']} of {_salvage['requested']} shots"
            _said = (_salvage.get("note") or "").strip()
            if _said:
                _msg = (f"⚠ B stopped after {_got} — instead of the rest the model wrote: "
                        f"\"{_said[:140]}\". It may have refused to continue; retry B or switch model.")
            elif not _salvage.get("unclosed"):
                _msg = (f"⚠ B stopped after {_got} — the model ended its reply early without an error "
                        f"(often content it declined to continue, or a length limit). "
                        f"Retry B or switch model.")
            else:
                _msg = (f"⚠ B response was cut off — repaired using {_got}. "
                        f"Retry B for the full count.")
            _set_b_salvage_notice(_msg)
        return _b_res

    def _b_shots_to_native(b_shots) -> list[dict]:
        """NORMAL-VNEXT-SCHEMA-CLEANUP-A1 §1: B's NATIVE schema plus an empty "prompt" (until Fill).
        B-SHOT-DESC-SINGLE-TRUTH: B no longer emits location/clothing — their truth already lives
        in shot_desc's World Grounding / Clothing-Appearance tags."""
        return [{
            "shot": s.get("shot"),
            "uses": list(s.get("uses") or []),
            "visible_people": s.get("visible_people", ""),
            "shot_desc": s.get("shot_desc", ""),
            "story": s.get("story", ""),
            "objects": s.get("objects", ""),
            "camera": s.get("camera", ""),
            "narration": s.get("narration", ""),
            "speech_char": s.get("speech_char", ""),
            "subtitle": s.get("subtitle", ""),
            "prompt": "",
        } for s in b_shots]

    async def _outline_refresh_b():
        """B-ACTION-CONTINUITY-DEBUG-REFRESH-A1: Debug-only. Reruns ONLY Normal vNext B from the
        current A1/A2 results and the SAME final image count — no Groom/A1/A2/count dialog/Fill.
        Non-destructive: B's result is generated into a temporary list and only replaces shots_data
        after run_b_vnext returned a structurally valid result; on failure the previous shots stay."""
        if llm_busy[0] or _b_run_state[0] == "RUNNING" or _manga_vnext_mode[0]:
            return
        a1, a2, n = _outline_vnext_a1[0], _outline_vnext_a2[0], _b_final_n[0]
        if not (a1 and a2 and n > 0):
            return
        _clear_b_salvage_notice()  # B-TRUNCATION-SALVAGE-A1: Retry B clears the sticky notice
        llm_wf = llm_dd.value
        _errs: list[str] = []

        async def _on_b_err(msg):
            _errs.append(str(msg)); await _llm_log(f"❌ Refresh B: {msg}")

        _b_run_state[0] = "RUNNING"
        llm_busy[0] = True; llm_prog.visible = True
        gen_shots_btn.disabled = True; fill_prompts_btn.disabled = True
        _set_outline_phase("PASS_B")
        _outline_timer_start("📐 Refreshing B...")
        page.update()
        _cap: dict = {}
        new_shots = None
        try:
            new_shots = await _call_b_vnext(a1, a2, n, llm_wf, _on_b_err, _cap)
        except Exception as _ex:
            import traceback
            await _llm_log(f"❌ Refresh B exception:\n{traceback.format_exc()}")
            _errs.append(f"B error: {_ex}")
        llm_busy[0] = False; llm_prog.visible = False
        gen_shots_btn.disabled = False; fill_prompts_btn.disabled = False
        # Wired up whether B succeeded or failed, so a failed macro's session (and whichever
        # macros DID complete before it) stay reachable in B chat.
        if _cap.get("turns"):
            _b_set_chat_capture(_cap)
            outline_chat_btn.disabled = False
        if new_shots:
            shots_data[:] = _b_shots_to_native(new_shots)
            _b_run_state[0] = "FINISHED_SUCCESS"
            status_lbl.value = f"📐 Outline done — {len(shots_data)} shots"
            _outline_review_rebuild()
            _set_outline_phase("READY", f"✅ Outline ready — {len(shots_data)} shots (B refreshed)")
        else:
            _b_run_state[0] = "FINISHED_FAILED"
            _why = _errs[-1] if _errs else "B refresh failed"
            if shots_data:
                _set_outline_phase("READY", f"⚠ B refresh failed — previous outline kept ({_why})")
            else:
                _set_outline_phase("FAILED", f"❌ {_why}")
        page.update()

    async def _stop_for_review_a():
        """A1+A2 are done: park the dialog so the user can fix Character/Events, then press
        Create shot/pages. Nothing else runs until then."""
        _save_state()
        llm_busy[0] = False; llm_prog.visible = False
        gen_shots_btn.disabled = False; fill_prompts_btn.disabled = False
        _outline_review_col.controls.clear()
        _outline_review_col.controls.append(ft.Text(
            "Story analysis is done. Review the Character and Events tabs, fix anything you want, "
            "then press "
            + ("Create pages" if _manga_vnext_mode[0] else "Create shot") + ".",
            size=12, color=C["text2"]))
        _set_outline_phase("REVIEW_A", "Review Character and Events, then create the shots")
        _outline_sync_char_env_tabs()
        page.update()

    async def _outline_create_shots():
        """Create shot/pages button: apply the Events edit, then run Shot List (count dialog first for
        Normal) from the reviewed Story Bible / Beat Sheet — never re-running them."""
        if llm_busy[0] or _outline_phase[0] not in ("REVIEW_A", "READY", "FAILED"):
            return
        if not (_outline_vnext_a1[0] and _outline_vnext_a2[0]):
            return
        if not _outline_apply_events_edit():
            return
        _sync_char_sheet_to_a1()
        llm_busy[0] = True; llm_prog.visible = True
        gen_shots_btn.disabled = True; fill_prompts_btn.disabled = True
        await _outline_generate_dispatch(ask_shot_count=True, reuse_pass_a=True, from_review=True)

    async def _do_generate_outline_vnext(ask_shot_count: bool = True, reuse_pass_a: bool = False,
                                         stop_after_a: bool = False, from_review: bool = False):
        """VNEXT-PRODUCTION-PROMOTION-A1: production entry point for the vNext pipeline —
        A1 -> user count dialog -> A2 -> B -> Fill -> shots_data, reusing the SAME Review Outline
        dialog/phase/timer/save plumbing as the legacy path, but calling none of legacy's own
        A1/A2/B creative code and none of its cast-review/Step-1/Step-2 audit dialogs (vNext has no
        equivalent stage for those — see task4.md §32). If vNext fails structurally, this reports
        the failure via _on_err and stops; it never silently falls back to generating a different
        interpretation through the legacy pipeline (item 4 of VNEXT-PRODUCTION-PROMOTION-A1)."""
        story = _outline_story[0] or story_field.value.strip()
        llm_wf = llm_dd.value
        _sync_outline_mode()
        normal_a2_mode = "sequential" if _outline_mode[0] == "sequential" else "cinematic"
        _clear_b_salvage_notice()  # B-TRUNCATION-SALVAGE-A1: a fresh Outline run clears the sticky notice

        async def _on_err(msg):
            llm_busy[0] = False; llm_prog.visible = False
            gen_shots_btn.disabled = False; fill_prompts_btn.disabled = False
            if _b_run_state[0] == "RUNNING":
                _b_run_state[0] = "FINISHED_FAILED"
            _set_outline_phase("FAILED", f"❌ {msg}")
            status_lbl.value = f"❌ {msg}"; await _llm_log(f"❌ {msg}"); page.update()

        _b_run_state[0] = "NOT_STARTED"
        if from_review or not outline_review_dialog.open:
            # from_review: the dialog is already open with the shots of the previous Create — Cancel
            # must restore THOSE, not the snapshot taken before the very first run.
            _outline_shots_backup[:] = [dict(s) for s in shots_data]
        if not reuse_pass_a:
            # vNext has no legacy "events" list of its own (A1 vNext's production bible uses
            # story_summary/continuity_notes instead) — clear the Events tab so it doesn't show
            # stale events left over from an older legacy generation; A2's beats repopulate it
            # below once the sequence exists.
            _outline_events[0] = []
        _set_outline_phase("PASS_B" if from_review else "PASS_A")
        _outline_timer_start("📐 Preparing shots..." if from_review else "📐 Analyzing story...")
        _outline_review_col.controls.clear()
        _outline_review_is_new[0] = True
        _outline_refresh_tabs_visibility()
        _open_dialog(outline_review_dialog)
        _sync_genre()

        try:
            _reuse_ok = (reuse_pass_a and _outline_vnext_a1[0] and _outline_vnext_a2[0]
                         and (from_review or _outline_vnext_a2[0].get("a2_mode") == normal_a2_mode))
            if from_review:
                _sync_char_sheet_to_a1()
            if _reuse_ok:
                a1 = _outline_vnext_a1[0]
                a2 = _outline_vnext_a2[0]
                await _llm_log("↩ vNext: reusing existing Story Bible + Beat Sheet (count-only change) — rerunning Shot List + Image Prompts only")
            else:
                _a1_chat_capture_local: dict = {}
                _a1_chat_capture[0] = _a1_chat_capture_local          # the A1 chat tab follows this run live
                a1 = await run_production_reference(
                    story, llm_wf, _on_err, on_log=_a1_live_logger(_a1_chat_capture_local), mode=normal_a2_mode,
                    genre=_sel_genre[0], genre2=_sel_genre2[0],
                    desc_lang=_desc_lang[0],
                    grok_api_key=_grok_api_key[0], grok_model=_grok_model[0],
                    chat_capture=_a1_chat_capture_local,
                )
                if a1 is None:
                    if _a1_chat_capture_local.get("turns"):      # keep the failed run's raw replies inspectable in the A1 chat
                        _a1_chat_capture[0] = _a1_chat_capture_local
                        _outline_reseed_a1_chat()
                    return
                _outline_vnext_a1[0] = a1
                if _a1_chat_capture_local.get("turns"):
                    _a1_chat_capture[0] = _a1_chat_capture_local
                    _outline_reseed_a1_chat()
                char_field.value = a1.get("character_sheet", "") or ""
                scene_field.value = a1.get("world_sheet", "") or ""
                # A1-BIBLE-LOCATION-PLUMBING-PROP-OWNERSHIP-A1: locations is its own bible sheet
                # with its own storage (_outline_locations), never folded back into scene_field —
                # always assigned (never conditionally preserved) so a previous story's locations
                # can't leak into a new one that genuinely has none.
                _outline_locations[0] = a1.get("locations", "") or ""
                props_field.value = a1.get("important_objects", "") or ""
                # SOURCE-STYLE-A1-FALLBACK: Groom already extracted source_style when it ran — A1
                # only fills it in when Groom didn't run or found none, and never overwrites a
                # value Groom already captured.
                _adopt_a1_source_style(a1.get("source_style") or "")
                # A1 vNext has no single "story_summary" string (replaced by macro_events) — keep
                # the legacy Summary tab useful by joining the macro events' own content.
                _macro_summary = " ".join(
                    str(e.get("content", "")).strip() for e in (a1.get("macro_events") or [])
                ).strip()
                if _macro_summary:
                    _story_summary[0] = _macro_summary
                    story_summary_field.value = _macro_summary
                _outline_sync_char_env_tabs()
                page.update()
                _save_state()

                unit = "shots"
                _outline_timer_start("📐 Finding shots-worth-showing moments...")
                page.update()
                _a2_chat_capture_local: dict = {}
                a2 = await run_beat_decomposition(
                    # CLEAN-A1: each macro event is written to carry enough story truth for A2 to
                    # subdivide it directly, so A2 receives the A1 structure rather than a second
                    # competing copy of the full story.
                    a1, llm_wf, _on_err, on_log=_llm_log,
                    mode=normal_a2_mode,
                    genre=_sel_genre[0], genre2=_sel_genre2[0], desc_lang=_desc_lang[0],
                    grok_api_key=_grok_api_key[0], grok_model=_grok_model[0],
                    chat_capture=_a2_chat_capture_local,
                )
                if a2 is None:
                    return
                # Remember which A2 mode produced these units (persisted with the A2 dict) so B's
                # strict 1:1 decision — and Refresh B — always follow the mode A2 really ran in.
                a2["a2_mode"] = normal_a2_mode
                _outline_vnext_a2[0] = a2
                if _a2_chat_capture_local.get("sessions"):
                    _a2_chat_sessions[0] = _a2_chat_capture_local["sessions"]
                    _outline_refresh_a2_sessions()
                # A2 vNext's micro beats are the closest vNext equivalent of legacy's events list —
                # populate the Events tab with them instead of leaving it empty.
                _outline_events[0] = [
                    f"{m.get('id')} ({m.get('parent')}): {m.get('content', '')}"
                    for m in (a2.get("micro_beats") or [])
                ]
                _outline_sync_char_env_tabs()
                page.update()
                if stop_after_a:
                    await _stop_for_review_a()
                    return

            unit = "shots"
            # A2-MICRO-COUNT-INSTEAD-OF-SHOT-RECOMMENDATION-A1: A2 unit count is decomposition
            # observability only. It is not a one-unit-per-shot contract. Final image count is user
            # authority; B owns final selection. Structural count of the accepted micro units.
            # PIPELINE-FIXPACK-A1: recommended count = Python's own count of the actual STRUCTURED A2 units
            # (well-formed {id, content} items) — never any count the LLM might state in free text.
            micro_n = sum(1 for _u in (a2.get("micro_beats") or [])
                          if isinstance(_u, dict) and str(_u.get("id") or "").strip()
                          and str(_u.get("content") or "").strip())
            # A2-UNIT-1TO1-DEFAULT: A2's unit count is the default final count for BOTH Sequential
            # and Cinematic — never a stale num_shots_field value. The user's explicit count-dialog
            # choice (or the field's own value when the dialog is skipped) is what overrides it.
            _a2_seq = (_outline_mode[0] == "sequential" and a2.get("a2_mode") == "sequential")
            micro_label = "events" if _a2_seq else "beats"
            requested_n = micro_n or 5
            _last_a2_micro_count[0] = micro_n
            final_n = requested_n
            if ask_shot_count:
                await _llm_log(f"Asking {unit}: Beat Sheet found {micro_n} {micro_label}, requested {requested_n}")
                _set_outline_phase("WAITING_COUNT")
                picked = await _ask_shot_count(micro_n, micro_label, requested_n, unit=unit,
                                               recommended=bool(micro_n > 0))
                if picked is None and not outline_review_dialog.open:
                    return
                final_n = picked if picked is not None else requested_n
                if picked is not None and picked != requested_n:
                    num_shots_field.value = str(picked)

            _set_outline_phase("PASS_B")
            _outline_timer_start(f"📐 Designing {unit}...")
            page.update()
            _b_chat_capture: dict = {}
            _b_final_n[0] = int(final_n)
            _b_run_state[0] = "RUNNING"
            b_shots = await _call_b_vnext(a1, a2, final_n, llm_wf, _on_err, _b_chat_capture)
            # NEXT: let "Chat" continue this exact B conversation (Local or Grok) instead of staying
            # permanently disabled — wired up whether B succeeded or failed, so a failed macro's
            # session (and whichever macros DID complete before it) stay reachable in B chat.
            if _b_chat_capture.get("turns"):
                _b_set_chat_capture(_b_chat_capture)
                outline_chat_btn.disabled = False
            if b_shots is None:
                return

            # NEXT: user asked to stop the pipeline here — A1→A2→B only. Fill (prompt writing)
            # no longer runs automatically; the outline review dialog opens with empty prompts,
            # and the user explicitly triggers Fill via "Continue to Image Prompts…" / "Fill Prompts".
            # NORMAL-VNEXT-SCHEMA-CLEANUP-A1 §1: shots_data now holds B's NATIVE schema directly —
            # no more vnext_shot_to_legacy_dict adapter round-trip. Only B's own field vocabulary
            # (shot/uses/visible_people/location/shot_desc/clothing/objects/camera/lighting/
            # narration/speech_char/subtitle) plus "prompt" (empty until Fill runs) — no legacy
            # aliases (scene_desc/clothing_state/prop_state/place/shot_scale/light_sources/
            # speech_text/covers) and no obsolete fields (expression/spacing/mood/env/time_of_day/
            # moment/world_interaction) are carried at all, not even as empty placeholders.
            shots_data[:] = _b_shots_to_native(b_shots)
            _b_run_state[0] = "FINISHED_SUCCESS"
        except Exception as _ex:
            import traceback
            _tb = traceback.format_exc()
            await _llm_log(f"❌ vNext outline exception:\n{_tb}")
            await _on_err(f"vNext outline error: {_ex}")
            return

        excess = [k for k in list(gallery_images.keys()) if k >= len(shots_data)]
        for k in excess:
            for img in gallery_images.pop(k, []):
                try: Path(img["path"]).unlink(missing_ok=True)
                except Exception: pass

        llm_busy[0] = False; llm_prog.visible = False
        gen_shots_btn.disabled = False; fill_prompts_btn.disabled = False
        status_lbl.value = f"📐 Outline done — {len(shots_data)} shots"
        _outline_review_rebuild()
        _set_outline_phase("READY", f"✅ Outline ready — {len(shots_data)} shots")
        page.update()

    async def _do_generate_outline_manga_vnext(ask_shot_count: bool = True, reuse_pass_a: bool = False,
                                               stop_after_a: bool = False, from_review: bool = False):
        """Shared A1 -> shared A2 Sequential -> B Manga -> shots_data.

        Manga has no separate A2 engine and no page-count dialog. B Manga groups the shared
        sequential micro-events into panels/pages and the length of its returned page array is the
        final page count. Fill Manga runs later from the normal Fill button path.
        """
        del ask_shot_count  # Manga page count belongs to B Manga, never the count dialog.
        story = _outline_story[0] or story_field.value.strip()
        llm_wf = llm_dd.value
        _clear_b_salvage_notice()  # a fresh Manga run clears the sticky "needed repair" notice, like a Normal Outline run

        async def _on_err(msg):
            llm_busy[0] = False; llm_prog.visible = False
            gen_shots_btn.disabled = False; fill_prompts_btn.disabled = False
            if _b_run_state[0] == "RUNNING":
                _b_run_state[0] = "FINISHED_FAILED"
            _set_outline_phase("FAILED", f"❌ {msg}")
            status_lbl.value = f"❌ {msg}"; await _llm_log(f"❌ {msg}"); page.update()

        if from_review or not outline_review_dialog.open:
            _outline_shots_backup[:] = [dict(s) for s in shots_data]
        if not reuse_pass_a:
            _outline_events[0] = []
        _b_run_state[0] = "NOT_STARTED"
        _set_outline_phase("PASS_B" if from_review else "PASS_A")
        _outline_timer_start("📐 Preparing pages..." if from_review else "📐 Analyzing story...")
        _outline_review_col.controls.clear()
        _outline_review_is_new[0] = True
        _outline_refresh_tabs_visibility()
        _open_dialog(outline_review_dialog)
        _sync_genre()

        try:
            # A1 is shared and mode-independent. Reuse it whenever available.
            a1 = _outline_vnext_a1[0] if (reuse_pass_a and _outline_vnext_a1[0]) else None
            if a1 is None:
                _a1_chat_capture_local: dict = {}
                _a1_chat_capture[0] = _a1_chat_capture_local          # the A1 chat tab follows this run live
                a1 = await run_production_reference(
                    story, llm_wf, _on_err, on_log=_a1_live_logger(_a1_chat_capture_local), mode="sequential",
                    genre=_sel_genre[0], genre2=_sel_genre2[0],
                    desc_lang=_desc_lang[0],
                    grok_api_key=_grok_api_key[0], grok_model=_grok_model[0],
                    chat_capture=_a1_chat_capture_local,
                )
                if a1 is None:
                    if _a1_chat_capture_local.get("turns"):      # keep the failed run's raw replies inspectable in the A1 chat
                        _a1_chat_capture[0] = _a1_chat_capture_local
                        _outline_reseed_a1_chat()
                    return
                _outline_vnext_a1[0] = a1
                if _a1_chat_capture_local.get("turns"):
                    _a1_chat_capture[0] = _a1_chat_capture_local
                    _outline_reseed_a1_chat()
                char_field.value = a1.get("character_sheet", "") or ""
                scene_field.value = a1.get("world_sheet", "") or ""
                _outline_locations[0] = a1.get("locations", "") or ""
                props_field.value = a1.get("important_objects", "") or ""
                _adopt_a1_source_style(a1.get("source_style") or "")
                _macro_summary = " ".join(
                    str(e.get("content", "")).strip() for e in (a1.get("macro_events") or [])
                ).strip()
                if _macro_summary:
                    _story_summary[0] = _macro_summary
                    story_summary_field.value = _macro_summary
                _outline_sync_char_env_tabs()
                page.update()
                _save_state()
            else:
                await _llm_log("↩ Manga: reusing shared Story Bible")

            # Manga uses the SAME clean A2 as Normal Sequential.
            a2 = None
            if reuse_pass_a and _outline_vnext_a2[0] and _outline_vnext_a2[0].get("a2_mode") == "sequential":
                a2 = _outline_vnext_a2[0]
                await _llm_log("↩ Manga: reusing shared Beat Sheet (Sequential)")
            if a2 is None:
                _outline_timer_start("📐 Finding sequential story events...")
                page.update()
                _a2_chat_capture_local: dict = {}
                a2 = await run_beat_decomposition(
                    a1, llm_wf, _on_err, on_log=_llm_log,
                    mode="sequential",
                    genre=_sel_genre[0], genre2=_sel_genre2[0], desc_lang=_desc_lang[0],
                    grok_api_key=_grok_api_key[0], grok_model=_grok_model[0],
                    chat_capture=_a2_chat_capture_local,
                )
                if a2 is None:
                    return
                a2["a2_mode"] = "sequential"
                _outline_vnext_a2[0] = a2
                if _a2_chat_capture_local.get("sessions"):
                    _a2_chat_sessions[0] = _a2_chat_capture_local["sessions"]
                    _outline_refresh_a2_sessions()

            micro_beats = a2.get("micro_beats") or []
            _outline_events[0] = [
                f"{m.get('id')} ({m.get('parent')}): {m.get('content', '')}"
                for m in micro_beats
            ]
            _last_a2_micro_count[0] = sum(
                1 for m in micro_beats
                if isinstance(m, dict) and str(m.get("id") or "").strip()
                and str(m.get("content") or "").strip()
            )
            _outline_sync_char_env_tabs()
            page.update()
            _save_state()
            if stop_after_a:
                await _stop_for_review_a()
                return

            _set_outline_phase("PASS_B")
            _outline_timer_start("📐 Designing pages...")
            page.update()
            _sync_char_sheet_to_a1()
            _b_run_state[0] = "RUNNING"

            async def _one_b_manga_attempt(macro_events, beats, cap):
                return await run_manga_shot_planning(
                    a1, macro_events, beats,
                    llm_wf, _on_err, on_log=_llm_log, dialogue_lang=_dialogue_lang[0],
                    dialogue_style=_cur_dlg_style(), narration_type=_narration_type[0],
                    genre=_sel_genre[0], genre2=_sel_genre2[0], desc_lang=_desc_lang[0],
                    grok_api_key=_grok_api_key[0], grok_model=_grok_model[0],
                    chat_capture=cap,
                    props_desc=_props_to_str(props_field.value).strip(),
                )

            # B-UNIT-CAP-A1: the full story is tried first, exactly as before. Only if that fails
            # outright does a second attempt run with the story capped to whole macro events (80
            # units) — no batching, and the user is told plainly what happened.
            _full_macro = a1.get("macro_events") or []
            _b_chat_capture: dict = {}
            pages = await _one_b_manga_attempt(_full_macro, micro_beats, _b_chat_capture)

            if pages is None and llm_wf == _GROK_OPTION:   # Local is per-macro: no story cap
                _capped_macro, _capped_micro, _truncated = _cap_b_material(_full_macro, micro_beats, 80)
                if _truncated:
                    await _llm_log(f"⚠ Page List: first pass failed — retrying capped to "
                                   f"{len(_capped_micro)}/{len(micro_beats)} beats")
                    _b_chat_capture = {}
                    pages = await _one_b_manga_attempt(_capped_macro, _capped_micro, _b_chat_capture)
                    if pages is not None:
                        micro_beats = _capped_micro
                        _set_b_salvage_notice(
                            f"⚠ Page List couldn't complete the full story — retried with it capped "
                            f"to {len(_capped_micro)} beats (through macro "
                            f"{_capped_macro[-1].get('id')}) for this pass. The rest of the story "
                            f"was not sent to B this run."
                        )
            # Wired up whether B Manga succeeded or failed, so a failed macro's session (and
            # whichever macros DID complete before it) stay reachable in B chat.
            if _b_chat_capture.get("turns"):
                _b_set_chat_capture(_b_chat_capture)
                outline_chat_btn.disabled = False
            if pages is None:
                return

            _b_run_state[0] = "FINISHED_SUCCESS"
            _manga_salvage = _b_chat_capture.get("salvage")
            if _manga_salvage:
                if _manga_salvage.get("dropped", 0):                  # content was lost: keep the sticky notice in the toolbar
                    _set_b_salvage_notice(
                        f"⚠ Page List response needed repair — kept {_manga_salvage['pages']} page(s) / "
                        f"{_manga_salvage['panels']} panel(s); {_manga_salvage.get('regions_repaired', 0)} "
                        f"layout region(s) fixed, {_manga_salvage.get('dropped', 0)} item(s) dropped. "
                        f"Create pages again to regenerate."
                    )
                else:                                                  # layout only (nothing lost): a toast that goes away after 3 seconds
                    _toast(f"⚠ Page List: {_manga_salvage.get('regions_repaired', 0)} layout region(s) fixed — "
                           f"{_manga_salvage['pages']} page(s) / {_manga_salvage['panels']} panel(s) kept, no content lost.")
            _manga_vnext_final_pages[0] = len(pages)
            _b_final_n[0] = len(pages)
            num_shots_field.value = str(len(pages))

            shots_data.clear()
            for page_spec in pages:
                shots_data.append(manga_vnext_page_to_legacy_dict({
                    "shot": page_spec["page"],
                    "_manga_page_spec": page_spec,
                }))
        except Exception as _ex:
            import traceback
            _tb = traceback.format_exc()
            await _llm_log(f"❌ Manga outline exception:\n{_tb}")
            await _on_err(f"Manga outline error: {_ex}")
            return

        excess = [k for k in list(gallery_images.keys()) if k >= len(shots_data)]
        for k in excess:
            for img in gallery_images.pop(k, []):
                try: Path(img["path"]).unlink(missing_ok=True)
                except Exception: pass

        llm_busy[0] = False; llm_prog.visible = False
        gen_shots_btn.disabled = False; fill_prompts_btn.disabled = False
        status_lbl.value = f"📐 Outline done — {len(shots_data)} pages"
        _outline_review_rebuild()
        _set_outline_phase("READY", f"✅ Outline ready — {len(shots_data)} pages")
        page.update()

    async def _static_pose_a0_check() -> str:
        """A0 (the source router, as before): is the Story field ONE still picture ('STATIC') or a story ('NARRATIVE')? Returns the label, or '' when the check
        could not decide (the caller then runs the normal pipeline, which does its own routing)."""
        story = _outline_story[0] or story_field.value.strip()
        await _llm_log("🔎 Static Pose Fill: checking whether the input is one still picture or a story...")
        decision = await _classify_source(story, llm_dd.value, _llm_log, grok_api_key=_grok_api_key[0], grok_model=_grok_model[0])
        if decision.get("status") == "invalid":
            await _llm_log(f"⚠ Static Pose Fill: invalid check reply {decision.get('raw')!r} - asking once more")
            decision = await _classify_source(story, llm_dd.value, _llm_log, grok_api_key=_grok_api_key[0], grok_model=_grok_model[0])
        label = decision.get("label") or ""
        if label == "STATIC":
            await _llm_log("ℹ Static Pose Fill: STATIC -> poses from the prompt")
        elif label == "NARRATIVE":
            await _llm_log("ℹ Static Pose Fill: NARRATIVE -> the story is planned as usual (Visual Novel)")
        else:
            await _llm_log(f"⚠ Static Pose Fill: the check gave no answer ({decision.get('error') or decision.get('raw')!r}) - the story is planned as usual")
        return label

    async def _do_generate_outline_static_pose(from_review: bool = False):
        """Static Pose Fill: the Story field holds ONE still-picture prompt (A0 said STATIC). No Story Bible, no Beat Sheet, no B, no Fill:
        base prompt (style words removed) -> N planned poses -> one written prompt per shot -> shots_data, each shot ready to render."""
        story = _outline_story[0] or story_field.value.strip()
        llm_wf = llm_dd.value
        n = max(1, min(60, _safe_num_shots()))
        _clear_b_salvage_notice()

        async def _on_err(msg):
            llm_busy[0] = False; llm_prog.visible = False
            gen_shots_btn.disabled = False; fill_prompts_btn.disabled = False
            _b_run_state[0] = "FINISHED_FAILED"
            _set_outline_phase("FAILED", f"❌ {msg}")
            status_lbl.value = f"❌ {msg}"; await _llm_log(f"❌ {msg}"); page.update()

        if from_review or not outline_review_dialog.open:
            _outline_shots_backup[:] = [dict(s) for s in shots_data]
        _outline_events[0] = []
        _b_run_state[0] = "RUNNING"
        _set_outline_phase("PASS_B")
        _outline_timer_start("🎭 Static Pose Fill: removing the style words...")
        _outline_review_col.controls.clear()
        _outline_review_is_new[0] = True
        _outline_refresh_tabs_visibility()
        _open_dialog(outline_review_dialog)
        _sync_genre()

        async def _on_shot(i, shot, total):
            if i + 1 < total:
                _outline_timer_start(f"🎭 Static Pose Fill: writing shot {i + 2}/{total}...")

        try:
            _outline_timer_start("🎭 Static Pose Fill: removing the style words and planning the poses...")
            result = await run_static_pose_fill(story, n, llm_wf, _on_err, on_log=_llm_log,
                                                grok_api_key=_grok_api_key[0], grok_model=_grok_model[0], on_shot=_on_shot)
            if result is None:
                return
            shots_data[:] = result["shots"]
            _b_final_n[0] = len(shots_data)
            _b_run_state[0] = "FINISHED_SUCCESS"
            # The style words taken out of the prompt are the source's style: keep them as "As Source" (Style dropdown), the same place A1 puts the style it reads
            # from a source. A style the user typed or Groom captured for this same source is kept, exactly as after an A1 run.
            _adopt_a1_source_style(result.get("source_style") or "")
            await _llm_log(f"🎬 Static Pose Fill: source style kept as \"As Source\": {(_source_style[0] or '(none)')!r}")
        except Exception as _ex:
            import traceback
            await _llm_log(f"❌ Static Pose Fill exception:\n{traceback.format_exc()}")
            await _on_err(f"Static Pose Fill error: {_ex}")
            return

        excess = [k for k in list(gallery_images.keys()) if k >= len(shots_data)]
        for k in excess:
            for img in gallery_images.pop(k, []):
                try: Path(img["path"]).unlink(missing_ok=True)
                except Exception: pass

        llm_busy[0] = False; llm_prog.visible = False
        gen_shots_btn.disabled = False; fill_prompts_btn.disabled = False
        _failed = result.get("failed") or []
        status_lbl.value = (f"🎭 Static Pose Fill done — {len(shots_data)} shots" if not _failed else
                            f"⚠ Static Pose Fill — {len(shots_data)} shots, {len(_failed)} without a prompt (shots {', '.join(str(x) for x in _failed)})")
        _outline_review_rebuild()
        _set_outline_phase("READY", status_lbl.value)
        page.update()

    async def _outline_generate_dispatch(ask_shot_count: bool = True, reuse_pass_a: bool = False,
                                         from_review: bool = False):
        """VNEXT-PRODUCTION-PROMOTION-A1 follow-up: the legacy Outline pipeline (_do_generate_outline)
        was deleted as dead code — _VNEXT_DEFAULT is a hardcoded True with no UI toggle, so the
        `else` branch that used to call it could never run. This now always calls vNext directly.
        Manga routes to the dedicated entry point when enabled. That path still uses shared
        run_a1_vnext + run_a2_vnext(mode="sequential"), then branches to B Manga / Fill Manga."""
        # A fresh Outline run (count dialog wanted, A not reused) stops after A so the user can review
        # Character/Events and press Create shot/pages. Callers that pass ask_shot_count=False
        # (Fast Lane, Redo Info, Regenerate on reused A) keep running straight through to B.
        _stop_after_a = bool(ask_shot_count and not reuse_pass_a)
        if _outline_mode[0] == "static_pose":
            # A0 as before: one still picture -> Static Pose Fill; a story (or no answer) -> the normal pipeline below, as Visual Novel.
            if await _static_pose_a0_check() == "STATIC":
                await _do_generate_outline_static_pose(from_review=from_review)
                return
        if _manga_vnext_mode[0]:
            await _do_generate_outline_manga_vnext(
                ask_shot_count=ask_shot_count, reuse_pass_a=reuse_pass_a,
                stop_after_a=_stop_after_a, from_review=from_review)
            return
        await _do_generate_outline_vnext(
            ask_shot_count=ask_shot_count, reuse_pass_a=reuse_pass_a,
            stop_after_a=_stop_after_a, from_review=from_review)

    async def _do_gen_shots_run():
        try:
            await _do_gen_shots_run_inner()
        except Exception:
            import traceback
            _tb = traceback.format_exc()
            print(f"[main_ui] _do_gen_shots_run exception:\n{_tb}")
            await _llm_log(f"❌ _do_gen_shots_run exception:\n{_tb}")
            llm_busy[0] = False; llm_prog.visible = False; gen_shots_btn.disabled = False
            status_lbl.value = "❌ Error — see log"; page.update()

    async def _do_gen_shots_run_inner():
        story = story_field.value.strip()
        llm_wf = llm_dd.value
        try: n = int(num_shots_field.value or "5")
        except ValueError: n = 5
        llm_busy[0] = True; llm_prog.visible = True; gen_shots_btn.disabled = True
        status_lbl.value = "🤖 AI generating shots..."; page.update()

        # ใช้ pipeline outline หลัก (_do_generate_outline) เสมอ ไม่ว่า local หรือ Grok — ไม่ว่า Loop Mode
        # จะติ๊กไว้หรือไม่ (เดิม uncheck Loop Mode จะไปเข้า run_llm_story ซึ่งเป็น schema เก่าที่ไม่มี
        # character/scene/clothing_state เลย) — ask_shot_count=True (default) เพราะนี่คือ generate ใหม่จริง
        async def _outline_run_safe():
            try:
                await _outline_generate_dispatch(ask_shot_count=True)
            except Exception:
                import traceback
                _tb = traceback.format_exc()
                print(f"[main_ui] outline exception:\n{_tb}")
                await _llm_log(f"❌ Outline exception:\n{_tb}")
                llm_busy[0] = False; llm_prog.visible = False; gen_shots_btn.disabled = False
                status_lbl.value = "❌ Outline error — see log"; page.update()
        asyncio.create_task(_outline_run_safe())

    async def do_gen_shots(e):
        if llm_busy[0]: return
        if not story_field.value.strip(): status_lbl.value = "⚠ Enter a story first"; page.update(); return
        if not llm_dd.value: status_lbl.value = "⚠ Select LLM first"; page.update(); return
        if llm_dd.value == _GROK_OPTION and not _grok_api_key[0]:
            status_lbl.value = "⚠ Enter Grok API Key first"; page.update(); return
        genre_dd.value = _sel_genre[0]; genre2_dd.value = _sel_genre2[0]
        # RENDER-STYLE-PERSIST-A1: Style is now a remembered cross-project preference (like the
        # Grok key) — opening Outline Settings no longer resets it to "none"; just mirror the
        # dropdowns to the already-persisted _sel_style[0] (sourced from credentials.json).
        style_dd.value = _sel_style[0]; outline_style_dd.value = _sel_style[0]
        _sync_style()
        _save_state()
        # ต้อง _sync_outline_mode() ไม่ใช่ _apply_manga_prompt_lock() ตรงๆ — lock อ่าน _manga_page_mode[0]
        # ซึ่งอาจค้างเป็น True จากตอนเลือก manga ถ้า Dropdown.on_change ไม่ fire (ดู docstring ของ
        # _sync_outline_mode) ทำให้ Prompt Format ยังถูก disable ทั้งที่ Outline Style เปลี่ยนไปแล้ว
        # _sync_outline_mode() เรียก _apply_manga_prompt_lock() ให้ท้ายสุดอยู่แล้ว
        _sync_outline_mode()
        source_lang_dd.value = _source_lang[0]
        desc_lang_dd.value = _desc_lang[0]
        dialogue_lang_dd.value = _dialogue_lang[0]
        _open_dialog(outline_genre_dialog)
        # ต้อง flush เอง — _sync_outline_mode() แก้ .disabled/.value ของ Prompt Format + Manga Page ฝั่ง
        # server แต่ do_gen_shots เดิมไม่มี page.update() เลยสักบรรทัด (ต่างจาก do_fill_prompts ที่มี)
        # และ _open_dialog() ก็ไม่ได้เรียกให้ ค่าที่แก้จึงไม่เคยถูกส่งไป client — dropdown ค้าง disable ตาม
        # ภาพที่ client จำไว้ตั้งแต่ตอนล็อกครั้งก่อน
        page.update()
    gen_shots_btn.on_click = do_gen_shots

    # ── Edit Outline (open Review Outline with existing shots) ───────
    edit_outline_btn = ft.IconButton(
        icon=ft.Icons.FORMAT_LIST_BULLETED_ROUNDED, icon_color=C["accent"], icon_size=20,
        tooltip="Review Outline — review & fix current shots without regenerating",
        bgcolor="transparent", width=40, height=38,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=8),
            side=ft.BorderSide(1, C["accent"]),
        ),
    )

    def _do_edit_outline(e):
        if not shots_data and _outline_vnext_a1[0] and _outline_vnext_a2[0]:
            # Saved at the review-A stage: A1/A2 exist but no shots yet — reopen where it stopped.
            _outline_review_is_new[0] = False
            _b_run_state[0] = "NOT_STARTED"
            _outline_req_section.visible = False
            _outline_sync_char_env_tabs()
            _outline_refresh_tabs_visibility()
            _open_dialog(outline_review_dialog)
            _outline_review_col.controls.clear()
            _outline_review_col.controls.append(ft.Text(
                "Review the Character and Events tabs, then press "
                + ("Create pages" if _manga_vnext_mode[0] else "Create shot") + ".",
                size=12, color=C["text2"]))
            _set_outline_phase("REVIEW_A", "Review Character and Events, then create the shots")
            page.update()
            return
        if not shots_data:
            status_lbl.value = "⚠ No shots to edit"; page.update(); return
        _outline_review_is_new[0] = False
        # Reopened (not freshly generated) outline: if the Normal vNext A1/A2 that produced these
        # shots are still available, treat B as already finished so Debug "Refresh B" can rerun it.
        # The final count is the number of shots currently in the outline.
        if (_b_run_state[0] == "NOT_STARTED" and not _manga_vnext_mode[0]
                and _outline_vnext_a1[0] and _outline_vnext_a2[0]):
            _b_run_state[0] = "FINISHED_SUCCESS"
            _b_final_n[0] = len(shots_data)
        _set_outline_phase("READY", "")
        _outline_req_section.visible = False
        # เส้นนี้เข้ามาพร้อมช็อตเดิมครบอยู่แล้ว ไม่ได้กำลังสร้างใหม่ — ไม่ต้องมี Regenerate
        _outline_review_regen_btn.visible = False
        _outline_sync_char_env_tabs()
        _outline_refresh_tabs_visibility()
        _open_dialog(outline_review_dialog)
        _outline_review_rebuild()
        page.update()

    edit_outline_btn.on_click = _do_edit_outline

    # ── Insert Shot ───────────────────────────────────────────────────
    insert_shot_btn = ft.IconButton(
        icon=ft.Icons.ADD_BOX_ROUNDED, icon_color=C["green"], icon_size=20,
        tooltip="Add Shot", bgcolor="transparent",
        width=40, height=38,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=8),
            side=ft.BorderSide(1, C["green"]),
        ),
    )

    # The editor fills the dialog's whole height (expand=True inside a fixed-height column).
    _insert_instruction = ft.TextField(
        hint_text="Describe the shot to insert, e.g. 'hero discovers a secret room'",
        multiline=True, expand=True, min_lines=None, max_lines=None,
        text_size=13, border_color=C["border"], focused_border_color=C["accent"],
        border_radius=10, bgcolor=C["surface"],
        content_padding=po(left=12, right=12, top=10, bottom=10),
    )
    _insert_pos_dd = ft.Dropdown(
        options=[ft.dropdown.Option("0", "Insert before Shot 1")],
        value="0", text_size=12, height=40, width=230,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=10, right=4, top=0, bottom=0),
    )
    # who is in the story — names read from the character sheet's <charN.name> tags
    _insert_chars_row = ft.Row([], wrap=True, spacing=6, run_spacing=6)

    def _refresh_insert_characters():
        names = [n.strip() for n in re.findall(r"<char\d+\.name>(.*?)</char\d+\.name>",
                                               char_field.value or "", flags=re.S) if n.strip()]
        _insert_chars_row.controls = [
            ft.Container(
                content=ft.Text(n, size=11, color=C["purple"], weight=ft.FontWeight.W_600),
                bgcolor=C["purple_lt"], border_radius=12,
                padding=po(left=10, right=10, top=3, bottom=3),
            ) for n in names
        ] or [ft.Text("No characters listed yet", size=11, color=C["text3"], italic=True)]
    _insert_status = ft.Text("", size=10, color=C["text3"])
    _insert_prog = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)
    _insert_clarify_col = ft.Column([], spacing=10, visible=False, scroll=ft.ScrollMode.AUTO, height=190)
    _insert_phase1_row = ft.Column([], spacing=6)  # instruction + position (shown in phase 1)
    _insert_confirm_btn = ft.FilledButton(
        content=ft.Row([ft.Icon(ft.Icons.ADD_ROUNDED, size=15, color="white"),
                        ft.Text("Create Shot", size=12, color="white", weight=ft.FontWeight.W_600)],
                       tight=True, spacing=5),
        height=40,
        style=ft.ButtonStyle(bgcolor=C["green"], shape=ft.RoundedRectangleBorder(radius=8)),
    )
    _insert_answer_btn = ft.ElevatedButton(
        "Submit Answer", height=40,
        style=ft.ButtonStyle(bgcolor=C["accent"], color="white"),
        visible=False,
    )

    # state ระหว่าง phase
    _insert_state: dict = {}  # เก็บ instruction, insert_after, questions dds

    def _refresh_insert_pos_options():
        opts = [ft.dropdown.Option("0", "Insert before Shot 1")]
        for i in range(len(shots_data)):
            opts.append(ft.dropdown.Option(str(i + 1), f"Insert after Shot {i + 1}"))
        _insert_pos_dd.options = opts
        _insert_pos_dd.value = str(len(shots_data))

    def _do_finalize(shot_obj: dict, insert_after: int):
        """Insert shot_obj into shots_data at insert_after and rebuild UI.

        NORMAL-VNEXT-SCHEMA-CLEANUP-A1 §5: run_insert_shot()'s LLM reply only fills the fields it
        can reason about (visible_people/location/shot_desc/clothing/objects/camera/lighting/
        narration/speech_char/subtitle) — complete it into the app's full native shot shape (same
        keys _do_insert_shot's blank template uses) so downstream code (Raw JSON, Fill routing,
        renderer) sees a shot with the same field set as any other, not a partial dict. Any stray
        key the LLM invents outside this set is dropped rather than carried into shots_data."""
        # Insert Shot writes B truth only, in the current B Normal shape (no location/clothing/
        # camera/lighting authoring fields); Fill produces the prompt later like for any B shot.
        _complete = {"shot": 0, "uses": [], "visible_people": "", "shot_desc": "", "story": "",
                     "objects": "", "narration": "", "speech_char": "", "subtitle": "", "prompt": ""}
        _complete.update({k: v for k, v in shot_obj.items() if k in _complete})
        shot_obj = _complete
        new_gallery: dict = {}
        for k, v in gallery_images.items():
            new_gallery[k + 1 if k >= insert_after else k] = v
        gallery_images.clear(); gallery_images.update(new_gallery)
        shots_data.insert(insert_after, shot_obj)
        for _idx, _s in enumerate(shots_data):
            _s["shot"] = _idx + 1
        insert_dlg.open = False
        _save_state(); rebuild_shots_col()
        status_lbl.value = f"✅ Shot inserted — total {len(shots_data)} shots"
        page.update()

    async def _do_insert_phase1(e):
        instr = _insert_instruction.value.strip()
        if not instr:
            _insert_status.value = "⚠ Enter a shot description first"; page.update(); return
        if llm_busy[0]:
            _insert_status.value = "⚠ LLM is busy"; page.update(); return
        try:
            insert_after = int(_insert_pos_dd.value or "0")
        except ValueError:
            insert_after = len(shots_data)

        llm_busy[0] = True; _insert_prog.visible = True
        _insert_status.value = "🤖 AI analyzing..."; _insert_confirm_btn.disabled = True
        page.update()
        _sync_genre()

        result = await run_insert_shot(
            instruction=instr, insert_after=insert_after,
            shots_data=shots_data,
            story_text=story_field.value.strip(),
            char_desc=char_field.value.strip(),
            scene_desc=scene_field.value.strip(),
            story_summary=_story_summary[0],
            llm_wf_file=llm_dd.value,
            dialogue_style=_cur_dlg_style(), narration_type=_narration_type[0],
            dialogue_lang=_dialogue_lang[0], desc_lang=_desc_lang[0], source_lang=_source_lang[0],
            genre=_sel_genre[0],
            on_log=_llm_log,
            props_desc=_props_to_str(props_field.value).strip(),
        )

        llm_busy[0] = False; _insert_prog.visible = False; _insert_confirm_btn.disabled = False

        if result.get("error"):
            _insert_status.value = f"❌ {result['error']}"; page.update(); return

        if not result.get("needs_clarification", False):
            # ไม่ต้องถาม → insert ทันที
            _do_finalize(result.get("shot", result), insert_after)
            return

        # LLM ถามกลับ → แสดง dropdowns
        questions = result.get("questions", [])
        if not questions:
            _do_finalize(result.get("shot", {}), insert_after)
            return

        _insert_state["instruction"] = instr
        _insert_state["insert_after"] = insert_after
        _insert_state["q_dds"] = []

        _insert_clarify_col.controls.clear()
        _insert_clarify_col.controls.append(
            ft.Text("AI needs more information:", size=11, weight=ft.FontWeight.W_600, color=C["accent"])
        )
        for q in questions:
            q_id = q.get("id", q.get("question", "")[:20])
            q_text = q.get("question", "")
            opts = q.get("options", [])
            dd = ft.Dropdown(
                label=q_text, value=opts[0] if opts else None,
                options=[ft.dropdown.Option(o) for o in opts],
                text_size=11, height=40,
                border_color=C["border"], focused_border_color=C["accent"],
                bgcolor=C["surface"],
                content_padding=po(left=8, right=4, top=0, bottom=0),
            )
            _insert_state["q_dds"].append((q_id, dd))
            _insert_clarify_col.controls.append(dd)

        _insert_clarify_col.visible = True
        _insert_confirm_btn.visible = False
        _insert_answer_btn.visible = True
        _insert_status.value = "Answer the questions above then click 'Submit Answer'"
        page.update()

    async def _do_insert_phase2(e):
        if llm_busy[0]:
            _insert_status.value = "⚠ LLM is busy"; page.update(); return
        answers = {q_id: dd.value or "" for q_id, dd in _insert_state.get("q_dds", [])}
        insert_after = _insert_state.get("insert_after", len(shots_data))
        instr = _insert_state.get("instruction", "")

        llm_busy[0] = True; _insert_prog.visible = True
        _insert_status.value = "🤖 AI creating shot..."; _insert_answer_btn.disabled = True
        page.update()
        _sync_genre()

        result = await run_insert_shot(
            instruction=instr, insert_after=insert_after,
            shots_data=shots_data,
            story_text=story_field.value.strip(),
            char_desc=char_field.value.strip(),
            scene_desc=scene_field.value.strip(),
            story_summary=_story_summary[0],
            llm_wf_file=llm_dd.value,
            dialogue_style=_cur_dlg_style(), narration_type=_narration_type[0],
            dialogue_lang=_dialogue_lang[0], desc_lang=_desc_lang[0], source_lang=_source_lang[0],
            genre=_sel_genre[0],
            clarification_answers=answers,
            on_log=_llm_log,
            props_desc=_props_to_str(props_field.value).strip(),
        )

        llm_busy[0] = False; _insert_prog.visible = False; _insert_answer_btn.disabled = False

        if result.get("error"):
            _insert_status.value = f"❌ {result['error']}"; page.update(); return

        _do_finalize(result.get("shot", result), insert_after)

    _insert_confirm_btn.on_click = lambda e: asyncio.create_task(_do_insert_phase1(e))
    _insert_answer_btn.on_click = lambda e: asyncio.create_task(_do_insert_phase2(e))

    def _open_insert_dlg(e):
        _refresh_insert_pos_options()
        _refresh_insert_characters()
        _insert_status.value = ""; _insert_instruction.value = ""
        _insert_clarify_col.controls.clear(); _insert_clarify_col.visible = False
        _insert_confirm_btn.visible = True; _insert_answer_btn.visible = False
        _insert_state.clear()
        insert_dlg.open = True
        page.update()

    _INSERT_W = 640
    insert_dlg = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Row([
            ft.Icon(ft.Icons.ADD_BOX_ROUNDED, color=C["green"], size=18),
            ft.Text("Insert Shot", size=14, weight=ft.FontWeight.W_700, color=C["text1"]),
        ], spacing=8),
        content=ft.Container(
            width=_INSERT_W, height=460,
            content=ft.Column([
                ft.Text("CHARACTERS IN THIS STORY", size=10, weight=ft.FontWeight.W_700, color=C["text3"]),
                _insert_chars_row,
                ft.Text("SHOT DESCRIPTION", size=10, weight=ft.FontWeight.W_700, color=C["text3"]),
                _insert_instruction,
                _insert_clarify_col,
                ft.Row([_insert_prog, _insert_status], spacing=8,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], spacing=8, expand=True),
        ),
        # Cancel on the left; the position picker sits right next to the action button. The Row needs
        # the same explicit width as the content: AlertDialog does not stretch its actions row.
        actions=[
            ft.Row([
                ft.TextButton("Cancel", on_click=lambda e: (setattr(insert_dlg, "open", False), page.update()),
                             style=ft.ButtonStyle(color=C["text2"])),
                ft.Container(expand=True),
                _insert_pos_dd,
                _insert_answer_btn,
                _insert_confirm_btn,
            ], width=_INSERT_W, spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ],
        actions_alignment=ft.MainAxisAlignment.START,
        content_padding=po(left=24, right=24, top=12, bottom=12),
        actions_padding=po(left=24, right=24, top=4, bottom=20),
    )
    page.overlay.append(insert_dlg)
    insert_shot_btn.on_click = _open_insert_dlg

    # ── Raw JSON dialog ───────────────────────────────────────────────
    def _fit_dialog(box: ft.Container, max_w: int, max_h: int):
        """ย่อกล่องเนื้อหาของ dialog ให้พอดีหน้าต่างปัจจุบัน

        ขนาดตายตัวใหญ่กว่าหน้าต่างเมื่อไหร่ แถวปุ่มด้านล่าง (Close/Save) จะถูกดันออกนอกจอ
        มองเห็นขอบปุ่มอยู่แต่กดไม่โดน — อาการคือ "ปุ่ม Close ไม่ทำงาน" ทั้งที่โค้ดปุ่มถูกต้อง
        เผื่อที่ให้หัวเรื่องกับแถวปุ่มไว้ด้วย ไม่ใช่ให้เนื้อหากินทั้งหน้าต่าง"""
        _w = page.width or getattr(page.window, "width", None) or 1200
        _h = page.height or getattr(page.window, "height", None) or 800
        box.width = int(min(max_w, max(360, _w - 80)))
        box.height = int(min(max_h, max(240, _h - 190)))

    raw_editor = ft.TextField(multiline=True, text_size=11, expand=True,
                              border_color=C["border"], focused_border_color=C["accent"], bgcolor=C["surface"],
                              content_padding=po(left=10, right=10, top=8, bottom=8))
    raw_status = ft.Text("", size=10, color=C["text3"])

    def _clean_ai_json(text: str) -> str:
        """AI มักตอบมาพร้อม ```json fence หรือมีประโยคนำ/ปิดท้าย — ทำให้ json.loads ตรง ๆ fail
        หยิบเฉพาะ fenced block แรก (ถ้ามี) แล้วหยิบ {...} object แรกถ้ายังมีคำพูดเหลืออยู่
        ใช้ร่วมกันทั้ง _do_merge, _paste_merge และ Raw Save/Import — จุดเดียวที่ล้าง AI text"""
        t = (text or "").strip()
        if "```" in t:
            m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
            if m: t = m.group(1).strip()
        if not t.startswith("{"):
            i = t.find("{")
            if i >= 0: t = t[i:t.rfind("}") + 1]
        return t

    # snapshot ชั้นเดียวก่อน merge/save/import ทุกครั้ง — พอให้ Undo กลับสถานะก่อนหน้าได้ 1 ก้าว
    # (ไม่ต้องเก็บ history ทั้งสาย แค่กันเคส "AI ตอบมาผิด/user วางเลข shot คลาดเคลื่อน" ให้ย้อนได้)
    _raw_undo: list = [None]
    # RAW-MODE-SAFETY-FINAL-A1: parallel snapshot of _outline_mode[0] taken at the exact same
    # moment as _raw_undo[0] — Undo must restore the outline mode along with the project data,
    # or undoing a Manga import leaves the app stuck in Manga mode even after the data itself
    # reverts. Pure snapshot/storage, read back into _outline_mode[0] on Undo only — never an
    # independent routing authority of its own.
    _raw_undo_mode: list = [None]

    def _build_raw_data() -> dict:
        return {"story": story_field.value,
                "character": char_field.value, "scene": scene_field.value,
                "locations": _outline_locations[0] or "", "props": props_field.value,
                "events": list(_outline_events[0] or []),
                "scenario_script": list(_outline_scenario_script[0] or []),
                "source_mode": _outline_source_mode[0] or "",
                "source_blocks": list(_outline_source_blocks[0] or []),
                "clothing_arc": _outline_clothing_arc[0] or "",
                "physical_state_plan": _outline_physical_state[0] or "",
                # NORMAL-VNEXT-SCHEMA-CLEANUP-A1 §5/6: Raw JSON serializes B's native shot
                # vocabulary only — shot/uses/visible_people/location/shot_desc/clothing/objects/
                # camera/lighting/narration/speech_char/subtitle/prompt. No legacy aliases
                # (scene_desc/clothing_state/prop_state/place/shot_scale/light_sources/speech_text/
                # covers) and no obsolete fields (expression/spacing/mood/env/time_of_day/moment/
                # world_interaction) are round-tripped through this dialog any more.
                #
                # MANGA-VNEXT-RAW-JSON-SHAPE-A1: a Manga vNext shot's canonical truth is
                # shot["_manga_page_spec"] (page/identity_lock/location_lock/object_lock/panels[]),
                # never the Normal native fields above — manga_vnext_page_to_legacy_dict() only
                # synthesizes OLD legacy-named display fields (scene_desc/clothing_state/place/...)
                # for Review Outline UI compatibility, none of which match the NEW native names this
                # dict reads (shot_desc/clothing/location/subtitle/...). Serializing a Manga row
                # through the Normal branch below would therefore export empty flat fields and
                # silently drop the actual Manga content. Detect Manga rows by the presence of
                # "_manga_page_spec" and serialize the spec verbatim instead — never reconstructed
                # from the legacy display fields, never coerced into Normal shape.
                "shots": [_build_raw_shot(s, i) for i, s in enumerate(shots_data)]}

    def _build_raw_shot(s: dict, i: int) -> dict:
        if isinstance(s.get("_manga_page_spec"), dict):
            return {"shot": s.get("shot", i + 1),
                    "_manga_page_spec": copy.deepcopy(s["_manga_page_spec"]),
                    "prompt": s.get("prompt", ""),
                    "_fill_engine": s.get("_fill_engine", ""),
                    "prompt_style": s.get("prompt_style", "")}
        return {"shot": s.get("shot", i+1),
                "uses": list(s.get("uses") or []),
                "visible_people": s.get("visible_people", ""),
                "location": s.get("location", ""),
                "shot_desc": s.get("shot_desc", ""),
                "story": s.get("story", ""),
                "clothing": s.get("clothing", ""),
                "objects": s.get("objects", ""),
                "camera": s.get("camera", ""),
                "lighting": s.get("lighting", ""),
                "narration": s.get("narration", ""),
                "speech_char": s.get("speech_char", ""),
                "subtitle": s.get("subtitle", ""),
                "prompt": s.get("prompt", "")}

    def _apply_raw_data(data: dict):
        # NORMAL-VNEXT-SCHEMA-CLEANUP-A1 §5/6: Save/Import reads the app's own native shot
        # vocabulary directly (shot_desc/subtitle/etc.) — no legacy-name lookups here any more.
        # _normalize_external_compact_payload still runs first for the unrelated
        # character_sheet -> character top-level rename (see that function's docstring).
        data = _normalize_external_compact_payload(data)
        # RAW-MODE-SAFETY-FINAL-A1: pre-flight structural shape check BEFORE any mutation of
        # project state (shots_data.clear(), top-level field assignment, mode). A payload whose
        # "shots" mixes Manga rows (isinstance(row.get("_manga_page_spec"), dict)) and Normal rows
        # (no Manga canonical spec) is rejected outright — no partial import, unlike the previous
        # warn-and-continue behavior this replaces. Purely structural, never prompt_style/prose.
        if "shots" in data and isinstance(data["shots"], list):
            _preflight_manga_ct = sum(
                1 for s in data["shots"]
                if isinstance(s, dict) and isinstance(s.get("_manga_page_spec"), dict)
            )
            _preflight_total_ct = len(data["shots"])
            if 0 < _preflight_manga_ct < _preflight_total_ct:
                return "Mixed Manga/Normal Raw JSON is not supported."
        if "story" in data:
            story_field.value = data["story"]
        if "story_summary" in data:   # no longer exported; an older Raw JSON may still carry it, and a missing key must not clear the field
            _s = data.get("story_summary", "")
            story_summary_field.value = _s; _story_summary[0] = _s
        if "character" in data: char_field.value = data["character"]
        if "scene" in data: scene_field.value = data["scene"]
        if "locations" in data: _outline_locations[0] = str(data.get("locations") or "")
        if "props" in data: props_field.value = _props_to_str(data["props"])
        if "events" in data and isinstance(data["events"], list):
            _outline_events[0] = [str(x) for x in data["events"]]
        if "scenario_script" in data and isinstance(data["scenario_script"], list):
            _outline_scenario_script[0] = [
                dict(u) for u in data["scenario_script"] if isinstance(u, dict)
            ]
        if "source_mode" in data:
            _outline_source_mode[0] = str(data.get("source_mode") or "")
        if "source_blocks" in data and isinstance(data["source_blocks"], list):
            _outline_source_blocks[0] = [
                dict(b) for b in data["source_blocks"] if isinstance(b, dict)
            ]
        if "clothing_arc" in data:
            _outline_clothing_arc[0] = str(data.get("clothing_arc") or "")
        if "physical_state_plan" in data:
            _outline_physical_state[0] = str(data.get("physical_state_plan") or "")
        _raw_import_warning = None
        if "shots" in data:
            shots_data.clear()
            for s in data["shots"]:
                # MANGA-VNEXT-RAW-JSON-SHAPE-A1: mirror _build_raw_data's export shape — a raw
                # shot carrying "_manga_page_spec" is Manga vNext canonical truth and must round-
                # trip back through manga_vnext_page_to_legacy_dict() (the same reconstruction
                # used everywhere else a Manga row enters shots_data, e.g. Outline Review rebuild)
                # rather than being read through the Normal native field names below, which would
                # silently coerce it into an empty-fielded Normal row and lose the spec.
                if isinstance(s.get("_manga_page_spec"), dict):
                    shots_data.append(manga_vnext_page_to_legacy_dict({
                        "shot": s.get("shot"),
                        "_manga_page_spec": copy.deepcopy(s["_manga_page_spec"]),
                        "prompt": s.get("prompt", ""),
                        "_fill_engine": s.get("_fill_engine", ""),
                        "prompt_style": s.get("prompt_style", ""),
                    }))
                    continue
                s = _normalize_external_shot_fields(s)
                shots_data.append({
                    "shot": s.get("shot"),
                    "uses": _norm_merge_shot_field("uses", s.get("uses", [])),
                    "visible_people": _norm_merge_shot_field("visible_people", s.get("visible_people", "")),
                    "location": _norm_merge_shot_field("location", s.get("location", "")),
                    "shot_desc": _norm_merge_shot_field("shot_desc", s.get("shot_desc", "")),
                    "story": _norm_merge_shot_field("story", s.get("story", "")),
                    "clothing": _norm_merge_shot_field("clothing", s.get("clothing", "")),
                    "objects": _norm_merge_shot_field("objects", s.get("objects", "")),
                    "camera": _norm_merge_shot_field("camera", s.get("camera", "")),
                    "lighting": _norm_merge_shot_field("lighting", s.get("lighting", "")),
                    "narration": _norm_merge_shot_field("narration", s.get("narration", "")),
                    "speech_char": _norm_merge_shot_field("speech_char", s.get("speech_char", "")),
                    "subtitle": _norm_merge_shot_field("subtitle", s.get("subtitle", "")),
                    "prompt": _norm_merge_shot_field("prompt", s.get("prompt", "")),
                })
            # RAW-MODE-SAFETY-FINAL-A1: keep the app's own outline/routing mode in sync with what
            # was JUST imported (data["shots"], not the post-loop shots_data) — purely structural
            # (isinstance(..., dict) on "_manga_page_spec"), never prompt_style/prose. Mixed rows
            # can no longer reach this point — the pre-flight check above already rejected them
            # before any mutation, so only all-Manga / all-Normal / empty remain possible here.
            # All-Manga -> flip _outline_mode to "manga" and let _sync_outline_mode() (the sole
            # propagation authority) cascade into _manga_vnext_mode/UI/Fill routing. All-Normal ->
            # actively force OUT of Manga if that's where mode currently sits (routing must not
            # remain Manga for a Normal project); if already some Normal mode (cinematic/
            # sequential/...), preserve that exact choice — never infer cinematic vs sequential
            # from content, the only signal is "was it manga before, yes/no".
            _manga_shot_ct = sum(
                1 for s in data["shots"] if isinstance(s.get("_manga_page_spec"), dict)
            )
            _total_shot_ct = len(data["shots"])
            if _total_shot_ct > 0 and _manga_shot_ct == _total_shot_ct:
                _outline_mode[0] = "manga"
                _sync_outline_mode()
            elif _total_shot_ct > 0 and _manga_shot_ct == 0:
                if _outline_mode[0] == "manga":
                    _outline_mode[0] = "cinematic"
                    _sync_outline_mode()
                # else: already a valid Normal mode (e.g. "cinematic"/"sequential") — leave as-is.
            # else: empty "shots" — _outline_mode left exactly as it was.
        _outline_sync_char_env_tabs()
        _save_state(); rebuild_shots_col()
        return _raw_import_warning

    # ฟิลด์ของช็อตที่ merge ยอมให้ทับได้ — ตรงกับที่ _build_raw_data ส่งออก (native B schema เท่านั้น
    # NORMAL-VNEXT-SCHEMA-CLEANUP-A1 §5/6 — ไม่มี legacy alias/obsolete field เหลืออยู่แล้ว)
    _SHOT_MERGE_FIELDS = ("uses", "visible_people", "location", "shot_desc", "story", "clothing",
                          "objects", "camera", "lighting", "narration", "speech_char",
                          "subtitle", "prompt")

    def _new_merge_shot() -> dict:
        """ช็อตเปล่าตามโครงที่แอปใช้จริง — ที่เดียวที่กำหนดค่าเริ่มต้นของช็อตที่ merge สร้างขึ้น
        เลข shot ใส่ทีหลังตอน renumber (ตำแหน่งเป็นตัวกำหนด ไม่ใช่ค่าที่ payload ส่งมา)"""
        _s = {"shot": 0}
        for _f in _SHOT_MERGE_FIELDS:
            _s[_f] = ""
        return _s

    def _norm_merge_shot_field(field: str, value):
        """ค่าที่ payload ส่งมา ต้องกลายเป็น type ที่ฟิลด์นั้นใช้จริงในแอป

        ทุกฟิลด์ของช็อตเก็บเป็นสตริง (ดู _build_raw_data / _apply_raw_data) — uses ที่ AI
        ชอบส่งมาเป็น list ("uses": ["E1","E1.2"]) จึงต้องแปลงเป็น "E1, E1.2" ไม่ใช่เก็บ list ดิบไว้
        ให้โค้ดปลายทางที่คาด str/list (ดู shot.get("uses") ใน llm_engine.py) อ่านผิดทีหลัง
        None = ค่าว่าง ไม่ใช่คำว่า "None" """
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(_v).strip() for _v in value if str(_v).strip())
        elif not isinstance(value, str):
            value = str(value)
        # V3-PROMPT-BLOCK-SPACING-NORMALIZATION-A1: presentation-only whitespace fix, applied at
        # the single shared ingress every Raw Save/Import/Merge/Paste & Merge/Chat-merge path
        # already funnels shot fields through — never touches semantics, no-ops on anything not
        # unambiguously a valid V3 narrative skeleton (see the function's own docstring).
        if field == "prompt":
            value = _normalize_v3_prompt_block_spacing(value)
        return value

    def _shift_gallery_for_insert(insert_index: int) -> None:
        """เลื่อน index ของแกลเลอรีเมื่อมีช็อตใหม่แทรกที่ตำแหน่ง insert_index

        gallery_images ผูกกับ "ตำแหน่งในลิสต์" ไม่ใช่เลขช็อต ถ้าลิสต์ขยับแล้วอันนี้ไม่ขยับตาม
        ภาพทั้งหมดตั้งแต่จุดแทรกจะไปติดช็อตผิดแบบกู้คืนไม่ได้ — ต้องเรียกก่อน shots_data.insert()"""
        _new_gallery = {}
        for _k, _v in gallery_images.items():
            _new_gallery[_k + 1 if _k >= insert_index else _k] = _v
        gallery_images.clear()
        gallery_images.update(_new_gallery)

    # top-level key ที่ merge รู้จัก — key แปลกที่ไม่อยู่ในนี้ถือว่า "AI สะกดผิด/schema ผิด" (A5)
    _KNOWN_TOP_MERGE_KEYS = {"story", "story_summary", "character", "scene", "props",
                             "events", "clothing_arc", "physical_state_plan", "shots"}

    # EXTERNAL-CHARACTER-SHEET-ADAPTER-A1: the external AI's compact contract (helper/your_ai.txt
    # section C) is already native-schema (shot_desc/subtitle etc. — see that file's "PROGRAM JSON
    # CONTRACT" section) except for one top-level name: "character_sheet" -> "character". This is
    # a pure literal key rename at the paste/merge/save ingress, applied BEFORE the existing
    # merge/apply machinery ever runs — it never touches _SHOT_MERGE_FIELDS/_KNOWN_TOP_MERGE_KEYS,
    # calls no LLM, and infers nothing from the values (the string is transported verbatim, never
    # split/parsed in Python).
    #
    # NORMAL-VNEXT-SCHEMA-CLEANUP-A1 §5 fix: this function used to ALSO rename shot-level
    # "shot_desc" -> "scene_desc" and "subtitle" -> "speech_text" — a compatibility shim from when
    # shots_data was still legacy-shaped. Now that shots_data IS the native schema (shot_desc/
    # subtitle are the real, current field names — see B's own output at shots_data.append() in
    # the outline pipeline), that rename was backwards: it silently corrupted native-shaped
    # incoming JSON (a fresh "Copy for AI" round-trip, or a user pasting the app's own Raw JSON
    # back) into obsolete legacy keys nothing downstream reads any more. Removed. Per this
    # migration's established principle, old external-shape JSON that still uses scene_desc/
    # speech_text is NOT translated back — it is accepted as-is and simply won't populate
    # shot_desc/subtitle (graceful degradation, not a repair feature).
    def _normalize_external_compact_payload(obj):
        if not isinstance(obj, dict):
            return obj
        if "character_sheet" in obj:
            obj["character"] = obj.pop("character_sheet")
        return obj

    # RAW-JSON-LEGACY-SHOT-FIELD-ALIAS-A1: per-shot legacy field names from before the native-schema
    # migration (see the NORMAL-VNEXT-SCHEMA-CLEANUP-A1 note above) — accepted as aliases for their
    # native equivalent so old-shaped pasted/imported JSON (e.g. from an external AI, or an older
    # saved file) still populates the right field. Whichever key is actually present is used; when
    # BOTH the native and legacy key are present on the same shot, the native key wins and the
    # legacy one is left alone (reported as an ignored unknown field, same as any other stray key).
    # Pure key rename — never touches the value, never infers, never invented for fields with no
    # native successor (expression/spacing/mood/env/time_of_day/moment/world_interaction/covers).
    _LEGACY_SHOT_FIELD_ALIASES = {
        "place": "location",
        "scene_desc": "shot_desc",
        "clothing_state": "clothing",
        "prop_state": "objects",
        "shot_scale": "camera",
        "light_sources": "lighting",
        "speech_text": "subtitle",
    }

    def _normalize_external_shot_fields(shot):
        if not isinstance(shot, dict):
            return shot
        for _legacy, _native in _LEGACY_SHOT_FIELD_ALIASES.items():
            if _native not in shot and _legacy in shot:
                shot[_native] = shot.pop(_legacy)
        return shot

    def _merge_raw_data(data: dict) -> str:
        """อัปเดตเฉพาะสิ่งที่ JSON ส่งมา — ที่ไม่ได้ส่งมาไม่ถูกแตะ คืนข้อความสรุปว่าทำอะไรไปบ้าง

        ใช้กับกรณีที่เอาโปรเจกต์ไปคุยกับ AI ตัวอื่น แล้วมันส่งกลับมาแค่บางส่วน (เช่น 6 ช็อตจาก 36)
        ถ้าใช้ Save ทับ ช็อตที่เหลือจะหายทั้งหมด — Merge จึงจับคู่ด้วยเลข "shot" แล้วทับเฉพาะฟิลด์
        ที่มีมาจริง ช็อตที่ไม่มีอยู่เดิมถือเป็นช็อตใหม่ แล้วเรียงลำดับใหม่ตามเลขช็อต

        EXTERNAL-CHARACTER-SHEET-ADAPTER-A1: data is normalized first (character_sheet ->
        character at the top level only) — the external AI's shot fields are already the app's own
        native shape (shot_desc/subtitle/etc.), so the merge machinery below needs no shot-level
        translation any more."""
        data = _normalize_external_compact_payload(data)
        _top_done: list = []
        _warn: list = []
        for _k in data.keys():
            if _k not in _KNOWN_TOP_MERGE_KEYS:
                _warn.append(f"ignored unknown top-level field: {_k}")
        if "story" in data:
            story_field.value = str(data["story"] or ""); _top_done.append("story")
        if "story_summary" in data:
            _s = str(data["story_summary"] or "")
            story_summary_field.value = _s; _story_summary[0] = _s
            _top_done.append("story_summary")
        if "character" in data:
            char_field.value = str(data["character"] or ""); _top_done.append("character")
        if "scene" in data:
            scene_field.value = str(data["scene"] or ""); _top_done.append("scene")
        if "props" in data:
            props_field.value = _props_to_str(data["props"]); _top_done.append("props")
        if isinstance(data.get("events"), list):
            _outline_events[0] = [str(x) for x in data["events"]]; _top_done.append("events")
        if "clothing_arc" in data:
            _outline_clothing_arc[0] = str(data.get("clothing_arc") or "")
            _top_done.append("clothing_arc")
        if "physical_state_plan" in data:
            _outline_physical_state[0] = str(data.get("physical_state_plan") or "")
            _top_done.append("physical_state_plan")

        _updated: list = []
        _inserted: list = []
        _touched_fields: set = set()   # ฟิลด์ที่โดนแก้จริง (ทุก shot รวมกัน) — ใช้ตัดสิน hint ท้าย A7
        _incoming = data.get("shots")
        if isinstance(_incoming, list):
            _by_num = {}
            for _i, _s0 in enumerate(shots_data):
                try:
                    _by_num[int(_s0.get("shot"))] = _i
                except (TypeError, ValueError):
                    continue
            # เลขทุกตัวใน payload อ้างถึงสภาพ "ก่อน merge" เสมอ จึงทำ update ให้จบก่อน แล้วค่อยแทรก
            # และแทรกจากตำแหน่งท้ายมาหน้า — แทรกจากหน้าก่อนจะดันตำแหน่งของ insert ตัวถัดไปให้เพี้ยน
            _inserts: list = []
            # "shot" ที่ต่อท้ายพอดี (เช่นมี 3 shot อยู่ แล้ว payload ส่ง shot 4,5,6,... มาต่อเนื่อง
            # ไม่มีช่องว่าง) ถือว่าเป็นการ "ต่อเรื่อง" ไม่ใช่เลขจำผิด จึงสร้างต่อท้ายให้อัตโนมัติได้
            # ปลอดภัย — ต่างจากเลขที่ไม่ต่อเนื่อง/กระโดดข้าม ซึ่งยังคงเดาไม่ให้เหมือนเดิม (เสี่ยงเป็น
            # เลขที่ AI จำผิดจริง ๆ)
            _next_append = len(shots_data) + 1
            _auto_appends: list = []
            for _new in _incoming:
                if not isinstance(_new, dict):
                    _warn.append("an entry in \"shots\" is not a JSON object — skipped")
                    continue
                _new = _normalize_external_shot_fields(_new)
                _has_shot = _new.get("shot") is not None
                _has_ins  = _new.get("insert_after") is not None
                if _has_shot and _has_ins:
                    _warn.append("an entry has both \"shot\" and \"insert_after\" — skipped "
                                 "(use \"shot\" to update, \"insert_after\" to create)")
                    continue
                if _has_ins:
                    try:
                        _pos = int(_new.get("insert_after"))
                    except (TypeError, ValueError):
                        _warn.append(f"insert_after {_new.get('insert_after')!r} is not a number — skipped")
                        continue
                    # 0 = แทรกหน้าสุด, len(shots_data) = ต่อท้าย — นอกช่วงนี้ไม่เดาให้ ไม่ clamp
                    if not (0 <= _pos <= len(shots_data)):
                        _warn.append(f"insert_after {_pos} is outside the valid range "
                                     f"(0–{len(shots_data)}) — skipped")
                        continue
                    _inserts.append((_pos, _new))
                    continue
                if not _has_shot:
                    _warn.append("an entry has neither \"shot\" nor \"insert_after\" — skipped")
                    continue
                try:
                    _num = int(_new.get("shot"))
                except (TypeError, ValueError):
                    _warn.append(f"shot {_new.get('shot')!r} is not a number — skipped")
                    continue
                if _num not in _by_num:
                    if _num == _next_append:
                        # ต่อท้ายพอดี ไม่มีช่องว่าง — สร้างช็อตใหม่ให้เลย ไม่ต้องรอ insert_after
                        _auto_appends.append(_new)
                        _next_append += 1
                        continue
                    # เลขที่ไม่ต่อเนื่องกับท้ายสุดคือ AI จำเลขผิด ไม่ใช่คำสั่งสร้างช็อต — การสร้าง
                    # เงียบ ๆ จะดันตำแหน่งช็อตอื่นโดยที่แกลเลอรีไม่ขยับตาม
                    _warn.append(f"Shot {_num} not found — skipped. "
                                 f"Use \"insert_after\" to create a new shot.")
                    continue
                _tgt = shots_data[_by_num[_num]]
                # MANGA-VNEXT-PROMPT-ONLY-MERGE-A1: canonical truth for a Manga vNext row is
                # _tgt["_manga_page_spec"], which this path still can't write (no safe translation
                # from a flat payload into page/panels) — but "prompt" is pure presentation for a
                # Manga row exactly as it is for a Normal one: Fill Manga never reads it back as
                # input, only regenerates it, so overwriting it here behaves the same as a manual
                # prompt edit on a Normal shot — it stands until Fill Manga runs again. Any other
                # field (shot_desc/clothing/objects/...) doesn't exist in a Manga row's own schema
                # at all, so those still fail closed instead of being written onto the row.
                if isinstance(_tgt.get("_manga_page_spec"), dict) or isinstance(_new.get("_manga_page_spec"), dict):
                    if isinstance(_new.get("_manga_page_spec"), dict):
                        _warn.append(f"Shot {_num} is a Manga vNext row — raw Merge cannot write "
                                     f"_manga_page_spec (use Save to replace the whole file, or edit "
                                     f"via Outline Review) — skipped")
                        continue
                    _other = [f for f in _new if f not in ("shot", "prompt")]
                    if "prompt" in _new:
                        _tgt["prompt"] = _norm_merge_shot_field("prompt", _new["prompt"])
                        _touched_fields.add("prompt")
                        _updated.append(_num)
                    if _other:
                        _warn.append(f"shot {_num} is a Manga vNext row — only \"prompt\" can be "
                                     f"merged (ignored: {', '.join(_other)})")
                    continue
                for _f in _SHOT_MERGE_FIELDS:
                    if _f in _new:                       # มีคีย์ = เอาค่านั้น แม้จะเป็นค่าว่าง
                        _tgt[_f] = _norm_merge_shot_field(_f, _new[_f])
                        _touched_fields.add(_f)
                for _f in _new:
                    if _f not in _SHOT_MERGE_FIELDS and _f != "shot":
                        _warn.append(f"ignored unknown field: {_f} (shot {_num})")
                _updated.append(_num)

            for _pos, _new in sorted(_inserts, key=lambda _x: -_x[0]):
                # MANGA-VNEXT-RAW-JSON-SHAPE-A1: _new_merge_shot() is Normal-vNext-shaped only
                # (_SHOT_MERGE_FIELDS); a Manga-shaped insert payload has no safe translation here
                # (see the matching guard on the update path above) — fail closed.
                if isinstance(_new.get("_manga_page_spec"), dict):
                    _warn.append(f"insert_after {_pos}: Manga vNext rows are not supported by raw "
                                 f"Merge — skipped")
                    continue
                _shift_gallery_for_insert(_pos)
                _fresh = _new_merge_shot()
                for _f in _SHOT_MERGE_FIELDS:
                    if _f in _new:
                        _fresh[_f] = _norm_merge_shot_field(_f, _new[_f])
                        _touched_fields.add(_f)
                for _f in _new:
                    if _f not in _SHOT_MERGE_FIELDS and _f != "insert_after":
                        _warn.append(f"ignored unknown field: {_f} (new shot after {_pos})")
                shots_data.insert(_pos, _fresh)
                _inserted.append(_pos + 1)

            # ต่อท้าย (ไม่ต้องขยับแกลเลอรี เพราะอยู่หลังสุดเสมอ ไม่ดันตำแหน่งใคร)
            for _new in _auto_appends:
                if isinstance(_new.get("_manga_page_spec"), dict):
                    _warn.append(f"shot {_new.get('shot')}: Manga vNext rows are not supported by "
                                 f"raw Merge — skipped")
                    continue
                _fresh = _new_merge_shot()
                for _f in _SHOT_MERGE_FIELDS:
                    if _f in _new:
                        _fresh[_f] = _norm_merge_shot_field(_f, _new[_f])
                        _touched_fields.add(_f)
                for _f in _new:
                    if _f not in _SHOT_MERGE_FIELDS and _f != "shot":
                        _warn.append(f"ignored unknown field: {_f} (new shot {_new.get('shot')})")
                shots_data.append(_fresh)
                _inserted.append(len(shots_data))

            if _inserts or _auto_appends:
                for _j, _s in enumerate(shots_data):
                    _s["shot"] = _j + 1

        _outline_sync_char_env_tabs()
        _save_state()
        rebuild_shots_col()

        _parts = []
        _plural = lambda _n, _w: f"{_n} {_w}" + ("" if _n == 1 else "s")
        if _top_done:
            _parts.append(_plural(len(_top_done), "story field") + f" ({', '.join(_top_done)})")
        if _updated:
            _parts.append(_plural(len(_updated), "shot") + f" updated ({_fmt_shot_nums(_updated)})")
        if _inserted:
            _parts.append(_plural(len(_inserted), "shot") + f" inserted ({_fmt_shot_nums(_inserted)})")
        # ไม่มีอะไรถูกใช้ ต่างกันสองแบบ: JSON ไม่มีคีย์ที่รู้จักเลย กับ มีแต่ถูกปฏิเสธทั้งหมด
        _summary = ("; ".join(_parts) if _parts else
                    ("nothing applied" if _warn else
                     "nothing to merge — no known keys in that JSON"))
        if _warn:
            _summary += "  ⚠ " + " | ".join(_warn[:5])
            if len(_warn) > 5:
                _summary += f" | (+{len(_warn) - 5} more)"
        # A7: บอกต่อว่าหลัง merge นี้ prompt ยังใช้ได้เลยไหม หรือ stale เพราะ canonical เปลี่ยน —
        # กันบั๊กเงียบ "แก้ state แล้วรันรูปด้วย prompt เก่า"
        if _touched_fields:
            _canonical_touched = _touched_fields - {"prompt"}
            if _canonical_touched and "prompt" not in _touched_fields:
                _summary += "  · prompt of these shots may now be stale — run Image Prompts again before Draw"
            elif "prompt" in _touched_fields and not _canonical_touched:
                _summary += "  · ready to Draw"
        return _summary

    def _fmt_shot_nums(nums: list) -> str:
        _n = sorted(nums)
        return ", ".join(str(x) for x in _n[:10]) + (" …" if len(_n) > 10 else "")

    # กล่องวางของ Merge แยกจากกล่อง Raw โดยตั้งใจ — ถ้าให้วาง JSON บางส่วนทับในกล่องเดิม
    # วันหนึ่งจะมีคนกด Save แทน Merge แล้วช็อตที่ไม่ได้ส่งมาหายทั้งหมด กล่องนี้จึงเริ่มจากว่าง
    # และมีปุ่มเดียวที่ทำงานกับมันได้คือ Merge
    merge_editor = ft.TextField(
        multiline=True, text_size=11, expand=True, autofocus=True,
        hint_text='{ "shots": [ { "shot": 7, "prompt": "..." } ] }',
        border_color=C["border"], focused_border_color=C["green"], bgcolor=C["surface"],
        content_padding=po(left=10, right=10, top=8, bottom=8))
    merge_status = ft.Text("", size=10, color=C["text3"])

    # ปิดตัวหนึ่งแล้วเปิดอีกตัวใน page.update() รอบเดียว = client ยังเล่น dismiss animation ของตัวแรก
    # อยู่ตอนที่ตัวที่สองถูกสั่งเปิด สถานะสองฝั่งเลยหลุดจากกัน ผลคือหน้า Raw ที่เปิดขึ้นมาใหม่กด
    # ปุ่มอะไรก็ไม่ปิด (อาการ "กด Save แล้วนิ่ง" หลัง Merge) — ต้องรอให้ตัวแรกปิดจริงก่อนเสมอ
    # เป็นบั๊กชนิดเดียวกับที่ _open_dialog_swap แก้ไว้ให้ flow dialog สามตัว
    _DIALOG_SWAP_DELAY = 0.25

    async def _swap_dialog(close_dlg, open_dlg, box=None, w=0, h=0):
        close_dlg.open = False
        page.update()
        await asyncio.sleep(_DIALOG_SWAP_DELAY)
        if box is not None:
            _fit_dialog(box, w, h)
        open_dlg.open = True
        page.update()

    async def _close_merge(e=None):
        # กลับไปหน้า Raw เดิมที่เปิดค้างไว้
        await _swap_dialog(merge_dialog, raw_dialog, _raw_box, 800, 650)

    async def _do_merge(e):
        _raw = _clean_ai_json(merge_editor.value)
        if not _raw:
            merge_status.value = "⚠ Paste the JSON first"; page.update(); return
        try:
            data = json.loads(_raw)
        except Exception as ex:
            merge_status.value = f"❌ Invalid JSON: {ex}"; page.update(); return
        if not isinstance(data, dict):
            merge_status.value = "❌ Expected a JSON object at the top level"; page.update(); return
        _raw_undo[0] = _build_raw_data()
        _raw_undo_mode[0] = _outline_mode[0]
        try:
            _summary = _merge_raw_data(data)
        except Exception as ex:
            merge_status.value = f"❌ Merge failed: {ex}"; page.update(); return
        # กลับไปหน้า Raw พร้อมโปรเจกต์ที่ merge แล้ว จะได้เห็นทันทีว่าตอนนี้หน้าตาเป็นอย่างไร
        raw_editor.value = json.dumps(_build_raw_data(), ensure_ascii=False, indent=2)
        raw_status.value = f"✅ Merged — {_summary}"
        status_lbl.value = f"✅ Merged from Raw JSON — {_summary}"
        merge_editor.value = ""
        merge_status.value = ""
        await _swap_dialog(merge_dialog, raw_dialog, _raw_box, 800, 650)

    merge_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("Merge JSON", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
        content=(_merge_box := ft.Container(width=760, height=520, content=ft.Column([
            ft.Text("Paste what the AI sent back. Only the fields and shots it contains are "
                    "updated — everything else stays exactly as it is.\n"
                    "Existing shot — use \"shot\":  {\"shots\": [{\"shot\": 5, \"prompt\": \"new prompt\"}]}\n"
                    "New shot — use \"insert_after\":  {\"shots\": [{\"insert_after\": 5, "
                    "\"shot_desc\": \"new beat\", \"prompt\": \"new prompt\"}]}   (0 = before shot 1)\n"
                    "A \"shot\" number that does not exist is reported, never created. Shot numbers "
                    "always refer to the project as it is right now, before this merge.",
                    size=10, color=C["text3"], italic=True),
            merge_editor, merge_status,
        ], spacing=6, tight=True, expand=True))),
        actions=[
            ft.TextButton("Cancel", on_click=_close_merge, style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("🔀 Merge", on_click=_do_merge, style=ft.ButtonStyle(color=C["green"])),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(merge_dialog)

    async def _merge_raw(e):
        # เปิดกล่องวางแยก แล้วพับหน้า Raw ไว้ก่อน — สอง modal ซ้อนกันใน Flet ไม่แน่นอน
        merge_editor.value = ""
        merge_status.value = ""
        await _swap_dialog(raw_dialog, merge_dialog, _merge_box, 760, 520)

    def _open_raw(e):
        raw_editor.value = json.dumps(_build_raw_data(), ensure_ascii=False, indent=2)
        raw_status.value = ""
        _fit_dialog(_raw_box, 800, 650)
        raw_dialog.open = True; page.update()

    def _save_raw(e):
        try:
            data = json.loads(raw_editor.value)
            _raw_undo[0] = _build_raw_data()
            _raw_undo_mode[0] = _outline_mode[0]
            _warn = _apply_raw_data(data)
            raw_status.value = "✅ Saved" + (f" — {_warn}" if _warn else ""); raw_dialog.open = False
            status_lbl.value = "✅ Shots updated from Raw JSON" + (f" — {_warn}" if _warn else "")
        except Exception as ex:
            raw_status.value = f"❌ Invalid JSON: {ex}"
        page.update()

    async def _export_raw_json(e):
        try:
            data = json.loads(raw_editor.value)
        except Exception as ex:
            raw_status.value = f"❌ Invalid JSON: {ex}"; page.update(); return
        fp = ft.FilePicker()
        dest = await fp.save_file(
            dialog_title="Export Raw JSON",
            file_name=f"{(_cur_story_name[0] or 'story')}.json",
            allowed_extensions=["json"],
        )
        if not dest:
            raw_status.value = "Export cancelled"; page.update(); return
        try:
            if not dest.lower().endswith(".json"):
                dest += ".json"
            # V3-PROMPT-BLOCK-SPACING-NORMALIZATION-A1: Export writes straight from the parsed Raw
            # editor text, bypassing _norm_merge_shot_field entirely (it never calls _apply_raw_data)
            # — normalize a COPY of the parsed shots' prompt fields here so the exported file gets
            # the same canonical spacing, without mutating project state or the Raw editor itself.
            _export_data = dict(data)
            _export_shots = _export_data.get("shots")
            if isinstance(_export_shots, list):
                _export_data["shots"] = [
                    {**_s, "prompt": _normalize_v3_prompt_block_spacing(_s.get("prompt", ""))}
                    if isinstance(_s, dict) and isinstance(_s.get("prompt"), str) else _s
                    for _s in _export_shots
                ]
            Path(dest).write_text(json.dumps(_export_data, ensure_ascii=False, indent=2), encoding="utf-8")
            raw_status.value = f"✅ Exported to {dest}"
        except Exception as ex:
            raw_status.value = f"❌ Export failed: {ex}"
        page.update()

    async def _import_raw_json(e):
        fp = ft.FilePicker()
        files = await fp.pick_files(dialog_title="Import Raw JSON", allow_multiple=False,
                                     allowed_extensions=["json"])
        if not files or not files[0].path:
            raw_status.value = "Import cancelled"; page.update(); return
        try:
            data = json.loads(Path(files[0].path).read_text(encoding="utf-8"))
            _raw_undo[0] = _build_raw_data()
            _raw_undo_mode[0] = _outline_mode[0]
            raw_editor.value = json.dumps(data, ensure_ascii=False, indent=2)
            _warn = _apply_raw_data(data)
            raw_status.value = f"✅ Imported from {Path(files[0].path).name}" + (f" — {_warn}" if _warn else "")
            status_lbl.value = "✅ Shots updated from Raw JSON (import)" + (f" — {_warn}" if _warn else "")
        except Exception as ex:
            raw_status.value = f"❌ Import failed: {ex}"
        page.update()

    # คลิปบอร์ดของกล่อง Raw — service เดียวกันใช้ทั้ง copy ออกและอ่านกลับเข้ามา (Flet 0.85: ต้อง
    # ผูกกับ page.services ก่อนใช้ ทั้ง .set()/.get() เป็น async — เห็น pattern เดียวกันที่ Help ใช้)
    _raw_clipboard = ft.Clipboard()
    page.services.append(_raw_clipboard)

    async def _copy_for_ai(e):
        payload = (
            "Current project (Full JSON) is below. Reply per the workspace "
            "system prompt: return the result as ONE fenced ```json block "
            "(Merge JSON with only the changed fields, unless Full JSON is "
            "explicitly requested), placed FIRST in your reply.\n\n"
            "```json\n" + (raw_editor.value or "") + "\n```"
        )
        try:
            await _raw_clipboard.set(payload)
            raw_status.value = "✅ Copied — paste into the AI chat"
        except Exception as ex:
            raw_status.value = f"⚠ Copy failed ({ex}) — select the text and copy manually"
        page.update()

    async def _paste_merge(e):
        try:
            _clip = await _raw_clipboard.get()
        except Exception as ex:
            raw_status.value = f"❌ Could not read clipboard: {ex}"; page.update(); return
        _raw = _clean_ai_json(_clip)
        if not _raw:
            raw_status.value = "⚠ Clipboard is empty or has no JSON in it"; page.update(); return
        try:
            data = json.loads(_raw)
        except Exception as ex:
            raw_status.value = f"❌ Invalid JSON in clipboard: {ex}"; page.update(); return
        if not isinstance(data, dict):
            raw_status.value = "❌ Expected a JSON object at the top level"; page.update(); return
        _raw_undo[0] = _build_raw_data()
        _raw_undo_mode[0] = _outline_mode[0]
        try:
            _summary = _merge_raw_data(data)
        except Exception as ex:
            raw_status.value = f"❌ Merge failed: {ex}"; page.update(); return
        raw_editor.value = json.dumps(_build_raw_data(), ensure_ascii=False, indent=2)
        raw_status.value = f"✅ Merged from clipboard — {_summary}"
        status_lbl.value = f"✅ Merged from clipboard — {_summary}"
        page.update()

    def _undo_raw(e):
        if not _raw_undo[0]:
            raw_status.value = "⚠ Nothing to undo"; page.update(); return
        _warn = _apply_raw_data(_raw_undo[0])
        # RAW-MODE-SAFETY-FINAL-A1: _apply_raw_data's own all-Manga/all-Normal branch already ran
        # above against the restored data's own shot shape, but the exact remembered mode may
        # differ from whatever it landed on (e.g. restoring to "sequential" when the restored
        # data is all-Normal would only be preserved by that branch if mode was ALREADY sequential
        # going in — it was "manga" going into this Undo, so that branch would only take it as far
        # as the generic "cinematic" fallback). Explicitly override with the precise remembered
        # value afterward — running _sync_outline_mode() a second time here is intended, not a bug.
        if _raw_undo_mode[0] is not None:
            _outline_mode[0] = _raw_undo_mode[0]
            _sync_outline_mode()
        _raw_undo[0] = None
        _raw_undo_mode[0] = None
        raw_editor.value = json.dumps(_build_raw_data(), ensure_ascii=False, indent=2)
        raw_status.value = "↩ Reverted to state before last merge/save/import" + (f" — {_warn}" if _warn else "")
        status_lbl.value = "↩ Reverted to state before last merge/save/import" + (f" — {_warn}" if _warn else "")
        page.update()

    raw_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("Raw JSON", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
        content=(_raw_box := ft.Container(width=800, height=650, content=ft.Column([
            ft.Text("Edit story, character, scene and each shot's prompt directly · "
                    "Save replaces everything · Merge opens a separate box for a partial JSON · "
                    "Copy for AI / Paste & Merge round-trip through the clipboard",
                    size=10, color=C["text3"], italic=True),
            raw_editor, raw_status,
        ], spacing=6, tight=True, expand=True))),
        actions=[
            ft.TextButton("Close", on_click=lambda e: setattr(raw_dialog, "open", False) or page.update(),
                         style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("📋 Copy for AI", on_click=lambda e: asyncio.create_task(_copy_for_ai(e)),
                         style=ft.ButtonStyle(color=C["text2"]),
                         tooltip="Copy the current project plus reply instructions, ready to paste "
                                 "into another AI chat"),
            ft.TextButton("📥 Import", on_click=lambda e: asyncio.create_task(_import_raw_json(e)),
                         style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("📤 Export", on_click=lambda e: asyncio.create_task(_export_raw_json(e)),
                         style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("↩ Undo", on_click=_undo_raw, style=ft.ButtonStyle(color=C["text2"]),
                          tooltip="Revert to the state before the last merge/save/import (one step)"),
            ft.TextButton("📥 Paste & Merge", on_click=lambda e: asyncio.create_task(_paste_merge(e)),
                         style=ft.ButtonStyle(color=C["green"]),
                         tooltip="Read the AI's reply from the clipboard and merge it — only the "
                                 "fields and shots it contains are updated"),
            ft.TextButton("🔀 Merge", on_click=_merge_raw, style=ft.ButtonStyle(color=C["green"]),
                          tooltip="Open a separate box to paste a partial JSON — only the fields "
                                  "and shots it contains are updated"),
            ft.TextButton("💾 Save", on_click=_save_raw, style=ft.ButtonStyle(color=C["accent"]),
                          tooltip="Replace the whole project with this JSON"),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(raw_dialog)

    # ── Outline Chat — คุยต่อจาก conversation จริงของ Outline B (STEP 2) ────────
    # ไม่ใช่ story_chat_panel (นั่นมี system prompt ของตัวเองสำหรับแก้ story) — อันนี้ต่อ
    # system prompt + history ชุดเดียวกับที่ยิงตอนสร้างช็อตจริงเป๊ะ ไม่แทรกอะไรเพิ่มเลย
    _outline_chat_busy: list[bool] = [False]
    outline_chat_list = ft.ListView(spacing=6, expand=True, auto_scroll=True)
    outline_chat_status = ft.Text("", size=10, color=C["text3"])
    outline_chat_input = ft.TextField(
        hint_text="ถามว่าทำไมมันสร้างช็อตแบบนั้น...", multiline=True, min_lines=1, max_lines=4,
        text_size=13, border_color=C["border"], focused_border_color=C["accent"],
        content_padding=po(left=10, right=10, top=8, bottom=8), expand=True,
    )

    def _outline_chat_bubble(text: str, is_user: bool):
        outline_chat_list.controls.append(ft.Container(
            content=ft.Text(text, size=12.5, color=C["text1"] if is_user else C["text2"],
                            selectable=True),
            bgcolor=C["accent"] + "22" if is_user else C["surface"],
            border_radius=br(8), padding=po(left=10, right=10, top=8, bottom=8),
            margin=ft.Margin(left=40 if is_user else 0, right=0 if is_user else 40, top=0, bottom=0),
        ))
        if outline_chat_list.page: outline_chat_list.update()

    _OB_DISPLAY_CAP = 4000

    def _outline_chat_seed_view():
        outline_chat_list.controls.clear()
        cap = _b_active_capture()
        turns = cap.get("turns") or []
        backend = cap.get("backend", "?")
        outline_chat_list.controls.append(ft.Text(
            f"🔗 ต่อจาก Outline B จริง ({backend}, {len(turns)} call) — system prompt เดิมทั้งหมด "
            f"บวกโน้ตสั้น ๆ ท้ายสุดกันหลุดไปโหมด JSON ตอนคุยต่อ",
            size=10, color=C["text3"], italic=True))
        for i, t in enumerate(turns):
            _u = t.get("user_prompt", "")
            _a = t.get("raw_response", "")
            _u_show = _u if len(_u) <= _OB_DISPLAY_CAP else _u[:_OB_DISPLAY_CAP] + f"\n…[ตัดการแสดงผล ยาว {len(_u)} ตัวอักษร — ที่ส่งจริงครบ]"
            _a_show = _a if len(_a) <= _OB_DISPLAY_CAP else _a[:_OB_DISPLAY_CAP] + f"\n…[ตัดการแสดงผล ยาว {len(_a)} ตัวอักษร — ที่ส่งจริงครบ]"
            _outline_chat_bubble(_u_show, is_user=True)
            _outline_chat_bubble(_a_show, is_user=False)
        if outline_chat_list.page: outline_chat_list.update()

    def _open_outline_chat(e):
        _n_turns = len(_b_active_capture().get("turns") or [])
        print(f"[outline_chat] Chat button clicked — turns captured: {_n_turns}")
        if not _n_turns:
            status_lbl.value = "⚠ ยังไม่มี Outline B ให้คุยต่อ — กด Create Outline ก่อน"
            status_lbl.update()
            page.update(); return
        _outline_chat_seed_view()   # reseed ทุกครั้งที่เปิด — เรียบง่ายกว่าแคช ไม่มีอะไรจะ stale
        outline_chat_dialog.open = True
        print("[outline_chat] dialog.open set True, calling page.update()")
        page.update()

    async def _send_outline_chat(e=None):
        if _outline_chat_busy[0]: return
        msg = outline_chat_input.value.strip()
        if not msg: return
        outline_chat_input.value = ""; outline_chat_input.update()
        _outline_chat_bubble(msg, is_user=True)
        _outline_chat_busy[0] = True
        outline_chat_status.value = "⏳ Thinking..."; outline_chat_status.update()
        try:
            reply = await run_outline_chat_message(
                _b_active_capture(), _b_active_capture().get("session_id") or _outline_chat_session_id[0], msg,
                grok_api_key=_grok_api_key[0], on_log=_llm_log,
            )
            _outline_chat_bubble(reply if reply else "⚠ No response", is_user=False)
        except Exception as ex:
            _outline_chat_bubble(f"❌ {ex}", is_user=False)
        _outline_chat_busy[0] = False
        outline_chat_status.value = ""; outline_chat_status.update()

    outline_chat_input.on_submit = lambda e: asyncio.create_task(_send_outline_chat(e))

    outline_chat_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("Outline Chat — ถาม Grok/Local ว่าทำไมสร้างช็อตแบบนั้น",
                      size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
        content=ft.Container(width=700, height=560, content=ft.Column([
            outline_chat_list,
            outline_chat_status,
            ft.Row([outline_chat_input,
                    ft.IconButton(icon=ft.Icons.SEND_ROUNDED, icon_color=C["accent"],
                                 on_click=lambda e: asyncio.create_task(_send_outline_chat(e)))],
                   vertical_alignment=ft.CrossAxisAlignment.END),
        ], spacing=6, tight=True, expand=True)),
        actions=[
            ft.TextButton("Close", on_click=lambda e: setattr(outline_chat_dialog, "open", False) or page.update(),
                         style=ft.ButtonStyle(color=C["text2"])),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(outline_chat_dialog)
    print(f"[outline_chat] dialog constructed, in page.overlay: {outline_chat_dialog in page.overlay}")

    outline_chat_btn = ft.OutlinedButton(
        content=ft.Row([ft.Icon(ft.Icons.PERSON_OUTLINE, size=13, color=C["accent"]),
                         ft.Text("Ask AI", size=11, color=C["accent"])], tight=True, spacing=4,
                        alignment=ft.MainAxisAlignment.CENTER),
        on_click=_open_outline_chat, height=36, width=90, disabled=True, visible=False,  # hidden by request; logic kept
        tooltip="Continue the exact Outline B conversation — ask why it wrote what it wrote",
        style=ft.ButtonStyle(side=ft.BorderSide(1, C["accent"])),
    )

    raw_btn = ft.OutlinedButton(
        content=ft.Row([ft.Icon(ft.Icons.DATA_OBJECT_ROUNDED, size=13, color=C["accent"]),
                         ft.Text("Raw", size=11, color=C["accent"])], tight=True, spacing=4,
                        alignment=ft.MainAxisAlignment.CENTER),
        on_click=_open_raw, height=36, expand=True, tooltip="View or edit raw project data",
        style=ft.ButtonStyle(side=ft.BorderSide(1, C["accent"])),
    )

    # ── Help ─────────────────────────────────────────────────────────
    # เนื้อหามาจากไฟล์ในโฟลเดอร์ helper/ (ดู help_docs.py) — หนึ่งไฟล์ = หนึ่งแท็บ
    # อ่านใหม่ทุกครั้งที่เปิด เพราะผู้ใช้แก้ไฟล์ system prompt อยู่เรื่อย ๆ แล้วอยากเห็นผลทันที
    # อ่านอย่างเดียว: ไม่มี Save/Import/Export — มีแค่ Copy เพราะก๊อปคือเหตุผลเดียวที่หน้านี้มีอยู่
    _help_status = ft.Text("", size=10, color=C["text3"])
    _help_docs: list = []                 # [(ชื่อแท็บ, เนื้อหา)] ของรอบที่เปิดอยู่
    _help_body = ft.Container(expand=True)
    _help_tabs_ref: list = [None]

    def _help_current_text() -> str:
        if not _help_docs:
            return ""
        _t = _help_tabs_ref[0]
        try:
            _i = int(getattr(_t, "selected_index", 0) or 0)
        except Exception:
            _i = 0
        return _help_docs[max(0, min(_i, len(_help_docs) - 1))][1]

    def _build_help_body():
        """สร้างแท็บใหม่จากไฟล์ล่าสุด — เรียกทุกครั้งที่กดเปิด"""
        _help_docs.clear()
        _help_docs.extend(load_help_tabs())
        _views = [
            ft.Container(
                content=ft.Column([
                    ft.TextField(
                        value=_txt, multiline=True, read_only=True, text_size=11, expand=True,
                        border_color=C["border"], focused_border_color=C["border"],
                        bgcolor=C["surface"], color=C["text2"],
                        content_padding=po(left=10, right=10, top=8, bottom=8),
                    ),
                ], spacing=0, expand=True),
                padding=po(top=8), expand=True,
            )
            for _lbl, _txt in _help_docs
        ]
        _tabs = ft.Tabs(
            length=len(_help_docs),
            selected_index=0,
            content=ft.Column([
                ft.TabBar(tabs=[ft.Tab(label=_lbl) for _lbl, _ in _help_docs]),
                ft.TabBarView(controls=_views, expand=True),
            ], spacing=0, expand=True),
            expand=True,
        )
        _help_tabs_ref[0] = _tabs
        _help_body.content = _tabs

    # Flet 0.85: คลิปบอร์ดเป็น "service" ที่ต้องผูกกับ page ก่อนใช้ และเมธอด set เป็น async
    # (ของเดิม page.set_clipboard ถูก deprecate ตั้งแต่ 0.80) — เรียกแบบ sync จะได้ coroutine
    # ที่ไม่มีใคร await เงียบ ๆ ปุ่มเลยกดแล้วไม่มีอะไรเกิดขึ้น
    _help_clipboard = ft.Clipboard()
    page.services.append(_help_clipboard)

    async def _copy_help_async():
        _txt = _help_current_text()
        if not _txt.strip():
            _help_status.value = "⚠ Nothing to copy"
            page.update()
            return
        try:
            await _help_clipboard.set(_txt)
            _help_status.value = (f"✓ Copied {len(_txt):,} characters — paste it as the first "
                                  f"message of a new AI session")
        except Exception as ex:
            _help_status.value = f"⚠ Copy failed ({ex}) — select the text and press Ctrl+C"
        page.update()

    def _copy_help(e):
        asyncio.create_task(_copy_help_async())

    help_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("Help — prompt reference", size=13, weight=ft.FontWeight.W_700,
                      color=C["text1"]),
        content=(_help_box := ft.Container(width=800, height=650, content=ft.Column([
            ft.Text("Read-only. Copy this into a new session with another AI, then paste the "
                    "JSON from Raw → Export to keep working there.",
                    size=10, color=C["text3"], italic=True),
            _help_body, _help_status,
        ], spacing=6, tight=True, expand=True))),
        actions=[
            ft.TextButton("Close",
                          on_click=lambda e: setattr(help_dialog, "open", False) or page.update(),
                          style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("📋 Copy", on_click=_copy_help, style=ft.ButtonStyle(color=C["accent"])),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(help_dialog)

    def _open_help(e):
        _help_status.value = ""
        _build_help_body()          # อ่านไฟล์ใหม่ทุกครั้ง ไม่ใช้ของที่ค้างจากรอบก่อน
        _fit_dialog(_help_box, 800, 650)
        help_dialog.open = True
        page.update()

    # ── Export ────────────────────────────────────────────────────────
    export_name_field = ft.TextField(hint_text="Export name...", text_size=12, bgcolor=C["surface"],
                                     border_color=C["border"], focused_border_color=C["green"], autofocus=True)

    async def _do_export(e):
        name = export_name_field.value.strip()
        if not name: status_lbl.value = "⚠ Enter export name first"; export_dialog.open = False; page.update(); return
        export_dialog.open = False; page.update()
        fp = ft.FilePicker()
        dest_dir = await fp.get_directory_path("Select export folder")
        if not dest_dir: status_lbl.value = "Export cancelled"; page.update(); return
        dest = Path(dest_dir); copied = 0
        for shot_idx, shot in enumerate(shots_data):
            for run_num, img_item in enumerate(gallery_images.get(shot_idx, []), start=1):
                src = Path(img_item["path"])
                if not src.exists(): continue
                new_name = f"{name}_{shot_idx+1:02d}_{run_num:02d}{src.suffix or '.jpg'}"
                try: shutil.copy2(str(src), str(dest / new_name)); copied += 1
                except Exception as ex: print(f"[export] error: {ex}")
        status_lbl.value = f"✅ Exported {copied} images to {dest_dir}"; page.update()

    export_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("Export All Images", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
        content=ft.Container(width=300, content=ft.Column([
            ft.Text("File prefix name, e.g. Tifa_story", size=11, color=C["text2"]),
            export_name_field,
            ft.Text("Files will be named: name_01_01.jpg...", size=9, color=C["text3"], italic=True),
        ], spacing=8, tight=True)),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: setattr(export_dialog, "open", False) or page.update(),
                         style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("Export", on_click=_do_export, style=ft.ButtonStyle(color=C["green"])),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(export_dialog)

    # ── Workflow action buttons: one design system ─────────────────────
    # PRIMARY (filled green): the next main step. TONAL (soft green): strong secondary. NEUTRAL: supporting.
    # DESTRUCTIVE: red outline. Same height, radius, icon size, padding and weight for all; the disabled look
    # is a soft neutral and is defined in the style, so button content carries NO hard-coded colours.
    _WF_H = 40

    def _wf_content(icon, text):
        return ft.Row([ft.Icon(icon, size=15), ft.Text(text, size=12, weight=ft.FontWeight.W_600)],
                      tight=True, spacing=6, alignment=ft.MainAxisAlignment.CENTER)

    def _wf_style(bg, fg, border, dis_bg="#F4F5F7", dis_fg="#A8ADB7", dis_border="#E9EBEF"):
        _side = lambda c: ft.BorderSide(1, c)
        return ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=po(left=12, right=12, top=0, bottom=0),
            bgcolor={ft.ControlState.DEFAULT: bg, ft.ControlState.DISABLED: dis_bg},
            color={ft.ControlState.DEFAULT: fg, ft.ControlState.DISABLED: dis_fg},
            side={ft.ControlState.DEFAULT: _side(border), ft.ControlState.DISABLED: _side(dis_border)},
            elevation=0,
        )

    _WF_PRIMARY = _wf_style(C["green"], "#FFFFFF", C["green"])
    _WF_TONAL = _wf_style("#EDFAF3", "#15803D", "#BBE7CC")
    _WF_NEUTRAL = _wf_style(C["panel"], "#374151", C["border"])
    _WF_DANGER = _wf_style("#FFFFFF", C["red"], "#F5B5B5")

    export_btn = ft.FilledButton(
        content=_wf_content(ft.Icons.DOWNLOAD_ROUNDED, "Export Images"),
        on_click=lambda e: (setattr(export_name_field, "value", _cur_story_name[0]),
                             setattr(export_dialog, "open", True), page.update()),
        height=_WF_H, expand=True, tooltip="Export project results",
        style=_WF_NEUTRAL,
    )

    # ── Run All / Stop / Test / Fix ───────────────────────────────────
    run_all_btn = ft.FilledButton(
        content=_wf_content(ft.Icons.PLAY_ARROW_ROUNDED, "Render All"),
        height=_WF_H, expand=True,
        tooltip="Generate Images for All Shots",
        style=_WF_PRIMARY,
    )
    run_no_image_btn = ft.FilledButton(
        content=_wf_content(ft.Icons.HIDE_IMAGE_OUTLINED, "Render Missing"),
        height=_WF_H, expand=True,
        style=_WF_NEUTRAL,
        tooltip="Generate images only for shots that do not have one yet",
    )
    stop_btn = ft.FilledButton(
        content=ft.Row([ft.Icon(ft.Icons.STOP_ROUNDED, size=15, color="white"),
                         ft.Text("Stop", size=12, color="white")], tight=True, spacing=5),
        bgcolor=C["red"], height=34, disabled=True,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
    )

    def _stop_btn_state(active: bool, stopping: bool = False):
        """จุดเดียวที่คุมหน้าตาปุ่ม Stop — disabled/สี/ป้าย ต้องขยับพร้อมกันเสมอ
        active=True  งานกำลังรัน  -> กดได้ สีแดง "Stop"
        stopping=True กดหยุดแล้ว   -> กดไม่ได้ สีเทา "Stopping..."
        ทั้งคู่ False = งานจบแล้ว  -> กดไม่ได้ สีแดง "Stop" (พร้อมรอบถัดไป)"""
        stop_btn.disabled = stopping or not active
        stop_btn.bgcolor = C["text3"] if stopping else C["red"]
        stop_btn.content.controls[1].value = "Stopping..." if stopping else "Stop"

    def _do_stop(e):
        _stop_event.set()
        _stop_btn_state(False, stopping=True)
        page.update()

    stop_btn.on_click = _do_stop

    def _set_running(v):
        _is_running[0] = v
        run_all_btn.disabled = v
        run_no_image_btn.disabled = v
        _stop_btn_state(v)
        page.update()
    _set_running_ref[0] = _set_running

    async def do_run_all(e):
        if _is_running[0]: return
        await _maybe_offer_yolo()
        if api_dd.value: selected_api[0] = api_dd.value
        if size_dd.value: sel_size[0] = size_dd.value
        if id_wf_dd.value: _id_workflow[0] = id_wf_dd.value
        if not shots_data: status_lbl.value = "⚠ No shots yet"; page.update(); return
        if not selected_api[0]: status_lbl.value = "⚠ Select Image Workflow first"; page.update(); return
        print(f"[RUN ALL] เริ่ม — Inpaint Mode = {'เปิด (ใช้รูปก่อนหน้าเป็น reference ทุก shot)' if _inpaint_mode[0] else 'ปิด (text-to-image ปกติ)'}")
        try: _loops_per_shot[0]  = int(loop_shot_field.value or "1")
        except: pass
        try: _loops_per_story[0] = int(loop_story_field.value or "1")
        except: pass
        _stop_event.clear(); _set_running(True)
        w, h = _SIZE_MAP.get(sel_size[0], (2304, 1296))
        story_loops = _loops_per_story[0]; shot_loops = _loops_per_shot[0]
        total = len(shots_data) * shot_loops * story_loops
        n = 0; _shot_times: list[float] = []

        for story_loop in range(story_loops):
            if _stop_event.is_set(): break
            for i, shot in enumerate(shots_data):
                if _stop_event.is_set(): break
                for shot_loop in range(shot_loops):
                    if _stop_event.is_set(): break
                    n += 1
                    avg = sum(_shot_times) / len(_shot_times) if _shot_times else 0
                    remaining = total - n
                    eta_txt = ""
                    if avg > 0 and remaining > 0:
                        eta_sec = int(avg * remaining)
                        if eta_sec >= 3600:   eta_str = f"{eta_sec//3600}h {(eta_sec%3600)//60}m"
                        elif eta_sec >= 60:   eta_str = f"{eta_sec//60}m {eta_sec%60}s"
                        else:                 eta_str = f"{eta_sec}s"
                        eta_txt = f" • ETA {eta_str}"
                    lbl = f"⏳ {n}/{total} Shot {i+1}"
                    if shot_loops > 1: lbl += f" round {shot_loop+1}"
                    if story_loops > 1: lbl += f" Story {story_loop+1}"
                    status_lbl.value = lbl + eta_txt; progress_ring.visible = True; page.update()
                    try:
                        _seed = _locked_seed[0] if _seed_lock[0] else -1
                        _prompt = shot.get("prompt", "")
                        # inject narration box จาก 3 fields
                        _prompt = runtime_dialogue_prompt(_prompt, shot, _cur_dlg_style())

                        _style_kw = _resolve_style_kw(shot.get("_dir_scale_category") or shot.get("shot_scale", "medium"))
                        print(f"[RUN ALL shot {i+1}] style={_sel_style[0]!r} | source_style={_source_style[0]!r} | kw={_style_kw!r}")
                        # re-sync จาก dropdown ตรงๆ ก่อนใช้ — on_change ของ Dropdown บาง client ไม่ยิง event ให้ _run_steps ตามทัน
                        if run_steps_field.value:
                            _run_steps[0] = int(run_steps_field.value) if run_steps_field.value != "default" else -1
                        _t0 = time.time()
                        out, used_seed, _trace = await _gen_shot_image(i, _prompt, selected_api[0],
                                                       w, h, n, fixed_seed=_seed,
                                                       override_steps=_run_steps[0],
                                                       stop_event=_stop_event,
                                                       style_kw=_style_kw)
                        _shot_times.append(time.time() - _t0)
                        avg = sum(_shot_times) / len(_shot_times)
                        remaining = total - n
                        if remaining > 0:
                            eta_sec = int(avg * remaining)
                            if eta_sec >= 3600:   eta_str = f"{eta_sec//3600}h {(eta_sec%3600)//60}m"
                            elif eta_sec >= 60:   eta_str = f"{eta_sec//60}m {eta_sec%60}s"
                            else:                 eta_str = f"{eta_sec}s"
                            status_lbl.value = f"✅ Shot {i+1} done • ETA {eta_str}"
                        else:
                            status_lbl.value = f"✅ Shot {i+1} done"
                        if out:
                            if _id_pass[0] and _id_ref_image[0] and _id_workflow[0]:
                                id_out = await run_identity_pass(out, _id_ref_image[0], _id_workflow[0],
                                                                  Path(output_dir[0]), index=n, stop_event=_stop_event)
                                if id_out:
                                    out = id_out
                                    _trace["generation_mode"] = "identity_pass"
                                    _trace["identity_reference"] = _id_ref_image[0]
                            _ds = _cur_dlg_style()
                            if pil_overlay_applies(shots_data[i]):
                                _narr_t, _sub_t = _pil_overlay_texts(shots_data[i], _ds)
                                if _narr_t or _sub_t:
                                    _dp = shots_data[i].get("dialogue_position", "bottom")
                                    await asyncio.get_event_loop().run_in_executor(
                                        None, apply_dual_text_overlay, out, _narr_t, _sub_t, _ds, _dp)
                            add_thumb_to_shot(i, out, seed=used_seed, trace=_trace,
                                               fill_backend=shots_data[i].get("_fill_backend", "unknown"),
                                               story_id=_cur_story_name[0]); _save_state()
                    except Exception as ex:
                        status_lbl.value = f"⚠ Shot {i+1}: {ex}"; page.update()

        status_lbl.value = "⏹ Stopped" if _stop_event.is_set() else f"✅ Done — {n} images!"
        progress_ring.visible = False; _set_running(False)

    run_all_btn.on_click = do_run_all
    _do_run_all_ref[0] = do_run_all

    async def do_run_no_image(e):
        if _is_running[0]: return
        await _maybe_offer_yolo()
        if api_dd.value: selected_api[0] = api_dd.value
        if size_dd.value: sel_size[0] = size_dd.value
        if id_wf_dd.value: _id_workflow[0] = id_wf_dd.value
        if not shots_data: status_lbl.value = "⚠ No shots yet"; page.update(); return
        if not selected_api[0]: status_lbl.value = "⚠ Select Image Workflow first"; page.update(); return
        pending = [i for i in range(len(shots_data)) if not gallery_images.get(i)]
        if not pending: status_lbl.value = "✅ All shots already have images"; page.update(); return
        try: _loops_per_shot[0] = int(loop_shot_field.value or "1")
        except: pass
        _stop_event.clear(); _set_running(True)
        w, h = _SIZE_MAP.get(sel_size[0], (2304, 1296))
        total = len(pending); n = 0; _shot_times: list[float] = []
        for idx in pending:
            if _stop_event.is_set(): break
            shot = shots_data[idx]
            n += 1
            avg = sum(_shot_times) / len(_shot_times) if _shot_times else 0
            eta_txt = f" • ETA {int(avg*(total-n))}s" if avg > 0 and total - n > 0 else ""
            status_lbl.value = f"⏳ {n}/{total} Shot {idx+1}{eta_txt}"; progress_ring.visible = True; page.update()
            try:
                _seed = _locked_seed[0] if _seed_lock[0] else -1
                _prompt = shot.get("prompt", "")
                _prompt = runtime_dialogue_prompt(_prompt, shot, _cur_dlg_style())
                _style_kw = _resolve_style_kw(shot.get("_dir_scale_category") or shot.get("shot_scale", "medium"))
                print(f"[RUN shot {idx+1}] style={_sel_style[0]!r} | source_style={_source_style[0]!r} | kw={_style_kw!r}")
                if run_steps_field.value:
                    _run_steps[0] = int(run_steps_field.value) if run_steps_field.value != "default" else -1
                _t0 = time.time()
                out, used_seed, _trace = await _gen_shot_image(idx, _prompt, selected_api[0],
                                               w, h, n, fixed_seed=_seed,
                                               override_steps=_run_steps[0],
                                               stop_event=_stop_event,
                                               style_kw=_style_kw)
                _shot_times.append(time.time() - _t0)
                if out:
                    _ds = _cur_dlg_style()
                    if pil_overlay_applies(shots_data[idx]):
                        _narr_t, _sub_t = _pil_overlay_texts(shots_data[idx], _ds)
                        if _narr_t or _sub_t:
                            _dp = shots_data[idx].get("dialogue_position", "bottom")
                            await asyncio.get_event_loop().run_in_executor(
                                None, apply_dual_text_overlay, out, _narr_t, _sub_t, _ds, _dp)
                    add_thumb_to_shot(idx, out, seed=used_seed, trace=_trace,
                                       fill_backend=shots_data[idx].get("_fill_backend", "unknown"),
                                       story_id=_cur_story_name[0]); _save_state()
            except Exception as ex:
                status_lbl.value = f"⚠ Shot {idx+1}: {ex}"; page.update()
        status_lbl.value = "⏹ Stopped" if _stop_event.is_set() else f"✅ Done — {n} images!"
        progress_ring.visible = False; _set_running(False)

    run_no_image_btn.on_click = do_run_no_image

    async def do_test_all(e):
        if _is_running[0]: return
        await _maybe_offer_yolo()
        if api_dd.value: selected_api[0] = api_dd.value
        if size_dd.value: sel_size[0] = size_dd.value
        if not shots_data: status_lbl.value = "⚠ No shots yet"; page.update(); return
        if not selected_api[0]: status_lbl.value = "⚠ Select Image Workflow first"; page.update(); return
        _stop_event.clear()
        try: _test_steps[0] = int(test_steps_field.value or "3")
        except: pass
        _set_running(True)
        w, h = _SIZE_MAP.get(sel_size[0], (2304, 1296))
        # Test เจตนาคือ preview เร็ว 1 รูปต่อ shot เสมอ — ไม่พึ่ง "จำนวนรอบ" ของ Run All
        story_loops = 1; shot_loops = 1
        total = len(shots_data) * shot_loops * story_loops
        n = 0; _shot_times: list[float] = []

        for story_loop in range(story_loops):
            if _stop_event.is_set(): break
            for i, shot in enumerate(shots_data):
                if _stop_event.is_set(): break
                for shot_loop in range(shot_loops):
                    if _stop_event.is_set(): break
                    n += 1
                    prompt = shot.get("prompt", "")
                    if not prompt.strip() and not _sheets_fallback(): continue
                    prompt = runtime_dialogue_prompt(prompt, shot, _cur_dlg_style())
                    _style_kw = _resolve_style_kw(shot.get("_dir_scale_category") or shot.get("shot_scale", "medium"))
                    print(f"[TEST shot {i+1}] style={_sel_style[0]!r} | kw={_style_kw!r}")
                    lbl = f"🧪 Test {n}/{total} Shot {i+1}"
                    if shot_loops > 1: lbl += f" round {shot_loop+1}"
                    if story_loops > 1: lbl += f" Story {story_loop+1}"
                    status_lbl.value = lbl + f" ({_test_steps[0]} steps)"
                    progress_ring.visible = True; page.update()
                    try:
                        _t0 = time.time()
                        out, used_seed, _trace = await _gen_shot_image(i, prompt, selected_api[0],
                                                       w, h, n,
                                                       fixed_seed=_locked_seed[0] if _seed_lock[0] else -1,
                                                       override_steps=_test_steps[0], stop_event=_stop_event,
                                                       style_kw=_style_kw)
                        _shot_times.append(time.time() - _t0)
                        remaining = total - n
                        if remaining > 0:
                            avg = sum(_shot_times) / len(_shot_times)
                            eta_sec = int(avg * remaining)
                            if eta_sec >= 3600:   eta_str = f"{eta_sec//3600}h {(eta_sec%3600)//60}m"
                            elif eta_sec >= 60:   eta_str = f"{eta_sec//60}m {eta_sec%60}s"
                            else:                 eta_str = f"{eta_sec}s"
                            status_lbl.value = f"✅ Test Shot {i+1} done • ETA {eta_str}"
                        else:
                            status_lbl.value = f"✅ Test Shot {i+1} done"
                        if out:
                            _dlg_style = _cur_dlg_style()
                            if pil_overlay_applies(shots_data[i]):
                                _narr_t, _sub_t = _pil_overlay_texts(shots_data[i], _dlg_style)
                                if _narr_t or _sub_t:
                                    _dlg_pos = shots_data[i].get("dialogue_position", "bottom")
                                    await asyncio.get_event_loop().run_in_executor(
                                        None, apply_dual_text_overlay, out, _narr_t, _sub_t, _dlg_style, _dlg_pos)
                            add_thumb_to_shot(i, out, seed=used_seed, is_test=True, trace=_trace,
                                               fill_backend=shots_data[i].get("_fill_backend", "unknown"),
                                               story_id=_cur_story_name[0]); _save_state()
                    except Exception as ex:
                        status_lbl.value = f"⚠ Shot {i+1}: {ex}"; page.update()

        status_lbl.value = "⏹ Stopped" if _stop_event.is_set() else f"✅ Test done — {n} images!"
        progress_ring.visible = False; _set_running(False)

    test_all_btn = ft.FilledButton(
        content=_wf_content(ft.Icons.SCIENCE_ROUNDED, "Preview All"),
        height=_WF_H,
        style=_WF_NEUTRAL,
        on_click=do_test_all, expand=True,
        tooltip="Quick preview — one low-step image for every shot",
    )
    _do_test_all_ref[0] = do_test_all

    async def do_fix_all(e):
        if _is_running[0]: return
        await _maybe_offer_yolo()
        if api_dd.value: selected_api[0] = api_dd.value
        if size_dd.value: sel_size[0] = size_dd.value
        if not selected_api[0]: status_lbl.value = "⚠ Select Image Workflow first"; page.update(); return
        test_imgs = [(i, img) for i, imgs in sorted(gallery_images.items())
                     for img in imgs if img.get("is_test") and img.get("seed", -1) >= 0]
        if not test_imgs: status_lbl.value = "⚠ No test images (🧪) to fix"; page.update(); return
        _stop_event.clear(); _set_running(True)
        w, h = _SIZE_MAP.get(sel_size[0], (2304, 1296))
        total = len(test_imgs); done = 0

        for shot_idx, img_data in test_imgs:
            if _stop_event.is_set(): break
            seed = img_data.get("seed", -1); old_path = img_data["path"]
            shot = shots_data[shot_idx] if shot_idx < len(shots_data) else {}
            prompt = shot.get("prompt", "")
            if not prompt.strip() and not _sheets_fallback(): continue
            prompt = runtime_dialogue_prompt(prompt, shot, _cur_dlg_style())
            _style_kw = _resolve_style_kw(shot.get("_dir_scale_category") or shot.get("shot_scale", "medium"))
            print(f"[FIX shot {shot_idx+1}] style={_sel_style[0]!r} | source_style={_source_style[0]!r} | kw={_style_kw!r}")
            status_lbl.value = f"🔧 Fix {done+1}/{total} Shot {shot_idx+1} seed={seed}..."
            progress_ring.visible = True; page.update()
            if run_steps_field.value:
                _run_steps[0] = int(run_steps_field.value) if run_steps_field.value != "default" else -1
            try:
                out, _, _trace = await _gen_shot_image(shot_idx, prompt, selected_api[0],
                                        w, h, shot_idx+1, fixed_seed=seed,
                                        override_steps=_run_steps[0],
                                        stop_event=_stop_event,
                                        style_kw=_style_kw)
                if out:
                    _fix_ds = _cur_dlg_style()
                    if pil_overlay_applies(shot):
                        _fix_narr_t, _fix_sub_t = _pil_overlay_texts(shot, _fix_ds)
                        if _fix_narr_t or _fix_sub_t:
                            _fix_dp = shot.get("dialogue_position", "bottom")
                            await asyncio.get_event_loop().run_in_executor(
                                None, apply_dual_text_overlay, out, _fix_narr_t, _fix_sub_t, _fix_ds, _fix_dp)
                    imgs = gallery_images.get(shot_idx, [])
                    for idx2, img in enumerate(imgs):
                        if img["path"] == old_path:
                            imgs[idx2] = {
                                "path": out, "ts": time.time(), "seed": seed, "is_test": False,
                                "story_id": _cur_story_name[0], "shot": shot_idx,
                                "fill_backend": shot.get("_fill_backend", "unknown"),
                                "generation_mode": _trace.get("generation_mode", "unknown"),
                                "workflow": _trace.get("workflow", "unknown"),
                                "reference_image": _trace.get("reference_image"),
                                "identity_reference": _trace.get("identity_reference"),
                                "final_prompt": _trace.get("final_prompt", ""),
                            }
                            break
                    try: Path(old_path).unlink(missing_ok=True)
                    except Exception: pass
                    if shot_idx < len(shots_col.controls):
                        _shot_rows.pop(shot_idx, None); _shot_prompt_fields.pop(shot_idx, None)
                        _build_shot_row(shot_idx, shots_data[shot_idx] if shot_idx < len(shots_data) else {})
                        shots_col.controls[shot_idx] = shots_col.controls.pop()
                    _save_state(); done += 1
            except Exception as ex:
                status_lbl.value = f"⚠ Shot {shot_idx+1}: {ex}"; page.update()

        status_lbl.value = "⏹ Stopped" if _stop_event.is_set() else f"✅ Fix done — {done}/{total} images!"
        progress_ring.visible = False; _set_running(False)

    fix_all_btn = ft.FilledButton(
        content=_wf_content(ft.Icons.BUILD_CIRCLE_ROUNDED, "Finalize"),
        height=_WF_H,
        style=_WF_TONAL,
        on_click=do_fix_all, expand=True,
        tooltip="Re-render every test preview (🧪) at full quality, keeping its seed",
    )

    # ── Clear All ─────────────────────────────────────────────────────
    clear_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("Clear all images?", size=13, weight=ft.FontWeight.W_700, color=C["red"]),
        content=ft.Text("All images in every shot will be permanently deleted from disk.", size=12, color=C["text2"]),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: setattr(clear_dialog, "open", False) or page.update(),
                         style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("Clear", on_click=lambda e: _do_clear_all(e), style=ft.ButtonStyle(color=C["red"])),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(clear_dialog)

    def clear_all(e):
        clear_dialog.open = True; page.update()

    def _do_clear_all(e):
        clear_dialog.open = False
        for imgs in gallery_images.values():
            for img in imgs:
                try: Path(img["path"]).unlink(missing_ok=True)
                except Exception: pass
        gallery_images.clear(); _save_state(); rebuild_shots_col()
        status_lbl.value = "🗑 All images cleared"; page.update()

    # ── Project dialogs (New / Save / Manage) ─────────────────────────
    # A project is one saved story file. Only a NAMED project is ever written automatically; an
    # unnamed ("Untitled") workspace lives in the session autosave (_save_state) only.
    _selected_story: list[str] = [""]

    new_name_field = ft.TextField(hint_text="Project name...", text_size=12, autofocus=True,
                                  border_color=C["border"], focused_border_color=C["accent"], bgcolor=C["surface"])

    def _clear_extra_req_fields():
        groom_req_field.value = ""
        _check_extra_req.value = ""
        _director_extra_req.value = ""
        _outline_review_req.value = ""

    def _write_project(name: str, fresh: bool = False):
        return save_story(name, story_field.value, char_field.value, shots_data,
                          {str(i): v for i, v in gallery_images.items()},
                          int(num_shots_field.value or "5"),
                          scene_desc=scene_field.value, requirement=requirement_field.value,
                          story_summary=story_summary_field.value,
                          props_desc=props_field.value, outline_events=list(_outline_events[0]),
                          outline_clothing_arc=_outline_clothing_arc[0],
                          outline_physical_state=_outline_physical_state[0],
                          outline_locations=_outline_locations[0],
                          source_style=_source_style[0], source_style_meta=_source_style_meta[0],
                          outline_scenario_script=list(_outline_scenario_script[0]),
                          outline_source_mode=_outline_source_mode[0],
                          outline_source_blocks=list(_outline_source_blocks[0]),
                          outline_vnext_a1=_outline_vnext_a1[0], outline_vnext_a2=_outline_vnext_a2[0],
                          outline_vnext_a2_manga=None,  # legacy storage parameter; shared A2 lives above
                          fill_design_ledger=_fill_design_ledger[0], fresh=fresh)

    def _autosave_current_project(skip_name: str = "") -> bool:
        """Save the current workspace into its own named project. No-op for an unnamed workspace
        (and when skip_name is the current project, e.g. re-loading it)."""
        name = (_cur_story_name[0] or "").strip()
        if not name or name == skip_name:
            return False
        try:
            _write_project(name)
            return True
        except Exception as ex:
            log.error(f"[PROJECT] autosave '{name}' failed: {ex}", exc_info=True)
            return False

    # One shared confirmation dialog (overwrite / delete). on_cancel lets the caller re-open its own dialog.
    _confirm_text = ft.Text("", size=12, color=C["text1"])
    _confirm_yes_btn = ft.TextButton("OK")
    _confirm_action: list = [None, None]   # [on_confirm, on_cancel]

    # Shown with page.show_dialog / pop_dialog (like the Outline flow dialogs), one frame AFTER the
    # dialog that raised it has closed — an overlay dialog opened in the same tick as another one
    # closing could stay stuck on screen. Callbacks run after the pop, guarded so an error is logged
    # and shown in the status line instead of leaving the user staring at a dead dialog.
    async def _run_after(cb, delay: float = 0.25):
        await asyncio.sleep(delay)
        if not cb:
            return
        try:
            cb()
            page.update()
        except Exception as ex:
            log.error(f"[PROJECT] action failed: {ex}", exc_info=True)
            status_lbl.value = f"❌ Project action failed: {ex}"
            page.update()

    def _close_confirm(run_cancel: bool):
        page.pop_dialog()
        asyncio.create_task(_run_after(_confirm_action[1] if run_cancel else None))

    def _do_confirm(e):
        page.pop_dialog()
        asyncio.create_task(_run_after(_confirm_action[0]))

    _confirm_yes_btn.on_click = _do_confirm
    confirm_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("Confirm", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
        content=ft.Container(width=300, content=_confirm_text),
        actions=[ft.TextButton("Cancel", on_click=lambda e: _close_confirm(True),
                               style=ft.ButtonStyle(color=C["text2"])), _confirm_yes_btn],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    def _ask_confirm(title: str, text: str, yes_label: str, yes_color: str, on_yes, on_cancel=None):
        confirm_dialog.title = ft.Text(title, size=13, weight=ft.FontWeight.W_700, color=C["text1"])
        _confirm_text.value = text
        _confirm_yes_btn.content = yes_label
        _confirm_yes_btn.style = ft.ButtonStyle(color=yes_color)
        _confirm_action[0], _confirm_action[1] = on_yes, on_cancel

        async def _show():
            await asyncio.sleep(0.25)          # let the previous dialog finish closing
            page.show_dialog(confirm_dialog)
        asyncio.create_task(_show())

    def _reset_workspace():
        _clear_b_salvage_notice()
        story_field.value = ""; requirement_field.value = ""
        char_field.value = ""; scene_field.value = ""; props_field.value = ""
        story_summary_field.value = ""; _story_summary[0] = ""
        _groom_text[0] = ""; _groom_card[0] = {}
        _groom_orig[0] = ""; _groom_chat.clear(); _groom_active[0] = False
        _outline_events[0] = []
        _outline_clothing_arc[0] = ""
        _outline_physical_state[0] = ""
        _outline_locations[0] = ""
        _outline_scenario_script[0] = []
        _outline_source_mode[0] = ""
        _outline_source_blocks[0] = []
        _source_style[0] = ""; _source_style_meta[0] = {}
        _update_style_desc()
        _clear_extra_req_fields()
        shots_data.clear(); gallery_images.clear()
        _outline_sync_char_env_tabs()

    def _create_project(name: str, fresh: bool):
        _cur_story_name[0] = name
        _reset_workspace()
        _write_project(name, fresh=fresh)
        _save_state(); rebuild_shots_col()
        cur_story_lbl.value = name; status_lbl.value = f"✨ New project '{name}'"
        page.update()

    def _confirm_new(e):
        name = new_name_field.value.strip()
        if not name:
            new_name_field.error_text = "Project name is required"; page.update(); return
        new_name_field.error_text = None
        new_dialog.open = False; new_name_field.value = ""; page.update()
        _autosave_current_project()
        if project_exists(name):
            def _reopen():
                new_name_field.value = name; new_dialog.open = True
            _ask_confirm("Project already exists", "Project already exists.\nDo you want to overwrite it?",
                         "Overwrite", C["red"], lambda: _create_project(name, fresh=True), _reopen)
        else:
            _create_project(name, fresh=True)

    new_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("New Project", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
        content=ft.Container(width=300, content=ft.Column([
            ft.Text("Project name", size=11, color=C["text2"]), new_name_field], spacing=8, tight=True)),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: setattr(new_dialog, "open", False) or page.update(),
                         style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("Create", on_click=_confirm_new, style=ft.ButtonStyle(color=C["accent"])),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(new_dialog)

    def _open_new_dialog(e=None):
        # ตั้งชื่อเริ่มต้น "Untitled N" (N = เลขถัดไปที่ยังไม่ซ้ำ) แล้วเลือกทั้งหมด พิมพ์ทับได้ทันที
        import re as _re_un
        _used = {_m.group(1) for _p in list_projects()
                 if (_m := _re_un.fullmatch(r"Untitled (\d+)", str(_p.get("name", ""))))}
        _n = 1
        while str(_n) in _used:
            _n += 1
        _name = f"Untitled {_n}"
        new_name_field.value = _name; new_name_field.error_text = None
        new_dialog.open = True; page.update()

        async def _select_all():
            await asyncio.sleep(0.15)          # รอ dialog แสดงก่อนค่อย focus/select
            try:
                new_name_field.selection = ft.TextSelection(base_offset=0, extent_offset=len(_name))
                await new_name_field.focus()
                new_name_field.update()
            except Exception:
                pass
        asyncio.create_task(_select_all())

    save_name_field = ft.TextField(hint_text="Project name...", text_size=12, autofocus=True,
                                   border_color=C["border"], focused_border_color=C["green"], bgcolor=C["surface"])

    def _open_save_dialog(e):
        save_name_field.value = _cur_story_name[0]; save_name_field.error_text = None
        save_dialog.open = True; page.update()

    def _save_named_project(name: str):
        _write_project(name)
        _cur_story_name[0] = name; cur_story_lbl.value = name
        _save_state(); _rebuild_load_list()
        status_lbl.value = f"💾 Saved '{name}'"; page.update()

    def _confirm_save(e):
        name = save_name_field.value.strip()
        if not name:
            save_name_field.error_text = "Project name is required"; page.update(); return
        save_name_field.error_text = None
        save_dialog.open = False; page.update()
        if name != _cur_story_name[0] and project_exists(name):
            _ask_confirm("Project already exists", "Project already exists.\nDo you want to overwrite it?",
                         "Overwrite", C["red"], lambda: _save_named_project(name),
                         lambda: setattr(save_dialog, "open", True))
        else:
            _save_named_project(name)

    save_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("Save Project", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
        content=ft.Container(width=300, content=ft.Column([
            ft.Text("Project name", size=11, color=C["text2"]), save_name_field], spacing=8, tight=True)),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: setattr(save_dialog, "open", False) or page.update(),
                         style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("Save", on_click=_confirm_save, style=ft.ButtonStyle(color=C["green"])),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(save_dialog)

    load_filter_field = ft.TextField(
        hint_text="Search...", text_size=11, height=34,
        border_color=C["border"], focused_border_color=C["accent"], bgcolor=C["surface"],
        content_padding=po(left=8, right=8, top=4, bottom=4), prefix_icon=ft.Icons.SEARCH_ROUNDED,
    )
    load_list_view = ft.ListView(spacing=2, height=260)

    _tile_refs: dict = {}   # name -> (container, icon, title text): single click restyles in place instead of rebuilding the list,
                            # so a double click is not broken by the tile being replaced between its two clicks

    def _select_tile(name: str):
        _selected_story[0] = name
        for n, (cont, icon, title) in _tile_refs.items():
            sel = (n == name)
            cont.bgcolor = C["accent_lt"] if sel else "transparent"
            icon.color = C["accent"] if sel else C["text3"]
            title.color = C["accent"] if sel else C["text1"]
            title.weight = ft.FontWeight.W_600 if sel else ft.FontWeight.W_400
        page.update()

    def _make_story_tile(item: dict):
        name = item["name"]
        is_selected = _selected_story[0] == name
        icon = ft.Icon(ft.Icons.BOOK_ROUNDED, size=14, color=C["accent"] if is_selected else C["text3"])
        title = ft.Text(name, size=12,
                        color=C["accent"] if is_selected else C["text1"],
                        weight=ft.FontWeight.W_600 if is_selected else ft.FontWeight.W_400)
        cont = ft.Container(
            content=ft.Row([icon,
                             ft.Column([
                                 title,
                                 ft.Text(f"Updated: {format_project_time(item['updated_at'])}", size=10, color=C["text3"]),
                                 ft.Text(f"Created: {format_project_time(item['created_at'])}", size=10, color=C["text3"]),
                             ], spacing=1, expand=True)],
                            spacing=8),
            bgcolor=C["accent_lt"] if is_selected else "transparent",
            border_radius=br(6), padding=po(left=10, right=10, top=8, bottom=8),
        )
        _tile_refs[name] = (cont, icon, title)

        def _on_double(e, n=name):
            _selected_story[0] = n
            _confirm_load(e)
        return ft.GestureDetector(content=cont, on_tap=lambda e, n=name: _select_tile(n), on_double_tap=_on_double)

    def _rebuild_load_list(query: str = ""):
        stories = list_projects()   # newest updated first
        if query: stories = [s for s in stories if query.lower() in s["name"].lower()]
        load_list_view.controls.clear(); _tile_refs.clear()
        if not stories:
            load_list_view.controls.append(ft.Container(
                content=ft.Text("No projects found", size=11, color=C["text3"], italic=True),
                padding=po(left=10, top=8, right=10, bottom=8)))
        else:
            for s in stories: load_list_view.controls.append(_make_story_tile(s))
        if load_list_view.page: load_list_view.update()

    load_filter_field.on_change = lambda e: _rebuild_load_list(load_filter_field.value or "")

    def _confirm_load(e):
        name = _selected_story[0]
        if not name: return
        data = load_story(name)
        if not data: status_lbl.value = f"⚠ Project '{name}' not found"; load_dialog.open = False; page.update(); return
        # the project being left is saved first; loading itself never touches the loaded file's dates
        _autosave_current_project(skip_name=name)
        _cur_story_name[0] = name; cur_story_lbl.value = name
        story_field.value = data.get("story_text", ""); requirement_field.value = data.get("requirement", "")
        char_field.value = data.get("char_desc", ""); scene_field.value = data.get("scene_desc", "")
        props_field.value = _props_to_str(data.get("props_desc", ""))
        num_shots_field.value = str(data.get("num_shots", 5))
        _clear_extra_req_fields()
        shots_data.clear(); shots_data.extend(data.get("shots", []))
        gallery_images.clear()
        for k, v in data.get("gallery", {}).items(): gallery_images[int(k)] = v
        # ไฟล์เรื่องเก่าอาจไม่มี field พวกนี้ (ก่อนหน้านี้ save_story() ไม่เคยเขียน) — โหลดคืนถ้ามี
        # ถ้าไม่มี ต้องล้างของเรื่องก่อนหน้าทิ้ง ไม่งั้นจะค้างข้ามเรื่อง แล้ว Regenerate จะเข้าทาง
        # reuse ด้วย events ที่ไม่ใช่ของเรื่องนี้
        _outline_events[0] = list(data.get("outline_events") or [])
        _outline_clothing_arc[0] = data.get("outline_clothing_arc") or ""
        _outline_physical_state[0] = data.get("outline_physical_state") or ""
        _outline_locations[0] = data.get("outline_locations") or ""
        _outline_scenario_script[0] = [
            dict(u) for u in (data.get("outline_scenario_script") or []) if isinstance(u, dict)
        ]
        _outline_source_mode[0] = str(data.get("outline_source_mode") or "")
        _outline_source_blocks[0] = [
            dict(b) for b in (data.get("outline_source_blocks") or []) if isinstance(b, dict)
        ]
        # SOURCE-STYLE-A1: no key in an older saved file means no extraction ever ran for that
        # story — "" is correct, never carry over the PREVIOUS story's source_style (stale-leak).
        _source_style[0] = data.get("source_style") or ""
        _source_style_meta[0] = dict(data.get("source_style_meta") or {})
        _update_style_desc()
        _clear_b_salvage_notice()  # a notice belongs to the run that produced it, never to the story loaded next
        _groom_orig[0] = data.get("groom_original") or ""
        _groom_text[0] = data.get("groom_draft") or ""
        _groom_chat[:] = [{"role": m.get("role", "groom"), "text": str(m.get("text", ""))}
                          for m in (data.get("groom_chat") or []) if isinstance(m, dict)]
        _groom_active[0] = False
        # VNEXT-B-CLOTHING-STATE-FORM-A1 follow-up: same reasoning as the session-autosave restore
        # above — a saved story without a persisted vNext bible (any story saved before this fix, or
        # generated entirely through legacy Outline) gets a synthesized one instead of None, so Fill
        # never silently falls back to the legacy engine after reopening this project.
        _b_run_state[0] = "NOT_STARTED"
        _va1 = data.get("outline_vnext_a1")
        _outline_vnext_a1[0] = (_va1 if isinstance(_va1, dict) and _va1
                                 else _synthesize_vnext_a1_from_fields())
        _va2 = data.get("outline_vnext_a2")
        _outline_vnext_a2[0] = _va2 if isinstance(_va2, dict) and _va2 else None
        # FILL-CREATIVE-DESIGN-LEDGER-A1: restore THIS story's ledger only (older saves -> empty)
        _vfl = data.get("fill_design_ledger")
        _fill_design_ledger[0] = _vfl if isinstance(_vfl, str) else ""
        _outline_sync_char_env_tabs()
        _save_state(); rebuild_shots_col(); load_dialog.open = False
        status_lbl.value = f"📂 Loaded project '{name}'" ; page.update()

    def _delete_project_now(name: str):
        delete_story(name); _selected_story[0] = ""
        if _cur_story_name[0] == name:
            # the workspace stays, but is now unnamed — otherwise the next autosave would recreate the file
            _cur_story_name[0] = ""; cur_story_lbl.value = "Untitled"; _save_state()
        _rebuild_load_list(load_filter_field.value or "")
        status_lbl.value = f"🗑 Deleted project '{name}'"
        load_dialog.open = True; page.update()

    def _confirm_delete_story(e):
        name = _selected_story[0]
        if not name: return
        load_dialog.open = False; page.update()
        _ask_confirm("Delete project", f"Delete project '{name}'?\nThis cannot be undone.",
                     "Delete", C["red"], lambda: _delete_project_now(name),
                     lambda: setattr(load_dialog, "open", True))

    load_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Text("Manage Projects", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
        content=ft.Container(width=360, content=ft.Column([
            load_filter_field,
            ft.Container(content=load_list_view,
                         border=ft.Border(left=ft.BorderSide(1, C["border"]), right=ft.BorderSide(1, C["border"]),
                                          top=ft.BorderSide(1, C["border"]), bottom=ft.BorderSide(1, C["border"])),
                         border_radius=br(6)),
        ], spacing=8, tight=True)),
        actions=[
            ft.TextButton("Delete", on_click=_confirm_delete_story, style=ft.ButtonStyle(color=C["red"])),
            ft.TextButton("Close", on_click=lambda e: setattr(load_dialog, "open", False) or page.update(),
                         style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("Load", on_click=_confirm_load, style=ft.ButtonStyle(color=C["accent"])),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(load_dialog)

    # ── Settings dialog (tabbed) ──────────────────────────────────────
    def _sfield(label: str, hint: str = "", value: str = "", width=None, **kw) -> ft.TextField:
        return ft.TextField(
            value=value, hint_text=hint, text_size=11,
            border_color=C["border"], focused_border_color=C["accent"], bgcolor=C["surface"],
            content_padding=po(left=8, right=8, top=6, bottom=6),
            width=width, expand=(width is None), **kw,
        )

    def _slabel(text: str) -> ft.Text:
        return ft.Text(text, size=11, color=C["text2"], weight=ft.FontWeight.W_600)

    def _shint(text: str) -> ft.Text:
        return ft.Text(text, size=9, color=C["text3"], italic=True)

    def _srow(*controls) -> ft.Row:
        return ft.Row(list(controls), spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _tab_col(*rows) -> ft.Column:
        return ft.Column(list(rows), spacing=10, tight=True,
                         horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    # ── Connection tab ────────────────────────────────────────────────
    comfy_host_field = _sfield("e.g. 127.0.0.1:8188", value=_comfy_host[0])

    # ── LLM tab ───────────────────────────────────────────────────────
    llm_tokens_groom_field   = _sfield("e.g. 12288", value=str(_llm_tokens_groom[0]),   width=90)
    llm_tokens_outline_field = _sfield("e.g. 6144",  value=str(_llm_tokens_outline[0]), width=90)
    llm_tokens_fill_field    = _sfield("e.g. 8192",  value=str(_llm_tokens_fill[0]),    width=90)
    llm_tokens_chat_field    = _sfield("e.g. 8000",  value=str(_llm_tokens_chat[0]),    width=90)
    llm_tokens_story_a_field = _sfield("e.g. 4096",  value=str(_llm_tokens_story_a[0]), width=90)
    outline_batch_field      = _sfield("0 = off",    value=str(_outline_batch[0]),      width=90)
    llm_timeout_field        = _sfield("e.g. 120",  value=str(_llm_timeout[0]),        width=100)
    llm_n_ctx_field          = _sfield("0 = ตามโมเดล", value=str(_llm_n_ctx[0]),        width=100)

    # ── Grok API tab ──────────────────────────────────────────────────
    grok_key_field = ft.TextField(
        value=_grok_api_key[0],                      # the saved key, masked, with the eye to show it (as in Prompt Lab)
        label="Grok API Key", hint_text="xai-xxxxxxxx...",
        password=True, can_reveal_password=True, text_size=11,
        border_color=C["border"], focused_border_color=C["accent"], bgcolor=C["surface"],
        content_padding=po(left=8, right=8, top=6, bottom=6), expand=True,
    )
    grok_model_dd = ft.Dropdown(
        value=_grok_model[0], label="Grok Model",
        options=[ft.dropdown.Option(m) for m in [
            "grok-4-latest", "grok-4.6", "grok-4.5", "grok-3-latest", "grok-3-mini-latest",
        ]],
        text_size=11, height=38, expand=True, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=4, top=0, bottom=0),
    )
    grok_temp_field = _sfield("0.0 – 2.0", value=str(_grok_temperature[0]), width=100)
    grok_temp_field.label = "Temperature"
    custom_sys_field = ft.TextField(
        value=_custom_sys[0], multiline=True, min_lines=3, max_lines=6,
        hint_text="Appended to the end of every Grok system prompt (Story Editor, Outline, Image Prompt, Chat, ...)",
        text_size=11, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=8, top=6, bottom=6),
    )

    _CAPTION_AUTO = "__auto__"
    caption_wf_dd = ft.Dropdown(
        options=[ft.dropdown.Option(_CAPTION_AUTO, "Auto (a JoyCaption workflow, the short one first)")],
        value=_CAPTION_AUTO, text_size=11, height=38, width=420,
        border_color=C["border"], focused_border_color=C["accent"], bgcolor=C["surface"],
        content_padding=po(left=8, right=4, top=0, bottom=0),
    )

    def _refresh_caption_dd():
        files = sorted(p.name for p in CAPTION_DIR.glob("*.json"))
        caption_wf_dd.options = [ft.dropdown.Option(_CAPTION_AUTO, "Auto (a JoyCaption workflow, the short one first)")] + [ft.dropdown.Option(f) for f in files]
        caption_wf_dd.value = _caption_wf[0] if _caption_wf[0] in files else _CAPTION_AUTO

    llm_source_switch = ft.Switch(
        value=_llm_use_program[0], active_color=C["accent"],
        label="Use the program's LLM settings",
        label_text_style=ft.TextStyle(size=11, color=C["text2"]),
        tooltip="ON (default) = the program sets the LLM values it was tested with (for example repeat penalty 1.0 for Shot List and Page List, the context size of long conversations). "
                "OFF = the program leaves the temperature, repeat penalty and context size alone and every call uses the workflow file's own.",
    )

    llm_debug_switch = ft.Switch(
        value=_llm_debug_logging[0], active_color=C["accent"],
        label="LLM Debug Logging",
        label_text_style=ft.TextStyle(size=11, color=C["text2"]),
        tooltip="OFF by default. When ON, save raw Local/Grok request+response JSON under %LOCALAPPDATA%\\WorkflowPad\\debug for Story Editor, Outline, Image Prompt, Consistency Check, and Visual Direction.",
    )

    # ── Tools tab ─────────────────────────────────────────────────────

    def _save_sys_prompt(e):
        # Connection
        _comfy_host[0] = comfy_host_field.value.strip() or "127.0.0.1:8188"
        _comfy_api_mod.set_comfy_host(_comfy_host[0])
        # LLM
        try: _llm_tokens_groom[0]   = max(512, int(llm_tokens_groom_field.value   or "12288"))
        except ValueError: pass
        try: _llm_tokens_outline[0] = max(512, int(llm_tokens_outline_field.value or "6144"))
        except ValueError: pass
        try: _llm_tokens_fill[0]    = max(512, int(llm_tokens_fill_field.value    or "8192"))
        except ValueError: pass
        try: _llm_tokens_chat[0]    = max(512, int(llm_tokens_chat_field.value    or "8000"))
        except ValueError: pass
        try: _llm_tokens_story_a[0] = max(512, int(llm_tokens_story_a_field.value or "4096"))
        except ValueError: pass
        try: _outline_batch[0] = max(0, int(outline_batch_field.value or "0"))
        except ValueError: pass
        try:
            _v = int(llm_n_ctx_field.value or "0")
            _llm_n_ctx[0] = 0 if _v <= 0 else max(4096, _v)
        except ValueError: pass
        try: _llm_timeout[0] = max(30, int(llm_timeout_field.value or "120"))
        except ValueError: pass
        try: _grok_temperature[0] = max(0.0, min(2.0, float(grok_temp_field.value or "0.7")))
        except ValueError: pass
        _llm_debug_logging[0] = bool(llm_debug_switch.value)
        _llm_use_program[0] = bool(llm_source_switch.value)
        _caption_wf[0] = "" if (caption_wf_dd.value or _CAPTION_AUTO) == _CAPTION_AUTO else caption_wf_dd.value
        _llm_overrides.set_mode(_llm_overrides.PROGRAM if _llm_use_program[0] else _llm_overrides.WORKFLOW)
        set_llm_params(_llm_tokens_outline[0], _llm_tokens_fill[0],
                       _llm_timeout[0], _grok_temperature[0], _llm_tokens_groom[0],
                       _llm_tokens_chat[0], _llm_tokens_story_a[0],
                       _llm_n_ctx[0])
        set_llm_debug_logging(_llm_debug_logging[0])
        _outline_refresh_b_visibility()
        # Grok
        _new_key = grok_key_field.value.strip()           # what is in the field is the key (clearing it removes the saved key)
        if _new_key != _grok_api_key[0]:
            _grok_api_key[0] = _new_key
            save_credentials({"grok_api_key": _new_key})
        grok_key_field.value = _grok_api_key[0]
        if grok_model_dd.value: _grok_model[0] = grok_model_dd.value
        _custom_sys[0] = custom_sys_field.value or ""
        set_llm_params(grok_custom_sys=_custom_sys[0])
        # Tools
        _ext_editor[0] = ext_editor_field.value.strip()
        _save_state(); sys_dialog.open = False
        status_lbl.value = "💾 Settings saved"; page.update()

    _stab_index: list[int] = [0]
    _stab_labels = ["Connection", "LLM", "Grok API", "Tools", "Detection"]

    _stab_pages = [
        _tab_col(
            _slabel("ComfyUI Host"),
            comfy_host_field,
            _shint("Host and port of the running ComfyUI server"),
        ),
        _tab_col(
            _srow(
                ft.Column([_slabel("Story Editor Tokens"),   llm_tokens_groom_field],   spacing=4),
                ft.Column([_slabel("Outline Tokens"), llm_tokens_outline_field], spacing=4),
                ft.Column([_slabel("Shots / batch"), outline_batch_field], spacing=4),
                ft.Column([_slabel("Image Prompt Tokens"),    llm_tokens_fill_field],    spacing=4),
                ft.Column([_slabel("Timeout (s)"),    llm_timeout_field],        spacing=4),
                ft.Column([_slabel("n_ctx (0=โมเดล)"), llm_n_ctx_field],          spacing=4),
            ),
            _srow(
                ft.Column([_slabel("Chat Tokens"),    llm_tokens_chat_field],    spacing=4),
                ft.Column([_slabel("Story Pass"),     llm_tokens_story_a_field], spacing=4),
            ),
            _shint("Story Editor = rewrite the story · Outline = plan shots (Shot List) · Image Prompt = write prompts · Timeout = local LLM only"),
            _shint("Chat = edit the outline in conversation · Story Pass = pass A (character/scene/props/events) — a lower cap is fine because the output is short"),
            ft.Text("Image-to-text workflow (vision Check / Direct)", size=11, color=C["text2"]),
            caption_wf_dd,
            _shint("The workflow that describes the pictures for the vision Check and Direct buttons: any workflow in comfy_apis/caption. Auto = a JoyCaption workflow (the short one first)."),
            llm_source_switch,
            _shint("ON = the program's tested LLM values are used (default). OFF = the workflow file's own values are used; max tokens and session handling stay program-controlled, and some stages "
                   "may fail or cut their answers short with other values. Covers every stage: repeat penalty (Shot List, Page List), temperature and context size (checks, visual direction, Story Editor, chat, Image Prompts, Story Bible). Timeouts and the conversation history settings stay with the program."),
            llm_debug_switch,
            _shint("OFF = production (no extra prompt, no debug files). ON = save raw request/response + a short decision report under %LOCALAPPDATA%\\WorkflowPad\\debug for every Story Editor, Outline, Image Prompt, Consistency Check, and Visual Direction call."),
        ),
        _tab_col(
            grok_key_field,
            _shint("Get your key at console.x.ai · the key is shared with Prompt Lab (%APPDATA%\\StoryGenerator\\credentials.json)"),
            ft.Container(height=4),
            _srow(
                ft.Column([grok_model_dd],   spacing=4, expand=True),
                ft.Column([grok_temp_field], spacing=4),
            ),
            _shint("Temperature: 0.0 = deterministic · 1.0 = default · 2.0 = max creative"),
            ft.Container(height=8),
            _slabel("My System Prompt"),
            custom_sys_field,
            _shint("Optional — appended to the end of every Grok system prompt, every call."),
        ),
        _tab_col(
            _slabel("External Image Editor"),
            ft.Row([
                ext_editor_field,
                ft.IconButton(icon=ft.Icons.CLEAR_ROUNDED, icon_size=14, icon_color=C["text3"],
                             tooltip="Clear", on_click=_clear_ext_editor, width=28, height=28, padding=0),
            ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            _shint("Full path to an image editor — leave blank for Windows 'Open With'"),
        ),
        _tab_col(
            _slabel("Person & Face Detection"),
            _shint("Optional models that keep the narration box off people and faces. Narration placement works without them."),
            ft.Container(height=4),
            ft.FilledButton(content=ft.Text("Detection Models…", size=12, color="white", weight=ft.FontWeight.W_600),
                            bgcolor=C["accent"], on_click=_open_detection_settings,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))),
        ),
    ]

    _stab_content = ft.Container(
        content=_stab_pages[0],
        padding=po(left=2, right=2, top=14, bottom=2),
    )
    _stab_btns: list[ft.TextButton] = []

    def _stab_select(idx: int):
        _stab_index[0] = idx
        _stab_content.content = _stab_pages[idx]
        for i, b in enumerate(_stab_btns):
            b.style = ft.ButtonStyle(
                color=C["accent"] if i == idx else C["text3"],
                bgcolor=C["accent_lt"] if i == idx else "transparent",
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=po(left=10, right=10, top=4, bottom=4),
            )
        _stab_content.update()
        for b in _stab_btns: b.update()

    for _i, _lbl in enumerate(_stab_labels):
        _idx = _i
        _stab_btns.append(ft.TextButton(
            _lbl,
            on_click=lambda e, i=_idx: _stab_select(i),
            style=ft.ButtonStyle(
                color=C["accent"] if _i == 0 else C["text3"],
                bgcolor=C["accent_lt"] if _i == 0 else "transparent",
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=po(left=10, right=10, top=4, bottom=4),
            ),
        ))

    _stab_bar = ft.Container(
        content=ft.Row(_stab_btns, spacing=2),
        border=ft.Border(bottom=ft.BorderSide(1, C["border2"])),
        padding=po(left=0, right=0, top=0, bottom=6),
    )

    _settings_body = ft.Column(
        [_stab_bar, _stab_content],
        spacing=0,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    sys_dialog = ft.AlertDialog(
        modal=True, bgcolor=C["surface"],
        title=ft.Row([ft.Text("Settings", size=14, weight=ft.FontWeight.W_700, color=C["text1"]),
                      ft.Text(f"{APP_NAME}  ·  Version {APP_VERSION}", size=11, color=C["text3"])],
                     spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        content=ft.Container(width=800, height=600, content=_settings_body),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: setattr(sys_dialog, "open", False) or page.update(),
                         style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("Save", on_click=_save_sys_prompt, style=ft.ButtonStyle(color=C["accent"])),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(sys_dialog)

    def _open_sys_dialog(e):
        grok_key_field.value = _grok_api_key[0]
        _refresh_caption_dd()
        llm_source_switch.value = _llm_use_program[0]
        llm_debug_switch.value = _llm_debug_logging[0]
        sys_dialog.open = True; page.update()

    # ── Toolbar ───────────────────────────────────────────────────────
    cur_story_lbl = ft.Text(
        _cur_story_name[0] or "Untitled", size=14, weight=ft.FontWeight.W_700, color=C["text1"],
        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
    )

    clear_all_btn = ft.OutlinedButton(
        content=_wf_content(ft.Icons.DELETE_SWEEP_ROUNDED, "Clear Images"),
        on_click=clear_all, height=_WF_H, expand=True,
        tooltip="Remove generated images from the current project",
        style=_WF_DANGER,
    )

    top_toolbar = ft.Container(
        content=ft.Row([
            # ── กลุ่ม 1: Story nav ─────────────────────────────────────
            ft.IconButton(ft.Icons.ADD_ROUNDED, icon_size=17, icon_color=C["accent"],
                          tooltip="New Project", width=30, height=30, padding=0,
                          on_click=_open_new_dialog),
            ft.IconButton(ft.Icons.SAVE_ROUNDED, icon_size=17, icon_color=C["green"],
                          tooltip="Save Project", width=30, height=30, padding=0, on_click=_open_save_dialog),
            ft.IconButton(ft.Icons.FOLDER_OPEN_ROUNDED, icon_size=17, icon_color=C["purple"],
                          tooltip="Manage Projects", width=30, height=30, padding=0,
                          on_click=lambda e: (_rebuild_load_list(""),
                                              setattr(load_filter_field, "value", ""),
                                              setattr(load_dialog, "open", True), page.update())),
            ft.IconButton(ft.Icons.SETTINGS_ROUNDED, icon_size=17, icon_color=C["text3"],
                          tooltip="Settings", width=30, height=30, padding=0, on_click=_open_sys_dialog),
            # ── Divider ────────────────────────────────────────────────
            ft.Container(width=1, height=24, bgcolor=C["border2"]),
            # ── กลุ่ม 3: Status + Stop (right) ────────────────────────
            # The status text takes the free space and is right-aligned; the spinner follows it, so the two stay together at the right (the spinner used to sit at the
            # left edge of the expanded status area, in the middle of the toolbar). The B notice banner expands into the free space itself when it is shown.
            b_salvage_banner,
            ft.Row([status_lbl, llm_prog], spacing=6, expand=True,
                   alignment=ft.MainAxisAlignment.END, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(width=1, height=24, bgcolor=C["border2"]),
            stop_btn,
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        height=46, bgcolor=C["panel"],
        padding=po(left=14, right=14, top=0, bottom=0),
        border=ft.Border(bottom=ft.BorderSide(1, C["border2"])),
    )

    toolbar = top_toolbar  # compat alias — left_panel ยังอ้างถึง toolbar

    # ── Layout: Left / Center / Right panels ──────────────────────────
    left_panel = ft.Container(
        width=310, bgcolor=C["panel"],
        border=ft.Border(right=ft.BorderSide(1, C["border2"])),
        content=ft.Column([
            ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.AUTO_STORIES_ROUNDED, size=14, color=C["text3"]),
                    cur_story_lbl,
                ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=po(left=14, right=14, top=14, bottom=10),
                border=ft.Border(bottom=ft.BorderSide(1, C["border2"])),
            ),
            # ── Story: ยืดเต็มความสูงที่เหลือ จนถึงแถบปุ่มด้านล่าง ────────────────────
            # Summary (AI) กับ Character / Scene / Special instructions ถูกซ่อน — แก้ได้ที่
            # Outline dialog อยู่แล้ว แต่ widget ต้องคงอยู่เพราะ save/load และทุก pass อ่านค่าจากมัน
            ft.Container(
                content=ft.Column([
                    ft.Row([ft.Icon(ft.Icons.AUTO_STORIES_ROUNDED, size=14, color=C["text3"]),
                            ft.Text("Story", size=11, weight=ft.FontWeight.W_600, color=C["text2"])],
                           spacing=6),
                    story_field,
                    ft.Column([story_summary_field, char_field, scene_field, requirement_field],
                              spacing=0, tight=True, visible=False),
                ], spacing=6, expand=True),
                padding=po(left=14, right=14, top=10, bottom=10),
                expand=True,
            ),
            divider(),
            # ── Fixed bottom: ปุ่มอยู่กับที่เหมือนแผงขวา ไม่เลื่อนตามเนื้อเรื่อง ──────
            ft.Container(
                content=ft.Column([
                    ft.Row([ft.Text("AI Model", size=11, color=C["text2"]), llm_dd],
                           spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Row([gen_shots_btn, outline_chat_btn], spacing=8,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    # สามปุ่มแบ่งความกว้างเท่า ๆ กันให้พอดีแถบ — ก่อนหน้านี้แต่ละปุ่มกว้างตามข้อความ
                    # พอเพิ่ม Help เข้ามา ปุ่มสุดท้ายเลยล้นขอบแผงออกไป และความสูงก็ไม่เท่ากันด้วย
                    ft.Row([
                        raw_btn,
                        ft.OutlinedButton(
                            content=ft.Row([ft.Icon(ft.Icons.TERMINAL_ROUNDED, size=13, color=C["text3"]),
                                             ft.Text("AI Logs", size=11, color=C["text3"])],
                                            tight=True, spacing=4,
                                            alignment=ft.MainAxisAlignment.CENTER),
                            on_click=lambda e: (setattr(log_dialog, "open", True), page.update()),
                            height=36, expand=True, tooltip="View AI request and response logs",
                            style=ft.ButtonStyle(side=ft.BorderSide(1, C["border"])),
                        ),
                        ft.OutlinedButton(
                            content=ft.Row([ft.Icon(ft.Icons.HELP_OUTLINE_ROUNDED, size=13, color=C["text3"]),
                                             ft.Text("Help", size=11, color=C["text3"])],
                                            tight=True, spacing=4,
                                            alignment=ft.MainAxisAlignment.CENTER),
                            on_click=_open_help, tooltip="Open help",
                            height=36, expand=True,
                            style=ft.ButtonStyle(side=ft.BorderSide(1, C["border"])),
                        ),
                    ], spacing=8),
                ], spacing=8, tight=True),
                padding=po(left=14, right=14, top=10, bottom=12),
            ),
        ], spacing=0, expand=True),
    )

    def _rp_label(text: str) -> ft.Text:
        return ft.Text(text, size=10, color=C["text3"], weight=ft.FontWeight.W_600)

    right_panel = ft.Container(
        width=290, bgcolor=C["panel"],
        border=ft.Border(left=ft.BorderSide(1, C["border2"])),
        content=ft.Column([
            # ── Header ──────────────────────────────────────────────────
            ft.Container(
                content=ft.Text("Render", size=14, weight=ft.FontWeight.W_700, color=C["text1"]),
                padding=po(left=14, top=14, right=14, bottom=10)),
            divider(),
            # ── Scrollable body ─────────────────────────────────────────
            ft.Column([
                # WORKFLOW
                group_header("RENDER ENGINE"),
                ft.Container(
                    content=ft.Column([
                        _rp_label("Image Workflow"),
                        ft.Row([ft.Container(content=api_dd, expand=True),
                                ft.IconButton(icon=ft.Icons.REFRESH_ROUNDED, icon_size=15,
                                              icon_color=C["text3"], on_click=rescan,
                                              width=30, height=30, padding=0)],
                               spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ], spacing=6),
                    padding=po(left=14, right=14, top=8, bottom=12),
                ),
                # IDENTITY PASS (hidden)
                ft.Container(visible=False, content=ft.Column([
                    group_header("IDENTITY PASS"),
                    ft.Container(
                        content=ft.Column([id_pass_switch, id_pass_container], spacing=6),
                        padding=po(left=14, right=14, top=8, bottom=12),
                    ),
                ])),
                # INPAINT MODE
                group_header("REFERENCE IMAGE"),
                ft.Container(
                    content=ft.Column([
                        inpaint_switch,
                        ft.Text("Use previous frame as reference instead of text-to-image",
                                size=9, color=C["text3"], italic=True),
                        inpaint_container,
                    ], spacing=4),
                    padding=po(left=14, right=14, top=8, bottom=12),
                ),
                # GENERATION
                group_header("RENDER SETTINGS"),
                ft.Container(
                    content=ft.Column([
                        _rp_label("Style"),
                        style_dd,
                        style_desc_lbl,
                        ft.Container(height=2),
                        _rp_label("Image Size"),
                        size_dd,
                        ft.Container(height=2),
                        _rp_label("Steps"),
                        run_steps_field,
                        ft.Container(height=2),
                        _rp_label("Iterations"),
                        ft.Row([
                            ft.Column([
                                ft.Text("per shot", size=10, color=C["text2"]),
                                loop_shot_field,
                            ], spacing=4, expand=True),
                            ft.Column([
                                ft.Text("per story", size=10, color=C["text2"]),
                                loop_story_field,
                            ], spacing=4, expand=True),
                        ], spacing=12),
                    ], spacing=6),
                    padding=po(left=14, right=14, top=8, bottom=12),
                ),
            ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True),
            divider(),
            # ── Fixed bottom ────────────────────────────────────────────
            ft.Container(
                content=ft.Column([
                    ft.Row([seed_lock_switch, append_scene_desc_switch],
                           spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    seed_row,
                    ft.Row([run_all_btn, run_no_image_btn], spacing=8),
                    divider(),
                    ft.Row([_rp_label("Preview Steps:"), test_steps_field],
                           spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Row([test_all_btn, fix_all_btn], spacing=8),
                    divider(),
                    ft.Row([export_btn, clear_all_btn], spacing=8),
                ], spacing=10),
                padding=po(left=14, right=14, top=12, bottom=14),
            ),
        ], spacing=0, expand=True),
    )

    # ── Story Chat Panel — คุยกับ LLM ทั้งเรื่อง (ส่ง raw JSON ครั้งเดียวต่อ session) ──
    _story_chat_session_id: list[str] = [uuid.uuid4().hex[:8]]
    _story_chat_sent_json: list[bool] = [False]
    _story_chat_last_json: list[str] = [""]
    _story_chat_wf_template: list      = [None]
    _story_chat_busy: list[bool]      = [False]
    _story_chat_tokens: list[int]     = [0]      # ESTIMATE of what the Comfy chat session already holds (messages and replies; the system prompt is counted per check)

    story_chat_list = ft.ListView(spacing=6, expand=True, auto_scroll=True)
    _story_chat_height: list[float] = [200.0]
    story_chat_list_box = ft.Container(content=story_chat_list, height=_story_chat_height[0],
                                       bgcolor=C["bg"], border_radius=br(8),
                                       padding=po(left=8, right=8, top=8, bottom=8))

    def _story_chat_resize(e: ft.DragUpdateEvent):
        dy = e.local_delta.y if e.local_delta else 0
        # handle อยู่บนสุดของ panel — ลากลง (dy บวก) ควรทำให้ panel แคบลง ลากขึ้นควรขยาย
        _story_chat_height[0] = max(80.0, min(700.0, _story_chat_height[0] - dy))
        story_chat_list_box.height = _story_chat_height[0]
        story_chat_list_box.update()

    story_chat_resize_handle = ft.GestureDetector(
        mouse_cursor=ft.MouseCursor.RESIZE_ROW,
        drag_interval=16,
        on_pan_update=_story_chat_resize,
        content=ft.Container(
            content=ft.Container(width=40, height=4, bgcolor=C["border"], border_radius=br(2)),
            alignment=ft.Alignment(0, 0), height=10,
        ),
    )
    story_chat_status = ft.Text("🆕 New session • Full story context", size=10, color=C["text3"])
    story_chat_input = ft.TextField(
        hint_text="Ask about the story, revise scenes, or request changes…",
        text_size=12, expand=True,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=10, right=10, top=8, bottom=8),
    )

    def _story_chat_bubble(text: str, is_user: bool):
        bubble = ft.Container(
            content=ft.Text(text, size=11, selectable=True,
                           color="white" if is_user else C["text1"]),
            bgcolor=C["accent"] if is_user else C["surface"],
            border_radius=br(8),
            padding=po(left=10, top=6, right=10, bottom=6),
            margin=ft.Margin(left=80 if is_user else 0, right=0 if is_user else 80, top=0, bottom=0),
        )
        story_chat_list.controls.append(bubble)
        if story_chat_list.page: story_chat_list.update()

    def _story_chat_new_session(e=None):
        _story_chat_session_id[0] = uuid.uuid4().hex[:8]
        _story_chat_sent_json[0] = False
        _story_chat_tokens[0] = 0
        _story_chat_wf_template[0] = None
        story_chat_list.controls.clear()
        story_chat_list.update()
        story_chat_status.value = "🆕 New session • Full story context"
        story_chat_status.update()

    def _build_story_chat_sys() -> str:
        _sync_prompt_style()
        _sync_style()
        _fmt_rule, _ = _build_prompt_format_rule(_prompt_style[0])
        _fmt_rule = block_prompt_format_rule(shots_data) or _fmt_rule   # labelled Fill blocks win over the legacy rule
        return (
            "You are the supervising assistant for a story-to-image project. You understand the whole "
            "project before you change any part of it, and you can reason across story, character, "
            "scene, props, events, clothing continuity, physical state, shot structure, prompts, "
            "dialogue, narration, camera and visual style. You are not a prompt rewriter.\n"
            "The user's FIRST message in this session contains the complete current project as JSON. "
            "If the project changes later, a message headed UPDATED FULL STORY JSON carries the new "
            "complete project: it replaces every earlier JSON. The latest JSON is your only source of "
            "truth for the project's content.\n\n"

            "THREE MODES — read which one the user is in before you answer:\n"
            "- DISCUSS (\"what do you think?\", \"would this work?\", \"how do I make this stronger?\"): "
            "talk normally. Suggest, argue, brainstorm. NEVER output JSON here.\n"
            "- REVIEW (\"what's wrong?\", \"why did her arms disappear?\", \"check continuity\"): inspect "
            "the project, name the affected shots and fields, and explain the likely CAUSE — not just the "
            "symptom. Still no JSON: report what you found and let the user decide what to fix.\n"
            "- EDIT (\"fix it\", \"change shots 5-8\", \"add a shot here\"): make the agreed change and "
            "return the merge patch.\n\n"

            "DIAGNOSE WIDE, EDIT NARROW. When the user reports one visible problem, check whether it is "
            "isolated, repeated across several shots, or project-wide, and say so — \"the same thing "
            "happens in shots 4, 8, 9 and 11\" is the useful answer, not silently fixing only the one they "
            "named. But do NOT then edit everything you found: propose the full list and let them confirm. "
            "A patch touching shots the user never agreed to is worse than a patch that is too small, "
            "because they have one Apply button and no way to review thirty shots behind it.\n"
            "Keep every edit scoped to what was asked. Fixing a character's appearance is not licence to "
            "change the lighting, the camera, the clothing, the events or anyone's name — leave every "
            "unrelated fact exactly as it is, unless it directly contradicts the fix.\n\n"

            "CHARACTER FIRST, SHOT SECOND. The character sheet says WHO a person is; the shot says what "
            "they are DOING right now. A permanent physical trait the sheet establishes — an artificial or "
            "missing limb, a scar, a burn, a tattoo, horns, a tail, wings, claws, scales, fur, animal ears, "
            "non-human or supernatural anatomy, a mutation, an implant built into the flesh — stays true in "
            "every shot where that person is visible, in any genre and whatever language the story is "
            "written in. Those are examples, not a checklist to match words against.\n"
            "NOT EMPHASIZED IS NOT ABSENT: a shot being about drinking, or lighting, or a face does not "
            "make the rest of the body stop existing. \"The event doesn't mention her mechanical arms\" is "
            "never a reason to describe ordinary arms — she is a woman WITH mechanical arms, drinking.\n"
            "But describe only what the camera can see: on a face close-up a prosthetic leg is out of "
            "frame and does not belong in the prompt. Distance may simplify a trait (\"black mechanical "
            "arms\" instead of every joint) — it may never turn it back into an ordinary limb.\n"
            "Express identity naturally in the prompt's own voice; do not paste the character sheet in "
            "verbatim. Preserve what the sheet establishes and invent nothing it does not — and keep each "
            "person's traits on that person, never moved onto someone else.\n"
            "Keep anatomy, clothing, temporary state, action and props separate: mechanical arms are the "
            "body, a sports bra is clothing, blood on the arm is a passing state, a glass is a prop, and "
            "holding it is the action.\n\n"

            "COUNT THE BODY WHEN SOMETHING IS WRONG WITH IT. \"Why does she have three hands?\", \"check "
            "the hands\", \"who is holding the gun?\", \"her mechanical arm disappeared\" are all the same "
            "question: does what the prompt describes fit the body the sheet gives this person? Answer it "
            "by rebuilding the picture limb by limb — which limbs this character has, what kind each one "
            "is, which limb performs each action, and what each is already holding or touching. An image "
            "model draws exactly what it can count, so the usual cause is not a bad model: it is the "
            "prompt asking for more simultaneous limb actions than the body has. The classic one is a "
            "body-part phrase with no owner — \"her fingers\", \"the other hand\" — arriving after both "
            "hands are already committed; that reads as one more limb, and one more limb gets drawn. "
            "Other causes: left and right contradicting each other between clauses, a prosthetic quietly "
            "written as flesh, a prop landing on a limb that is busy, one character's gesture attaching "
            "to another's body.\n"
            "In REVIEW, explain the cause in those terms — name the phrase, name the limb it implies, and "
            "say whether the same pattern appears in other shots — and do not edit yet. When the user "
            "then says fix it, rewrite only the clauses that create the contradiction, in natural prose: "
            "never by pasting a missing limb onto the end of a prompt, and never by deleting the story "
            "beat. Keep the beat, restage the pose — combine actions that fit on one limb, drop the least "
            "important, or move the moment — but never invent a limb to make an action work.\n"
            "Judge against THIS character. Four arms doing four things is correct for a four-armed "
            "character; a two-handed grip is impossible for someone the sheet gives one arm; one hand can "
            "hold a cigarette and rest on an armrest at once and that is not an error. Never normalise "
            "unusual anatomy into ordinary human anatomy, and never label every limb left or right in a "
            "pose that was already clear.\n\n"

            "- For casual conversation, questions, or feedback — just reply naturally in plain text, no JSON.\n"
            "- When the user asks you to CHANGE something (edit a shot, add dialogue, rewrite a scene, fix "
            "continuity, etc.), reply with a short explanation first, then output ONLY WHAT CHANGED as a "
            "JSON object in one ```json fenced code block```: "
            "{\"shots\": [ ...only the shot objects you actually changed... ]}. "
            "Each shot you include MUST have its \"shot\" number plus only the fields that changed on it; "
            "everything you leave out stays untouched. Change the smallest amount that fixes the problem — "
            "if only the prompt changed, send only \"shot\" and \"prompt\". "
            "Do NOT include shots that didn't change. Do NOT resend the whole story — this is applied as a "
            "merge into the existing shots by shot number, not a full replace.\n"
            "- Shot fields you may set: uses, visible_people, location, shot_desc, clothing, "
            "objects, camera, lighting, narration, speech_char, subtitle, prompt. "
            "Camera and location are yours to fix when the user asks for them.\n"
            "- Project-level fields you may set, at the top level of the same JSON object (only the ones "
            "that actually changed): story, character, scene, props, events, clothing_arc, "
            "physical_state_plan. A patch containing only a project-level field is valid on its own.\n"
            "- To INSERT a brand new shot instead of editing an existing one, add an object to \"shots\" "
            "with \"insert_after\" (the shot number it goes after — use 0 to insert as the new first shot) "
            "instead of \"shot\", and fill in enough of the fields above that the new shot is usable rather "
            "than an empty skeleton. Everything from the old next shot onward shifts down by one "
            "automatically. Never invent an unused shot number to mean insertion.\n"
            "- Never output a JSON block unless the user actually asked for an edit.\n"
            "- Answer in the user's language. Keep names, dialogue, invented terminology and culturally "
            "specific wording exactly as the project has them unless asked to change them; image prompts "
            "stay in the language the project already writes them in.\n"
            "- The project's visual style and prompt format are already decided. Keep them: do not turn a "
            "sectioned or labelled-block prompt into a paragraph, or swap the established medium or look, unless asked.\n"
            "- This is fiction, and mature, violent or disturbing material is part of some stories. Review "
            "it as craft — continuity, clarity, ownership, camera, state, structure. When you repair a "
            "prompt, never quietly add clothing, restore a removed garment, cover something up, erase an "
            "established injury or soften the beat: that silently rewrites the user's story. If you will "
            "not make a requested edit, say so plainly instead of changing something else.\n"
            f"- Whenever you write or rewrite a shot's \"prompt\" field, it MUST follow this exact format:\n{_fmt_rule}\n"
            "- Before you send a patch, re-read each prompt you wrote: with the action taken out, is the "
            "person still recognisably the character the sheet describes — right permanent traits, on the "
            "right person, nothing invented, anatomy not confused with clothing? Fix it before sending, "
            "not after.\n"
            # chat มี system prompt ของตัวเอง จึงไม่เคยเห็นกติกากล้องที่ Fill/Director ใช้ — ผลคือมัน
            # แนะนำให้ซูมเข้าเพื่อรักษารายละเอียดที่ควรถูกตัด และอ่าน POV เป็นกล้องจริงในมือคน
            f"\n{chat_camera_semantics_rule()}\n"
            f"{ANIMAL_SAFETY_RULE}"
        )

    def _sanitize_json_strings(chunk: str) -> str:
        res = []; in_s = False; esc = False
        for c in chunk:
            if esc: res.append(c); esc = False; continue
            if c == '\\': res.append(c); esc = True; continue
            if c == '"': in_s = not in_s; res.append(c); continue
            if in_s and c == '\n': res.append('\\n'); continue
            if in_s and c == '\r': continue
            res.append(c)
        return ''.join(res)

    def _extract_balanced_object(text: str, start: int):
        """หา } ที่ปิดคู่กับ { ที่ start จริง — ข้าม { } ที่อยู่ใน string. คืน (chunk, end) หรือ (None, None) ถ้าไม่สมบูรณ์ (โดนตัด)"""
        depth = 0; in_s = False; esc = False
        for j in range(start, len(text)):
            c = text[j]
            if esc: esc = False; continue
            if c == '\\': esc = True; continue
            if c == '"': in_s = not in_s; continue
            if in_s: continue
            if c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0: return text[start:j+1], j + 1
        return None, None

    def _salvage_partial_shots(text: str) -> list[dict]:
        """ข้อความโดนตัดกลาง JSON — งม '\"shots\": [' แล้วดึงเฉพาะ shot object ที่สมบูรณ์ทีละตัว ตัวที่โดนตัดคือหยุด"""
        import re as _re_sv
        m = _re_sv.search(r'"shots"\s*:\s*\[', text)
        if not m: return []
        i = m.end()
        shots = []
        while i < len(text):
            j = text.find('{', i)
            if j < 0: break
            chunk, end = _extract_balanced_object(text, j)
            if chunk is None: break   # object นี้ถูกตัดกลาง — หยุดตรงนี้ ไม่เดาต่อ
            try:
                shots.append(json.loads(chunk))
            except Exception:
                try:
                    shots.append(json.loads(_sanitize_json_strings(chunk)))
                except Exception:
                    break
            i = end
        return shots

    def _parse_story_chat_reply(raw: str):
        import re as _re_sc
        text = raw.strip()
        # หาจุดเริ่ม ``` fence (json หรือเปล่าก็ได้) แล้วหา { ตัวแรกหลังจากนั้น
        m = _re_sc.search(r'```(?:json)?\s*', text)
        start = None
        if m:
            i = text.find('{', m.end())
            if i >= 0: start = i
        if start is None:
            i = text.find('{')
            if i >= 0: start = i
        if start is None:
            return None, text, False

        chunk, end = _extract_balanced_object(text, start)
        if chunk is None:
            # ข้อความโดนตัดกลาง — เอาเท่าที่ parse ได้ แจ้งจำนวนที่กู้มาได้
            salvaged = _salvage_partial_shots(text[start:])
            explanation = text[:start].strip()
            explanation = _re_sc.sub(r'```(?:json)?\s*```?', '', explanation).strip()
            if not salvaged:
                return None, text, False
            return {"shots": salvaged}, explanation, True

        explanation = (text[:start] + text[end:]).strip()
        explanation = _re_sc.sub(r'```(?:json)?\s*```?', '', explanation).strip()
        try:
            obj = json.loads(chunk)
        except Exception:
            try:
                obj = json.loads(_sanitize_json_strings(chunk))
            except Exception:
                # parse ทั้งก้อนไม่ผ่านทั้งที่วงเล็บครบ — ลองกู้เฉพาะ shots แทนดีกว่าทิ้งหมด
                salvaged = _salvage_partial_shots(chunk)
                if salvaged:
                    return {"shots": salvaged}, explanation, True
                return None, text, False
        # ต้องตรงกับคีย์ที่ _merge_raw_data รองรับจริง ไม่งั้น patch อย่าง {"events": [...]}
        # จะถูกตีว่าไม่ใช่ JSON ที่ใช้ได้ แล้วโชว์เป็นข้อความธรรมดาทั้งที่ merge ได้
        _known_keys = ("shots", "story", "story_summary", "character", "scene",
                       "props", "events", "clothing_arc", "physical_state_plan")
        if not isinstance(obj, dict) or not any(k in obj for k in _known_keys):
            return None, text, False
        return obj, explanation, False

    def _story_chat_merge_shots(obj: dict) -> str:
        """merge patch จาก Chat — ใช้ตัวเดียวกับ Raw Merge ไม่มี semantics แยก

        เดิมฟังก์ชันนี้มีรายการฟิลด์ของตัวเอง (8 ฟิลด์ต่อช็อต / 4 ฟิลด์ระดับเรื่อง) ส่วน Raw Merge
        มี 14/8 ผลคือคำสั่งที่แตะ place/env/spacing/covers/expression/shot_scale หรือ
        props/events/clothing_arc/physical_state_plan ถูกดรอปเงียบ ๆ ตอน merge โดยไม่มีคำเตือน
        และทั้งสองทางยังต่างกันเรื่องการแปลง type (covers ที่มาเป็น list) การเลื่อนแกลเลอรี
        และการเตือนเมื่อเลขช็อตไม่มีจริง — รวมเป็นทางเดียวจึงปิดทั้ง drift และบั๊กเหล่านั้นพร้อมกัน
        คืนข้อความสรุปแบบเดียวกับ Raw Merge (เดิมคืนจำนวนช็อตเป็น int)"""
        return _merge_raw_data(obj)

    # ระดับเรื่อง: ชื่อที่เอาไว้โชว์ให้ผู้ใช้อ่าน ไม่ใช่ชื่อคีย์ดิบ
    _CHAT_TOP_LABELS = {
        "story": "Story", "story_summary": "Summary", "character": "Character",
        "scene": "Scene", "props": "Props", "events": "Events",
        "clothing_arc": "Clothing arc", "physical_state_plan": "Physical state",
    }

    def _story_chat_change_summary(obj: dict) -> str:
        """สรุปว่า patch นี้แตะอะไรบ้าง — การแก้ระดับเรื่องนับเป็นการเปลี่ยนแปลงเช่นกัน
        เดิมนับแต่ช็อต ทำให้ patch ที่แก้ character อย่างเดียวขึ้นว่า "0 shots" เหมือนไม่มีอะไรเลย"""
        _tops = [_CHAT_TOP_LABELS[k] for k in _CHAT_TOP_LABELS if k in obj]
        _n = len([_x for _x in obj.get("shots", []) if isinstance(_x, dict)])
        _parts = list(_tops)
        if _n:
            _parts.append(f"{_n} shot" + ("" if _n == 1 else "s"))
        return " + ".join(_parts) if _parts else "nothing"

    def _story_chat_show_apply_preview(obj: dict, partial: bool):
        n_shots = len([_x for _x in obj.get("shots", []) if isinstance(_x, dict)])
        total = len(shots_data)
        applied_lbl = ft.Text("", size=10, color=C["green"])
        preview_box = ft.Container()

        def _do_apply(e, _o=obj):
            _summary = _story_chat_merge_shots(_o)
            status_lbl.value = f"✅ Applied from Chat — {_summary}"; page.update()
            # APPLY-LABEL-HONESTY-A1: the label next to the button must reflect what _summary
            # actually says, not just that the button was clicked — a Manga/unknown-field payload
            # can come back with nothing applied, or applied-with-warnings, and silently showing
            # a green "Applied!" either way hid exactly that from the user.
            if _summary.startswith("nothing applied") or _summary.startswith("nothing to merge"):
                applied_lbl.value = "⚠ Nothing applied — see status"; applied_lbl.color = C["amber"]
            elif "⚠" in _summary:
                applied_lbl.value = "⚠ Applied with warnings — see status"; applied_lbl.color = C["amber"]
            else:
                applied_lbl.value = "✅ Applied!"; applied_lbl.color = C["green"]
            applied_lbl.update()

        def _do_discard(e):
            preview_box.visible = False; preview_box.update()

        _title = (f"⚠ Output was truncated — recovered {n_shots}/{total} shots (Apply updates only the recovered shots)"
                  if partial else
                  f"📝 Proposed edits — {_story_chat_change_summary(obj)} (click Apply to save)")
        preview_box.content = ft.Column([
            ft.Text(_title, size=11, weight=ft.FontWeight.W_600,
                    color=C["amber"] if partial else C["text1"]),
            ft.Row([
                ft.FilledButton("✅ Apply", on_click=_do_apply, bgcolor=C["green"],
                               style=ft.ButtonStyle(color="white")),
                ft.OutlinedButton("Discard", on_click=_do_discard),
                applied_lbl,
            ], spacing=8),
        ], spacing=6)
        preview_box.bgcolor = "#FFF7ED" if partial else C["green_lt"]
        preview_box.border_radius = br(8)
        preview_box.padding = po(left=10, top=8, right=10, bottom=8)
        story_chat_list.controls.append(preview_box)
        if story_chat_list.page: story_chat_list.update()

    _STORY_CHAT_N_CTX = 32768       # what run_llm_chat asks the local node for (PROGRAM mode); WORKFLOW mode uses the workflow's own n_ctx

    def _story_chat_context_problem(wf_tpl, system_prompt: str, text: str, fresh_text: str, max_tok: int) -> str:
        """Local model only. Estimate (it over-counts a little: the safe side) whether system prompt + the session so far + this message + the room reserved for the reply fit the
        context window. A prompt that does not fit makes the Comfy node hang the queue instead of failing, so nothing is sent and the user is told why. Returns '' when it fits."""
        n_ctx = _llm_overrides.effective_n_ctx(wf_tpl, _STORY_CHAT_N_CTX)
        _est = _fc_tokens.estimate_tokens
        _msg_oh = _fc_tokens.PER_MESSAGE_OVERHEAD
        base = _fc_tokens.BASE_OVERHEAD + _est(system_prompt) + _msg_oh
        need = base + _story_chat_tokens[0] + _est(text) + _msg_oh + max_tok
        if need <= n_ctx:
            return ""
        fresh = base + _est(fresh_text) + _msg_oh + max_tok
        if fresh > n_ctx:
            return (f"⚠ Not sent: the project is too large for this model's context window. A new chat would need about {fresh:,} tokens "
                    f"(system prompt + the whole project ≈ {base + _est(fresh_text):,}, plus {max_tok:,} reserved for the reply) but the window is {n_ctx:,}. "
                    "Lower the reply-length limit in Settings, raise n_ctx in the LLM workflow, or use Grok for this chat.")
        return (f"⚠ Not sent: this chat is full. It would need about {need:,} tokens (the earlier conversation ≈ {_story_chat_tokens[0]:,}, "
                f"plus this message and {max_tok:,} reserved for the reply) but the window is {n_ctx:,}. "
                "Press New Session (the project is sent again, the earlier conversation is dropped), then ask again.")

    async def _send_story_chat(e=None):
        if _story_chat_busy[0]: return
        msg = story_chat_input.value.strip()
        if not msg: return
        llm_wf = llm_dd.value
        if not llm_wf:
            status_lbl.value = "⚠ Select LLM first"; page.update(); return
        _is_grok = (llm_wf == _GROK_OPTION)
        story_chat_input.value = ""; story_chat_input.update()
        _story_chat_bubble(msg, is_user=True)
        _story_chat_busy[0] = True
        story_chat_status.value = "⏳ Thinking..."; story_chat_status.update()
        try:
            wf_tpl = None
            if not _is_grok:
                wf_tpl = _story_chat_wf_template[0]
                if not wf_tpl:
                    wf_tpl = json.loads((LLM_DIR / f"{llm_wf}.json").read_text(encoding="utf-8"))
                    _story_chat_wf_template[0] = wf_tpl
            # snapshot ถูกส่งครั้งเดียวต่อ session แล้วค้าง — ถ้าผู้ใช้เพิ่ม/แก้ช็อตหลังเปิดแชท
            # (เช่น มี 15 ช็อตแต่แชทยังเห็น 10) แชทจะทำงานบนโปรเจกต์เก่า จึงเทียบ snapshot ทุกครั้ง
            # แล้วส่งฉบับใหม่ทั้งก้อนเมื่อโปรเจกต์เปลี่ยน โดยไม่ล้างประวัติสนทนา
            raw_json_str = json.dumps(_build_raw_data(), ensure_ascii=False, indent=2)
            if not _story_chat_sent_json[0]:
                combined = f"=== FULL STORY JSON ===\n{raw_json_str}\n\n=== USER MESSAGE ===\n{msg}"
                if not _is_grok:
                    _problem = _story_chat_context_problem(wf_tpl, _build_story_chat_sys(), combined, combined, _llm_tokens_outline[0])
                    if _problem:
                        _story_chat_bubble(_problem, is_user=False)
                        _story_chat_busy[0] = False
                        story_chat_status.value = ""; story_chat_status.update()
                        return
                raw = await run_llm_chat(combined, wf_tpl, _story_chat_session_id[0],
                                         system_prompt=_build_story_chat_sys(), on_log=_llm_log,
                                         reset=True, max_tokens=_llm_tokens_outline[0],
                                         grok_api_key=_grok_api_key[0], grok_model=_grok_model[0])
                _story_chat_sent_json[0] = True
                _story_chat_last_json[0] = raw_json_str
                if raw:
                    _story_chat_tokens[0] = (_fc_tokens.estimate_tokens(combined) + _fc_tokens.estimate_tokens(raw)
                                             + 2 * _fc_tokens.PER_MESSAGE_OVERHEAD)
            else:
                _prev_json = _story_chat_last_json[0]
                if raw_json_str != _story_chat_last_json[0]:
                    msg = ("=== UPDATED FULL STORY JSON (the project changed since the earlier JSON; "
                           "this version REPLACES it — ignore the old one) ===\n"
                           f"{raw_json_str}\n\n=== USER MESSAGE ===\n{msg}")
                    _story_chat_last_json[0] = raw_json_str
                if not _is_grok:
                    _fresh = f"=== FULL STORY JSON ===\n{raw_json_str}\n\n=== USER MESSAGE ===\n{msg}"
                    _problem = _story_chat_context_problem(wf_tpl, _build_story_chat_sys(), msg, _fresh, _llm_tokens_outline[0])
                    if _problem:
                        _story_chat_last_json[0] = _prev_json       # nothing was sent: the next try must still see the project as changed
                        _story_chat_bubble(_problem, is_user=False)
                        _story_chat_busy[0] = False
                        story_chat_status.value = ""; story_chat_status.update()
                        return
                raw = await run_llm_chat(msg, wf_tpl, _story_chat_session_id[0],
                                         system_prompt=_build_story_chat_sys(), on_log=_llm_log,
                                         reset=False, max_tokens=_llm_tokens_outline[0],
                                         grok_api_key=_grok_api_key[0], grok_model=_grok_model[0])
                if raw:
                    _story_chat_tokens[0] += (_fc_tokens.estimate_tokens(msg) + _fc_tokens.estimate_tokens(raw)
                                              + 2 * _fc_tokens.PER_MESSAGE_OVERHEAD)
            if raw:
                obj, explanation, partial = _parse_story_chat_reply(raw)
                if explanation:
                    _story_chat_bubble(explanation, is_user=False)
                if obj:
                    _story_chat_show_apply_preview(obj, partial)
            else:
                _story_chat_bubble("⚠ No response", is_user=False)
        except Exception as ex:
            _story_chat_bubble(f"❌ {ex}", is_user=False)
        _story_chat_busy[0] = False
        story_chat_status.value = ""; story_chat_status.update()

    story_chat_input.on_submit = _send_story_chat

    story_chat_panel = ft.Container(
        content=ft.Column([
            story_chat_resize_handle,
            ft.Row([
                ft.Icon(ft.Icons.SUPPORT_AGENT, size=16, color=C["accent"]),
                ft.Text("Project Assistant", size=12, weight=ft.FontWeight.W_700, color=C["text1"]),
                ft.Container(
                    content=ft.Text("✨ Grok recommended for best results", size=10, color=C["accent"],
                                    weight=ft.FontWeight.W_500),
                    bgcolor=C["accent_lt"], border_radius=br(10), padding=po(left=8, right=8, top=2, bottom=2),
                    tooltip="The Project Assistant reasons across the whole project; Grok handles that far more reliably than a local model.",
                ),
                ft.Container(expand=True),
                story_chat_status,
                ft.TextButton("🆕 New Session", on_click=_story_chat_new_session,
                             style=ft.ButtonStyle(color=C["text2"])),
            ], spacing=8),
            story_chat_list_box,
            ft.Row([story_chat_input,
                   ft.IconButton(icon=ft.Icons.SEND_ROUNDED, icon_color=C["accent"],
                                on_click=_send_story_chat)], spacing=6),
        ], spacing=8),
        padding=po(left=14, right=14, top=10, bottom=12),
        border=ft.border.Border(top=ft.BorderSide(1, C["border"])),
    )

    _hide_chat_lbl = ft.Text("Hide", size=12, color=C["text1"], weight=ft.FontWeight.W_600)
    def _toggle_chat_panel(e):
        story_chat_panel.visible = not story_chat_panel.visible
        _hide_chat_lbl.value = "Hide" if story_chat_panel.visible else "Show"
        page.update()
    hide_chat_btn = ft.OutlinedButton(
        content=_hide_chat_lbl, on_click=_toggle_chat_panel, height=34,
        style=ft.ButtonStyle(side=ft.BorderSide(1, C["border"])),
    )

    story_actions_toolbar = ft.Container(
        content=ft.Row([
            fill_prompts_btn, check_prompts_btn, director_prompts_btn,
            vision_check_btn, vision_director_btn, insert_shot_btn,
            edit_outline_btn,
            ft.Container(expand=True),
            hide_chat_btn,
        ], spacing=8),
        padding=po(left=14, right=14, top=8, bottom=8),
        border=ft.border.Border(top=ft.BorderSide(1, C["border"])),
    )

    center_panel = ft.Container(
        expand=True,
        content=ft.Column([
            ft.Container(
                content=ft.Row([
                    ft.Text("Storyboard", size=14, weight=ft.FontWeight.W_700, color=C["text1"]),
                    ft.Container(expand=True),
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),
                padding=po(left=16, right=16, top=14, bottom=10)),
            divider(),
            ft.Container(content=shots_col, expand=True),
            story_actions_toolbar,
            story_chat_panel,
        ], spacing=0, expand=True),
    )

    # ต้องเปิด prevent_close ไว้ก่อน ไม่งั้น Flet จะปิดหน้าต่างเองก่อนที่ event close/disconnect
    # จะยิงแน่ๆ (behavior ไม่เสถียร ขึ้นกับ platform) — เราต้อง handle การปิดเองเสมอ
    page.window.prevent_close = True

    def _hard_kill():
        """ฆ่าทั้ง process tree ของแอปนี้ (backend + หน้าต่าง Flutter shell + terminal/console ที่ผูกกัน)
        ในทีเดียว — ไม่พึ่ง page.window.destroy() เพราะเป็น async method เรียกแบบ sync ไม่ await
        แล้วมันไม่ทำงานจริง (แค่สร้าง coroutine ทิ้งไว้เฉยๆ) ทำให้ต้องคลิก X ซ้ำสองครั้งกว่าจะปิดสนิท
        taskkill /T ฆ่าทั้ง subtree ในคำสั่งเดียว ไม่ต้องพึ่งจังหวะ async เลย"""
        pid = os.getpid()
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=3,
            )
        except Exception as ex:
            _clog(f"[CLOSE] taskkill failed: {ex}")
        # เผื่อ taskkill ไม่มี (ไม่ใช่ Windows) หรือฆ่าตัวเองไม่ทันก่อน timeout — os._exit(0) เป็นตาข่ายสุดท้าย
        os._exit(0)

    def on_disconnect(e):
        _clog(f"[DISCONNECT] fired — llm_busy={llm_busy[0]} is_running={_is_running[0]}")
        _stop_event.set()
        try:
            _save_state()
            _clog("[DISCONNECT] save_state OK")
        except Exception as ex:
            _clog(f"[DISCONNECT] save_state FAILED: {ex}")
            log.error(f"[DISCONNECT] save_state FAILED", exc_info=True)
        _autosave_current_project()
        _clog("[DISCONNECT] hard kill")
        _hard_kill()

    page.on_disconnect = on_disconnect

    def on_window_event(e):
        data = getattr(e, "data", None)
        etype = getattr(e, "type", None)
        name = getattr(e, "name", None)
        # skip focus/blur/resize noise — those filled the file and hid LLM outline logs
        _etype_s = str(etype or "").lower()
        if "focus" in _etype_s or "blur" in _etype_s or "resize" in _etype_s:
            return
        _clog(
            f"[WINDOW EVENT] raw={e!r} data={data!r} type={etype!r} "
            f"name={name!r} llm_busy={llm_busy[0]}"
        )
        # เดิมเช็คแบบเป๊ะ (data == "close") แล้วไม่เคยตรงกับ event จริงที่ Flet ยิงมาบนเครื่องนี้ —
        # ผ่อนเป็น substring match กว้างขึ้น event window ประเภทอื่น (focus/blur/resize) ไม่มีคำว่า
        # "close" ปนอยู่ใน data/type/name อยู่แล้ว ความเสี่ยง false-positive จึงต่ำ
        blob = " ".join(str(x).lower() for x in (data, etype, name) if x is not None)
        is_close = (
            "close" in blob
            or blob.strip() == "destroy"
            or data in ("close", "destroy")
            or (hasattr(ft, "WindowEventType") and etype == ft.WindowEventType.CLOSE)
        )
        if not is_close:
            return
        _clog("[CLOSE] confirmed — save then hard kill")
        _stop_event.set()
        try:
            _save_state()
            _clog("[CLOSE] save_state OK")
        except Exception as ex:
            _clog(f"[CLOSE] save_state FAILED: {ex}")
        _autosave_current_project()
        _clog("[CLOSE] hard kill")
        _hard_kill()

    # ตรวจ source ของ flet ที่ติดตั้งจริงแล้ว (site-packages/flet/controls/core/window.py) — event ปิด
    # หน้าต่างส่งผ่าน Window.on_event (page.window.on_event) เท่านั้น ไม่มี Page.on_window_event เลย —
    # แก้ผิดที่มาก่อน (page.on_window_event) ทำให้ handler ไม่เคยถูกเรียกจริง สร้างแค่ attribute เปล่าๆ
    # บน Page object เฉยๆ นี่คือสาเหตุจริงที่ปิดหน้าต่างไม่ได้ ไม่ใช่ปัญหาที่เงื่อนไข is_close
    page.window.on_event = on_window_event
    _clog(f"[STARTUP] WorkflowPad started — LLM outline/fill logs go to {_LOG_FILE}")

    page.add(
        ft.Column([
            top_toolbar,
            ft.Stack([
                ft.Row([left_panel, center_panel, right_panel],
                       expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.START),
                lb_overlay,
            ], expand=True),
        ], spacing=0, expand=True)
    )

    rebuild_shots_col()
    _sync_manga_agent_availability()   # MANGA-VNEXT-A1: apply saved manga_vnext_mode's UI state on load
