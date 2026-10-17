"""
llm_engine_a1.py — Story Bible engine (legacy name: A1).

Story Bible builds the canonical character/world/location/object/continuity reference.

Design rule:
    LLM = semantic story understanding.
    Python = transport + explicit tag structure only.

Story Bible reads the complete story once, splits it into macro events, and returns the
compact reference used by Beat Sheet, Shot List, and Image Prompts. Python never infers,
repairs, rewrites, classifies, or validates story meaning.
"""

__version__ = "20260924_a1_tag_format"

import asyncio
import copy
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .constants import LLM_DIR, lang_prompt_name
from .comfy_api import comfy_queue, comfy_history


_CODE_FENCE_RE = re.compile(r"^```(?:json|xml)?\s*|```\s*$")


def _strip_code_fence(raw: str) -> str:
    """Transport-only cleanup: some models wrap the answer in ``` fences. Strip fence markers
    only — never touch the content itself."""
    return _CODE_FENCE_RE.sub("", raw.strip()).strip()


# Flat-tag transport (same idea as A2/B): <E1>text</E1>, <char1.name>text</char1.name>,
# <world_sheet>text</world_sheet>. Matched by exact tag name with a regex, so quotes, "<", ">", "&"
# and newlines inside the text are harmless, and a truncated trailing tag simply doesn't match.
_EVENT_RE = re.compile(r"<(E\d+)>(.*?)</\1>", re.DOTALL)
_EVENT_OPEN_RE = re.compile(r"<E(\d+)>")
_EVENT_CLOSE_RE = re.compile(r"</E(\d+)>")
_CHAR_TAG_RE = re.compile(r"<(char\d+)\.([a-z_]+)>(.*?)</\1\.\2>", re.DOTALL)
_SECTION_RE = re.compile(r"<(world_sheet|locations|important_objects|continuity_notes|source_style)>(.*?)</\1>", re.DOTALL)

# Character fields in display order. `trigger` is the token of a separately trained character
# model; the user fills it in, A1 always leaves it empty.
_CHAR_FIELDS = (
    "name", "alias", "trigger", "age", "gender", "nationality",
    "body", "face", "skin", "marks", "init_hair", "init_cloth",
)


GROK_MARKER = "[Grok API]"
_LLM_TIMEOUT = 120
_GROK_TEMPERATURE = 0.4
_GROK_REASONING_EFFORT = "low"


