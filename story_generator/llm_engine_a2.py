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

__version__ = "20261020_a2_short"

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
from .llm_engine_a1_router import _strip_template_tail


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
    """LENIENT extraction, for diagnostics only (which macro events produced any beat at all): every complete <id>content</id> pair, in the order they appear, duplicate ids once. It
    never raises. It is NOT what accepts an answer: see _micro_beat_problems / _parse_micro_tags, which refuse a reply whose structure is wrong."""
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


_A2_SYSTEM_PROMPT = """You are the second story editor. Split each macro into ordered beats. Do not add an action the macro does not contain. Do not design camera shots. Name each character with the exact name from CHARACTERS. Return only the requested tags."""


def _beat_guard() -> str:
    """Short rules placed next to the request. The long system prompt already states them; a small local model follows the nearby lines."""
    return (
        "PICTURE RULES:\n"
        "- One beat is one drawable state or action phase: one picture that shows who is doing what at that point of the macro event. A decision and the action that carries it out "
        "belong to the same beat.\n"
        "- Start a new beat when the macro event moves to a different action phase or a different resulting state (a new action, a change of position, place, who holds what, or who is involved), "
        "in the order the macro event tells them. A look, a breath, a tighter grip or a sigh stays inside the beat of the action it belongs to; it is not a beat of its own.\n"
        "- Do not copy clothing, hair, or an outfit from PRODUCTION REFERENCE. Name clothing only when this macro states it.\n"
        "- Every action and every result this macro states must appear in a beat. If one sentence has two results, keep both. Do not drop the first result and keep only what remains.\n"
        "- If this macro is one frozen moment, return one beat, written as the action that moment is already doing. "
        "Do not add an action the macro does not contain. Do not turn an ongoing action into a finished pose or a person sitting still.\n"
    )


def _mode_instruction(mode: str) -> str:
    if (mode or "").strip().lower() == "sequential":
        return ("Sequential mode: keep the macro's order. Every distinct action phase or resulting state is its own beat; "
                "gestures and expressions that belong to an action stay inside that action's beat.")
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
        f"{_beat_guard()}\n"
        "OUTPUT FORMAT:\n"
        "Return one tag per micro-event, in this exact form, nothing else:\n"
        "<E1.1>\nHAPPENS: who is doing what in this beat\nDETAILS: the body and objects at that point of the action\n</E1.1>\n"
        "Represent every macro event in order. Each macro uses its own parent id: E1.1 (then E1.2... only if needed) under E1; "
        "E2.1... under E2; E3.1... under E3, and so on. "
        "Each tag's content is plain prose — write it exactly as the story needs, including any "
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
        f"{_beat_guard()}\n"
        "OUTPUT FORMAT:\n"
        "Return one tag per micro-event for this macro event, in this exact form, nothing else:\n"
        f"<{mid}.1>\nHAPPENS: who is doing what in this beat\nDETAILS: the body and objects at that point of the action\n</{mid}.1>\n"
        f"IDs use the form {mid}.1 (then {mid}.2... only if needed) in order. Each tag's content is plain prose — write "
        "it exactly as the story needs, including any quotation marks, punctuation, or line "
        "breaks; there is no JSON and nothing to escape."
    )


def _a2_max_tokens_for_macro(text: str) -> int:
    return max(2200, min(7000, len(text or "") + 1600))


def _a2_max_tokens_whole(a1: dict) -> int:
    size = sum(len(str(e.get("content") or "")) for e in (a1.get("macro_events") or []))
    return max(5000, min(20000, size + 3500))


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
        inputs["reset_session"] = reset
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


_INNER_PROTOCOL_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9_]*\.\d+>")     # a beat-id shaped tag (E1.2) only: prose, quotes and ordinary words are never looked at


def _micro_beat_problems(raw: str | None, *, allowed_parents: set[str], parent_order: list[str] | None = None,
                         require_parents: set[str] | None = None) -> tuple[list[dict], list[str]]:
    """STRUCTURE only: (beats, problems). No word, language or meaning is read; the beat content is kept exactly as the model wrote it (outer whitespace trimmed).
    A reply is refused (problems not empty) when: text or an incomplete / unclosed / unexpected tag sits outside the beat tags; a tag is not <ParentId.N>; the parent is not one of this
    request's macro events; N is not 1, 2, 3 ... without gaps, in order; an id repeats; a macro event's beats are not together; the macro events are not in their own order (whole story);
    a beat is empty; a required macro event has no beat. Whether the beats are complete or faithful to the macro event cannot be checked by structure."""
    problems: list[str] = []
    if not isinstance(raw, str) or not raw.strip():
        return [], ["the reply is empty"]
    text = _strip_template_tail(_strip_code_fence(raw))
    beats: list[dict] = []
    outside: list[str] = []
    last = 0
    for m in _TAG_RE.finditer(text):
        outside.append(text[last:m.start()])
        last = m.end()
        tag_id, content = m.group(1), m.group(2).strip()
        if "." not in tag_id:
            problems.append(f"<{tag_id}> is not a beat tag (expected <E1.1> form)")
            continue
        parent, child = tag_id.rsplit(".", 1)
        if not child.isdigit() or int(child) < 1:
            problems.append(f"<{tag_id}>: the beat number must be 1, 2, 3 ...")
            continue
        if parent not in allowed_parents:
            problems.append(f"<{tag_id}>: {parent} is not a macro event of this request")
            continue
        if not content:
            problems.append(f"<{tag_id}> is empty")
        _inner = _INNER_PROTOCOL_TAG_RE.findall(content)
        if _inner:
            problems.append(f"<{tag_id}> has a beat tag inside its content ({', '.join(dict.fromkeys(_inner))}): every beat tag must open and close with its own id")
        beats.append({"id": tag_id, "parent": parent, "content": content})
    outside.append(text[last:])
    left = "".join(outside).strip()
    if left:
        problems.append(("an incomplete, unclosed or unexpected tag is left outside the beat tags: " if re.search(r"</?[A-Za-z0-9_.]+>", left)
                         else "text outside the beat tags: ") + repr(left[:80]))
    if not beats and not problems:
        problems.append("no beat tags found")
    ids = [b["id"] for b in beats]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        problems.append("duplicate beat id(s): " + ", ".join(dup))
    groups: list[tuple[str, list[int]]] = []
    for b in beats:
        n = int(b["id"].rsplit(".", 1)[1])
        if groups and groups[-1][0] == b["parent"]:
            groups[-1][1].append(n)
        else:
            groups.append((b["parent"], [n]))
    seen_parents = [g[0] for g in groups]
    for par in dict.fromkeys(seen_parents):
        if seen_parents.count(par) > 1:
            problems.append(f"the beats of {par} are not together (another macro event's beats come between them)")
    for par, nums in groups:
        if par in {d.split(".")[0] for d in dup}:
            continue                                              # duplicates are reported above; numbering would only repeat the same fault
        if nums != list(range(1, len(nums) + 1)):
            problems.append(f"the beats of {par} must be numbered {par}.1, {par}.2 ... without gaps and in order (got {', '.join(f'{par}.{n}' for n in nums)})")
    if parent_order:
        pos = {pid: i for i, pid in enumerate(parent_order)}
        idx = [pos[x] for x in dict.fromkeys(seen_parents) if x in pos]
        if idx != sorted(idx):
            problems.append("the macro events are not in their own order: " + ", ".join(dict.fromkeys(seen_parents)))
    if require_parents is not None:
        missing = sorted(require_parents - set(seen_parents))
        if missing:
            problems.append("no beat for macro event(s): " + ", ".join(missing))
    return beats, problems


