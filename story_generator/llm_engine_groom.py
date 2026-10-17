"""
llm_engine_groom.py — Story Editor engine (legacy name: Groom).

Story Editor owns how the source story reads; it never changes what happened.

Design rule:
    LLM = semantic writer/editor.
    Python = transport only.

This module deliberately does NOT classify, infer, validate, repair, or rewrite
story meaning. It only sends the user's source/settings to the selected LLM and
returns the model's story text unchanged apart from outer whitespace.

Temporary compatibility helpers used by the existing Outline review UI are
re-exported from llm_engine_groom_legacy.py. They are NOT called by Groom and
can be removed when the Outline/A1 stage is rebuilt.
"""

__version__ = "20260922_groom_clean_a1"

import asyncio
import copy
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .constants import LLM_DIR, LANG_MAP, lang_prompt_name
from .comfy_api import comfy_queue, comfy_history
from .groom_genre_arcs import _GENRE_GOALS, format_genre_arc_options   # authored Genre Goals + Arc Library (lookup only)

# Structural-only: extracts an optional trailing <source_style>...</source_style> tag the model
# may append after the story prose. Never interprets or validates its content.
_SOURCE_STYLE_TAG_RE = re.compile(r"<source_style>(.*?)</source_style>\s*$", re.DOTALL | re.IGNORECASE)


def _split_source_style(text: str) -> tuple[str, str]:
    """(story_text_without_tag, source_style_or_empty). Structural extraction only."""
    m = _SOURCE_STYLE_TAG_RE.search(text or "")
    if not m:
        return (text or "").strip(), ""
    return text[:m.start()].strip(), m.group(1).strip()

# Outline character-review UI helpers only — Groom itself never calls these.
from .llm_engine_groom_cast_review import (
    outline_cast_review_rows,
    apply_cast_review_outfits,
    detect_sheet_field_leaked_as_name,
    groom_final_check_issues,
)


GROK_MARKER = "[Grok API]"
_LLM_TIMEOUT = 120
_LOCAL_MAX_TOKENS = 12288
_GROK_REASONING_EFFORT = "low"
_GROK_TEMPERATURE = 0.7
_CHAT_REPLY_MAX_CHARS = 6000       # longest tag-less / unclosed chat answer accepted as a chat-only reply
_SESSION_MAX_TURNS = 5          # exchanges kept in a Groom chat session (see _call_local_text)


_CREATIVITY_TEXT = {
    "faithful": "Faithful — restrained. Existing story: clarify prose and keep every event. Static visual source: a short, plain progression of only two or three events from the setup, ending on a small resolution.",
    "balanced": "Balanced — moderate. Existing story: improve prose and add limited compatible transitions and reactions. Static visual source: a coherent progression of about four or five events with natural consequences and a clear resolution.",
    "creative": "Creative — rich. Existing story: add compatible micro-events without changing established story truth. Static visual source: five to seven events with meaningful interactions, at least one complication, and its consequence.",
    "genre_max": "Genre Max — full genre development. Existing story: intensify the genre progression while preserving established facts and outcomes. Static visual source: the selected genre's full arc, with escalating developments, a turning point, and a payoff.",
}

def _genre_prompt_lines(genre: str, genre2: str) -> list[str]:
    """Selected genres with their authored goal and ALL four arc options (deterministic lookup and formatting only;
    the LLM picks and adapts the arc). An unmapped genre falls back to its bare label."""
    lines: list[str] = []
    for role, key in (("Primary", genre), ("Secondary", genre2)):
        key = (key or "").strip()
        if not key or key == "none":
            continue
        lines.append(f"{role} Genre: {key.replace('_', ' ')}")
        goal = _GENRE_GOALS.get(key)
        if goal:
            lines.append(f"{role} Narrative Goal: {goal}")
        options = format_genre_arc_options(key)
        if options:
            lines.append(f"{role} Arc Options (choose and adapt ONE):\n{options}")
    return lines


_LENGTH_TEXT = {
    "short": "Short — about 3-5 paragraphs.",
    "medium": "Medium — about 6-10 paragraphs.",
    "long": "Long — about 12-20 paragraphs; the extra room goes to developed events and interactions, never repeated description.",
    "auto": "Auto — the length the story needs, enough for the progression the source calls for; never pad with repeated description.",
}

