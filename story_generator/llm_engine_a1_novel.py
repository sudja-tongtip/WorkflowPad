"""
llm_engine_a1_novel.py — EXPERIMENTAL A1 for Story / Novel input (not wired into the app).

Same output contract as llm_engine_a1.run_a1_vnext (macro_events, character_sheet, world_sheet, locations,
important_objects, continuity_notes, source_style) so Beat Sheet (A2) can consume it unchanged.

Differences from llm_engine_a1:
  * a new, shorter system prompt organised in the order  understand -> characters -> objects -> locations -> events;
    it removes the old conflicting rules (event-local body facts "must" live in a macro event vs. appearance detail
    "never" makes one) and states that supporting description is written INTO the event it accompanies;
  * the requested output order puts the reference fields BEFORE the events, so names, clothing and objects are decided
    before the events that use them;
  * ONE generation pass; no full-rewrite second pass;
  * transport, tag parser (with scoped character-tag recovery) and Grok call are reused from llm_engine_a1 unchanged.
"""

__version__ = "20261103_a1_novel_f3_conversation"

import json
import re
import uuid

from .constants import LLM_DIR, lang_prompt_name
from .llm_engine_a1 import (
    GROK_MARKER, _a1_max_tokens, _call_grok_text, _call_local_text, _parse_a1_tags,
)

_NOVEL_SYSTEM_PROMPT = r"""You are a Story Structure Editor. You read a finished story and prepare the reference that a storyboard team will use to draw it as a sequence of images, so that the images alone tell the story in order.

FIRST DECIDE what the input is. A developed story contains meaningful developments through time (actions followed by reactions, dialogue exchanges, changes of state, consequences). A static description is mainly one frozen moment - a single composition, pose, scene or starting situation - even when it is long and detailed, and even when it shows an action frozen at one instant. If the input is a static description, output exactly the single word STATIC and stop (no tags, no other text). If it is a developed story, complete the normal structured output below.

HOW TO WORK (think silently; never print your reasoning, output only the requested tags)

1. UNDERSTAND the whole story before writing anything: who the characters are, the starting situation, the chronology, actions and reactions, cause and effect, changes of physical state, important objects, places, and where the story ends. Do not split the story sentence by sentence or paragraph by paragraph.

2. CHARACTERS. Every named or significantly acted-upon character - a person, animal or creature - has its own <charN.*> set, not only the protagonist (merge only an undifferentiated group). Naming: every <charN.name> is a real personal given name that fits the setting, nationality and era (a Thai given name in a Thai setting), including a first-person narrator. If the story gives no name, invent one. It is never a role, descriptive word or pronoun (a young woman, the man, woman, lover, narrator, I) and never a bracketed role after a name. Keep the story's own labels (such as "the woman", "the man") in <charN.alias>, never a pronoun there. Once chosen, use the name consistently in every event, the character sheet and every other field; never write the label or a pronoun as the person's name. age, body, face, skin, marks and init_hair describe the person BEFORE the story's events (no emotion, injury or consequence). init_cloth names the actual garments and worn accessories the person wears at the START, before any later event removes, damages or changes them; a character who truly starts nude or partly clothed is recorded that way. Keep the source's appearance and clothing; when a fixed visual detail is missing, make one simple concrete choice that does not contradict the story. Never write placeholders ("unspecified", "unknown", "casual clothes").

3. OBJECTS. important_objects lists discrete external physical objects whose identity, owner or holder, position, use, transfer, damage or changing state matters later (what it is, who has it, where it starts, how it changes). Never list body parts, bodily material, or clothing a character wears.

4. LOCATIONS. locations lists the places of the story and the moves between them. world_sheet holds persistent environment facts (setting, weather, lighting). They support events; they are not events.

5. EVENTS. Write the macro events E1, E2, E3... in chronological order, from the first actual event to the final one. A macro event is a connected passage of the story containing several meaningful developments (an action and the reaction to it, dialogue, a change of relationship, situation or physical state, a consequence) - not a single frozen pose and not an inventory of visual details. Group the story's developments into passages by cause and effect; each passage picks up from the state the previous one ended in and ends in a state the next one starts from. Write each as flowing prose naming who does what to whom (no pronouns, no role labels), in the source's order, keeping every important fact: clothing, body state, objects and who holds them, position, place, light and viewpoint appear inside the prose where they matter, none contradicted, none moved to another passage. Keep dialogue: who says what, in order, with its meaning. You may add small connecting developments the story clearly implies (a step, a turn, an in-character reply, the use of an object) when they make the passage flow, but never change the main events, the characters and relationships, the chronology, the stated states and facts, or the ending, and never invent anything that contradicts the source. If the story holds only one real development, do not stretch it unnaturally. Never write HAPPENS: or DETAILS: labels or bullet lists; a passage that only describes how someone or something looks is never an event of its own.

6. continuity_notes holds facts that stay true across events (a persisting state or clothing change); it never repeats the character sheet.

LANGUAGE: write every tag's text in the requested output language, translating the story if it is in another language; keep established names. source_style is always English.

source_style: leave it empty unless the story itself is a style-describing prompt or explicitly states one overall visual/art style for the whole work; then write one dense paragraph of subject-free image-style descriptors (medium, palette, lighting quality, materials, camera character, mood) without naming characters, pose, clothing or events. Never invent a style.

CHECK before answering: events reach the story's final event; every event is a connected prose passage with several developments (no HAPPENS:/DETAILS: labels); every significant character has a set with actual garments in init_cloth; re-read every <charN.name> now: it must be a proper personal name, not a role label or descriptive word (woman, man, lover, narrator) and not a pronoun - if it is, replace it with an invented personal name and use it everywhere, moving the label to <charN.alias>; no event is a pose or an appearance inventory; no source fact (clothing, object, owner, position, state, dialogue) was dropped.

Return only the requested tags."""


