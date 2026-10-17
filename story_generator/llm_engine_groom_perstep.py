"""
Groom New - Per-Step PROTOTYPE (experimental; not wired into the UI, no default flag, production Groom is untouched).

    SOURCE -> STEP 1 EVENT PLANNER -> accepted event plan -> STEP 2 STORY WRITER -> complete groomed story

Two isolated LLM calls (fresh session each, no history), the second one carries the ACCEPTED plan inside its own user message.
Python only: builds the prompts from the existing Groom data (settings text, genre goals/arcs, core paragraphs), checks the plan's STRUCTURE
(tags, repeated <event> tags counted in order, no empty field), routes the calls, checks the output language with the existing bounded check, and returns the same
(story, card) pair as run_story_grooming. It never reads the meaning of the plan or the story.

Isolation from editing: the returned card has no session_id and no grok_messages (the planning exchange is never offered to Groom Chat), so
Chat / manual edits start from the working draft exactly as they do for any story that has no live session; the event plan is stored only
as card["perstep"] (a generation artifact). The second pass of production Groom is deliberately not used here.
"""

from __future__ import annotations

import json
import re
import time

from . import llm_engine_groom as G
from .constants import LLM_DIR, lang_prompt_name
from .groom_genre_arcs import _GENRE_ARCS
GROK_MARKER = G.GROK_MARKER

_PLAN_RETRY_LIMIT = 1          # one structural retry of the planner (same conversation text, same choices), never a new arc


def _core_paragraph(prefix: str) -> str:
    for para in G._GROOM_CORE.split("\n\n"):
        if para.startswith(prefix):
            return para
    raise RuntimeError(f"Groom core paragraph not found: {prefix!r}")


_INTRO = G._GROOM_CORE.split("\n\n")[0]
_PLANNER_OP = """OPERATION: PLAN THE STORY (no prose yet). Decide the SOURCE SHAPE, choose ONE arc per selected genre from its Arc Options, fit it to SOURCE's own cast and setting, and plan the whole story as numbered events before anything is written.
Each event is one or two plain sentences in the requested language: what a character does or says (for an important spoken line, the words themselves), how the other person or the surroundings answer, what happens as a result, and how the situation has changed so that the next event follows. A character's motive may appear where the cause of an event needs it, shown through what they choose, say or do. The number of events follows the Creativity and Length settings and SOURCE (an existing story keeps its own important events, spoken words and outcome first, and an established outcome is never changed just to make it more concrete).
For a visual-like SOURCE the first event starts from the starting situation SOURCE states (its characters, clothing, objects and physical state) before anything changes; camera or composition facts in SOURCE are source information, never story events.
The outcome gives the final action or event and its result, in one or two sentences. No prose, no camera terms, no new characters.

EXAMPLES of the writing style (demonstrations only: never use their characters, objects, places or events in the real story)

Example 1 - an event.
BAD: Mali became more determined. Her actions demonstrated a growing willingness to confront the problem and take control of her future.
GOOD: Mali pulled the jammed drawer open. A sealed envelope slid onto the floor. She picked it up, recognized her father's handwriting, and called her brother to ask why he had hidden it.

Example 2 - the outcome.
BAD: Their relationship reached a new level of mutual trust. Both characters became more confident about facing the future together.
GOOD: Arun handed Mali the only key. She unlocked the gate and stepped outside. Arun followed, leaving the gate open behind them.

Return exactly this structure and nothing else (no markdown fences, no notes):
<story_plan>
<source_shape>story or visual_like</source_shape>
<selected_arc>the id(s) of the chosen arc(s), or none</selected_arc>
<cast>each person SOURCE establishes and what is fixed about them</cast>
<source_truth>the facts, setting, objects and outcomes of SOURCE that stay true</source_truth>
<event>...</event>
<event>...</event>
(one <event>...</event> per event, in story order, as many as the story needs)
<outcome>...</outcome>
</story_plan>
The "..." are placeholders: write real content in them, never copy them."""

