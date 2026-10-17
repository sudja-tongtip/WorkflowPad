"""
llm_engine_a2.py — Beat Sheet engine (legacy name: A2).

Beat Sheet divides macro events into ordered narrative progression, including reached states.

Design rule:
    LLM = semantic story understanding.
    Python = transport + explicit JSON structure only.

Beat Sheet receives the Story Bible's macro events and breaks them into smaller
ordered story events for Shot List. Python never decides what an event means, never repairs story content,
and never rewrites model-authored semantic fields.
"""

__version__ = "20261028_a2_lia_it1"

import asyncio
import copy
import json
import re
import time
import urllib.error
import urllib.request
import uuid

from .constants import LLM_DIR, lang_prompt_name
from .comfy_api import comfy_queue, comfy_history


_CODE_FENCE_RE = re.compile(r"^```(?:json|xml)?\s*|```\s*$")


def _strip_code_fence(raw: str) -> str:
    """Transport-only cleanup: some models wrap output in ```/```json/```xml fences. Strip fence
    markers only — never touch the content itself."""
    return _CODE_FENCE_RE.sub("", raw.strip()).strip()


# A2-TAG-FORMAT-A1: micro_beats are transported as one flat tag per unit, <id>content</id> — no
# JSON, no quote-escaping, multi-line content allowed. The tag name IS the id (e.g. <E1.1>), so
# there is exactly one open/close pair per unit — no nesting, nothing to balance. Parsed by exact
# string match on the id via this regex, never a real XML parser, so a literal <, >, &, or quote
# inside the content is harmless — it's just text between two known markers.
_TAG_RE = re.compile(r"<([A-Za-z0-9_.]+)>(.*?)</\1>", re.DOTALL)


def _extract_micro_beats(raw: str) -> list[dict]:
    """Extract every complete <id>content</id> pair, in the order they appear. Duplicate ids keep
    only the first occurrence. Naturally truncation-tolerant: an incomplete trailing tag simply
    produces no match, so this never raises and never needs a separate salvage codepath."""
    out: list[dict] = []
    seen: set[str] = set()
    for m in _TAG_RE.finditer(raw):
        tag_id = m.group(1)
        if "." not in tag_id or tag_id in seen:
            continue
        content = m.group(2).strip()
        if not content:
            continue
        seen.add(tag_id)
        out.append({"id": tag_id, "parent": tag_id.rsplit(".", 1)[0], "content": content})
    return out


GROK_MARKER = "[Grok API]"
_LLM_TIMEOUT = 120
_GROK_TEMPERATURE = 0.35
_GROK_REASONING_EFFORT = "low"


