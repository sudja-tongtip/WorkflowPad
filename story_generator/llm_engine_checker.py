"""llm_engine_checker.py — Consistency Check for Normal prompts (legacy name: Checker).

Consistency Check detects material contradictions against canonical truth.

Design rule:
    LLM = semantic consistency judgment.
    Python = transport + JSON + structural safety only.

The checker never directs the image and never rewrites narration/subtitle.  It compares
Shot List/current production truth with the renderer-facing prompt and makes only the smallest
material repair when the prompt has drifted.
"""

from __future__ import annotations

__version__ = "20260925_checker_clean_a1"

import asyncio
import json
import re
from pathlib import Path

from . import llm_engine_tools as _tools


_CHECKER_SYSTEM_PROMPT = """You are the final consistency checker for a Krea2 image prompt.

CANONICAL SHOT data is the truth. CURRENT PROMPT is the image prompt to judge.
If the prompt is faithful, keep it unchanged. If a material visible fact is wrong, missing, or invented, make the smallest repair needed.
Judge meaning, not wording. Do not improve style, camera, composition, lighting, emotion, or prose merely because you prefer another version. CAMERA and LIGHT are repaired only on a material contradiction with canonical truth (e.g. canonical nighttime candlelight vs. a prompt LIGHT of midday sunlight) — never because another angle or lighting treatment would look better. FRAME LAYOUT is repaired only on a material contradiction with canonical spatial truth (e.g. canonical shot places one person beside another but FRAME LAYOUT separates them across the room, or a boundary/proximity relation is reversed) — never because another screen arrangement would look better, and never invent an image-left/image-right placement that canonical truth does not establish.
Do not write or rewrite narration or subtitle; they are user-owned metadata.
Preserve every correct fact and the existing prompt structure. Repair FRAME LAYOUT, CAMERA, LIGHT, IDENTITY LOCK, LOCATION LOCK, or OBJECT LOCK only when canonical evidence proves that block wrong.
Never change canonical data.

Return JSON only:
{"shot":<number>,"keep_original":<true|false>,"issues":["<short issue>"],"reason":"<short reason>","prompt":"KEEP or the complete repaired prompt"}
"""