_WRITER_OP = """OPERATION: WRITE THE STORY FROM THE ACCEPTED EVENT PLAN. The user message gives SOURCE, the settings, the selected arc, and an ACCEPTED EVENT PLAN that was already decided. The roles are fixed: the plan says WHAT HAPPENS and in what order; SOURCE says the facts, appearance, clothing, starting pose and cast that must stay true; you are the author who turns the plan into a novel, not someone who summarises it.
Write the complete finished story in the requested language and length (Medium = about 6-10 paragraphs; start a new paragraph when the scene or the beat changes):
- Open from the situation SOURCE establishes and state its appearance, clothing, setting and starting position in the narration, then follow the plan's events in order and end on its outcome. Do not choose another arc, do not change or add to the cast, and do not change the plan's events or outcome, except where SOURCE's own facts require it.
- Expand every event into what is done, how the other person or the situation answers, and what follows. When the plan has someone speak or whisper, write the actual words as dialogue. The consequence of each event shows in the next one.
- Keep continuity of appearance, clothing, objects, place and bodies from the first paragraph to the last.
- Tell the events as they happen; never explain what an event shows or means ("this change indicates...") and never summarise the plan.
No plan labels, no event numbers, no notes.

Return exactly this structure and nothing else (no markdown fences, no headings):
<revised_story>
the complete finished story prose
</revised_story>
Optionally, after it, one <source_style>...</source_style> tag as described above."""

_PLANNER_SYSTEM = "\n\n".join([
    _INTRO, _core_paragraph("EVENT-DRIVEN STORYTELLING."), _core_paragraph("CREATIVITY SCOPE."), _core_paragraph("GENRE-DRIVEN STORYTELLING."),
    _core_paragraph("CAST."), _core_paragraph("SOURCE SHAPE (decide silently"), _PLANNER_OP])
_WRITER_INTRO = _INTRO.replace("; see SOURCE SHAPE.", "; the accepted plan's source_shape says which.")
_WRITER_SYSTEM = "\n\n".join([
    _WRITER_INTRO, _core_paragraph("READABILITY."), _core_paragraph("COHERENCE."), _core_paragraph("CAST."), _core_paragraph("If SOURCE is itself a prompt describing"), _WRITER_OP])

_SINGLE_TAGS = ("source_shape", "selected_arc", "cast", "source_truth", "outcome")
_EVENT_RE = re.compile(r"<event>(.*?)</event>", re.DOTALL | re.IGNORECASE)


def _parse_plan(raw: str | None) -> tuple[str | None, list[str]]:
    """Structure only: (the plan text exactly as written, []) when it is usable, otherwise (None, problems)."""
    text = raw or ""
    m = re.search(r"<story_plan>(.*?)</story_plan>", text, re.DOTALL | re.IGNORECASE)
    if not m:
        return None, ["<story_plan> is missing or not closed with </story_plan>"]
    inner = m.group(1)
    problems: list[str] = []
    for tag in _SINGLE_TAGS:
        found = re.findall(rf"<{tag}>(.*?)</{tag}>", inner, re.DOTALL | re.IGNORECASE)
        if not found:
            problems.append(f"<{tag}> is missing or not closed")
        elif len(found) > 1:
            problems.append(f"<{tag}> appears {len(found)} times")
        elif not found[0].strip():
            problems.append(f"<{tag}> is empty")
    events = _EVENT_RE.findall(inner)
    if len(events) < 2:
        problems.append(f"{len(events)} <event>...</event> tag(s): at least two are needed")
    for k, body in enumerate(events, 1):
        if not body.strip():
            problems.append(f"the {k}th <event> is empty")
    unknown = sorted({t.lower() for t in re.findall(r"<([a-z_][a-z0-9_]*)>", inner, re.IGNORECASE)} - {"event", *_SINGLE_TAGS})
    if unknown:
        problems.append("unexpected tag(s) inside <story_plan>: " + ", ".join(f"<{t}>" for t in unknown))
    opens = len(re.findall(r"<(?:event|" + "|".join(_SINGLE_TAGS) + r")>", inner, re.IGNORECASE))
    pairs = len(events) + sum(1 for t in _SINGLE_TAGS if re.search(rf"<{t}>.*?</{t}>", inner, re.DOTALL | re.IGNORECASE))
    if opens != pairs:
        problems.append("a tag was opened and not closed with its own exact name")
    if problems:
        return None, problems
    return numbered_plan(inner, events), problems


