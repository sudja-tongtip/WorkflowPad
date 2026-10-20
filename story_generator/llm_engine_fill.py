"""
llm_engine_fill.py — Image Prompts engine (legacy name: Fill).

Image Prompts converts current shot truth into renderer-facing HOW.

Design rule:
    LLM = visual reasoning + prompt writing.
    Python = transport + explicit structure only.

Image Prompts receives Shot List's shot truth and writes the final Krea2 prompt. Python never
interprets, repairs, deletes, adds, or rewrites prompt meaning.
"""

__version__ = "20260922_fill_clean_a1"

import asyncio
import copy
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

from .constants import LLM_DIR
from . import llm_overrides as _overrides
from .comfy_api import comfy_queue, comfy_history
from .visual_direction import visual_direction_for_preset


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|```\s*$")


def _strip_code_fence(raw: str) -> str:
    """Transport-only cleanup: some models wrap valid JSON in ```json fences. Strip fence
    markers only — never touch the JSON content itself."""
    return _CODE_FENCE_RE.sub("", raw.strip()).strip()


GROK_MARKER = "[Grok API]"
FILL_ENGINE_VNEXT2 = "vnext2"
_LLM_TIMEOUT = 120
# LOCAL ONE-CALL FILL (experimental): all shots in ONE local request, the same batch prompt and JSON answer that Grok uses. A very large n_ctx is needed (the sequence context
# plus every target shot plus every answer must fit); ~+5 GB of VRAM over the workflow's 12,288 was measured to be about 98,304 tokens on the 12 GB RTX 5070.
# Falls back to the per-shot loop whenever the single call cannot be accepted. False = the previous per-shot behaviour only.
_LOCAL_ONE_CALL = False
_LOCAL_ONE_CALL_N_CTX = 98304
_LOCAL_ONE_CALL_TIMEOUT = 1200
_GROK_TEMPERATURE = 0.45
_GROK_REASONING_EFFORT = "low"
# FILL CONVERSATION (experimental, local only): the per-shot loop becomes ONE multi-call session (INITIALIZE with the production sheet -> ACK, then one short request per shot,
# the node keeps the history; n_ctx 32768 in a workflow copy and Python checks a token budget before every call - see fill_conversation.py). False = the per-shot behaviour below.
_FILL_CONVERSATION = True
# Grok conversation (one API call per shot with the real history) was measured to be much slower than the one-call batch; False = Grok keeps the batch even when _FILL_CONVERSATION is True.
_FILL_CONVERSATION_GROK = False


_CAMERA_FROM_EMOTION = (
    "Choose one camera from the Emotion words of this shot, and name only that camera. "
    "Shock, startle, caught, or being seen: the camera is behind the person who feels it. "
    "A gaze, or a feeling that belongs to one person: the camera is through that person's eyes. "
    "Attention on a surface or a result: the camera is above that surface. "
    "Only a calm or shared feeling uses the side; when the feeling is flat or unclear, choose by what the shot needs to show. "
    "A camera that repeats a recent one without a visual necessity is changed in direction, height or distance."
)

_FILL_SYSTEM_PROMPT = """You are an illustrator who receives the data and writes one final Krea 2 image prompt. Use the data sheets. Interpret the meaning of the story and of shot_desc into that prompt, so Krea 2 draws a picture that is physically complete and realistic. Emphasize each character's emotion and the completeness of the image.

The current Beat is the visual priority of this shot.
shot_desc is the factual, spatial, and continuity authority (above all Beat, Body, Interaction, Setting, State). CURRENT B SHOT.story carries the emotion and the meaning of the moment; it does not add a person, object, or event.

CURRENT B SHOT is the authoritative truth. Draw the settled picture — who is where, which body meets which body, and what each face shows — never a new WHAT: who is present, character identity, story-relevant objects, the core event/action, clothing state, injury/restraint/body state, location, dialogue, relationships, outcome, or chronology; show the emotion of the moment in each face. Use the supplied VISUAL DIRECTION to choose the concrete technique yourself; never output control labels.
Resolve B's spatial relations before writing anything: what is inside, outside, through, behind, in front of, touching, or crossing a boundary or opening, including which body parts are on each side. These are story truth, not composition variables.
For familiar real-world objects, preserve normal geometry, scale, boundaries, openings, support, and usable space unless B/A1 defines otherwise. A body rests on the surface that supports it. No body part passes through furniture, the floor, or a wall. If framing conflicts with physical reality, adapt camera/framing/staging — never the object's structure or scale. Adapting the frame never moves a body part onto a contact the shot did not state.

