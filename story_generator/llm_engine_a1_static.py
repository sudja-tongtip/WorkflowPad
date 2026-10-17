"""
llm_engine_a1_static.py — EXPERIMENTAL A1 for a STATIC description (one frozen moment). Not wired by itself: it is called by
llm_engine_a1_novel.run_a1_routed after A1 Novel answered exactly STATIC.

TWO calls in ONE conversation session:
  Call 1  STORY PLANNER   (reset session): source + user genre/requirements + exactly one randomly drawn Groom arc  ->  <story_outline>
          (cast, source moment, five concrete plot beats, outcome). No reference tags, no macro events.
  Call 2  REFERENCE BUILDER (same session_id, reset_session=False / Grok: history): turns the ACCEPTED outline into <arc_plan> (the auditable
          step -> event mapping) + the full character/objects/locations/world/continuity reference + chronological macro events.
The static image is a continuity ANCHOR inside the story, not text to copy. The arc is drawn once per run_a1_static call; planner retries
and builder retries reuse it. A malformed/invalid outline is a PLANNER failure: the builder is never called with it. A builder retry stays in
the same session and never re-runs the planner.

Genre resolution (project convention: genre = primary, genre2 = secondary, "none"/empty = unset; keys equal groom_genre_arcs keys):
    1. genre if supported;  2. else genre2 if supported;  3. else FALLBACK "drama".
Diagnostics (arc, outline, plan, validation issues, anchor, every attempt's prompt/raw/parse outcome) go to chat_capture and logs only; the
A1 dict keeps its seven fields, so A2 consumes it unchanged.
"""

__version__ = "20261102_a1_static_s18_conversation"

import json
import random
import re
import unicodedata
import uuid

from .constants import LLM_DIR, lang_prompt_name
from .groom_genre_arcs import _GENRE_ARCS, _GENRE_GOALS
from .a1_static_genre_arcs import _STATIC_GENRE_ARCS, _STATIC_GENRE_GOALS      # arcs owned by A1 Static (preferred when the genre has one)
try:
    from .a1_static_genre_arcs import _STATIC_GLOBAL_RULES
except ImportError:
    _STATIC_GLOBAL_RULES = ""
from .llm_engine_a1 import (
    GROK_MARKER, _a1_max_tokens, _call_grok_text, _call_local_text, _parse_a1_tags,
)

FALLBACK_GENRE = "drama"
_ARC_RNG = random.Random()          # private instance: no global random.seed(), no effect on unrelated randomness
_PARTS_RE = re.compile(r"^\s*HAPPENS:\s*(.*?)\s*DETAILS?:\s*(.*?)\s*$", re.DOTALL)
_ARC_PLAN_RE = re.compile(r"<arc_plan>(.*?)</arc_plan>", re.DOTALL)
_OUTLINE_RE = re.compile(r"<story_outline>(.*?)</story_outline>", re.DOTALL)

_PLANNER_SYSTEM_PROMPT = r"""You are an expert STORY PLANNER. The input describes ONE static moment. You invent the whole short story around it, following the selected genre arc from its first step to its outcome, so that images made from the story, viewed in order, tell it. The static input is a CONTINUITY CONSTRAINT - a state that must hold at one instant of the story - not text to copy. A genre arc is given; Python chose it, you adapt it to this source.

Think silently in this order (PLOT FIRST, ANCHOR SECOND), then output ONLY the requested <story_outline> block:

PHASE A - CONSTRUCT THE PLOT
1. Understand the source (situation, characters, relationships, objects, place, every fact it states) and the arc as a narrative framework: its steps are functions you realise with your own invented events, never wording to copy or translate.
2. Invent a coherent plot across ALL five steps as if the source image did not exist. Each step is a passage of the story with several connected developments: someone acts or speaks, who or what reacts, answers or resists, what a character then does about it, and the meaningful change or result - and it ends in a situation (who is where, what is held or worn, what has changed) from which the next step starts. Use concrete events that can be SEEN (someone or something arrives, a sound or a line makes a character act, an object is used or changes state, a decision becomes an action, a position or relationship changes, a consequence follows). Thinking, gazing, standing still or describing clothing is never the development of a whole step; every step, step 1 included, has at least two connected developments when the story allows. PROGRESSIVE POSE: the main character's physical pose moves forward in every step after the first - each step ends in a clearly different body pose or body state (for example sitting -> standing, a raised leg -> both feet down, a hand holding -> the hand released), caused by that step's events and physically reachable from where the previous step ended. A new expression, gaze, feeling or wording is not a new pose. Never return to an earlier pose unless the story shows the return as a purposeful new action. Through every change hand occupancy, held objects, body support, leg positions, clothing and the distances between people stay continuous. A step that would not change the pose is rewritten so that it does. The steps are consecutive in time and place - each starts exactly where the previous one ended, and someone already in front of a person does not arrive again later. The last step and the outcome show a result that actually happens because of the earlier actions, not merely a feeling or a pose. Do not add new characters or places unless the plot truly needs them; the existing cast and setting may carry a complete plot. No major plot twist just for drama; special requirements, if given, take precedence over the arc.

PHASE B - PLACE THE SOURCE ANCHOR
3. The source state is the STARTING state of the story: source_moment_step is 1, and step 1 opens with the source pose exactly, with nothing before it. It is a continuity checkpoint at the first instant of step 1, never a plot beat, never a separate event and never the step's text; step 1 keeps its own developments (something is said, noticed or done) while that pose still holds, and the pose first changes in step 2. Adapt step 1 so that it starts from the source state, without removing the plot you built in Phase A.
4. At that instant everything the source states must be true: who is visible and how many, poses and hand positions, clothing and accessories, objects and who holds them, positions and distances, place, light. If the source limits who is in the picture (for example "no other people"), nobody else is visible at that instant, though others may appear before or after it. After that first instant, things change only through stated events. Never change something and change it back merely to fit the picture. Keep the story in the source's own place; add an adjoining area only when the plot needs it.

PHASE C - VERIFY BEFORE ANSWERING (fix the plot first if a check fails)
5. (a) Read step_1 to step_5 without source_moment: is it still a complete cause-and-effect story? (b) Does every step contain real developments, none being only a description of a state, pose, gaze, thought or clothing? (c) Does step 1 open with the source pose exactly and still have a plot development of its own? (d) Does each later step end in a different, physically reachable body pose of the main character (not only a new expression), starting from the state the previous step left? (e) Does the outcome really happen?
6. Names: give every character, including an animal or creature, a real personal given name that fits the setting and era (a Thai given name in a Thai setting), never a role, descriptive word or pronoun. Tokens, trigger or LoRA words, file names and other metadata in the input (such as something like name_style or ta86_inkriot) are not people and never names.
Write every step as plain short prose without labels or notes: never "(Source Moment)", "(Anchor)", "(unchanged)" or words such as "source image" or "original picture" inside a step.
Write the outline in the requested output language."""

