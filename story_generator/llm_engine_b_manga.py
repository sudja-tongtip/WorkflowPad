"""llm_engine_b_manga.py — Page List engine (legacy name: B Manga).

Page List owns the final WHAT and page geometry of each manga page.

Design rule:
    LLM = comic page/panel semantics + page-budget judgment.
    Python = transport + explicit tag structure only.

Story Bible and Beat Sheet are shared with the normal pipeline. This stage receives normal A2
sequential micro-events, groups them into panels/pages, and chooses the final
page count itself.
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

from .constants import LLM_DIR, lang_prompt_name, dialogue_authoring_text, gate_dialogue_fields
from . import llm_overrides as _overrides
from . import manga_grid as _grid
from .comfy_api import comfy_queue, comfy_history

__version__ = "20260925_b_manga_tag_format"

_CODE_FENCE_RE = re.compile(r"^```(?:json|xml)?\s*|```\s*$")
GROK_MARKER = "[Grok API]"
_LLM_TIMEOUT = 120
_GROK_TEMPERATURE = 0.4
_GROK_REASONING_EFFORT = "low"


def _strip_code_fence(raw: str) -> str:
    return _CODE_FENCE_RE.sub("", raw.strip()).strip()


_B_MANGA_SYSTEM_PROMPT = """You are the comic-page storyboard planner in a visual-story pipeline.
A1 already established production truth and A2 already split the story into ordered sequential micro-events.

Your job is to turn those events into the natural final set of pages and panels.
You decide the final page count, which adjacent events share a panel or page, panel count, page rhythm, and panel geometry.
Do not use a formula, fixed panels-per-page rule, or one-event-per-panel rule. Do not pad or duplicate moments.

Each panel desc is one concise natural-prose description of exactly one drawable physical instant: normally 1-2 sentences, about 35-70 words, shorter when the moment is simple. State the visible people, current action/contact, pose and spatial relationship, each visible person's current clothing (or nudity) and physical state, relevant objects and their current state, the immediate sub-area, and any explicit visible reaction or established light needed to preserve the moment. Merge related facts naturally and state each fact once; never repeat a fact to cover another category. Each panel stays self-sufficient for what is true NOW (current clothing and state), but does not re-describe permanent identity (identity_lock), the full location (location_lock), or permanent object lore (object_lock). Mention light only when it is established, needed for continuity, or materially important. Emotion is source-owned: write only visible or explicitly supported reactions, never a feeling the story does not state. For every visible character in every panel, explicitly describe their complete CURRENT outfit, even when unchanged from earlier panels or absent from the current A2 unit. Continue established clothing until an event explicitly changes it. Never restore an outfit that has already changed.
Do not invent missing actions, reactions, transitions, clothing changes, objects, or story meaning.
Do not write camera, shot size, lens, or framing (never "close-up", "close on", "tight shot", "focus on"); Fill Manga owns HOW the panel is shown. desc is storyboard truth, not an image-generation prompt.

A2 micro-events may carry two labelled lines. HAPPENS gives the rhythm and meaning of the event and never a source of invented poses, gestures or objects. DETAILS gives what is visible in the panel: actions, positions, contact, object holders and states, clothing. One panel is one drawable instant: never add a panel only to show unchanged clothing, pose or description, and never invent a reaction or gesture the source does not support. Keep object holders, clothing and contact until an event changes them (an object not mentioned in a later unit has not vanished). Add no precision the source does not state: no hand side, finger, button, target body part, light direction, expression or gesture. Keep the unit's time: an action described as about to happen is shown as not yet happened, simultaneous actions share one panel, and one panel never mixes a state from before an event with the state after it (when several sequential acts are listed, draw one consistent instant). BAD: "he reaches his right hand toward her torso" for a unit that says only "he reaches a hand toward her"; GOOD: "he reaches a hand toward her; it has not touched her yet". Never write emphasis, framing, or what part of the image shows something or how much of the image is used. Never copy the labels HAPPENS: or DETAILS: into a panel desc or a lock.

CHARACTERS is a data sheet with one <charN.*> tag set per person; the tags only label data and are never part of your output. Refer to every person by their <charN.name> in identity_lock and every panel desc.
If a person's <charN.trigger> is filled, write that trigger word exactly as given instead of their name (or alias) as their identity, in identity_lock and every panel desc. For that person write nothing about age, gender, nationality, body build, face, skin or marks; use only their current hair, outfit and state. Ordinary words that merely occur inside a name stay allowed. If the trigger is empty, use the name and describe them from the look tags. Never mention whether a person has a trigger.