MAIN IMAGE DESCRIPTION opens with the view, then one sentence per fact. The view sentence says whose front or back the camera sees and who is behind or beside them. When CURRENT B SHOT names no viewpoint, that view is the one camera chosen from this shot's emotion. A fact is one of: body orientation, which body part meets which body part, current clothing, and what supports the body. Write each fact once, in concrete words, naming the body parts the shot names. Do not follow a fact with a synonym or a vaguer verb. When CURRENT B SHOT gives the body geometry (which part touches which, where hands, knees and feet are), keep every part of it. When it states only the action, state that action as the shot states it and add no contact point and no hand, arm or leg position the shot does not give; you may decide only staging that changes no story fact (small angles, which body overlaps which from the camera side, what supports the weight). A fact the data does not give is left out of the prompt: never write "not specified", "unstated", "unknown" or "assumed". No lighting in this block, and do not describe the view a second time.
When CURRENT B SHOT states a concrete current physical or continuity state, keep it in the block that owns it. Do not weaken it into a vaguer mood. Pose, body orientation, and contact go only in MAIN IMAGE DESCRIPTION. IDENTITY LOCK is appearance only: who the person is (gender, age, nationality, body, face, skin, marks) and the full outfit worn now; nothing about what they do, where they are, how they lie or stand, or who they touch. FRAME LAYOUT and LOCATION LOCK do not restate them. Never infer a new state from an earlier shot.

FRAME LAYOUT owns only screen-space placement of visible people and current objects. Write short natural English using explicit image-relative terms such as image-left, image-right, center, foreground, midground, and background; avoid ambiguous bare left/right, tags, key:value syntax, coordinates, or percentages. If no previous FRAME LAYOUT exists, choose one coherent initial arrangement. Once screen-space relationships are established, they persist until CURRENT B SHOT requires them to change, or the camera moves to another side and the screen is redrawn from that camera. Absence of repeated screen-side wording is not a change. CURRENT B SHOT remains authoritative for world-space truth. Keep identity, action, lighting, location identity, and camera description out of this block.

CAMERA owns only POV, viewing angle, framing, and scale. If CURRENT B SHOT names a viewpoint, keep it. If it names none, choose one camera from the Emotion words of this shot, and name only that camera, then write that same view as MAIN's first sentence. Shock, startle, caught, or being seen: the camera is behind the person who feels it. A gaze, or a feeling that belongs to one person: the camera is through that person's eyes. Attention on a surface or a result: the camera is above that surface. Only a calm or shared feeling uses the side; when the feeling is flat or unclear, choose by what the shot needs to show. A camera that repeats a recent one without a visual necessity is changed in direction, height or distance. The angle must not move a body part onto a contact the shot did not state. CAMERA may change from shot to shot. When the camera moves to another side, FRAME LAYOUT is redrawn from that camera and world positions stay. The pose does not choose the camera. This rule changes only CAMERA, never the pose, people, objects or layout. Write one concise natural English sentence with no mood, lighting, story interpretation, body action, or decorative cinematic prose.

LIGHT is one short natural English sentence stating the established illumination: its source, direction, and visible behavior (softness/hardness, falloff, reflection, material response, when relevant) — never a keyword list. Use a source already established by B/A1. If no physical source is established, describe only the visible lighting quality and behavior without inventing a new source (no default moonlight, sunlight, golden hour, rim light, volumetric shafts, or neon merely because it looks cinematic) — Fill controls how established light appears, never a new factual light source, and a new light source never becomes a new story fact.

Describe current clothing and physical state from CURRENT B SHOT.
Stable identity comes from CHARACTERS: a data sheet with one <charN.*> tag set per person. The tags only label the data — they are never part of your answer. Meaning of each:
- name, alias: who the person is. CURRENT B SHOT refers to people by name: find each visible person's tag set in CHARACTERS by that name. alias is only another name the story uses for them.
- trigger: the token of a separately trained model that already knows this person's face and body.
  Person WITH a trigger: use the actual trigger token, written exactly as given, as their identifier inside natural sentences — the way a sentence would naturally use someone's name, never as a label or metadata field. Whenever their full name or an alias would identify them (CURRENT B SHOT uses names), use the trigger token instead; never output the name or alias as their identity. Ordinary words that merely occur inside a name stay allowed (for a person named "Rose Red", the word "red" in "a red dress" is fine).
  Use the actual value written inside <charN.trigger>. Never output the literal text "<trigger>" or any angle-bracket placeholder.
  Person WITHOUT a trigger: use their canonical name as their identifier inside natural sentences. Resolve their <charN.name> to ONE short English label and use that exact label everywhere for them, in every block: transliterate a non-Latin personal name (e.g. Thai) into Latin script; if the name is only a role or description with no personal-name part (e.g. "Young Man (the narrator)"), translate it into a short English label instead (e.g. "the young man") — never copy the original non-Latin text into the label. Never use the word "trigger" as anyone's identity — that word names a mechanism, never a person. A trigger token itself is never transliterated or modified.
