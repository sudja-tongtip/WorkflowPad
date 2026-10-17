"""
a1_novel_conversation.py - A1 Novel as a STEP-BY-STEP CONVERSATION (the same controller as A1 Static: a1_static_conversation._Conversation).

Python sends one user request per step, keeps the history, checks the STRUCTURE of every answer, corrects a rejected answer (max 3 attempts per step) and stops with
the reason if one cannot be accepted. The LLM writes everything that is story; Python never reads meaning. The finished story is the source of every request.

Steps (one request = one kind of result; the story is sent inside the first request, which also asks for the characters):
  1 CHARACTERS      <charN.*> sets for every character of the story
  2 WORLD SHEET     <world_sheet> (+ <source_style>, empty unless the story states a style)
  3 LOCATIONS       <locations>
  4 IMPORTANT OBJECTS <important_objects>
  5 MACRO EVENTS    <E1>..<En> (as many as the story needs, chronological)
  6 CONTINUITY NOTES <continuity_notes>, summarised from the events that were really written
The result is the unchanged A1 dict (seven fields), so A2 consumes it as before. A1 Novel's old single-call path (which may answer STATIC) is kept as
run_a1_novel's behaviour for the classifier-fallback case, where only it can re-route a static input.
"""

import re
import uuid

from . import a1_static_conversation as _C
from .constants import lang_prompt_name
from .llm_engine_a1 import GROK_MARKER

__version__ = "20261103_a1_novel_conversation_v1"

# One call per step of a Genre Arc: each call REARRANGES a part of the finished story into the next paragraph (nothing is invented). The paragraphs are the macro events E1..En
# (n = the number of arc steps), so there is no separate MACRO EVENTS call. False = the previous flow (one MACRO EVENTS call, as many events as the story needs).
_NOVEL_PER_STEP = True

_NOVEL_SYSTEM_PROMPT = (
    "You are a professional story editor. You read a finished story and prepare the reference that a storyboard team uses to draw it as a sequence of images, "
    "so that the images alone tell the story in order. You create the characters from the story: if the story gives a character no name, create a fitting personal name for them. "
    "You are in a conversation with the user: answer what they ask, in the form they ask for. When the user asks for tagged output, reply with those tags only."
)

# which earlier accepted answers each Local request carries (the story itself is always included)
_NEEDS = {
    "CHARACTERS": [],
    "WORLD SHEET": ["CHARACTERS"],
    "LOCATIONS": ["CHARACTERS", "WORLD"],
    "IMPORTANT OBJECTS": ["CHARACTERS", "LOCATIONS"],
    "MACRO EVENTS": ["CHARACTERS", "LOCATIONS", "OBJECTS"],
    "CONTINUITY NOTES": ["CHARACTERS", "MACROS"],
}


def _needs_per_step() -> dict:
    """Local transcript needs when the story is rearranged paragraph by paragraph: later steps also see every accepted paragraph (kind EVENT<k>)."""
    per = [f"EVENT{k}" for k in range(1, _C._MAX_EVENT_STEPS + 1)]
    out = {"CHARACTERS": [], "WORLD SHEET": ["CHARACTERS"] + per, "LOCATIONS": ["CHARACTERS", "WORLD"] + per, "IMPORTANT OBJECTS": ["CHARACTERS", "LOCATIONS"] + per,
           "CONTINUITY NOTES": ["CHARACTERS"] + per}
    for k in range(1, _C._MAX_EVENT_STEPS + 1):
        out[f"STORY EVENT {k}"] = ["CHARACTERS"] + per[:k - 1]
    return out


def _lang(desc_lang: str) -> str:
    return _C._lang(desc_lang)


