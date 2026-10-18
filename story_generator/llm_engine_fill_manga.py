"""llm_engine_fill_manga.py — Manga Prompts engine (legacy name: Fill Manga).

Image Prompts for manga pages: converts the current page/panel truth into renderer-facing HOW.

Design rule:
    LLM = visual staging + renderer prompt writing.
    Python = transport + explicit structure only.

Page List owns WHAT exists and page geometry. Manga Prompts owns HOW each panel is
shown while preserving the page/panel truth.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
import urllib.error
import urllib.request
import uuid

from .constants import LLM_DIR
from .comfy_api import comfy_queue, comfy_history
from . import manga_grid as _grid
from .visual_direction import visual_direction_for_preset

__version__ = "20260923_fill_manga_clean_a1"

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|```\s*$")
GROK_MARKER = "[Grok API]"
FILL_ENGINE_MANGA_VNEXT = "manga_vnext"
_LLM_TIMEOUT = 120
_GROK_TEMPERATURE = 0.45
_GROK_REASONING_EFFORT = "low"


def _strip_code_fence(raw: str) -> str:
    return _CODE_FENCE_RE.sub("", raw.strip()).strip()


_MANGA_LOCKS = ("IDENTITY LOCK", "LOCATION LOCK", "OBJECT LOCK")
_MANGA_HEADER_RE = re.compile(r"(?m)^[ \t]*(PAGE LAYOUT|IDENTITY LOCK|LOCATION LOCK|OBJECT LOCK|PANEL[ \t]+(\d+))[ \t]*:")
# Any other line shaped like a section header: an upper-case label at the very start of a line followed by a colon ("STYLE:", "MOTION:"). Prose such as
# 'The television reads: OFF.' or 'A sign displays "TV: off".' has the colon mid-line and never starts with an all-capital label; other scripts have no [A-Z].
_MANGA_ANY_HEADER_RE = re.compile(r"(?m)^[ \t]*([A-Z][A-Z0-9 /&_-]{1,40}[A-Z0-9])[ \t]*:")
_PREAMBLE_MAX_LINES = 2                   # the contract has ONE short instruction line before PAGE LAYOUT (all 31 stored pages: exactly one, at most 238 characters)
_PREAMBLE_MAX_CHARS = 600


def validate_manga_page_output(text: str, panel_count: int = 0) -> str:
    """Structure only: '' when the reply is a well-formed page prompt, otherwise the reason. The text of a section is never read.
    Contract: [one instruction line] PAGE LAYOUT, then the optional IDENTITY / LOCATION / OBJECT LOCK blocks (each at most once, in that order),
    then PANEL 1..N exactly once each, in order (N = the page's panel count when known), no empty section."""
    t = (text or "").strip()
    if not t:
        return "empty reply"
    found = list(_MANGA_HEADER_RE.finditer(t))
    if not found:
        return "no section headers (PAGE LAYOUT / PANEL n) found"
    known = {m.start() for m in found}
    for m in _MANGA_ANY_HEADER_RE.finditer(t):
        if m.start() not in known:
            return f"unknown section header {m.group(1).strip()}:"
    names = [("PANEL" if m.group(2) else m.group(1)) for m in found]
    if names.count("PAGE LAYOUT") != 1:
        return f"PAGE LAYOUT appears {names.count('PAGE LAYOUT')} times"
    for lock in _MANGA_LOCKS:
        if names.count(lock) > 1:
            return f"{lock} appears {names.count(lock)} times"
    order = ["PAGE LAYOUT"] + [x for x in _MANGA_LOCKS if x in names] + ["PANEL"] * names.count("PANEL")
    if names != order:
        return "sections are not in the contract order: " + ", ".join(names)
    nums = [int(m.group(2)) for m in found if m.group(2)]
    if not nums:
        return "no PANEL section"
    if nums != list(range(1, len(nums) + 1)):
        return "PANEL numbers are not 1..N in order: " + ", ".join(map(str, nums))
    if panel_count and len(nums) != panel_count:
        return f"{len(nums)} PANEL sections, the page has {panel_count}"
    pre = [ln for ln in t[:found[0].start()].splitlines() if ln.strip()]
    if len(pre) > _PREAMBLE_MAX_LINES or sum(len(ln) for ln in pre) > _PREAMBLE_MAX_CHARS:
        return f"{len(pre)} lines of text before PAGE LAYOUT (the contract has one short instruction line)"
    for i, m in enumerate(found):
        body = t[m.end():(found[i + 1].start() if i + 1 < len(found) else len(t))]
        if not body.strip():
            return f"section {m.group(1).strip()} is empty"
    return ""


_FILL_MANGA_SYSTEM_PROMPT = """You are the visual director writing one final Krea 2 multi-panel page prompt.

CURRENT B MANGA PAGE is the authority for WHAT exists and for page geometry.
Preserve its exact panel count, panel order, page locks, every panel desc, and the relationships between them.

PRODUCTION SHEETS (character/world/location/object/continuity reference, supplied separately) are canonical, stable reference only — never proof of current presence or current state. CURRENT B MANGA PAGE is the current page structure and supplied page truth; CURRENT PANEL DESC is the sole authority for what is true now inside that specific panel: visible people, current action/interaction, current clothing, current physical state, current object presence/state, current location/sub-area when specified, and that panel's own chronology.

Each panel description is a compact piece of natural visual prose for Krea2. Preserve the panel's factual truth exactly, but express it as one coherent visual moment rather than a list of attributes or prompt fragments. For every panel, preserve who is visible, the current action/interaction, current clothing and physical state, location, objects actually present, panel chronology, and the page-level identity/location/object locks. You may improve phrasing, spatial clarity, visible expression, and environmental presentation, and decide staging that changes no story fact (small angles, which body overlaps which from the camera side). When a panel gives body geometry or contact (which part touches which, where hands, knees and feet are), keep every part of it; when it states only the action, state that action as the panel does and add no contact point and no hand, arm or leg position it does not give. Never invent a new event, cause, state, object, relationship, or chronology.

Write natural English sentences with concrete visible action, clear body orientation/contact/relative position, concise spatial description, current clothing/state, and the relevant setting. Avoid keyword piles, comma-only attribute lists, cinematic filler, abstract story explanation, invisible motives, and repeating the same fact in multiple forms.

Treat the page as one continuous visual sequence. Keep character identity, location, clothing state, object state, and spatial continuity coherent across panels unless the panel truth explicitly changes them.

A panel desc is the presence authority for that panel. Do not add a person, object, action, clothing state, location detail, dialogue, reaction, or story meaning merely because a page lock or a production sheet mentions it — a global reference may describe a fact belonging to a different point in the story than this panel. Never import a person, object, clothing state, physical state, location state, action, or future transition into a panel merely because it appears in a stable sheet or a page lock.
IDENTITY LOCK, LOCATION LOCK, and OBJECT LOCK are permanent identity anchors only; preserve their facts without turning them into panel-presence claims.
PAGE LAYOUT TEXT: when the user message carries a PAGE LAYOUT TEXT block, copy that text exactly — every word, including its first sentence about thick black borders — as the whole body of PAGE LAYOUT. Do not shorten it, reword it, merge it, or add percentages, sizes or coordinates to it. It already states the panel count and where each panel sits. Without such a block, translate region_pct as described below.
OUTFIT CONTINUITY (the one exception to the clothing rules above about sheets and page locks): every person in IDENTITY LOCK gets a complete outfit there. Resolve it in this order: (1) a PANEL DESC of this page that states or changes the outfit (that panel keeps its own statement); (2) otherwise the outfit stated for that person in PREVIOUS PAGE IDENTITY LOCK, when that note is supplied; (3) otherwise that person's init_cloth in CHARACTERS — init_cloth is only the outfit at the very start of the story, so once PREVIOUS PAGE IDENTITY LOCK states an outfit for that person it replaces init_cloth for the rest of the story. When a panel of this page changes the outfit, IDENTITY LOCK gives the outfit the person has after the change. Keep an outfit until a panel explicitly changes it, and never restore an outfit that has already changed. This applies only to people who are visible on this page — never add a person, and never add a different outfit, merely to describe clothing.
A person may be identified by a trigger word (an identity token of a trained character model). Keep such a word exactly as written; never replace it with a name or a description, and never mention or explain trigger status.

Write IDENTITY LOCK, LOCATION LOCK, and OBJECT LOCK as normal spoken English sentences, the same as a PANEL description — never as tags, labels, or key:value pairs, and never a comma-only attribute list. No "trigger:", no "Name:", no attribute dumped after a colon.
Build IDENTITY LOCK from the UNION of people actually visible across all CURRENT PANEL DESC entries on this page — never include a character merely because CHARACTERS lists them. For each of those people, resolve CHARACTERS (the sheet gives WHAT is stable about a person; the panel desc decides WHETHER they are visible). IDENTITY LOCK describes each such character's stable identity and design for the whole page, in complete sentences, and MUST explicitly include: their identifier (trigger token or name, used naturally the way a sentence uses someone's name), their supplied gender, and their supplied age/age range (exact age stays exact, a range stays a range, never invented when absent, and never remark that a fact is missing) — not optional, even for secondary characters or when a trigger is present — plus nationality when known/relevant, hair, face, body, skin, and permanent marks. Finish each person's part of IDENTITY LOCK with a plain sentence about what that person wears (resolved by OUTFIT CONTINUITY; the outfit they have after any change on this page), so the next page can carry it on — state it as a fact such as "He wears ...", never as a "final outfit". Current physical state at each moment belongs in each PANEL's own prose, per that panel's truth.
Age and gender live in this page-level lock once; PANEL prose does not need to repeat them.
LOCATION LOCK describes the canonical page-level place, the relevant sub-area, and any important stable spatial or boundary relationship, in complete sentences. If a panel changes sub-area, boundary relation, or location state, describe that current fact inside that PANEL instead.
BAD: "Location: airplane cabin, window seat, inside"
GOOD: "The scene takes place inside the passenger cabin of an airplane, at a seat beside the window."
OBJECT LOCK describes only the stable identity, ownership, and reference facts of page-level objects supplied by CURRENT B MANGA PAGE, in complete sentences. Actual object presence and current object state are decided per PANEL DESC — an object lock here must not make that object appear, or appear in a particular state, in every panel.
If any other semantic block appears, apply the same rule: the header stays a structural label, but its content is always normal spoken/written English sentences, never a tag, key:value pair, or attribute dump.

Translate each region_pct into clear natural-language page placement and relative panel size. Do not output raw percentages, coordinates, region_pct, or bounding-box syntax.
Keep page geometry separate from camera framing: panel size/placement describes the page; framing describes what is seen inside that panel.

Realize each panel's same truth per the supplied VISUAL DIRECTION (framing/scale, performance, composition, camera, lighting, environment, motion), applied inside each panel only and solved per panel — not identical framing everywhere. Choose the technique yourself; never write control labels. It authorizes HOW only: never change panel count or order, region_pct, panel truth, or advance the story between panels.
Keep camera and light concise and woven naturally into the panel's prose — never let them dominate it or turn a panel into a cinematography essay, and never invent a light source the panel/page truth does not establish.
Within each panel, preserve explicit inside/outside/through/boundary relations and familiar real-world object geometry and scale; if the framing conflicts, adapt framing/staging — never the panel truth or the object.
Each PANEL section must remain one physical instant; never merge consecutive panel states into one panel.

Write concrete natural English for Krea 2. Translate supplied non-English text into English while preserving meaning. Use STYLE only for visual look and do not add competing aesthetics.

For a single page output only this structure:
(line 1: write one short instruction sentence, in your own words, that asks for ONE page with the exact supplied number of clearly separated panels — no brackets, no label)

PAGE LAYOUT:
<one concise geometry line per panel>

IDENTITY LOCK:
<one or more natural English sentences per visible character — stable page-level identity: identifier, gender, age/age range, nationality when known, hair, face, body, skin, and permanent marks, then what the person wears after any change on the page; current physical state belongs in each PANEL; never tags or key:value pairs>
(omit this block when empty)

LOCATION LOCK:
<one or more natural English sentences describing the canonical page-level place, relevant sub-area, and any stable spatial/boundary relationship; a panel-specific change belongs in that PANEL instead>
(omit this block when empty)

OBJECT LOCK:
<one or more natural English sentences describing each page-level object's stable identity, ownership, and reference facts; current presence/state is decided per PANEL, not here>
(omit this block when empty)

PANEL 1:
<compact natural visual prose: the panel's placement/relative size, then its complete visual moment as one coherent scene, with camera/framing woven in naturally rather than listed separately>

PANEL 2:
<...>

Continue through the exact supplied panel count in order. Do not output JSON for a single-page request.

Before answering, collect the union of people actually visible across all panel descriptions. For each visible person, resolve CHARACTERS and verify that the supplied gender and supplied age/age range are explicitly present in page-level IDENTITY LOCK. Do not add people who are not visible."""


def _page_spec(shot: dict) -> dict:
    spec = shot.get("_manga_page_spec")
    return spec if isinstance(spec, dict) else shot


def _style_block(image_style: str) -> str:
    return (image_style or "").strip()


def _production_reference(a1: dict, *, props_desc: str) -> str:
    """Transport-only: routes A1's existing production-reference sheets verbatim, never
    interprets/filters/merges them. macro_events (chronology) and source_style (style metadata)
    are not production sheets and are intentionally excluded here, same as Normal Fill."""
    a1 = a1 or {}
    parts = [
        f"CHARACTERS:\n{a1.get('character_sheet', '')}",
        f"WORLD:\n{a1.get('world_sheet', '')}",
        f"LOCATIONS:\n{a1.get('locations', '')}",
    ]
    object_ref = (props_desc or "").strip() or str(a1.get("important_objects") or "").strip()
    if object_ref:
        parts.append("IMPORTANT OBJECTS:\n" + object_ref)
    continuity = str(a1.get("continuity_notes") or "").strip()
    if continuity:
        parts.append("CONTINUITY:\n" + continuity)
    return "PRODUCTION REFERENCE:\n" + "\n\n".join(parts)


def _page_payload(spec: dict) -> str:
    fields = {
        "page": spec.get("page"),
        "identity_lock": spec.get("identity_lock", ""),
        "location_lock": spec.get("location_lock", ""),
        "object_lock": spec.get("object_lock", ""),
        "panels": [
            {
                "panel": p.get("panel"),
                "region_pct": p.get("region_pct"),
                "desc": p.get("desc", ""),
            }
            for p in (spec.get("panels") or []) if isinstance(p, dict)
        ],
    }
    return json.dumps(fields, ensure_ascii=False, indent=2)


_IDENTITY_LOCK_RE = re.compile(r"^[ \t]*IDENTITY LOCK:[ \t]*\n(.*?)(?=^[ \t]*LOCATION LOCK:)", re.M | re.S)


def _extract_identity_lock(prompt: str) -> str:
    """Structural slice only: the text between the IDENTITY LOCK: and LOCATION LOCK: headers of a finished page prompt ('' when the headers are not there)."""
    m = _IDENTITY_LOCK_RE.search(prompt or "")
    return m.group(1).strip() if m else ""


def _build_local_user_prompt(spec: dict, a1: dict, *, image_style: str, props_desc: str,
                             visual_direction: str, previous_identity_lock: str = "") -> str:
    parts: list[str] = [_production_reference(a1, props_desc=props_desc)]
    if _style_block(image_style):
        parts.append("STYLE:\n" + _style_block(image_style))
    parts.append(visual_direction)
    parts.append("PREVIOUS PAGE IDENTITY LOCK (outfit carry-over note — the outfit it states for a person replaces init_cloth in CHARACTERS):\n" + (
        previous_identity_lock.strip() or "none — this is the first page, or no earlier page prompt exists; take each outfit from CHARACTERS (init_cloth) unless a panel desc states it."))
    layout = _grid.layout_text_for([p.get("region_pct") for p in (spec.get("panels") or []) if isinstance(p, dict)])
    if layout:
        parts.append("PAGE LAYOUT TEXT (copy exactly as the whole body of PAGE LAYOUT; no added percentages or numbers):\n" + layout)
    parts.append("CURRENT B MANGA PAGE:\n" + _page_payload(spec))
    parts.append("Write the complete final Krea 2 page prompt now.")
    return "\n\n".join(parts)


def _build_batch_user_prompt(shots: list[dict], a1: dict, *, image_style: str, props_desc: str,
                             visual_direction: str) -> str:
    payloads = []
    page_numbers: list[int] = []
    for idx, shot in enumerate(shots):
        spec = _page_spec(shot)
        num = spec.get("page") if isinstance(spec.get("page"), int) else shot.get("shot")
        if not isinstance(num, int):
            num = idx + 1
        page_numbers.append(num)
        payloads.append({"page": num, "b_manga_page": json.loads(_page_payload(spec))})
    parts: list[str] = [_production_reference(a1, props_desc=props_desc)]
    if _style_block(image_style):
        parts.append("STYLE:\n" + _style_block(image_style))
    parts.append(visual_direction)
    parts.append("TARGET B MANGA PAGES:\n" + json.dumps(payloads, ensure_ascii=False, indent=2))
    parts.append(
        "Return JSON only:\n"
        '{"pages": [{"page": <target page number>, "prompt": "<complete final page prompt>"}]}\n'
        f"Return exactly one item for each target page number: {page_numbers}. "
        "Inside every prompt string use the single-page block structure required by the system prompt."
    )
    return "\n\n".join(parts)


def _local_max_tokens(spec: dict) -> int:
    panels = spec.get("panels") or []
    text_size = sum(len(str((p or {}).get("desc") or "")) for p in panels if isinstance(p, dict))
    return max(2200, min(8000, text_size + len(panels) * 700 + 1600))


def _batch_max_tokens(shots: list[dict]) -> int:
    total = 2500
    for shot in shots:
        spec = _page_spec(shot)
        panels = spec.get("panels") or []
        total += max(1600, len(panels) * 1000)
    return max(5000, min(32000, total))


async def _call_local_text(
    wf_template: dict,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
    session_id: str,
    on_log=None,
) -> str | None:
    wf = copy.deepcopy(wf_template or {})
    hit = False
    for nid, node in wf.items():
        if not isinstance(node, dict) or node.get("class_type") != "LLMSessionChatNode":
            continue
        inputs = wf[nid].setdefault("inputs", {})
        inputs["user_text"] = user_prompt
        inputs["system_prompt"] = system_prompt
        inputs["session_id"] = session_id
        inputs["reset_session"] = True
        inputs["max_tokens"] = max_tokens
        hit = True
    if not hit:
        if on_log:
            await on_log("❌ Manga Prompts: workflow has no LLMSessionChatNode")
        return None

    if on_log:
        await on_log(f"📋 MANGA RENDER PROMPT SYSTEM:\n{system_prompt}")
        await on_log(f"📤 MANGA RENDER PROMPT USER:\n{user_prompt}")

    loop = asyncio.get_event_loop()
    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, uuid.uuid4().hex)
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Manga Prompts queue: {exc}")
        return None

    deadline = time.time() + _LLM_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(2)
        try:
            hist = await loop.run_in_executor(None, comfy_history, pid)
        except Exception:
            continue
        if pid not in hist:
            continue
        entry = hist[pid]
        for node_out in (entry.get("outputs") or {}).values():
            for text in (node_out.get("text") or []):
                if isinstance(text, str) and text:
                    if on_log:
                        await on_log(f"📥 MANGA RENDER PROMPT RAW:\n{text}")
                    return text
        status = ((entry.get("status") or {}).get("status_str") or "").lower()
        if status in ("success", "error"):
            break
    if on_log:
        await on_log("❌ Manga Prompts: local LLM returned no text")
    return None


