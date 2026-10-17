"""
llm_engine_b.py — Shot List engine (legacy name: B).

Shot List owns the final WHAT of each still image.

Design rule:
    LLM = semantic shot planning.
    Python = transport + explicit JSON structure only.

Shot List turns the Story Bible and Beat Sheet into exactly N shot truths. It
owns WHAT is in each still image. Camera/composition belong to Image Prompts.
"""

__version__ = "20260924_b_tag_format_a1"

import asyncio
import copy
import json
import re
import time
import urllib.error
import urllib.request
import uuid

from .constants import LLM_DIR, lang_prompt_name, dialogue_authoring_text, gate_dialogue_fields
from .comfy_api import comfy_queue, comfy_history


_CODE_FENCE_RE = re.compile(r"^```(?:json|xml)?\s*|```\s*$")


def _strip_code_fence(raw: str) -> str:
    """Transport-only cleanup: some models wrap output in ```/```json/```xml fences. Strip fence
    markers only — never touch the content itself."""
    return _CODE_FENCE_RE.sub("", raw.strip()).strip()


# B-TAG-FORMAT-A1: shots are transported as one flat tag per field, <shotN.field>value</shotN.field>
# — no JSON, no quote-escaping, multi-line content allowed. Each field is its own tag with no
# nesting, so there is nothing to bracket-balance. Parsed by exact string match on the tag name via
# this regex, never a real XML parser, so a literal <, >, &, or quote inside a value is harmless.
_TAG_RE = re.compile(r"<([A-Za-z0-9_.]+)>(.*?)</\1>", re.DOTALL)
_SHOT_TAG_RE = re.compile(r"^shot(\d+)\.([a-z_]+)$")
# The closing tag may be the full name (</shot1.narration>), just the bare field name
# (</narration>) — models drop the "shotN." prefix on close often enough that requiring an exact
# match rejected otherwise complete responses — or, less often, the WRONG shot number
# (</shot1.uses> closing a <shot3.uses> opener). The opening tag's own number (group 2) is what
# _extract_shot_fields groups by, never the closer's, so tolerating a mismatched number here loses
# nothing and stops a single typo from silently discarding an otherwise complete shot.
_SHOT_PAIR_RE = re.compile(r"<(shot(\d+)\.([a-z_]+))>(.*?)</(?:\1|[\w.]*?\.\3|\3)>", re.DOTALL)


_SHOT_OPEN_TAG_RE = re.compile(r"<shot(\d+)\.([a-z_]+)>")


def _extract_shot_fields(raw: str) -> dict[int, dict[str, str]]:
    """Extract every <shotN.field>value pair, grouped by shot number, in the order the opening
    tags appear. Scans by OPENING tag only: a field's value runs to its own closing tag (exact,
    wrong shot number, or bare field name — models are inconsistent) when that appears before the
    next field opens, otherwise to wherever the next <shotN.field> tag starts. This tolerates a
    field whose closing tag is missing entirely (a model habit seen on shot_desc) without losing
    the content or bleeding it into the next field. Naturally truncation-tolerant: a genuinely
    incomplete trailing tag with nothing after it simply extracts to end-of-string, which
    downstream validation (e.g. an unclosed quote/parenthesis) is not responsible for catching."""
    shots: dict[int, dict[str, str]] = {}
    opens = list(_SHOT_OPEN_TAG_RE.finditer(raw))
    for i, m in enumerate(opens):
        num, field = int(m.group(1)), m.group(2)
        start = m.end()
        next_open_pos = opens[i + 1].start() if i + 1 < len(opens) else len(raw)
        # Only look for a proper closer BEFORE the next field opens — searching further afield
        # risks matching a same-named field's closer that belongs to a different, later shot.
        close_m = re.search(rf"</(?:shot{num}\.{field}|shot\d+\.{field}|{field})>", raw[start:next_open_pos])
        content = raw[start:start + close_m.start()] if close_m else raw[start:next_open_pos]
        shots.setdefault(num, {})[field] = content.strip()
    return shots


GROK_MARKER = "[Grok API]"
_LLM_TIMEOUT = 120
_GROK_TEMPERATURE = 0.4
_GROK_REASONING_EFFORT = "low"
# ComfyUI's LLMSessionChatNode declares max_tokens as an INT input with a hard schema max of
# 32768 — sending more makes ComfyUI reject the whole queued prompt (HTTP 400) before any node
# runs. Grok's API has no such fixed ceiling.
_LOCAL_MAX_TOKENS_CEILING = 32768