def _build_novel_user_prompt(story_text: str, *, desc_lang: str, requirement: str = "", allow_static: bool = True) -> str:
    lang_label = lang_prompt_name(desc_lang or "en")
    parts = [f"Output language for every tag's text (translate if the story is in another language): {lang_label}"]
    if allow_static:
        parts.append("The only allowed untagged reply is the single word STATIC, for an input that is one static moment rather than a developed story.")
    if (requirement or "").strip():
        parts.append(f"Special requirements: {(requirement or '').strip()}")
    parts.append(
        "OUTPUT FORMAT - flat tags, plain text inside, no JSON, in this order:\n"
        "(1) characters, one numbered set for EVERY named or significantly acted-upon character (person, animal or creature), not only the main one:\n"
        "<char1.name>a real personal given name (invent one if the story gives none) - never a role, descriptive word or pronoun such as woman, man, lover, narrator</char1.name>\n"
        "<char1.alias>the story's own labels and other names for this person (for example the woman), or empty; never a pronoun</char1.alias>\n"
        "<char1.trigger></char1.trigger>\n"
        "<char1.age>...</char1.age>\n"
        "<char1.gender>...</char1.gender>\n"
        "<char1.nationality>...</char1.nationality>\n"
        "<char1.body>...</char1.body>\n"
        "<char1.face>...</char1.face>\n"
        "<char1.skin>...</char1.skin>\n"
        "<char1.marks>...</char1.marks>\n"
        "<char1.init_hair>...</char1.init_hair>\n"
        "<char1.init_cloth>...</char1.init_cloth>\n"
        "(the same twelve tags for char2, char3...)\n"
        "(2) reference fields:\n"
        "<important_objects>external objects: what, who holds or owns it, where, state; empty if none</important_objects>\n"
        "<locations>places and moves between them</locations>\n"
        "<world_sheet>persistent environment facts</world_sheet>\n"
        "<continuity_notes>facts that stay true across events</continuity_notes>\n"
        "<source_style>(English) only when the story states or is a visual style; otherwise empty</source_style>\n"
        "(3) macro events in story order:\n"
        "<E1>connected prose of the first passage: actions, dialogue, reactions, what changes...</E1>\n"
        "<E2>connected prose of the next passage, starting from where E1 ended...</E2>\n"
        "Always leave <charN.trigger> empty. Write \"none\" in a character tag whose detail truly does not apply "
        "(e.g. marks). Keep every tag present; use an empty tag when a field has nothing to add. Tag names stay exactly as "
        "shown (English); only the text inside follows the output language. Text inside a tag is plain - quotes need no escaping. "
        "Close every tag with its own exact name."
    )
    parts.append("STORY:\n" + (story_text or ""))
    if allow_static:
        parts.append("Before answering: is the STORY above a developed story, or a description of one static moment? "
                     "If it is one static moment, reply with exactly the single word STATIC and nothing else.")
    return "\n\n".join(parts)