async def _call_grok_text(
    system_prompt: str,
    user_prompt: str,
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    on_log=None,
    meta: dict | None = None,
) -> str | None:
    """meta (optional, filled by this call): {"finish_reason": ...} from the API response ("length" = the reply hit max_tokens)."""
    if not api_key:
        if on_log:
            await on_log("❌ Manga Prompts: Grok API key is empty")
        return None
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": _GROK_TEMPERATURE,
        "reasoning_effort": _GROK_REASONING_EFFORT,
    }

    def _post(body: dict) -> dict:
        req = urllib.request.Request(
            "https://api.x.ai/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=max(_LLM_TIMEOUT, 60)) as response:
            return json.loads(response.read())

    if on_log:
        await on_log(f"📋 MANGA RENDER PROMPT SYSTEM:\n{system_prompt}")
        await on_log(f"📤 MANGA RENDER PROMPT USER:\n{user_prompt}")

    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(None, _post, payload)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 400 and "reasoning" in body.lower():
            retry_payload = dict(payload)
            retry_payload.pop("reasoning_effort", None)
            try:
                response = await loop.run_in_executor(None, _post, retry_payload)
            except Exception as retry_exc:
                if on_log:
                    await on_log(f"❌ Manga Prompts Grok retry: {retry_exc}")
                return None
        else:
            if on_log:
                await on_log(f"❌ Manga Prompts Grok HTTP {exc.code}: {body[:500]}")
            return None
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Manga Prompts Grok: {exc}")
        return None

    choices = response.get("choices") if isinstance(response, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else {}
    message = first.get("message") if isinstance(first, dict) else {}
    text = message.get("content") if isinstance(message, dict) else None
    if meta is not None and isinstance(first, dict):
        meta["finish_reason"] = first.get("finish_reason")
    if not isinstance(text, str):
        if on_log:
            await on_log("❌ Manga Prompts: Grok response has no text content")
        return None
    if on_log:
        await on_log(f"📥 MANGA RENDER PROMPT RAW:\n{text}")
    return text


def _parse_batch_json(raw: str | None, target_numbers: list[int]) -> dict[int, str] | None:
    """Structural batch parser only; prompt strings are returned unchanged."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        obj = json.loads(_strip_code_fence(raw))
    except Exception:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("pages"), list):
        return None
    if len(obj["pages"]) != len(target_numbers):
        return None
    allowed = set(target_numbers)
    out: dict[int, str] = {}
    for item in obj["pages"]:
        if not isinstance(item, dict):
            return None
        num = item.get("page")
        prompt = item.get("prompt")
        if not isinstance(num, int) or num not in allowed or num in out:
            return None
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        out[num] = prompt
    return out if set(out) == allowed else None


async def run_fill_manga_vnext(
    pages: list[dict],
    a1: dict,
    llm_wf_file: str,
    on_err,
    on_log=None,
    image_style: str = "",
    genre: str = "",
    genre2: str = "",
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    on_page=None,
    props_desc: str = "",
    stop_event: asyncio.Event | None = None,
    visual_direction: str = "",
    previous_prompt: str = "",
) -> list[dict]:
    """Clean Manga Fill. genre/genre2 remain transport-neutral (unused); a1/props_desc now route
    the existing A1 production-reference sheets to the LLM instead of being discarded."""
    del genre, genre2
    total = len(pages)
    # resolved in Python from the shared Visual Preset (no LLM); default = Natural
    visual_direction = (visual_direction or "").strip() or visual_direction_for_preset("")

    if llm_wf_file == GROK_MARKER:
        if stop_event and stop_event.is_set():
            return pages
        target_numbers: list[int] = []
        for idx, shot in enumerate(pages):
            spec = _page_spec(shot)
            num = spec.get("page") if isinstance(spec.get("page"), int) else shot.get("shot")
            target_numbers.append(num if isinstance(num, int) else idx + 1)
        grok_meta: dict = {}
        raw = await _call_grok_text(
            _FILL_MANGA_SYSTEM_PROMPT,
            _build_batch_user_prompt(pages, a1, image_style=image_style, props_desc=props_desc,
                                     visual_direction=visual_direction),
            api_key=grok_api_key,
            model=grok_model,
            max_tokens=_batch_max_tokens(pages),
            on_log=on_log,
            meta=grok_meta,
        )
        cut_off = grok_meta.get("finish_reason") == "length"            # the API says the reply hit max_tokens: nothing of it is committed
        parsed = None if cut_off else _parse_batch_json(raw, target_numbers)
        if parsed is None:
            why_batch = ("the Grok reply was cut off (finish_reason=length)" if cut_off
                         else "no text returned by Grok" if not (isinstance(raw, str) and raw.strip())
                         else "Grok batch did not return the required JSON structure")
            await on_err("Manga Prompts: " + ("Grok reply was cut off at the output limit; nothing was committed" if cut_off else "Grok batch did not return the required JSON structure"))
            for idx, shot in enumerate(pages):
                shot["_fill_failed"] = True
                shot["_fill_rejected"] = {"reason": why_batch, "raw": raw if isinstance(raw, str) else "", "kind": "empty" if not (isinstance(raw, str) and raw.strip()) else "structure"}
                if on_page:
                    await on_page(idx + 1, total)
            return pages

        for idx, shot in enumerate(pages):
            if stop_event and stop_event.is_set():
                break
            prompt = parsed[target_numbers[idx]]
            why = validate_manga_page_output(prompt, len(_page_spec(shot).get("panels") or []))
            if why:
                if on_log:
                    await on_log(f"⚠ Manga Prompts (Grok): page {target_numbers[idx]} reply refused: {why}; the page keeps its old prompt")
                shot["_fill_failed"] = True
                shot["_fill_rejected"] = {"reason": why, "raw": prompt, "kind": "structure"}
                if on_page:
                    await on_page(idx + 1, total)
                continue
            shot["prompt"] = prompt
            shot.pop("_fill_rejected", None)
            shot["_fill_engine"] = FILL_ENGINE_MANGA_VNEXT
            shot["prompt_style"] = "manga_page"
            shot.pop("_fill_session_id", None)
            shot.pop("_fill_failed", None)
            shot.pop("_fill_renderer_ready", None)
            if on_log:
                await on_log(f"FILL Manga page={target_numbers[idx]} prompt_len={len(prompt)}")
            if on_page:
                await on_page(idx + 1, total)
        return pages

    if not (llm_wf_file or "").strip():
        await on_err("Manga Prompts: no local LLM workflow selected")
        return pages
    wf_path = LLM_DIR / f"{llm_wf_file}.json"
    if not wf_path.exists():
        await on_err(f"ไม่พบ {wf_path}")
        return pages
    try:
        wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
    except Exception as exc:
        await on_err(f"Manga Prompts workflow read failed: {exc}")
        return pages

    carry_lock = _extract_identity_lock(previous_prompt)        # IDENTITY LOCK of the page before this one (outfit carry-over note)
    for idx, shot in enumerate(pages):
        if stop_event and stop_event.is_set():
            if on_log:
                await on_log(f"⏹ Stop Manga Prompts after {idx}/{total} pages")
            break
        spec = _page_spec(shot)
        previous = shot.get("prompt", "")
        session_id = uuid.uuid4().hex[:8]
        raw = await _call_local_text(
            wf_template,
            _FILL_MANGA_SYSTEM_PROMPT,
            _build_local_user_prompt(spec, a1, image_style=image_style, props_desc=props_desc,
                                     visual_direction=visual_direction, previous_identity_lock=carry_lock),
            max_tokens=_local_max_tokens(spec),
            session_id=session_id,
            on_log=on_log,
        )
        prompt = raw.strip() if isinstance(raw, str) else ""
        why = validate_manga_page_output(prompt, len(spec.get("panels") or [])) if prompt else ""
        if why:                                                  # not committed: the page keeps its old prompt, the raw reply and the reason are kept for review
            if on_log:
                await on_log(f"⚠ Manga Prompts: page {shot.get('shot')} reply refused: {why}; the page keeps its old prompt")
            shot["_fill_rejected"] = {"reason": why, "raw": raw, "kind": "structure"}
            prompt = ""
        if prompt:
            shot["prompt"] = prompt
            shot["_fill_engine"] = FILL_ENGINE_MANGA_VNEXT
            shot["prompt_style"] = "manga_page"
            shot["_fill_session_id"] = session_id
            shot.pop("_fill_failed", None)
            shot.pop("_fill_rejected", None)
            shot.pop("_fill_renderer_ready", None)
            carry_lock = _extract_identity_lock(prompt) or carry_lock        # the next page carries THIS page's lock (unchanged when the lock cannot be read)
            commit = "NEW"
        else:
            shot["_fill_failed"] = True
            if not why:                                  # empty reply / transport failure: no stale rejection from an earlier run, record THIS failure
                shot["_fill_rejected"] = {"reason": "no text returned by the local LLM (empty reply or transport failure)", "raw": "", "kind": "empty"}
            commit = "KEEP_OLD" if str(previous or "").strip() else "EMPTY"
        if on_log:
            await on_log(f"FILL Manga page={shot.get('shot')} commit={commit} prompt_len={len(prompt)}")
        if on_page:
            await on_page(idx + 1, total)
    return pages


# Canonical name — Manga Prompts (legacy: Fill Manga). The legacy entry point above stays for compatibility; run_manga_render_prompt is the same function.
run_manga_render_prompt = run_fill_manga_vnext
