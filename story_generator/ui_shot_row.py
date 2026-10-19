"""
ui_shot_row.py — Shot card UI: _build_shot_row, _thumb, regen chat, add_thumb_to_shot
"""
__version__ = "20260726_1907"

import re as _re_regen
import asyncio, base64, io, json, time, uuid
from pathlib import Path

import flet as ft

from .constants  import C, STYLE_MAP
from .comfy_api  import apply_dialogue_overlay, apply_dual_text_overlay
# LLM-ENGINE-SPLIT-A1: all of this shot card's LLM needs live in the shared tools module.
from .llm_engine_tools import (
    _FILL_LEDGER_START,
    run_check_prompts, run_director_prompts, run_llm_chat,
    DEFAULT_REGEN_SYS, regen_sys_for, _call_llm_node, _call_llm_generic, _build_prompt_format_rule,
    ANIMAL_SAFETY_RULE,
)
from .ui_helpers  import po, br, divider, _dlg_to_str, _pil_overlay_text, _pil_overlay_texts, pil_overlay_applies, runtime_dialogue_prompt, TEXT_OVERLAY_MARKERS


def _build_narration_suffix(boxes, fallback_dlg=""):
    """แปลง dialogue boxes → prompt suffix สำหรับ AI วาดกล่อง"""
    import json as _bj, re as _br, ast as _ast
    NARR_POS = ["upper left corner", "upper right corner"]
    if boxes is None and fallback_dlg:
        s = (fallback_dlg or "").strip()
        try:
            parsed = _bj.loads(s)
            if isinstance(parsed, list):
                boxes = parsed; fallback_dlg = ""
            elif isinstance(parsed, dict):
                boxes = [parsed]; fallback_dlg = ""
        except Exception:
            pass
        if boxes is None:
            try:
                parsed = _ast.literal_eval(s)
                if isinstance(parsed, list):
                    boxes = parsed; fallback_dlg = ""
                elif isinstance(parsed, dict):
                    boxes = [parsed]; fallback_dlg = ""
            except Exception:
                pass
        if boxes is None and _br.search(r'\}\s*\{', s):
            try:
                boxes = _bj.loads('[' + _br.sub(r'\}\s*\{', '},{', s) + ']')
                fallback_dlg = ""
            except Exception:
                pass
    if not boxes:
        txt = (fallback_dlg or "").strip()
        if txt:
            return (
                f', with graphic text elements added over the picture: a rectangular dark narration box'
                f' in the upper left corner with white text reading "{txt}",'
                f' clean legible printed font'
            )
        return ""
    parts = []
    narr_count = 0
    for b in boxes:
        btype = str(b.get("type", "narration")).lower()
        btext = str(b.get("text", "")).strip()
        bchar = str(b.get("character", "")).strip()
        if not btext:
            continue
        if btype == "speech" and bchar:
            parts.append(
                f'a white oval speech bubble pointing toward {bchar}'
                f' containing exactly the words "{btext}" and nothing else'
            )
        else:
            pos = NARR_POS[narr_count % len(NARR_POS)]
            parts.append(
                f'a small dark narration box in the {pos}, sized to fit its text on a single line,'
                f' with white text reading exactly "{btext}" and nothing else (no filler text, no extra lines)'
            )
            narr_count += 1
    if not parts:
        return ""
    return _format_text_elements(parts)


def _format_text_elements(parts: list) -> str:
    """The text block appended to a runtime prompt (its own paragraph, NOT glued onto the STYLE line).
    Every element is told to contain exactly its own words and nothing else — image models otherwise
    smear the words of one element (e.g. the speech bubble) into another (the narration box)."""
    lines = "\n".join(f"- {p}" for p in parts)
    return (
        "\n\nText to draw — graphic text elements added over the picture, each containing only its own words:\n"
        f"{lines}\n"
        "No other lettering anywhere in the image, and no words move between elements. All text is sharp, "
        "legible, clean printed font, no hand-drawn or distorted letters."
    )


def _build_narration_from_fields(narration: str, speech_char: str, speech_text: str) -> str:
    """สร้าง prompt suffix จาก narration + speech fields แยก"""
    NARR_POS = ["upper left corner", "upper right corner"]
    parts = []
    if narration.strip():
        parts.append(
            f'a small dark narration box in the {NARR_POS[0]}, sized to fit its text on a single line,'
            f' with white text reading exactly "{narration.strip()}" and nothing else (no filler text, no extra lines)'
        )
    if speech_char.strip() and speech_text.strip():
        parts.append(
            f'a white oval speech bubble pointing toward {speech_char.strip()}'
            f' containing exactly the words "{speech_text.strip()}" and nothing else'
        )
    elif speech_text.strip():
        parts.append(f'a white oval speech bubble containing exactly the words "{speech_text.strip()}" and nothing else')
    if not parts:
        return ""
    return _format_text_elements(parts)


def _get_shot_narration_suffix(shot: dict, dialogue_style: str) -> str:
    """ดึง narration suffix จาก shot — รองรับทั้ง 3 fields ใหม่และ dialogue เก่า"""
    if dialogue_style != "narration_box":
        return ""
    narration   = shot.get("narration", "").strip()
    speech_char = shot.get("speech_char", "").strip()
    # FIELD-FALLBACK-A1: native Normal vNext shots carry "subtitle", not "speech_text" (that name
    # only survives in the Manga-vNext adapter's synthesized row and pre-migration saved projects)
    speech_text = (shot.get("subtitle") or shot.get("speech_text", "")).strip()
    if not narration and not speech_text:
        # backward compat: ลอง dialogue เก่า
        old_dlg = shot.get("dialogue", "")
        if old_dlg:
            import json as _j, ast as _a
            try:
                boxes = _j.loads(old_dlg) if isinstance(old_dlg, str) else old_dlg
                if isinstance(boxes, list):
                    return _build_narration_suffix(boxes)
            except Exception:
                try:
                    boxes = _a.literal_eval(old_dlg)
                    if isinstance(boxes, list):
                        return _build_narration_suffix(boxes)
                except Exception:
                    pass
            if isinstance(old_dlg, str):
                speech_text = old_dlg.strip()
    return _build_narration_from_fields(narration, speech_char, speech_text)


# FILL-CREATIVE-DESIGN-LEDGER-A1: main_ui registers a getter returning the current story's Fill Design
# Ledger (opaque text); Regen Prompt hands it to the model so an edit does not casually redesign
# established appearance.
_FILL_LEDGER_GETTER = [lambda: ""]


def set_fill_ledger_getter(fn) -> None:
    _FILL_LEDGER_GETTER[0] = fn