Page locks are permanent identity anchors, not panel-presence claims:
- identity_lock: permanent identity traits for people visible somewhere on the page.
- location_lock: canonical location identity for the page.
- object_lock: permanent identity of a persistent object relevant on the page; omit or use an empty string when none is relevant.
Actual panel presence comes only from that panel's desc.

For every panel, region_pct is four comma-separated numbers x,y,width,height in page percentages. Values must stay inside 0..100, width/height must be positive, and the rectangle must remain inside the page.

Return flat tags only — no JSON, no commentary. For page N and panel M:
<pageN.identity_lock>...</pageN.identity_lock>
<pageN.location_lock>...</pageN.location_lock>
<pageN.object_lock></pageN.object_lock>
<pageN.panelM.region_pct>0,0,100,50</pageN.panelM.region_pct>
<pageN.panelM.desc>Mira stands beside the locked door in her blue coat, gripping the folder against her chest while Arun blocks the narrow passage in front of her.</pageN.panelM.desc>

Text tags per panel (empty is valid; keep each panel's text in that panel's own tags, never merged page-wide):
<pageN.panelM.narration></pageN.panelM.narration>
<pageN.panelM.speech_char></pageN.panelM.speech_char>
<pageN.panelM.subtitle></pageN.panelM.subtitle>