_A1_SYSTEM_PROMPT = r"""You are a Senior Story Structure Editor preparing a detailed production reference for a visual-story pipeline.

REQUIREMENTS

1. macro_events: sequential macro events E1, E2, E3... covering the complete chronological source from its first actual event through its final actual event. A macro event is one meaningful phase of story progression, not one sentence, action, or visual detail, and it stays coarse enough that the next editor can still subdivide it into smaller ordered beats. A continuous scene (same place, time and cast) may hold several macro events when the story meaningfully advances into a new phase: a new central interaction begins, an established interaction materially progresses, a consequential physical or relationship state changes, a result or aftermath becomes established fact, or the immediate objective changes. Keep closely connected actions, reactions, immediate consequences and simultaneous actions in one macro event. Atmosphere, lighting, weather, appearance, body-position detail, or another small movement inside the same interaction never makes a macro event, and neither does an anticipated action ("about to", "will", "seems ready to") that SOURCE does not actually show. No later meaningful action, reaction, decision, dialogue turn, state change, location transition, consequence, or aftermath may be omitted because an earlier moment feels like a closure. Each macro event keeps enough story truth (people, relationships, locations, external physical objects, actions, reactions, dialogue intent, physical states, chronology, cause-effect) for the next editor to subdivide it without rereading the source. Reference fields never substitute for a missing chronological event.
Example: "Mira enters the kitchen, puts her bag on a chair, pours water, and sits at the table while rain taps the window." -> ONE macro event (connected actions of one phase). "Mira sits down. Arun confronts her about missing money. She denies it. Arun lays the missing envelope on the table, proving she lied. Mira falls silent." -> E1 Mira arrives and settles; E2 Arun confronts her and she denies it; E3 Arun reveals the evidence and Mira's situation changes: not one macro per sentence, and not one for the whole scene.

2. Return exactly these production fields, written as tags (see OUTPUT FORMAT in the user message): macro_events, character_sheet (the <charN.*> tags of each character), world_sheet, locations, important_objects, continuity_notes, source_style. Every distinct named or significantly acted-upon character has a separate <charN.*> entry; merge characters only when the source treats them as an undifferentiated group.

3. Character names: every <charN.name> is a personal given name that fits the nationality, setting and era — including a first-person narrator referred to only as "I". Before writing a <charN.name> tag, check it is not a role, pronoun, or label ("protagonist", "the narrator", "I", "the boyfriend") and not a bracketed role after a name; if it would be, invent a real personal name instead and use that. <charN.alias> holds other names the story itself uses for that person (nickname, title, role), empty when none; pronouns are never aliases. In macro_events refer to every person by name, resolving pronouns and role labels (including a first-person narrator).

4. Fixed baseline: age, body, face, skin, marks and init_hair describe the person BEFORE any event — no emotion, expression, injury or other story consequence (those belong in macro_events). marks lists permanent features only.

5. Concrete production design: preserve appearance and clothing the source gives. When a production-relevant fixed visual detail is missing, make one simple concrete design choice that does not contradict the story, setting, era, role, chronology, or later events, and resolve it now. Never write placeholders such as "unspecified", "unknown", "not described", "to be designed", "generic appearance", or "casual clothes".

6. Clothing baseline: every character who is clothed at any point before a clothing-state change has <charN.init_cloth> naming the actual garments and important worn accessories (e.g. "white cotton blouse, light blue denim shorts, brown leather sandals") — the outfit worn BEFORE any later event alters, damages, removes, replaces, or adds clothing. A character who truly begins nude or partially clothed is recorded that way, without invented clothing. Later clothing changes are transitions FROM this baseline, recorded in macro_events and continuity_notes; they never leave the starting outfit undefined.

7. Route each physical fact to the field that owns its continuity role. Baseline anatomy and body features go in character_sheet; anatomical actions, contact, injuries, temporary conditions and other event-local body states go in the relevant macro_event; a physical or anatomical state that persists across later events and must be remembered goes in continuity_notes. Worn clothing goes in init_cloth or a clothing-state event; lighting, weather and surfaces in world_sheet, locations or the macro event. important_objects holds only discrete external physical objects whose identity, ownership, presence, use, transfer, damage, loss, or changing state matters across the story: if omitting the item could make a later action, ownership relation, transfer, use, damage, loss, or object state visually or causally inconsistent, it belongs; otherwise omit it. Never promote anatomy, bodily material, worn clothing, architecture, surfaces, lighting, weather, or incidental set dressing into important_objects merely because it is visually prominent, unusual, repeated, or part of the current scene, and never a furnishing, food or drink, vehicle, or container that merely appears. important_objects is EMPTY by default: list an item only when the omission test above says it must be tracked.
Example: "Her injured left hand stays bandaged for three events" -> the injury goes in its macro_event and the persistent bandaged state in continuity_notes; the hand is not an important object. "She gives Arun a brass key and he later opens the door with it" -> the brass key is an important object. "A blanket lies beneath them and never affects later action" -> omit the blanket.

8. continuity_notes holds persistent cross-event facts and states downstream stages must remember (including persisting clothing-state transitions). It never duplicates or replaces character_sheet.

9. Use the requested output language (except source_style, which is always English); preserve established names and useful source-language terms.

10. source_style: if STORY is not actually a narrative — it is itself a prompt describing a desired visual/art style (art direction, rendering technique, photographic or illustration style, palette, mood descriptors) rather than characters, events, or a story — still produce the best macro_events/character_sheet the material supports, and derive source_style from it. If STORY is a genuine narrative that also explicitly states an overall visual/art style for the whole work (not a single scene's camera or lighting), derive source_style from that statement. If STORY states no explicit style anywhere, leave source_style empty — never invent one. source_style is YOUR understanding of the work's overall visual style, distilled and subject-free: never copy sentences, and never name characters, pose, clothing design, or events. Cover whatever the source conveys of: medium/render type and fidelity, colour palette and grading, lighting quality and colour accents, material and surface feel, camera/depth-of-field character, and mood. Write it as one dense paragraph of image-prompt style descriptors in ENGLISH regardless of the output language (the image model reads English), complete enough that a new scene in the same style could be drawn from it alone.

FINAL CHECK: macro_events reach the source's final event; all seven fields are present as tags (source_style may be empty); every significant character has an entry; every <charN.name> is a real personal name — re-check each one specifically for a role, pronoun, or label ("narrator", "protagonist", "I", or a bracketed role) and replace any with an invented personal name; every clothed character has actual garments in init_cloth; no placeholders; designed details do not contradict the source; every important_object is an external physical object with a real continuity role, persistent anatomical/body states are routed to continuity_notes instead, and incidental scenery/set dressing is omitted; chronology and cause-effect are intact; macro events cover the source from first actual event to final actual event, stay coarse enough to subdivide, and neither collapse distinct story phases nor split mere descriptive or action detail into separate macro events.

Return only the requested tags."""