_A2_SYSTEM_PROMPT = """You are the second story editor in a visual-story pipeline. Your primary function is to analyze macro events provided by the first editor and distill them into sequential, meaningful story beats suitable for a storyboard planner.

CORE PRINCIPLES:
1. Each macro event from the previous editor is story truth. Never add new narrative elements; only segment existing ones.
2. Identify the most meaningful, chronologically ordered moments within the macro event.
3. Never reverse, relocate, or reinterpret events. Maintain strict chronological flow.
4. Do not move material across macro events or design shots/camera.
5. Every beat holds the facts of its phase - the story meaning in HAPPENS and the physical facts in DETAILS - without losing any fact from the source material.
6. When the source explicitly establishes a new resulting state, preserve that resulting state in the micro-events. Do not replace a concrete state with only a symptom, mood, pose, or nearby consequence. Preserve only what the source actually establishes — never infer a state the source does not establish.

BEAT DEFINITION AND GROUPING (crucial):
A micro beat = one coherent narrative action or meaningful visual change. A beat is one continuous, uninterrupted phase. Combine adjacent material into the same beat whenever it constitutes one continuous, instantaneous moment: if action B is the direct, immediate result of decision A, or one exchange runs from question to final reply, they belong together unless a real time gap separates them. When in doubt, prioritize merging an action with its immediate reaction, or a decision with its immediate consequence — do not hesitate to merge on emotional or narrative continuity. Split only when the story enters a distinct narrative phase that cannot be faithfully represented within the same coherent beat. An emotional change alone does not require a separate beat, and neither does a change of sentence, clause, or speaker. A decision and the action that expresses it, and a posture or facial expression that accompanies one action, belong in the same beat. A macro event usually has only a few such beats; the selected mode controls how broadly or finely they are grouped.

SUPPORTING CONTEXT IS NOT A BEAT: the following are never a reason to create a new beat - appearance and clothing that do not change; a pose or position that stays the same; lighting and background; a description of mood with no new event; a small shift of gaze or posture that belongs to the same response. Thoughts, memories, feelings, motives and imagined consequences are narrative context too: keep their meaning in the HAPPENS of one beat, however many sentences they take - never one beat per thought - and never turn them into a face, gesture or movement the macro does not state. A person who appears only in someone's thought is not in the scene. A story's importance is not a reason for a separate picture. Fold all of this into the beat it belongs to. When an action and its reaction run on without a significant turning point, make them ONE beat.
But keep the order and every fact: never merge beats so that a stated change disappears - a change of clothing or bodily state the source states, a change of who holds an object, a character entering or leaving, a change of place, a touch or interaction that matters to the story, or an outcome that actually happens.

TIME: keep the macro's temporal meaning exactly. "is doing" stays ongoing; "is about to" / "reaches out to" has NOT happened yet (never write it as done or as contact already made); "finished" is finished; "while" / "as" / "meanwhile" mean actions that overlap in time - they belong in ONE beat, even when they belong to different characters; "after" / "then" are a sequence; "until" is a turning point; "still" means the earlier state continues. Never change a temporal word to make the text shorter. An event and its immediate reaction (a door opens and she steps forward) belong together unless a real pause separates them.

BEAT FORMAT (every beat has two labelled lines inside its tag):
HAPPENS: what is happening in this beat NOW, in one short sentence, with the characters and actions the macro joins in this moment. Emotional or meaning language is allowed only where the macro itself states it. Never add a stance, purpose, motive or feeling the macro does not state (not "in a defensive stance", "to announce something", "to rest"), and never a new event.
DETAILS: the physical and visual truth of the same moment, exactly as the macro states it: who does what; which body part moves, only if the source says; which hand holds or releases which object, only if the source says (never add a side the source does not give); how each person stands, sits or moves; who touches whom; where objects are and how their state changes; what happens to clothing or the body. Never replace a stated fact with vague wording such as "changes posture", "moves slightly", "does something", "they interact" or "the object moves", and never add precision the macro does not give. Write only the facts of this moment. Always keep every action, hand and object the macro states for this moment (a sword she is holding, a sash she is adjusting), even when the PRODUCTION REFERENCE also lists it; do not recite the reference's clothing, hair or place. Appearance alone never creates a beat. Put meaning (inviting, yielding, a secret) in HAPPENS, never in DETAILS. Where the macro is ambiguous (which hand, whose palm, who moves) copy its own wording and do not decide; "as if" and "seemingly" are comparisons, not facts; "almost touches" is not touching; hesitating between two options is not choosing one.
Continuity: carry object holders, hand occupancy, clothing state, body position, contact and location faithfully. A held object that is later released keeps that release in the beat where the macro states it; never create a release, put-down, pick-up or change of state the macro does not state.

NARRATIVE FLOW:
In every beat, explicitly name each character performing or
receiving an action using the exact name from CHARACTERS.
Keep actions, body parts, and positions attached to the
correct character. Do not use pronouns in place of character
names when describing actions or interactions.

Treat PRODUCTION REFERENCE as supporting context
(identity, established state) only, never as the source
of new actions or narrative details.

EXAMPLE — too split (avoid this): a source macro with a repeated question, an offer, a repeated question again with an environmental reason, and a final question is turned into 8 beats, one per sentence (question alone, answer alone, offer alone, response alone, repeated question alone, weather alone, follow-up question alone, confirmation alone).

EXAMPLE — correct compactness for the SAME macro: 4 beats. Beat 1 merges the question and its direct answer. Beat 2 merges the offer and the immediate response to it. Beat 3 merges the repeated question with the environmental reason that motivates the next question. Beat 4 merges the final question with its confirmation. Each beat is the question/offer together with its own immediate answer/reaction — never split apart.

Return only the requested tags."""