_B_SYSTEM_PROMPT = r"""
ROLE:
Storyboard Planner converting A1/A2 into precise self-contained still-image shot truths.

TASK:
Create exactly the requested number of unique shots.
Each shot must carry enough current visual truth for Fill to work without rereading A1/A2.

CORE RULES

1. Preserve story truth exactly: characters, actions, reactions, clothing, physical states, objects, locations, relationships, contact, chronology, and outcome.
Do not invent, soften, intensify, repair, or reinterpret meaning.

2. Each shot is one complete frozen drawable moment, meaningfully distinct from the other shots.

3. No camera, framing, lens, angle, shot scale, or composition unless explicitly requested.
B defines WHAT exists. Fill decides HOW to photograph it.

4. A1 is the production-design authority. A2 is the event/chronology authority. Do not contradict either.

5. Describe the CURRENT state only.
Carry forward clothing changes, restraint, injury, dirt, wetness, damage, object state, and other persistent visual facts until the story changes them.
Facts already established as the character's current state remain current unless the CURRENT macro/micro event explicitly changes, ends, reverses, or contradicts them. Do not silently reset an established physical, clothing, object, or location state merely because the current beat focuses on something else. When PRECEDING SHOT context is supplied: it is continuity evidence only — the CURRENT A2 event remains the authority for what changes now, and the absence of a repeated state in the CURRENT A2 event is NOT by itself evidence that the state ended. Before finalizing each shot, semantically reconcile Body, State, Appearance, Interaction, and objects so they describe one mutually consistent current moment — within one shot they must not contradict each other about the same current fact (e.g. one field still wearing a garment another field already removed). Do not copy the preceding shot blindly, and do not carry forward a temporary action or gesture merely because it existed previously.

6. visible_people lists every visible person and no off-screen characters.

7. objects lists only story-relevant objects actually present in the shot.

8. Use concrete current clothing. Never use placeholders such as "casual clothes", "unspecified", "same clothes", or "appropriate outfit".

9. Describe only what can exist visibly in one still frame.
Do not invent story action, and do not invent gestures or expressions to explain invisible story meaning.

10. Keep shot_desc concise, concrete, and technical.
When an event depends on a boundary, opening, container, or enclosure, keep the explicit part-by-part spatial relation in Body/Interaction: what is inside, outside, through, crossing, or adjacent. Do not collapse it into a generic "inside X".

A2 MICRO-EVENT FORMAT

An A2 unit may carry two labelled lines. HAPPENS is narrative meaning / event context: use it to understand the event and any emotion the source supports; never use it to invent poses, gestures or objects. DETAILS is concrete physical / visual evidence: it decides Beat (the action now happening), Body (pose and position), Interaction (contact and object use), State (what persists or has changed) and the Appearance / Setting / Light facts relevant to that moment. A1 stays the production-design authority and A2 the chronology / event authority. If DETAILS in a later unit does not mention an object, a clothing state or a contact, do not conclude it disappeared or was put down: keep the last established state until an event changes it. Add no precision the A2 unit and A1 do not state: no hand side, finger, button, target body part, turn of the body, light direction, furniture position, expression, emotion or gesture. Keep the unit's time: an action described as about to happen is shown as not yet happened (no contact yet), actions the unit joins as simultaneous stay together in one shot, and a state from before and a state from after the event are never mixed. BAD: "his right arm stretches toward her shoulder" for a unit that says only "he reaches a hand toward her"; GOOD: "he reaches a hand toward her; it has not touched her yet" (no side, no target). Emotion is only a reaction the source states; otherwise write only that no expression is stated. Never copy the labels HAPPENS: or DETAILS: into shot_desc or story.

SHOT_DESC

Use exactly these eight blocks in this order:

(Beat: ...) (Emotion: ...) (Body: ...) (Appearance: ...) (Setting: ...) (Interaction: ...) (State: ...) (Light: ...)

Each fact has one primary owning block. Do not use one block to do another block's job.

Beat = the single current visible event/action of this frozen moment ("what is happening now?"). A genuinely static moment (resting, waiting, standing, watching) is valid; never exaggerate an action to fill it. Never composition, character arrangement, positioning, visual emphasis, shot purpose, mood, or any editorial label.
BAD: (Beat: main character arrangement) / (Beat: emotional climax)
GOOD: (Beat: Mira opens the door while Arun blocks the passage.)

Emotion = only gaze, facial expression, and visible reaction explicitly supported by the story. Never abstract mood, atmosphere, body geometry, invented emotion, or narrative meaning. If the story gives no visible reaction, keep it minimal and factual; do not invent fear, joy, tension, or relief to fill it.
BAD: (Emotion: overwhelming happiness and complete relaxation.)
GOOD: (Emotion: Mira smiles faintly and her tense gaze softens.)

Body = physical geometry only: pose, direction, spacing, limb placement, blocking, contact geometry, and story-relevant inside/outside/through spatial relations (which body parts are on which side of a boundary or opening). Never emotion, composition importance, camera/framing, clothing, or story interpretation.
BAD: (Body: Mira stands prominently in the center and appears the happiest.)
GOOD: (Body: Mira stands between Arun and the doorway, her torso turned toward Arun while one hand remains on the door.)

Appearance = current clothing, hair appearance, accessories, and visible exterior look (dirt or wetness only as outward appearance). Never pose, interaction, emotion, narrative meaning, or composition.

Setting = where this moment occurs: current physical location, relevant sub-area, and visible environmental facts (surfaces, architecture, terrain, weather) actually present. Never what characters do, interaction, emotion, composition, or camera language.

Interaction = the current direct engagement between person and person, object, surface, or environment (touching, gripping, leaning, using an object, facing or confronting when story-relevant, blocking a passage, keeping distance). Contact is not required. Never merely restate Setting.
BAD: (Interaction: Everyone is surrounded by the wide field.)
GOOD: (Interaction: Mira grips Arun's wrist while Arun braces his other hand against the door.)
GOOD without contact: (Interaction: Mira faces Arun across the open doorway without touching him.)

State = persistent physical/continuity conditions that remain true now (injury, restraint, wetness, dirt, exhaustion, damage, pregnancy, transformed condition, displaced or removed clothing, persistent object/body condition). WHAT remains true, never WHY. Never current action, transient pose, emotion, mood, interpretation, history, or cause.
BAD: (State: Mira is soaked because of the earlier struggle and feels relieved.)
GOOD: (State: Mira's hair and clothes remain soaked.)

Light = current established visible illumination. If a physical source is established, state it; otherwise describe only visible light quality and behavior without inventing a source (no sunlight, moonlight, golden hour, neon, or rim light unless established). No mood or cinematic styling.
BAD: (Light: dramatic golden cinematic light symbolizing hope.)
GOOD: (Light: Diffuse daylight enters through the open doorway and falls softly across the room.)

FIELD OWNERSHIP RULE: Beat owns the event; Emotion the visible reaction; Body the physical geometry; Appearance the current look/clothing; Setting the place; Interaction the current engagement/contact; State the persistent condition; Light the illumination. Do not repeat one fact across blocks merely to fill them (e.g. a soaked character: Appearance may show wet hair and clothes, State says the character remains soaked — not the same sentence twice). If a fact touches several concepts, put its primary truth in the owning block and let the others describe only their own aspect.
Composition language is forbidden in every block: centered, prominently centered, visual focus, dominant in frame, foreground emphasis, balanced composition, main focal point, arranged symmetrically — unless it is world-space story truth rather than image composition (GOOD Body: "Mira stands physically between Arun and the door."; BAD: "Mira is centered as the dominant focal point.").

STORY

<shotN.story> is one concise poetic narrative passage fusing the eight shot_desc blocks.
Use vivid natural literary prose while preserving the same shot truth.
Every person, action, expression, state, object, clothing detail, and setting detail in Story must already exist in the eight blocks of the same shot.
Poetic wording is allowed. New facts are not.
Story may rephrase the eight blocks naturally but must not reintroduce facts the ownership rules rejected: no composition, abstract emotional interpretation, invented cause, invented light, or camera language.
Story must NOT appear inside shot_desc.

OUTPUT

Return exactly eight tags per shot, in this shape (shot1 shown; shot2, shot3... repeat it with their own number):

<shot1.uses>E1.1</shot1.uses>
<shot1.visible_people>...</shot1.visible_people>
<shot1.shot_desc>
(Beat: ...)
(Emotion: ...)
(Body: ...)
(Appearance: ...)
(Setting: ...)
(Interaction: ...)
(State: ...)
(Light: ...)
</shot1.shot_desc>
<shot1.story>...</shot1.story>
<shot1.objects>...</shot1.objects>
<shot1.narration></shot1.narration>
<shot1.speech_char></shot1.speech_char>
<shot1.subtitle></shot1.subtitle>

The shape above is only a template: the "..." are placeholders, never copy them.
shot_desc holds ALL EIGHT blocks and nothing else, in this order. </shotN.shot_desc> is written exactly ONCE, right after (Light: ...): never close it earlier and never write a block after it.
Every shot has (Light: ...), even when its lighting is the same as the previous shot: state it again.

uses: one or more real supplied A2 ids separated by commas, e.g. E1.1,E1.2
Leave empty fields empty, but always include every required tag.

FINAL CHECK

- correct A2 units
- every shot follows the OUTPUT shape: all eight shot_desc blocks in one tag, closed once after (Light: ...); story tag filled
- current story, clothing, and physical state preserved
- an established current state was not silently reset merely because this beat focuses on something else
- Body, State, Appearance, Interaction, and objects agree with each other about the same current fact — no intra-shot contradiction
- every visible person included; only present relevant objects included
- story adds no new facts; no invented camera or story action
- no vague clothing placeholders
- every shot_desc fact sits in its owning block; no composition/editorial language in any block; Beat is a concrete current event/action; Emotion is visible/supported reaction only; Body is geometry only; Interaction is actual current engagement; State is persistent condition only; Light invents no source

OUTPUT ONLY the required shot tags, directly one after another.
No commentary, notes, explanations, headings, markdown, planning text, or any other text outside the tags: that is a format failure.

**IMPORTANT**: This is an API-style machine output, not a chat response. Any text outside the required tags is a format failure. Every tag is closed only by its own exact name: <shot1.story> ends with </shot1.story>, <shot1.objects> ends with </shot1.objects>.
"""