# One Groom behavior with an internal, LLM-owned source-shape decision (no Python classification).
_GROOM_CORE = """You are a story writer and editor. SOURCE is either an existing story (refine it) or a static visual description (create the story from it); see SOURCE SHAPE. Keep what SOURCE establishes: its characters, relationships, stated facts, appearance, setting, and, for an existing story, its important events, actions, contact, dialogue beats, and outcomes. Never reverse who does what to whom, and never change an established outcome without explicit permission. You may clarify awkward prose and write in the requested language; translation is not permission to change what happens. Keep stated ages, names, nationality, hair, and appearance exact when SOURCE gives them; do not invent an age, a name, or a look SOURCE does not state.

READABILITY. Write natural, fluid fiction with clear sentences, good transitions, and natural dialogue. Avoid repetitive description, awkward phrasing, and needless exposition.

EVENT-DRIVEN STORYTELLING. Progression means a meaningful change in the fictional situation, not body movement, pose changes, atmosphere, or emotional adjectives. Build the story through cause and effect: actions lead to interactions, decisions, discoveries, complications, opportunities, and consequences (event -> consequence -> next event is a principle of causality, never a fixed template). Each substantial scene has an evolving situation, with characters pursuing, answering, discovering, resolving, or deciding something. Never describe an unchanged moment twice, and do not force conflict, accidents, revelations, or a plot formula; quiet developments and ordinary interactions also advance a story. Use only the events a coherent, satisfying scene needs, narrated in forward order unless the user asks otherwise, opening with characters in action rather than a scenic paragraph.

CREATIVITY SCOPE. The Creativity setting in the user message decides how much is added beyond SOURCE, per source shape. Additions must be compatible with what SOURCE establishes; a quiet or atmospheric genre may progress through subtle but meaningful changes, so do not force combat, twists, or spectacle.

GENRE-DRIVEN STORYTELLING. Genres set narrative goals and the kinds of developments that satisfy, not only style or vocabulary. The primary genre drives the main direction, central dramatic interests, and event development; the secondary genre, if given, enriches relationships, emotional stakes, complications, and consequences. Blend them organically and express them through events, interactions, decisions, relationships, and outcomes, not repeated adjectives or atmosphere. For each selected genre the user message lists a Narrative Goal and four Arc Options. Silently choose ONE arc per genre that best fits SOURCE and adapt it: its stages are narrative functions, not fixed scenes, and are fitted to SOURCE's existing cast and setting, never by adding characters. The primary arc is the main line and the secondary arc enriches it; do not name, list, or number the arcs or stages in the story. Goals and arcs are guidance, not checklists; avoid stock plots, never invent a missing secondary genre, and let explicit user requirements outrank genre suggestions. For a static visual source the chosen arc drives the events that follow the source moment; for an existing story it shapes emphasis, pacing, and added developments but never removes, softens, or overwrites an important source event, outcome, or physical fact.

COHERENCE. Keep established character identities and major locations, believable cause and effect, and continuity across the rewrite; new events must fit naturally. Follow the user's explicit requirements and the requested language, creativity level, and length. Write fiction only: no camera, framing, lens, or render terms, no storyboard notes, no commentary, and never discuss these instructions.

CAST. Preserve the established character roster from SOURCE. Do not invent additional human or human-like characters, including unnamed companions, enemies, bystanders, or dialogue speakers, unless the user explicitly requests them. Creative reconstruction may add events, obstacles, discoveries, and environmental interactions using the existing cast. Never introduce a character merely to satisfy a genre arc or create conflict. This applies to every source shape; if SOURCE has one character, keep one character, and keep any characters SOURCE establishes. Do not turn a crowd or a group into named individuals.

SOURCE SHAPE (decide silently; never output it).
STORY SOURCE: SOURCE already has unfolding events. It is the narrative foundation: preserve its facts, identities, relationships, important actions, contact, and outcomes; improve prose, causal transitions, and pacing, and add compatible micro-events as Creativity allows. Never discard an important source event to fit a genre arc.
VISUAL-LIKE SOURCE: SOURCE mainly describes appearance, pose, lighting, camera, composition, or one visual moment. It is a starting point, not a complete plot. Keep every stated person, relationship, appearance, setting, object, and physical fact, then create the actions, decisions, interactions, discoveries, and consequences that follow from it, driven by the selected genre arc, so the story always moves past the source moment with distinct events and a payoff, at every Creativity level (the level decides how far it goes). Fold visual details into actions instead of describing them again and again, and drop image-generation terms (camera framing, bokeh, lens effects, render quality).

If SOURCE is itself a prompt describing only a desired visual/art style (art direction, technique, palette) rather than characters or events, still write the best short story prose the material supports and capture that style in a trailing <source_style>...</source_style> tag. If a genuine narrative also states an overall art style for the whole work (not one scene's camera or lighting), capture it in the same tag. If SOURCE states no explicit style, omit the tag; never invent one. source_style is YOUR understanding of the work's overall visual style, distilled and subject-free: never copy sentences, and never name characters, pose, clothing design, or events. Cover whatever the source conveys of: medium/render type and fidelity, colour palette and grading, lighting quality and colour accents, material and surface feel, camera/depth-of-field character, and mood. Write it as one dense paragraph of image-prompt style descriptors in ENGLISH regardless of the output language (the image model reads English), complete enough that a new scene in the same style could be drawn from it alone."""

_GROOM_OP_CREATE = """OPERATION: CREATE. Build a new Groomed Story from SOURCE (the original source) using the settings given, independent of any earlier draft or chat. Follow SOURCE SHAPE and the Creativity setting above; the goal is a polished, engaging story with real narrative progression.

Return exactly this structure and nothing else (no markdown fences, no headings, no notes):
<revised_story>
the complete finished story prose
</revised_story>
Optionally, after it, one <source_style>...</source_style> tag as described above."""

_GROOM_OP_EDIT = """OPERATION: CHAT ABOUT THE DRAFT. You are in a conversation with the user about the CURRENT GROOMED STORY (the working draft). Each user message gives the USER MESSAGE, and may also give ORIGINAL SOURCE (creative reference) and the CURRENT GROOMED STORY; earlier turns of this conversation may already be in context. A draft marked as edited by the user by hand is the truth now. In this operation "SOURCE" above means ORIGINAL SOURCE.
CHAT FIRST. Talk naturally, like a collaborator, and work out from the user's wording whether they are asking or instructing.
QUESTION or DISCUSSION ("why did you add a man?", "is it erotic enough?", "what do you think?", or a complaint phrased as a question): answer it in conversation and do NOT change the story. When the question is about something in the draft that ORIGINAL SOURCE does not contain, check the draft against ORIGINAL SOURCE and answer plainly: name what was added (a character, an event, a place), say that it was invented during the rewrite and that the source did not have it, and, when useful, offer to remove or change it and ask. What the user says the source contains is a fact: never defend an addition by claiming the source needed it.
INSTRUCTION ("remove...", "add...", "change...", "make it...", "I want it without a man", or agreement such as "yes, do it" to a change you proposed): change the story and return the revised story.
MIXED ("why did you add a man? remove him too"): answer the question briefly, then make the requested change and return the revised story.
Never edit on your own initiative, and never treat a question as permission to edit.
Authority when you do edit: 1) the explicit instruction and its scope; 2) character identities, major locations, and explicit story requirements; 3) the current draft's context and continuity; 4) ORIGINAL SOURCE as creative reference; 5) genre goals and creativity defaults (they may shape events only inside the requested scope, never authorize changes to unrelated passages).
Decide the edit's scope from its intent:
LOCAL EDIT (one named passage): edit only that passage, keep BEFORE and AFTER word for word (including manual user edits), do not rewrite unrelated passages, and do not continue past the passage into new events, plans, places, or threads the instruction did not ask for.
STRUCTURAL / GLOBAL EDIT (reshape, expand, retell, change events or outcomes): reconstruct as extensively as the instruction needs within its scope: events, dialogue, actions, chronology, and outcomes may change, and ORIGINAL SOURCE event fidelity does not limit you. Keep unrelated manual edits when possible. Whatever the instruction asks to add, show, or change becomes real story content, never a one-word tweak.
An instruction must change the story; if it is ambiguous, ask in your reply instead of guessing.

Return exactly one of these two structures and nothing else (no markdown fences).
WHEN YOU CHANGE THE STORY:
<revised_story>
the COMPLETE story after this edit: full text, never a patch, never commentary
</revised_story>
<chat_reply>
what you changed, briefly
</chat_reply>
WHEN YOU DO NOT CHANGE THE STORY (question, discussion, unclear request): only
<chat_reply>
your reply
</chat_reply>
with no revised_story tag and never repeating the story.
A reply without <revised_story> means the story was NOT changed, so such a reply must never say or imply that you changed, adjusted, or removed anything. If the user asked for a change (including "I want it without X"), the change is made only by returning the complete <revised_story>.
chat_reply is always a natural reply in the language and tone of the user's message: when answering or discussing, answer fully (more than three sentences is fine); do not add polite endings or gendered particles (such as ครับ, ค่ะ, คะ) unless the user used them, and keep one consistent register; never repeat the story."""