def _parse_micro_tags(raw: str | None, *, allowed_parents: set[str], require_parents: set[str] | None = None,
                      parent_order: list[str] | None = None, problems: list[str] | None = None) -> list[dict] | None:
    """Structural tag parser only. Model-authored content is preserved verbatim (only outer whitespace is trimmed). All-or-nothing: any structural problem rejects the whole reply
    (the caller retries, nothing is repaired here). `problems`, when given, receives the reasons."""
    beats, why = _micro_beat_problems(raw, allowed_parents=allowed_parents, parent_order=parent_order, require_parents=require_parents)
    if problems is not None:
        problems[:] = why
    return None if why else beats

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
        last_why: list[str] = []
        last_user = user_prompt
        for attempt in (1, 2):
            if attempt == 1:
                sent = user_prompt
            else:
                _got = {b["parent"] for b in _extract_micro_beats(_strip_code_fence(last_raw))}
                _missing = sorted(macro_ids - _got)
                sent = user_prompt + (
                    "\n\nThe previous response could not be accepted: " + ("; ".join(last_why[:6]) or f"missing or malformed tags for macro event id(s): {', '.join(_missing) or 'unknown'}") +
                    ". Return the complete set of tags, covering every macro event in order, in the exact <id>content</id> form."
                )
            last_user = sent
            raw = await _call_grok_text(
                _A2_SYSTEM_PROMPT, sent,
                api_key=grok_api_key, model=grok_model,
                max_tokens=_a2_max_tokens_whole(a1), on_log=on_log,
            )
            last_raw = raw or ""
            why: list[str] = []
            beats = _parse_micro_tags(raw, allowed_parents=macro_ids, require_parents=macro_ids, parent_order=[e["id"] for e in macros], problems=why)
            last_why = why
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
                await on_log(f"⚠ Beat Sheet Grok attempt {attempt}: " + "; ".join(why[:4]))
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
        why: list[str] = []
        for attempt in (1, 2, 3):
            # attempt 2: a short correction in the SAME session (the model sees its own rejected reply); attempt 3: a FRESH session with the full prompt + the correction
            # (two long replies in one session may overflow a small model's context)
            fresh = attempt != 2
            correction = (
                "Your previous reply could not be accepted: " + ("; ".join(why[:6]) if why else "missing or malformed tags") + ". "
                f"Answer again for macro event {mid} only. Every tag opens and closes with the same id, for example <{mid}.1>HAPPENS: ...\nDETAILS: ...</{mid}.1>, then <{mid}.2>...</{mid}.2>, "
                "numbered in order with no gaps, one opening tag and one closing tag per beat, nothing outside the tags."
            )
            sent = base_user if attempt == 1 else (correction if attempt == 2 else base_user + "\n\n" + correction)
            raw = await _call_local_text(
                wf_template, _A2_SYSTEM_PROMPT, sent,
                max_tokens=_a2_max_tokens_for_macro(str(macro.get("content") or "")),
                session_id=session_id, on_log=on_log, reset=fresh,
            )
            turns.append({"system_prompt": _A2_SYSTEM_PROMPT, "user_prompt": sent, "reset_session": fresh, "raw_response": raw or ""})
            accepted = _parse_micro_tags(raw, allowed_parents={mid}, require_parents={mid}, problems=why)
            if accepted is not None:
                break
            if on_log:
                await on_log(f"⚠ Beat Sheet {mid} attempt {attempt}: " + "; ".join(why[:4]))
        if accepted is None:
            if chat_capture is not None:        # keep the failed macro's turns so Beats chat can show what the model really answered
                chat_capture["sessions"] = sessions + [{
                    "label": mid, "turns": turns, "backend": "local",
                    "llm_wf_file": llm_wf_file, "session_id": session_id, "grok_model": grok_model,
                }]
            await on_err(f"Beat Sheet: macro {mid} did not return the required tags after 3 attempts")
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