def _build_a1_user_prompt(
    story_text: str,
    *,
    desc_lang: str,
    requirement: str = "",
) -> str:
    """Serialize explicit user input. No semantic interpretation."""
    lang_label = lang_prompt_name(desc_lang or "en")
    parts = [
        f"Output language for production-reference fields and macro descriptions: {lang_label}",
    ]
    if (requirement or "").strip():
        parts.append(f"Special requirements: {(requirement or '').strip()}")
    parts.append(
        "OUTPUT FORMAT — flat tags, plain text inside, no JSON:\n"
        "<E1>...</E1>\n"
        "<E2>...</E2>\n"
        "(macro events E1, E2, E3... in story order)\n"
        "<char1.name>a personal given name — invent one if the story has none; never a role, pronoun, or \"X (role)\"</char1.name>\n"
        "<char1.alias>other names the story uses for this person, or empty; never a pronoun</char1.alias>\n"
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
        "(the same twelve tags for char2, char3... one numbered set per distinct character)\n"
        "<world_sheet>compact persistent world facts</world_sheet>\n"
        "<locations>compact location facts</locations>\n"
        "<important_objects>compact facts about discrete external physical objects whose identity, ownership, presence, use, transfer, damage, loss, or changing state must remain consistent across events; omit anatomy/body states, bodily material, worn clothing, incidental scenery, surfaces, lighting and weather; leave empty when nothing qualifies</important_objects>\n"
        "<continuity_notes>compact cross-event continuity facts</continuity_notes>\n"
        "<source_style>(English, subject-free distilled style) only when STORY itself is a style-describing prompt rather than a narrative, or when a genuine narrative explicitly states an overall visual/art style for the whole work; otherwise leave empty</source_style>\n"
        "Always leave <charN.trigger> empty — it is filled in by the user. Write \"none\" in a "
        "character tag whose detail truly does not apply (e.g. marks). Keep every tag present; use an "
        "empty tag when a reference field has nothing useful to add. Tag names stay exactly as "
        "shown (English); only the text inside follows the output language. Text inside a tag is "
        "plain — quotes need no escaping."
    )
    parts.append("STORY:\n" + (story_text or ""))
    return "\n\n".join(parts)


def _a1_max_tokens(story_text: str) -> int:
    """Transport budget only; scales with input size without interpreting content."""
    return max(4096, min(16384, len(story_text or "") + 2500))


async def _call_local_text(
    wf_template: dict,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
    session_id: str,
    on_log=None,
    reset: bool = True,
) -> str | None:
    """Transport only: send one system/user pair to LLMSessionChatNode and return raw text.

    reset=True starts the session over (every A1 generation); reset=False continues the same session (second pass)."""
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
            await on_log("❌ Story Bible: workflow has no LLMSessionChatNode")
        return None

    if on_log:
        await on_log(f"📋 Story Bible SYSTEM:\n{system_prompt}")
        await on_log(f"📤 Story Bible USER:\n{user_prompt}")

    loop = asyncio.get_event_loop()
    cid = uuid.uuid4().hex
    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, cid)
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Story Bible queue: {exc}")
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
                        await on_log(f"📥 Story Bible RAW:\n{text}")
                    return text
        status = ((entry.get("status") or {}).get("status_str") or "").lower()
        if status in ("success", "error"):
            break

    if on_log:
        await on_log("❌ Story Bible: local LLM returned no text")
    return None