_GROOM_OPERATIONS = {"create": _GROOM_OP_CREATE, "edit": _GROOM_OP_EDIT}


def _build_groom_system_prompt(operation: str = "create") -> str:
    """Common Groom core + one compact operation block. Structural routing on the caller's operation only."""
    return _GROOM_CORE + "\n\n" + _GROOM_OPERATIONS.get(operation, _GROOM_OP_CREATE)


# Compatibility name: the CREATE assembly.
_GROOM_SYSTEM_PROMPT = _build_groom_system_prompt("create")


def _extract_tag(text: str, name: str) -> str | None:
    """Structural tag extraction only: inner text of <name>...</name>, or None when absent/unclosed."""
    m = re.search(rf"<{name}>(.*?)</{name}>", text or "", re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else None


_INNER_STYLE_RE = re.compile(r"<source_style>(.*?)(?:</source_style>|\Z)", re.DOTALL | re.IGNORECASE)


def _strip_inner_source_style(story: str) -> tuple[str, str]:
    """(story_without_style_block, style_text). The model sometimes puts its <source_style> metadata INSIDE the story tag;
    that block is never story text. Structural removal only (an unclosed block is cut to the end of the text)."""
    m = _INNER_STYLE_RE.search(story or "")
    if not m:
        return (story or "").strip(), ""
    return ((story[:m.start()] + story[m.end():]).strip(), m.group(1).strip())


def _parse_create_output(text: str) -> tuple[str, str, str]:
    """(story, source_style, chat_reply) from a CREATE response. Prefers <revised_story>; with no usable tag the
    whole response is the story (the pre-chat behavior), so a CREATE never fails on formatting alone."""
    story = _extract_tag(text, "revised_story")
    style = _extract_tag(text, "source_style") or ""
    reply = _extract_tag(text, "chat_reply") or ""
    if story:
        story, inner_style = _strip_inner_source_style(story)
        return story, style or inner_style, reply
    body = re.sub(r"</?revised_story>", "", text or "", flags=re.IGNORECASE)
    body, legacy_style = _split_source_style(body)
    body, inner_style = _strip_inner_source_style(body)      # also catches an unclosed <source_style> tail
    return body, style or legacy_style or inner_style, reply


def parse_chat_edit_output(text: str) -> tuple[str | None, str]:
    """(revised_story_or_None, chat_reply). revised_story is None when the tag is missing/unclosed/empty,
    so the caller keeps the current draft untouched. A missing chat_reply stays empty: nothing is invented about what changed."""
    story = _extract_tag(text, "revised_story")
    if story:
        story, _inner = _strip_inner_source_style(story)
    if not story:
        return None, ""
    return story, _extract_tag(text, "chat_reply") or ""


# ── Output-language validation (deterministic; it never edits, translates, or repairs the story) ─────────────────
# A story that is not in the requested language is rejected and Groom is asked, a bounded number of times, to
# rewrite that same story in the requested language. Python only measures scripts/words, counts attempts, routes the
# calls, and rejects an invalid result; the LLM does all the writing.
_LANG_RETRY_LIMIT = 3          # correction attempts after the first generation (so at most 1 + 3 calls)
_SOURCE_STYLE_BLOCK_RE = re.compile(r"<source_style>.*?</source_style>", re.DOTALL | re.IGNORECASE)
_MARKUP_TAG_RE = re.compile(r"</?[A-Za-z_][\w.\-]*>")
_WORD_RE = re.compile(r"[A-Za-z']+")
_TARGET_SCRIPT = {            # languages whose script differs from Latin: code -> unicode ranges of their letters
    "th": ((0x0E00, 0x0E7F),),
    "hi": ((0x0900, 0x097F),),
    "ru": ((0x0400, 0x04FF),),
    "ar": ((0x0600, 0x06FF), (0x0750, 0x077F)),
    "ko": ((0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F)),
    "zh": ((0x4E00, 0x9FFF),),
    "zh-tw": ((0x4E00, 0x9FFF),),
    "ja": ((0x3040, 0x30FF), (0x4E00, 0x9FFF)),
}
_KANA = ((0x3040, 0x30FF),)
_ENGLISH_FUNCTION_WORDS = frozenset(
    "the and of to in was were her his she he that with as on for at by from it is had but not they their you your "
    "this which be been into over under while when then there".split())


def _count_in(text: str, ranges) -> int:
    return sum(1 for ch in text if any(lo <= ord(ch) <= hi for lo, hi in ranges))


def _language_check(story: str, lang: str) -> dict:
    """Does the story's actual text read as the requested language? Tags/metadata are ignored; names, proper nouns and
    short foreign phrases are tolerated because the test is proportional ("predominantly"), not "every character"."""
    code = (lang or "").strip()
    code = code if code in LANG_MAP else "en"
    text = _SOURCE_STYLE_BLOCK_RE.sub(" ", story or "")
    text = _MARKUP_TAG_RE.sub(" ", text)
    latin = sum(1 for ch in text if "A" <= ch <= "Z" or "a" <= ch <= "z")
    ranges = _TARGET_SCRIPT.get(code)
    words = _WORD_RE.findall(text)
    eng_share = (sum(1 for w in words if w.lower() in _ENGLISH_FUNCTION_WORDS) / len(words)) if words else 0.0
    res = {"ok": True, "reason": "", "language": code, "latin_letters": latin, "target_letters": None,
           "script_share": None, "english_word_share": round(eng_share, 3)}
    if ranges:
        target = _count_in(text, ranges)
        total = target + latin
        res["target_letters"] = target
        res["script_share"] = round(target / total, 3) if total else 0.0
        if total < 30:
            res["reason"] = "too little text to judge; accepted"
        elif target < 40 or target / total < 0.5:
            res.update(ok=False, reason=f"{lang_prompt_name(code)} script is only {res['script_share']:.0%} of the letters "
                                        f"({target} of {total}); narrative is predominantly Latin-script text")
        elif code == "ja" and _count_in(text, _KANA) < 0.05 * target:
            res.update(ok=False, reason="no kana found; the text looks like Chinese, not Japanese")
        elif code in ("zh", "zh-tw") and _count_in(text, _KANA) > 0.1 * target:
            res.update(ok=False, reason="substantial kana found; the text looks like Japanese, not Chinese")
        else:
            res["reason"] = f"{lang_prompt_name(code)} script is {res['script_share']:.0%} of the letters ({target} of {total})"
        return res
    # Latin-script target (English and the other Latin languages)
    nonlatin = sum(1 for ch in text if ch.isalpha() and not ("A" <= ch <= "Z" or "a" <= ch <= "z"))
    if latin + nonlatin < 30:
        res["reason"] = "too little text to judge; accepted"
    elif latin / (latin + nonlatin) < 0.7:
        res.update(ok=False, reason=f"only {latin / (latin + nonlatin):.0%} of the letters are Latin-script; "
                                    f"expected {lang_prompt_name(code)}")
    elif code == "en" and eng_share < 0.2:
        res.update(ok=False, reason=f"English function words are only {eng_share:.0%} of the words; text does not read as English")
    elif code != "en" and eng_share > 0.25:
        res.update(ok=False, reason=f"English function words are {eng_share:.0%} of the words; text reads as English, "
                                    f"expected {lang_prompt_name(code)}")
    else:
        res["reason"] = f"reads as {lang_prompt_name(code)} (English function words {eng_share:.0%})"
    return res


def _language_correction_user_prompt(lang: str, failed_story: str, check: dict, source_style: str = "") -> str:
    """Targeted correction request. The failed story is fed back so the retry rewrites THAT story rather than
    starting an unrelated creative regeneration."""
    target = lang_prompt_name(lang if (lang or "").strip() in LANG_MAP else "en")
    detected = "English" if (check.get("english_word_share") or 0) >= 0.2 or (check.get("script_share") is not None) else "another language"
    msg = (f"Your previous revised_story was written predominantly in {detected}, but the requested output language is {target}. "
           f"Rewrite the revised_story entirely in natural {target} prose. Preserve all established characters, identity facts, "
           f"chronology, events, relationships, outcomes, and story length. Do not summarize, censor, simplify, add new "
           f"characters, or introduce new events. Keep the required output tags unchanged. Return only the corrected "
           f"structured output.")
    style_part = (f"\n<source_style>{source_style}</source_style>\n(source_style is image-prompt metadata and always stays in "
                  f"ENGLISH, whatever the output language.)") if (source_style or "").strip() else ""
    return f"Language: {target}\n\n{msg}\n\nPREVIOUS revised_story:\n<revised_story>\n{failed_story}\n</revised_story>{style_part}"


def _style_needs_english(style: str) -> bool:
    """True when source_style is mostly non-Latin script (e.g. Thai). It is read by the image model, so it must be English."""
    letters = [c for c in (style or "") if c.isalpha()]
    return bool(letters) and sum(ord(c) > 0x024F for c in letters) / len(letters) > 0.2


async def _ensure_english_style(style: str, call, on_log=None) -> str:
    """One bounded translation request when the model wrote source_style in the output language. Keeps the original on failure."""
    if not _style_needs_english(style):
        return style
    if on_log:
        await on_log("🌐 source_style is not English — requesting an English version")
    raw = await call(_GROOM_SYSTEM_PROMPT,
                     "Translate this visual-style description into ENGLISH image-prompt style descriptors, one dense paragraph, "
                     "keeping every descriptor and adding nothing. Return only <source_style>...</source_style>.\n\n"
                     f"<source_style>{style}</source_style>")
    new = (_extract_tag(raw or "", "source_style") or "").strip()
    return new if new and not _style_needs_english(new) else style


async def _correct_story_language(story: str, lang: str, *, system_prompt: str, call, parse, on_log=None,
                                  source_style: str = "", label: str = "Story Editor"):
    """Validate `story`; while it is in the wrong language, ask for a rewrite (at most _LANG_RETRY_LIMIT times).
    call(system_prompt, user_prompt) -> raw text or None. parse(raw) -> (story_or_None, source_style_or_None).
    Returns (story, source_style, ok, attempts, last_raw). `attempts[0]` is the first generation's check."""
    check = _language_check(story, lang)
    attempts = [{"attempt": 0, **check}]
    if on_log:
        await on_log(f"🌐 {label} language check (initial): {'PASS' if check['ok'] else 'FAIL'} — {check['reason']}")
    last_raw = ""
    n = 0
    while not check["ok"] and n < _LANG_RETRY_LIMIT:
        n += 1
        if on_log:
            await on_log(f"🌐 {label}: wrong language, correction attempt {n}/{_LANG_RETRY_LIMIT}")
        raw = await call(system_prompt, _language_correction_user_prompt(lang, story, check, source_style))
        last_raw = raw or ""
        new_story, new_style = parse(raw) if (raw or "").strip() else (None, None)
        if not (new_story or "").strip():
            check = {"ok": False, "reason": "correction response had no usable revised_story", "language": check.get("language"),
                     "latin_letters": None, "target_letters": None, "script_share": None, "english_word_share": None}
        else:
            story = new_story
            if new_style:
                source_style = new_style
            check = _language_check(story, lang)
        attempts.append({"attempt": n, **check})
        if on_log:
            await on_log(f"🌐 {label} language check (correction {n}): {'PASS' if check['ok'] else 'FAIL'} — {check['reason']}")
    return story, source_style, check["ok"], attempts, last_raw


async def _call_backend(llm_wf_file: str, wf_template, system_prompt: str, user_prompt: str, *,
                        grok_api_key: str, grok_model: str, on_log=None,
                        session_id: str = "", reset: bool = True) -> str | None:
    """Transport only: the same backend routing run_story_grooming / run_story_chat_edit use."""
    if llm_wf_file == GROK_MARKER:
        return await _call_grok_text(system_prompt, user_prompt, api_key=grok_api_key, model=grok_model,
                                     max_tokens=_LOCAL_MAX_TOKENS, on_log=on_log)
    return await _call_local_text(wf_template or {}, system_prompt, user_prompt, max_tokens=_LOCAL_MAX_TOKENS, on_log=on_log,
                                  session_id=session_id, reset=reset)


def _language_failure_message(lang: str, attempts: list) -> str:
    n = max(len(attempts) - 1, 0)
    return (f"output language check failed: requested {lang_prompt_name(lang if (lang or '').strip() in LANG_MAP else 'en')}, "
            f"but the story is still not in that language after {n} correction attempt(s) — {attempts[-1]['reason']}")


def _build_groom_user_prompt(
    raw_story: str,
    *,
    lang: str,
    genre: str,
    genre2: str,
    requirement: str,
    story_length: str,
    creativity: str,
) -> str:
    """Serialize explicit user settings + source. No semantic interpretation."""
    lang_label = lang_prompt_name(lang)
    genre_lines = _genre_prompt_lines(genre, genre2) or ["Genre: No specific genre"]
    creativity_text = _CREATIVITY_TEXT.get(
        (creativity or "balanced").strip().lower(),
        str(creativity or "balanced"),
    )
    length_text = _LENGTH_TEXT.get(
        (story_length or "auto").strip().lower(),
        str(story_length or "auto"),
    )

    parts = [
        f"Language: {lang_label}",
        *genre_lines,
        f"Creativity: {creativity_text}",
        f"Length: {length_text}",
    ]
    if (requirement or "").strip():
        parts.append(f"Special requirements: {(requirement or '').strip()}")
    # Style never outranks SOURCE or source shape.
    parts.append("Follow SOURCE SHAPE: refine an existing story while preserving its facts, relationships, and outcomes, or create the story from a static visual description. Creativity, genre, and length apply as stated above.")
    parts.append("SOURCE:\n" + (raw_story or ""))
    return "\n".join(parts)


async def _call_local_text(
    wf_template: dict,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int,
    on_log=None,
    session_id: str = "",
    reset: bool = True,
) -> str | None:
    """Transport only: send system/user text to one LLMSessionChatNode and return raw text.
    Without session_id every call is a fresh, isolated session. With session_id the call continues that
    Comfy chat session (reset=False), which is how Groom Chat keeps the conversation that New started."""
    wf = copy.deepcopy(wf_template or {})
    hit = False
    for nid, node in wf.items():
        if not isinstance(node, dict) or node.get("class_type") != "LLMSessionChatNode":
            continue
        inputs = wf[nid].setdefault("inputs", {})
        inputs["user_text"] = user_prompt
        inputs["system_prompt"] = system_prompt
        inputs["session_id"] = session_id or uuid.uuid4().hex[:8]
        inputs["reset_session"] = True if not session_id else bool(reset)
        if session_id:
            # Bounded, summary-free window: only the last _SESSION_MAX_TURNS exchanges stay in context and older ones are simply
            # dropped (no extra summarising LLM call, fewer tokens). Groom Chat re-sends the story before it falls out of the window.
            inputs["max_turns"] = _SESSION_MAX_TURNS
            inputs["summarize_old_history"] = False
        inputs["max_tokens"] = max_tokens
        hit = True

    if not hit:
        if on_log:
            await on_log("❌ Story Editor: workflow has no LLMSessionChatNode")
        return None

    loop = asyncio.get_event_loop()
    cid = uuid.uuid4().hex
    if on_log:
        await on_log(f"📋 STORY EDITING SYSTEM:\n{system_prompt}")
        await on_log(f"📤 STORY EDITING USER:\n{user_prompt}")

    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, cid)
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Story Editor queue: {exc}")
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
                        await on_log(f"📥 STORY EDITING RAW:\n{text}")
                    return text
        status = ((entry.get("status") or {}).get("status_str") or "").lower()
        if status in ("success", "error"):
            break

    if on_log:
        await on_log("❌ Story Editor: local LLM returned no text")
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
    """Transport only: one xAI chat-completions call, returning message.content verbatim.
    history = earlier {"role","content"} user/assistant messages of the same conversation (Grok's own chat format)."""
    if not api_key:
        if on_log:
            await on_log("❌ Story Editor: Grok API key is empty")
        return None

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            *[{"role": m["role"], "content": m["content"]} for m in (history or [])],
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
        await on_log(f"📋 STORY EDITING SYSTEM:\n{system_prompt}")
        await on_log(f"📤 STORY EDITING USER:\n{user_prompt}")

    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(None, _post, payload)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        # Transport compatibility only: older model aliases may reject reasoning_effort.
        if exc.code == 400 and "reasoning" in body.lower():
            retry_payload = dict(payload)
            retry_payload.pop("reasoning_effort", None)
            try:
                response = await loop.run_in_executor(None, _post, retry_payload)
            except Exception as retry_exc:
                if on_log:
                    await on_log(f"❌ Story Editor Grok retry: {retry_exc}")
                return None
        else:
            if on_log:
                await on_log(f"❌ Story Editor Grok HTTP {exc.code}: {body[:500]}")
            return None
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Story Editor Grok: {exc}")
        return None

    choices = response.get("choices") if isinstance(response, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else {}
    message = first.get("message") if isinstance(first, dict) else {}
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str):
        if on_log:
            await on_log("❌ Story Editor: Grok response has no text content")
        return None
    if on_log:
        await on_log(f"📥 STORY EDITING RAW:\n{text}")
    return text