_PLANNER_FORMAT = (
    "Reply with ONLY this block (plain text lines, no other tags, no commentary):\n"
    "<story_outline>\n"
    "cast: Name = the source's label for this character (one per character, animals included), separated by semicolons\n"
    "selected_arc: the Arc ID given above\n"
    "source_moment_step: 1 (the source state is the starting state of the story)\n"
    "source_moment: the exact physical snapshot that must be true at one identifiable instant inside the selected step (who is where, poses, hands, objects, clothing, contact, place) and it holds at the first instant of step 1 - a continuity checkpoint, not a plot beat or an event\n"
    "step_1: a short prose passage of several connected, visible developments (who does or says what, how it is answered, what changes and where it leaves the characters); no labels, no notes\n"
    "step_2: ...\nstep_3: ...\nstep_4: ...\nstep_5: ...\n"
    "outcome: the visible result that comes of the earlier events (not only a feeling or a pose)\n"
    "</story_outline>"
)

_BUILDER_SYSTEM_PROMPT = r"""You are a REFERENCE BUILDER for a visual-story pipeline. Earlier in this conversation you wrote a STORY OUTLINE for a static source. That outline is now the AUTHORITATIVE creative plan: build the production reference and the five macro events from exactly that plan. Do not change its events, order, names or outcome, and add no unrelated plotlines.

Think silently; output ONLY the requested tags.
1. <arc_plan> FIRST (the auditable mapping): selected_arc and source_moment_step as in the outline; step_1 ... step_5, each "E<n>: <a one-line summary of that outline beat>" - one step is exactly one macro event, so step n is carried by E<n> and by no other event; source_anchor_event = E<source_moment_step>.
2. CHARACTERS: one <charN.*> set for every cast member of the outline, with the outline's cast names copied exactly - the same spelling and script in every event and field, never a transliteration or a new name (a real personal given name, never a role or pronoun; the source's own labels such as "the woman" go in <charN.alias>). age, body, face, skin, marks and init_hair describe the person before any event; init_cloth names the actual garments and accessories worn in the source. Keep every appearance and clothing detail the source gives; where it is silent, make one simple concrete choice that does not contradict it. Never write placeholders or leave a set empty.
3. important_objects: discrete external objects that matter (what, who holds it, where, how it is used or changes); never body parts or clothing worn. locations: the places; world_sheet: environment, lighting, spatial limits; continuity_notes: facts that stay true across events (what is worn, held, where).
4. MACRO EVENTS: exactly five tags, E1 ... E5, in order; E<n> realises outline step n (never more or fewer events - several actions inside one step stay inside that one event). Write each as plain connected prose inside its own tag, <E1>...</E1>, closed with its own </E1>. The outline beat is only the skeleton of its passage: do not copy its wording - write a full passage of your own that carries the story from where the previous event ended to where this beat ends, adding the concrete actions, spoken lines and reactions in between. A macro event is a connected passage of story with several meaningful developments, not a single frozen pose and not an inventory of visual details: it picks up from the situation the previous event ended in; someone acts or speaks (dialogue is welcome when it moves the story - written in the output language, quoted inside the prose); something or someone reacts, answers or resists; a character then does something about it; and it ends in a new situation (positions, contact, objects, clothing, who is present) that the next event starts from. Name who does what to whom, no pronouns for people. POSE PROGRESSION: E1 keeps the source's starting pose exactly. Every later event ends in a clearly different physical pose or body state of the main character (for example sitting -> standing, a raised leg -> both feet down, a hand holding -> the hand released), caused by what happens in that event and reachable from where the previous event ended. A new expression, gaze, feeling or wording is not a new pose: never restate the previous pose with only those changes, and never return to an earlier pose unless the story shows it as a purposeful action. Keep hand occupancy, held objects, body support, leg positions, clothing state and the distances between people continuous through every change; if a beat would not change the pose, give it an action that does. A pose or position never changes at random. Mood, tension or closeness is not a development: show what is done or said. Clothing, armour and objects keep the state established earlier unless this passage visibly changes them (say how); never describe a state the story contradicts. Never write HAPPENS: or DETAILS: labels, bullet lists, parenthetical remarks or notes, or a separate list of appearance details; appearance, clothing, light, place and objects appear inside the prose only where they matter to what happens.
5. SOURCE MOMENT: the outline decides the events, their order, causes and the anchor event; the ORIGINAL static source (the first message) decides the facts of the image, and the outline may never change them. The anchor event (E<source_moment_step>) is a moving passage of several connected developments like every other event, not a description of the image: expand the outline's step into actions, spoken lines and reactions, and place the source snapshot at the fitting point inside it - the instant the story reaches the pictured state. At that point state the source facts that matter to the picture in full, inside the prose, exactly as the source gives them (who is visible and how many, poses and hand positions, clothing and accessories, held objects and who holds them, distances and contact, place, background, light; a hand only if the source names it), and let nothing change them while that instant lasts. Never open or end the passage with a list of appearance details and never reduce it to holding a pose. What comes before must lead to that state (an earlier touch, move or change of clothing is visibly undone in the story before the snapshot, so the state matches the source); what follows may change it only through what the passage shows, and the next event starts from the state this one ends in. If the outline's beat for this step is itself only a description of the image, build connected actions around the snapshot without changing the outline's goal, main events or outcome. Never write a stand-in such as "static image", "initial state", "as in the source" or "unchanged". Invent no pose, garment or object that contradicts the source, and never describe a body covered by armour or clothing as if seen directly; clothing, objects and place change only through what the story shows. The plot adapts to the image, never the image to the plot. Do not copy the image description into several events.
LANGUAGE: every tag's text, events and every reference field (age, gender, nationality, body, face, skin, init_hair, init_cloth, important_objects, locations, world_sheet, continuity_notes), in the requested output language; only source_style is English. source_style: empty unless the input itself is a style-describing prompt or states one overall visual/art style; then one dense paragraph of subject-free image-style descriptors. Keep personal names identical everywhere.
CHECK: exactly five events E1-E5, one per outline step, in order, the last delivering the outline's outcome; each is connected prose with several developments and ends in a changed situation - none is a pose, an appearance inventory or a list; the anchor event (E1) opens with the source pose exactly, stated in full inside a moving passage (not a recital of the image), and every later event ends in a different, reachable body pose (not only a new expression); no HAPPENS:/DETAILS: labels; every cast member has a set; every <charN.name> is a proper personal name; all fields in the requested language.
Return only the requested tags. Never answer STATIC."""