_HEADER_RE = re.compile(
    r"(?m)^(MAIN IMAGE DESCRIPTION|FRAME LAYOUT|CAMERA|LIGHT|IDENTITY LOCK|LOCATION LOCK|OBJECT LOCK):\s*$"
)
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|```\s*$", re.I)


def _parse_json_object(raw: str | None) -> dict | None:
    if not raw:
        return None
    text = _CODE_FENCE_RE.sub("", str(raw).strip()).strip()
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        obj = json.loads(text[start:end])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _text(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(str(x) for x in value if x is not None).strip()
    return str(value).strip() if value is not None else ""


def _issues(value) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _header_sequence(prompt: str) -> list[str]:
    return [m.group(1) for m in _HEADER_RE.finditer(prompt or "")]


def _structure_ok(before: str, after: str) -> tuple[bool, str]:
    """Transport guard only: the Consistency Check may repair lock content, but may not change the block skeleton."""
    before_headers = _header_sequence(before)
    after_headers = _header_sequence(after)
    if before_headers != after_headers:
        return False, f"block structure changed: {before_headers} -> {after_headers}"
    return True, ""


def _canonical_payload(
    shot: dict,
    *,
    character_desc: str,
    scene_desc_global: str,
    requirement: str,
    props_desc: str,
    a1: dict | None,
) -> dict:
    parent_ids, parent_text = _tools._resolve_shot_parent_blocks(shot, a1)
    live_props = str(props_desc or (a1 or {}).get("important_objects", "") or "").strip()
    payload = {
        "shot": shot.get("shot"),
        "canonical_shot": {
            "visible_people": shot.get("visible_people", ""),
            "shot_desc": shot.get("shot_desc", ""),
            "story": shot.get("story", ""),
            "objects": shot.get("objects", ""),
        },
        "character_sheet": character_desc or "",
        "props_reference": live_props,
        "production_environment": scene_desc_global or "",
        "parent_story_ids": parent_ids,
        "parent_story": parent_text,
        "project_requirement": requirement or "",
        "current_prompt": shot.get("prompt", ""),
    }
    return payload


async def _load_workflow(llm_wf_file: str, on_err) -> dict | None:
    if llm_wf_file == _tools.GROK_MARKER:
        return None
    path = _tools.LLM_DIR / f"{llm_wf_file}.json"
    if not path.exists():
        await on_err(f"ไม่พบ {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        await on_err(str(exc))
        return None


async def run_check_prompts(
    shots_data: list[dict],
    llm_wf_file: str,
    character_desc: str,
    scene_desc_global: str,
    requirement: str,
    story_text: str,
    story_summary: str,
    on_shot,
    on_err,
    on_log=None,
    on_progress=None,
    stop_event: asyncio.Event | None = None,
    single_shot_idx: int | list[int] | set[int] | tuple[int, ...] | None = None,
    extra_req: str = "",
    image_style: str = "",
    genre: str = "",
    genre2: str = "",
    dialogue_style: str = "",
    dialogue_lang: str = "th",
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    sys_variant: str = "",
    prompt_style: str = "narrative",
    manga_page: bool = False,
    outline_mode: str = "cinematic",
    props_desc: str = "",
    desc_lang: str = "",
    source_lang: str = "",
    fill_engine: str = "",
    narration_type: str = "caption",
    clothing_arc: str = "",
    a1: dict | None = None,
):
    """Check Normal Krea2 prompts against B/current canonical truth.

    The broad signature is intentionally kept compatible with the old tools engine so main_ui and
    saved workflows do not need schema changes. Parameters outside the clean Consistency Check's ownership are
    accepted but deliberately not injected into the system prompt.
    """
    del story_text, story_summary, extra_req, image_style, genre, genre2
    del dialogue_style, dialogue_lang, sys_variant, prompt_style, outline_mode
    del desc_lang, source_lang, fill_engine, narration_type, clothing_arc

    if single_shot_idx is None:
        target_idxs = set(range(len(shots_data)))
    elif isinstance(single_shot_idx, int):
        target_idxs = {single_shot_idx}
    else:
        target_idxs = {int(i) for i in single_shot_idx}

    wf_template = await _load_workflow(llm_wf_file, on_err)
    if llm_wf_file != _tools.GROK_MARKER and wf_template is None:
        return

    # Normal Checker only. Manga vNext has its own page/panel truth and main_ui already guards it.
    valid_targets = [
        i for i in sorted(target_idxs)
        if 0 <= i < len(shots_data)
        and not manga_page
        and str(shots_data[i].get("prompt_style") or "").strip() != "manga_page"
    ]

    if on_log:
        await on_log(
            f"CONSISTENCY CHECK sys={len(_CHECKER_SYSTEM_PROMPT)} chars targets={len(valid_targets)}"
        )

    grok_prefetch: dict[int, dict] = {}
    if llm_wf_file == _tools.GROK_MARKER and valid_targets:
        targets = []
        for idx in valid_targets:
            shot = shots_data[idx]
            shot_num = int(shot.get("shot") or (idx + 1))
            payload = _canonical_payload(
                shot,
                character_desc=character_desc,
                scene_desc_global=scene_desc_global,
                requirement=requirement,
                props_desc=props_desc,
                a1=a1,
            )
            payload["shot"] = shot_num
            targets.append((shot_num, _CHECKER_SYSTEM_PROMPT, payload))
        grok_prefetch = await _tools._prefetch_grok_batch(
            "CheckerClean",
            targets,
            _tools._grok_batch_budget(_tools._llm_tokens_check_grok_batch, len(targets)),
            grok_api_key,
            grok_model,
            on_log=on_log,
            stop_event=stop_event,
            one_call=True,
            strict_no_recovery=False,
        )

    loop = asyncio.get_event_loop()
    total = len(shots_data)

    for idx, shot in enumerate(shots_data):
        if stop_event and stop_event.is_set():
            if on_log:
                await on_log("⏹ หยุด Consistency Check")
            return
        if idx not in target_idxs:
            continue
        if on_progress:
            on_progress(idx + 1, total)

        updated = dict(shot)
        old_prompt = _text(shot.get("prompt", ""))

        if manga_page or str(shot.get("prompt_style") or "").strip() == "manga_page":
            updated["_check_status"] = "ok"
            updated["_check_issues"] = []
            updated["_check_reason"] = "Manga skipped"
            await on_shot(idx, updated)
            continue

        if not old_prompt:
            updated["_check_status"] = "error"
            updated["_check_issues"] = ["missing prompt"]
            updated["_check_reason"] = "No prompt to check"
            await on_shot(idx, updated)
            continue

        shot_num = int(shot.get("shot") or (idx + 1))
        payload = _canonical_payload(
            shot,
            character_desc=character_desc,
            scene_desc_global=scene_desc_global,
            requirement=requirement,
            props_desc=props_desc,
            a1=a1,
        )
        payload["shot"] = shot_num

        result = None
        if llm_wf_file == _tools.GROK_MARKER:
            result = grok_prefetch.get(shot_num)
        else:
            user_prompt = (
                "Review this shot. The JSON below is evidence, not a format to copy into the image prompt.\n\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
            )
            for attempt in range(1, 3):
                if on_log:
                    await on_log(f"🔍 Consistency Check shot {shot_num}/{total}" + (f" retry {attempt}" if attempt > 1 else ""))
                raw = await _tools._call_llm_generic(
                    llm_wf_file,
                    wf_template,
                    _CHECKER_SYSTEM_PROMPT,
                    user_prompt,
                    loop,
                    max_tokens=max(2500, int(_tools._llm_tokens_fill)),
                    on_log=on_log if attempt == 1 else None,
                    grok_api_key=grok_api_key,
                    grok_model=grok_model,
                )
                result = _parse_json_object(raw)
                if result:
                    break

        if not isinstance(result, dict):
            updated["_check_status"] = "error"
            updated["_check_issues"] = []
            updated["_check_reason"] = "ตรวจสอบไม่สำเร็จ"
            await on_shot(idx, updated)
            continue

        keep = bool(result.get("keep_original", False))
        issues = _issues(result.get("issues"))
        reason = _text(result.get("reason")) or ("OK" if keep else "Repaired")
        candidate = _text(result.get("prompt"))

        if keep:
            use_prompt = old_prompt
            status = "ok"
        else:
            if not candidate or candidate.upper() == "KEEP":
                use_prompt = old_prompt
                status = "error"
                issues.append("repair missing prompt")
                reason = "Invalid repair; kept original"
            else:
                ok, why = _structure_ok(old_prompt, candidate)
                if not ok:
                    use_prompt = old_prompt
                    status = "error"
                    issues.append("checker structure reject")
                    reason = why
                    if on_log:
                        await on_log(f"🚫 CONSISTENCY CHECK STRUCTURE REJECT shot={shot_num}: {why}")
                else:
                    use_prompt = candidate
                    status = "edited"

        updated["prompt"] = use_prompt
        updated["_check_status"] = status
        updated["_check_issues"] = issues
        updated["_check_reason"] = reason
        await on_shot(idx, updated)


# Canonical name — Consistency Check (legacy: Checker). The legacy entry point above stays for compatibility; run_consistency_check is the same function.
run_consistency_check = run_check_prompts