_SECOND_PASS_ENABLED = True       # after New, one follow-up message in the same conversation asks Groom to complete what its story left out
_SECOND_PASS_MIN_KEEP = 0.6       # structural sanity guard: a second-pass story shorter than this share of the first is a truncated/garbled reply and is ignored


def _second_pass_prompt(lang: str) -> str:
    """The follow-up message of New (round two). It reuses the conversation, so the story is not sent again."""
    return (
        "SECOND PASS. Review the story you just wrote (it is in our conversation above; do not ask for it again) and complete what it leaves out, without changing anything else.\n"
        "1. CLOTHING: every character who appears, secondary characters included, must have what they wear stated plainly where they first appear, completely: "
        "upper body, lower body, and footwear where visible (or one full-body garment or armor and what it covers, or barefoot). If the story already states clothing, bareness, or color, keep it. If it does not, leave that item unstated. Do not guess garments, colors, or footwear.\n"
        "2. SURROUNDINGS: the place, time, lighting, and the main objects around the action must be stated well enough to picture the scene.\n"
        "3. SECONDARY CHARACTERS: only if the story already has other characters, each of them has a name or a clear role, an age or apparent age, and a recognizable look.\n"
        f"4. LANGUAGE: the whole story must be in {lang_prompt_name(lang)}; fix any words or sentences in another language (character names and unavoidable proper nouns excepted).\n"
        "Never add a character, event, or line of dialogue that is not already in the story. Keep every event, name, and line of dialogue as it is, and add the missing details inside the existing sentences or as short additions where each character or place first appears. "
        "Write the additions as ordinary prose: no bold, asterisks, brackets, emoji, or markers of any kind. "
        "Reply with the complete story only, exactly as before:\n<revised_story>\nthe complete story\n</revised_story>"
    )