def numbered_plan(inner: str, events: list[str]) -> str:
    """The accepted plan in its canonical form: the single fields as written, the events labelled <event_1>, <event_2>, ... by their parsed order."""
    parts = ["<story_plan>"]
    for tag in _SINGLE_TAGS[:4]:
        parts.append(f"<{tag}>{re.search(rf'<{tag}>(.*?)</{tag}>', inner, re.DOTALL | re.IGNORECASE).group(1).strip()}</{tag}>")
    for k, body in enumerate(events, 1):
        parts.append(f"<event_{k}>{body.strip()}</event_{k}>")
    parts.append(f"<outcome>{re.search(r'<outcome>(.*?)</outcome>', inner, re.DOTALL | re.IGNORECASE).group(1).strip()}</outcome>")
    parts.append("</story_plan>")
    return "\n".join(parts)


def _selected_arc_lines(genre: str, genre2: str, plan_text: str) -> list[str]:
    """Deterministic lookup only: the arc whose id the planner wrote in <selected_arc>, with its authored steps. Nothing is guessed (an unknown id gives no line)."""
    m = re.search(r"<selected_arc>(.*?)</selected_arc>", plan_text or "", re.DOTALL | re.IGNORECASE)
    chosen = (m.group(1) if m else "").lower()
    lines: list[str] = []
    for role, key in (("Primary", genre), ("Secondary", genre2)):
        for arc in _GENRE_ARCS.get((key or "").strip(), ()):
            if re.search(rf"(?<![a-z0-9_]){re.escape(arc['id'].lower())}(?![a-z0-9_])", chosen):
                lines.append(f"{role} Selected Arc (decided in the plan): {arc['name']} [{arc['id']}]: " + " -> ".join(arc["steps"]))
    return lines


def _build_writer_user(raw_story: str, plan_text: str, *, lang: str, genre: str, genre2: str, requirement: str, story_length: str, creativity: str) -> str:
    """Same settings and SOURCE as the production user message, but the genre block carries the goal and the SELECTED arc instead of the four options to choose from."""
    parts = [f"Language: {lang_prompt_name(lang)}"]
    for role, key in (("Primary", genre), ("Secondary", genre2)):
        key = (key or "").strip()
        if key and key != "none":
            parts.append(f"{role} Genre: {key.replace('_', ' ')}")
            if G._GENRE_GOALS.get(key):
                parts.append(f"{role} Narrative Goal: {G._GENRE_GOALS[key]}")
    parts += _selected_arc_lines(genre, genre2, plan_text) or (["Genre: No specific genre"] if not any((g or "").strip() not in ("", "none") for g in (genre, genre2)) else [])
    parts.append(f"Creativity: {G._CREATIVITY_TEXT.get((creativity or 'balanced').strip().lower(), str(creativity or 'balanced'))}")
    parts.append(f"Length: {G._LENGTH_TEXT.get((story_length or 'auto').strip().lower(), str(story_length or 'auto'))}")
    if (requirement or "").strip():
        parts.append(f"Special requirements: {requirement.strip()}")
    parts.append("SOURCE:\n" + (raw_story or ""))
    parts.append("ACCEPTED EVENT PLAN (decided; follow it):\n" + plan_text)
    parts.append("Write the complete story now.")
    return "\n".join(parts[:-3]) + "\n" + "\n\n".join(parts[-3:])