identity_lock, location_lock and object_lock are plain text on one line — never nested tags, never <charN.*> tags. Give every page its identity_lock and location_lock (object_lock stays empty when none). Number pages 1..N in reading order and panels 1..M within each page. Every panel needs a region_pct and a non-empty desc.
Close every tag with its own exact name: <page1.panel1.desc> ends with </page1.panel1.desc>. Any text outside the tags is a format failure."""


def _story_material(macro_events: list[dict], micro_beats: list[dict]) -> str:
    by_parent: dict[str, list[dict]] = {}
    for beat in micro_beats or []:
        if isinstance(beat, dict):
            by_parent.setdefault(str(beat.get("parent") or ""), []).append(beat)
    lines: list[str] = []
    for event in macro_events or []:
        if not isinstance(event, dict):
            continue
        eid = str(event.get("id") or "")
        lines.append(f"{eid}: {event.get('content', '')}")
        for beat in by_parent.get(eid, []):
            lines.append(f"  {beat.get('id')}: {beat.get('content', '')}")
    return "\n".join(lines)


def _build_b_manga_user_prompt(
    a1: dict,
    macro_events: list[dict],
    micro_beats: list[dict],
    *,
    dialogue_lang: str,
    desc_lang: str,
    props_desc: str,
    genre: str,
    genre2: str,
    dialogue_style: str = "",
    narration_type: str = "caption",
) -> str:
    parts = [
        f"Output language for page locks and panel descriptions: {lang_prompt_name(desc_lang or 'en')}",
        f"Spoken dialogue language when dialogue appears inside desc: {lang_prompt_name(dialogue_lang or 'en')}",
    ]
    genre_bits = [x for x in (genre, genre2) if (x or "").strip()]
    if genre_bits:
        parts.append("Genre context: " + ", ".join(genre_bits))
    _authoring = dialogue_authoring_text(dialogue_style, dialogue_lang, narration_type, unit="panel")
    if _authoring:
        parts.append(_authoring)
    parts.extend([
        "",
        f"CHARACTERS:\n{a1.get('character_sheet', '')}",
        f"WORLD:\n{a1.get('world_sheet', '')}",
        f"LOCATIONS:\n{a1.get('locations', '')}",
        f"CONTINUITY:\n{a1.get('continuity_notes', '')}",
    ])
    object_ref = (props_desc or "").strip() or str(a1.get("important_objects") or "").strip()
    if object_ref:
        parts.append("CANONICAL OBJECT REFERENCE:\n" + object_ref)
    parts.append("STORY MATERIAL — macro events with sequential A2 micro-events:\n" + _story_material(macro_events, micro_beats))
    parts.append("Choose the natural final page count yourself and return only the required flat page/panel tags.")
    return "\n\n".join(parts)


def _b_manga_max_tokens(micro_beats: list[dict]) -> int:
    material_size = sum(len(str((b or {}).get("content") or "")) for b in (micro_beats or []))
    return max(7000, min(28000, int(material_size * 2.2) + 5000))


# B Manga Local only: the sampler's repeat penalty. 12 macros x 2 rounds (alien2): every macro was accepted at once with both values, but 1.0 was 18-21% faster, wrote 9-13% fewer
# output tokens and needed fewer geometry repairs than the workflow's 1.12 (B Normal: 1.12 cut the eight blocks of later shots short). Applied to the workflow COPY below only: the
# workflow file and every other stage keep their own value.
_B_MANGA_LOCAL_REPEAT_PENALTY = 1.0


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
        used = _overrides.apply(inputs, repeat_penalty=_B_MANGA_LOCAL_REPEAT_PENALTY)    # Program mode: 1.0; Workflow mode: the workflow's own value is kept
        hit = True
    if not hit:
        if on_log:
            await on_log("❌ Page List: workflow has no LLMSessionChatNode")
        return None

    if on_log:
        await on_log(_overrides.describe(used))
        await on_log(f"📋 MANGA SHOT PLANNING SYSTEM:\n{system_prompt}")
        await on_log(f"📤 MANGA SHOT PLANNING USER:\n{user_prompt}")

    loop = asyncio.get_event_loop()
    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, uuid.uuid4().hex)
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Page List queue: {exc}")
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
                        await on_log(f"📥 MANGA SHOT PLANNING RAW:\n{text}")
                    return text
        status = ((entry.get("status") or {}).get("status_str") or "").lower()
        if status in ("success", "error"):
            break
    if on_log:
        await on_log("❌ Page List: local LLM returned no text")
    return None


async def _call_grok_text(
    system_prompt: str,
    user_prompt: str,
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    on_log=None,
) -> str | None:
    if not api_key:
        if on_log:
            await on_log("❌ Page List: Grok API key is empty")
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
        await on_log(f"📋 MANGA SHOT PLANNING SYSTEM:\n{system_prompt}")
        await on_log(f"📤 MANGA SHOT PLANNING USER:\n{user_prompt}")

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
                    await on_log(f"❌ Page List Grok retry: {retry_exc}")
                return None
        else:
            if on_log:
                await on_log(f"❌ Page List Grok HTTP {exc.code}: {body[:500]}")
            return None
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Page List Grok: {exc}")
        return None

    choices = response.get("choices") if isinstance(response, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else {}
    message = first.get("message") if isinstance(first, dict) else {}
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str):
        if on_log:
            await on_log("❌ Page List: Grok response has no text content")
        return None
    if on_log:
        await on_log(f"📥 MANGA SHOT PLANNING RAW:\n{text}")
    return text


def _region_pct_valid(value) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    nums: list[float] = []
    for n in value:
        if isinstance(n, bool) or not isinstance(n, (int, float)):
            return False
        nums.append(float(n))
    x, y, w, h = nums
    return x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= 100 and y + h <= 100


# Flat-tag transport (same idea as A2/B): <page1.identity_lock>…</page1.identity_lock>,
# <page1.panel2.desc>…</page1.panel2.desc>. Matched by exact tag name with a regex, so quotes, "<",
# ">" and newlines inside the text are harmless and an unfinished trailing tag simply doesn't match.
# The closing tag may be the full name or just the bare field name (</desc>).
_PAGE_PAIR_RE = re.compile(r"<(page(\d+)\.(?:panel(\d+)\.)?([a-z_]+))>(.*?)</(?:\1|[\w.]*?\.\4|\4)>", re.DOTALL)
_PAGE_OPEN_RE = re.compile(r"<page\d+\.(?:panel\d+\.)?[a-z_]+>")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_region(text: str) -> list | None:
    """'x,y,w,h' (brackets/spaces tolerated) -> four numbers, or None. Structure only."""
    nums = _NUMBER_RE.findall(text or "")
    if len(nums) != 4:
        return None
    out = []
    for n in nums:
        f = float(n)
        out.append(int(f) if f == int(f) else f)
    return out


def _extract_page_fields(raw: str) -> dict[int, dict]:
    pages: dict[int, dict] = {}
    for m in _PAGE_PAIR_RE.finditer(raw):
        page = pages.setdefault(int(m.group(2)), {"fields": {}, "panels": {}})
        value = m.group(5).strip()
        if m.group(3) is None:
            page["fields"].setdefault(m.group(4), value)
        else:
            page["panels"].setdefault(int(m.group(3)), {}).setdefault(m.group(4), value)
    return pages


_FALLBACK_REGION = [0, 0, 100, 100]


def _fit_region(text: str) -> list | None:
    """Layout repair, geometry only: four numbers whose rectangle sticks out of the page (e.g.
    y=50,h=100) are clamped back inside it. None when there are not four usable numbers."""
    nums = _parse_region(text)
    if nums is None:
        return None
    x, y, w, h = (float(n) for n in nums)
    x, y = min(max(x, 0.0), 99.0), min(max(y, 0.0), 99.0)
    w, h = min(w, 100.0 - x), min(h, 100.0 - y)
    if w <= 0 or h <= 0:
        return None
    return [int(v) if v == int(v) else v for v in (x, y, w, h)]


# ── page-level geometry ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
# The documented region_pct is x,y,width,height. Gemma often writes corners x1,y1,x2,y2 (e.g. "0,50,100,100" = from y 50 down to y 100), which read as x,y,w,h puts the panel outside the
# page, and the per-panel clamp of _fit_region then stretches or keeps panels so that neighbours overlap. The two readings are tested for the WHOLE page, by numbers only: one consistent
# reading per page, never a mixture, no grid, no panel moved, added or reordered. Touching edges are not an overlap.
_OVERLAP_EPS = 1e-9


def _rect_overlap_area(a: list, b: list) -> float:
    """Intersection area of two [x,y,w,h] rectangles in % of the page area; 0 when they only touch."""
    ix = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    iy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return round(ix * iy / 100.0, 4) if ix > _OVERLAP_EPS and iy > _OVERLAP_EPS else 0.0


def _page_overlaps(rects: list) -> list[tuple[int, int, float]]:
    """[(panel i, panel j, % of page)] for every overlapping pair (1-based, reading order); rectangles that are None are skipped."""
    out = []
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            if rects[i] is not None and rects[j] is not None:
                a = _rect_overlap_area(rects[i], rects[j])
                if a > 0:
                    out.append((i + 1, j + 1, a))
    return out


def _corners_to_xywh(nums: list | None) -> list | None:
    if nums is None or len(nums) != 4:
        return None
    x1, y1, x2, y2 = (float(n) for n in nums)
    return [int(v) if v == int(v) else v for v in (x1, y1, x2 - x1, y2 - y1)]


def _geometry_clean(rects: list) -> bool:
    """Every rectangle inside the page with positive size, and no two overlap."""
    return bool(rects) and all(r is not None and _region_pct_valid(r) for r in rects) and not _page_overlaps(rects)


def _resolve_page_geometry(region_texts: list[str]) -> dict:
    """Read all panels of ONE page the same way. Returns {mode, rects, raw_xywh, corner, ambiguous, reason, raw_overlap}:
    mode 'xywh'      documented reading is clean -> rects = the numbers as written (nothing changes);
    mode 'corners'   the documented reading is not clean but (x1,y1,x2,y2) is -> rects = converted rectangles of the whole page;
    mode 'legacy'    neither is clean (or a region is missing/unreadable): rects None, the caller keeps the per-panel repair; ambiguous is True for a page with more than one panel."""
    raw_xywh = [_parse_region(t) for t in region_texts]
    corner = [_corners_to_xywh(n) for n in raw_xywh]
    info = {"raw_xywh": raw_xywh, "corner": corner, "raw_overlap": _page_overlaps([r for r in raw_xywh]), "rects": None, "mode": "legacy", "ambiguous": False, "reason": ""}
    if not raw_xywh:
        info["reason"] = "no panels"
        return info
    if any(n is None for n in raw_xywh):
        info["reason"] = "a region is missing or has no four numbers"
        info["ambiguous"] = len(raw_xywh) > 1
        return info
    if _geometry_clean(raw_xywh):
        info.update(mode="xywh", rects=raw_xywh, reason="x,y,width,height is clean for the whole page")
    elif _geometry_clean(corner):
        info.update(mode="corners", rects=corner, reason="x,y,width,height is not clean (out of the page or overlapping) but x1,y1,x2,y2 is")
    else:
        info["reason"] = "neither x,y,width,height nor x1,y1,x2,y2 gives a clean page"
        info["ambiguous"] = len(raw_xywh) > 1
    return info


def _geometry_notes(info: dict, label: str) -> list[tuple[str, str]]:
    """[(level, message)] for the log: page-level conversions (corner reading) and ambiguous pages. Level 'info' or 'warn'."""
    notes: list[tuple[str, str]] = []
    conv = [g["page"] for g in info.get("geometry", []) if g.get("mode") == "corners"]
    amb = [g["page"] for g in info.get("geometry", []) if g.get("ambiguous")]
    if conv:
        notes.append(("info", f"{label}: page(s) {conv} wrote region_pct as corners (x1,y1,x2,y2); read as one consistent layout per page and converted to x,y,width,height "
                              f"({info.get('regions_converted', 0)} region(s) changed)"))
    if amb:
        notes.append(("warn", f"{label}: geometry of page(s) {amb} is ambiguous (neither x,y,width,height nor x1,y1,x2,y2 is clean); the per-panel repair was used and panels may still overlap"))
    return notes


def _parse_pages_tags(raw: str | None) -> tuple[list[dict] | None, dict]:
    """Structure only; keeps every panel that has story content. Returns (pages, info).

    A panel is kept when it has a non-empty desc; its region_pct is clamped into the page when it
    sticks out, or set to the full page when it is missing/unreadable (content matters more than
    layout, and Fill Manga only describes placement in words). A page is kept when it has at
    least one such panel; a missing identity/location lock becomes an empty string. Pages and panels
    are renumbered 1..N in the order given. Model-authored semantic strings are never rewritten.
    info: complete (nothing dropped/repaired and no unclosed tag), regions_repaired, dropped_panels,
    dropped_pages.

    Geometry is resolved per PAGE first (_resolve_page_geometry): a page written as x,y,width,height stays as written; a page written as corners x1,y1,x2,y2 is converted as a whole; only when
    neither reading is clean does the per-panel repair above apply. regions_repaired counts every panel whose region changed (conversion or clamp); regions_converted / regions_clamped
    separate the two, geometry holds one diagnostic record per kept page and geometry_ambiguous_pages counts the pages left to the legacy repair. Conversions never drop content, so they
    cannot cause a retry."""
    info = {"complete": False, "regions_repaired": 0, "regions_converted": 0, "regions_clamped": 0, "dropped_panels": 0, "dropped_pages": 0,
            "geometry": [], "geometry_ambiguous_pages": 0}
    if not isinstance(raw, str) or not raw.strip():
        return None, info
    text = _strip_code_fence(raw)
    fields = _extract_page_fields(text)
    unclosed = len(_PAGE_OPEN_RE.findall(text)) != len(_PAGE_PAIR_RE.findall(text))

    out: list[dict] = []
    for page_key in sorted(fields):
        page = fields[page_key]
        kept: list[dict] = []
        for panel_key in sorted(page["panels"]):
            raw_panel = page["panels"][panel_key]
            if not raw_panel.get("desc", "").strip():
                info["dropped_panels"] += 1
                continue
            kept.append(raw_panel)
        geo = _resolve_page_geometry([rp.get("region_pct", "") for rp in kept])
        panels: list[dict] = []
        changed = 0
        for idx, raw_panel in enumerate(kept):
            desc = raw_panel.get("desc", "")
            region_text = raw_panel.get("region_pct", "")
            if geo["rects"] is not None:                      # the whole page was read one consistent way
                region = geo["rects"][idx]
                if region != geo["raw_xywh"][idx]:
                    info["regions_converted"] += 1
                    info["regions_repaired"] += 1
                    changed += 1
            else:                                             # legacy per-panel repair (unchanged behaviour)
                region = _parse_region(region_text)
                if region is None or not _region_pct_valid(region):
                    fitted = _fit_region(region_text)
                    region = fitted if fitted is not None else list(_FALLBACK_REGION)
                    info["regions_clamped"] += 1
                    info["regions_repaired"] += 1
                    changed += 1
            panels.append({
                "panel": len(panels) + 1, "region_pct": region, "desc": desc,
                # canonical per-panel text — panel-local by construction, "" when absent
                "narration": raw_panel.get("narration", ""),
                "speech_char": raw_panel.get("speech_char", ""),
                "subtitle": raw_panel.get("subtitle", ""),
            })
        if not panels:
            info["dropped_pages"] += 1
            continue
        final = [p["region_pct"] for p in panels]
        info["geometry"].append({"page": len(out) + 1, "panels": len(panels), "mode": geo["mode"], "ambiguous": geo["ambiguous"], "reason": geo["reason"], "raw_xywh": geo["raw_xywh"],
                                 "corner": geo["corner"], "final": final, "regions_changed": changed, "raw_overlap": geo["raw_overlap"], "final_overlap": _page_overlaps(final)})
        if geo["ambiguous"]:
            info["geometry_ambiguous_pages"] += 1
        grid = _grid.template_regions(len(panels))        # 2..5 panels: the page grid comes from manga_grid (the model only writes stacked bands the image model cannot draw); content untouched
        if grid:
            for _p, _r in zip(panels, grid):
                _p["region_pct"] = _r
            info["grid_template_pages"] = info.get("grid_template_pages", 0) + 1
        out.append({
            "page": len(out) + 1,
            "identity_lock": page["fields"].get("identity_lock", ""),
            "location_lock": page["fields"].get("location_lock", ""),
            "object_lock": page["fields"].get("object_lock", ""),
            "panels": panels,
        })
    info["unclosed"] = unclosed
    info["complete"] = bool(out) and not unclosed and not (
        info["regions_repaired"] or info["dropped_panels"] or info["dropped_pages"])
    return (out or None), info


async def _run_b_manga_local_per_macro(
    a1: dict, macro_events: list[dict], micro_beats: list[dict], wf_template: dict, llm_wf_file: str,
    on_err, on_log, *, dialogue_lang: str, genre: str, genre2: str, desc_lang: str, props_desc: str,
    dialogue_style: str, narration_type: str, chat_capture: dict | None,
) -> list[dict] | None:
    """Local Manga: one macro event (with only its A2 micro-events) per sequential call. Each call
    chooses that macro's own page count/panels; pages never span two macros. Python concatenates in
    macro order and renumbers pages globally — model-authored page/panel content is left as written."""
    from .llm_engine_b import group_micro_by_macro
    groups = group_micro_by_macro(macro_events, micro_beats)
    if not groups:
        await on_err("Page List: Beat Sheet has no micro-events under any macro event")
        return None
    if on_log:
        await on_log("Page List LOCAL per-macro: " + ", ".join(f"{m.get('id')} (Beat Sheet {len(g)})" for m, g in groups))

    all_pages: list[dict] = []
    sessions: list[dict] = []
    all_turns: list[dict] = []
    session_id = ""
    regions_repaired = 0
    geometry_reports: list[dict] = []
    for macro, micros in groups:
        mid = str(macro.get("id") or "")
        base_prompt = _build_b_manga_user_prompt(
            a1, [macro], micros,
            dialogue_lang=dialogue_lang, desc_lang=desc_lang, props_desc=props_desc,
            genre=genre, genre2=genre2, dialogue_style=dialogue_style, narration_type=narration_type,
        ) + (f"\n\nThis call covers ONLY macro event {mid}. Plan the pages for this event alone: every "
             "page and panel contains this event's story material only, never material from any other event.")
        max_tokens = _b_manga_max_tokens(micros)
        session_id = uuid.uuid4().hex[:8]          # fresh conversation per macro
        turns: list[dict] = []
        pages = None
        info: dict = {}
        for attempt in (1, 2, 3):
            sent = base_prompt if attempt == 1 else base_prompt + (
                "\n\nThe previous response had unclosed or mismatched tags. Return the complete set of flat tags only, no JSON: "
                "<pageN.identity_lock>, <pageN.location_lock>, <pageN.object_lock> (plain text, no nested tags), and for every panel "
                "<pageN.panelM.region_pct> (four comma-separated numbers) and <pageN.panelM.desc>. "
                "Close EVERY tag with its own exact name, e.g. <page1.panel2.desc>...</page1.panel2.desc> — never repeat 'panelM' in a closing tag."
            )
            raw = await _call_local_text(wf_template or {}, _B_MANGA_SYSTEM_PROMPT, sent,
                                         max_tokens=max_tokens, session_id=session_id, on_log=on_log)
            turns.append({"macro": mid, "system_prompt": _B_MANGA_SYSTEM_PROMPT, "user_prompt": sent,
                          "raw_response": raw or ""})
            pages, info = _parse_pages_tags(raw)
            # Accept only a response that lost no content: a dropped panel/page or an unclosed
            # (cut-off) tag retries THIS macro. A geometry-only region fix is transport repair.
            lost = bool(info.get("dropped_panels") or info.get("dropped_pages") or info.get("unclosed"))
            if pages is not None and not lost:
                if on_log:
                    for _lvl, _msg in _geometry_notes(info, f"Page List {mid}"):
                        await on_log(("ℹ " if _lvl == "info" else "⚠ ") + _msg)
                    if info["regions_clamped"]:
                        await on_log(f"⚠ Page List {mid}: {info['regions_clamped']} panel region(s) fixed to fit the page")
                geometry_reports.extend({"macro": mid, **_g} for _g in info.get("geometry", []))
                break
            if on_log:
                await on_log(f"⚠ Page List {mid} attempt {attempt}: " + (
                    "no valid pages in the tags" if pages is None else
                    f"content was dropped or cut off ({info['dropped_panels']} panel(s), "
                    f"{info['dropped_pages']} page(s), unclosed tag: {bool(info.get('unclosed'))})"))
            pages = None
        sessions.append({"macro": mid, "session_id": session_id, "turns": turns})
        all_turns.extend(turns)
        if pages is None:
            if chat_capture is not None:
                chat_capture.update({"turns": all_turns, "sessions": sessions, "backend": "local",
                                     "llm_wf_file": llm_wf_file, "session_id": session_id})
            await on_err(f"Page List: macro {mid} did not return valid page tags after 3 attempts")
            return None
        regions_repaired += info.get("regions_repaired", 0)
        for page in pages:                          # structure only: local 1..n -> global numbering
            page["page"] = len(all_pages) + 1
            for _p in page.get("panels") or []:
                gate_dialogue_fields(_p, dialogue_style)   # drop text channels this Dialogue Style does not use
            all_pages.append(page)

    if chat_capture is not None:
        chat_capture.update({"turns": all_turns, "sessions": sessions, "backend": "local",
                             "llm_wf_file": llm_wf_file, "session_id": session_id, "geometry": geometry_reports})
        if regions_repaired:                        # geometry-only repair: surface it like the whole-story path
            chat_capture["salvage"] = {
                "pages": len(all_pages), "panels": sum(len(p["panels"]) for p in all_pages),
                "regions_repaired": regions_repaired, "dropped": 0,
            }
    return all_pages


async def run_b_manga_vnext(
    a1: dict,
    macro_events: list[dict],
    micro_beats: list[dict],
    llm_wf_file: str,
    on_err,
    on_log=None,
    dialogue_lang: str = "en",
    genre: str = "",
    genre2: str = "",
    desc_lang: str = "en",
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    chat_capture: dict | None = None,
    props_desc: str = "",
    dialogue_style: str = "",
    narration_type: str = "caption",
) -> list[dict] | None:
    """Shared A1/A2 -> Manga pages. B Manga chooses the final page count."""
    valid_micro = [m for m in (micro_beats or []) if isinstance(m, dict) and str(m.get("id") or "").strip()]
    if not valid_micro:
        await on_err("Page List: Beat Sheet has no sequential micro-events")
        return None

    user_prompt = _build_b_manga_user_prompt(
        a1, macro_events, valid_micro,
        dialogue_lang=dialogue_lang, desc_lang=desc_lang,
        props_desc=props_desc, genre=genre, genre2=genre2,
        dialogue_style=dialogue_style, narration_type=narration_type,
    )
    max_tokens = _b_manga_max_tokens(valid_micro)
    session_id = uuid.uuid4().hex[:8]

    wf_template = None
    if llm_wf_file != GROK_MARKER:
        if not (llm_wf_file or "").strip():
            await on_err("Page List: no local LLM workflow selected")
            return None
        wf_path = LLM_DIR / f"{llm_wf_file}.json"
        if not wf_path.exists():
            await on_err(f"ไม่พบ {wf_path}")
            return None
        try:
            wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
        except Exception as exc:
            await on_err(f"Page List workflow read failed: {exc}")
            return None

    if llm_wf_file != GROK_MARKER:
        # LOCAL: one macro event per sequential call (Grok keeps the whole-story call below)
        return await _run_b_manga_local_per_macro(
            a1, macro_events, valid_micro, wf_template or {}, llm_wf_file, on_err, on_log,
            dialogue_lang=dialogue_lang, genre=genre, genre2=genre2, desc_lang=desc_lang,
            props_desc=props_desc, dialogue_style=dialogue_style, narration_type=narration_type,
            chat_capture=chat_capture)

    turns: list[dict] = []
    for attempt in (1, 2):
        sent = user_prompt if attempt == 1 else user_prompt + (
            "\n\nThe previous response could not be read. Return the complete set of flat tags only, no JSON: "
            "<pageN.identity_lock>, <pageN.location_lock>, <pageN.object_lock>, and for every panel "
            "<pageN.panelM.region_pct> (four comma-separated numbers) and <pageN.panelM.desc>. "
            "Number pages and panels from 1 and close every tag with its own exact name."
        )
        if llm_wf_file == GROK_MARKER:
            raw = await _call_grok_text(
                _B_MANGA_SYSTEM_PROMPT, sent,
                api_key=grok_api_key, model=grok_model,
                max_tokens=max_tokens, on_log=on_log,
            )
        else:
            raw = await _call_local_text(
                wf_template or {}, _B_MANGA_SYSTEM_PROMPT, sent,
                max_tokens=max_tokens, session_id=session_id, on_log=on_log,
            )
        turns.append({"system_prompt": _B_MANGA_SYSTEM_PROMPT, "user_prompt": sent, "raw_response": raw or ""})
        pages, info = _parse_pages_tags(raw)
        if pages is not None:
            if on_log:
                for _lvl, _msg in _geometry_notes(info, "Page List"):
                    await on_log(("ℹ " if _lvl == "info" else "⚠ ") + _msg)
            if not info["complete"] and on_log:
                await on_log(
                    f"⚠ Page List: response needed repair — kept {len(pages)} page(s), "
                    f"{info['regions_repaired']} region(s) fixed, {info['dropped_panels']} panel(s) "
                    f"and {info['dropped_pages']} page(s) dropped")
            if chat_capture is not None:
                if not info["complete"]:
                    chat_capture["salvage"] = {
                        "pages": len(pages),
                        "panels": sum(len(p["panels"]) for p in pages),
                        "regions_repaired": info["regions_repaired"],
                        "dropped": info["dropped_panels"] + info["dropped_pages"],
                    }
                chat_capture["turns"] = turns
                chat_capture["backend"] = "grok" if llm_wf_file == GROK_MARKER else "local"
                chat_capture["llm_wf_file"] = llm_wf_file
                chat_capture["session_id"] = session_id if llm_wf_file != GROK_MARKER else ""
                chat_capture["grok_model"] = grok_model
                chat_capture["geometry"] = info.get("geometry", [])
            for _pg in pages:
                for _p in _pg.get("panels") or []:
                    gate_dialogue_fields(_p, dialogue_style)
            return pages
        if on_log:
            await on_log(f"⚠ Page List attempt {attempt}: no valid pages in the tags")

    if chat_capture is not None:
        chat_capture["turns"] = turns
        chat_capture["backend"] = "grok" if llm_wf_file == GROK_MARKER else "local"
        chat_capture["llm_wf_file"] = llm_wf_file
        chat_capture["session_id"] = session_id if llm_wf_file != GROK_MARKER else ""
        chat_capture["grok_model"] = grok_model
    await on_err("Page List: LLM did not return the required page tags after 2 attempts")
    return None


def manga_vnext_page_to_legacy_dict(shot: dict) -> dict:
    """Display adapter only. Canonical Manga truth remains _manga_page_spec."""
    spec = shot.get("_manga_page_spec") or {}
    panels = spec.get("panels") or []
    scene_lines = [f"P{p.get('panel')}: {p.get('desc') or p.get('beat', '')}" for p in panels]
    identity_lock = str(spec.get("identity_lock") or "").strip()
    if identity_lock:
        people_all = identity_lock
    else:
        people_all = ", ".join(sorted({
            pp.strip() for p in panels
            for pp in str(p.get("visible_people") or "").split(",") if pp.strip()
        }))
    n = shot.get("shot")
    return {
        "shot": n,
        "uses": list(spec.get("uses") or []),
        "scene_desc": "\n".join(scene_lines),
        "mood": "",
        "expression": "",
        "clothing_state": "",
        "prop_state": "",
        "place": spec.get("location_lock", ""),
        "env": "",
        "spacing": "",
        "covers": f"MP{n}",
        "visible_people": people_all,
        "time_of_day": "",
        "light_sources": "",
        "narration": "; ".join(p.get("narration", "") for p in panels if p.get("narration")),
        "speech_char": "",
        "speech_text": "; ".join(p.get("subtitle", "") for p in panels if p.get("subtitle")),
        "shot_scale": "",
        "moment": "",
        "world_interaction": "",
        "prompt": shot.get("prompt", ""),
        "_manga_page_spec": spec,
        "_fill_engine": shot.get("_fill_engine", ""),
        "prompt_style": shot.get("prompt_style", ""),
    }


# Canonical name — Page List (legacy: B Manga). The legacy entry point above stays for compatibility; run_manga_shot_planning is the same function.
run_manga_shot_planning = run_b_manga_vnext