async def _second_pass(story_text: str, lang: str, *, llm_wf_file: str, wf_template, user_prompt: str, first_raw: str,
                       session_id: str, grok_api_key: str, grok_model: str, on_log=None) -> dict:
    """Round two of New. Returns {"story": str|None, "raw": str, "note": str}; story is None when the pass is skipped,
    fails, or returns something unusable, in which case the caller keeps the first story. Python only routes the call,
    checks structure/length/language, and never edits the text."""
    out = {"story": None, "raw": "", "note": ""}
    follow = _second_pass_prompt(lang)
    if llm_wf_file == GROK_MARKER:
        raw = await _call_grok_text(_GROOM_SYSTEM_PROMPT, follow, api_key=grok_api_key, model=grok_model,
                                    max_tokens=_LOCAL_MAX_TOKENS, on_log=on_log,
                                    history=[{"role": "user", "content": user_prompt}, {"role": "assistant", "content": first_raw}])
    elif session_id:
        raw = await _call_local_text(wf_template or {}, _GROOM_SYSTEM_PROMPT, follow, max_tokens=_LOCAL_MAX_TOKENS,
                                     on_log=on_log, session_id=session_id, reset=False)
    else:
        out["note"] = "skipped (no session)"
        return out
    out["raw"] = raw or ""
    if not (raw or "").strip():
        out["note"] = "no text returned"
        return out
    new, _style, _reply = _parse_create_output(raw)
    if not new or "<revised_story" not in raw.lower():
        out["note"] = "no <revised_story> tag"
        return out
    if len(new) < _SECOND_PASS_MIN_KEEP * len(story_text or ""):
        out["note"] = "reply too short to be the full story"
        return out
    chk = _language_check(new, lang)
    if not chk.get("ok"):
        out["note"] = f"language check failed: {chk.get('reason')}"
        return out
    out["story"] = new
    out["note"] = "applied" if _norm_story(new) != _norm_story(story_text) else "no change"
    return out