_PARTS_RE = re.compile(r"^\s*HAPPENS:\s*(.*?)\s*DETAILS?:\s*(.*?)\s*$", re.DOTALL)


def _merge_event_parts(result: dict) -> None:
    """Explicit-label formatting only: 'HAPPENS: a DETAILS: b' -> 'a b'; a lone label is removed, the text is kept."""
    for e in result.get("macro_events") or []:
        content = str(e.get("content") or "")
        m = _PARTS_RE.match(content)
        if m:
            e["content"] = (m.group(1) + " " + m.group(2)).strip()
        else:   # only one label (or a stray one): drop the label itself, keep every word of the text
            e["content"] = re.sub(r"\s*(?<![A-Za-z])DETAILS?:\s*", " ", re.sub(r"^\s*HAPPENS:\s*", "", content)).strip()


STATIC_ROUTE = {"route": "static"}


def _is_static_signal(raw: str | None) -> bool:
    """True only when the WHOLE reply is the single word STATIC. Normalisation: surrounding whitespace and an enclosing
    ``` fence are ignored and case is ignored; nothing else (no punctuation, no extra words, no tags) is accepted."""
    s = (raw or "").strip()
    if s.startswith("```") and s.endswith("```") and len(s) > 6:
        s = s[3:-3].strip()
    return s.casefold() == "static"


_CONVERSATION_MODE = True          # True: A1 Novel is a step-by-step conversation (a1_novel_conversation.py); False: the previous single call


async def run_a1_novel(
    story_text: str,
    llm_wf_file: str,
    on_err,
    on_log=None,
    mode: str = "cinematic",      # API compatibility with run_a1_vnext
    genre: str = "",
    genre2: str = "",
    requirement: str = "",
    desc_lang: str = "en",
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    chat_capture: dict | None = None,
    allow_static: bool = True,
) -> dict | None:
    """Entry point. allow_static=True keeps the previous single call, the only path that may answer STATIC (used when the source classifier gave no valid answer).
    allow_static=False (the source router already decided NARRATIVE) runs the step-by-step conversation."""
    if _CONVERSATION_MODE and not allow_static:
        if not (story_text or "").strip():
            await on_err("Story Bible (novel): story is empty")
            return None
        wf_template = None
        if llm_wf_file != GROK_MARKER:
            if not (llm_wf_file or "").strip():
                await on_err("Story Bible (novel): no local LLM workflow selected")
                return None
            wf_path = LLM_DIR / f"{llm_wf_file}.json"
            if not wf_path.exists():
                await on_err(f"ไม่พบ {wf_path}")
                return None
            try:
                wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
            except Exception as exc:
                await on_err(f"Story Bible (novel) workflow read failed: {exc}")
                return None
        from .a1_novel_conversation import run_a1_novel_conversation
        return await run_a1_novel_conversation(story_text, llm_wf_file, on_err, on_log, genre=genre, genre2=genre2, requirement=requirement, desc_lang=desc_lang,
                                               grok_api_key=grok_api_key, grok_model=grok_model, chat_capture=chat_capture, wf_template=wf_template)
    return await _run_a1_novel_single(story_text, llm_wf_file, on_err, on_log, mode=mode, genre=genre, genre2=genre2, requirement=requirement, desc_lang=desc_lang,
                                      grok_api_key=grok_api_key, grok_model=grok_model, chat_capture=chat_capture)