def _mode_instruction(mode: str) -> str:
    if (mode or "").strip().lower() == "sequential":
        return ("Sequential mode: preserve meaningful chronological progression and distinct state changes. Group a decision "
                "with its immediate physical expression, and group supporting gestures or expressions with the action they belong to. "
                "Create a separate beat only when a new narrative phase, consequential action, or distinct resulting state needs "
                "independent representation. Never split merely to show intermediate motions.")
    return ("Visual Novel mode: optimize for extremely compact drawable beats. One beat should equal one "
            "drawable dramatic moment. If adjacent actions, dialogue, and immediate reactions happen in the "
            "same continuous moment and can be shown in one image, group them into one beat. Preserve "
            "chronology inside the beat whenever possible instead of splitting every step.")


def _reference_text(a1: dict) -> str:
    return (
        f"CHARACTERS:\n{a1.get('character_sheet', '')}\n\n"
        f"WORLD:\n{a1.get('world_sheet', '')}\n\n"
        f"LOCATIONS:\n{a1.get('locations', '')}\n\n"
        f"IMPORTANT OBJECTS:\n{a1.get('important_objects', '')}\n\n"
        f"CONTINUITY:\n{a1.get('continuity_notes', '')}"
    )


def _build_whole_story_user_prompt(a1: dict, *, mode: str, desc_lang: str) -> str:
    lang_label = lang_prompt_name(desc_lang or "en")
    events = "\n".join(
        f"{e.get('id')}: {e.get('content', '')}" for e in (a1.get("macro_events") or [])
    )
    return (
        f"Output language: {lang_label}\n"
        f"{_mode_instruction(mode)}\n\n"
        f"PRODUCTION REFERENCE\n{_reference_text(a1)}\n\n"
        f"MACRO EVENTS\n{events}\n\n"
        "OUTPUT FORMAT:\n"
        "Return one tag per micro-event, in this exact form, nothing else:\n"
        "<E1.1>\nHAPPENS: one short sentence of narrative meaning\nDETAILS: the concrete physical facts of this moment\n</E1.1>\n"
        "Represent every macro event in order. Each macro uses its own parent id: E1.1 (then E1.2... only if needed) under E1; "
        "E2.1... under E2; E3.1... under E3, and so on. "
        "One micro beat is valid when the macro contains one coherent dramatic moment. Create additional beats only when the source contains distinct meaningful phases. "
        "Write each beat in the two labelled lines HAPPENS (one short sentence of narrative meaning) and DETAILS (the concrete physical facts of that moment, one or two sentences, only what the source states). Each tag's content is plain prose — write it exactly as the story needs, including any "
        "quotation marks, punctuation, or line breaks; there is no JSON and nothing to escape."
    )


def _build_one_macro_user_prompt(a1: dict, macro: dict, *, mode: str, desc_lang: str) -> str:
    lang_label = lang_prompt_name(desc_lang or "en")
    mid = str(macro.get("id") or "")
    return (
        f"Output language: {lang_label}\n"
        f"{_mode_instruction(mode)}\n\n"
        f"PRODUCTION REFERENCE\n{_reference_text(a1)}\n\n"
        f"TARGET MACRO EVENT\n{mid}: {macro.get('content', '')}\n\n"
        "Identify the story beats of this target macro event, in order.\n"
        "OUTPUT FORMAT:\n"
        "Return one tag per micro-event for this macro event, in this exact form, nothing else:\n"
        f"<{mid}.1>\nHAPPENS: one short sentence of narrative meaning\nDETAILS: the concrete physical facts of this moment\n</{mid}.1>\n"
        "One micro beat is valid when the macro contains one coherent dramatic moment. Create additional beats only when the source contains distinct meaningful phases. "
        f"IDs use the form {mid}.1 (then {mid}.2... only if needed) in order. Write each beat in the two labelled lines HAPPENS (one short sentence of narrative meaning) and DETAILS (the concrete physical facts of that moment, one or two sentences, only what the source states). Each tag's content is plain prose — write "
        "it exactly as the story needs, including any quotation marks, punctuation, or line "
        "breaks; there is no JSON and nothing to escape."
    )