def _req_event_step(arc: dict, k: int, names: list[str], *, desc_lang: str) -> str:
    """Paragraph k of n: the part of the finished story that fits arc step k, rearranged. The story is only rearranged; nothing is invented."""
    n = len(arc["steps"])
    where = "It starts from the first event of the story." if k == 1 else "It continues exactly where the previous paragraph ended."
    rest = ("This last paragraph takes everything that remains, up to the final event of the story." if k == n
            else "Leave the later parts of the story for the later paragraphs.")
    head = "Genre direction: %s\nArc: %s (%s)\nStep %d of %d: %s" % (arc.get("goal", ""), arc["name"], arc["id"], k, n, arc["steps"][k - 1])
    return "\n".join(x for x in (
        _lang(desc_lang),
        f"TASK - STORY EVENT {k}: rearrange the story into paragraph {k} of {n}, following this arc step. Use only what the story says: do not invent events, characters or facts, "
        "and do not change the chronology or the ending.",
        head,
        "Take from the story the part that fits this step and write it as one connected paragraph of flowing prose that names who does what to whom (names, never pronouns or role labels). "
        f"{where} {rest} A paragraph may hold as many of the story's events as fit. Keep every important fact of that part (clothing, body state, objects and who holds them, place, dialogue).",
        f"Use only these characters: {'; '.join(names)}.",
        "Reply with this tag only:\n<event_%d>...</event_%d>" % (k, k),
    ) if x)


def _req_characters(story: str, *, desc_lang: str, genre: str, requirement: str) -> str:
    skeleton = "\n".join(f"<char1.{f}>{_C._CHAR_HINTS.get(f, '')}</char1.{f}>" for f in _C._CHAR_FIELDS)
    return ("\n".join(x for x in (
        _lang(desc_lang),
        f"Genre: {genre}" if genre else "",
        f"Special requirements: {requirement.strip()}" if (requirement or "").strip() else "",
        "STORY (the finished story; authoritative):\n" + story,
        "",
        "TASK - CHARACTER SHEET: from the story make a character sheet for every character who appears (a person, animal or creature that is named, acts or is acted upon; "
        "merge only an undifferentiated group): skip no one and add no one. Every name must be a real personal given name that fits the setting and era (invent one if the story gives none), "
        "never a role, a description or a pronoun; put the story's own label (for example the woman) in alias. age, body, face, skin, marks and hair describe the person BEFORE the story's events; "
        "init_cloth is the complete outfit they wear at the START: top, bottom, footwear and any accessory, naming the garments themselves; how a garment is worn or arranged (pulled up, open, slipping) is not part of it. "
        "Keep the story's appearance details; where one is missing, or the story shows only part of the outfit, choose concrete, plausible garments for the rest and never write that it is not specified or assumed. "
        f"Every field needs a value, at most 20 words each; never write unspecified or unknown. Write every value in {lang_prompt_name(desc_lang or 'en')}; "
        "if the story gives a name, copy it exactly as written (same script). Reply with tags only, one set per character: char1, char2, char3 ... as many as appear, "
        "each with these 12 tags (leave trigger empty):",
        skeleton,
        "(the next sets, char2, char3 ..., have the same 12 tags, only for characters who appear in the story)",
    ) if x != ""))


def _req_world(*, desc_lang: str) -> str:
    return (f"{_lang(desc_lang)}\nTASK - WORLD SHEET: write the persistent environment facts of the story (setting, weather, lighting). They support the events and are not events.\n"
            "Output format - plain text inside each tag, no sub-tags:\n<world_sheet>...</world_sheet>\n<source_style>(English) one paragraph of image-style descriptors ONLY if the story itself states or is an overall visual style, otherwise empty</source_style>")


def _req_locations(*, desc_lang: str) -> str:
    return f"{_lang(desc_lang)}\nTASK - LOCATIONS: list the places of the story and the moves between them. Do not change any event.\nOutput format - plain text inside the tag, no sub-tags:\n<locations>...</locations>"


def _req_objects(*, desc_lang: str) -> str:
    return (f"{_lang(desc_lang)}\nTASK - IMPORTANT OBJECTS: list the discrete external objects whose identity, owner or holder, position, use, transfer, damage or changing state matters later: "
            "what it is, who has it, where it starts, how it changes. No body parts, no bodily material, no clothing that is worn; an empty tag if there is none.\n"
            "Output format - plain text lines inside the tag (one line per object), no sub-tags:\n<important_objects>...</important_objects>")