async def _run_a1_novel_single(
    story_text: str,
    llm_wf_file: str,
    on_err,
    on_log=None,
    mode: str = "cinematic",      # API compatibility with run_a1_vnext
    genre: str = "",
    genre2: str = "",
    requirement: str = "",
    desc_lang: str = "en",
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    chat_capture: dict | None = None,
) -> dict | None:
    """One generation pass (two attempts only if the tags cannot be read). No second pass.

    Returns the normal A1 dict for a developed story, None on error, or the routing signal {"route": "static"}
    (the only dict with a "route" key) when the model replied exactly STATIC. Callers must not pass the signal on to A2."""
    if not (story_text or "").strip():
        await on_err("Story Bible (novel): story is empty")
        return None
    wf_template = None
    if llm_wf_file != GROK_MARKER:
        if not (llm_wf_file or "").strip():
            await on_err("Story Bible (novel): no local LLM workflow selected")
            return None
        wf_path = LLM_DIR / f"{llm_wf_file}.json"
        if not wf_path.exists():
            await on_err(f"ไม่พบ {wf_path}")
            return None
        try:
            wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
        except Exception as exc:
            await on_err(f"Story Bible (novel) workflow read failed: {exc}")
            return None

    base_user = _build_novel_user_prompt(story_text, desc_lang=desc_lang, requirement=requirement)
    max_tokens = _a1_max_tokens(story_text)
    session_id = uuid.uuid4().hex[:8]
    if on_log:
        await on_log("✨ Story Bible (novel) → " + ("Grok" if llm_wf_file == GROK_MARKER else f"Comfy ({llm_wf_file})"))

    last_raw, last_user = "", base_user
    for attempt in (1, 2):
        user_prompt = base_user
        if attempt == 2:
            user_prompt += ("\n\nYour previous response could not be read. Return the complete set of flat tags in the order "
                            "shown, every tag closed with its own exact matching closing tag. No JSON, no commentary.")
        last_user = user_prompt
        if llm_wf_file == GROK_MARKER:
            raw = await _call_grok_text(_NOVEL_SYSTEM_PROMPT, user_prompt, api_key=grok_api_key, model=grok_model,
                                        max_tokens=max_tokens, on_log=on_log)
        else:
            raw = await _call_local_text(wf_template or {}, _NOVEL_SYSTEM_PROMPT, user_prompt, max_tokens=max_tokens,
                                         session_id=session_id, on_log=on_log)
        last_raw = raw or ""
        if _is_static_signal(raw):
            if on_log:
                await on_log("ℹ Story Bible (novel): input classified STATIC - routing to A1 Static")
            return dict(STATIC_ROUTE)
        notes: list[str] = []
        result = _parse_a1_tags(raw, notes)
        if on_log:
            for n in notes:
                await on_log(f"ℹ Story Bible (novel) parse: {n}")
        if result is not None:
            _merge_event_parts(result)
            if chat_capture is not None:
                chat_capture["turns"] = [{"system_prompt": _NOVEL_SYSTEM_PROMPT, "user_prompt": user_prompt, "raw_response": last_raw}]
                chat_capture["backend"] = "grok" if llm_wf_file == GROK_MARKER else "local"
                chat_capture["llm_wf_file"] = llm_wf_file
                chat_capture["session_id"] = session_id
                chat_capture["grok_model"] = grok_model
            return result
        if on_log:
            await on_log(f"⚠ Story Bible (novel) attempt {attempt}: invalid tag structure")

    if chat_capture is not None and last_raw:
        chat_capture["turns"] = [{"system_prompt": _NOVEL_SYSTEM_PROMPT, "user_prompt": last_user, "raw_response": last_raw}]
    await on_err("Story Bible (novel): LLM did not return the required tag structure after 2 attempts")
    return None