def _a2_max_tokens_for_macro(text: str) -> int:
    return max(2200, min(7000, len(text or "") + 1600))


def _a2_max_tokens_whole(a1: dict) -> int:
    size = sum(len(str(e.get("content") or "")) for e in (a1.get("macro_events") or []))
    return max(5000, min(20000, size + 3500))


async def _call_local_text(wf_template: dict, system_prompt: str, user_prompt: str, *,
                           max_tokens: int, session_id: str, on_log=None) -> str | None:
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
            await on_log("❌ Beat Sheet: workflow has no LLMSessionChatNode")
        return None

    if on_log:
        await on_log(f"📋 Beat Sheet SYSTEM:\n{system_prompt}")
        await on_log(f"📤 Beat Sheet USER:\n{user_prompt}")

    loop = asyncio.get_event_loop()
    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, uuid.uuid4().hex)
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Beat Sheet queue: {exc}")
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
                        await on_log(f"📥 Beat Sheet RAW:\n{text}")
                    return text
        status = ((entry.get("status") or {}).get("status_str") or "").lower()
        if status in ("success", "error"):
            break
    if on_log:
        await on_log("❌ Beat Sheet: local LLM returned no text")
    return None


async def _call_grok_text(system_prompt: str, user_prompt: str, *, api_key: str,
                          model: str, max_tokens: int, on_log=None) -> str | None:
    if not api_key:
        if on_log:
            await on_log("❌ Beat Sheet: Grok API key is empty")
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
        await on_log(f"📋 Beat Sheet SYSTEM:\n{system_prompt}")
        await on_log(f"📤 Beat Sheet USER:\n{user_prompt}")

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
                    await on_log(f"❌ Beat Sheet Grok retry: {retry_exc}")
                return None
        else:
            if on_log:
                await on_log(f"❌ Beat Sheet Grok HTTP {exc.code}: {body[:500]}")
            return None
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Beat Sheet Grok: {exc}")
        return None

    choices = response.get("choices") if isinstance(response, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else {}
    message = first.get("message") if isinstance(first, dict) else {}
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str):
        if on_log:
            await on_log("❌ Beat Sheet: Grok response has no text content")
        return None
    if on_log:
        await on_log(f"📥 Beat Sheet RAW:\n{text}")
    return text