async def _call_grok_text(
    system_prompt: str,
    user_prompt: str,
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    on_log=None,
    history: list | None = None,
) -> str | None:
    """Transport only: one xAI chat-completions request, returning message.content verbatim."""
    if not api_key:
        if on_log:
            await on_log("❌ Story Bible: Grok API key is empty")
        return None

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            *[m for m in (history or []) if isinstance(m, dict)],
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
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=max(_LLM_TIMEOUT, 60)) as response:
            return json.loads(response.read())

    if on_log:
        await on_log(f"📋 Story Bible SYSTEM:\n{system_prompt}")
        await on_log(f"📤 Story Bible USER:\n{user_prompt}")

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
                    await on_log(f"❌ Story Bible Grok retry: {retry_exc}")
                return None
        else:
            if on_log:
                await on_log(f"❌ Story Bible Grok HTTP {exc.code}: {body[:500]}")
            return None
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Story Bible Grok: {exc}")
        return None

    choices = response.get("choices") if isinstance(response, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else {}
    message = first.get("message") if isinstance(first, dict) else {}
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str):
        if on_log:
            await on_log("❌ Story Bible: Grok response has no text content")
        return None
    if on_log:
        await on_log(f"📥 Story Bible RAW:\n{text}")
    return text


def _extract_event_blocks(text: str, notes: list | None = None) -> list[dict] | None:
    """Macro events from <E1>...</E1> tags by tag position, so an event closed with the wrong number
    (e.g. <E11>..</E12>) is still found instead of silently dropped. Boundary recovery is structural only
    and accepted only when it is unambiguous: event tags strictly alternate open/close, every opener has
    one closer, opener numbers strictly increase, bodies are nonempty and no prose sits between event
    blocks. Anything else (nested, truncated, duplicated, overlapping, stray prose) returns None so the
    caller's existing second attempt runs; a partial event list is never returned. Bodies are exact slices
    of the response (outer whitespace trimmed). Gaps in the opener numbering are normalised to E1..En."""
    toks = sorted(
        [(m.start(), m.end(), True, int(m.group(1))) for m in _EVENT_OPEN_RE.finditer(text)]
        + [(m.start(), m.end(), False, int(m.group(1))) for m in _EVENT_CLOSE_RE.finditer(text)]
    )
    if not toks or len(toks) % 2:
        return None
    ids = [t[3] for t in toks if t[2]]
    if any(b <= a for a, b in zip(ids, ids[1:])):
        return None
    out: list[tuple[int, str]] = []
    for k in range(0, len(toks), 2):
        o, c = toks[k], toks[k + 1]
        if not o[2] or c[2]:
            return None
        body = text[o[1]:c[0]].strip()
        if not body:
            return None
        if k + 2 < len(toks) and text[c[1]:toks[k + 2][0]].strip():
            return None
        if notes is not None and o[3] != c[3]:
            notes.append(f"E{o[3]} closed with </E{c[3]}>: boundary recovered")
        out.append((o[3], body))
    if ids != list(range(1, len(ids) + 1)):
        if notes is not None:
            notes.append(f"event numbering had gaps ({', '.join(f'E{i}' for i in ids)}): renumbered E1..E{len(out)}")
        return [{"id": f"E{n}", "content": b} for n, (_, b) in enumerate(out, 1)]
    return [{"id": f"E{n}", "content": b} for n, b in out]


_SCOPED_CHAR_RECOVERY = True
_ANY_TAG_RE = re.compile(r"<(/?)([A-Za-z][\w.]*)>")
_NESTED_CHAR_TAG_RE = re.compile(r"</?char\d+\.[a-z_]+>")


