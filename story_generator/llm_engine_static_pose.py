"""
llm_engine_static_pose.py - STATIC POSE FILL: a still picture prompt becomes a set of shots that differ in pose, facial expression, emotion and camera.

No Story Bible, no sheets, no Beat Sheet, no B and no Fill: the user's own prompt is the source and the writer.

    1. BASE   one call: the prompt without its STYLE words (art style, medium, quality tags). The app adds the style itself at render time.
    2. PLAN   one call: N shots, each with pose / expression / emotion / camera. No story; the shots only have to differ.
    3. WRITE  one call per shot: the base prompt rewritten for that shot. Only the four things change; everything else stays as written.

Python only checks STRUCTURE (the tag is there, the text is not empty, the count is right, nothing is a copy of another shot, no tag left inside the prompt).
It never reads the meaning of the prompt or of the answer. Every call is a fresh session: nothing grows, so a long run cannot overfill the model's context.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid

from .constants import LLM_DIR
from .llm_engine_a1 import GROK_MARKER, _call_grok_text, _call_local_text
from . import fill_conversation as _fc

__version__ = "20261019_static_pose_1"

_MAX_ATTEMPTS = 3
_FIELDS = ("pose", "expression", "emotion", "camera")
_SHOT_MIN_RATIO = 0.40       # a rewritten prompt shorter than this share of the base prompt lost too much
_SHOT_MAX_RATIO = 2.50       # ... and longer than this share grew into something else

_SYSTEM = "You edit image-generation prompts for a picture-making tool. Follow the task exactly and reply only in the requested format, with no explanation."


def _tag(raw: str | None, name: str) -> str | None:
    m = re.search(rf"<{name}>(.*?)</{name}>", raw or "", re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else None


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _build_base_user(prompt: str) -> str:
    return (
        "Below is an image prompt. Remove its STYLE words and change nothing else.\n"
        "STYLE words name the art style, the medium or the rendering look, and quality tags: for example \"oil painting\", \"digital art\", \"anime style\", "
        "\"cinematic\", \"photorealistic\", \"masterpiece\", \"highly detailed\", \"8k\", \"film grain\".\n"
        "Keep everything that says what is IN the picture: the person, face, hair, clothing, body, pose, objects, place, light, colours, mood and camera framing.\n"
        "Keep the original wording, order and language of everything you keep. Do not add, explain or rewrite anything.\n"
        "Reply in exactly this format and nothing else:\n"
        "<base_prompt>the prompt without the style words</base_prompt>\n"
        "<source_style>the style words you removed, as one short comma-separated style description (empty if the prompt had no style words)</source_style>\n\n"
        f"PROMPT:\n{prompt}"
    )


def _build_plan_user(base: str, n: int, previous_problems: list[str] | None = None) -> str:
    blocks = "\n".join(f"<shot_{i}>\npose: ...\nexpression: ...\nemotion: ...\ncamera: ...\n</shot_{i}>" for i in (1, 2, 3))
    more = "" if n <= 3 else f"\n... and so on up to <shot_{n}>"
    fix = ("\nYour previous reply could not be accepted: " + "; ".join(previous_problems[:4]) + ". Write the complete plan again.\n") if previous_problems else ""
    return (
        f"Plan {n} different shots of the SAME person in the SAME place for an image generator. The picture description below is the starting picture.\n"
        "For every shot choose a new pose, a new facial expression, a new emotion and a new camera angle or distance. There is no story: the shots only need to be varied and good to look at.\n"
        "Rules: every shot differs from the others and from the starting picture in its pose AND its expression; a pose must be physically possible for this person in this place; "
        "do not change the person, clothing, hair, objects, place or light; write each field as plain visual words (body positions, face, camera), not pronouns alone.\n"
        f"Reply in exactly this format for shot 1 to shot {n} and nothing else:\n{blocks}{more}\n{fix}\n"
        f"PICTURE DESCRIPTION:\n{base}"
    )


def _parse_plan(raw: str | None, n: int) -> tuple[list[dict] | None, list[str]]:
    problems: list[str] = []
    shots: list[dict] = []
    for i in range(1, n + 1):
        block = _tag(raw, f"shot_{i}")
        if block is None:
            problems.append(f"<shot_{i}> is missing or not closed")
            continue
        item = {}
        for line in block.splitlines():
            m = re.match(r"^\s*(pose|expression|emotion|camera)\s*:\s*(.*)$", line, re.IGNORECASE)
            if m and m.group(1).lower() not in item:
                item[m.group(1).lower()] = m.group(2).strip()
        missing = [k for k in _FIELDS if not (item.get(k) or "").strip()]
        if missing:
            problems.append(f"<shot_{i}> has no " + ", ".join(missing))
        shots.append(item)
    if problems:
        return None, problems
    seen_pose: dict[str, int] = {}
    for i, it in enumerate(shots, 1):
        key = _norm(it["pose"])
        if key in seen_pose:
            problems.append(f"shot {i} repeats the pose of shot {seen_pose[key]}")
        else:
            seen_pose[key] = i
    return (None, problems) if problems else (shots, [])


def _build_shot_user(base: str, plan: dict, previous_problems: list[str] | None = None) -> str:
    fix = ("\nYour previous reply could not be accepted: " + "; ".join(previous_problems[:3]) + ". Write the complete description again.\n") if previous_problems else ""
    return (
        "Rewrite the picture description below for a new shot.\n"
        "Change ONLY these four things, in the places where the description already speaks about the pose, the face, the mood and the camera:\n"
        f"pose: {plan['pose']}\nexpression: {plan['expression']}\nemotion: {plan['emotion']}\ncamera: {plan['camera']}\n"
        "Everything else stays as written: the person, face shape, hair, clothing, body, objects, place, light and colours. Remove every sentence or phrase that still describes the old pose, "
        "the old expression or the old camera. Do not add style words (no art style, medium or quality tags). Keep the language and the wording of the description as much as you can.\n"
        f"Reply with the complete new description inside <prompt></prompt> and nothing else.\n{fix}\n"
        f"PICTURE DESCRIPTION:\n{base}"
    )


def _check_shot_prompt(text: str | None, base: str) -> list[str]:
    t = (text or "").strip()
    if not t:
        return ["the <prompt> block is missing or empty"]
    problems = []
    if re.search(r"</?[A-Za-z_][\w.\-]*>", t):
        problems.append("a tag is left inside the description")
    ratio = len(t) / max(len(base), 1)
    if ratio < _SHOT_MIN_RATIO:
        problems.append("the description is much shorter than the original")
    elif ratio > _SHOT_MAX_RATIO:
        problems.append("the description is much longer than the original")
    if _norm(t) == _norm(base):
        problems.append("the description is unchanged")
    return problems


async def _ask(llm_wf_file: str, wf_template: dict | None, user: str, *, max_tokens: int, grok_api_key: str, grok_model: str, on_log=None) -> str | None:
    if llm_wf_file == GROK_MARKER:
        return await _call_grok_text(_SYSTEM, user, api_key=grok_api_key, model=grok_model, max_tokens=max_tokens, on_log=on_log)
    return await _call_local_text(wf_template or {}, _SYSTEM, user, max_tokens=max_tokens, session_id="sp" + uuid.uuid4().hex[:6], on_log=on_log, reset=True)


def _tokens_for(text: str, factor: float, extra: int, cap: int = 6144) -> int:
    return int(min(cap, max(768, _fc.estimate_tokens(text) * factor + extra)))


async def run_static_pose_fill(
    static_prompt: str,
    n_shots: int,
    llm_wf_file: str,
    on_err,
    on_log=None,
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    on_shot=None,
    stop_event: asyncio.Event | None = None,
) -> dict | None:
    """Returns {"base_prompt": str, "source_style": str (the removed style words), "plan": [...], "shots": [native shot dicts], "failed": [shot numbers]} or None when the base prompt or the plan could not be made
    (on_err has been told why). A shot whose prompt could not be written is kept with an empty prompt and "_fill_failed": True."""
    source = (static_prompt or "").strip()
    n = int(n_shots or 0)
    if not source:
        await on_err("Static Pose Fill: the input is empty")
        return None
    if n < 1:
        await on_err("Static Pose Fill: the number of shots must be at least 1")
        return None
    wf_template = None
    if llm_wf_file != GROK_MARKER:
        if not (llm_wf_file or "").strip():
            await on_err("Static Pose Fill: no local LLM workflow selected")
            return None
        wf_path = LLM_DIR / f"{llm_wf_file}.json"
        if not wf_path.exists():
            await on_err(f"Static Pose Fill: workflow not found: {wf_path}")
            return None
        try:
            wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
        except Exception as exc:
            await on_err(f"Static Pose Fill: workflow read failed: {exc}")
            return None

    async def log(msg: str):
        if on_log:
            await on_log(msg)

    def stopped() -> bool:
        return bool(stop_event and stop_event.is_set())

    # 1. BASE: the prompt without its style words
    base = None
    style = ""
    problems: list[str] = []
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        if stopped():
            await on_err("Static Pose Fill: stopped")
            return None
        raw = await _ask(llm_wf_file, wf_template, _build_base_user(source), max_tokens=_tokens_for(source, 1.4, 256, 4096),
                         grok_api_key=grok_api_key, grok_model=grok_model, on_log=on_log)
        cand = _tag(raw, "base_prompt")
        problems = []
        if not (cand or "").strip():
            problems.append("the <base_prompt> block is missing or empty")
        elif len(cand.strip()) < _SHOT_MIN_RATIO * len(source) or len(cand.strip()) > 1.3 * len(source):
            problems.append("the length of the base prompt is not plausible")
        elif re.search(r"</?[A-Za-z_][\w.\-]*>", cand):
            problems.append("a tag is left inside the base prompt")
        if not problems:
            base = cand.strip()
            style = " ".join((_tag(raw, "source_style") or "").split())      # the removed style words: they go to the project's "as source" style
            await log(f"ℹ Static Pose Fill: base prompt ready (attempt {attempt}), {len(source)} -> {len(base)} characters; source style: {style!r}")
            break
        await log(f"⚠ Static Pose Fill: base prompt attempt {attempt}: " + "; ".join(problems))
    if base is None:
        await on_err("Static Pose Fill: the style words could not be removed after 3 attempts - " + "; ".join(problems))
        return None

    # 2. PLAN: N different poses
    plan = None
    problems = []
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        if stopped():
            await on_err("Static Pose Fill: stopped")
            return None
        raw = await _ask(llm_wf_file, wf_template, _build_plan_user(base, n, problems if attempt > 1 else None), max_tokens=min(8192, 400 + 140 * n),
                         grok_api_key=grok_api_key, grok_model=grok_model, on_log=on_log)
        plan, problems = _parse_plan(raw, n)
        if plan is not None:
            await log(f"ℹ Static Pose Fill: {n} poses planned (attempt {attempt})")
            break
        await log(f"⚠ Static Pose Fill: plan attempt {attempt}: " + "; ".join(problems))
    if plan is None:
        await on_err("Static Pose Fill: the pose plan was not accepted after 3 attempts - " + "; ".join(problems))
        return None

    # 3. WRITE: one prompt per shot
    shots: list[dict] = []
    failed: list[int] = []
    for i, item in enumerate(plan, 1):
        if stopped():
            await log("⏹ Static Pose Fill: stopped by the user")
            break
        written, problems = None, []
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            raw = await _ask(llm_wf_file, wf_template, _build_shot_user(base, item, problems if attempt > 1 else None), max_tokens=_tokens_for(base, 1.8, 384),
                             grok_api_key=grok_api_key, grok_model=grok_model, on_log=on_log)
            cand = _tag(raw, "prompt")
            problems = _check_shot_prompt(cand, base)
            if not problems:
                written = cand.strip()
                break
            await log(f"⚠ Static Pose Fill: shot {i} attempt {attempt}: " + "; ".join(problems))
        shot = {
            "shot": i,
            "uses": [],
            "visible_people": "",
            "shot_desc": f"(Pose: {item['pose']}) (Expression: {item['expression']}) (Emotion: {item['emotion']}) (Camera: {item['camera']})",
            "story": f"{item['pose']} / {item['expression']}",
            "objects": "",
            "camera": item["camera"],
            "narration": "",
            "speech_char": "",
            "subtitle": "",
            "prompt": written or "",
            "prompt_style": "narrative",
            "_static_pose": True,
        }
        if written is None:
            shot["_fill_failed"] = True
            failed.append(i)
            await log(f"❌ Static Pose Fill: shot {i} was not accepted after {_MAX_ATTEMPTS} attempts - " + "; ".join(problems))
        shots.append(shot)
        if on_shot:
            await on_shot(i - 1, shot, n)
    return {"base_prompt": base, "source_style": style, "plan": plan, "shots": shots, "failed": failed}