_STATIC_SYSTEM_PROMPT = _BUILDER_SYSTEM_PROMPT      # name kept for callers/tests that read the "static system prompt"


# ----------------------------------------------------------------------------------------------------------------------
# Python: genre resolution + one random arc (data lookup and serialisation only)
# ----------------------------------------------------------------------------------------------------------------------
def _resolve_genre(genre: str, genre2: str) -> tuple[str, str]:
    """-> (genre key, source) with source in {"genre", "genre2", "fallback"}. Never random, never an unrelated pick."""
    g = (genre or "").strip()
    g2 = (genre2 or "").strip()
    if g in _GENRE_ARCS:
        return g, "genre"
    if g2 in _GENRE_ARCS:
        return g2, "genre2"
    return FALLBACK_GENRE, "fallback"


def _select_arc(genre: str, genre2: str, rng: random.Random | None = None) -> dict:
    """Resolve the effective genre and draw exactly ONE of its four arcs. Returns a copy: genre, genre_source, goal, id, name, steps."""
    key, source = _resolve_genre(genre, genre2)
    if key in _STATIC_GENRE_ARCS:                         # Static-owned arc; other genres keep the Groom arcs (transitional fallback)
        arc = (rng or _ARC_RNG).choice(_STATIC_GENRE_ARCS[key])
        return {"genre": key, "genre_source": source, "goal": _STATIC_GENRE_GOALS.get(key, ""), "arc_source": "static", "rules": _STATIC_GLOBAL_RULES,
                "id": arc["id"], "name": arc["name"], "steps": list(arc["steps"])}
    arc = (rng or _ARC_RNG).choice(_GENRE_ARCS[key])
    return {"genre": key, "genre_source": source, "goal": _GENRE_GOALS.get(key, ""), "arc_source": "groom",
            "id": arc["id"], "name": arc["name"], "steps": list(arc["steps"])}


def _format_arc_block(arc: dict) -> str:
    lines = ["SELECTED STATIC ARC" if arc.get("arc_source") == "static" else "SELECTED GROOM ARC", f"Genre: {arc['genre']}"]
    if arc.get("goal"):
        lines.append(f"Genre direction: {arc['goal']}")
    lines += [f"Arc ID: {arc['id']}", f"Arc Name: {arc['name']}", "", "Steps:"]
    lines += [f"{i}. {s}" for i, s in enumerate(arc["steps"], 1)]
    return "\n".join(lines)


_ARC_PLAN_FORMAT = (
    "(0) first, the story outline covering ALL five steps:\n"
    "<arc_plan>\nselected_arc: the Arc ID given below\nsource_moment_step: the step number (1-5) the source state belongs to\n"
    "step_1: E1: your own story beat for step 1\nstep_2: E2: ...\nstep_3: E3: ...\nstep_4: E4: ...\nstep_5: E5: ...\n"
    "source_anchor_event: E<the source_moment_step number> (one step = one event)\n</arc_plan>\n"
)


# ----------------------------------------------------------------------------------------------------------------------
# <arc_plan> parsing and structural validation (report only: never moves or rewrites events)
# ----------------------------------------------------------------------------------------------------------------------
def _parse_arc_plan(raw: str | None) -> dict | None:
    m = _ARC_PLAN_RE.search(raw or "")
    if not m:
        return None
    plan = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep and k.strip():
            plan[k.strip().lower()] = v.strip()
    return plan


def _plan_ids(text: str) -> list[int]:
    """Event numbers named in a plan line: 'E3', 'E1-E2' / 'E1-2' (inclusive range), 'E1, E3'."""
    ids: list[int] = []
    s = text or ""
    for m in re.finditer(r"E(\d+)\s*(?:[-\u2013\u2014]\s*E?(\d+))?", s):
        a, b = int(m.group(1)), (int(m.group(2)) if m.group(2) else None)
        ids += list(range(a, b + 1)) if b and b >= a else [a]
    return ids