def build_shot_col(
    page: ft.Page,
    shots_data: list,
    gallery_images: dict,
    # shared state refs
    _dialogue_style: list,
    _dialogue_lang: list,
    _regen_sys_custom: list,
    _story_summary: list,
    _id_pass: list,
    _id_ref_image: list,
    _id_workflow: list,
    _seed_lock: list,
    _locked_seed: list,
    _loops_per_shot: list,
    _stop_event: asyncio.Event,
    output_dir: list,
    selected_api: list,
    # widget refs ที่ต้องอ่านค่า
    style_dd,
    llm_dd,
    sel_size: list,
    _SIZE_MAP: dict,
    # shared UI refs
    status_lbl,
    progress_ring,
    llm_busy: list,
    llm_prog,
    # callbacks
    save_state_fn,
    rebuild_shots_col_fn,
    # field refs (อ่านค่าข้ามไฟล์)
    story_field,
    char_field,
    scene_field,
    requirement_field,
    story_summary_field,
    # LLM option
    _GROK_OPTION: str,
    _grok_api_key: list,
    _grok_model: list,
    _prompt_style: list,
    # for vision / check / director reuse
    do_director_run_fn,
    do_check_run_fn,
    run_vision_fn,
    add_thumb_fn,
    _llm_log,
    _test_steps: list,
    _run_steps: list,
    run_steps_field=None,
    api_dd=None,
    size_dd=None,
    test_steps_field=None,
    id_wf_dd_ref=None,
    _loop_shot_field_ref: list = None,
    _set_running_ref: list = None,
    _is_running: list = None,
    _ext_editor: list = None,
    sync_prompt_style_fn=None,
    gen_shot_image_fn=None,
    sync_style_fn=None,
    resolve_style_kw_fn=None,
    _sel_genre: list = None,
    _sel_genre2: list = None,
    _manga_page_mode: list = None,
    open_fill_dialog_fn=None,
    current_story_id_fn=None,
    _dialogue_dd_ref: list = None,
    ensure_yolo_fn=None,
):
    """
    สร้าง shots_col (ft.Column) และ helper functions
    คืน: (shots_col, shot_rows, shot_prompt_fields, build_shot_row_fn, add_thumb_fn)
    """
    shots_col = ft.Column(spacing=0, expand=True, scroll=ft.ScrollMode.AUTO)
    _shot_rows: dict = {}
    _shot_prompt_fields: dict = {}

    from .comfy_api import run_identity_pass

    def _cur_dlg() -> str:
        """The Dialogue Style dropdown as it is RIGHT NOW (Dropdown.on_change does not always fire),
        so every render/preview/fix uses the current choice, never the one B was authored under."""
        from .constants import DIALOGUE_STYLE_MAP
        _dd = _dialogue_dd_ref[0] if _dialogue_dd_ref else None
        if _dd is not None and (_dd.value or "") in DIALOGUE_STYLE_MAP:
            _dialogue_style[0] = _dd.value
        return _dialogue_style[0]

    def _inject_dialogue(prompt: str, shot_or_dlg, style: str) -> str:
        """รับ shot dict หรือ dlg string/list"""
        if style in ("none", "narration_box_pil", "subtitle_bar"):
            return prompt
        if isinstance(shot_or_dlg, dict) and pil_overlay_applies(shot_or_dlg):
            return prompt   # non-manga text is PIL-drawn after the image, never injected into the prompt
        # ถ้าส่ง shot dict มา → อ่าน fields โดยตรง
        if isinstance(shot_or_dlg, dict):
            shot = shot_or_dlg
            if style == "speech_bubble":
                _st = (shot.get("speech_text","").strip() or shot.get("subtitle","").strip()
                       or _dlg_to_str(shot.get("dialogue","")))
                if _st and "speech bubble" not in prompt.lower():
                    return prompt + f', speech bubble with text "{_st}", white oval speech bubble, black outlined readable text'
                return prompt
            if style == "narration_box":
                if any(m in prompt for m in TEXT_OVERLAY_MARKERS) or "narration box in the" in prompt:
                    return prompt
                suffix = _build_narration_from_fields(
                    shot.get("narration","").strip(),
                    shot.get("speech_char","").strip(),
                    (shot.get("subtitle") or shot.get("speech_text","")).strip()
                )
                return prompt + suffix if suffix else prompt
            return prompt
        # fallback: dlg เป็น string/list เหมือนเดิม
        dlg_raw = shot_or_dlg
        dlg = _dlg_to_str(dlg_raw)
        if not dlg:
            return prompt
        if style == "speech_bubble":
            if "speech bubble" not in prompt.lower():
                return prompt + f', speech bubble with text "{dlg}", white oval speech bubble, black outlined readable text'
            return prompt
        if style == "narration_box":
            if any(m in prompt for m in TEXT_OVERLAY_MARKERS) or "narration box in the" in prompt:
                return prompt
            boxes = None
            try:
                raw_list = dlg_raw if isinstance(dlg_raw, list) else json.loads(dlg)
                if isinstance(raw_list, list):
                    boxes = raw_list
            except Exception:
                pass
            suffix = _build_narration_suffix(boxes, fallback_dlg=dlg)
            return prompt + suffix if suffix else prompt
        return prompt

    _thumb_registry: dict = {}   # path -> {"shot_idx": int, "fix":fn, "save":fn, "edit":fn, "reload":fn, "delete":fn} — ให้ lightbox เรียกใช้ action เดียวกับที่การ์ดใช้

    def add_thumb_to_shot(shot_idx: int, path: str, seed: int = -1, is_test: bool = False,
                          trace: dict | None = None, fill_backend: str = "unknown",
                          story_id: str = ""):
        if shot_idx not in gallery_images:
            gallery_images[shot_idx] = []
        _t = trace or {}
        gallery_images[shot_idx].append({
            "path": path, "ts": time.time(), "seed": seed, "is_test": is_test,
            "story_id": story_id, "shot": shot_idx, "fill_backend": fill_backend,
            "generation_mode": _t.get("generation_mode", "unknown"),
            "workflow": _t.get("workflow", "unknown"),
            "reference_image": _t.get("reference_image"),
            "identity_reference": _t.get("identity_reference"),
            "final_prompt": _t.get("final_prompt", ""),
        })
        if shot_idx in _shot_rows:
            row = _shot_rows[shot_idx]
            row.controls.append(_thumb(path, shot_idx, seed=seed, is_test=is_test))
            page.update()

    _thumb_cache: dict = {}  # path -> (mtime, b64_src)

    def _thumb_b64(path: str) -> str:
        try:
            mtime = Path(path).stat().st_mtime
            if _thumb_cache.get(path, (None,))[0] == mtime:
                return _thumb_cache[path][1]
            from PIL import Image as _PI
            im = _PI.open(path)
            im.thumbnail((300, 300), _PI.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=75)
            src = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
            _thumb_cache[path] = (mtime, src)
            return src
        except Exception:
            return path

    def _thumb(path: str, shot_idx: int, seed: int = -1, is_test: bool = False) -> ft.Container:
        if seed < 0 or not is_test:
            for img in gallery_images.get(shot_idx, []):
                if img["path"] == path:
                    if seed < 0: seed = img.get("seed", -1)
                    is_test = img.get("is_test", False)
                    break

        def _on_click(e, p=path):
            all_paths = [img["path"] for img in gallery_images.get(shot_idx, [])]
            # ใช้ open_lb จาก lightbox ซึ่ง inject ผ่าน page.data
            if hasattr(page, "data") and page.data and "open_lb" in page.data:
                page.data["open_lb"](p, all_paths)

        def _on_delete(e, p=path, _i=shot_idx):
            imgs = gallery_images.get(_i, [])
            gallery_images[_i] = [img for img in imgs if img["path"] != p]
            try: Path(p).unlink(missing_ok=True)
            except Exception: pass
            save_state_fn()
            if _i in _shot_rows:
                row = _shot_rows[_i]
                row.controls = [c for c in row.controls if getattr(c, "key", None) != p]
                row.update()

        async def _on_fix(e, p=path, _i=shot_idx, _seed=seed):
            if _seed < 0:
                status_lbl.value = "⚠ No seed for this image"; page.update(); return
            if llm_busy[0]:
                status_lbl.value = "⚠ LLM is busy"; page.update(); return
            if _is_running is not None and _is_running[0]:
                status_lbl.value = "⚠ Run All/Test All/Fix All is running — wait for it to finish"; page.update(); return
            if ensure_yolo_fn is not None:
                await ensure_yolo_fn()   # ask (once) before the first PIL narration render
            _stop_event.clear()  # เหมือน Test — ถ้าค้าง set จากครั้งก่อนจะ bail เงียบๆ ไม่มี error
            shot = shots_data[_i] if _i < len(shots_data) else {}
            prompt = shot.get("prompt", "")
            if not prompt:
                status_lbl.value = "⚠ No prompt"; page.update(); return
            prompt = runtime_dialogue_prompt(prompt, shot, _cur_dlg())
            _style_kw = (resolve_style_kw_fn(shot.get("_dir_scale_category") or shot.get("shot_scale", "medium")) if resolve_style_kw_fn else
                         STYLE_MAP.get((sync_style_fn() if sync_style_fn else style_dd.value) or "none", ("", ""))[1])
            print(f"[FIX shot {_i+1}] style_kw={_style_kw!r} | has_resolve_fn={resolve_style_kw_fn is not None}")
            print(f"[FIX shot {_i+1}] PROMPT:\n{prompt}\n{'='*60}")
            # re-sync จาก dropdown ตรงๆ — Dropdown.on_change บางครั้งไม่ยิง event ให้ selected_api/sel_size ตามทัน
            if api_dd is not None and api_dd.value: selected_api[0] = api_dd.value
            if size_dd is not None and size_dd.value: sel_size[0] = size_dd.value
            w, h = _SIZE_MAP.get(sel_size[0], (2304, 1296))
            status_lbl.value = f"🔧 Fix shot {_i+1} seed={_seed}..."
            progress_ring.visible = True; page.update()
            out, _, _trace = await gen_shot_image_fn(_i, prompt, selected_api[0], w, h, _i + 1,
                                   fixed_seed=_seed, stop_event=_stop_event, style_kw=_style_kw)
            if out:
                _fix_dlg_style = _cur_dlg()
                if pil_overlay_applies(shot):
                    _fix_narr_t, _fix_sub_t = _pil_overlay_texts(shot, _fix_dlg_style)
                    if _fix_narr_t or _fix_sub_t:
                        _fix_dlg_pos = shot.get("dialogue_position", "bottom")
                        await asyncio.get_event_loop().run_in_executor(
                            None, apply_dual_text_overlay, out, _fix_narr_t, _fix_sub_t, _fix_dlg_style, _fix_dlg_pos)
                imgs = gallery_images.get(_i, [])
                for idx2, img in enumerate(imgs):
                    if img["path"] == p:
                        imgs[idx2] = {
                            "path": out, "ts": time.time(), "seed": _seed,
                            "is_test": img.get("is_test", False),
                            "story_id": (current_story_id_fn() if current_story_id_fn else ""),
                            "shot": _i, "fill_backend": shot.get("_fill_backend", "unknown"),
                            "generation_mode": _trace.get("generation_mode", "unknown"),
                            "workflow": _trace.get("workflow", "unknown"),
                            "reference_image": _trace.get("reference_image"),
                            "identity_reference": _trace.get("identity_reference"),
                            "final_prompt": _trace.get("final_prompt", ""),
                        }
                        break
                try: Path(p).unlink(missing_ok=True)
                except Exception: pass
                save_state_fn()
                if _i < len(shots_col.controls):
                    _shot_rows.pop(_i, None)
                    _shot_prompt_fields.pop(_i, None)
                    _build_shot_row(_i, shots_data[_i] if _i < len(shots_data) else {})
                    shots_col.controls[_i] = shots_col.controls.pop()
                status_lbl.value = f"✅ Fix shot {_i+1} done"
            else:
                status_lbl.value = f"⚠ Fix: no image returned"
            progress_ring.visible = False; page.update()

        def _on_edit_external(e, p=path):
            import subprocess
            editor = (_ext_editor[0] if _ext_editor else "").strip() or "mspaint.exe"
            try:
                subprocess.Popen([editor, p])
                status_lbl.value = "📂 Opened in external app — edit then click Reload"
            except Exception as ex:
                status_lbl.value = f"⚠ Cannot open: {ex}"
            page.update()

        async def _on_reload(e, p=path, _i=shot_idx):
            if not Path(p).exists():
                status_lbl.value = f"⚠ File not found: {Path(p).name}"; page.update(); return
            imgs = gallery_images.get(_i, [])
            for idx2, img in enumerate(imgs):
                if img["path"] == p:
                    imgs[idx2] = {**img, "ts": Path(p).stat().st_mtime}
                    break
            save_state_fn()
            if _i < len(shots_col.controls):
                _shot_rows.pop(_i, None); _shot_prompt_fields.pop(_i, None)
                _build_shot_row(_i, shots_data[_i] if _i < len(shots_data) else {})
                shots_col.controls[_i] = shots_col.controls.pop()
            status_lbl.value = f"✅ Reload shot {_i+1} done"; page.update()

        async def _on_save(e, p=path, _i=shot_idx):
            import shutil as _sh
            fp = ft.FilePicker()
            result_path = await fp.save_file(
                dialog_title="Save Image",
                file_name=Path(p).name,
                allowed_extensions=["jpg", "jpeg", "png"],
            )
            if result_path:
                try:
                    dest = Path(result_path)
                    if not dest.suffix: dest = dest.with_suffix(".jpg")
                    _sh.copy2(p, str(dest))
                    status_lbl.value = f"✅ Saved: {dest.name}"
                except Exception as ex:
                    status_lbl.value = f"⚠ Save failed: {ex}"
                page.update()

        save_btn = ft.Container(
            content=ft.Icon(ft.Icons.SAVE_ALT_ROUNDED, size=13, color="white"),
            width=24, height=24, bgcolor="#99000000", border_radius=br(4),
            alignment=ft.Alignment(0, 0), tooltip="Save image",
            on_click=_on_save, left=28, top=2,
        )
        fix_btn = ft.Container(
            content=ft.Icon(ft.Icons.BUILD_ROUNDED, size=13, color="white"),
            width=24, height=24, bgcolor="#99000000", border_radius=br(4),
            alignment=ft.Alignment(0, 0), visible=seed >= 0,
            tooltip=f"Fix seed={seed}", on_click=_on_fix, left=2, top=2,
        )
        edit_ext_btn = ft.Container(
            content=ft.Icon(ft.Icons.OPEN_IN_NEW_ROUNDED, size=13, color="white"),
            width=24, height=24, bgcolor="#99000000", border_radius=br(4),
            alignment=ft.Alignment(0, 0), tooltip="Edit in external app",
            on_click=_on_edit_external, left=54, top=2,
        )
        reload_btn = ft.Container(
            content=ft.Icon(ft.Icons.REFRESH_ROUNDED, size=13, color="white"),
            width=24, height=24, bgcolor="#9900AA88", border_radius=br(4),
            alignment=ft.Alignment(0, 0), tooltip="Reload after external edit",
            on_click=_on_reload, left=80, top=2,
        )
        delete_btn = ft.Container(
            content=ft.Icon(ft.Icons.CLOSE_ROUNDED, size=14, color="white"),
            width=24, height=24, bgcolor="#99000000", border_radius=br(4),
            alignment=ft.Alignment(0, 0), on_click=_on_delete, right=2, top=2,
        )
        seed_label = ft.Container(
            content=ft.Text(
                f"{'🧪' if is_test else '🌱'}{seed}" if seed >= 0 else ("🧪" if is_test else ""),
                size=8, color="white",
            ),
            bgcolor="#88000000", padding=po(left=3, right=3, top=1, bottom=1),
            border_radius=br(3), left=2, bottom=2,
            visible=seed >= 0 or is_test,
        )
        _thumb_registry[path] = {
            "shot_idx": shot_idx,
            "fix": _on_fix, "save": _on_save, "edit": _on_edit_external,
            "reload": _on_reload, "delete": _on_delete,
        }
        return ft.Container(
            key=path,
            width=150, height=150, border_radius=br(6),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            on_click=_on_click,
            content=ft.Stack([
                ft.Image(src=_thumb_b64(path), fit="cover", width=150, height=150),
                delete_btn, fix_btn, save_btn, edit_ext_btn, reload_btn, seed_label,
            ]),
        )

    def _build_shot_row(i: int, shot: dict):
        # Thumbnails wrap onto new lines as the panel gets narrower (flex-wrap style) instead of
        # scrolling sideways; the card grows to fit, the page's own vertical scroll does the rest.
        row = ft.Row(spacing=6, run_spacing=6, wrap=True,
                     vertical_alignment=ft.CrossAxisAlignment.START)
        _shot_rows[i] = row
        imgs = gallery_images.get(i, [])
        for img_item in imgs:
            row.controls.append(_thumb(img_item["path"], i))

        _narration   = shot.get("narration", "").strip()
        _speech_char = shot.get("speech_char", "").strip()
        # FIELD-FALLBACK-A1: native Normal vNext shots carry "subtitle", not "speech_text" (that
        # name only survives in the Manga-vNext adapter's synthesized row and pre-migration saved
        # projects) — read speech_text first (Manga/old-saved), fall back to subtitle (native).
        _speech_text = (shot.get("speech_text") or shot.get("subtitle", "")).strip()
        # backward compat: ถ้าไม่มี narration fields ให้ดู dialogue เก่า
        _old_dlg = shot.get("dialogue", "")
        if not _narration and not _speech_text and _old_dlg:
            _speech_text = _dlg_to_str(_old_dlg)
        dialogue = " | ".join(filter(None, [_narration[:40]+"…" if len(_narration)>40 else _narration, f"{_speech_char}: {_speech_text}" if _speech_char else _speech_text]))
        _shot_label = f"Shot {shot.get('shot', i+1)}"
        # FIELD-FALLBACK-A1: scene_desc is what the Manga-vNext adapter synthesizes and what
        # pre-migration saved projects carry; native Normal vNext shots carry shot_desc instead
        # and never had scene_desc. scene_desc checked first (opposite order from the Review
        # Outline dialog, which is Normal-vNext-only and correctly prioritizes shot_desc there) —
        # this component is shared by both pipelines plus old-saved-project display for both.
        # Normal vNext shots carry a story: "Shot N: <story>" on the first line, desc below it.
        _desc_txt = shot.get('scene_desc') or shot.get('shot_desc', '')
        _story_txt = (shot.get("story") or "").strip()
        scene_lbl = ft.Text(
            f"{_shot_label}: {_story_txt}\n{_desc_txt}" if _story_txt
            else f"{_shot_label}  •  {_desc_txt}",
            size=11, weight=ft.FontWeight.W_600, color=C["text2"],
        )
        _cur_pos = shot.get("dialogue_position", "bottom")
        _pos_icon = "⬆" if _cur_pos == "top" else "⬇"

        def _make_toggle_pos(_ii=i, _s=shot):
            async def _toggle(e):
                cur = _s.get("dialogue_position", "bottom")
                _s["dialogue_position"] = "top" if cur == "bottom" else "bottom"
                save_state_fn()
                if _ii < len(shots_col.controls):
                    _shot_rows.pop(_ii, None); _shot_prompt_fields.pop(_ii, None)
                    _build_shot_row(_ii, _s)
                    shots_col.controls[_ii] = shots_col.controls.pop()
                page.update()
            return _toggle

        pos_toggle_btn = ft.OutlinedButton(
            content=ft.Text(_pos_icon, size=12, color=C["purple"]),
            tooltip=f"Dialogue position: {_cur_pos} (click to toggle)",
            on_click=_make_toggle_pos(),
            height=26,
            style=ft.ButtonStyle(
                side=ft.BorderSide(1, C["purple"]),
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=po(left=6, right=6, top=0, bottom=0),
            ),
            # PIL วาดที่ขอบล่างอย่างเดียวแล้ว ปุ่มสลับบน/ล่างจึงไม่มีอะไรให้สลับ — ซ่อนไว้
            visible=False,
        )

        dialogue_lbl = ft.Container(
            visible=bool(dialogue),
            content=ft.Row([
                ft.Icon(ft.Icons.CHAT_BUBBLE_OUTLINE_ROUNDED, size=12, color=C["purple"]),
                ft.Text(f'"{dialogue}"', size=11, color=C["purple"], italic=True,
                        no_wrap=False, overflow=ft.TextOverflow.VISIBLE, expand=True),
                pos_toggle_btn,
            ], spacing=4),
        )

        prompt_field = ft.TextField(
            value=shot.get("prompt", ""),
            multiline=True, min_lines=2,   # ไม่ตั้ง max_lines — ให้ขยายสูงเต็มตามความยาว prompt ไม่มี scroll ใน field
            text_size=11, expand=True,
            border_color=C["border"], focused_border_color=C["accent"],
            bgcolor=C["surface"],
            content_padding=po(left=8, right=8, top=6, bottom=6),
        )
        _shot_prompt_fields[i] = prompt_field

        def _on_prompt_change(e, _i=i, _f=prompt_field):
            shots_data[_i]["prompt"] = _f.value
            save_state_fn()
        prompt_field.on_change = _on_prompt_change

        # ── Check / Director badges ───────────────────────────────────
        def _make_badge(status_key, issues_key, reason_key, ok_text, edit_icon, ok_color, edit_color, ok_bg, edit_bg):
            _cs = shot.get(status_key)
            _ci = shot.get(issues_key, [])
            _cr = shot.get(reason_key, "")
            _prompt_empty = not shot.get("prompt", "").strip()
            if _prompt_empty and _cs in ("ok", "edited"):
                _cs = "error"; _cr = "⚠ prompt missing after check"; _ci = []
            if _cs == "ok":
                color, bg, icon = ok_color, ok_bg, ft.Icons.CHECK_CIRCLE_ROUNDED
                text, detail = ok_text, ""
            elif _cs == "edited":
                color, bg, icon = edit_color, edit_bg, edit_icon
                text = f"✏ Revised — {_cr}" if _cr else "✏ Revised"
                detail = "\n".join(f"• {iss}" for iss in _ci) if _ci else ""
            elif _cs == "error":
                color, bg, icon = C["red"], C["red_lt"], ft.Icons.WARNING_ROUNDED
                text = f"⚠ {_cr}" if _cr else "⚠ Check failed"
                detail = ""
            else:
                return ft.Container(visible=False)
            # expand + no_wrap=False: a long Director/Check reason must wrap inside the badge, not run off the card edge
            children = [ft.Row([ft.Icon(icon, size=12, color=color),
                                 ft.Text(text, size=10, color=color, weight=ft.FontWeight.W_600,
                                         expand=True, no_wrap=False, selectable=True)],
                                spacing=4, vertical_alignment=ft.CrossAxisAlignment.START)]
            if _ci:
                for iss in _ci:
                    children.append(ft.Text(f"• {iss}", size=9, color=color, italic=True, selectable=True))
            elif detail:
                children.append(ft.Text(detail, size=9, color=color, italic=True, selectable=True))
            return ft.Container(
                content=ft.Column(children, spacing=2, tight=True),
                bgcolor=bg, border_radius=br(5),
                padding=po(left=8, top=4, right=8, bottom=4),
                border=ft.Border(left=ft.BorderSide(3, color)),
            )

        check_badge    = _make_badge("_check_status", "_check_issues", "_check_reason",
                                     "✅ Looks good", ft.Icons.EDIT_NOTE_ROUNDED,
                                     C["green"], C["amber"], C["green_lt"], "#FFF9EC")
        director_badge = _make_badge("_dir_status", "_dir_issues", "_dir_reason",
                                     "🎬 Camera/lighting looks good", ft.Icons.MOVIE_CREATION_ROUNDED,
                                     "#0F766E", "#0F766E", "#F0FDFA", "#F0FDFA")

        _vs = shot.get("_vision_status")
        _vi = shot.get("_vision_issues", [])
        _vr = shot.get("_vision_reason", "")
        if _vs == "ok":
            vision_badge = ft.Container(
                content=ft.Row([ft.Icon(ft.Icons.VISIBILITY_ROUNDED, size=12, color="#0369A1"),
                                 ft.Text("👁 Image matches prompt", size=10, color="#0369A1",
                                         weight=ft.FontWeight.W_600)], spacing=4, tight=True),
                bgcolor="#F0F9FF", border_radius=br(5),
                padding=po(left=8, top=4, right=8, bottom=4),
                border=ft.Border(left=ft.BorderSide(3, "#0369A1")),
            )
        elif _vs == "edited":
            vchildren = [ft.Row([ft.Icon(ft.Icons.MANAGE_SEARCH_ROUNDED, size=12, color="#B45309"),
                                  ft.Text(f"👁 Vision revised — {_vr}" if _vr else "👁 Vision revised",
                                          size=10, color="#B45309", weight=ft.FontWeight.W_600,
                                          expand=True, no_wrap=False, selectable=True)],
                                 spacing=4, vertical_alignment=ft.CrossAxisAlignment.START)]
            for iss in _vi:
                vchildren.append(ft.Text(f"• {iss}", size=9, color="#B45309", italic=True, selectable=True))
            vision_badge = ft.Container(content=ft.Column(vchildren, spacing=2, tight=True),
                                        bgcolor="#FFFBEB", border_radius=br(5),
                                        padding=po(left=8, top=4, right=8, bottom=4),
                                        border=ft.Border(left=ft.BorderSide(3, "#B45309")))
        else:
            vision_badge = ft.Container(visible=False)

        # ── Regen Chat ────────────────────────────────────────────────
        _regen_session_id: list[str]   = [uuid.uuid4().hex[:8]]
        _regen_wf_template: list       = [None]
        _regen_shot_idx: list[int]     = [i]
        _regen_field_ref: list         = [prompt_field]
        _regen_initialized: list[bool] = [False]
        _regen_init_msg: list[str]     = [""]
        # True เมื่อ _regen_session_id มาจาก session ที่ Fill ใช้เขียน prompt นี้จริง (ไม่ใช่ session
        # ใหม่ที่สุ่มเอง) — first-turn call ของแชทจะได้ reset_session=False เพื่อต่อบริบทเดิม
        # แทนที่จะเริ่มเปล่า
        _regen_is_continuation: list[bool] = [False]
        # REGEN-CHAT-PERSIST-A1: True after the dialog has been opened (and its session/briefing
        # set up) once for this row's lifetime — closing and reopening the SAME shot's dialog must
        # not wipe an in-progress conversation. Only the 🔄 refresh button, or genuinely never
        # having opened it before, starts a fresh session.
        _regen_opened: list[bool] = [False]

        regen_chat_list = ft.ListView(spacing=6, expand=True, auto_scroll=True)
        regen_input = ft.TextField(
            hint_text="Type a request, e.g. make it darker, change camera...",
            text_size=11, expand=True,
            border_color=C["border"], focused_border_color=C["purple"],
            bgcolor=C["surface"],
            content_padding=po(left=8, right=8, top=6, bottom=6),
        )
        regen_busy: list[bool] = [False]

        def _add_chat_bubble(text: str, is_user: bool):
            bubble = ft.Container(
                content=ft.Text(text, size=10, selectable=True,
                               color="white" if is_user else C["text1"]),
                bgcolor=C["purple"] if is_user else C["panel"],
                border_radius=br(8),
                padding=po(left=10, top=6, right=10, bottom=6),
                margin=ft.Margin(left=60 if is_user else 0, right=0 if is_user else 60, top=0, bottom=0),
            )
            regen_chat_list.controls.append(bubble)
            if regen_chat_list.page: regen_chat_list.update()

        def _build_regen_sys():
            # โทนของบรรทัดเปิดเลือกตามปลายทาง (API ได้โทนกลาง) — ข้อความที่ผู้ใช้แก้เองชนะเสมอ
            base = regen_sys_for(llm_dd.value or "", _regen_sys_custom[0])
            # ต่อ session เดิมของ Fill ได้ (reset=False) แต่ session prompt ที่ยัดเข้ามาใหม่นี้เป็นคนละ
            # persona กับตอน Fill เขียน prompt (director vs. editor) — ถ้าไม่บอกตรง ๆ ว่านี่คือ
            # บทสนทนาต่อเนื่อง โมเดลจะเข้าใจว่าเจอ prompt นี้ครั้งแรก แล้วตอบแบบแนะนำตัวใหม่ ทั้งที่
            # user ต้องการถามเหตุผลจริงของตอนที่มันเพิ่งเขียนไป
            if _regen_is_continuation[0]:
                base = (
                    "CONTINUING YOUR OWN SESSION: earlier in this same conversation, you wrote "
                    "this shot's Krea2 image prompt under the Minimal V3 Fill contract. You are "
                    "not a new assistant meeting this shot for the first time. When the user asks "
                    "why you wrote it a certain way, answer from your own actual reasoning in that "
                    "generation — do not deflect into a generic introduction. You may still act as "
                    "the editor described below and apply changes when the user asks for one.\n\n"
                    + base
                )
            _i = _regen_shot_idx[0]
            # FIELD-FALLBACK-A1: scene_desc first (Manga-vNext adapter + pre-migration saved
            # projects both carry it), shot_desc as fallback (native Normal vNext only). "mood" has
            # no native replacement at all — Normal vNext dropped it outright rather than renaming
            # it, so it's simply empty (not wrong) for native shots; left unfallbacked on purpose.
            def _sd(s): return s.get("scene_desc") or s.get("shot_desc", "")
            ctx = "\n--- STORY CONTEXT ---\n"
            if char_field.value.strip():
                ctx += f"Characters: {char_field.value.strip()}\n"
            if scene_field.value.strip():
                ctx += f"Setting: {scene_field.value.strip()}\n"
            if _i - 2 >= 0:
                prev2 = shots_data[_i - 2]
                ctx += f"\nPrevious Shot ({_i-1}): {_sd(prev2)} [mood: {prev2.get('mood','')}]\n"
            if _i - 1 >= 0:
                prev1 = shots_data[_i - 1]
                ctx += (f"\nPrevious Shot ({_i}) — most recent:\nScene: {_sd(prev1)} [mood: {prev1.get('mood','')}]\n"
                        f"Prompt:\n{prev1.get('prompt','')}\n")
            if _i < len(shots_data):
                cur = shots_data[_i]
                ctx += f"\nCurrent Shot ({_i+1}) — THIS IS WHAT YOU ARE EDITING:\n"
                ctx += f"Scene: {_sd(cur)} [mood: {cur.get('mood','')}]\n"
                if (cur.get("story") or "").strip():
                    ctx += f"Story (narrative core — read first; Scene above is the factual constraint): {cur['story'].strip()}\n"
                _clothing = cur.get("clothing_state") or cur.get("clothing")
                if _clothing:
                    ctx += f"clothing: {_clothing}\n"
                _objects = cur.get("prop_state") or cur.get("objects")
                if _objects:
                    ctx += f"objects: {_objects}\n"
            if _i + 1 < len(shots_data):
                nxt = shots_data[_i + 1]
                ctx += f"\nNext Shot ({_i+2}): {_sd(nxt)} [mood: {nxt.get('mood','')}]\n"
            ctx += "\nYour job: improve and refine the CURRENT SHOT prompt based on user requests.\n"
            # ใช้ตัว resync กลางตัวเดียวกับทุกจุดใน main_ui.py (Dropdown.on_change ไม่ fire เสมอ)
            if sync_prompt_style_fn: sync_prompt_style_fn()
            _fmt_rule, _ = _build_prompt_format_rule(_prompt_style[0])
            ctx += f"\nWhenever you output the rewritten prompt, it MUST follow this exact format:\n{_fmt_rule}\n"
            # Fill writes labelled blocks (MAIN IMAGE DESCRIPTION / FRAME LAYOUT / CAMERA / LIGHT / ... LOCKs).
            # The legacy format rule above is a flat paragraph; when the current prompt already has blocks,
            # the labelled structure wins (structural header match only - no semantic inspection).
            _cur_prompt = (shots_data[_i].get("prompt") or "") if _i < len(shots_data) else ""
            _hdrs = _re_regen.findall(r"(?m)^(MAIN IMAGE DESCRIPTION|PAGE LAYOUT|PANEL \d+|FRAME LAYOUT|CAMERA|LIGHT|"
                                      r"IDENTITY LOCK|LOCATION LOCK|OBJECT LOCK|STYLE):[ \t]*$", _cur_prompt)
            if _hdrs:
                ctx += (
                    "\nBLOCK FORMAT OVERRIDE (highest priority for the \"prompt\" field): the CURRENT prompt is written as "
                    "labelled blocks. Your revised prompt MUST keep exactly this structure - the same labels, in this order, "
                    "each label on its own line followed by its text, blocks separated by a blank line: "
                    + ", ".join(_hdrs) + ". Rewrite only the blocks the user's request affects and copy every other "
                    "block unchanged. Do NOT merge the blocks into one paragraph, drop a label, or add one. The flat-paragraph "
                    "format and the OPENING RULE above do not apply to a labelled prompt; CAMERA stays in its own block. "
                    "Inside the JSON string write the line breaks as \\n.\n"
                )
            ctx += f"\n{ANIMAL_SAFETY_RULE}\n"
            return base + ctx

        async def _send_regen_chat(e=None):
            if regen_busy[0]: return
            msg = regen_input.value.strip()
            if not msg: return
            llm_wf = llm_dd.value
            if not llm_wf: return
            _is_grok = (llm_wf == _GROK_OPTION)
            regen_input.value = ""; regen_input.update()
            _add_chat_bubble(msg, is_user=True)
            regen_busy[0] = True
            try:
                wf_tpl = None
                if not _is_grok:
                    wf_tpl = _regen_wf_template[0]
                    if not wf_tpl:
                        from .constants import LLM_DIR
                        wf_tpl = json.loads((LLM_DIR / f"{llm_wf}.json").read_text(encoding="utf-8"))
                        _regen_wf_template[0] = wf_tpl
                if not _regen_initialized[0]:
                    combined = f"{_regen_init_msg[0]}\n\nUser says: {msg}"
                    # ต่อ session เดิมของ Fill (ไม่ reset) เมื่อ session_id นี้มาจาก Fill จริง — reset
                    # เฉพาะตอนเป็น session ใหม่ที่สุ่มเอง (Grok-filled shot หรือกดปุ่มรีเฟรชแชท)
                    raw = await run_llm_chat(combined, wf_tpl, _regen_session_id[0],
                                             system_prompt=_build_regen_sys(), on_log=_llm_log,
                                             reset=not _regen_is_continuation[0], max_tokens=4096,
                                             grok_api_key=_grok_api_key[0], grok_model=_grok_model[0])
                    _regen_initialized[0] = True
                else:
                    raw = await run_llm_chat(msg, wf_tpl, _regen_session_id[0],
                                             system_prompt=_build_regen_sys(), on_log=_llm_log,
                                             reset=False, max_tokens=4096,
                                             grok_api_key=_grok_api_key[0], grok_model=_grok_model[0])
                if raw:
                    import re as _re3
                    def _fix_r(t):
                        t = t.strip()
                        t = _re3.sub(r'^```(?:json)?\s*', '', t)
                        t = _re3.sub(r'```\s*$', '', t).strip()
                        try:
                            s = t.index('{'); e = t.rindex('}') + 1
                            res = []; in_s = False
                            for c in t[s:e]:
                                if c == '"': in_s = not in_s
                                if in_s and c == '\n': res.append('\\n')
                                else: res.append(c)
                            return json.loads(''.join(res))
                        except Exception: return None
                    obj = _fix_r(raw)
                    # fallback: ถ้า LLM ตอบ plain-text prompt (sectioned_detailed etc.) ให้ wrap เป็น obj
                    if not obj or "prompt" not in obj:
                        stripped = raw.strip()
                        if stripped and not stripped.startswith(("{", "[")):
                            obj = {"prompt": stripped}
                    # a continued Fill session may append its design-ledger block: transport metadata,
                    # never part of the prompt
                    if obj and isinstance(obj.get("prompt"), str) and _FILL_LEDGER_START in obj["prompt"]:
                        obj["prompt"] = obj["prompt"].split(_FILL_LEDGER_START, 1)[0].strip()
                    if obj and "prompt" in obj:
                        idx = _regen_shot_idx[0]
                        preview = obj["prompt"]
                        def _make_save_btn(o=obj, _idx=idx):
                            saved_lbl = ft.Text("", size=10, color=C["green"])
                            def _do_save(e, _o=o, _i=_idx):
                                shots_data[_i]["prompt"] = _o["prompt"]
                                # REGEN-NATIVE-SCHEMA-A1: native Normal vNext fields only
                                # (shot_desc/narration/speech_char/subtitle) — "shot_desc"/"subtitle"
                                # replace the old "scene_desc"/"speech_text" keys so Regen never
                                # leaves a stray parallel pre-migration key on a native shot. This
                                # save button is only reachable via the chat-based Regen dialog,
                                # which is disabled entirely for Manga vNext shots (see
                                # regen_chat_btn below) — it never runs against a
                                # shot["_manga_page_spec"] shot.
                                # MANGA-PAGE-ORPHAN-AUTHORITY-CLEANUP-A1 / REGEN-OLD-SHOT-UPGRADE-A1:
                                # "shot_desc" was missing from this tuple entirely (DEFAULT_REGEN_SYS
                                # asks the LLM for it, but it was silently discarded on save), and the
                                # legacy pre-migration keys ("scene_desc"/"speech_text") were never
                                # removed from the touched shot — so a pre-migration shot ended up
                                # with native + legacy aliases sitting side by side after a Regen
                                # save. Fixed here: write shot_desc too, and drop the legacy aliases
                                # from THIS shot only (never a bulk/untouched-project migration).
                                # CLEAR-FIELD-A1: key presence, not value truthiness — a truthiness
                                # check can never let Regen intentionally clear a field back to "".
                                for _f in ("shot_desc","narration","speech_char","subtitle"):
                                    if _f in _o: shots_data[_i][_f] = _o[_f]
                                for _legacy in ("scene_desc","speech_text"):
                                    shots_data[_i].pop(_legacy, None)
                                save_state_fn(); rebuild_shots_col_fn()
                                saved_lbl.value = "✅ Saved!"; saved_lbl.update()
                            return ft.Container(
                                content=ft.Column([
                                    ft.Text(preview, size=10, selectable=True, color=C["text1"]),
                                    ft.Row([ft.FilledButton(
                                        content=ft.Row([ft.Icon(ft.Icons.SAVE_ROUNDED, size=13, color="white"),
                                                        ft.Text("Save Prompt", size=11, color="white")], tight=True, spacing=4),
                                        on_click=_do_save, bgcolor=C["green"], height=32,
                                        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))), saved_lbl], spacing=8),
                                ], spacing=6),
                                bgcolor=C["green_lt"], border_radius=br(8),
                                padding=po(left=10, top=8, right=10, bottom=8),
                            )
                        regen_chat_list.controls.append(_make_save_btn())
                        if regen_chat_list.page: regen_chat_list.update()
                    else:
                        _add_chat_bubble(raw.strip(), is_user=False)
                else:
                    _add_chat_bubble("⚠ No response", is_user=False)
            except Exception as ex:
                _add_chat_bubble(f"❌ {ex}", is_user=False)
            regen_busy[0] = False

        regen_input.on_submit = _send_regen_chat

        regen_sys_field = ft.TextField(
            value=_regen_sys_custom[0] or DEFAULT_REGEN_SYS,
            multiline=True, min_lines=10, max_lines=20,
            text_size=10, expand=True,
            border_color=C["border"], focused_border_color=C["purple"],
            bgcolor=C["surface"],
            content_padding=po(left=8, right=8, top=6, bottom=6),
        )

        def _save_regen_sys(e):
            val = regen_sys_field.value.strip()
            _regen_sys_custom[0] = "" if val == DEFAULT_REGEN_SYS.strip() else val
            save_state_fn(); regen_sys_dialog.open = False; page.update()

        def _reset_regen_sys(e):
            regen_sys_field.value = DEFAULT_REGEN_SYS
            _regen_sys_custom[0] = ""; save_state_fn(); page.update()

        regen_sys_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Regen Chat System Prompt", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
            bgcolor=C["surface"],
            content=ft.Container(width=560, expand=True,
                content=ft.Column([regen_sys_field], expand=True, tight=True)),
            actions=[
                ft.TextButton("Reset Default", on_click=_reset_regen_sys, style=ft.ButtonStyle(color=C["text3"])),
                ft.TextButton("Cancel", on_click=lambda e: setattr(regen_sys_dialog, "open", False) or page.update(),
                             style=ft.ButtonStyle(color=C["text2"])),
                ft.TextButton("Save", on_click=_save_regen_sys, style=ft.ButtonStyle(color=C["purple"])),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.overlay.append(regen_sys_dialog)

        regen_extra_field = ft.TextField(
            hint_text="Additional instructions (optional), e.g. 'dramatic lighting, no wide shot'",
            text_size=11, height=40,
            border_color=C["border"], focused_border_color=C["purple"],
            bgcolor=C["surface"],
            content_padding=po(left=8, right=8, top=4, bottom=4),
        )

        async def _refresh_regen_chat(e):
            # REGEN-CHAT-PERSIST-A1: _regen_this_prompt is defined further below in this same
            # row's closure — resolved by name at call time, well after that definition exists.
            _regen_opened[0] = False
            await _regen_this_prompt(e)

        regen_dialog = ft.AlertDialog(
            modal=False,
            title=ft.Row([
                ft.Text("🎨 Regen Prompt", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
                ft.Container(expand=True),
                ft.IconButton(ft.Icons.SETTINGS_ROUNDED, icon_size=16, icon_color=C["text3"],
                              on_click=lambda e: (setattr(regen_sys_field, "value", _regen_sys_custom[0] or DEFAULT_REGEN_SYS),
                                                  setattr(regen_sys_dialog, "open", True), page.update()),
                              width=30, height=30, padding=0),
                ft.IconButton(ft.Icons.REFRESH_ROUNDED, icon_size=16, icon_color=C["text3"],
                              tooltip="Start a fresh session for this shot",
                              # REGEN-CHAT-PERSIST-A1: explicit reset — clears _regen_opened so
                              # _regen_this_prompt (called below, once defined) rebuilds a brand-
                              # new session + briefing right away, instead of just wiping the
                              # visible bubbles and leaving the dialog blank forever after.
                              on_click=_refresh_regen_chat,
                              width=30, height=30, padding=0),
            ]),
            bgcolor=C["surface"],
            content=ft.Container(width=520, content=ft.Column([
                regen_extra_field,
                ft.Container(content=regen_chat_list, height=280,
                             border=ft.Border(left=ft.BorderSide(1, C["border"]), right=ft.BorderSide(1, C["border"]),
                                              top=ft.BorderSide(1, C["border"]), bottom=ft.BorderSide(1, C["border"])),
                             border_radius=br(6), padding=po(left=8, top=8, right=8, bottom=8)),
                ft.Row([regen_input,
                        ft.IconButton(ft.Icons.SEND_ROUNDED, icon_color=C["purple"],
                                      on_click=_send_regen_chat, width=36, height=36, padding=0)], spacing=6),
            ], spacing=8, tight=True, expand=True)),
            actions=[ft.TextButton("Close", on_click=lambda e: setattr(regen_dialog, "open", False) or page.update(),
                                   style=ft.ButtonStyle(color=C["text2"]))],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.overlay.append(regen_dialog)

        async def _regen_this_prompt(e, _i=i, _f=prompt_field):
            # REGEN-MANGA-BLOCK-A1: generic chat Regen writes shot_desc/narration/speech_char/
            # subtitle as top-level keys (see _do_save above) — not structurally capable of
            # targeting shot["_manga_page_spec"]["panels"][j]["desc"], the Manga vNext canonical
            # field. Rather than inventing a translation layer, block execution here too (the
            # button itself is already disabled/hidden below — this is the same "don't trust the
            # UI state alone" runtime guard used elsewhere in this app for Manga vNext).
            if shots_data[_i].get("_manga_page_spec"):
                status_lbl.value = "⚠ Chat Regen is unavailable for Manga vNext pages — edit panel desc in Review Outline instead"
                page.update(); return
            _regen_shot_idx[0] = _i; _regen_field_ref[0] = _f
            # REGEN-CHAT-PERSIST-A1: this setup — new session, cleared chat, fresh briefing —
            # must only happen the FIRST time this row's dialog opens. Reopening it later (Close
            # then click again) must resume the same in-progress conversation, not wipe it; the
            # 🔄 refresh button remains the explicit way to start over.
            if not _regen_opened[0]:
                _regen_opened[0] = True
                _regen_wf_template[0] = None; _regen_initialized[0] = False
                shot = shots_data[_i]
                # ต่อ session เดียวกับตอน Fill เขียน prompt นี้ ถ้ามี — โมเดล local จำบริบทเดิมได้เลย
                # (Grok-filled shot / ช็อตเก่าก่อนมีฟีเจอร์นี้ไม่มีค่านี้ → เริ่ม session ใหม่ตามปกติ)
                _fill_sid = shot.get("_fill_session_id")
                _regen_session_id[0] = _fill_sid or uuid.uuid4().hex[:8]
                _regen_is_continuation[0] = bool(_fill_sid)
                regen_extra_field.value = ""
                regen_chat_list.controls.clear()
                cur = shot.get("prompt", "")
                # FIELD-FALLBACK-A1: same scene_desc-first-then-shot_desc order as _build_regen_sys
                # above — this component is shared by Normal vNext, Manga vNext (via its adapter's
                # synthesized scene_desc) and pre-migration saved projects of either.
                def _sd(s): return s.get("scene_desc") or s.get("shot_desc", "")
                outline_sum = "\n".join(f"Shot {s.get('shot',j+1)}: {_sd(s)} [{s.get('mood','')}]"
                                        for j, s in enumerate(shots_data))
                prev_ctx = ""
                if _i > 0:
                    ps = shots_data[_i-1]
                    prev_ctx = (f"Previous Shot {ps.get('shot',_i)} ({_sd(ps)}) [mood: {ps.get('mood','')}]:\n"
                                f"{ps.get('prompt','')}\n\n")
                next_ctx = ""
                if _i + 1 < len(shots_data):
                    ns = shots_data[_i+1]
                    next_ctx = (f"Next Shot {ns.get('shot',_i+2)} ({_sd(ns)}) [mood: {ns.get('mood','')}]:\n"
                                f"{ns.get('prompt','')}\n\n")
                env_rule = ("⚠ ENVIRONMENT (CRITICAL): extract the exact location, setting, props, lighting, and atmosphere "
                            "from the previous and next shot prompts above. The rewritten prompt MUST use the same environment — "
                            "same room/place/surface/light source/color palette. Do NOT invent a new location.\n\n") if (prev_ctx or next_ctx) else ""
                story_ctx = ""
                if story_field.value.strip():
                    story_ctx += f"Full Story:\n{story_field.value.strip()}\n\n"
                # story_summary (A1's macro events joined) is no longer sent to the Regen chat: this message already has the Full Story
                # and the Full Outline, so it only duplicated them. The field is still stored and shown in the Summary tab.
                extra = regen_extra_field.value.strip() if regen_extra_field.value else ""
                extra_block = f"\n⚠ SPECIAL REQUIREMENT: {extra}" if extra else ""
                try:
                    _fl_txt = (_FILL_LEDGER_GETTER[0]() or "").strip()
                except Exception:
                    _fl_txt = ""
                ledger_block = (
                    "CURRENT FILL DESIGN LEDGER (reusable visual design already established for this story — "
                    "appearance only, it does not authorize presence; keep these designs stable, and change one "
                    "only if current canonical truth contradicts it or the user explicitly asks to redesign "
                    "it):\n" + _fl_txt + "\n\n") if _fl_txt else ""
                _shot_story = (shot.get("story") or "").strip()
                story_line = (f"Current Shot {_i+1} story (narrative core — read first): {_shot_story}\n\n"
                              if _shot_story else "")
                _regen_init_msg[0] = (
                    f"{story_ctx}Full Outline:\n{outline_sum}\n\n{prev_ctx}{next_ctx}"
                    f"{env_rule}{ledger_block}{story_line}"
                    f"Current Shot {_i+1} to edit ({_sd(shot)}) [mood: {shot.get('mood','')}]:\n{cur}\n\n"
                    f"I will tell you what changes I want.{extra_block}"
                )
                _add_chat_bubble(f"Shot {_i+1}: {_sd(shot)}", is_user=False)
                if cur: _add_chat_bubble(f"Current prompt:\n{cur}", is_user=False)
                if extra: _add_chat_bubble(f"⚠ Special instructions: {extra}", is_user=False)
                _add_chat_bubble("Type a request to edit the prompt.", is_user=False)
            regen_dialog.open = True; page.update()

        async def _quick_regen(e, _i=i, _f=prompt_field):
            if llm_busy[0]:
                status_lbl.value = "⚠ LLM is busy"; page.update(); return
            if not llm_dd.value:
                status_lbl.value = "⚠ Select LLM workflow first"; page.update(); return
            if open_fill_dialog_fn is None:
                status_lbl.value = "⚠ Regen dialog unavailable"; page.update(); return
            open_fill_dialog_fn(_i)

        # ── Per-shot action buttons ───────────────────────────────────
        async def _run_this_shot(e, _i=i):
            if _is_running is not None and _is_running[0]:
                status_lbl.value = "⚠ Run All/Test All/Fix All is running — wait for it to finish"; page.update(); return
            from .constants import LLM_DIR
            if ensure_yolo_fn is not None:
                await ensure_yolo_fn()
            _stop_event.clear()
            shot = shots_data[_i]
            prompt = shot.get("prompt", "")
            prompt = runtime_dialogue_prompt(prompt, shot, _cur_dlg())
            if run_steps_field and run_steps_field.value:
                v = run_steps_field.value
                _run_steps[0] = int(v) if v != "default" else -1
            # re-sync จาก dropdown ตรงๆ — Dropdown.on_change บางครั้งไม่ยิง event ให้ selected_api/sel_size ตามทัน
            if api_dd is not None and api_dd.value: selected_api[0] = api_dd.value
            if size_dd is not None and size_dd.value: sel_size[0] = size_dd.value
            if _loop_shot_field_ref and _loop_shot_field_ref[0]:
                try: _loops_per_shot[0] = int(_loop_shot_field_ref[0].value or "1")
                except: pass
            _style_kw = (resolve_style_kw_fn(shot.get("_dir_scale_category") or shot.get("shot_scale", "medium")) if resolve_style_kw_fn else
                         STYLE_MAP.get((sync_style_fn() if sync_style_fn else style_dd.value) or "none", ("", ""))[1])
            print(f"[RUN SHOT {_i+1}] style_kw={_style_kw!r} | has_resolve_fn={resolve_style_kw_fn is not None}")
            print(f"[RUN SHOT] PROMPT:\n{prompt}\n{'='*60}")
            # An empty prompt is a valid render, exactly as in Run All: _gen_shot_image builds the runtime
            # prompt from the shot description + character/location sheets (assemble_runtime_prompt).
            w, h = _SIZE_MAP.get(sel_size[0], (2304, 1296))
            _cur_seed = _locked_seed[0] if _seed_lock[0] else -1
            loops = _loops_per_shot[0]
            _stop_event.clear()
            if _set_running_ref and _set_running_ref[0]: _set_running_ref[0](True)
            progress_ring.visible = True
            for loop_n in range(loops):
                if _stop_event.is_set(): break
                status_lbl.value = f"⏳ Shot {_i+1} ({loop_n+1}/{loops})..."; page.update()
                try:
                    out, used_seed, _trace = await gen_shot_image_fn(_i, prompt, selected_api[0], w, h, _i+1,
                                                   fixed_seed=_cur_seed, override_steps=_run_steps[0],
                                                   stop_event=_stop_event, style_kw=_style_kw)
                except Exception as _ex:
                    import traceback
                    print(f"[RUN SHOT {_i+1}] EXCEPTION:\n{traceback.format_exc()}")
                    status_lbl.value = f"❌ Shot {_i+1} error: {_ex}"
                    progress_ring.visible = False
                    if _set_running_ref and _set_running_ref[0]: _set_running_ref[0](False)
                    page.update(); return
                if out:
                    # re-sync จาก dropdown ตรงๆ — Dropdown.on_change บางครั้งไม่ยิง event ให้ _id_workflow ตามทัน
                    if id_wf_dd_ref is not None and id_wf_dd_ref[0] is not None and id_wf_dd_ref[0].value:
                        _id_workflow[0] = id_wf_dd_ref[0].value
                    if _id_pass[0] and _id_ref_image[0] and _id_workflow[0]:
                        id_out = await run_identity_pass(out, _id_ref_image[0], _id_workflow[0],
                                                         Path(output_dir[0]), index=_i+1, stop_event=_stop_event)
                        if id_out:
                            out = id_out
                            _trace["generation_mode"] = "identity_pass"
                            _trace["identity_reference"] = _id_ref_image[0]
                    _dlg_style = _cur_dlg()
                    if pil_overlay_applies(shots_data[_i]):
                        _narr_t, _sub_t = _pil_overlay_texts(shots_data[_i], _dlg_style)
                        if _narr_t or _sub_t:
                            _dlg_pos = shots_data[_i].get("dialogue_position", "bottom")
                            await asyncio.get_event_loop().run_in_executor(
                                None, apply_dual_text_overlay, out, _narr_t, _sub_t, _dlg_style, _dlg_pos)
                    add_thumb_to_shot(_i, out, seed=used_seed, trace=_trace,
                                       fill_backend=shots_data[_i].get("_fill_backend", "unknown"),
                                       story_id=(current_story_id_fn() if current_story_id_fn else ""))
                    save_state_fn()
            status_lbl.value = f"✅ Shot {_i+1} done"; progress_ring.visible = False
            if _set_running_ref and _set_running_ref[0]: _set_running_ref[0](False)
            page.update()

        async def _test_this_shot(e, _i=i):
            if _is_running is not None and _is_running[0]:
                status_lbl.value = "⚠ Run All/Test All/Fix All is running — wait for it to finish"; page.update(); return
            if ensure_yolo_fn is not None:
                await ensure_yolo_fn()
            _stop_event.clear()  # ถ้า stop_event ค้าง set จากครั้งก่อน (Stop ระหว่าง Run All/Test All)
                                  # run_one จะ bail คืน None ทันทีเงียบๆ โดยไม่มี error ให้เห็นเลย
            # empty prompt is allowed, as in Test All: the runtime prompt falls back to shot description + sheets
            shot = shots_data[_i]
            prompt = shot.get("prompt", "")
            prompt = runtime_dialogue_prompt(prompt, shot, _cur_dlg())
            _style_kw = (resolve_style_kw_fn(shot.get("_dir_scale_category") or shot.get("shot_scale", "medium")) if resolve_style_kw_fn else
                         STYLE_MAP.get((sync_style_fn() if sync_style_fn else style_dd.value) or "none", ("", ""))[1])
            print(f"[TEST SHOT {_i+1}] style_kw={_style_kw!r} | has_resolve_fn={resolve_style_kw_fn is not None}")
            # re-sync จาก dropdown ตรงๆ — Dropdown.on_change บางครั้งไม่ยิง event ให้ mirror state ตามทัน
            if api_dd is not None and api_dd.value: selected_api[0] = api_dd.value
            if size_dd is not None and size_dd.value: sel_size[0] = size_dd.value
            if test_steps_field is not None and test_steps_field.value:
                try: _test_steps[0] = int(test_steps_field.value)
                except ValueError: pass
            w, h = _SIZE_MAP.get(sel_size[0], (2304, 1296))
            _cur_seed = _locked_seed[0] if _seed_lock[0] else -1
            status_lbl.value = f"🧪 Test shot {_i+1} ({_test_steps[0]} steps)..."
            progress_ring.visible = True; page.update()
            try:
                out, used_seed, _trace = await gen_shot_image_fn(_i, prompt, selected_api[0], w, h, _i+1,
                                               fixed_seed=_cur_seed,
                                               override_steps=_test_steps[0], stop_event=_stop_event,
                                               style_kw=_style_kw)
            except Exception:
                import traceback
                _tb = traceback.format_exc()
                print(f"[TEST shot {_i+1}] EXCEPTION:\n{_tb}")
                status_lbl.value = f"❌ Test shot {_i+1} error — see console"
                progress_ring.visible = False; page.update()
                return
            if out:
                _ds = _cur_dlg()
                if pil_overlay_applies(shots_data[_i]):
                    _narr_t, _sub_t = _pil_overlay_texts(shots_data[_i], _ds)
                    print(f"[PIL overlay] shot={_i+1} style={_ds} narration={repr(_narr_t)} subtitle={repr(_sub_t)} shot_keys={list(shots_data[_i].keys())}")
                    if _narr_t or _sub_t:
                        _dp = shots_data[_i].get("dialogue_position", "bottom")
                        ok = await asyncio.get_event_loop().run_in_executor(None, apply_dual_text_overlay, out, _narr_t, _sub_t, _ds, _dp)
                        print(f"[PIL overlay] result={ok}")
                add_thumb_to_shot(_i, out, seed=used_seed, is_test=True, trace=_trace,
                                   fill_backend=shots_data[_i].get("_fill_backend", "unknown"),
                                   story_id=(current_story_id_fn() if current_story_id_fn else ""))
                save_state_fn()
                status_lbl.value = f"✅ Test shot {_i+1} done (seed={used_seed})"
            else:
                status_lbl.value = f"⚠ Test shot {_i+1}: no image returned"
            progress_ring.visible = False; page.update()

        async def _check_this_shot(e, _i=i):
            if llm_busy[0]: status_lbl.value = "⚠ LLM is busy"; page.update(); return
            llm_wf = llm_dd.value
            if not llm_wf: status_lbl.value = "⚠ Select LLM workflow first"; page.update(); return
            if not shots_data[_i].get("prompt", "").strip(): status_lbl.value = f"⚠ Shot {_i+1} has no prompt"; page.update(); return
            for key in ("_check_status", "_check_issues", "_check_reason"):
                shots_data[_i].pop(key, None)
            await do_check_run_fn(single_idx=_i)

        async def _director_this_shot(e, _i=i):
            if llm_busy[0]: status_lbl.value = "⚠ LLM is busy"; page.update(); return
            llm_wf = llm_dd.value
            if not llm_wf: status_lbl.value = "⚠ Select LLM workflow first"; page.update(); return
            if not shots_data[_i].get("prompt", "").strip(): status_lbl.value = f"⚠ Shot {_i+1} has no prompt"; page.update(); return
            for key in ("_dir_status", "_dir_issues", "_dir_reason"):
                shots_data[_i].pop(key, None)
            await do_director_run_fn(single_idx=_i)

        async def _vision_check_shot(e, _i=i):
            if llm_busy[0]: status_lbl.value = "⚠ LLM is busy"; page.update(); return
            await run_vision_fn("editor", single_idx=_i)

        async def _vision_director_shot(e, _i=i):
            if llm_busy[0]: status_lbl.value = "⚠ LLM is busy"; page.update(); return
            await run_vision_fn("director", single_idx=_i)

        def _make_icon_btn(icon, color, tooltip, on_click, bg, bg_hover):
            return ft.IconButton(icon=icon, icon_size=18, icon_color=color, tooltip=tooltip,
                                 on_click=on_click,
                                 style=ft.ButtonStyle(
                                     bgcolor={ft.ControlState.DEFAULT: bg, ft.ControlState.HOVERED: bg_hover},
                                     shape=ft.RoundedRectangleBorder(radius=8), padding=6))

        regen_quick_btn       = _make_icon_btn(ft.Icons.PERSON_OUTLINE, C["purple"], "Image Prompt — regenerate this shot's prompt", _quick_regen, C["purple_lt"], "#EDE9FE")
        regen_chat_btn        = _make_icon_btn(ft.Icons.SUPPORT_AGENT, C["purple"], "Chat to edit this prompt", _regen_this_prompt, C["purple_lt"], "#EDE9FE")
        check_shot_btn        = _make_icon_btn(ft.Icons.PERSON_SEARCH, C["amber"], "Consistency Check", _check_this_shot, "#FFF9EC", "#FEF3C7")
        director_shot_btn     = _make_icon_btn(ft.Icons.RECORD_VOICE_OVER, "#0F766E", "Visual Direction — camera & lighting", _director_this_shot, "#F0FDFA", "#CCFBF1")
        test_shot_btn         = _make_icon_btn(ft.Icons.SCIENCE_ROUNDED, "#7C3AED", "Test Shot — quick preview, fewer steps", _test_this_shot, "#F5F3FF", "#EDE9FE")
        vision_check_shot_btn = _make_icon_btn(ft.Icons.MANAGE_SEARCH_ROUNDED, "#B45309", "Vision Check", _vision_check_shot, "#FFF7ED", "#FED7AA")
        vision_dir_shot_btn   = _make_icon_btn(ft.Icons.MOVIE_FILTER_ROUNDED, "#065F46", "Vision Director", _vision_director_shot, "#F0FDF4", "#BBF7D0")
        # ซ่อนปุ่ม vision ระดับช็อต — ยังสร้างไว้เพื่อให้ layout/สายเรียกเดิมไม่เปลี่ยน
        vision_check_shot_btn.visible = False
        vision_dir_shot_btn.visible   = False
        # ช็อตที่ fill ด้วย Grok ไม่มี local session ให้ปุ่มแชทต่อ (Grok เป็น stateless call เดี่ยว
        # ใน Fill) — ซ่อนปุ่มไปเลยแทนที่จะเปิด session ใหม่ที่ไม่เกี่ยวกับตอน fill จริง
        if shot.get("_fill_backend") == "grok":
            regen_chat_btn.visible = False
        if shot.get("_static_pose"):
            # Static Pose Fill shot: the prompt is complete. Image Prompt (Fill) would rewrite it; the Checker / Director read the sheets and Fill's blocks this shot does not have.
            regen_quick_btn.visible = False
            check_shot_btn.visible = False
            director_shot_btn.visible = False
        # REGEN-MANGA-BLOCK-A1 (Part C): generic chat Regen is not structurally capable of safely
        # editing Manga vNext's canonical shot["_manga_page_spec"]["panels"][j]["desc"] — it only
        # knows how to write flat top-level shot_desc/narration/speech_char/subtitle, which would
        # become a second, silently-ignored semantic authority for a Manga page (Fill Manga always
        # reads _manga_page_spec, never those flat keys). Disabled (not hidden) so its absence for
        # Manga rows reads as "unavailable here", not "the feature vanished" — same pattern already
        # used for check_prompts_btn's Manga vNext disabled state.
        if shot.get("_manga_page_spec"):
            regen_chat_btn.disabled = True
            regen_chat_btn.tooltip = ("Unavailable for Manga vNext pages — edit panel description "
                                       "directly in Review Outline instead")
        run_shot_btn          = ft.IconButton(icon=ft.Icons.PLAY_ARROW_ROUNDED, icon_size=20, icon_color=C["accent"],
                                              tooltip="Generate Shot", on_click=_run_this_shot,
                                              style=ft.ButtonStyle(
                                                  bgcolor={ft.ControlState.DEFAULT: C["accent_lt"], ft.ControlState.HOVERED: "#DBEAFE"},
                                                  shape=ft.RoundedRectangleBorder(radius=8), padding=6))

        card = ft.Container(
            content=ft.Column([
                scene_lbl, dialogue_lbl, check_badge, director_badge, vision_badge,
                prompt_field,
                ft.Row([regen_quick_btn, regen_chat_btn, check_shot_btn,
                        director_shot_btn, test_shot_btn, vision_check_shot_btn, vision_dir_shot_btn, run_shot_btn],
                       spacing=4),
                ft.Container(content=row, border_radius=br(6)),
            ], spacing=6),
            bgcolor=C["surface"], border_radius=br(8),
            border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
            padding=po(left=14, right=14, top=12, bottom=12),
            margin=ft.Margin(left=0, right=0, top=0, bottom=0),
        )
        shots_col.controls.append(card)

    def rebuild_shots_col(skip_gallery_filter: bool = False, do_update: bool = True):
        if not skip_gallery_filter:
            for i in list(gallery_images.keys()):
                gallery_images[i] = [img for img in gallery_images[i] if Path(img["path"]).exists()]
        shots_col.controls.clear()
        _shot_rows.clear()
        _shot_prompt_fields.clear()
        if not shots_data:
            shots_col.controls.append(ft.Container(
                content=ft.Column([
                    ft.Icon(ft.Icons.AUTO_STORIES_ROUNDED, size=48, color=C["text3"]),
                    ft.Text("No story yet — enter a story and click Generate Shots",
                            size=13, color=C["text3"]),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
                alignment=ft.Alignment(0, 0), expand=True,
            ))
        else:
            for i, shot in enumerate(shots_data):
                _build_shot_row(i, shot)
        if do_update:
            page.update()

    return (shots_col, _shot_rows, _shot_prompt_fields, _build_shot_row, add_thumb_to_shot,
            rebuild_shots_col, _inject_dialogue, _thumb_registry)