def _req_macros(*, desc_lang: str) -> str:
    return (f"{_lang(desc_lang)}\nTASK - MACRO EVENTS: write the story as macro events E1, E2, E3 ... in chronological order, from the first actual event to the final one. "
            "A macro event is a connected passage with several meaningful developments (an action and the reaction to it, dialogue, a change of relationship, situation or state, a consequence): "
            "not a single pose and not a list of looks. Group the developments by cause and effect, and let each passage start from the state the previous one ended in. "
            "Write flowing prose that names who does what to whom (names, never pronouns or role labels) in the story's order, keep every important fact (clothing, body state, objects and who holds them, "
            "place, dialogue) and do not change the events, the characters, the chronology or the ending. Use only the characters of the character sheet. "
            "Output format - tags only, as many events as the story needs, each closed with its own </E#>:\n<E1>connected prose of the first passage</E1>\n<E2>connected prose of the next passage</E2>")


def _req_continuity(*, desc_lang: str, per_step: bool = False) -> str:
    return (f"{_lang(desc_lang)}\nTASK - CONTINUITY NOTES: summarise, from the " + ("story paragraphs" if per_step else "macro events") + " you actually wrote, the facts that must stay consistent: character consistency, clothing state, "
            "held-object state, spatial continuity, persistent physical states and location changes. Do not invent new events.\nOutput format:\n<continuity_notes>...</continuity_notes>")


def _parse_events_any(raw: str | None) -> tuple[list[dict] | None, list[str]]:
    """E1..En in order, each non-empty, nothing outside the tags (n is whatever the story needs; at least one)."""
    text = _C._clean(raw)
    pats = [re.compile(r"<E(\d+)>(.*?)</E\1>", re.DOTALL)]
    found = pats[0].findall(text)
    problems: list[str] = []
    left = _C._outside(text, pats)
    if left:
        problems.append("text outside the event tags: " + repr(left[:80]))
    ids = [int(n) for n, _b in found]
    if not ids:
        problems.append("no <E1>...</E1> event found")
    elif ids != list(range(1, len(ids) + 1)):
        problems.append(f"events must be numbered E1..E{len(ids)} in order (got {', '.join('E%d' % i for i in ids)})")
    for n, body in found:
        if not body.strip():
            problems.append(f"E{n} is empty")
    return (None, problems) if problems else ([{"id": f"E{n}", "content": body.strip()} for n, body in found], [])