- age, gender, nationality, body, face, skin, marks: stable visual identity facts. For every visible person, IDENTITY LOCK MUST explicitly include their supplied gender, their supplied age or age range, and their supplied nationality when provided, keeping each exactly in meaning ("Thai-American" stays "Thai-American", every supplied nationality stays); never infer or swap a nationality from appearance, name, language, clothing, or location, and never replace it with ethnicity or residence. These are mandatory stable identity anchors, even when visually obvious, unrelated to the current action, or the person has a trigger token or is a secondary character. Keep the supplied form: an exact age stays exact, a range stays a range ("in his early 40s"), never a vaguer category or a fake exact number. Preserve the other supplied stable facts naturally too. Never invent a missing fact — if no age or nationality is supplied, give none and never remark that it is missing; do not guess it from face, body, role, clothing, or story. Each visible person gets this coverage independently.
  BAD: "The guard is a broad-shouldered man with cropped black hair."
  GOOD: "The guard is a man in his early 40s with broad shoulders and cropped black hair."
  (With a trigger the identifier is that trigger token, never the name.) Age and nationality need to appear only in IDENTITY LOCK, not in the other blocks.
- init_hair, init_cloth: the starting hair and outfit. Current hair, clothing and physical state come from CURRENT B SHOT; if it changes or removes them, that wins.
  OUTFIT CONTINUITY: each person wears one outfit that carries through the whole story. Start from init_cloth, add every item any shot's B names, and keep each item until a B shot removes or changes it (an item B names once and then stops mentioning is still worn). If the outfit is incomplete or vague (no lower garment or footwear, or only "casual clothes"), choose concrete, plausible items the first time the person appears and use the same items in every later shot. Write the person's full current outfit in IDENTITY LOCK.
Write IDENTITY LOCK, LOCATION LOCK, and OBJECT LOCK as normal spoken English sentences, the way a person would describe the scene aloud — never as tags, labels, or key:value pairs, and never a comma-only attribute list. No "trigger:", no "Name:", no attribute dumped after a colon; write one or more short factual sentences per visible person, place, or object.
LOCATION LOCK describes the canonical location, the relevant sub-area, and any doorway, window, or threshold — never pose, emotion, action, camera, lighting, or decorative prose.
OBJECT LOCK describes only objects actually present in CURRENT B SHOT.objects: each object's identity, relevant appearance, current state, and ownership — never pose, emotion, appearance of people, camera, or lighting, and never an object that is not present there.
Trigger status is internal control metadata and must never appear in the output: never write "no trigger", "without trigger", "trigger empty", "with trigger", "triggered", "untriggered", or any explanation of whether someone has a trigger, and never start a sentence with the bare word "trigger". Output only natural sentences that use the resolved identifier — the trigger token or the name — exactly the way a sentence naturally uses a person's name.
Environment comes from B's Setting plus the established production reference.
Actual object presence comes from CURRENT B SHOT.objects.

Write natural visual English for Krea 2: concrete, spatial, concise, camera-visible, and physically coherent.
Prefer strong nouns, visible actions, body geometry, and depth over abstract explanation of story meaning.
Do not repeat the same fact in multiple forms.
Do not describe invisible story context.
Do not use hedge words such as "unspecified", "unknown", "implied presence", or "n/a".
Do not materialize abstract emotion, desire, tension, fear, romance, or mood into a new object, anatomy, contact, action, or story event.

Emotion may be expressed through performance and visual direction only.

Never invent a physical cause merely to make an emotion visually explicit.

Story-relevant object presence is controlled exclusively by CURRENT B SHOT.objects.
If an object is not present there, do not create it to explain or justify the scene.

Preserve every B truth that affects the visible current image, and never contradict any B truth. Do not restate unrelated facts merely for completeness — this never applies to the mandatory IDENTITY LOCK gender, age, and nationality facts above, and "do not repeat the same fact" does not stop them from appearing once in IDENTITY LOCK.

Use STYLE only to control visual appearance.
If a style reference or explicit style is supplied, follow it without adding competing aesthetics.

SCENE REASONING (silent, never output): before writing, settle the moment in world-space — who is in front of, behind, or beside whom, what supports each person, and for every hand or arm action in B whose hand it is, which arm it belongs to, and what it touches. A person has two arms and one hand does one thing. World-space (B) and screen-space (image-left, foreground) are different facts: a person B places behind another stays behind in every block, whatever the framing. Where B leaves a staging detail open (arm angle, small lean, what overlaps what, which arm is hidden), choose the most natural option that keeps every B fact; never change who is present, the action, contact, objects, clothing or body state, location, order, or shot scale. Then choose one camera from this shot's emotion, unless CURRENT B SHOT already names a viewpoint. Never add a hand, arm, contact, or action for drama; if B asks one hand to do two incompatible things, keep the people, the object, and the contact and write one physically possible arrangement of the arms they have. Parts the frame hides still exist; do not force them into view.

Output only:

MAIN IMAGE DESCRIPTION:
<First sentence: the one camera chosen from this shot's emotion, whose front or back it sees and who is behind or beside them. World positions stay as the shot states them. Then one sentence per fact: body orientation, which body part meets which body part, current clothing, and what supports the body. Each fact once. Name each person by their trigger token when one is filled, otherwise by their name. No lighting.>