def _recover_char_tags(text: str, chars: dict, notes: list | None) -> None:
    """A <charN.field> that the exact open/close match could not read is recovered only when its boundary is structurally
    unambiguous; otherwise it is reported and left empty (never guessed):
      1. the next tag is a closer of the SAME character but another field  -> value = text up to it
         (e.g. <char1.init_hair>…</char1.init_cloth>)
      2. the next tag is the opener of another field of the SAME character  -> value = text up to it (closer missing)
    After case 1, an orphan value (text, then that same wrong closer again, with no opener for that field anywhere and
    the field still empty) is taken as that field's value. Truncated trailing tags are never recovered."""
    consumed = {m.start() for m in _CHAR_TAG_RE.finditer(text) if not _NESTED_CHAR_TAG_RE.search(m.group(3))}
    toks = [(m.start(), m.end(), bool(m.group(1)), m.group(2)) for m in _ANY_TAG_RE.finditer(text)]
    split = lambda name: tuple(name.split(".", 1)) if "." in name else (name, "")
    for i, (s, e, closing, name) in enumerate(toks):
        cid, field = split(name)
        if closing or not re.fullmatch(r"char\d+", cid) or field not in _CHAR_FIELDS or s in consumed:
            continue
        if field in chars.get(cid, {}):
            continue                                           # an earlier complete tag already supplied this field
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        value = ""
        how = ""
        if nxt is not None:
            ncid, nfield = split(nxt[3])
            if ncid == cid and nfield in _CHAR_FIELDS and nfield != field:
                value = text[e:nxt[0]].strip()
                how = f"closed with </{nxt[3]}>" if nxt[2] else f"closer missing before <{nxt[3]}>"
                if nxt[2] and nfield not in chars.get(cid, {}) and not any(
                        t2[3] == nxt[3] and not t2[2] for t2 in toks):
                    # orphan value: text between the wrong closer and the next closer of the same tag
                    j = i + 2
                    if j < len(toks) and toks[j][2] and toks[j][3] == nxt[3]:
                        orphan = text[nxt[1]:toks[j][0]].strip()
                        if orphan:
                            chars.setdefault(cid, {})[nfield] = orphan
                            if notes is not None:
                                notes.append(f"<{nxt[3]}> had no opening tag: its value recovered from the text before its closing tag")
        if value:
            chars.setdefault(cid, {})[field] = value
            if notes is not None:
                notes.append(f"<{cid}.{field}> {how}: value recovered")
        elif notes is not None:
            notes.append(f"<{cid}.{field}> is malformed and could not be recovered unambiguously: left empty")