def _story_material(a1: dict, macro_events: list[dict], micro_beats: list[dict]) -> str:
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


def _build_b_user_prompt(a1: dict, macro_events: list[dict], micro_beats: list[dict], count: int,
                         *, mode: str, strict_a2_1to1: bool, desc_lang: str, dialogue_style: str = "",
                         dialogue_lang: str = "en", narration_type: str = "caption") -> str:
    lang_label = lang_prompt_name(desc_lang or "en")
    mode_line = (
        "1:1 mode: make exactly one shot for each A2 unit, in order, using each once."
        if strict_a2_1to1 else
        "Editorial mode: select or combine adjacent A2 moments as needed to fit the requested shot count without changing the story."
    )
    _authoring = dialogue_authoring_text(dialogue_style, dialogue_lang, narration_type)
    _authoring_block = (_authoring + "\n") if _authoring else ""
    return (
        f"Output language for shot fields: {lang_label}\n"
        f"Final shot count: {int(count)}\n"
        f"{mode_line}\n"
        f"{_authoring_block}\n"
        f"CHARACTERS:\n{a1.get('character_sheet', '')}\n\n"
        f"WORLD:\n{a1.get('world_sheet', '')}\n\n"
        f"LOCATIONS:\n{a1.get('locations', '')}\n\n"
        f"IMPORTANT OBJECTS:\n{a1.get('important_objects', '')}\n\n"
        f"CONTINUITY:\n{a1.get('continuity_notes', '')}\n\n"
        f"STORY STRUCTURE:\n{_story_material(a1, macro_events, micro_beats)}\n\n"
        "OUTPUT FORMAT:\n"
        "Use the tag shape defined under OUTPUT in the system prompt. "
        "Return exactly the requested number of shots numbered 1..N, each with all eight tags. "
        '"uses" holds one or more A2 unit ids separated by commas, e.g. E1.1,E1.2 — never a list syntax. '
        "Refer to every person by the <charN.name> given in CHARACTERS (visible_people, shot_desc, story, speech_char). "
        "Every tag's content is plain text — no JSON, no quotes to escape. Leave a tag's content "
        "empty when that field has nothing to add, but still include the tag."
    )


def _b_max_tokens(count: int, material_size: int) -> int:
    # B-TRUNCATION-SALVAGE-A1: the old 24000 ceiling was lower than what a large shot count
    # actually needs (each shot's 8-tag Golden Formula shot_desc alone runs ~150-250 tokens),
    # so a high requested count was structurally guaranteed to hit the ceiling and truncate
    # mid-object. Raised so the budget scales with what was actually asked for.
    return max(5000, min(48000, material_size + int(count) * 900 + 2500))


async def _call_local_text(wf_template: dict, system_prompt: str, user_prompt: str, *,
                           max_tokens: int, session_id: str, on_log=None, reset: bool = True) -> str | None:
    wf = copy.deepcopy(wf_template or {})
    hit = False
    for nid, node in wf.items():
        if not isinstance(node, dict) or node.get("class_type") != "LLMSessionChatNode":
            continue
        inputs = wf[nid].setdefault("inputs", {})
        inputs["user_text"] = user_prompt
        inputs["system_prompt"] = system_prompt
        inputs["session_id"] = session_id
        inputs["reset_session"] = bool(reset)
        inputs["max_tokens"] = max_tokens
        hit = True
    if not hit:
        if on_log:
            await on_log("❌ Shot List: workflow has no LLMSessionChatNode")
        return None

    if on_log:
        await on_log(f"📋 SHOT PLANNING SYSTEM:\n{system_prompt}")
        await on_log(f"📤 SHOT PLANNING USER:\n{user_prompt}")

    loop = asyncio.get_event_loop()
    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, uuid.uuid4().hex)
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Shot List queue: {exc}")
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
                        await on_log(f"📥 SHOT PLANNING RAW:\n{text}")
                    return text
        status = ((entry.get("status") or {}).get("status_str") or "").lower()
        if status in ("success", "error"):
            break
    if on_log:
        await on_log("❌ Shot List: local LLM returned no text")
    return None


async def _call_grok_text(system_prompt: str, user_prompt: str, *, api_key: str,
                          model: str, max_tokens: int, on_log=None) -> str | None:
    if not api_key:
        if on_log:
            await on_log("❌ Shot List: Grok API key is empty")
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
        await on_log(f"📋 SHOT PLANNING SYSTEM:\n{system_prompt}")
        await on_log(f"📤 SHOT PLANNING USER:\n{user_prompt}")

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
                    await on_log(f"❌ Shot List Grok retry: {retry_exc}")
                return None
        else:
            if on_log:
                await on_log(f"❌ Shot List Grok HTTP {exc.code}: {body[:500]}")
            return None
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Shot List Grok: {exc}")
        return None

    choices = response.get("choices") if isinstance(response, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else {}
    message = first.get("message") if isinstance(first, dict) else {}
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str):
        if on_log:
            await on_log("❌ Shot List: Grok response has no text content")
        return None
    if on_log:
        await on_log(f"📥 SHOT PLANNING RAW:\n{text}")
    return text


_B_ALL_FIELDS = ("uses", "visible_people", "shot_desc", "story", "objects", "narration", "speech_char", "subtitle")
_FIELD_ALT = "|".join(_B_ALL_FIELDS)
# one token = a system opener <shotN.field>, a system closer </shotN.field>, or a bare closer </field>. Anything else (<b>, a < b, quotes, parentheses...) is plain content.
_TOKEN_RE = re.compile(rf"<shot(\d+)\.({_FIELD_ALT})>|</shot(\d+)\.({_FIELD_ALT})>|</({_FIELD_ALT})>")