_EVENT_WRITER_OP = """OPERATION: CONTINUE THE STORY FOR THE CURRENT EVENT. The user message gives SOURCE, the settings, the selected arc, the ACCEPTED EVENT PLAN, the STORY WRITTEN SO FAR, and the CURRENT EVENT. You are the author of one continuous novel, writing it one stretch at a time: write only the prose for the CURRENT EVENT, continuing directly from the last sentence of the story so far. The roles are fixed: the plan says what happens, SOURCE says the facts, appearance, clothing, starting pose and cast that stay true.
- Write the current event as it happens: what is done, how the other person or the situation answers, and what follows. When the event includes spoken words, write them as dialogue.
- Do not repeat or retell anything already written, do not hurry on to later events, do not start a new scene or introduce anyone new unless the event requires it, and do not change the plan's events or the cast.
- If the story so far is empty, this is the first stretch: open from the situation SOURCE establishes and state its appearance, clothing, setting and starting position in the narration. If the current event is the last one, end the story on the plan's outcome, shown as something that happens.
- Tell the event as it happens; never explain what it shows or means, never summarise, no camera terms."""

_EVENT_RETURN_FIRST = """

Return exactly this structure and nothing else (no markdown fences, no headings):
<scene>
the prose for this event
</scene>
Optionally, after it, one <source_style>...</source_style> tag as described above."""

_EVENT_RETURN_NEXT = """

Return exactly this structure and nothing else (no markdown fences, no headings, no other tags):
<scene>
the prose for this event
</scene>"""

# Event 1 keeps the source_style paragraph of the core and may add the tag; event 2 onward is assembled WITHOUT any source_style instruction.
_EVENT_WRITER_SYSTEM_LONG = "\n\n".join([
    _WRITER_INTRO, _core_paragraph("READABILITY."), _core_paragraph("COHERENCE."), _core_paragraph("CAST."), _core_paragraph("If SOURCE is itself a prompt describing"), _EVENT_WRITER_OP + _EVENT_RETURN_FIRST])
_EVENT_WRITER_SYSTEM_NEXT_LONG = "\n\n".join([
    _WRITER_INTRO, _core_paragraph("READABILITY."), _core_paragraph("COHERENCE."), _core_paragraph("CAST."), _EVENT_WRITER_OP + _EVENT_RETURN_NEXT])

# ---- SHORT per-event prompt + two few-shot examples (shared by every event; only event 1 adds the source_style contract) ----
_SHORT_BODY = """You are a fiction writer.

Write only the CURRENT EVENT as natural story prose.
Continue directly from STORY SO FAR.

SOURCE defines the characters, appearance, clothing, physical facts, and starting situation.
PLAN defines what happens and in what order.

Show events through actions, spoken dialogue, reactions, and consequences.
Do not explain what an action symbolizes or means.

Preserve established facts and physical continuity.
Do not invent characters or contradict SOURCE.
Do not write future events early.
Do not include camera or image-generation instructions in story prose.

Write in the requested language."""
_SHORT_EXAMPLES = """EXAMPLES (demonstrations only: never copy their names, events or objects into the story)

Example 1 - show the action, not the interpretation.
BAD: Her decision marked a significant turning point. The gesture demonstrated her growing courage and willingness to confront the truth.
GOOD: Mira unfolded the letter again. She read the final line, pushed her chair back, and reached for the telephone. Her fingers hesitated over the numbers before she pressed the first key.

Example 2 - write the actual dialogue.
PLAN: Arun asks Mali whether she will leave. Mali answers and makes a decision.
BAD: Arun asked a meaningful question, and Mali's response showed that she had finally made up her mind.
GOOD:
"Are you leaving?" Arun asked.

Mali put the suitcase down. "Not tonight. We need to finish this first."

Arun moved away from the doorway, leaving her a clear path to the table."""
_SHORT_RETURN_NEXT = "Return only <scene>story prose</scene>."
_SHORT_RETURN_FIRST = "Return <scene>story prose</scene>. After it you may add one <source_style>...</source_style> tag as described below."
_EVENT_WRITER_SYSTEM = "\n\n".join([_SHORT_BODY + "\n" + _SHORT_RETURN_FIRST, _SHORT_EXAMPLES, _core_paragraph("If SOURCE is itself a prompt describing")])
_EVENT_WRITER_SYSTEM_NEXT = "\n\n".join([_SHORT_BODY + "\n" + _SHORT_RETURN_NEXT, _SHORT_EXAMPLES])