def _validate_arc_plan(plan: dict | None, arc: dict, event_ids: list[str]) -> list[str]:
    """Structural checks only (the plan exists, covers every step with existing events in order, names a real anchor that
    belongs to its step, and every event is accounted for). Never rewrites or moves events. Returns issues ([] = consistent)."""
    if plan is None:
        return ["arc_plan missing or unreadable"]
    issues = []
    have = {int(e[1:]) for e in event_ids if re.fullmatch(r"E\d+", e)}
    if (plan.get("selected_arc") or "").strip() != arc["id"]:
        issues.append(f"selected_arc {plan.get('selected_arc')!r} != selected arc id {arc['id']!r}")
    n_steps = len(arc["steps"])
    ms = (plan.get("source_moment_step") or "").strip()
    if not ms.isdigit() or not (1 <= int(ms) <= n_steps):
        issues.append(f"source_moment_step {ms!r} is not a step number 1..{n_steps}")
    per_step: dict[int, list[int]] = {}
    for n in range(1, n_steps + 1):
        line = (plan.get(f"step_{n}") or "").strip()
        ids = _plan_ids(line)
        if not line:
            issues.append(f"step_{n} missing in the plan")
        elif not ids:
            issues.append(f"step_{n} names no event id")
        elif any(i not in have for i in ids):
            issues.append(f"step_{n} names events that do not exist: {['E%d' % i for i in ids if i not in have]}")
        else:
            per_step[n] = ids
    steps_in_order = [per_step[n] for n in sorted(per_step)]
    for a, b in zip(steps_in_order, steps_in_order[1:]):
        if max(a) > min(b):
            issues.append("plan steps are not in chronological event order")
            break
    ev = (plan.get("source_anchor_event") or "").strip()
    if ev and (not re.fullmatch(r"E\d+", ev) or ev not in event_ids):
        issues.append(f"source_anchor_event {ev!r} is not an existing event id")
    elif ev and ms.isdigit() and int(ms) in per_step and int(ev[1:]) not in per_step[int(ms)]:
        issues.append(f"source_anchor_event {ev} is not among the events planned for step {ms}")
    for n in range(1, n_steps + 1):
        if n in per_step and per_step[n] != [n]:
            issues.append(f"step_{n} maps to {['E%d' % i for i in per_step[n]]}; one arc step must be exactly event E{n}")
    covered = {i for ids in per_step.values() for i in ids}
    if len(per_step) == n_steps and have - covered:
        issues.append(f"events not accounted for by the plan: {['E%d' % i for i in sorted(have - covered)]}")
    return issues



# ----------------------------------------------------------------------------------------------------------------------
# Structural normalisation of event tags (before the A1 tag parser). Only forms whose boundaries are unambiguous are rewritten:
#   P1  <E n>A</E n> <DETAILS>B</DETAILS>                      -> <E n>A DETAILS: B</E n>   (a DETAILS tag right after the event's closer)
#   P2  <E n>A <DETAILS>B</DETAILS>  + end of text / <E n+1>    -> <E n>A DETAILS: B</E n>   (closer missing; the closed DETAILS block ends the event)
#   P3  <E n><HAPPENS>A</HAPPENS><DETAILS>B</DETAILS></E n>    -> <E n>HAPPENS: A DETAILS: B</E n>
# A, B contain no '<'. Text is copied verbatim (nothing is trimmed, guessed or rewritten). All-or-nothing: if any HAPPENS/DETAILS tag
# is left over after these rewrites the ORIGINAL text is returned untouched (the caller's parser then rejects it / retries).
# ----------------------------------------------------------------------------------------------------------------------
_P3_RE = re.compile(r"<E(\d+)>\s*<HAPPENS>([^<]*)</HAPPENS>\s*<DETAILS>([^<]*)</DETAILS>\s*</E\1>", re.IGNORECASE)
_P1_RE = re.compile(r"<E(\d+)>([^<]*)</E\1>\s*<DETAILS>([^<]*)</DETAILS>", re.IGNORECASE)
_P2_RE = re.compile(r"<E(\d+)>([^<]*?)\s*<DETAILS>([^<]*)</DETAILS>(?=\s*(?:<E\d+>|\Z))", re.IGNORECASE)
_STRAY_RE = re.compile(r"</?(?:HAPPENS|DETAILS)>", re.IGNORECASE)


