"""llm_engine_director.py — Visual Direction for Normal Krea2 prompts (legacy name: Director).

Visual Direction changes authorized HOW domains without changing WHAT.

Design rule:
    CURRENT PROMPT locks WHAT.
    User controls explicitly authorize changes to selected HOW domains.
    LLM chooses one coherent visual solution; Python only transports and protects structure/locks.
"""

from __future__ import annotations

__version__ = "20260925_director_clean_a2"

import asyncio
import json
import re

from . import llm_engine_tools as _tools
from .visual_direction import SHOT_SCALE_RULES, build_visual_direction_brief


_DIRECTOR_SYSTEM_PROMPT = """You are the visual director for a Krea2 still image.

The CURRENT PROMPT locks story truth, not visual direction. Preserve the people, identity, clothing/state, story objects and ownership, location, relationships, action identity, dialogue, chronology, and outcome.
The USER DIRECTING BRIEF is a hard requirement on the RESULT. It explicitly authorizes you to change or introduce visual-direction details inside the selected domains. Stronger settings grant more creative freedom there; Preserve/Follow settings grant none.
Read all controls together and find one coherent visual solution for this specific moment. Do not translate controls into canned tricks. Choose the method yourself.
You may invent HOW; never invent WHAT.
CURRENT PROMPT is supplied as separate blocks. Return the directed content of EVERY supplied block as plain text without its header — never omit a block. You may rewrite visual-direction wording inside a block only where the selected controls authorize it; the canonical facts in every block (identity, clothing/state, location, objects, ownership, story facts) stay intact.
CAMERA (POV, angle, framing, scale only) is rewritten only by camera-related controls (shot scale, camera energy). FRAME LAYOUT (screen-space placement of people and objects within the image frame only — image-left/image-right/center, foreground/midground/background, relative spacing; never world-space location or camera POV) is rewritten only by the composition control. LIGHT (source, direction, visible behavior only) is rewritten only by the lighting control. A Preserve/Follow setting on a domain leaves that block's meaning alone. Whenever CAMERA or FRAME LAYOUT changes, keep the two mutually coherent — a camera choice changes how world-space relationships appear on screen. Never move camera wording, layout wording, or lighting wording into MAIN IMAGE DESCRIPTION — each stays in its own block.

Return JSON only, with one field per supplied block:
{"shot":<number>,"keep_original":<true|false>,"issues":["<short visual issue>"],"reason":"<short reason>","main_image_description":"...","frame_layout":"...","camera":"...","light":"...","identity_lock":"...","location_lock":"...","object_lock":"...","dialogue_position":"top|bottom"}
"""

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|```\s*$", re.I)
_HEADER_RE = re.compile(
    r"(?m)^(MAIN IMAGE DESCRIPTION|FRAME LAYOUT|CAMERA|LIGHT|IDENTITY LOCK|LOCATION LOCK|OBJECT LOCK):\s*$"
)


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


_BLOCK_KEYS = {
    "MAIN IMAGE DESCRIPTION": "main_image_description",
    "FRAME LAYOUT": "frame_layout",
    "CAMERA": "camera",
    "LIGHT": "light",
    "IDENTITY LOCK": "identity_lock",
    "LOCATION LOCK": "location_lock",
    "OBJECT LOCK": "object_lock",
}


def _split_blocks(prompt: str) -> list[tuple[str, str, str]]:
    """[(field, header, body)] in the prompt's OWN order and presence. A prompt with no MAIN header
    (its leading text) is one headerless main block. Structure only — bodies are never read."""
    text = prompt or ""
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return [("main_image_description", "", text.strip())] if text.strip() else []
    out: list[tuple[str, str, str]] = []
    lead = text[:matches[0].start()].strip()
    if lead and matches[0].group(1) != "MAIN IMAGE DESCRIPTION":
        out.append(("main_image_description", "", lead))
    for pos, m in enumerate(matches):
        end = matches[pos + 1].start() if pos + 1 < len(matches) else len(text)
        out.append((_BLOCK_KEYS[m.group(1)], m.group(1), text[m.end():end].strip()))
    return out


def _rebuild_prompt(blocks: list[tuple[str, str, str]], result: dict) -> tuple[str | None, str]:
    """Deterministic rebuild: the ORIGINAL headers in the ORIGINAL order, each followed by the body
    the model returned for that block. Rejects (None, reason) only when a supplied block came back
    missing/empty. A header the model wrote at the top of its own value is dropped so it is never
    doubled. No exact-text comparison of any lock — content is the model's to direct."""
    parts: list[str] = []
    for field, header, _body in blocks:
        text = _text(result.get(field))
        if header:
            text = re.sub(rf"(?is)^\s*{re.escape(header)}\s*:\s*", "", text, count=1).strip()
        if not text:
            return None, f"missing block body: {field}"
        parts.append(f"{header}:\n{text}" if header else text)
    return "\n\n".join(parts), ""


def _blocks_contract(blocks: list[tuple[str, str, str]]) -> str:
    fields = ", ".join(field for field, _h, _b in blocks)
    return (f"Return one field for each supplied block of CURRENT PROMPT — {fields} — plus the usual "
            "shot/keep_original/issues/reason/dialogue_position. Never omit a supplied block; write "
            "block content only, without headers.")


def _normalize_control(value: str, aliases: dict[str, str], default: str) -> str:
    key = (value or default).strip().lower()
    return aliases.get(key, key)


def _directing_brief(
    *,
    shot_scale_override: str,
    assigned_distance: str,
    composition: str,
    camera_energy: str,
    lighting: str,
    environment: str,
    motion: str,
    extra_req: str,
) -> str:
    scale_key = (shot_scale_override or "").strip().lower()
    if not scale_key:
        scale_line = "FOLLOW OUTLINE — preserve the camera distance already expressed by CURRENT PROMPT."
    else:
        scale_line = (
            f"{SHOT_SCALE_RULES.get(scale_key, scale_key.upper())} Hard sequence-scale request: "
            f"for this shot the assigned camera-distance result is {assigned_distance or 'as directed'}. "
            "You may rewrite the camera distance to achieve it."
        )
    # the shared brief supplies every control's meaning; the Director adds only its own scale context
    # and has no Expression control (that one is Fill-time performance).
    brief = build_visual_direction_brief(
        {"composition": composition, "camera_energy": camera_energy, "lighting": lighting,
         "environment": environment, "motion": motion},
        header="USER DIRECTING BRIEF",
        include=("shot_scale", "composition", "camera_energy", "lighting", "environment", "motion"),
        scale_line=scale_line,
    )
    lines = [
        brief,
        "Solve these together as one image. A selected control may override the CURRENT PROMPT's HOW in that domain, but never its WHAT.",
    ]
    if extra_req.strip():
        lines.append("Additional user direction: " + extra_req.strip())
    return "\n".join(lines)


# CAMERA/LIGHT are ordinary English words on their own ("she turns to face the camera", "his light
# footsteps") — unlike the other, distinctive multi-word headers, stripping them needs the colon that
# actually marks them as a header line; the multi-word headers keep the trailing colon optional.
_HEADER_WORDS_RE = re.compile(r"(?i:\b(?:MAIN IMAGE DESCRIPTION|FRAME LAYOUT|IDENTITY LOCK|LOCATION LOCK|OBJECT LOCK)\b\s*:?|\b(?:CAMERA|LIGHT)\b\s*:)")


def _without_headers(text: str) -> str:
    """Sequence summaries must not show the output-header vocabulary: a 'Previous shot: MAIN IMAGE
    DESCRIPTION: …' line looked like an example of a one-block prompt and the model copied it."""
    text = _HEADER_RE.sub("", text or "")
    return re.sub(r"[ \t]{2,}", " ", _HEADER_WORDS_RE.sub("", text)).strip()


def _sequence_context(shots_data: list[dict], idx: int) -> str:
    bits: list[str] = []
    if idx > 0:
        prev = shots_data[idx - 1]
        summary = _without_headers(_tools._framing_digest(_without_headers(str(prev.get("prompt") or "")), words=34))
        if summary:
            bits.append("PREVIOUS SHOT SUMMARY: " + summary)
    if idx + 1 < len(shots_data):
        nxt = shots_data[idx + 1]
        nxt_story = _text(nxt.get("story")) or _text(nxt.get("shot_desc"))
        if nxt_story:
            bits.append("NEXT SHOT SUMMARY: " + _without_headers(nxt_story[:700]))
    return "\n".join(bits)


def _payload(
    shots_data: list[dict],
    idx: int,
    *,
    shot_scale_override: str,
    composition: str,
    camera_energy: str,
    lighting: str,
    environment: str,
    motion: str,
    extra_req: str,
    image_style: str,
    image_size: str,
) -> dict:
    shot = shots_data[idx]
    _blk = _split_blocks(str(shot.get("prompt") or ""))
    assigned = _text(shot.get("_dir_scale_category"))
    brief = _directing_brief(
        shot_scale_override=shot_scale_override,
        assigned_distance=assigned,
        composition=composition,
        camera_energy=camera_energy,
        lighting=lighting,
        environment=environment,
        motion=motion,
        extra_req=extra_req,
    )
    return {
        "shot": int(shot.get("shot") or (idx + 1)),
        "user_directing_brief": brief,
        "image_style": image_style or "",
        "canvas": image_size or "",
        "sequence_context": _sequence_context(shots_data, idx),
        "narration": shot.get("narration", ""),
        "speech_char": shot.get("speech_char", ""),
        "subtitle": shot.get("subtitle", ""),
        "current_prompt": {field: body for field, _h, body in _blk},
        "output_contract": _blocks_contract(_blk),
    }


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


async def run_director_prompts(
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
    image_size: str = "",
    genre: str = "",
    genre2: str = "",
    dialogue_style: str = "",
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    sys_variant: str = "",
    prompt_style: str = "narrative",
    manga_page: bool = False,
    shot_scale_override: str = "",
    shot_scale_preset: str = "balanced",
    props_desc: str = "",
    lighting_pass: str = "enhance",
    composition_pass: str = "balanced",
    camera_energy: str = "natural",
    environment_pass: str = "natural",
    motion_pass: str = "natural",
    beauty_pass: str = "",
    fill_engine: str = "",
    a1: dict | None = None,
):
    """Direct Normal still-image prompts while keeping story truth and lock blocks intact.

    The broad signature remains compatible with the previous tools engine.  Preset is intentionally
    absent: main_ui uses it only as a macro that sets the six live controls before this function runs.
    """
    del character_desc, scene_desc_global, requirement, story_text, story_summary
    del genre, genre2, dialogue_style, sys_variant, prompt_style, shot_scale_preset
    del props_desc, fill_engine, a1

    if single_shot_idx is None:
        target_idxs = set(range(len(shots_data)))
    elif isinstance(single_shot_idx, int):
        target_idxs = {single_shot_idx}
    else:
        target_idxs = {int(i) for i in single_shot_idx}

    if manga_page:
        if on_log:
            await on_log("⏭ Visual Direction skipped — Manga mode")
        return

    if any(str(shots_data[i].get("prompt_style") or "").strip() == "manga_page"
           for i in target_idxs if 0 <= i < len(shots_data)):
        if on_log:
            await on_log("⏭ Visual Direction skipped — Manga vNext owns panel direction")
        return

    composition = _normalize_control(composition_pass or beauty_pass, {"light": "balanced", "push": "cinematic"}, "balanced")
    environment = _normalize_control(environment_pass, {"on": "natural", "strong": "emphasized"}, "natural")
    motion = _normalize_control(motion_pass, {"scene": "natural", "sharpen": "dynamic", "freeze": "peak"}, "natural")
    camera = _normalize_control(camera_energy, {}, "natural")
    lighting = _normalize_control(lighting_pass, {}, "enhance")
    scale_key = (shot_scale_override or "").strip().lower()

    # Only an explicit Director Shot Scale selection rewrites distance. Follow Outline means exactly that:
    # keep whatever distance the current Fill prompt already expresses.
    if scale_key:
        for idx, shot in enumerate(shots_data):
            shot["_dir_scale_category"] = _tools._assign_shot_scale(idx, len(shots_data), scale_key)

    if on_log:
        await on_log(
            "🎬 VISUAL DIRECTION\n"
            f"   Scale       : {scale_key or 'Follow Outline'}\n"
            f"   Composition : {composition}\n"
            f"   Camera      : {camera}\n"
            f"   Lighting    : {lighting}\n"
            f"   Environment : {environment}\n"
            f"   Motion      : {motion}\n"
            f"   System      : {len(_DIRECTOR_SYSTEM_PROMPT)} chars"
        )

    wf_template = await _load_workflow(llm_wf_file, on_err)
    if llm_wf_file != _tools.GROK_MARKER and wf_template is None:
        return

    valid_targets = [i for i in sorted(target_idxs) if 0 <= i < len(shots_data)]

    grok_prefetch: dict[int, dict] = {}
    if llm_wf_file == _tools.GROK_MARKER and valid_targets:
        targets = []
        for idx in valid_targets:
            payload = _payload(
                shots_data,
                idx,
                shot_scale_override=scale_key,
                composition=composition,
                camera_energy=camera,
                lighting=lighting,
                environment=environment,
                motion=motion,
                extra_req=extra_req,
                image_style=image_style,
                image_size=image_size,
            )
            targets.append((payload["shot"], _DIRECTOR_SYSTEM_PROMPT, payload))
        grok_prefetch = await _tools._prefetch_grok_batch(
            "DirectorClean",
            targets,
            _tools._grok_batch_budget(_tools._llm_tokens_director_grok_batch, len(targets)),
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
                await on_log("⏹ หยุด Visual Direction")
            return
        if idx not in target_idxs:
            continue
        if on_progress:
            on_progress(idx + 1, total)

        updated = dict(shot)
        old_prompt = _text(shot.get("prompt"))
        shot_num = int(shot.get("shot") or (idx + 1))

        if not old_prompt:
            updated["_dir_status"] = "error"
            updated["_dir_issues"] = ["missing prompt"]
            updated["_dir_reason"] = "No prompt to direct"
            await on_shot(idx, updated)
            continue

        payload = _payload(
            shots_data,
            idx,
            shot_scale_override=scale_key,
            composition=composition,
            camera_energy=camera,
            lighting=lighting,
            environment=environment,
            motion=motion,
            extra_req=extra_req,
            image_style=image_style,
            image_size=image_size,
        )

        result = None
        if llm_wf_file == _tools.GROK_MARKER:
            result = grok_prefetch.get(shot_num)
        else:
            user_prompt = (
                "Direct this shot. Treat CURRENT PROMPT as the content authority and the brief as explicit HOW authority.\n\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
            )
            for attempt in range(1, 3):
                if on_log:
                    await on_log(
                        f"🎥 Visual Direction shot {shot_num}/{total}"
                        + (f" retry {attempt}" if attempt > 1 else "")
                    )
                raw = await _tools._call_llm_generic(
                    llm_wf_file,
                    wf_template,
                    _DIRECTOR_SYSTEM_PROMPT,
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
            updated["_dir_status"] = "error"
            updated["_dir_issues"] = []
            updated["_dir_reason"] = "ตรวจสอบไม่สำเร็จ"
            await on_shot(idx, updated)
            continue

        keep = bool(result.get("keep_original", False))
        issues = _issues(result.get("issues"))
        reason = _text(result.get("reason")) or ("OK" if keep else "Directed")

        if keep:
            use_prompt = old_prompt
            status = "ok"
        else:
            # Python owns the headers and their order: the model only returns each block's content.
            orig_blocks = _split_blocks(old_prompt)
            rebuilt, why = _rebuild_prompt(orig_blocks, result)
            if len({f for f, _h, _b in orig_blocks}) != len(orig_blocks):
                rebuilt, why = None, "unsupported prompt structure (repeated block)"
            if rebuilt is None:
                use_prompt = old_prompt
                status = "error"
                issues.append("director block missing")
                reason = why
                if on_log:
                    await on_log(f"🚫 VISUAL DIRECTION BLOCK MISSING shot={shot_num}: {why}")
            else:
                use_prompt = rebuilt
                status = "edited"

        updated["prompt"] = use_prompt
        updated["_dir_status"] = status
        updated["_dir_issues"] = issues
        updated["_dir_reason"] = reason

        pos = _text(result.get("dialogue_position")).lower()
        if pos in ("top", "bottom"):
            updated["dialogue_position"] = pos

        await on_shot(idx, updated)


# Canonical name — Visual Direction (legacy: Director). The legacy entry point above stays for compatibility; run_visual_direction is the same function.
run_visual_direction = run_director_prompts