def _parse_micro_tags(raw: str | None, *, allowed_parents: set[str],
                      require_parents: set[str] | None = None) -> list[dict] | None:
    """Structural tag parser only. Model-authored content is preserved verbatim (only outer
    whitespace is trimmed). All-or-nothing when require_parents is set: any macro event missing
    its tags rejects the whole response, same as the old JSON parser's behavior."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    fenced = _strip_code_fence(raw)
    beats = _extract_micro_beats(fenced)
    if not beats:
        return None

    out: list[dict] = []
    seen_parents: set[str] = set()
    for beat in beats:
        if beat["parent"] not in allowed_parents:
            return None
        seen_parents.add(beat["parent"])
        out.append(beat)

    if require_parents is not None and seen_parents != require_parents:
        return None
    return out


async def run_a2_vnext(a1: dict, llm_wf_file: str, on_err, on_log=None,
                        mode: str = "cinematic", genre: str = "", genre2: str = "",
                        desc_lang: str = "en", story_text: str = "",
                        grok_api_key: str = "", grok_model: str = "grok-4-latest",
                        chat_capture: dict | None = None) -> dict | None:
    """Clean A2. Genre/story_text args remain for API compatibility and are not semantically processed."""
    macros = [e for e in (a1.get("macro_events") or [])
              if isinstance(e, dict) and isinstance(e.get("id"), str) and e.get("id").strip()]
    if not macros:
        await on_err("Beat Sheet: Story Bible has no macro events")
        return None
    macro_ids = {e["id"] for e in macros}

    if llm_wf_file == GROK_MARKER:
        user_prompt = _build_whole_story_user_prompt(a1, mode=mode, desc_lang=desc_lang)
        last_raw = ""
        last_user = user_prompt
        for attempt in (1, 2):
            if attempt == 1:
                sent = user_prompt
            else:
                _got = {b["parent"] for b in _extract_micro_beats(_strip_code_fence(last_raw))}
                _missing = sorted(macro_ids - _got)
                sent = user_prompt + (
                    "\n\nThe previous response was missing or malformed tags for macro event "
                    f"id(s): {', '.join(_missing) or 'unknown'}. Return the complete set of tags, "
                    "covering every macro event in order, in the exact <id>content</id> form."
                )
            last_user = sent
            raw = await _call_grok_text(
                _A2_SYSTEM_PROMPT, sent,
                api_key=grok_api_key, model=grok_model,
                max_tokens=_a2_max_tokens_whole(a1), on_log=on_log,
            )
            last_raw = raw or ""
            beats = _parse_micro_tags(raw, allowed_parents=macro_ids, require_parents=macro_ids)
            if beats is not None:
                if chat_capture is not None:
                    chat_capture["sessions"] = [{
                        "label": "ALL", "turns": [{
                            "system_prompt": _A2_SYSTEM_PROMPT,
                            "user_prompt": sent,
                            "raw_response": raw or "",
                        }],
                        "backend": "grok", "llm_wf_file": llm_wf_file,
                        "session_id": "", "grok_model": grok_model,
                    }]
                return {"micro_beats": beats}
            if on_log:
                await on_log(f"⚠ Beat Sheet Grok attempt {attempt}: missing or malformed tags")
        if chat_capture is not None and last_raw:
            chat_capture["sessions"] = [{
                "label": "ALL", "turns": [{
                    "system_prompt": _A2_SYSTEM_PROMPT,
                    "user_prompt": last_user,
                    "raw_response": last_raw,
                }],
                "backend": "grok", "llm_wf_file": llm_wf_file,
                "session_id": "", "grok_model": grok_model,
            }]
        await on_err("Beat Sheet: Grok did not return the required tags for every macro event after 2 attempts")
        return None

    if not (llm_wf_file or "").strip():
        await on_err("Beat Sheet: no local LLM workflow selected")
        return None
    wf_path = LLM_DIR / f"{llm_wf_file}.json"
    if not wf_path.exists():
        await on_err(f"ไม่พบ {wf_path}")
        return None
    try:
        wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
    except Exception as exc:
        await on_err(f"Beat Sheet workflow read failed: {exc}")
        return None

    all_beats: list[dict] = []
    sessions: list[dict] = []
    for macro in macros:
        mid = macro["id"]
        base_user = _build_one_macro_user_prompt(a1, macro, mode=mode, desc_lang=desc_lang)
        session_id = uuid.uuid4().hex[:8]
        turns: list[dict] = []
        accepted = None
        for attempt in (1, 2):
            sent = base_user if attempt == 1 else base_user + (
                "\n\nThe previous response was missing or malformed tags. Return non-empty "
                f'<{mid}.N>content</{mid}.N> tags covering this macro event only, in the exact '
                "form shown above."
            )
            raw = await _call_local_text(
                wf_template, _A2_SYSTEM_PROMPT, sent,
                max_tokens=_a2_max_tokens_for_macro(str(macro.get("content") or "")),
                session_id=session_id, on_log=on_log,
            )
            turns.append({"system_prompt": _A2_SYSTEM_PROMPT, "user_prompt": sent, "raw_response": raw or ""})
            accepted = _parse_micro_tags(raw, allowed_parents={mid}, require_parents={mid})
            if accepted is not None:
                break
            if on_log:
                await on_log(f"⚠ Beat Sheet {mid} attempt {attempt}: missing or malformed tags")
        if accepted is None:
            await on_err(f"Beat Sheet: macro {mid} did not return the required tags after 2 attempts")
            return None
        all_beats.extend(accepted)
        sessions.append({
            "label": mid, "turns": turns, "backend": "local",
            "llm_wf_file": llm_wf_file, "session_id": session_id, "grok_model": grok_model,
        })

    if chat_capture is not None:
        chat_capture["sessions"] = sessions
    return {"micro_beats": all_beats}


# Canonical name — Beat Sheet (legacy: A2). The legacy entry point above stays for compatibility; run_beat_decomposition is the same function.
run_beat_decomposition = run_a2_vnext