_EVENT_INSTR_FIRST = "This is the first stretch. Begin from SOURCE's starting situation. Preserve its stated character appearance and physical details. Then write the first event."
_EVENT_INSTR_MIDDLE = "Continue from STORY SO FAR without restarting the scene. Write only the current development."
_EVENT_INSTR_LAST = "Complete the planned outcome through actual events. Do not end with an interpretation or moral summary."
_EVENT_TAG_RE = re.compile(r"<event_(\d+)>(.*?)</event_\1>", re.DOTALL | re.IGNORECASE)
_SCENE_RE = re.compile(r"<scene>(.*?)</scene>", re.DOTALL | re.IGNORECASE)
_STYLE_RE = re.compile(r"<source_style>.*?(?:</source_style>|\Z)", re.DOTALL | re.IGNORECASE)
_SCENE_RETRY_LIMIT = 1


def plan_events(plan_text: str) -> tuple[list[str], str]:
    """The numbered events and the outcome of an ACCEPTED (canonical) plan, exactly as written. Structure only."""
    events = [body.strip() for _n, body in _EVENT_TAG_RE.findall(plan_text or "")]
    m = re.search(r"<outcome>(.*?)</outcome>", plan_text or "", re.DOTALL | re.IGNORECASE)
    return events, (m.group(1).strip() if m else "")


