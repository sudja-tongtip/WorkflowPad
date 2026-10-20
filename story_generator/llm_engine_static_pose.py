"""
llm_engine_static_pose.py - POSE VARIATIONS (internal name: static pose): ONE image prompt becomes a set of prompts that differ in pose, facial expression, emotion and camera.

No Story Bible, no sheets, no cast list, no Beat Sheet, no B and no Fill: the user's own prompt, taken as ONE complete visual composition, is the source and the writer.

    1. BASE   one call: the prompt without its STYLE words (art style, medium, rendering look, quality tags). The words that were removed come back as the
              source style. The app adds the style itself at render time.
    2. PLAN   one call: N variations, each with pose / expression / emotion / camera. No story, no events; the variations only have to differ.
    3. WRITE  one call per shot: the SAME base prompt rewritten for that shot (never the previous shot's prompt, so nothing drifts from shot to shot).

Python only checks STRUCTURE (the tag is there, the text is not empty, the count is right, no two plans are the same, no tag left inside, the answer is not just the plan).
It never reads the meaning of the prompt or of the answer, so it can NOT guarantee that every subject, object or garment of the original survived: that is up to the model.
Every call is a fresh session: nothing grows, so a long run cannot overfill the model's context.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid

from .constants import LLM_DIR
from .llm_engine_a1 import GROK_MARKER, _call_grok_text, _call_local_text
from . import fill_conversation as _fc

__version__ = "20261020_pose_variations_2"

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


def _loose(text: str) -> str:
    """Lower case, every character that is not a letter or a digit becomes a space, runs of spaces collapse: two texts that differ only in spacing,
    punctuation or capitals have the same loose form."""
    return " ".join(re.sub(r"[^\w]+|_", " ", (text or "").lower()).split())


def _word_set(text: str) -> str:
    """The same words in any order: the loose form with its words sorted and repeated words dropped (a plan that only re-phrases the order of its words is not new)."""
    return " ".join(sorted(set(_loose(text).split())))


def _build_base_user(prompt: str) -> str:
    return (
        "Below is an image prompt. Take its STYLE words out and change nothing else.\n"
        "STYLE words are ONLY words that name the art style, the medium, the rendering look, or quality tags: for example \"oil painting\", \"digital art\", \"anime style\", "
        "\"cinematic\", \"photorealistic\", \"masterpiece\", \"highly detailed\", \"8k\", \"film grain\".\n"
        "Everything that is not style stays exactly as written: do not summarise, shorten or rewrite anything. Keep the original language, the order of the text and the details. "
        "Keep the number and the kind of the subjects (people, animals, objects), their clothing, the objects, the location, the lighting, the colours, the mood and the camera framing "
        "exactly as the prompt gives them.\n"
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
        "Treat the original image prompt below as one complete visual composition. "
        f"Plan {n} variations by changing poses, facial expressions where applicable, emotions and camera perspectives. "
        "Preserve all original subjects, clothing, objects, environment and lighting. Do not add characters, remove subjects or invent story events.\n"
        "Rules:\n"
        "- Keep every subject that the prompt has. Do not assume there is one person: there may be several people, animals or objects.\n"
        "- If there are several subjects, change their poses together in a way that suits the whole composition.\n"
        "- The pose of every variation differs from the original and from the other variations.\n"
        "- Expression and emotion must fit the subjects that really exist. Do not give a human expression to something without a face: write none for expression (and for emotion if it does not apply) in that case.\n"
        "- The camera may change angle, distance and framing, but must not hide the important details of the original.\n"
        "- No action sequence and no story progression: the variations are separate, independent pictures of the same composition.\n"
        f"Reply in exactly this format for shot 1 to shot {n} and nothing else:\n{blocks}{more}\n{fix}\n"
        f"ORIGINAL PROMPT:\n{base}"
    )


def _parse_plan(raw: str | None, n: int) -> tuple[list[dict] | None, list[str]]:
    problems: list[str] = []
    # The <shot_k> tags are only a frame: a small model often shifts a closing tag (</shot_4> after shot 3, a shot without its opening tag). The DATA is the labelled lines, so the
    # tags are ignored and every "pose:" line starts the next variation, which then collects its expression / emotion / camera lines.
    shots: list[dict] = []
    cur: dict | None = None
    last_key = ""
    for line in (raw or "").splitlines():
        clean = re.sub(r"</?shot_\d+\s*>", "", line).strip()
        if not clean:
            continue
        m = re.match(r"^(pose|expression|emotion|camera)\s*:\s*(.*)$", clean, re.IGNORECASE)
        if m:
            key, val = m.group(1).lower(), m.group(2).strip()
            if key == "pose":
                cur = {"pose": val}
                shots.append(cur)
                last_key = "pose"
            elif cur is not None and key not in cur:
                cur[key] = val
                last_key = key
            continue
        if cur is not None and last_key and not clean.startswith("<"):     # a value that continues on the next line
            cur[last_key] = (cur[last_key] + " " + clean).strip()
    if len(shots) != n:
        problems.append(f"the plan has {len(shots)} variation(s) instead of {n}")
    for i, item in enumerate(shots[:n], 1):
        missing = [k for k in _FIELDS if not (item.get(k) or "").strip()]
        if missing:
            problems.append(f"variation {i} has no " + ", ".join(missing))
    if problems:
        return None, problems
    shots = shots[:n]
    # The same pose twice: exact, or the same after spacing / punctuation / capitals / word order are ignored. Different words are NOT taken to mean a different pose, and
    # same words are not proof of the same pose either: this only catches a plan that repeats itself in the plainest ways.
    seen_loose: dict[str, int] = {}
    seen_words: dict[str, int] = {}
    for i, it in enumerate(shots, 1):
        k1, k2 = _loose(it["pose"]), _word_set(it["pose"])
        first = seen_loose.get(k1) or seen_words.get(k2)
        if first:
            problems.append(f"shot {i} repeats the pose of shot {first}")
            continue
        seen_loose[k1] = i
        seen_words[k2] = i
    return (None, problems) if problems else (shots, [])


def _build_shot_user(base: str, plan: dict, previous_problems: list[str] | None = None) -> str:
    fix = ("\nYour previous reply could not be accepted: " + "; ".join(previous_problems[:3]) + ". Write the complete prompt again.\n") if previous_problems else ""
    return (
        "Rewrite the original image prompt below as the complete prompt of one new shot.\n"
        "Change the pose, the expression, the emotion and the camera to this plan:\n"
        f"pose: {plan['pose']}\nexpression: {plan['expression']}\nemotion: {plan['emotion']}\ncamera: {plan['camera']}\n"
        "Rules:\n"
        "- Where the original already speaks about the pose, the face, the mood or the camera, replace it. If the original has no clear pose or camera, add the short description that is needed.\n"
        "- Remove every phrase that still describes the old pose, expression or camera and that contradicts the new one.\n"
        "- Keep everything else of the original: the subjects, clothing, objects, location, lighting and colours, with the wording of the original as far as you can.\n"
        "- Do not add people, objects or events. Do not add style words (no art style, medium, rendering look or quality tags): the style is handled separately.\n"
        "- Write the whole prompt, not a list of what changed.\n"
        f"Reply with the complete rewritten prompt inside <prompt></prompt> and nothing else.\n{fix}\n"
        f"ORIGINAL PROMPT:\n{base}"
    )


def _check_shot_prompt(text: str | None, base: str, plan: dict | None = None) -> list[str]:
    """Structure only. A short or odd answer is caught; whether every person, object and garment survived is NOT checked here."""
    t = (text or "").strip()
    if not t:
        return ["the <prompt> block is missing or empty"]
    problems = []
    if re.search(r"</?[A-Za-z_][\w.\-]*>", t):
        problems.append("a tag is left inside the prompt")
    ratio = len(t) / max(len(base), 1)
    if ratio < _SHOT_MIN_RATIO:
        problems.append("the prompt is much shorter than the original")
    elif ratio > _SHOT_MAX_RATIO:
        problems.append("the prompt is much longer than the original")
    if _loose(t) == _loose(base):
        problems.append("the prompt is unchanged")
    if len(re.findall(r"(?im)^[ \t]*(?:pose|expression|emotion|camera)[ \t]*:", t)) >= 2:
        problems.append("the answer lists the plan fields instead of writing the prompt")
    if plan and _loose(t) == _loose(" ".join(str(plan.get(k) or "") for k in _FIELDS)):
        problems.append("the answer is only the plan")
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
    """Pose Variations. Returns {"base_prompt": str, "source_style": str (the removed style words), "plan": [...], "shots": [native shot dicts], "failed": [shot numbers]} or None
    when the base prompt or the plan could not be made (on_err has been told why). A shot whose prompt could not be written is kept with an empty prompt and "_fill_failed": True."""
    source = (static_prompt or "").strip()
    n = int(n_shots or 0)
    if not source:
        await on_err("Pose Variations: the input is empty")
        return None
    if n < 1:
        await on_err("Pose Variations: the number of shots must be at least 1")
        return None
    wf_template = None
    if llm_wf_file != GROK_MARKER:
        if not (llm_wf_file or "").strip():
            await on_err("Pose Variations: no local LLM workflow selected")
            return None
        wf_path = LLM_DIR / f"{llm_wf_file}.json"
        if not wf_path.exists():
            await on_err(f"Pose Variations: workflow not found: {wf_path}")
            return None
        try:
            wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
        except Exception as exc:
            await on_err(f"Pose Variations: workflow read failed: {exc}")
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
            await on_err("Pose Variations: stopped")
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
            await log(f"ℹ Pose Variations: base prompt ready (attempt {attempt}), {len(source)} -> {len(base)} characters; source style: {style!r}")
            break
        await log(f"⚠ Pose Variations: base prompt attempt {attempt}: " + "; ".join(problems))
    if base is None:
        await on_err("Pose Variations: the style words could not be removed after 3 attempts - " + "; ".join(problems))
        return None

    # 2. PLAN: N different poses
    plan = None
    problems = []
    best: list[dict] = []                      # the longest plan with FEWER variations than asked that is otherwise sound (complete fields, no repeated pose)
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        if stopped():
            await on_err("Pose Variations: stopped")
            return None
        raw = await _ask(llm_wf_file, wf_template, _build_plan_user(base, n, problems if attempt > 1 else None), max_tokens=min(8192, 400 + 140 * n),
                         grok_api_key=grok_api_key, grok_model=grok_model, on_log=on_log)
        plan, problems = _parse_plan(raw, n)
        if plan is not None:
            await log(f"ℹ Pose Variations: {n} variations planned (attempt {attempt})")
            break
        await log(f"⚠ Pose Variations: plan attempt {attempt}: " + "; ".join(problems))
        got = len(re.findall(r"^\s*(?:</?shot_\d+\s*>\s*)*pose\s*:", raw or "", re.IGNORECASE | re.MULTILINE))
        if 0 < got < n:
            part, _why = _parse_plan(raw, got)         # the same checks, for the number the model really wrote
            if part and len(part) > len(best):
                best = part
    if plan is None and best:
        plan = best                            # fewer than asked: use them and tell the user (status line), not a failure
        await log(f"⚠ Pose Variations: the model planned only {len(plan)} of the {n} variations after {_MAX_ATTEMPTS} attempts - continuing with {len(plan)}")
    if plan is None:
        await on_err("Pose Variations: the plan was not accepted after 3 attempts - " + "; ".join(problems))
        return None

    # 3. WRITE: one prompt per shot, every one from the SAME base prompt
    shots: list[dict] = []
    failed: list[int] = []
    for i, item in enumerate(plan, 1):
        if stopped():
            await log("⏹ Pose Variations: stopped by the user")
            break
        written, problems = None, []
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            raw = await _ask(llm_wf_file, wf_template, _build_shot_user(base, item, problems if attempt > 1 else None), max_tokens=_tokens_for(base, 1.8, 384),
                             grok_api_key=grok_api_key, grok_model=grok_model, on_log=on_log)
            cand = _tag(raw, "prompt")
            problems = _check_shot_prompt(cand, base, item)
            if not problems:
                written = cand.strip()
                break
            await log(f"⚠ Pose Variations: shot {i} attempt {attempt}: " + "; ".join(problems))
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
            await log(f"❌ Pose Variations: shot {i} was not accepted after {_MAX_ATTEMPTS} attempts - " + "; ".join(problems))
        shots.append(shot)
        if on_shot:
            await on_shot(i - 1, shot, n)
    return {"base_prompt": base, "source_style": style, "plan": plan, "shots": shots, "failed": failed, "requested": n}