async def run_story_grooming(
    raw_story: str,
    llm_wf_file: str,
    lang: str = "en",
    genre: str = "",
    genre2: str = "",
    requirement: str = "",
    on_log=None,
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    outline_mode: str = "cinematic",   # kept only for UI/API compatibility; Groom does not use it
    story_length: str = "auto",
    dialogue_style: str = "none",      # kept only for UI/API compatibility; Groom does not use it
    creativity: str = "balanced",
) -> tuple[str, dict]:
    """Clean Groom: one LLM call, one story result, no semantic Python post-processing."""
    card = {
        "clean_groom": True,
        "final_check": None,
        "final_check_note": "",
        "source_style": "",
        "chat_reply": "",
        "error": "",
    }

    if not (raw_story or "").strip():
        card["error"] = "empty_source"
        return raw_story or "", card

    wf_template = None
    if llm_wf_file != GROK_MARKER:
        if not (llm_wf_file or "").strip():
            card["error"] = "no_llm"
            if on_log:
                await on_log("❌ Story Editor: no local LLM workflow selected")
            return raw_story, card
        wf_path = LLM_DIR / f"{llm_wf_file}.json"
        if not wf_path.exists():
            card["error"] = f"missing workflow: {wf_path.name}"
            if on_log:
                await on_log(f"❌ Story Editor: missing {wf_path}")
            return raw_story, card
        try:
            wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
        except Exception as exc:
            card["error"] = str(exc)
            if on_log:
                await on_log(f"❌ Story Editor workflow read: {exc}")
            return raw_story, card

    user_prompt = _build_groom_user_prompt(
        raw_story,
        lang=lang,
        genre=genre,
        genre2=genre2,
        requirement=requirement,
        story_length=story_length,
        creativity=creativity,
    )

    if on_log:
        backend = "Grok" if llm_wf_file == GROK_MARKER else f"Comfy ({llm_wf_file})"
        await on_log(f"✨ Story Editor → {backend}")

    if llm_wf_file == GROK_MARKER:
        result = await _call_grok_text(
            _GROOM_SYSTEM_PROMPT,
            user_prompt,
            api_key=grok_api_key,
            model=grok_model,
            max_tokens=_LOCAL_MAX_TOKENS,
            on_log=on_log,
        )
    else:
        # New starts a Comfy chat session; Groom Chat continues it (reset only here, on the first call).
        card["session_id"] = _new_session_id = uuid.uuid4().hex[:12]
        result = await _call_local_text(
            wf_template or {},
            _GROOM_SYSTEM_PROMPT,
            user_prompt,
            max_tokens=_LOCAL_MAX_TOKENS,
            on_log=on_log,
            session_id=_new_session_id,
            reset=True,
        )

    if result is None or not result.strip():
        card["error"] = "LLM returned no story text"
        card.pop("session_id", None)
        return raw_story, card

    # Deliberately no semantic cleanup/repair/checking here.
    # Only outer whitespace is removed for storage/UI cleanliness, and the optional trailing
    # <source_style> tag is split off structurally (never interpreted) into the card.
    story_text, card["source_style"], card["chat_reply"] = _parse_create_output(result)

    # Validate the actual story language; wrong language is corrected (bounded) or reported, never passed downstream.
    async def _call(sp, up):
        return await _call_backend(llm_wf_file, wf_template, sp, up, grok_api_key=grok_api_key,
                                   grok_model=grok_model, on_log=on_log,
                                   session_id=card.get("session_id", ""), reset=False)

    def _parse(raw):
        st, sty, _r = _parse_create_output(raw)
        return st, sty

    story_text, card["source_style"], _lang_ok, card["lang_check"], _last_raw = await _correct_story_language(
        story_text, lang, system_prompt=_GROOM_SYSTEM_PROMPT, call=_call, parse=_parse, on_log=on_log,
        source_style=card["source_style"])
    card["source_style"] = await _ensure_english_style(card["source_style"], _call, on_log)
    if not _lang_ok:
        card["error"] = _language_failure_message(lang, card["lang_check"])
        card["last_raw"] = _last_raw or result
        card.pop("session_id", None)
        card.pop("grok_messages", None)
        if on_log:
            await on_log(f"❌ Story Editor: {card['error']}\nLAST RAW RESPONSE:\n{card['last_raw']}")
        return raw_story, card
    _first_raw = _last_raw or result
    card["second_pass"] = "disabled"
    if _SECOND_PASS_ENABLED:
        sp = await _second_pass(story_text, lang, llm_wf_file=llm_wf_file, wf_template=wf_template, user_prompt=user_prompt,
                                first_raw=_first_raw, session_id=card.get("session_id", ""), grok_api_key=grok_api_key,
                                grok_model=grok_model, on_log=on_log)
        card["second_pass"] = sp["note"]
        if on_log:
            await on_log(f"🔎 Story Editor second pass: {sp['note']}")
        if sp["story"] is not None:
            _changed = _norm_story(sp["story"]) != _norm_story(story_text)
            if _changed and on_log:
                await on_log("📝 Story Editor first-pass story (before the second pass changed it):\n" + story_text)
            story_text = sp["story"]
            if _changed and llm_wf_file == GROK_MARKER:
                # the conversation now holds the completed story: keep the second exchange so chat edits start from it
                card["grok_messages"] = [{"role": "user", "content": user_prompt}, {"role": "assistant", "content": _first_raw},
                                         {"role": "user", "content": _second_pass_prompt(lang)},
                                         {"role": "assistant", "content": f"<revised_story>\n{story_text}\n</revised_story>"}]
    if llm_wf_file == GROK_MARKER and "grok_messages" not in card:
        # Grok has no session: the conversation is its own message list. New starts it with the create exchange.
        card["grok_messages"] = [{"role": "user", "content": user_prompt},
                                 {"role": "assistant", "content": _first_raw}]
    return story_text, card