async def run_a1_routed(
    story_text: str,
    llm_wf_file: str,
    on_err,
    on_log=None,
    mode: str = "cinematic",
    genre: str = "",
    genre2: str = "",
    requirement: str = "",
    desc_lang: str = "en",
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    chat_capture: dict | None = None,
) -> dict | None:
    """Drop-in for run_production_reference. The SOURCE ROUTER (llm_engine_a1_router: one short classification call, own session) decides
    BEFORE any macro story is written, and a VALID answer is authoritative: STATIC -> A1 Static, NARRATIVE -> A1 Novel (Novel only writes the story).
    An invalid reply or a backend error is never guessed: the previous behaviour (A1 Novel decides, STATIC allowed) is used and recorded.
    If a valid NARRATIVE route is answered STATIC by A1 Novel, that is a CLASSIFIER_GENERATION_CONFLICT: it is recorded and reported as an error;
    the input is NOT sent to A1 Static silently and no macro is invented.
    chat_capture["routing"] records: method, contract, expected_labels, raw, label (parsed route), status, status_code, route, reason, backend,
    fallback_used, llm_calls_before_generation (and error / conflict when present)."""
    from .llm_engine_a1_static import run_a1_static     # lazy: static imports nothing from this module
    from .llm_engine_a1_router import classify_source
    kwargs = dict(mode=mode, genre=genre, genre2=genre2, requirement=requirement, desc_lang=desc_lang,
                  grok_api_key=grok_api_key, grok_model=grok_model, chat_capture=chat_capture)

    decision = await classify_source(story_text, llm_wf_file, on_log, grok_api_key=grok_api_key, grok_model=grok_model)
    classifier_calls = 1
    if decision["status"] == "invalid":              # the model answered but not with a label: ask once more before the previous behaviour (Novel decides) is used
        if on_log:
            await on_log(f"⚠ Story Bible router: invalid classification reply {decision['raw']!r} - asking once more")
        first_raw = decision["raw"]
        decision = await classify_source(story_text, llm_wf_file, on_log, grok_api_key=grok_api_key, grok_model=grok_model)
        classifier_calls = 2
        decision["first_invalid_raw"] = first_raw
    routing = {"method": "classifier", "contract": decision.get("contract"), "expected_labels": decision.get("expected_labels"), "raw": decision["raw"],
               "label": decision["label"], "status": decision["status"], "status_code": "", "route": None, "reason": "", "error": decision["error"],
               "backend": decision["backend"], "fallback_used": False, "llm_calls_before_generation": classifier_calls}
    if classifier_calls == 2:
        routing["first_invalid_raw"] = decision.get("first_invalid_raw", "")

    async def note(msg: str):
        if on_log:
            await on_log(msg)

    def record():
        if chat_capture is not None:
            chat_capture["routing"] = routing

    if decision["label"] == "STATIC":
        routing.update(status_code="CLASSIFIED_STATIC", route="static", reason="classifier answered a valid STATIC label")
        record()
        await note("ℹ Story Bible router: STATIC -> A1 Static")
        return await run_a1_static(story_text, llm_wf_file, on_err, on_log, **kwargs)

    if decision["label"] == "NARRATIVE":
        routing.update(status_code="CLASSIFIED_NARRATIVE", route="novel", reason="classifier answered a valid NARRATIVE label")
        record()
        await note("ℹ Story Bible router: NARRATIVE -> A1 Novel")
        result = await run_a1_novel(story_text, llm_wf_file, on_err, on_log, allow_static=False, **kwargs)
        if isinstance(result, dict) and result.get("route") == "static":
            routing.update(status_code="CLASSIFIER_GENERATION_CONFLICT", route=None, conflict=True,
                           reason="the router classified the input as NARRATIVE but A1 Novel answered STATIC; not routed to A1 Static, no macro created")
            record()
            await note("⚠ Story Bible router: CLASSIFIER_GENERATION_CONFLICT (router NARRATIVE, A1 Novel STATIC)")
            await on_err("Story Bible: source classification conflict - the router classified the input as a narrative but A1 Novel answered STATIC; nothing was generated")
            return None
        return result

    # invalid classification reply or backend error: previous behaviour, recorded
    why = decision["error"] or f"invalid classification reply {decision['raw']!r} for contract {decision.get('contract')}"
    routing.update(method="fallback", fallback_used=True, route="novel", reason=f"{why}; previous behaviour used (A1 Novel decides, STATIC allowed)",
                   status_code="CLASSIFIER_ERROR_FALLBACK" if decision["status"] == "error" else "CLASSIFIER_INVALID_FALLBACK")
    record()
    await note(f"⚠ Story Bible router: {why} - falling back to A1 Novel (it may still answer STATIC)")
    result = await run_a1_novel(story_text, llm_wf_file, on_err, on_log, **kwargs)
    if result is None:
        return None
    if isinstance(result, dict) and result.get("route") == "static":
        routing.update(route="static", reason=routing["reason"] + "; A1 Novel then answered STATIC")
        record()
        await note("ℹ Story Bible router: fallback - A1 Novel answered STATIC -> A1 Static")
        return await run_a1_static(story_text, llm_wf_file, on_err, on_log, **kwargs)
    return result