def _scan_shot_tags(text: str) -> tuple[dict[int, dict[str, str]], list[dict], dict[int, int]]:
    """STRICT structural scan (nothing is repaired, moved or guessed). A field opened as <shotN.field> is valid only when the next system tag is its own exact
    closer </shotN.field>. Returns (shots, issues, last_end): shots[num][field] = the content (first occurrence only; stored even for a faulty field so that
    diagnostics can read it); issues = [{kind, shot, field, detail, pos}] with kind in missing_close / mismatch_close / stray_close / duplicate / outside_text /
    truncated; last_end[num] = end offset of the last tag that belongs to the shot (used by the salvage rule)."""
    shots: dict[int, dict[str, str]] = {}
    issues: list[dict] = []
    last_end: dict[int, int] = {}
    cur = None                                   # (num, field, content_start, open_pos)
    pos = 0

    def store(num, field, content, at):
        if field in shots.setdefault(num, {}):
            issues.append({"kind": "duplicate", "shot": num, "field": field, "detail": "", "pos": at})
        else:
            shots[num][field] = content.strip()
        last_end[num] = max(last_end.get(num, 0), at)

    for m in _TOKEN_RE.finditer(text):
        gap = text[pos:m.start()]
        if cur is None and gap.strip():
            issues.append({"kind": "outside_text", "shot": None, "field": "", "detail": gap.strip(), "pos": pos})
        if m.group(1) is not None:                                    # opener
            if cur is not None:                                       # the previous field never closed
                issues.append({"kind": "missing_close", "shot": cur[0], "field": cur[1], "detail": "", "pos": cur[3]})
                store(cur[0], cur[1], text[cur[2]:m.start()], m.start())
            cur = (int(m.group(1)), m.group(2), m.end(), m.start())
        else:                                                         # closer (exact, wrong number, or bare)
            num = int(m.group(3)) if m.group(3) is not None else None
            field = m.group(4) or m.group(5)
            if cur is None:
                issues.append({"kind": "stray_close", "shot": num, "field": field, "detail": m.group(0), "pos": m.start()})
            else:
                if num != cur[0] or field != cur[1]:
                    issues.append({"kind": "mismatch_close", "shot": cur[0], "field": cur[1], "detail": m.group(0), "pos": cur[3]})
                store(cur[0], cur[1], text[cur[2]:m.start()], m.end())
                cur = None
        pos = m.end()
    tail = text[pos:]
    if cur is not None:
        issues.append({"kind": "truncated", "shot": cur[0], "field": cur[1], "detail": "", "pos": cur[3]})
        store(cur[0], cur[1], tail, len(text))
    elif tail.strip():
        issues.append({"kind": "outside_text", "shot": None, "field": "", "detail": tail.strip(), "pos": pos})
    return shots, issues, last_end


_B_TEXT_FIELDS = ("visible_people", "shot_desc", "objects", "narration", "speech_char", "subtitle")


_SHOT_DESC_BLOCKS = ("Beat", "Emotion", "Body", "Appearance", "Setting", "Interaction", "State", "Light")
_SHOT_DESC_OPEN_RE = re.compile(r"\(\s*(" + "|".join(_SHOT_DESC_BLOCKS) + r")\s*:")


def _valid_shot_desc_structure(text: str) -> bool:
    """Structure only (never the meaning): shot_desc must be exactly the eight blocks (Beat) (Emotion) (Body) (Appearance) (Setting)
    (Interaction) (State) (Light), each once, in that order, each ending with its own ')' and nothing but whitespace (or leftover markdown '*') before the first block.
    Ordinary and nested parentheses inside a block are fine: a block runs from its '(Name:' opener up to the next opener and must be
    parenthesis-balanced (which is also how a reply cut off in the middle of a block is recognised). Nothing is added, moved or repaired here."""
    s = (text or "").strip()
    opens = list(_SHOT_DESC_OPEN_RE.finditer(s))
    if len(opens) != len(_SHOT_DESC_BLOCKS) or [m.group(1) for m in opens] != list(_SHOT_DESC_BLOCKS):
        return False
    if s[:opens[0].start()].strip("* \t\r\n"):
        return False
    for i, m in enumerate(opens):
        block = s[m.start():(opens[i + 1].start() if i + 1 < len(opens) else len(s))].rstrip("* \t\r\n")     # tolerate leftover markdown bold after ')'
        depth = 0
        for j, ch in enumerate(block):                  # every block (not only the last) must be one complete parenthesis unit; nested pairs are fine
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0 or (depth == 0 and j != len(block) - 1):
                    return False                        # closed early and text follows it
        if depth != 0 or not block.endswith(")"):
            return False
    return True


def _shot_desc_retry_hint(attempt: int) -> str:
    """Wording only. A retry that repeats the same text gets the same reply (observed: attempts 2 and 3 were identical), so the last attempt differs.
    It points back to the OUTPUT shape of the system prompt; it adds no story content."""
    hint = "\nKeep all eight blocks inside ONE <shotN.shot_desc>, closed once, right after (Light: ...); write no block after its closing tag."
    if attempt >= 3:
        hint += " This is the last attempt: follow the OUTPUT shape of the system prompt exactly."
    return hint


def _failure_note_message(last_note: str, problems: list[str] | None = None, has_tags: bool = False) -> str:
    """Explains what the model wrote outside the shot tags. Blocks outside shot_desc are a format fault, not a refusal."""
    if (last_note or "").lstrip().startswith("<shot"):
        return ' — the reply was cut off in the middle of a tag (output or context limit), not refused; try again or reduce the number of shots per macro'
    if _SHOT_DESC_OPEN_RE.search(last_note or ""):
        return ' — the model closed <shotN.shot_desc> before all eight blocks and wrote the remaining blocks outside the tag (a format fault, not a refusal; try again or another model)'
    if problems and has_tags:                  # shot tags are present: the model answered, the tags are malformed
        return " — format fault (not a refusal): " + "; ".join(problems[:3])
    return f' — the model replied: "{last_note[:160]}" (it may have refused this content; try another model)'


def _blocks_after_closer(raw: str, idx: int) -> list[str]:
    """Names of the (Block: ...) openers written between the end of shot<idx>.shot_desc's tag content and the next shot tag - i.e. AFTER an early closing tag.
    Reading only; nothing is moved."""
    text = _strip_code_fence(raw or "")
    opens = list(_SHOT_OPEN_TAG_RE.finditer(text))
    for i, m in enumerate(opens):
        if int(m.group(1)) == idx and m.group(2) == "shot_desc":
            end = opens[i + 1].start() if i + 1 < len(opens) else len(text)
            span = text[m.end():end]
            names = [x.group(1) for x in _SHOT_DESC_OPEN_RE.finditer(span)]
            return names
    return []