def _build_groom_edit_user_prompt(original_source: str, current_story: str, user_request: str, *,
                                  lang: str, genre: str, genre2: str, creativity: str) -> str:
    """Serialize one EDIT CURRENT DRAFT turn. Special requirements and length are CREATE-level settings and are
    not sent; genre/creativity are defaults (the request outranks them)."""
    genre_lines = _genre_prompt_lines(genre, genre2)
    creativity_text = _CREATIVITY_TEXT.get((creativity or "balanced").strip().lower(), str(creativity or "balanced"))
    return "\n".join([
        "OPERATION:\nEDIT CURRENT DRAFT",
        f"Story language: {lang_prompt_name(lang)}",
        "Genre defaults (for structural edits within the requested scope only; the request outranks them. A local edit does not apply or force an arc and must not change unrelated passages because of genre):",
        *(genre_lines or ["Genre: No specific genre"]),
        f"Creativity (default level; the request outranks it): {creativity_text}",
        "",
        "ORIGINAL SOURCE:\n" + (original_source or ""),
        "",
        "CURRENT GROOMED STORY:\n" + (current_story or ""),
        "",
        "USER MESSAGE:\n" + (user_request or ""),
    ])


_SESSION_STORY_REFRESH = 4     # chat-only turns after which the story is re-sent; must stay below _SESSION_MAX_TURNS


def _norm_story(text: str) -> str:
    return " ".join((text or "").split())


def _session_alive(wf_template: dict, session_id: str) -> bool:
    """Structural check only: does the Comfy session history file for this id still exist? (unknown location -> assume yes)"""
    for node in (wf_template or {}).values():
        if isinstance(node, dict) and node.get("class_type") == "LLMSessionChatNode":
            hist_dir = str((node.get("inputs") or {}).get("history_dir") or "").strip()
            if not hist_dir:
                return True
            try:
                return (Path(hist_dir) / f"{session_id}.json").is_file()
            except OSError:
                return True
    return True


def _clean_history(history) -> list[dict]:
    """Keep only well-formed user/assistant messages, in order, starting with a user message (structural only)."""
    out = [{"role": m["role"], "content": m["content"]} for m in (history or [])
           if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip()]
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _trim_history(history: list[dict]) -> list[dict]:
    """The last _SESSION_MAX_TURNS exchanges (user+assistant pairs); older messages are dropped, never summarised."""
    keep = history[-2 * _SESSION_MAX_TURNS:]
    while keep and keep[0]["role"] != "user":
        keep = keep[1:]
    return keep


def _session_bookkeeping(out: dict, current_story: str, session_story: str, session_age: int,
                         grok_hist: list | None = None, user_prompt: str = "") -> None:
    """Tell the caller which story the session now holds and how many chat-only turns ago it last appeared.
    For Grok also return the conversation to keep: the history + this exchange (the reply carries the story only if it changed)."""
    if grok_hist is not None:
        _reply = out.get("chat_reply") or ""
        _asst = (f"<revised_story>\n{out['revised_story']}\n</revised_story>\n<chat_reply>\n{_reply}\n</chat_reply>"
                 if out.get("changed") and out.get("revised_story") else f"<chat_reply>\n{_reply}\n</chat_reply>")
        out["grok_messages"] = _trim_history(list(grok_hist) + [{"role": "user", "content": user_prompt},
                                                                  {"role": "assistant", "content": _asst}])
    if out.get("changed") and out.get("revised_story"):
        out["session_story"], out["session_age"] = out["revised_story"], 0
    elif out.get("story_sent", True):
        out["session_story"], out["session_age"] = current_story, 0
    else:
        out["session_story"], out["session_age"] = session_story, int(session_age or 0) + 1


def _build_groom_session_edit_user_prompt(current_story: str, user_request: str, *, lang: str,
                                          send_story: bool, edited: bool) -> str:
    """One chat turn inside an existing Groom session. The earlier turns (ORIGINAL SOURCE, genre, creativity and the story
    itself) are already in the session, so the draft is sent only when the user changed it by hand or the story has fallen
    out of the session's recent window; otherwise only the message is sent (token saving)."""
    parts = ["OPERATION:\nCHAT ABOUT THE DRAFT (continuing our conversation)", f"Story language: {lang_prompt_name(lang)}", ""]
    if send_story:
        parts.append(("CURRENT GROOMED STORY (the user EDITED the draft by hand; this text is now the truth):\n" if edited
                      else "CURRENT GROOMED STORY (latest draft):\n") + (current_story or ""))
    else:
        parts.append("(The draft is unchanged since your last story in our conversation above.)")
    parts += ["", "USER MESSAGE:\n" + (user_request or "")]
    return "\n".join(parts)