def _paragraphs(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def check_scene(raw: str | None, previous_scenes: list[str], lang: str, first: bool) -> tuple[str | None, str, list[str]]:
    """Structure only: (scene text, source_style, problems). Tag pair present, not empty, nothing outside the contract, no paragraph repeated word for word from an
    earlier scene, and the existing language check on the scene."""
    text = raw or ""
    m = _SCENE_RE.search(text)
    if not m:
        return None, "", ["<scene> is missing or not closed with </scene>"]
    scene = m.group(1).strip()
    problems: list[str] = []
    if not scene:
        problems.append("<scene> is empty")
    rest = _SCENE_RE.sub("", text, count=1)
    style = ""
    sm = _STYLE_RE.search(rest)
    if sm and first:
        style = re.sub(r"</?source_style>", "", sm.group(0), flags=re.IGNORECASE).strip()
        rest = _STYLE_RE.sub("", rest, count=1)
    if rest.strip():
        problems.append("text outside <scene> (only the first stretch may add a <source_style> tag)")
    seen = {pp for prev in previous_scenes for pp in _paragraphs(prev)}
    repeated = [pp for pp in _paragraphs(scene) if pp in seen]
    if repeated:
        problems.append(f"{len(repeated)} paragraph(s) repeat an earlier scene word for word")
    if scene:
        chk = G._language_check(scene, lang)
        if not chk.get("ok"):
            problems.append("language check failed: " + str(chk.get("reason")))
    return (scene if not problems else None), style, problems


async def _write_per_event(raw_story: str, plan_text: str, *, lang: str, genre: str, genre2: str, requirement: str, story_length: str, creativity: str, call, art: dict, on_log=None):
    """One call per event. Returns (story, source_style, error). The assembled story is only the scenes joined in order; on any failure nothing is returned as a story and the accepted scenes stay in art['partial_scenes']."""
    events, outcome = plan_events(plan_text)
    if not events:
        return None, "", "per-event writer: the accepted plan has no <event_N> tags"
    base = _build_writer_user(raw_story, plan_text, lang=lang, genre=genre, genre2=genre2, requirement=requirement, story_length=story_length, creativity=creativity)
    tail_old = "\n\nWrite the complete story now."
    assert base.endswith(tail_old)
    head = base[:-len(tail_old)]
    scenes: list[str] = []
    style = ""
    art["scene_calls"] = []
    art["partial_scenes"] = scenes
    for k, ev in enumerate(events, 1):
        last = k == len(events)
        user = (head + "\n\nSTORY WRITTEN SO FAR:\n" + ("\n\n".join(scenes) if scenes else "(nothing yet: this is the first stretch)")
                + f"\n\nCURRENT EVENT (event {k} of {len(events)}):\n{ev}"
                + (f"\n\nThis is the last event. The plan's outcome to reach:\n{outcome}" if last and outcome else "")
                + "\n\n" + (_EVENT_INSTR_FIRST if k == 1 else (_EVENT_INSTR_LAST if last else _EVENT_INSTR_MIDDLE))
                + "\n\nWrite the prose for the current event now.")
        problems: list[str] = []
        scene = None
        for attempt in range(_SCENE_RETRY_LIMIT + 1):
            sent = user if attempt == 0 else user + "\n\nYour previous reply could not be accepted: " + "; ".join(problems[:4]) + ". Write the prose for the current event again in the required <scene> structure, nothing else."
            if on_log:
                await on_log(f"✨ Story Editor (per-event writer): event {k}/{len(events)}" + (" (retry)" if attempt else ""))
            raw = await call(_EVENT_WRITER_SYSTEM if k == 1 else _EVENT_WRITER_SYSTEM_NEXT, sent)
            t_info = {"event": k, "attempt": attempt + 1, "user": sent, "raw": raw or ""}
            art["scene_calls"].append(t_info)
            scene, st, problems = check_scene(raw, scenes, lang, first=(k == 1))
            t_info["problems"] = problems
            if scene:
                if k == 1 and st:
                    style = st
                break
        if not scene:
            return None, "", f"per-event writer failed at event {k}/{len(events)}: " + "; ".join(problems[:3])
        scenes.append(scene)
    return "\n\n".join(scenes), style, ""


def _retry_message(problems: list[str], previous_raw: str) -> str:
    return ("Your previous reply could not be accepted because the plan structure is wrong: " + "; ".join(problems[:6]) + ".\n"
            "Write the complete <story_plan> again with the SAME source_shape, the SAME selected arc and the SAME events (only fix the structure), every tag closed with its own exact name, "
            "each event in its own <event>...</event>. Nothing else.\nPREVIOUS REPLY:\n" + (previous_raw or "")[:6000])


async def run_story_grooming_perstep(
    raw_story: str,
    llm_wf_file: str,
    lang: str = "en",
    genre: str = "",
    genre2: str = "",
    requirement: str = "",
    on_log=None,
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    outline_mode: str = "cinematic",
    story_length: str = "auto",
    dialogue_style: str = "none",
    creativity: str = "balanced",
    accepted_plan: str = "",
    per_event: bool = False,
) -> tuple[str, dict]:
    """Same signature and return shape as run_story_grooming (plus the optional accepted_plan: a plan that was already accepted, used as is, planner skipped - for controlled tests). On any failure the ORIGINAL source text is returned with card["error"] set
    (the caller keeps the working draft); nothing incomplete is ever returned as a result."""
    card = {"clean_groom": True, "final_check": None, "final_check_note": "", "source_style": "", "chat_reply": "", "error": "",
            "second_pass": "disabled (per-step prototype)",
            "perstep": {"plan": "", "planner_raw": [], "writer_raw": "", "problems": [], "calls": 0, "seconds": 0.0}}
    art = card["perstep"]
    if not (raw_story or "").strip():
        card["error"] = "empty_source"
        return raw_story or "", card
    wf_template = None
    if llm_wf_file != GROK_MARKER:
        if not (llm_wf_file or "").strip():
            card["error"] = "no_llm"
            return raw_story, card
        wf_path = LLM_DIR / f"{llm_wf_file}.json"
        try:
            wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
        except Exception as exc:
            card["error"] = f"workflow read failed: {exc}"
            return raw_story, card

    user_prompt = G._build_groom_user_prompt(raw_story, lang=lang, genre=genre, genre2=genre2, requirement=requirement,
                                             story_length=story_length, creativity=creativity)
    t0 = time.time()

    async def call(system_prompt: str, user_text: str):
        art["calls"] += 1
        return await G._call_backend(llm_wf_file, wf_template, system_prompt, user_text, grok_api_key=grok_api_key, grok_model=grok_model,
                                     on_log=on_log, session_id="", reset=True)         # isolated: no history in either direction

    plan_text = accepted_plan.strip() if accepted_plan else None
    if plan_text:
        art["plan"] = plan_text
        art["frozen_plan"] = True
    else:
        if on_log:
            await on_log(f"✨ Story Editor (per-step prototype) → {'Grok' if llm_wf_file == GROK_MARKER else 'Comfy (' + llm_wf_file + ')'}: step 1 event planner")
        # ---- step 1: event planner (structure checked; a wrong structure is never handed to the writer) ----
        plan_text, planner_user = None, user_prompt + "\n\nPlan the story now (events only, no prose)."
        for attempt in range(_PLAN_RETRY_LIMIT + 1):
            raw = await call(_PLANNER_SYSTEM, planner_user)
            art["planner_raw"].append(raw or "")
            plan_text, problems = _parse_plan(raw)
            if plan_text:
                break
            art["problems"].append(problems)
            if on_log:
                await on_log(f"⚠ Story Editor planner attempt {attempt + 1}: {'; '.join(problems[:3])}")
            planner_user = user_prompt + "\n\n" + _retry_message(problems, raw or "")
        if not plan_text:
            card["error"] = "event planner did not return a usable <story_plan>: " + "; ".join(art["problems"][-1][:3])
            art["seconds"] = round(time.time() - t0, 1)
            return raw_story, card
        art["plan"] = plan_text

    # ---- step 2: story writer from the ACCEPTED plan ----
    if on_log:
        await on_log("✨ Story Editor (per-step prototype): step 2 story writer")
    if per_event:
        story_text, card["source_style"], err = await _write_per_event(
            raw_story, plan_text, lang=lang, genre=genre, genre2=genre2, requirement=requirement, story_length=story_length, creativity=creativity,
            call=call, art=art, on_log=on_log)
        result = story_text or ""
        if err:
            card["error"] = err
            art["seconds"] = round(time.time() - t0, 1)
            return raw_story, card
    else:
        writer_user = _build_writer_user(raw_story, plan_text, lang=lang, genre=genre, genre2=genre2, requirement=requirement, story_length=story_length, creativity=creativity)
        art["writer_user"] = writer_user
        result = await call(_WRITER_SYSTEM, writer_user)
        art["writer_raw"] = result or ""
        if not (result or "").strip():
            card["error"] = "LLM returned no story text"
            art["seconds"] = round(time.time() - t0, 1)
            return raw_story, card
        story_text, card["source_style"], card["chat_reply"] = G._parse_create_output(result)

    def _parse(r):
        st, sty, _x = G._parse_create_output(r)
        return st, sty

    story_text, card["source_style"], lang_ok, card["lang_check"], last_raw = await G._correct_story_language(
        story_text, lang, system_prompt=_WRITER_SYSTEM, call=call, parse=_parse, on_log=on_log, source_style=card["source_style"])
    card["source_style"] = await G._ensure_english_style(card["source_style"], call, on_log)
    art["seconds"] = round(time.time() - t0, 1)
    if not lang_ok:
        card["error"] = G._language_failure_message(lang, card["lang_check"])
        card["last_raw"] = last_raw or result
        return raw_story, card
    return story_text, card