def _parse_a1_tags(raw: str | None, notes: list | None = None) -> dict | None:
    """Explicit machine-structure parser only. Never repairs or interprets semantic content.

    Needs at least one macro event and one character with a name. The four reference sections
    default to "" when their tag is absent (they come last, so a truncated answer loses them
    first). character_sheet is rebuilt as the <charN.*> tag block — all twelve fields per
    character in fixed order, `trigger` always empty, so the user can fill it in the Character
    box and downstream stages read one uniform format."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = _strip_code_fence(raw)

    events = _extract_event_blocks(text, notes)
    if not events:
        return None

    chars: dict[str, dict[str, str]] = {}
    for m in _CHAR_TAG_RE.finditer(text):
        cid, field, value = m.group(1), m.group(2), m.group(3).strip()
        if _SCOPED_CHAR_RECOVERY and _NESTED_CHAR_TAG_RE.search(value):
            continue                                       # another <charN.*> tag inside the value: boundary is wrong, recover below
        if field in _CHAR_FIELDS and field not in chars.setdefault(cid, {}):
            chars[cid][field] = value
    if _SCOPED_CHAR_RECOVERY:
        _recover_char_tags(text, chars, notes)
    ordered = sorted((c for c in chars if chars[c].get("name")), key=lambda c: int(c[4:]))
    if not ordered:
        return None
    lines: list[str] = []
    for cid in ordered:
        for field in _CHAR_FIELDS:
            value = "" if field == "trigger" else chars[cid].get(field, "")
            lines.append(f"<{cid}.{field}>{value}</{cid}.{field}>")
        lines.append("")
    character_sheet = "\n".join(lines).strip()

    sections = {k: "" for k in ("world_sheet", "locations", "important_objects", "continuity_notes", "source_style")}
    for m in _SECTION_RE.finditer(text):
        if not sections[m.group(1)]:
            sections[m.group(1)] = m.group(2).strip()

    return {
        "macro_events": events,
        "character_sheet": character_sheet,
        "world_sheet": sections["world_sheet"],
        "locations": sections["locations"],
        "important_objects": sections["important_objects"],
        "continuity_notes": sections["continuity_notes"],
        "source_style": sections["source_style"],
    }


# SECOND PASS (same conversation as the generation, like Groom's): after A1 succeeds, one fixed follow-up asks it to remove anatomy
# from important_objects, replace role/label character names with personal names, and write names instead of pronouns in
# macro_events. A reply that cannot be read, or that changes the cast size or drops most events, is ignored (first result kept).
_SECOND_PASS_ENABLED = True
_SECOND_PASS_MIN_KEEP = 0.6      # structural sanity guard: fewer than this share of the first pass's events = truncated/garbled reply
_SECOND_PASS_PROMPT = (
    "SECOND PASS. Review the A1 output you just wrote (it is in our conversation above; do not ask for it again) and correct only "
    "these three things, without changing anything else:\n\n"
    "1. important_objects: remove every anatomical body part or bodily material. A body part belongs to its character, even when it "
    "has an owner or is part of the action. Keep every legitimate external object exactly as written.\n\n"
    "2. Character names: every <charN.name> must be a real personal given name. If a name is a role, species, pronoun, or descriptive "
    "label (for example \"Owner\", \"Dog\", \"Redhair\"), replace it with a real personal name and use the new name everywhere it "
    "appears (macro_events, character_sheet, continuity_notes, important_objects).\n\n"
    "3. macro_events: replace every pronoun (he, she, they, it, him, her, his, its, their, them) with the character's name, so each "
    "sentence states exactly who does what to whom. Keep each event's wording otherwise.\n\n"
    "Keep every other event, field, and fact exactly as written. Do not add or remove events or characters. Reply with the complete "
    "A1 output again, in the same flat-tag format as before (all tags)."
)


def _second_pass_accept(first: dict, second: dict | None) -> tuple[dict | None, str]:
    """Structure only: accept the second-pass result when it parsed, keeps the cast size, and keeps most events. Reference fields
    the reply left empty fall back to the first pass (a short reply must not erase them)."""
    if not second:
        return None, "reply could not be parsed"
    n1 = len(re.findall(r"<char\d+\.name>", first.get("character_sheet", "")))
    n2 = len(re.findall(r"<char\d+\.name>", second.get("character_sheet", "")))
    if n2 != n1:
        return None, f"character count changed ({n1} -> {n2})"
    if len(second.get("macro_events") or []) < _SECOND_PASS_MIN_KEEP * len(first.get("macro_events") or []):
        return None, "reply keeps too few events"
    merged = dict(second)
    for key in ("world_sheet", "locations", "continuity_notes", "source_style"):
        if not (merged.get(key) or "").strip() and (first.get(key) or "").strip():
            merged[key] = first[key]
    return merged, "ok"


async def _a1_second_pass(first_result: dict, first_user: str, first_raw: str, *, llm_wf_file: str, wf_template, session_id: str,
                          max_tokens: int, grok_api_key: str, grok_model: str, on_log=None) -> tuple[dict | None, str, str]:
    """Returns (accepted result | None, raw reply, note). None = keep the first-pass result."""
    if llm_wf_file == GROK_MARKER:
        raw = await _call_grok_text(_A1_SYSTEM_PROMPT, _SECOND_PASS_PROMPT, api_key=grok_api_key, model=grok_model,
                                    max_tokens=max_tokens, on_log=on_log,
                                    history=[{"role": "user", "content": first_user}, {"role": "assistant", "content": first_raw}])
    else:
        raw = await _call_local_text(wf_template or {}, _A1_SYSTEM_PROMPT, _SECOND_PASS_PROMPT, max_tokens=max_tokens,
                                     session_id=session_id, on_log=on_log, reset=False)
    if not (raw or "").strip():
        return None, "", "no reply"
    accepted, why = _second_pass_accept(first_result, _parse_a1_tags(raw, []))
    return accepted, raw, why


async def run_a1_vnext(
    story_text: str,
    llm_wf_file: str,
    on_err,
    on_log=None,
    mode: str = "cinematic",      # API compatibility; clean A1 is mode-independent
    genre: str = "",              # API compatibility; Groomed story is already genre-shaped
    genre2: str = "",             # API compatibility
    requirement: str = "",
    desc_lang: str = "en",
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    chat_capture: dict | None = None,
) -> dict | None:
    """Clean A1: LLM does story understanding; Python only transports and checks tag shape."""
    if not (story_text or "").strip():
        await on_err("Story Bible: story is empty")
        return None

    wf_template = None
    if llm_wf_file != GROK_MARKER:
        if not (llm_wf_file or "").strip():
            await on_err("Story Bible: no local LLM workflow selected")
            return None
        wf_path = LLM_DIR / f"{llm_wf_file}.json"
        if not wf_path.exists():
            await on_err(f"ไม่พบ {wf_path}")
            return None
        try:
            wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
        except Exception as exc:
            await on_err(f"Story Bible workflow read failed: {exc}")
            return None

    base_user_prompt = _build_a1_user_prompt(
        story_text,
        desc_lang=desc_lang,
        requirement=requirement,
    )
    max_tokens = _a1_max_tokens(story_text)
    session_id = uuid.uuid4().hex[:8]

    if on_log:
        backend = "Grok" if llm_wf_file == GROK_MARKER else f"Comfy ({llm_wf_file})"
        await on_log(f"✨ Story Bible → {backend}")

    last_raw = ""
    last_user_prompt = base_user_prompt
    for attempt in (1, 2):
        user_prompt = base_user_prompt
        if attempt == 2:
            user_prompt += (
                "\n\nYour previous response could not be read. Return the complete set of flat tags "
                "described in OUTPUT FORMAT: <E1>..</E1> style macro events, one full <charN.*> set of "
                "twelve tags per character (with name filled in), then <world_sheet>, <locations>, "
                "<important_objects>, <continuity_notes>. Every tag must be closed with its exact "
                "matching closing tag. No JSON, no commentary."
            )
        last_user_prompt = user_prompt

        if llm_wf_file == GROK_MARKER:
            raw = await _call_grok_text(
                _A1_SYSTEM_PROMPT,
                user_prompt,
                api_key=grok_api_key,
                model=grok_model,
                max_tokens=max_tokens,
                on_log=on_log,
            )
        else:
            raw = await _call_local_text(
                wf_template or {},
                _A1_SYSTEM_PROMPT,
                user_prompt,
                max_tokens=max_tokens,
                session_id=session_id,
                on_log=on_log,
            )

        last_raw = raw or ""
        _parse_notes: list[str] = []
        result = _parse_a1_tags(raw, _parse_notes)
        if on_log:
            for _n in _parse_notes:
                await on_log(f"ℹ Story Bible parse: {_n}")
        if result is not None:
            if chat_capture is not None:
                chat_capture["turns"] = [{
                    "system_prompt": _A1_SYSTEM_PROMPT,
                    "user_prompt": user_prompt,
                    "raw_response": raw or "",
                }]
                chat_capture["backend"] = "grok" if llm_wf_file == GROK_MARKER else "local"
                chat_capture["llm_wf_file"] = llm_wf_file
                chat_capture["session_id"] = session_id
                chat_capture["grok_model"] = grok_model
            if _SECOND_PASS_ENABLED:
                _sp, _sp_raw, _sp_note = await _a1_second_pass(
                    result, user_prompt, raw or "", llm_wf_file=llm_wf_file, wf_template=wf_template, session_id=session_id,
                    max_tokens=max_tokens, grok_api_key=grok_api_key, grok_model=grok_model, on_log=on_log)
                if _sp is not None:
                    result = _sp
                    if chat_capture is not None:
                        chat_capture["turns"].append({"system_prompt": _A1_SYSTEM_PROMPT, "user_prompt": _SECOND_PASS_PROMPT,
                                                      "raw_response": _sp_raw})
                    if on_log:
                        await on_log("🔎 Story Bible second pass: applied")
                elif on_log:
                    await on_log(f"⚠ Story Bible second pass ignored ({_sp_note}); first result kept")
            return result

        if on_log:
            await on_log(f"⚠ Story Bible attempt {attempt}: invalid tag structure")

    if chat_capture is not None and last_raw:
        chat_capture["turns"] = [{
            "system_prompt": _A1_SYSTEM_PROMPT,
            "user_prompt": last_user_prompt,
            "raw_response": last_raw,
        }]
        chat_capture["backend"] = "grok" if llm_wf_file == GROK_MARKER else "local"
        chat_capture["llm_wf_file"] = llm_wf_file
        chat_capture["session_id"] = session_id
        chat_capture["grok_model"] = grok_model

    await on_err("Story Bible: LLM did not return the required tag structure after 2 attempts")
    return None


# Canonical name — Story Bible (legacy: A1). The legacy entry point above stays for compatibility; run_production_reference is the same function.
run_production_reference = run_a1_vnext