async def run_a1_novel_conversation(story_text, llm_wf_file, on_err, on_log=None, *, genre="", genre2="", requirement="", desc_lang="en",
                                    grok_api_key="", grok_model="grok-4-latest", chat_capture=None, wf_template=None):
    is_grok = llm_wf_file == GROK_MARKER
    cap = chat_capture if chat_capture is not None else {}
    session_id = uuid.uuid4().hex[:8]
    cap.update({"route": "novel", "mode": "conversation", "session_id": session_id, "backend": "grok" if is_grok else "local",
                "llm_wf_file": llm_wf_file, "grok_model": grok_model, "conversation": [], "turns": []})
    conv = _C._Conversation(backend=cap["backend"], wf_template=wf_template, session_id=session_id, grok_api_key=grok_api_key, grok_model=grok_model,
                            on_log=on_log, cap=cap, system=_NOVEL_SYSTEM_PROMPT, needs=_needs_per_step() if _NOVEL_PER_STEP else _NEEDS, n_ctx=None if is_grok else _C.n_ctx_for(len(story_text or "")))
    cap["n_ctx"] = conv.n_ctx or "workflow default"
    conv.source = story_text
    if on_log:
        await on_log("✨ Story Bible (novel, conversation) → " + ("Grok" if is_grok else f"Comfy ({llm_wf_file})"))

    async def fail(step: str, problems: list[str]):
        await on_err(f"Story Bible (novel): step {step} was not accepted after {_C.MAX_ATTEMPTS} attempts - " + "; ".join(problems[:4])
                     + " (the conversation so far is in the A1 chat tab)")
        return None

    chars, pr = await conv.ask("CHARACTERS", _req_characters(story_text, desc_lang=desc_lang, genre=genre if genre not in ("", "none") else "", requirement=requirement),
                               _C._parse_characters, tokens=3072)
    if chars is None:
        return await fail("CHARACTERS", pr)
    cap["cast"] = chars["names"]

    def _world(raw):
        pb: list[str] = []
        b = _C._single_blocks(_C._clean(raw), ("world_sheet", "source_style"), pb, optional=("source_style",))
        if "world_sheet" in b and not b["world_sheet"]:
            pb.append("<world_sheet> is empty")
        return (None, pb) if pb else (b, [])

    def _one(tag, allow_empty=False):
        def f(raw):
            pb: list[str] = []
            b = _C._single_blocks(_C._clean(raw), (tag,), pb)
            if tag in b and not b[tag] and not allow_empty:
                pb.append(f"<{tag}> is empty")
            return (None, pb) if pb else (b[tag], [])
        return f

    events = None
    if _NOVEL_PER_STEP:                                      # the story rearranged into the arc, one paragraph per call; the paragraphs are the macro events
        from . import llm_engine_a1_static as _S0
        arc = _S0._select_arc(genre, genre2)                 # ONCE per run
        cap["arc"] = arc
        cap["events_per_step"] = True
        if on_log:
            await on_log(f"ℹ Story Bible (novel): arc={arc['id']} \"{arc['name']}\" genre={arc['genre']} ({arc['genre_source']}); the story is rearranged into {len(arc['steps'])} paragraphs, one call each")
        events = []
        for k in range(1, len(arc["steps"]) + 1):
            para, pr = await conv.ask(f"STORY EVENT {k}", _req_event_step(arc, k, chars["names"], desc_lang=desc_lang), lambda r, k=k: _C._parse_event_step(r, k), tokens=3072)
            if para is None:
                return await fail(f"STORY EVENT {k}", pr)
            events.append({"id": f"E{k}", "content": para})
    world, pr = await conv.ask("WORLD SHEET", _req_world(desc_lang=desc_lang), _world, tokens=1536)
    if world is None:
        return await fail("WORLD SHEET", pr)
    locations, pr = await conv.ask("LOCATIONS", _req_locations(desc_lang=desc_lang), _one("locations"), tokens=1536)
    if locations is None:
        return await fail("LOCATIONS", pr)
    objects, pr = await conv.ask("IMPORTANT OBJECTS", _req_objects(desc_lang=desc_lang), _one("important_objects", allow_empty=True), tokens=1536)
    if objects is None:
        return await fail("IMPORTANT OBJECTS", pr)
    if events is None:
        events, pr = await conv.ask("MACRO EVENTS", _req_macros(desc_lang=desc_lang), _parse_events_any, tokens=8192)
        if events is None:
            return await fail("MACRO EVENTS", pr)
    continuity, pr = await conv.ask("CONTINUITY NOTES", _req_continuity(desc_lang=desc_lang, per_step=_NOVEL_PER_STEP), _one("continuity_notes"), tokens=2048)
    if continuity is None:
        return await fail("CONTINUITY NOTES", pr)

    result = {"macro_events": events, "character_sheet": chars["sheet"], "world_sheet": world.get("world_sheet", ""), "locations": locations,
              "important_objects": objects, "continuity_notes": continuity, "source_style": world.get("source_style", "")}
    from . import llm_engine_a1_static as _S
    _S._merge_event_parts(result)
    dropped = _S._drop_empty_characters(result)
    ev_text = " ".join(e["content"] for e in result["macro_events"])
    missing = [n for n in chars["names"] if n not in ev_text]
    if missing and on_log:
        await on_log(f"⚠ Story Bible (novel): cast name(s) {missing} never appear in the events (the events may use another spelling)")
    cap.update({"cast_names_missing_in_events": missing, "calls": len(conv.records), "dropped_empty_characters": dropped, "event_count": len(events)})
    if on_log:
        await on_log(f"ℹ Story Bible (novel conversation): done in {len(conv.records)} call(s), {len(events)} macro events")
    return result