async def run_story_chat_edit(
    original_source: str,
    current_story: str,
    user_request: str,
    llm_wf_file: str,
    lang: str = "en",
    genre: str = "",
    genre2: str = "",
    creativity: str = "balanced",
    on_log=None,
    grok_api_key: str = "",
    grok_model: str = "grok-4-latest",
    session_id: str = "",
    session_story: str = "",
    session_age: int = 0,
    grok_messages: list | None = None,
) -> dict:
    """CHAT ABOUT THE DRAFT: one LLM call. Returns {"ok", "revised_story", "chat_reply", "error", "raw"}.
    revised_story is None unless the response carried a closed <revised_story> tag; the caller then keeps the
    current draft exactly as it is. Python never decides how the story is rewritten."""
    out = {"ok": False, "revised_story": None, "chat_reply": "", "error": "", "raw": "", "changed": False, "chat_only": False}
    if not (user_request or "").strip():
        out["error"] = "empty_request"
        return out
    wf_template = None
    if llm_wf_file != GROK_MARKER:
        if not (llm_wf_file or "").strip():
            out["error"] = "no_llm"
            return out
        wf_path = LLM_DIR / f"{llm_wf_file}.json"
        if not wf_path.exists():
            out["error"] = f"missing workflow: {wf_path.name}"
            return out
        try:
            wf_template = json.loads(wf_path.read_text(encoding="utf-8"))
        except Exception as exc:
            out["error"] = str(exc)
            return out

    system_prompt = _build_groom_system_prompt("edit")
    # With a session id (local backend) this turn continues the Comfy chat session that New started, so the model already
    # holds the original source and the earlier turns; only the current draft (it may carry manual edits) and the request are sent.
    _chat_session = (session_id or "").strip() if llm_wf_file != GROK_MARKER else ""
    _reset = False
    _grok_hist = _trim_history(_clean_history(grok_messages)) if llm_wf_file == GROK_MARKER else []
    out["story_sent"] = True
    if _chat_session and not _session_alive(wf_template, _chat_session):
        out["session_reset"] = True        # the Comfy session file is gone: restart it from the full context below
        _reset = True
        user_prompt = _build_groom_edit_user_prompt(
            original_source, current_story, user_request,
            lang=lang, genre=genre, genre2=genre2, creativity=creativity)
    elif _chat_session or _grok_hist:
        _edited = bool((session_story or "").strip()) and _norm_story(current_story) != _norm_story(session_story)
        _send = _edited or not (session_story or "").strip() or int(session_age or 0) >= _SESSION_STORY_REFRESH
        out["story_sent"] = _send
        user_prompt = _build_groom_session_edit_user_prompt(current_story, user_request, lang=lang, send_story=_send, edited=_edited)
    else:
        user_prompt = _build_groom_edit_user_prompt(
            original_source, current_story, user_request,
            lang=lang, genre=genre, genre2=genre2, creativity=creativity)
    out["session_id"] = _chat_session
    if on_log:
        backend = "Grok" if llm_wf_file == GROK_MARKER else f"Comfy ({llm_wf_file})"
        await on_log(f"💬 Story Editor chat → {backend}")
    if llm_wf_file == GROK_MARKER:
        result = await _call_grok_text(system_prompt, user_prompt, api_key=grok_api_key, model=grok_model,
                                       max_tokens=_LOCAL_MAX_TOKENS, on_log=on_log, history=_grok_hist)
    else:
        result = await _call_local_text(wf_template or {}, system_prompt, user_prompt,
                                        max_tokens=_LOCAL_MAX_TOKENS, on_log=on_log,
                                        session_id=_chat_session, reset=_reset)
    out["raw"] = result or ""
    if result is None or not result.strip():
        out["error"] = "LLM returned no text"
        return out
    story, reply = parse_chat_edit_output(result)
    if story is not None:
        # Structural comparison only: did the returned text differ from the draft that was sent?
        out["changed"] = " ".join(story.split()) != " ".join((current_story or "").split())
        if not out["changed"]:
            reply = _extract_tag(result, "chat_reply") or "I didn't change the story."
    if story is not None and out["changed"]:
        # The revised story is new text: it must be in the requested language (an unchanged story is the user's own draft).
        async def _call(sp, up):
            return await _call_backend(llm_wf_file, wf_template, sp, up, grok_api_key=grok_api_key,
                                       grok_model=grok_model, on_log=on_log,
                                       session_id=_chat_session, reset=False)

        def _parse(raw):
            _st = _extract_tag(raw, "revised_story")
            return (_strip_inner_source_style(_st)[0] if _st else _st), None

        story, _sty, _lang_ok, out["lang_check"], _last_raw = await _correct_story_language(
            story, lang, system_prompt=system_prompt, call=_call, parse=_parse, on_log=on_log,
            label="Story Editor chat")
        if not _lang_ok:
            out.update(ok=False, revised_story=None, changed=False, chat_only=False,
                       error=_language_failure_message(lang, out["lang_check"]))
            out["raw"] = _last_raw or out["raw"]
            if on_log:
                await on_log(f"❌ Story Editor chat: {out['error']} — draft kept.\nLAST RAW RESPONSE:\n{out['raw']}")
            return out
        out["changed"] = " ".join(story.split()) != " ".join((current_story or "").split())
    if story is None:
        # A chat-only turn (a question, "I'm confused", thanks...): the model answered without a story. Show that
        # answer in the chat and leave the draft alone. Never read a story out of free prose.
        _only = _extract_tag(result, "chat_reply")
        if _only is None and "<revised_story" not in result.lower():
            # No story tag at all: this is a conversational reply (the draft is never touched). A reply that opens <chat_reply> but
            # never closes it, or that has no tags, is still shown as the chat answer, whatever its length.
            _plain = re.sub(r"</?chat_reply>", "", result, flags=re.IGNORECASE).strip()
            if 0 < len(_plain) <= _CHAT_REPLY_MAX_CHARS:
                _only = _plain
        if _only:
            out.update(ok=True, revised_story=current_story, chat_reply=_only, changed=False, chat_only=True)
            _session_bookkeeping(out, current_story, session_story, session_age,
                                 _grok_hist if llm_wf_file == GROK_MARKER else None, user_prompt)
            return out
        out["error"] = "response had no complete <revised_story> tag"
        if on_log:
            await on_log(f"⚠ Story Editor chat: malformed response, draft kept. RAW:\n{result}")
        return out
    out.update(ok=True, revised_story=story, chat_reply=reply)
    _session_bookkeeping(out, current_story, session_story, session_age,
                         _grok_hist if llm_wf_file == GROK_MARKER else None, user_prompt)
    return out


# Canonical name — Story Editor (legacy: Groom). The legacy entry point above stays for compatibility; run_story_editing is the same function.
run_story_editing = run_story_grooming