def _reply_problems(raw: str | None, *, count: int, valid_ids: set[str], strict_unit_ids: list[str] | None) -> list[str]:
    """Short structural list of why a reply was rejected, for the corrective message of a same-session retry. It names tags, blocks and ids only."""
    shots, issues, _end = _scan_shot_tags(_strip_code_fence(raw or ""))
    problems: list[str] = []
    struct: list[str] = []                        # tag-level faults; listed AFTER the per-shot findings
    seen_outside = False
    has_outside = any(i["kind"] == "outside_text" for i in issues)
    for i in issues:                              # tag-level faults: missing / mismatched closer, duplicate, truncation, text outside the tags
        k, n, f = i["kind"], i["shot"], i["field"]
        if k == "missing_close":
            struct.append(f"shot{n}.{f} has no closing tag </shot{n}.{f}> (another tag started before it was closed)")
        elif k == "mismatch_close":
            struct.append(f"shot{n}.{f} was closed with {i['detail']} instead of </shot{n}.{f}>")
        elif k == "stray_close" and not has_outside:      # after text outside the tags the stray closers are only a consequence
            struct.append(f"closing tag {i['detail']} has no matching opening tag")
        elif k == "duplicate":
            struct.append(f"duplicate shot{n}.{f}")
        elif k == "truncated":
            struct.append(f"the reply stopped inside an unfinished tag (it was cut off) at shot{n}.{f}")
        elif k == "outside_text" and not seen_outside:
            seen_outside = True
            struct.append("some (Block: ...) text was written outside the shot tags, after a closing tag" if _SHOT_DESC_OPEN_RE.search(i["detail"])
                          else "text outside the required tags: " + repr(i["detail"][:80]))
    for n in sorted(k for k in shots if k < 1 or k > int(count)):
        struct.append(f"unexpected shot{n} (only shots 1 to {int(count)} are requested)")
    for idx in range(1, int(count) + 1):
        f = shots.get(idx)
        if f is None:
            problems.append(f"shot{idx} is missing"); continue
        miss = [k for k in _B_ALL_FIELDS if k not in f]
        if miss:
            problems.append(f"shot{idx} lacks the tag(s) {', '.join(miss)}")
        uses = [u.strip() for u in f.get("uses", "").split(",") if u.strip()]
        if not uses or (valid_ids and any(u not in valid_ids for u in uses)) or (strict_unit_ids is not None and (idx > len(strict_unit_ids) or uses != [strict_unit_ids[idx - 1]])):
            problems.append(f"shot{idx}.uses must be exactly " + (strict_unit_ids[idx - 1] if strict_unit_ids is not None and idx <= len(strict_unit_ids) else "one or more of the supplied ids"))
        d = f.get("shot_desc", "")
        if d.strip() and not _valid_shot_desc_structure(d):
            found = [m.group(1) for m in _SHOT_DESC_OPEN_RE.finditer(d)]
            missing = [b for b in _SHOT_DESC_BLOCKS if b not in found]
            everything = _blocks_after_closer(raw or "", idx)             # every block written between the tag opener and the next shot tag
            if missing and everything == list(_SHOT_DESC_BLOCKS):
                problems.append(f"shot{idx}.shot_desc was closed right after ({found[-1] if found else 'the start'}) although the blocks {', '.join(missing)} follow: "
                                f"delete that early </shot{idx}.shot_desc> so that all eight blocks sit inside the tag, and close shot_desc only once, after (Light: ...)")
            elif missing and len(found) == len(set(found)):
                problems.append(f"shot{idx}.shot_desc lacks the block(s) {', '.join(missing)} (found [{', '.join(found) or 'none'}]; all eight are required, in order, inside one tag)")
            else:
                problems.append(f"shot{idx}.shot_desc has the blocks [{', '.join(found) or 'none'}] but they must be exactly {', '.join(_SHOT_DESC_BLOCKS)}, each once, in that order, inside one tag")
    if not seen_outside and _SHOT_DESC_OPEN_RE.search(_outside_tags_text(raw)):
        struct.append("some (Block: ...) text was written outside the shot tags, after a closing tag")
    problems += struct
    return problems or ["the tags could not be read"]


async def _log_empty_story(shots: list[dict], on_log) -> None:
    """The story TAG is required; an empty one is accepted (downstream falls back to shot_desc) but never silent."""
    empty = [str(s.get("shot")) for s in shots if not str(s.get("story") or "").strip()]
    if empty and on_log:
        await on_log("⚠ Shot List: shot(s) " + ", ".join(empty) + " came back with an empty <story> tag")


def _correction_message(problems: list[str], target: int, attempt: int) -> str:
    """The user message of a same-session retry: the model still sees its own previous reply, so only the fault and the required shape are stated."""
    msg = ("Your previous reply could not be accepted because the tag structure is wrong: " + "; ".join(problems[:6]) + ". "
           f"Reply again with the complete set of tags for all {target} shots, numbered 1 through {target}, each with all eight tags "
           "(uses, visible_people, shot_desc, story, objects, narration, speech_char, subtitle), every tag closed with its own exact closing tag.")
    return msg + _shot_desc_retry_hint(attempt)


def _clean_shot_fields(fields: dict[str, str], idx: int, *, valid_ids: set[str],
                       strict_unit_ids: list[str] | None) -> dict | None:
    """One shot's structural checks only — shared by the strict (all-shots-present) parser and
    the natural-prefix salvage below. No semantic checking or rewriting. "uses" is a comma-
    separated list inside its own tag's value, split here — never a JSON array to malform."""
    uses = [u.strip() for u in fields.get("uses", "").split(",") if u.strip()]
    if not uses:
        return None
    if valid_ids and any(u not in valid_ids for u in uses):
        return None
    if strict_unit_ids is not None and (idx > len(strict_unit_ids) or uses != [strict_unit_ids[idx - 1]]):
        return None
    if not fields.get("shot_desc", "").strip():
        return None
    if not _valid_shot_desc_structure(fields["shot_desc"]):     # eight blocks, in order - structure only; both the full parse and the salvage come through here
        return None
    if any(key not in fields for key in _B_TEXT_FIELDS + ("story",)):         # the story TAG is required (an empty tag is a separate condition, logged by the caller)
        return None
    return {
        "shot": idx,
        "uses": uses,
        "visible_people": fields["visible_people"],
        "shot_desc": fields["shot_desc"],
        "objects": fields["objects"],
        "narration": fields["narration"],
        "speech_char": fields["speech_char"],
        "subtitle": fields["subtitle"],
        "story": fields["story"],
    }