FRAME LAYOUT:
<screen position only, naming each person by their trigger token when one is filled, otherwise by their name: image-left/image-right/center, foreground/midground/background. No pose, action, or contact.>

CAMERA:
<one concise natural English sentence: POV, viewing angle, framing, and scale only>

LIGHT:
<one concise natural English sentence: the established light source, direction, and visible behavior; if no physical source is established, describe only the visible lighting quality and behavior without inventing a new source>

IDENTITY LOCK:
<one or more short sentences per person in visible_people, appearance only: gender, age, nationality, body shape, face, skin, marks, hair (colour and style), and the full outfit worn now. Nothing about pose, action, place, or contact.>

LOCATION LOCK:
<the place and sub-area only. No pose or action.>

OBJECT LOCK:
<one or more short natural English sentences, only when CURRENT B SHOT.objects is non-empty, describing each object's identity, state, and ownership. No pose.>

IMPORTANT — TRIGGER NAMES: CURRENT B SHOT and OBJECT REFERENCE call people by name, but the final prompt must not use them as identity. For every visible person whose <charN.trigger> is filled, write the trigger token wherever their full name or an alias would identify them, in EVERY block: first of all in MAIN IMAGE DESCRIPTION, then FRAME LAYOUT, IDENTITY LOCK and OBJECT LOCK (e.g. an object's owner) — naturally, inside a sentence, never as a label. Ordinary words that merely occur inside a name stay allowed (for a person named "Rose Red", the word "red" in "a red dress" is fine). Before answering, check every block — MAIN IMAGE DESCRIPTION, FRAME LAYOUT, CAMERA, LIGHT, IDENTITY LOCK, LOCATION LOCK, OBJECT LOCK — that no triggered person's name or alias appears as an identity. Also verify every visible person against CHARACTERS: if gender is supplied it appears explicitly in IDENTITY LOCK, and if age or age range is supplied it appears explicitly there too — never omit these because they seem obvious, unrelated to the action, or implied by appearance (CURRENT B SHOT's visible_people and shot_desc use the name; you write the trigger), and that FRAME LAYOUT, IDENTITY LOCK, LOCATION LOCK, and OBJECT LOCK all read as natural sentences with no "trigger:", "Name:", or comma-only attribute list.

When a character's trigger value is empty, use only their canonical name. Do not invent a token, substitute another character's identifier, or mention trigger status in the rendered prompt.

FINAL CHECK, silent, fix the prompt before you output it: MAIN opens with the view and states each pose and contact fact once, with no second wording. CAMERA uses that view. Every person in visible_people is inside the frame. Each person has one head on the neck, two arms, and two legs, and no extra limb. Skin has natural colour and texture, with no patches or markings the shot does not state. Each body rests on the surface that supports it, and no body part passes through furniture, the floor, or another body except at the contact the shot states. Where bodies touch, the contact is drawn at the body parts the shot names, each part where a real body has it. If the shot names no part, keep the action as the shot states it and add no contact point; if it names an impossible one, draw the contact where a real body would make it. Every person whose trigger is filled is named by the trigger token in every block, MAIN IMAGE DESCRIPTION and FRAME LAYOUT included, never by name or alias (the name in CURRENT B SHOT only tells you which person it is). If any of that is missing, rewrite the prompt before answering."""

def _parent_macro_text(a1: dict, micro_beats: list[dict], shot: dict) -> str:
    """Exact ID lookup only."""
    macros = {str(e.get("id") or ""): e for e in (a1.get("macro_events") or []) if isinstance(e, dict)}
    parents = {str(m.get("id") or ""): str(m.get("parent") or "")
               for m in (micro_beats or []) if isinstance(m, dict)}
    wanted: list[str] = []
    for use in (shot.get("uses") or []):
        uid = str(use or "")
        mid = uid if uid in macros else parents.get(uid, "")
        if mid in macros and mid not in wanted:
            wanted.append(mid)
    return "\n".join(f"{mid}: {macros[mid].get('content', '')}" for mid in wanted)


def _production_reference(a1: dict, *, props_desc: str, image_style: str) -> str:
    parts = [
        f"CHARACTERS:\n{a1.get('character_sheet', '')}",
        f"WORLD:\n{a1.get('world_sheet', '')}",
        f"LOCATIONS:\n{a1.get('locations', '')}",
    ]
    object_ref = (props_desc or "").strip() or str(a1.get("important_objects") or "").strip()
    if object_ref:
        parts.append("OBJECT REFERENCE (appearance only; B decides presence):\n" + object_ref)
    if (image_style or "").strip():
        parts.append("STYLE:\n" + image_style.strip())
    return "\n\n".join(parts)


def _shot_payload(shot: dict) -> str:
    # B-SHOT-DESC-SINGLE-TRUTH: B no longer emits separate location/clothing fields — their
    # truth already lives in shot_desc's Clothing/Appearance and World Grounding tags.
    fields = {
        "shot": shot.get("shot"),
        "uses": shot.get("uses") or [],
        "visible_people": shot.get("visible_people", ""),
        "shot_desc": shot.get("shot_desc", ""),
        "story": shot.get("story", ""),
        "objects": shot.get("objects", ""),
        "narration": shot.get("narration", ""),
        "speech_char": shot.get("speech_char", ""),
        "subtitle": shot.get("subtitle", ""),
    }
    return json.dumps(fields, ensure_ascii=False, indent=2)


def _output_shape_note(objects_nonempty: bool) -> str:
    tail = (
        "\n\nOBJECT LOCK:\n<one or more short natural English sentences describing each current object's "
        "identity, state, and ownership. No pose.>"
        if objects_nonempty else ""
    )
    return (
        "Return exactly this block structure and nothing else:\n"
        "MAIN IMAGE DESCRIPTION:\n<First sentence: the one camera chosen from this shot's emotion, whose front or back it sees and who is behind or beside them. World positions stay as the shot states them. Then one sentence per fact: body orientation, which body part meets which body part, current clothing, and what supports the body. Each fact once. Name each visible person by their trigger token or canonical name. No lighting.>\n\n"
        "FRAME LAYOUT:\n<screen position only, naming each visible person by their trigger token or canonical name: image-left/image-right/center, foreground/midground/background. No pose, action, or contact.>\n\n"
        "CAMERA:\n<one concise natural English sentence: POV, viewing angle, framing, and scale only>\n\n"
        "LIGHT:\n<one concise natural English sentence: the established light source, direction, and visible behavior; if no physical source is established, describe only the visible lighting quality and behavior without inventing a new source>\n\n"
        "IDENTITY LOCK:\n<one or more short sentences per person in visible_people, appearance only: gender, age, nationality, body shape, face, skin, marks, hair (colour and style), and the full outfit worn now. Nothing about pose, action, place, or contact.>\n\n"
        "LOCATION LOCK:\n<the place and sub-area only. No pose or action.>"
        + tail
        + "\n\nBefore answering: in every block, including object descriptions such as \"X's lips\", "
        "replace each triggered person's name or alias, when used as their identity, with their trigger "
        "(ordinary words that merely occur inside a name, like \"red\" in \"Rose Red\", stay allowed). "
        "FRAME LAYOUT, IDENTITY LOCK, LOCATION LOCK, and OBJECT LOCK must all read as normal spoken sentences — "
        "no \"trigger:\", no \"Name:\", no comma-only attribute list, and no coordinate numbers or percentages "
        "in FRAME LAYOUT."
    )


_FRAME_LAYOUT_RE = re.compile(r"(?m)^FRAME LAYOUT:\s*\n(.*?)(?=\n[A-Z][A-Z ]*:\s*\n|\Z)", re.S)


def _extract_frame_layout(prompt: str) -> str:
    """Structural-only extraction of the FRAME LAYOUT block text from a previously generated prompt,
    by header alone. Never interprets left/right/foreground meaning — that stays LLM-owned."""
    m = _FRAME_LAYOUT_RE.search(prompt or "")
    return m.group(1).strip() if m else ""


_CAMERA_RE = re.compile(r"(?m)^CAMERA:\s*\n(.*?)(?=\n[A-Z][A-Z ]*:\s*\n|\Z)", re.S)
_BODY_BLOCK_RE = re.compile(r"\(\s*Body\s*:(.*?)\)\s*(?=\(\s*Appearance\s*:|\Z)", re.S)


def _extract_camera(prompt: str) -> str:
    """Structural-only extraction of the CAMERA block text from a previously generated prompt, by header alone."""
    m = _CAMERA_RE.search(prompt or "")
    return m.group(1).strip() if m else ""


def _extract_body_block(shot_desc: str) -> str:
    """The text of the (Body: ...) block of a B shot_desc, by block name only (nothing is interpreted)."""
    m = _BODY_BLOCK_RE.search(shot_desc or "")
    return m.group(1).strip() if m else ""


def _build_local_user_prompt(shot: dict, a1: dict, *, image_style: str, props_desc: str,
                             visual_direction: str, micro_beats: list[dict],
                             previous_frame_layout: str = "", previous_camera: str = "", previous_pose: str = "") -> str:
    parent = _parent_macro_text(a1, micro_beats, shot)
    parts = [
        _production_reference(a1, props_desc=props_desc, image_style=image_style),
        visual_direction,
    ]
    if parent:
        parts.append("PARENT STORY EVENT:\n" + parent)
    if previous_frame_layout:
        parts.append(
            "PREVIOUS FRAME LAYOUT — established screen-space continuity:\n" + previous_frame_layout + "\n\n"
            "Treat this as the starting screen-space arrangement for CURRENT B SHOT. Carry its established "
            "screen-space relationships forward unless CURRENT B SHOT requires them to change. It is continuity "
            "evidence only: do not inherit previous action, pose, clothing/object state, camera, lighting, or "
            "emotion. CURRENT B SHOT remains authoritative for what changes now."
        )
    if previous_camera and previous_pose:
        parts.append(
            "PREVIOUS SHOT POSE (from its B shot): " + previous_pose + "\n"
            "PREVIOUS CAMERA: " + previous_camera + "\n\n"
            "CAMERA CHECK: " + _CAMERA_FROM_EMOTION + " This changes only CAMERA: the pose, people, objects and layout stay exactly as CURRENT B SHOT says."
        )
    parts.append("CURRENT B SHOT:\n" + _shot_payload(shot))
    parts.append(_output_shape_note(bool(str(shot.get("objects") or "").strip())))
    return "\n\n".join(parts)


def _b_sequence_text(b_sequence: list[dict]) -> str:
    return "\n".join(_shot_payload(s) for s in (b_sequence or []) if isinstance(s, dict))


def _build_batch_user_prompt(shots: list[dict], a1: dict, *, image_style: str, props_desc: str,
                             visual_direction: str,
                             micro_beats: list[dict], story_text: str,
                             b_sequence: list[dict]) -> str:
    target_numbers = [int(s.get("shot")) if isinstance(s.get("shot"), int) else i + 1
                      for i, s in enumerate(shots)]
    targets = "\n\n".join("TARGET B SHOT:\n" + _shot_payload(s) for s in shots)
    parts = [
        _production_reference(a1, props_desc=props_desc, image_style=image_style),
        visual_direction,
        "B SEQUENCE CONTEXT supplies continuity/progression only. Each TARGET B SHOT is authoritative for its "
        "own image. Carry established FRAME LAYOUT screen-space relationships forward until the current TARGET "
        "requires them to change; camera/framing may vary without resetting those relationships. Never import "
        "an action, person, object, clothing/body state, location condition, environmental motion, or light "
        "source from another shot unless the current TARGET itself supports it. " + _CAMERA_FROM_EMOTION + "\n"
        "B SEQUENCE CONTEXT:\n" + _b_sequence_text(b_sequence),
    ]
    # story_text (full story) is intentionally not sent: each shot's own `story` field replaces it.
    parts.append(targets)
    parts.append(
        "Return JSON only:\n"
        '{"shots": [{"shot": <target shot number>, "prompt": "<complete final prompt>"}]}\n'
        f"Return exactly one item for each target shot number: {target_numbers}. "
        "Inside each prompt use the same required block structure: MAIN IMAGE DESCRIPTION, FRAME LAYOUT, "
        "CAMERA, LIGHT, IDENTITY LOCK, LOCATION LOCK, plus OBJECT LOCK only when the current shot has objects."
    )
    return "\n\n".join(parts)


def _local_max_tokens(shot: dict) -> int:
    size = len(str(shot.get("shot_desc") or "")) + len(str(shot.get("visible_people") or ""))
    return max(1800, min(5000, size + 1600))


def _batch_max_tokens(shots: list[dict]) -> int:
    return max(5000, min(32000, len(shots) * 2200 + 2500))


async def _call_local_text(wf_template: dict, system_prompt: str, user_prompt: str, *,
                           max_tokens: int, session_id: str, on_log=None, n_ctx: int | None = None, timeout: int | None = None) -> str | None:
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
        _overrides.apply(inputs, n_ctx=int(n_ctx) if n_ctx else None)      # per-call copy of the workflow: the shared template is not modified; Workflow mode keeps the workflow's n_ctx
        hit = True
    if not hit:
        if on_log:
            await on_log("❌ Image Prompts: workflow has no LLMSessionChatNode")
        return None

    if on_log:
        await on_log(f"📋 NORMAL RENDER PROMPT SYSTEM:\n{system_prompt}")
        await on_log(f"📤 NORMAL RENDER PROMPT USER:\n{user_prompt}")

    loop = asyncio.get_event_loop()
    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, uuid.uuid4().hex)
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Image Prompts queue: {exc}")
        return None

    deadline = time.time() + (timeout or _LLM_TIMEOUT)
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
                        await on_log(f"📥 NORMAL RENDER PROMPT RAW:\n{text}")
                    return text
        status = ((entry.get("status") or {}).get("status_str") or "").lower()
        if status in ("success", "error"):
            break
    if on_log:
        await on_log("❌ Image Prompts: local LLM returned no text")
    return None


async def _call_grok_text(system_prompt: str, user_prompt: str, *, api_key: str,
                          model: str, max_tokens: int, on_log=None) -> str | None:
    if not api_key:
        if on_log:
            await on_log("❌ Image Prompts: Grok API key is empty")
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
        await on_log(f"📋 NORMAL RENDER PROMPT SYSTEM:\n{system_prompt}")
        await on_log(f"📤 NORMAL RENDER PROMPT USER:\n{user_prompt}")

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
                    await on_log(f"❌ Image Prompts Grok retry: {retry_exc}")
                return None
        else:
            if on_log:
                await on_log(f"❌ Image Prompts Grok HTTP {exc.code}: {body[:500]}")
            return None
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Image Prompts Grok: {exc}")
        return None

    choices = response.get("choices") if isinstance(response, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else {}
    message = first.get("message") if isinstance(first, dict) else {}
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str):
        if on_log:
            await on_log("❌ Image Prompts: Grok response has no text content")
        return None
    if on_log:
        await on_log(f"📥 NORMAL RENDER PROMPT RAW:\n{text}")
    return text


def _parse_batch_json(raw: str | None, target_numbers: list[int]) -> dict[int, str] | None:
    """Structural batch parser only; prompt strings are returned unchanged."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        obj = json.loads(_strip_code_fence(raw))
    except Exception:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("shots"), list):
        return None
    if len(obj["shots"]) != len(target_numbers):
        return None
    out: dict[int, str] = {}
    allowed = set(target_numbers)
    for item in obj["shots"]:
        if not isinstance(item, dict):
            return None
        num = item.get("shot")
        prompt = item.get("prompt")
        if not isinstance(num, int) or num not in allowed or num in out:
            return None
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        out[num] = prompt
    return out if set(out) == allowed else None


async def run_fill_vnext2(shots: list[dict], a1: dict, llm_wf_file: str, on_err, on_log=None,
                          image_style: str = "", genre: str = "", genre2: str = "",
                          grok_api_key: str = "", grok_model: str = "grok-4-latest",
                          on_shot=None, props_desc: str = "", visual_direction: str = "",
                          stop_event: asyncio.Event | None = None,
                          micro_beats: list[dict] | None = None, story_text: str = "",
                          b_sequence: list[dict] | None = None) -> list[dict]:
    """Clean Fill. Python commits non-empty model-authored prompts without semantic editing."""
    micro_beats = micro_beats or []
    total = len(shots)
    # VISUAL DIRECTION is resolved in Python from the shared Visual Preset (no LLM); default = Natural.
    visual_direction = (visual_direction or "").strip() or visual_direction_for_preset("")
    system_prompt = _FILL_SYSTEM_PROMPT

    if llm_wf_file == GROK_MARKER:
        if stop_event and stop_event.is_set():
            return shots
        if _FILL_CONVERSATION and _FILL_CONVERSATION_GROK:
            from .fill_conversation import run_fill_conversation_grok
            return await run_fill_conversation_grok(
                sys.modules[__name__], shots, a1, on_err, on_log, api_key=grok_api_key, model=grok_model, image_style=image_style, props_desc=props_desc,
                visual_direction=visual_direction, micro_beats=micro_beats, on_shot=on_shot, stop_event=stop_event)
        target_numbers = [s.get("shot") if isinstance(s.get("shot"), int) else i + 1
                          for i, s in enumerate(shots)]
        user_prompt = _build_batch_user_prompt(
            shots, a1, image_style=image_style, props_desc=props_desc,
            visual_direction=visual_direction,
            micro_beats=micro_beats, story_text=story_text,
            b_sequence=b_sequence if b_sequence is not None else shots,
        )
        raw = await _call_grok_text(
            system_prompt, user_prompt,
            api_key=grok_api_key, model=grok_model,
            max_tokens=_batch_max_tokens(shots), on_log=on_log,
        )
        parsed = _parse_batch_json(raw, target_numbers)
        if parsed is None:
            await on_err("Image Prompts: Grok batch did not return the required JSON structure")
            for idx, shot in enumerate(shots):
                shot["_fill_failed"] = True
                if on_shot:
                    await on_shot(idx + 1, total)
            return shots

        for idx, shot in enumerate(shots):
            if stop_event and stop_event.is_set():
                break
            num = target_numbers[idx]
            prompt = parsed[num]
            shot["prompt"] = prompt
            shot["_fill_engine"] = FILL_ENGINE_VNEXT2
            shot["prompt_style"] = FILL_ENGINE_VNEXT2
            shot.pop("_fill_session_id", None)
            shot.pop("_fill_failed", None)
            shot.pop("_fill_renderer_ready", None)
            if on_log:
                await on_log(f"NORMAL RENDER PROMPT shot={num} prompt_len={len(prompt)}")
            if on_shot:
                await on_shot(idx + 1, total)
        return shots

    if not (llm_wf_file or "").strip():
        await on_err("Image Prompts: no local LLM workflow selected")
        return shots
    wf_path = LLM_DIR / f"{llm_wf_file}.json"
    if not wf_path.exists():
        await on_err(f"ไม่พบ {wf_path}")
        return shots
    try:
        wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
    except Exception as exc:
        await on_err(f"Image Prompts workflow read failed: {exc}")
        return shots

    if _FILL_CONVERSATION:
        from .fill_conversation import run_fill_conversation
        return await run_fill_conversation(
            sys.modules[__name__], shots, a1, wf_template, on_err, on_log, image_style=image_style, props_desc=props_desc, visual_direction=visual_direction,
            micro_beats=micro_beats, on_shot=on_shot, stop_event=stop_event)

    if _LOCAL_ONE_CALL and total > 1 and not (stop_event and stop_event.is_set()):
        target_numbers = [s.get("shot") if isinstance(s.get("shot"), int) else i + 1 for i, s in enumerate(shots)]
        user_prompt = _build_batch_user_prompt(
            shots, a1, image_style=image_style, props_desc=props_desc, visual_direction=visual_direction,
            micro_beats=micro_beats, story_text=story_text, b_sequence=b_sequence if b_sequence is not None else shots)
        out_tokens = min(_batch_max_tokens(shots), 32768)
        need = int((len(system_prompt) + len(user_prompt)) / 1.8) + out_tokens + 512         # prompt tokens (rough chars/1.8) + the answer + margin
        if need > _LOCAL_ONE_CALL_N_CTX:
            if on_log:
                await on_log(f"ℹ Image Prompts: one-call local Fill needs about {need} tokens > n_ctx {_LOCAL_ONE_CALL_N_CTX}; using the per-shot loop")
        else:
            if on_log:
                await on_log(f"ℹ Image Prompts: ONE local call for {total} shots (n_ctx {_LOCAL_ONE_CALL_N_CTX}, about {need} tokens)")
            raw = await _call_local_text(wf_template, system_prompt, user_prompt, max_tokens=out_tokens, session_id=uuid.uuid4().hex[:8], on_log=on_log,
                                         n_ctx=_LOCAL_ONE_CALL_N_CTX, timeout=_LOCAL_ONE_CALL_TIMEOUT)
            parsed = _parse_batch_json(raw, target_numbers)
            if parsed is not None:
                for idx, shot in enumerate(shots):
                    num = target_numbers[idx]
                    prompt = parsed[num]
                    shot["prompt"] = prompt
                    shot["_fill_engine"] = FILL_ENGINE_VNEXT2
                    shot["prompt_style"] = FILL_ENGINE_VNEXT2
                    shot.pop("_fill_session_id", None)
                    shot.pop("_fill_failed", None)
                    shot.pop("_fill_renderer_ready", None)
                    if on_log:
                        await on_log(f"NORMAL RENDER PROMPT shot={num} prompt_len={len(prompt)} (one call)")
                    if on_shot:
                        await on_shot(idx + 1, total)
                return shots
            if on_log:
                await on_log("⚠ Image Prompts: the one-call local answer was not the required JSON for every shot; using the per-shot loop")

    previous_frame_layout = ""
    previous_camera = ""
    previous_pose = ""
    for idx, shot in enumerate(shots):
        if stop_event and stop_event.is_set():
            if on_log:
                await on_log(f"⏹ Stop Image Prompts after {idx}/{total} shots")
            break
        previous = shot.get("prompt", "")
        user_prompt = _build_local_user_prompt(
            shot, a1, image_style=image_style, props_desc=props_desc,
            visual_direction=visual_direction, micro_beats=micro_beats,
            previous_frame_layout=previous_frame_layout,
            previous_camera=previous_camera, previous_pose=previous_pose,
        )
        session_id = uuid.uuid4().hex[:8]
        raw = await _call_local_text(
            wf_template, system_prompt, user_prompt,
            max_tokens=_local_max_tokens(shot), session_id=session_id, on_log=on_log,
        )
        prompt = raw.strip() if isinstance(raw, str) else ""
        if prompt:
            shot["prompt"] = prompt
            shot["_fill_engine"] = FILL_ENGINE_VNEXT2
            shot["prompt_style"] = FILL_ENGINE_VNEXT2
            shot["_fill_session_id"] = session_id
            shot.pop("_fill_failed", None)
            previous_frame_layout = _extract_frame_layout(prompt) or previous_frame_layout
            previous_camera = _extract_camera(prompt) or previous_camera
            previous_pose = _extract_body_block(shot.get("shot_desc", "")) or previous_pose
            shot.pop("_fill_renderer_ready", None)
            commit = "NEW"
        else:
            shot["_fill_failed"] = True
            commit = "KEEP_OLD" if str(previous or "").strip() else "EMPTY"
        if on_log:
            await on_log(f"NORMAL RENDER PROMPT shot={shot.get('shot')} commit={commit} prompt_len={len(prompt)}")
        if on_shot:
            await on_shot(idx + 1, total)
    return shots


# Canonical name — Image Prompts (legacy: Fill Normal). The legacy entry point above stays for compatibility; run_normal_render_prompt is the same function.
run_normal_render_prompt = run_fill_vnext2