def _normalize_event_tags(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not _STRAY_RE.search(text or ""):
        return text, notes
    count = {"P1": 0, "P2": 0, "P3": 0}

    def p3(m):
        count["P3"] += 1
        return f"<E{m.group(1)}>HAPPENS: {m.group(2).strip()} DETAILS: {m.group(3).strip()}</E{m.group(1)}>"

    def p1(m):
        count["P1"] += 1
        return f"<E{m.group(1)}>{m.group(2)} DETAILS: {m.group(3).strip()}</E{m.group(1)}>"

    def p2(m):
        count["P2"] += 1
        return f"<E{m.group(1)}>{m.group(2)} DETAILS: {m.group(3).strip()}</E{m.group(1)}>"

    out = _P3_RE.sub(p3, text)
    out = _P1_RE.sub(p1, out)
    out = _P2_RE.sub(p2, out)
    if _STRAY_RE.search(out):
        return text, ["event tags: HAPPENS/DETAILS tags in a form that cannot be recovered unambiguously - left unchanged"]
    notes.append("event tags normalised (" + ", ".join(f"{k} x{v}" for k, v in count.items() if v) + "): DETAILS tag moved inside its event")
    return out, notes


_CHAR_SET_RE = re.compile(r"<(char\d+)\.([a-z_]+)>(.*?)</\1\.\2>", re.DOTALL)
_EMPTY_VALUES = {"", "none", "n/a", "null", "-", "ไม่มี"}


def _drop_empty_characters(result: dict) -> list[str]:
    """Remove a <charN.*> set whose EVERY field is empty / none / n/a (an empty placeholder character). A set that has any real
    value (even if its name is 'none') is kept - dropping it would lose a real character. Returns the dropped ids."""
    sheet = result.get("character_sheet") or ""
    fields: dict[str, dict[str, str]] = {}
    for m in _CHAR_SET_RE.finditer(sheet):
        fields.setdefault(m.group(1), {})[m.group(2)] = m.group(3).strip()
    dropped = [cid for cid, f in fields.items() if all((v or "").strip().lower() in _EMPTY_VALUES for v in f.values())]
    if dropped:
        keep = [line for line in sheet.splitlines() if not any(line.startswith(f"<{cid}.") for cid in dropped)]
        result["character_sheet"] = "\n".join(keep).strip()
    return dropped


def _merge_event_parts(result: dict) -> None:
    """Explicit-label formatting only: 'HAPPENS: a DETAILS: b' -> 'a b'; a lone label is removed, the text is kept."""
    for e in result.get("macro_events") or []:
        content = str(e.get("content") or "")
        m = _PARTS_RE.match(content)
        if m:
            e["content"] = (m.group(1) + " " + m.group(2)).strip()
        else:
            e["content"] = re.sub(r"\s*(?<![A-Za-z])DETAILS?:\s*", " ", re.sub(r"^\s*HAPPENS:\s*", "", content)).strip()



def _lang_name(desc_lang: str) -> str:
    return lang_prompt_name(desc_lang or "en")


def _build_planner_user_prompt(story_text: str, *, desc_lang: str, requirement: str = "", arc: dict) -> str:
    parts = [f"Output language for the outline: {_lang_name(desc_lang)}"]
    if (requirement or "").strip():
        parts.append(f"Special requirements (they take precedence over the arc): {(requirement or '').strip()}")
    parts.append(_format_arc_block(arc))
    parts.append("SOURCE (ONE static moment - a continuity constraint of your story, not text to copy):\n" + (story_text or ""))
    parts.append(_PLANNER_FORMAT)
    return "\n\n".join(parts)


def _build_builder_user_prompt(*, desc_lang: str, requirement: str = "", cast: str = "") -> str:
    # the OUTPUT FORMAT (reference tags + events) is the A1 Novel one, so the same parser reads it; the story and the accepted outline are
    # already in the conversation history, so neither is sent again
    from .llm_engine_a1_novel import _build_novel_user_prompt
    head = _build_novel_user_prompt("", desc_lang=desc_lang, requirement=requirement, allow_static=False).partition("STORY:\n")[0]
    if "(1) characters," in head:
        head = head.replace("(1) characters,", _ARC_PLAN_FORMAT.replace("the Arc ID given below", "the arc id of your outline") + "(1) characters,", 1)
    fmt_note = ("Event format: exactly five events E1-E5, one per arc step, each ONE tag, <E1>connected prose of that step's passage</E1>, with its own closing </E1>. "
                "No HAPPENS:/DETAILS: labels and no lists. Every <E#> is closed with its own </E#>.")
    cast_line = (f"Cast to use, copied exactly in every event and field (same spelling and script): {cast.strip()}\n" if (cast or "").strip() else "")
    return ("Build the reference now from the ACCEPTED STORY OUTLINE above (your previous reply), keeping its names, events, order and outcome unchanged; "
            "the static source in the first message supplies the facts for the anchor event and the character sheet. Write the five events as connected prose passages (one per arc step); "
            "the anchor event must state the real source facts inside its prose, not a stand-in.\n" + cast_line + "\n"
            + head + fmt_note + f"\n\nReminder: write every tag's text, events and all reference fields, in {_lang_name(desc_lang)}; "
            "only source_style stays English; keep personal names unchanged. Before you finish, check that there are exactly five events E1-E5, each a connected prose passage with several developments.")


# ----------------------------------------------------------------------------------------------------------------------
# <story_outline> parsing and structural validation (Call 1)
# ----------------------------------------------------------------------------------------------------------------------
_OUTLINE_KEYS = ("cast", "selected_arc", "source_moment_step", "source_moment", "step_1", "step_2", "step_3", "step_4", "step_5", "outcome")


def _parse_story_outline(raw: str | None) -> dict | None:
    m = _OUTLINE_RE.search(raw or "")
    if not m:
        return None
    out: dict[str, str] = {}
    cur = None
    for line in m.group(1).splitlines():
        km = re.match(r"^\s*(" + "|".join(_OUTLINE_KEYS) + r")\s*:\s*(.*)$", line, re.IGNORECASE)
        if km:
            cur = km.group(1).lower()
            out[cur] = km.group(2).strip()
        elif cur and line.strip():
            out[cur] = (out[cur] + " " + line.strip()).strip()
    return out


def _validate_outline(outline: dict | None, arc: dict) -> list[str]:
    """Structural only: the block exists, names the drawn arc, a valid source step, a cast, and a non-empty beat for every step and the outcome."""
    if outline is None:
        return ["story_outline missing or unreadable"]
    issues = []
    if (outline.get("selected_arc") or "").strip() != arc["id"]:
        issues.append(f"selected_arc {outline.get('selected_arc')!r} != selected arc id {arc['id']!r}")
    n = len(arc["steps"])
    ms = (outline.get("source_moment_step") or "").strip()
    if not ms.isdigit() or not (1 <= int(ms) <= n):
        issues.append(f"source_moment_step {ms!r} is not a step number 1..{n}")
    for key in ("cast", "source_moment", "outcome") + tuple(f"step_{i}" for i in range(1, n + 1)):
        if not (outline.get(key) or "").strip():
            issues.append(f"{key} missing or empty")
    return issues


def _outline_vs_plan_issues(outline: dict, plan: dict | None) -> list[str]:
    """Builder fidelity (structural): the arc_plan must name the same arc and source step as the accepted outline."""
    if plan is None:
        return []
    issues = []
    if (plan.get("selected_arc") or "").strip() != (outline.get("selected_arc") or "").strip():
        issues.append("arc_plan selected_arc differs from the accepted outline")
    if (plan.get("source_moment_step") or "").strip() != (outline.get("source_moment_step") or "").strip():
        issues.append("arc_plan source_moment_step differs from the accepted outline")
    return issues



_PLAN_HEAD_RE = re.compile(r"^\s*((?:E\d+(?:\s*[-\u2013\u2014]\s*E?\d+)?[\s,;/&]*)+):")


def _plan_head_ids(line: str) -> list[int]:
    """Event numbers in the id list at the START of a plan line ('E2: ...', 'E1-E2: ...', 'E1, E3: ...'); [] when the line has no such head."""
    m = _PLAN_HEAD_RE.match(line or "")
    return sorted(set(_plan_ids(m.group(1)))) if m else []


def _resolve_anchor(plan: dict | None, outline: dict, event_ids: list[str]) -> dict:
    """Which event is the source anchor? One arc step = one macro event, so the Planner's (validated) source_moment_step N IS event E<N>.
      * Builder wrote source_anchor_event naming an existing event: kept as written (a difference from E<N> or from the builder's own step is only
        REPORTED, never overridden).
      * otherwise: E<N>, when it exists (method one_step_one_event); a malformed builder anchor is reported and ignored.
      * E<N> missing / no step: unresolved - nothing is guessed.
    Returns raw_anchor, resolved_anchor ('' = unresolved), resolution_method, issues."""
    res = {"raw_anchor": "", "resolved_anchor": "", "resolution_method": "none", "issues": []}
    have = set(event_ids)
    pstep = (outline.get("source_moment_step") or "").strip()          # planner authority
    raw = ((plan or {}).get("source_anchor_event") or "").strip()
    res["raw_anchor"] = raw
    if plan is None:
        res["issues"].append("arc_plan missing or unreadable")
    else:
        bstep = (plan.get("source_moment_step") or "").strip()
        if bstep and bstep != pstep:
            res["issues"].append(f"builder source_moment_step {bstep!r} differs from the planner's {pstep!r}")
    expected = f"E{pstep}" if pstep.isdigit() else ""
    if raw and re.fullmatch(r"E\d+", raw) and raw in have:
        res["resolved_anchor"], res["resolution_method"] = raw, "builder"
        if expected and raw != expected:
            res["issues"].append(f"builder anchor {raw} differs from {expected} (planner step {pstep})")
        return res
    if raw:
        res["issues"].append(f"builder source_anchor_event {raw!r} is not an existing event id - ignored")
    if not expected:
        res["issues"].append(f"planner source_moment_step {pstep!r} is not a step number")
    elif expected not in have:
        res["issues"].append(f"planner step {pstep} -> {expected}, which does not exist")
    else:
        res["resolved_anchor"], res["resolution_method"] = expected, "one_step_one_event"
    return res


_LABELS_RE = re.compile(r"^\s*HAPPENS:\s*(.*?)\s*DETAILS?:\s*(.*?)\s*$", re.DOTALL)
_PLACEHOLDER_PHRASES = (
    "ภาพคงที่", "สถานะเริ่มต้น", "รายละเอียดตามภาพต้นฉบับ", "ตามภาพต้นฉบับ", "ตามต้นฉบับ", "เหมือนต้นฉบับ", "เหมือนภาพต้นฉบับ", "เหมือนเดิม",
    "ไม่เปลี่ยนแปลง", "static image", "initial state", "same as source", "same as the source", "same as the image", "as in the source",
    "as the source", "as source", "see source", "unchanged", "n/a", "none",
)


def _is_placeholder_details(text: str) -> bool:
    """Structural stand-in test for an anchor's DETAILS: empty, or a short text that is only a known stand-in phrase (no meaning analysis)."""
    # keep letters, Thai vowel/tone marks (category M) and digits; turn punctuation, symbols and whitespace into single spaces
    norm = " ".join("".join(ch if unicodedata.category(ch)[0] in "LMN" else " " for ch in (text or "")).split()).lower()
    if not norm:
        return True
    return len(norm) <= 40 and any(p in norm for p in _PLACEHOLDER_PHRASES)


def _validate_event_blocks(events: list[dict], anchor: str) -> list[str]:
    """Run on the RAW event blocks (after structural normalisation, BEFORE _merge_event_parts removes the labels): every event has a HAPPENS: part
    and a DETAILS: label, the anchor event is identified, and its DETAILS is non-empty and not a stand-in. Report only; nothing is rewritten or filled in."""
    issues: list[str] = []
    ids = [e["id"] for e in events]
    parts: dict[str, tuple[str, str] | None] = {}
    for e in events:
        m = _LABELS_RE.match(str(e.get("content") or ""))
        parts[e["id"]] = (m.group(1), m.group(2)) if m else None
        content = str(e.get("content") or "")
        if not m:
            has_h = bool(re.search(r"(?:^|\s)HAPPENS:", content)); has_d = bool(re.search(r"DETAILS?:", content))
            issues.append(f"{e['id']} lacks " + ("a DETAILS: part" if has_h and not has_d else "a HAPPENS: part" if has_d and not has_h else "both HAPPENS: and DETAILS: parts"))
        elif not m.group(1).strip():
            issues.append(f"{e['id']} has an empty HAPPENS: part")
    if not re.fullmatch(r"E\d+", anchor or "") or anchor not in ids:
        issues.append("source_anchor_event is missing or not an existing event, so the anchor's DETAILS cannot be checked")
    elif parts.get(anchor) is not None and _is_placeholder_details(parts[anchor][1]):
        issues.append(f"anchor event {anchor} has empty or stand-in DETAILS (the real source facts must be written out)")
    return issues


def _validate_prose_events(events: list[dict], anchor: str, n_expected: int) -> list[str]:
    """Structural checks on the raw event tags: exactly E1..E<n> (one per arc step, in order), each non-empty, the anchor is an existing event
    whose text is not a stand-in. Report only; nothing is rewritten. (HAPPENS:/DETAILS: labels are not required; if a model still writes them
    _merge_event_parts removes the labels and keeps the text.)"""
    issues: list[str] = []
    ids = [e["id"] for e in events]
    want = [f"E{i}" for i in range(1, n_expected + 1)]
    if ids != want:
        issues.append(f"expected exactly the events E1..E{n_expected} (one per arc step, no extra events), got {ids}")
    for e in events:
        if not str(e.get("content") or "").strip():
            issues.append(f"{e['id']} is empty")
    if not re.fullmatch(r"E\d+", anchor or "") or anchor not in ids:
        issues.append("the source anchor event is missing or not an existing event")
    else:
        text = next(str(e.get("content") or "") for e in events if e["id"] == anchor)
        if _is_placeholder_details(text):
            issues.append(f"anchor event {anchor} is empty or only a stand-in (it must state the real source facts inside its prose)")
    return issues


def _cast_names(cast: str) -> list[str]:
    """'Risa = the woman; Rex = the dog' -> ['Risa', 'Rex'] (the text before '=' of each ';'-separated item)."""
    return [p.split("=")[0].strip() for p in (cast or "").split(";") if p.split("=")[0].strip()]


_CONVERSATION_MODE = True           # True: step-by-step conversation (a1_static_conversation.py); False: the previous planner + builder two-pass code below


async def run_a1_static(
    story_text: str,
    llm_wf_file: str,
    on_err,
    on_log=None,
    mode: str = "cinematic",      # API compatibility with run_a1_vnext / run_a1_novel
    genre: str = "",
    genre2: str = "",
    requirement: str = "",
    desc_lang: str = "en",
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    chat_capture: dict | None = None,
) -> dict | None:
    """Entry point used by run_a1_routed. Takes the ORIGINAL input text."""
    if not _CONVERSATION_MODE:
        return await _run_a1_static_two_pass(story_text, llm_wf_file, on_err, on_log, mode=mode, genre=genre, genre2=genre2, requirement=requirement,
                                             desc_lang=desc_lang, grok_api_key=grok_api_key, grok_model=grok_model, chat_capture=chat_capture)
    if not (story_text or "").strip():
        await on_err("Story Bible (static): input is empty")
        return None
    wf_template = None
    if llm_wf_file != GROK_MARKER:
        if not (llm_wf_file or "").strip():
            await on_err("Story Bible (static): no local LLM workflow selected")
            return None
        wf_path = LLM_DIR / f"{llm_wf_file}.json"
        if not wf_path.exists():
            await on_err(f"ไม่พบ {wf_path}")
            return None
        try:
            wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
        except Exception as exc:
            await on_err(f"Story Bible (static) workflow read failed: {exc}")
            return None
    from .a1_static_conversation import run_a1_static_conversation
    return await run_a1_static_conversation(story_text, llm_wf_file, on_err, on_log, mode=mode, genre=genre, genre2=genre2, requirement=requirement, desc_lang=desc_lang,
                                            grok_api_key=grok_api_key, grok_model=grok_model, chat_capture=chat_capture, wf_template=wf_template)


async def _run_a1_static_two_pass(
    story_text: str,
    llm_wf_file: str,
    on_err,
    on_log=None,
    mode: str = "cinematic",      # API compatibility with run_a1_vnext / run_a1_novel
    genre: str = "",
    genre2: str = "",
    requirement: str = "",
    desc_lang: str = "en",
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    chat_capture: dict | None = None,
) -> dict | None:
    """Planner call, then Builder call in the same session. Takes the ORIGINAL input text."""
    if not (story_text or "").strip():
        await on_err("Story Bible (static): input is empty")
        return None
    wf_template = None
    if llm_wf_file != GROK_MARKER:
        if not (llm_wf_file or "").strip():
            await on_err("Story Bible (static): no local LLM workflow selected")
            return None
        wf_path = LLM_DIR / f"{llm_wf_file}.json"
        if not wf_path.exists():
            await on_err(f"ไม่พบ {wf_path}")
            return None
        try:
            wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
        except Exception as exc:
            await on_err(f"Story Bible (static) workflow read failed: {exc}")
            return None

    is_grok = llm_wf_file == GROK_MARKER
    arc = _select_arc(genre, genre2)                      # ONCE per request; planner retries and builder retries reuse it
    session_id = uuid.uuid4().hex[:8]                     # ONE session for both calls (local)
    planner_user = _build_planner_user_prompt(story_text, desc_lang=desc_lang, requirement=requirement, arc=arc)
    planner_tokens = max(_a1_max_tokens(story_text), 4096)
    builder_tokens = 8192
    attempts: list[dict] = []                             # every call's prompts, raw reply and parse outcome (kept even on failure)
    history: list[dict] = []                              # Grok conversation (local uses the ComfyUI session file)
    cap = chat_capture if chat_capture is not None else {}
    cap.update({"route": "static", "arc": arc, "attempts": attempts, "session_id": session_id, "backend": "grok" if is_grok else "local",
                "llm_wf_file": llm_wf_file, "grok_model": grok_model})

    async def call(system: str, user: str, *, tokens: int, reset: bool) -> str:
        if is_grok:
            raw = await _call_grok_text(system, user, api_key=grok_api_key, model=grok_model, max_tokens=tokens, on_log=on_log,
                                        history=list(history) if history else None)
        else:
            raw = await _call_local_text(wf_template or {}, system, user, max_tokens=tokens, session_id=session_id, on_log=on_log, reset=reset)
        return raw or ""

    if on_log:
        await on_log("✨ Story Bible (static, two-pass) → " + ("Grok" if is_grok else f"Comfy ({llm_wf_file})"))
        await on_log(f"ℹ Story Bible (static): genre={arc['genre']} ({arc['genre_source']}), arc={arc['id']} \"{arc['name']}\"")

    # ------------------------------------------------ Call 1: Story Planner ------------------------------------------------
    outline, outline_raw, outline_user = None, "", planner_user
    for p_attempt in (1, 2):
        user = planner_user if p_attempt == 1 else planner_user + (
            "\n\nYour previous reply was not a valid <story_outline>. Reply with ONLY the <story_outline> block, every line present, "
            "all five steps as concrete visible plot beats.")
        history.clear()                                    # a planner retry starts a fresh outline (reset session / empty Grok history)
        raw = await call(_PLANNER_SYSTEM_PROMPT, user, tokens=planner_tokens, reset=True)
        parsed = _parse_story_outline(raw)
        issues = _validate_outline(parsed, arc)
        attempts.append({"stage": "planner", "attempt": p_attempt, "user_prompt": user, "raw_response": raw, "outline": parsed, "issues": issues, "ok": not issues})
        if on_log:
            await on_log(f"{'ℹ' if not issues else '⚠'} Story Bible (static) planner attempt {p_attempt}: " + ("outline accepted" if not issues else "; ".join(issues)))
        if not issues:
            outline, outline_raw, outline_user = parsed, raw, user
            break
    cap["story_outline"] = outline
    if outline is None:
        cap["turns"] = [{"stage": "planner", "system_prompt": _PLANNER_SYSTEM_PROMPT, "user_prompt": attempts[-1]["user_prompt"], "raw_response": attempts[-1]["raw_response"]}]
        await on_err("Story Bible (static): the story planner did not return a valid story outline after 2 attempts (reference builder not started)")
        return None

    # ------------------------------------------------ Call 2: Reference Builder (same session) ------------------------------------
    builder_user = _build_builder_user_prompt(desc_lang=desc_lang, requirement=requirement, cast=outline.get("cast", ""))
    if is_grok:
        history[:] = [{"role": "user", "content": outline_user}, {"role": "assistant", "content": outline_raw}]
    last_raw, last_user = "", builder_user
    prev_problems: list[str] = []
    last_block_issues: list[str] = []
    for b_attempt in (1, 2):
        user = builder_user if b_attempt == 1 else builder_user + (
            "\n\nYour previous response could not be accepted" + (": " + "; ".join(prev_problems) if prev_problems else "") + ". Using the SAME accepted story outline "
            "(same names, events, order and outcome), return <arc_plan> (all five steps) and then the complete set of flat tags in the order shown, every tag closed with "
            "its own exact matching closing tag: exactly five events E1-E5 (one per arc step), each a connected prose passage (no HAPPENS:/DETAILS: labels, no lists), "
            "and the anchor event stating the real source facts inside its prose (no stand-in text). No JSON, no commentary.")
        raw = await call(_BUILDER_SYSTEM_PROMPT, user, tokens=builder_tokens, reset=False)
        if is_grok:                                        # keep the failed exchange in the conversation like the local session does
            history.extend([{"role": "user", "content": user}, {"role": "assistant", "content": raw}])
        last_raw, last_user = raw, user
        plan = _parse_arc_plan(raw)
        notes: list[str] = []
        parse_text, norm_notes = _normalize_event_tags(_ARC_PLAN_RE.sub("", raw))
        notes += norm_notes
        result = _parse_a1_tags(parse_text, notes)
        resolution = _resolve_anchor(plan, outline, [e["id"] for e in result["macro_events"]] if result is not None else [])
        anchor_id = resolution["resolved_anchor"]
        block_issues = _validate_prose_events(result["macro_events"], anchor_id, len(arc["steps"])) if result is not None else []
        attempts.append({"stage": "builder", "attempt": b_attempt, "user_prompt": user, "raw_response": raw, "arc_plan": plan,
                         "normalization": list(norm_notes), "parsed": result is not None, "block_issues": block_issues, "anchor_resolution": resolution})
        cap["anchor_resolution"] = resolution
        if on_log:
            for n in notes:
                await on_log(f"ℹ Story Bible (static) parse: {n}")
        if result is not None and block_issues:
            prev_problems = block_issues[:4] + (["anchor: " + i for i in resolution["issues"][:2]] if not anchor_id else [])
            if on_log:
                await on_log("⚠ Story Bible (static) builder attempt %d rejected - %s" % (b_attempt, "; ".join(block_issues)))
            result = None
            last_block_issues = block_issues
        elif result is None:
            prev_problems = ["the tags could not be read"]
        if result is not None:
            _merge_event_parts(result)
            dropped = _drop_empty_characters(result)
            if dropped and on_log:
                await on_log(f"ℹ Story Bible (static): dropped empty character set(s) {dropped}")
            ids = [e["id"] for e in result["macro_events"]]
            plan_eff = dict(plan) if plan else None
            if plan_eff is not None and anchor_id and not resolution["raw_anchor"]:
                plan_eff["source_anchor_event"] = anchor_id      # the resolved anchor (the raw plan stays untouched in the capture)
            issues = _validate_arc_plan(plan_eff, arc, ids) + _outline_vs_plan_issues(outline, plan) + resolution["issues"]
            anchor = anchor_id if anchor_id in ids else ""
            if on_log:
                if issues:
                    await on_log("⚠ Story Bible (static): arc_plan inconsistent - " + "; ".join(issues) + " (events left unchanged)")
                else:
                    await on_log(f"ℹ Story Bible (static): anchor = {anchor} via {resolution['resolution_method']} (arc step {plan.get('source_moment_step')}); {len(ids)} events carry all {len(arc['steps'])} planned steps")
            ev_text = " ".join(e["content"] for e in result["macro_events"])
            missing_names = [n for n in _cast_names(outline.get("cast", "")) if n not in ev_text]
            if missing_names and on_log:
                await on_log(f"⚠ Story Bible (static): cast name(s) {missing_names} never appear in the events (the events may use another spelling)")
            cap.update({
                "cast_names_missing_in_events": missing_names,
                "turns": [{"stage": "planner", "system_prompt": _PLANNER_SYSTEM_PROMPT, "user_prompt": outline_user, "raw_response": outline_raw},
                          {"stage": "builder", "system_prompt": _BUILDER_SYSTEM_PROMPT, "user_prompt": user, "raw_response": raw}],
                "arc_plan": plan, "arc_plan_issues": issues, "static_anchor_event": anchor or None,
                "generated_events": [i for i in ids if i != anchor] if anchor else [],
            })
            return result
        if on_log:
            await on_log(f"⚠ Story Bible (static) builder attempt {b_attempt}: not accepted")

    cap["turns"] = [{"stage": "planner", "system_prompt": _PLANNER_SYSTEM_PROMPT, "user_prompt": outline_user, "raw_response": outline_raw},
                    {"stage": "builder", "system_prompt": _BUILDER_SYSTEM_PROMPT, "user_prompt": last_user, "raw_response": last_raw}]
    await on_err("Story Bible (static): the reference builder did not return valid events after 2 attempts"
                 + (" - " + "; ".join(last_block_issues) if last_block_issues else " (tags unreadable)") + " (the accepted outline was kept; the planner was not re-run)")
    return None