def _parse_b_tags(raw: str | None, *, count: int, valid_ids: set[str],
                   strict_unit_ids: list[str] | None = None) -> list[dict] | None:
    """Structural parser only. No semantic checking or rewriting. All-or-nothing: shot 1..count
    must each have every required tag and pass its structural check, and the reply must hold no faulty / duplicate / extra tag and no text outside the tags, or this returns None (the
    existing retry path, and _salvage_b_tags below, handle a short/malformed response).

    strict_unit_ids: when set (1:1 mode), an ID/order check only — shot i must use exactly
    A2 unit i, no more, no less. Not a semantic check."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    shots_by_num, issues, _end = _scan_shot_tags(_strip_code_fence(raw))
    if issues or set(shots_by_num) != set(range(1, int(count) + 1)):         # faulty / duplicate tag, text outside the tags, extra or skipped shot number -> reject
        return None
    out: list[dict] = []
    for idx in range(1, int(count) + 1):
        fields = shots_by_num.get(idx)
        if fields is None:
            return None
        clean = _clean_shot_fields(fields, idx, valid_ids=valid_ids, strict_unit_ids=strict_unit_ids)
        if clean is None:
            return None
        out.append(clean)
    return out


def _salvage_b_tags(raw: str | None, *, valid_ids: set[str],
                     strict_unit_ids: list[str] | None = None, count: int | None = None) -> list[dict] | None:
    """B-TRUNCATION-SALVAGE-A1: recovers the longest leading run of shots 1, 2, 3... that are
    individually structurally complete and valid (exact closers, no duplicates), stopping at the first gap or violation — the
    tag format makes this the SAME extraction as the strict parser, just without requiring every
    shot up to `count` to be present. Never edits, completes, or invents shot content; returns
    None if nothing usable was recovered."""
    shots_by_num, issues, last_end = _scan_shot_tags(_strip_code_fence(raw or ""))
    out: list[dict] = []
    idx = 1
    while idx in shots_by_num and (count is None or idx <= int(count)):
        # a shot is salvageable only when it is individually clean: no faulty / duplicate tag of its own and no stray text before its last tag
        if any(i["shot"] == idx for i in issues) or any(i["kind"] == "outside_text" and i["pos"] < last_end.get(idx, 0) for i in issues):
            break
        clean = _clean_shot_fields(shots_by_num[idx], idx, valid_ids=valid_ids, strict_unit_ids=strict_unit_ids)
        if clean is None:
            break
        out.append(clean)
        idx += 1
    if count is not None and len(out) >= int(count):
        return None                    # nothing is missing: a reply that has all shots but a fault elsewhere (extra shot, stray text) is a retry, never a "partial" result
    return out or None


_SHOT_OPEN_RE = re.compile(r"<shot\d+\.[a-z_]+>")


def _outside_tags_text(raw: str | None) -> str:
    """Whatever the model wrote OUTSIDE the shot tags (a refusal, commentary). Transport-only —
    used to tell the user what the model actually said, never to interpret it."""
    return _SHOT_PAIR_RE.sub("", _strip_code_fence(raw or "")).strip()


def _has_unclosed_tag(raw: str | None) -> bool:
    return any(i["kind"] in ("truncated", "missing_close") for i in _scan_shot_tags(_strip_code_fence(raw or ""))[1])


# ── LOCAL per-macro batching ─────────────────────────────────────────────────────────────────────
# Local B runs ONE A1 macro event (with only its A2 micro-events) per call, sequentially. Python
# owns the counting/routing/merging; B owns every semantic storyboard decision inside a macro.
# Grok keeps the whole-story call in run_b_vnext below.

def group_micro_by_macro(macro_events: list[dict], micro_beats: list[dict]) -> list[tuple[dict, list[dict]]]:
    """[(macro, its A2 micro-events)] in macro order, non-empty macros only. Pure grouping by the
    parent id A2 already wrote — no interpretation."""
    by_parent: dict[str, list[dict]] = {}
    for beat in micro_beats or []:
        if isinstance(beat, dict) and str(beat.get("id") or "").strip():
            by_parent.setdefault(str(beat.get("parent") or ""), []).append(beat)
    out: list[tuple[dict, list[dict]]] = []
    for event in macro_events or []:
        if not isinstance(event, dict):
            continue
        group = by_parent.get(str(event.get("id") or ""), [])
        if group:
            out.append((event, group))
    return out


def allocate_macro_targets(recommended: list[int], requested: int) -> list[int]:
    """Shots per macro, from each macro's A2 micro-event count (largest-remainder allocation).

    raw_i = requested * recommended_i / sum(recommended). Every macro gets at least 1 and the
    result sums to `requested` exactly. requested == sum(recommended) gives recommended unchanged.
    Ties break toward the denser macro, then the earlier one. Raises ValueError when requested is
    below the number of macros (Python never chooses a macro to drop). Counts only — no story
    knowledge."""
    n = len(recommended)
    total = sum(recommended)
    if n == 0 or total <= 0:
        raise ValueError("no beat micro-events to allocate")
    if requested < n:
        raise ValueError(f"requested {requested} shots is below the minimum of {n} "
                         f"(one per macro event that has beat micro-events)")
    raw = [requested * r / total for r in recommended]
    base = [max(1, int(x)) for x in raw]           # floor, but never below 1
    diff = requested - sum(base)
    if diff > 0:
        order = sorted(range(n), key=lambda i: (-(raw[i] - int(raw[i])), -recommended[i], i))
        for k in range(diff):
            base[order[k % n]] += 1
    elif diff < 0:
        # the min-1 clamp over-allocated: take back from the most over-allocated macros (base > 1)
        for _ in range(-diff):
            cands = [i for i in range(n) if base[i] > 1]
            i = max(cands, key=lambda j: (base[j] - raw[j], j))
            base[i] -= 1
    return base


def _build_b_macro_user_prompt(a1: dict, macro: dict, micros: list[dict], target: int, *,
                               strict_1to1: bool, prev_shot: dict | None, desc_lang: str,
                               dialogue_style: str = "", dialogue_lang: str = "en",
                               narration_type: str = "caption") -> str:
    lang_label = lang_prompt_name(desc_lang or "en")
    n_micro = len(micros)
    if strict_1to1:
        duty = ("TARGET SHOTS equals the number of supplied micro-events: make exactly one shot for each "
                "micro-event, in order, using each id once.")
    elif target < n_micro:
        duty = ("TARGET SHOTS is smaller than the number of supplied micro-events: choose the most "
                "story-important drawable moments and combine adjacent compatible micro-events when one "
                "frozen image can truthfully represent them. Preserve the essential actions, interactions, "
                "reactions, reveals, state changes and consequences; do not drop essential story progression "
                "merely to reduce the count. A shot's uses may list several adjacent ids.")
    else:
        duty = ("TARGET SHOTS is larger than the number of supplied micro-events: expand dense supplied "
                "moments into additional distinct drawable instants, choosing meaningful visual phases the "
                "supplied event already supports or implies. Do not invent new story actions, characters, "
                "objects, state changes, outcomes or chronology. A shot's uses may repeat a supplied id.")
    _authoring = dialogue_authoring_text(dialogue_style, dialogue_lang, narration_type)
    _authoring_block = (_authoring + "\n") if _authoring else ""
    prev = ""
    if prev_shot:
        prev = (
            "PRECEDING SHOT (already drawn elsewhere — read-only context for inherited clothing, physical state, "
            "object, and location continuity; do not create a shot for it, never use its id):\n"
            f"visible_people: {prev_shot.get('visible_people', '')}\n"
            f"shot_desc: {prev_shot.get('shot_desc', '')}\n"
            f"objects: {prev_shot.get('objects', '')}\n\n"
        )
    micro_lines = "\n".join(f"{m.get('id')}: {m.get('content', '')}" for m in micros)
    first_id = str(micros[0].get("id") or "")
    return (
        f"Output language for shot fields: {lang_label}\n"
        f"TARGET SHOTS: {int(target)} — this is HARD. Create exactly {int(target)} shots for THIS macro event only, "
        f"numbered 1..{int(target)}.\n"
        f"{duty}\n"
        "The macro event and its micro-events define WHAT happens; distribute that truth across exactly the target shots.\n"
        f"{_authoring_block}\n"
        f"CHARACTERS:\n{a1.get('character_sheet', '')}\n\n"
        f"WORLD:\n{a1.get('world_sheet', '')}\n\n"
        f"LOCATIONS:\n{a1.get('locations', '')}\n\n"
        f"IMPORTANT OBJECTS:\n{a1.get('important_objects', '')}\n\n"
        f"CONTINUITY:\n{a1.get('continuity_notes', '')}\n\n"
        f"{prev}"
        f"CURRENT MACRO EVENT:\n{macro.get('id')}: {macro.get('content', '')}\n\n"
        f"CURRENT A2 MICRO-EVENTS (the only ids \"uses\" may contain):\n{micro_lines}\n\n"
        "OUTPUT FORMAT:\n"
        "Use the tag shape defined under OUTPUT in the system prompt. "
        f"Return exactly {int(target)} shots numbered 1..{int(target)}, each with all eight tags. "
        '"uses" holds one or more of the ids above separated by commas — never a list syntax. '
        "Refer to every person by the <charN.name> given in CHARACTERS (visible_people, shot_desc, story, speech_char). "
        "Every tag's content is plain text — no JSON, no quotes to escape. Leave a tag's content "
        "empty when that field has nothing to add, but still include the tag."
    )


async def _run_b_local_per_macro(a1: dict, macro_events: list[dict], micro_beats: list[dict], count: int,
                                 wf_template: dict, llm_wf_file: str, on_err, on_log, *,
                                 desc_lang: str, dialogue_style: str, dialogue_lang: str,
                                 narration_type: str, chat_capture: dict | None) -> list[dict] | None:
    groups = group_micro_by_macro(macro_events, micro_beats)
    if not groups:
        await on_err("Shot List: Beat Sheet has no micro-events under any macro event")
        return None
    recommended = [len(g) for _m, g in groups]
    try:
        targets = allocate_macro_targets(recommended, count)
    except ValueError as exc:
        await on_err(f"Shot List: {exc}")
        return None
    if on_log:
        await on_log("Shot List LOCAL per-macro allocation: " + ", ".join(
            f"{m.get('id')}={t} (beats {r})" for (m, _g), t, r in zip(groups, targets, recommended)))

    all_shots: list[dict] = []
    sessions: list[dict] = []
    all_turns: list[dict] = []
    session_id = ""
    for (macro, micros), target in zip(groups, targets):
        mid = str(macro.get("id") or "")
        valid_ids = {str(m.get("id")) for m in micros}
        strict_ids = [str(m.get("id")) for m in micros] if target == len(micros) else None
        prev_shot = all_shots[-1] if all_shots else None
        user_prompt = _build_b_macro_user_prompt(
            a1, macro, micros, target, strict_1to1=strict_ids is not None, prev_shot=prev_shot,
            desc_lang=desc_lang, dialogue_style=dialogue_style, dialogue_lang=dialogue_lang,
            narration_type=narration_type)
        # Output budget only — sized from THIS macro's own content, not the full-story context now
        # included in user_prompt for continuity (that inflates len(user_prompt) without changing how
        # much B actually has to write back).
        _material = len(str(macro.get("content") or "")) + sum(len(str(m.get("content") or "")) for m in micros)
        max_tokens = min(_b_max_tokens(target, _material), _LOCAL_MAX_TOKENS_CEILING)
        session_id = uuid.uuid4().hex[:8]          # fresh conversation per macro
        turns: list[dict] = []
        result = None
        last_note = ""
        raw = None
        for attempt in (1, 2, 3):
            # attempt 1: the full prompt in a fresh session; attempts 2-3: a short corrective message in the SAME session (reset_session=False),
            # so the model sees its own rejected reply and what is wrong with it
            # attempt 3 starts a FRESH session again (full prompt + the specific correction): the history of two long replies would overflow the model's
            # context and the reply was cut off in practice
            fresh = attempt != 2
            if attempt == 1:
                sent = user_prompt
            else:
                corr = _correction_message(_reply_problems(raw, count=target, valid_ids=valid_ids, strict_unit_ids=strict_ids), target, attempt)
                sent = corr if attempt == 2 else user_prompt + "\n\n" + corr
            raw = await _call_local_text(wf_template or {}, _B_SYSTEM_PROMPT, sent,
                                         max_tokens=max_tokens, session_id=session_id, on_log=on_log, reset=fresh)
            turns.append({"macro": mid, "system_prompt": _B_SYSTEM_PROMPT, "user_prompt": sent, "reset_session": fresh,
                          "raw_response": raw or ""})
            # complete assigned target only — no prefix salvage for a small explicit per-macro batch
            result = _parse_b_tags(raw, count=target, valid_ids=valid_ids, strict_unit_ids=strict_ids)
            if result is not None:
                break
            last_note = _outside_tags_text(raw)
            if on_log:
                await on_log(f"⚠ Shot List {mid} attempt {attempt}: missing or malformed tags for {target} shots"
                             + (f' — model said: "{last_note[:160]}"' if last_note else ""))
        sessions.append({"macro": mid, "session_id": session_id, "target": target, "turns": turns})
        all_turns.extend(turns)
        if result is None:
            if chat_capture is not None:
                chat_capture.update({"turns": all_turns, "sessions": sessions, "backend": "local",
                                     "llm_wf_file": llm_wf_file, "session_id": session_id})
            _msg = f"B: macro {mid} did not return its {target} required shots after 3 attempts"
            _msg += _failure_note_message(last_note, _reply_problems(raw, count=target, valid_ids=valid_ids, strict_unit_ids=strict_ids), bool(_TOKEN_RE.search(raw or "")))
            await on_err(_msg)
            return None
        await _log_empty_story(result, on_log)
        for shot in result:                         # structure only: local 1..target -> global numbering
            shot["shot"] = len(all_shots) + 1
            gate_dialogue_fields(shot, dialogue_style)   # drop text channels this Dialogue Style does not use
            all_shots.append(shot)

    if chat_capture is not None:
        chat_capture.update({"turns": all_turns, "sessions": sessions, "backend": "local",
                             "llm_wf_file": llm_wf_file, "session_id": session_id})
    return all_shots


async def run_b_vnext(a1: dict, macro_events: list[dict], micro_beats: list[dict], count: int,
                       llm_wf_file: str, on_err, on_log=None,
                       dialogue_lang: str = "en", genre: str = "", genre2: str = "",
                       desc_lang: str = "en",
                       grok_api_key: str = "", grok_model: str = "grok-4-latest",
                       chat_capture: dict | None = None, mode: str = "cinematic",
                       a2_count: int = 0, strict_a2_1to1: bool = False,
                       dialogue_style: str = "", narration_type: str = "caption") -> list[dict] | None:
    """Clean B. Compatibility args remain accepted; Python only transports them or ignores them."""
    try:
        count = int(count)
    except Exception:
        await on_err("Shot List: invalid shot count")
        return None
    if count <= 0:
        await on_err("Shot List: shot count must be greater than zero")
        return None

    valid_ids = {
        str(e.get("id")) for e in (macro_events or []) if isinstance(e, dict) and e.get("id")
    } | {
        str(m.get("id")) for m in (micro_beats or []) if isinstance(m, dict) and m.get("id")
    }
    # A2-UNIT-1TO1-DEFAULT §5: ID/order validation only — the ordered A2 unit IDs 1:1 mode checks
    # each shot's `uses` against, structurally, not semantically.
    strict_unit_ids = (
        [str(m.get("id")) for m in (micro_beats or []) if isinstance(m, dict) and m.get("id")]
        if strict_a2_1to1 else None
    )
    user_prompt = _build_b_user_prompt(
        a1, macro_events, micro_beats, count,
        mode=mode, strict_a2_1to1=bool(strict_a2_1to1), desc_lang=desc_lang,
        dialogue_style=dialogue_style, dialogue_lang=dialogue_lang, narration_type=narration_type,
    )
    material_size = len(_story_material(a1, macro_events, micro_beats))
    max_tokens = _b_max_tokens(count, material_size)
    session_id = uuid.uuid4().hex[:8]

    wf_template = None
    if llm_wf_file != GROK_MARKER:
        if not (llm_wf_file or "").strip():
            await on_err("Shot List: no local LLM workflow selected")
            return None
        wf_path = LLM_DIR / f"{llm_wf_file}.json"
        if not wf_path.exists():
            await on_err(f"ไม่พบ {wf_path}")
            return None
        try:
            wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
        except Exception as exc:
            await on_err(f"Shot List workflow read failed: {exc}")
            return None

    if llm_wf_file != GROK_MARKER:
        # LOCAL: one macro event per sequential call (Grok keeps the whole-story call below)
        return await _run_b_local_per_macro(
            a1, macro_events, micro_beats, count, wf_template or {}, llm_wf_file, on_err, on_log,
            desc_lang=desc_lang, dialogue_style=dialogue_style, dialogue_lang=dialogue_lang,
            narration_type=narration_type, chat_capture=chat_capture)

    turns: list[dict] = []
    last_note = ""
    for attempt in (1, 2, 3):
        sent = user_prompt if attempt == 1 else user_prompt + (
            "\n\nThe previous response was missing or malformed tags. Return the complete set of "
            f"tags for all {count} shots, numbered 1 through {count}, each with all eight tags "
            "(uses, visible_people, shot_desc, story, objects, narration, speech_char, subtitle). "
            '"uses" holds ids separated by commas (e.g. E1.1,E1.2), never a list syntax.'
            + _shot_desc_retry_hint(attempt)
        )
        if llm_wf_file == GROK_MARKER:
            raw = await _call_grok_text(
                _B_SYSTEM_PROMPT, sent, api_key=grok_api_key, model=grok_model,
                max_tokens=max_tokens, on_log=on_log,
            )
        else:
            raw = await _call_local_text(
                wf_template or {}, _B_SYSTEM_PROMPT, sent,
                # B-TRUNCATION-SALVAGE-A1 follow-up: ComfyUI's LLMSessionChatNode schema hard-caps
                # max_tokens at 32768 (INT input, max=32768) and rejects the whole queue with
                # HTTP 400 above that — clamp only the Local request; Grok has no such ceiling.
                max_tokens=min(max_tokens, _LOCAL_MAX_TOKENS_CEILING),
                session_id=session_id, on_log=on_log,
            )
        turns.append({"system_prompt": _B_SYSTEM_PROMPT, "user_prompt": sent, "raw_response": raw or ""})
        result = _parse_b_tags(raw, count=count, valid_ids=valid_ids, strict_unit_ids=strict_unit_ids)
        if result is not None:
            if chat_capture is not None:
                chat_capture["turns"] = turns
                chat_capture["backend"] = "grok" if llm_wf_file == GROK_MARKER else "local"
                chat_capture["llm_wf_file"] = llm_wf_file
                chat_capture["session_id"] = session_id if llm_wf_file != GROK_MARKER else ""
                chat_capture["grok_model"] = grok_model
            await _log_empty_story(result, on_log)
            for _s in result:
                gate_dialogue_fields(_s, dialogue_style)
            return result

        # B-TRUNCATION-SALVAGE-A1: the tag format makes salvage safe to attempt unconditionally —
        # it either recovers a genuine leading prefix of complete shots, or finds nothing and
        # falls through to the normal retry below. Accept it immediately rather than burning the
        # remaining retries on a response that already gave usable shots.
        salvaged = _salvage_b_tags(raw, valid_ids=valid_ids, strict_unit_ids=strict_unit_ids, count=count)
        if salvaged:
            if on_log:
                await on_log(f"⚠ Shot List: response was incomplete — salvaged {len(salvaged)} of "
                             f"{count} complete shots, using them as-is")
            if chat_capture is not None:
                chat_capture["turns"] = turns
                chat_capture["backend"] = "grok" if llm_wf_file == GROK_MARKER else "local"
                chat_capture["llm_wf_file"] = llm_wf_file
                chat_capture["session_id"] = session_id if llm_wf_file != GROK_MARKER else ""
                chat_capture["grok_model"] = grok_model
                chat_capture["salvage"] = {
                    "recovered": len(salvaged), "requested": count,
                    # what the model said instead of the rest (a refusal shows up here), and whether
                    # it stopped cleanly (no unfinished tag) or was cut off mid-tag
                    "note": _outside_tags_text(raw)[:240],
                    "unclosed": _has_unclosed_tag(raw),
                }
            for _s in salvaged:
                gate_dialogue_fields(_s, dialogue_style)
            return salvaged
        last_note = _outside_tags_text(raw)
        if on_log:
            if last_note:
                await on_log(f'❌ Shot List attempt {attempt}: the model replied with text instead of shot '
                             f'tags (possible refusal): "{last_note[:200]}"')
            else:
                await on_log(f"⚠ Shot List attempt {attempt}: missing or malformed tags")

    if chat_capture is not None:
        chat_capture["turns"] = turns
        chat_capture["backend"] = "grok" if llm_wf_file == GROK_MARKER else "local"
        chat_capture["llm_wf_file"] = llm_wf_file
        chat_capture["session_id"] = session_id if llm_wf_file != GROK_MARKER else ""
        chat_capture["grok_model"] = grok_model
    _msg = "B: LLM did not return the required tags for every shot after 3 attempts"
    _msg += _failure_note_message(last_note, _reply_problems(raw, count=count, valid_ids=valid_ids, strict_unit_ids=strict_unit_ids), bool(_TOKEN_RE.search(raw or "")))
    await on_err(_msg)
    return None


# Canonical name — Shot List (legacy: B). The legacy entry point above stays for compatibility; run_shot_planning is the same function.
run_shot_planning = run_b_vnext
