"""
a1_static_conversation.py - A1 Static as a STEP-BY-STEP CONVERSATION with a Visual Novel writer.

Python is the controller: it sends one user request per step, keeps the conversation history, checks the STRUCTURE of every answer and
stops when an answer cannot be accepted. The LLM writes everything that is story (characters, plan, anchors, world, locations, objects,
macro events, continuity). Python never writes, repairs or guesses story content and never reads meaning (no keyword heuristics).

Steps (one user request = one kind of result; the source is sent inside the first request, which also asks for the characters):
  1 CHARACTERS   source + genre + requirements -> <charN.*> sets
  2 STORY PLAN   selected Static Arc + accepted cast -> <story_plan> (5 steps: action / end_pose / state / leads_to)
  3 ANCHORS      source anchors checked against the plan -> <source_anchors>; a reported plan conflict gets ONE plan revision, then anchors again
  4 WORLD        <world_sheet> (+ <source_style>, empty unless the source states a style)
  5 LOCATIONS    <locations>
  6 OBJECTS      <important_objects>
  7 MACRO EVENTS <E1>..<E5>, built from the accepted plan and references
  8 CONTINUITY   <continuity_notes>, summarised from the macro events that were really written
The result is the unchanged A1 dict (seven fields), so A2 consumes it as before.

History: Grok receives the real message list (system, user, assistant, ...). Local (ComfyUI LLMSessionChatNode), _SESSION_MODE = True (default): ONE real session. The
system prompt goes once, with the first request (which carries the source); every later request is only the new message and the node keeps the history. The workflow COPY of
each call forces n_ctx, a large max_turns and summarize_old_history=False (fill_conversation.call_session), Python counts tokens before every call and opens a NEW session when
the history would not fit, after a retry (a rejected reply must not stay in the history), after an empty reply, or when the node's history file is not what Python expects;
the new session is rebuilt from the accepted answers (the source + the latest accepted answer of every step: _transcript). _SESSION_MODE = False = the old behaviour: a fresh
session per call (reset_session=True) with the stored conversation sent inside every request.
A rejected answer is corrected inside the working conversation; only the accepted request/answer pair is committed to the history.
"""

import os
import re
import time
import uuid

from .constants import lang_prompt_name
from . import llm_engine_a1_static as _S
from . import fill_conversation as _FC
from . import llm_overrides as _ov
from .llm_engine_a1 import GROK_MARKER, _CHAR_FIELDS, _CHAR_TAG_RE, _strip_code_fence

__version__ = "20261102_a1_static_conversation_v1"

MAX_ATTEMPTS = 3                     # first answer + two corrections per step
_SESSION_MODE = os.environ.get("WP_A1_SESSION_MODE", "1") != "0"      # env WP_A1_SESSION_MODE=0 (old-path tests) / Local: one real session (system prompt once, then only the new message); False = a fresh session per call with the history sent inside every request
_EVENTS_PER_STEP = True              # EXPERIMENT: STORY EVENTS is written one call per arc step (step 1 from the source, every later step continues the story with a new pose / expression / mood, a change of place allowed); False = one call for all paragraphs
_MAX_EVENT_STEPS = 12
_PLAN_STAGE = False                 # False: the STORY EVENTS paragraphs ARE the story - no STORY PLAN / PLAN CHECK call; every later step reads the events. True (or _EVENTS_FIRST False) = the plan flow
_EVENTS_FIRST = True                # story EVENTS first (what happens, why, what it changes), then the plan (poses / state) derived from them
_PLAN_CHECK = True                  # one physical-feasibility check of the plan (the model returns it unchanged when it is feasible)
_LONG_SOURCE_CHARS = 5000           # a source longer than this is re-sent in every Local request, so the workflow's n_ctx (12288) is too small
_LONG_SOURCE_N_CTX = 32768


def n_ctx_for(source_chars: int) -> int | None:
    """The n_ctx for every Local request of an A1 conversation. The app's Settings field "n_ctx (0=model)" (llm_engine_tools._llm_n_ctx) is honoured: a value above 0 is used.
    A long source is re-sent in every request, so it gets at least 32768 even when the setting is smaller (a request that does not fit is silently cut by the node, which broke
    a 13,900-character source at SOURCE ANCHORS). None = keep the workflow's own n_ctx."""
    try:
        from . import llm_engine_tools as _T
        user = int(getattr(_T, "_llm_n_ctx", 0) or 0)
    except Exception:
        user = 0
    if source_chars > _LONG_SOURCE_CHARS:
        return max(user, _LONG_SOURCE_N_CTX)
    return user if user > 0 else None


_VN_MODES = ("cinematic",)           # Outline Style "Visual Novel" reaches A1 as mode="cinematic"; "sequential" (and Manga, which runs A1 as sequential) keep the source scene


def _places_rule(vn: bool) -> str:
    """The one place where the Outline Style changes what A1 Static asks for: a Visual Novel may move through new scenes, the other styles stay in the source scene."""
    if vn:
        return ("This is a Visual Novel: the story must MOVE through new scenes. Event 1 is the source scene; from event 2 on the character goes to other places that you invent, suited to the genre "
                "and the story (for example another room, another building, the street, a vehicle). Use at least two new places in events 2-5. A new place is a different room, building, street or vehicle that is NOT part of the source scene (a corner, table or door of the same hall is not a new place), and the story must not stay in the source place. "
                "A change of place happens through an action (the character goes there); clothing and objects stay continuous unless an action changes them. "
                "Start every event with its place in brackets, for example (place: ...).")
    return "Keep every event in the source scene: the same place, no new locations (people and things may move inside it)."


_PLAN_ACTION_AS_MACRO = False        # EXPERIMENT: use the final accepted plan's step_N.action as E1..EN and skip the MACRO EVENTS call (False = the previous flow)
_PLAN_CHALLENGE = False              # one extra request after the first plan (see _req_plan_challenge)
_ANCHOR_REVISIONS = 1                # plan revisions allowed after a reported source conflict
_NONE_VALUES = {"", "none", "n/a", "na", "null", "-", "no", "no conflict", "no conflicts", "ไม่มี"}

_SYSTEM_PROMPT = (
    "You are a professional Visual Novel story writer who turns a static visual source into a coherent sequence of cinematic or illustrated story events. "
    "Treat the source as the base of the story, and follow the user's genre and Static Arc when they are given. "
    "You create the characters from the source: if the source gives a character no name, create a fitting personal name for them. "
    "When you plan a story, every step is a real event: the character does something that changes their pose or the situation (a new position, support, object or clothing state), never only a look, a feeling or a small gesture. "
    "You are in a conversation with the user: answer what they ask, in the form they ask for. When the user asks for tagged output, reply with those tags only."
)

_CHAR_FORMAT = (
    "<char1.name>a real personal given name that fits the setting (invent one if the source gives none), never a role, descriptive word or pronoun</char1.name>\n"
    "<char1.alias>the source's own labels for this person (for example the woman), or empty; never a pronoun</char1.alias>\n"
    "<char1.trigger></char1.trigger>\n<char1.age>...</char1.age>\n<char1.gender>...</char1.gender>\n<char1.nationality>...</char1.nationality>\n"
    "<char1.body>...</char1.body>\n<char1.face>...</char1.face>\n<char1.skin>...</char1.skin>\n<char1.marks>...</char1.marks>\n"
    "<char1.init_hair>...</char1.init_hair>\n<char1.init_cloth>the garments and accessories worn in the source</char1.init_cloth>\n"
    "(the same twelve tags for char2, char3 ... only if the source has more characters)"
)
_PLAN_KEYS = ("action", "end_pose", "state", "leads_to")
_ANCHOR_KEYS = ("initial_pose", "initial_clothing", "held_objects", "appearance_locks", "environment_anchors", "source_facts", "plan_conflicts")


# ---------------------------------------------------------------------------------------------------------------------------------
# structure helpers (no meaning is read)
# ---------------------------------------------------------------------------------------------------------------------------------
def _clean(raw: str | None) -> str:
    return _strip_code_fence(raw or "")


def _outside(text: str, patterns: list) -> str:
    """Whatever is left of the reply after removing the expected tagged blocks (must be whitespace only)."""
    rest = text
    for p in patterns:
        rest = p.sub("", rest)
    return rest.strip()


def _single_blocks(text: str, tags: tuple[str, ...], problems: list[str], optional: tuple[str, ...] = ()) -> dict[str, str]:
    """Each tag exactly once as <tag>...</tag>; text outside the blocks is a fault."""
    out: dict[str, str] = {}
    pats = []
    for t in tags:
        p = re.compile(rf"<{t}>(.*?)</{t}>", re.DOTALL)
        found = p.findall(text)
        pats.append(p)
        if not found:
            if t not in optional:
                problems.append(f"<{t}> is missing or not closed with </{t}>")
        elif len(found) > 1:
            problems.append(f"<{t}> appears {len(found)} times (exactly one is required)")
        else:
            out[t] = found[0].strip()
    left = _outside(text, pats)
    if left:
        problems.append("text outside the required tags: " + repr(left[:80]))
    return out


def _parse_keyed(inner: str, key_re: str) -> dict[str, str]:
    """'key: value' lines (a value may continue on the following lines) -> {key: value}; the first occurrence of a key wins."""
    out: dict[str, str] = {}
    cur = None
    for line in inner.splitlines():
        m = re.match(rf"^\s*({key_re})\s*:\s*(.*)$", line, re.IGNORECASE)
        if m:
            cur = m.group(1).lower()
            if cur not in out:
                out[cur] = m.group(2).strip()
            else:
                cur = None
        elif cur and line.strip():
            out[cur] = (out[cur] + " " + line.strip()).strip()
    return out


def _parse_characters(raw: str | None) -> tuple[dict | None, list[str]]:
    """-> ({'sheet': legacy character_sheet text, 'names': [...], 'count': n}, problems)."""
    text = _clean(raw)
    problems: list[str] = []
    chars: dict[str, dict[str, str]] = {}
    for m in _CHAR_TAG_RE.finditer(text):
        cid, field, value = m.group(1), m.group(2), m.group(3).strip()
        if re.search(r"</?char\d+\.[a-z_]+>", value):
            problems.append(f"<{cid}.{field}> contains another character tag (a tag was not closed correctly)")
            continue
        if field not in _CHAR_FIELDS:
            problems.append(f"<{cid}.{field}> is not a character field")
        elif field in chars.setdefault(cid, {}):
            problems.append(f"duplicate <{cid}.{field}>")
        else:
            chars[cid][field] = value
    left = _outside(text, [_CHAR_TAG_RE])
    if left:
        problems.append("text outside the character tags: " + repr(left[:80]))
    if not chars:
        return None, problems + ["no <charN.*> set found"]
    ids = sorted(chars, key=lambda c: int(c[4:]))
    if [int(c[4:]) for c in ids] != list(range(1, len(ids) + 1)):
        problems.append(f"character sets must be numbered char1..char{len(ids)} without gaps (got {', '.join(ids)})")
    for cid in ids:
        miss = [f for f in _CHAR_FIELDS if f not in chars[cid]]
        if miss:
            problems.append(f"{cid} lacks the tag(s) {', '.join(miss)}")
        if not chars[cid].get("name", "").strip():
            problems.append(f"{cid}.name is empty")
    if problems:
        return None, problems
    lines: list[str] = []
    for cid in ids:
        for field in _CHAR_FIELDS:
            lines.append(f"<{cid}.{field}>{'' if field == 'trigger' else chars[cid].get(field, '')}</{cid}.{field}>")
        lines.append("")
    return {"sheet": "\n".join(lines).strip(), "names": [chars[c]["name"].strip() for c in ids], "count": len(ids)}, []


def _parse_plan(raw: str | None, arc: dict, names: list[str]) -> tuple[dict | None, list[str]]:
    text = _clean(raw)
    problems: list[str] = []
    blk = _single_blocks(text, ("story_plan",), problems)
    if "story_plan" not in blk:
        return None, problems
    n = len(arc["steps"])
    key_re = r"selected_arc|cast|" + "|".join(rf"step_{i}\.(?:{'|'.join(_PLAN_KEYS)})" for i in range(1, n + 1))
    plan = _parse_keyed(blk["story_plan"], key_re)
    if plan.get("selected_arc", "").strip() != arc["id"]:
        problems.append(f"selected_arc must be {arc['id']!r}")
    cast = [c.split("=")[0].strip() for c in plan.get("cast", "").split(";") if c.split("=")[0].strip()]
    if not cast:
        problems.append("cast is empty")
    extra = [c for c in cast if c not in names]
    if extra:
        problems.append(f"cast names not in the accepted character sheet: {extra} (allowed: {names})")
    for i in range(1, n + 1):
        for k in _PLAN_KEYS:
            if not plan.get(f"step_{i}.{k}", "").strip():
                problems.append(f"step_{i}.{k} is missing or empty")
    return (None, problems) if problems else (plan, [])


def _parse_anchors(raw: str | None) -> tuple[dict | None, list[str]]:
    text = _clean(raw)
    problems: list[str] = []
    blk = _single_blocks(text, ("source_anchors",), problems)
    if "source_anchors" not in blk:
        return None, problems
    anchors = _parse_keyed(blk["source_anchors"], "|".join(_ANCHOR_KEYS))
    for k in _ANCHOR_KEYS:
        if not anchors.get(k, "").strip():
            problems.append(f"{k} is missing or empty")
    return (None, problems) if problems else (anchors, [])


def _parse_events(raw: str | None, n_expected: int) -> tuple[list[dict] | None, list[str]]:
    text = _clean(raw)
    problems: list[str] = []
    pats = [re.compile(r"<E(\d+)>(.*?)</E\1>", re.DOTALL)]
    found = pats[0].findall(text)
    left = _outside(text, pats)
    if left:
        problems.append("text outside the event tags: " + repr(left[:80]))
    events = [{"id": f"E{num}", "content": body.strip()} for num, body in found]
    problems += _S._validate_prose_events(events, "E1", n_expected)
    return (None, problems) if problems else (events, [])


# ---------------------------------------------------------------------------------------------------------------------------------
# the conversation
# ---------------------------------------------------------------------------------------------------------------------------------
_TEMPLATE_END_RE = re.compile(r"(?:\s*</?(?:end_of_turn|eos|start_of_turn)>)+\s*$")      # chat-template end markers a local model may leak after its answer


def _strip_template_tail(raw: str) -> tuple[str, list[str]]:
    """Transport only (like the code-fence strip): a trailing chat-template marker such as </end_of_turn> is not part of the answer. It is removed and REPORTED, never silent."""
    m = _TEMPLATE_END_RE.search(raw or "")
    return ((raw or "")[:m.start()], [f"trailing chat-template marker removed: {m.group(0).strip()}"]) if m else (raw or "", [])


_BLOCK_TAGS = ("story_plan", "story_events", "source_anchors", "world_sheet", "source_style", "locations", "important_objects", "continuity_notes") + tuple(f"event_{i}" for i in range(1, 13))


def _strip_duplicate_closer(raw: str) -> tuple[str, list[str]]:
    """Transport only: the model sometimes writes the closing tag of the requested block twice at the very end ('...</story_plan>\n</story_plan>', seen in real runs, repeated
    identically in every correction). A closing tag at the end that has no opener of its own left is removed and REPORTED; nothing inside the block and no other text is touched."""
    text, notes = raw or "", []
    while True:
        m = re.search(r"\s*</(" + "|".join(_BLOCK_TAGS) + r")>\s*$", text)
        if not m:
            break
        tag = m.group(1)
        if text.count(f"</{tag}>") <= text.count(f"<{tag}>"):
            break                                            # this closer has its opener: it is the real one
        text = text[:m.start()]
        notes.append(f"duplicate closing tag </{tag}> at the end removed")
    return text, notes


_KIND = {"CHARACTERS": "CHARACTERS", "STORY EVENTS": "EVENTS", "REVISED STORY EVENTS": "EVENTS", "PLAN CHECK": "PLAN", "STORY PLAN": "PLAN", "REVISED STORY PLAN": "PLAN", "SOURCE ANCHORS": "ANCHORS", "WORLD SHEET": "WORLD", "LOCATIONS": "LOCATIONS",
         "IMPORTANT OBJECTS": "OBJECTS", "MACRO EVENTS": "MACROS", "CONTINUITY NOTES": "CONTINUITY"}
# Local only: which earlier accepted answers each request needs (the source is always included). Grok receives the full message history instead.
_LOCAL_NEEDS = {"CHARACTERS": [], "STORY EVENTS": ["CHARACTERS"], "PLAN CHECK": ["CHARACTERS", "EVENTS", "PLAN"], "STORY PLAN": ["CHARACTERS", "EVENTS"], "REVISED STORY PLAN": ["CHARACTERS", "PLAN", "ANCHORS"], "SOURCE ANCHORS": ["CHARACTERS", "PLAN"],
                "WORLD SHEET": ["PLAN", "ANCHORS"], "LOCATIONS": ["PLAN", "ANCHORS", "WORLD"], "IMPORTANT OBJECTS": ["PLAN", "ANCHORS", "LOCATIONS"],
                "MACRO EVENTS": ["CHARACTERS", "PLAN", "ANCHORS", "WORLD", "LOCATIONS", "OBJECTS"], "CONTINUITY NOTES": ["CHARACTERS", "PLAN", "ANCHORS", "MACROS"]}


def _kind_of(step: str) -> str | None:
    """Kind of an accepted answer for the Local transcript. Each per-step paragraph ('STORY EVENT k') is its own kind so every paragraph stays visible."""
    if step.startswith("STORY EVENT ") and step[12:].isdigit():
        return f"EVENT{step[12:]}"
    return _KIND.get(step)


def _needs_table() -> dict:
    base = _LOCAL_NEEDS if _use_plan() else _no_plan_needs()
    if not _EVENTS_PER_STEP:
        return base
    per = [f"EVENT{k}" for k in range(1, _MAX_EVENT_STEPS + 1)]
    out = {k: [y for x in v for y in ((per + ["EVENTS"]) if x == "EVENTS" else [x])] for k, v in base.items()}
    for k in range(1, _MAX_EVENT_STEPS + 1):
        out[f"STORY EVENT {k}"] = ["CHARACTERS"] + per[:k - 1]
    return out


def _use_plan() -> bool:
    return _PLAN_STAGE or not _EVENTS_FIRST


def _no_plan_needs() -> dict:
    """The needs table when there is no plan: every 'PLAN' need becomes the story events (the last accepted EVENTS answer, also a revised one)."""
    out = {k: list(dict.fromkeys("EVENTS" if x == "PLAN" else x for x in v)) for k, v in _LOCAL_NEEDS.items()}
    out["REVISED STORY EVENTS"] = ["CHARACTERS", "EVENTS", "ANCHORS"]
    return out


def _short(request: str) -> str:
    """The label of a request kept in later Local transcripts: its TASK line (wording written by Python, no story content)."""
    i = request.find("TASK - ")
    return request[i:].splitlines()[0][:260] if i >= 0 else request[:260]


class _Conversation:
    def __init__(self, *, backend: str, wf_template, session_id: str, grok_api_key: str, grok_model: str, on_log, cap: dict, system: str | None = None, needs: dict | None = None, n_ctx: int | None = None):
        self.n_ctx = n_ctx                                      # Local only: overrides the workflow's n_ctx when the source is long
        self.system = system or _SYSTEM_PROMPT                  # A1 Novel passes its own system prompt and needs table
        self.needs = needs if needs is not None else _needs_table()
        self.backend, self.wf_template, self.session_id = backend, wf_template, session_id
        self.grok_api_key, self.grok_model, self.on_log, self.cap = grok_api_key, grok_model, on_log, cap
        self.source = ""
        self.history: list[dict] = []          # committed pairs: {"role","content","step"[,"short"]}
        self._sess: dict | None = None         # Local session mode: {"id", "tokens" (user + assistant, system excluded), "turns"} = what Python believes the node's history holds
        self._rotate_reason = ""               # set when the NEXT call must open a new session
        self.records: list[dict] = []          # every call (also rejected ones), for debugging
        self._dbg = None                       # conversation_debug.ConversationDebugLog, created on the first call (active only while LLM Debug Logging is ON)

    def _debug_flush(self) -> None:
        """Debug mode: the whole conversation of this run (every call, also rejected attempts) is rewritten into ONE file in the debug folder after each call."""
        from .conversation_debug import ConversationDebugLog
        if self._dbg is None:
            self._dbg = ConversationDebugLog("a1_" + str(self.cap.get("route") or "static"))
        if not self._dbg.enabled:
            return
        self._dbg.write({"meta": {k: self.cap.get(k) for k in ("route", "mode", "outline_style", "backend", "llm_wf_file", "grok_model", "n_ctx", "cast")} | {"arc": (self.cap.get("arc") or {}).get("id")},
                         "system_prompt": self.system, "source": self.source, "calls": self.records})

    def _transcript(self, step: str, working: list[dict], request: str, all_kinds: bool = False) -> str:
        """Local only: the stored conversation written into one user message (the node's own session memory is not relied upon).
        The workflow's context (n_ctx) is small, so a request carries the SOURCE plus the accepted ANSWERS the step depends on (_LOCAL_NEEDS), each with the short
        label of the request it answered; a rejected attempt in this step is carried in full. Nothing is summarised or rewritten."""
        needed = list(_KIND.values()) if all_kinds else self.needs.get(step, list(_KIND.values()))      # all_kinds: the rebuild of a new session carries every accepted step
        pairs = [(self.history[i], self.history[i + 1]) for i in range(0, len(self.history), 2)]
        pick: dict[str, int] = {}
        for idx, (u, a) in enumerate(pairs):
            if _kind_of(u["step"]) in needed:
                pick[_kind_of(u["step"])] = idx                          # the LAST accepted answer of that kind (a revised plan replaces the first plan)
        if "EVENTS" in pick:                                          # a revised block of story events replaces the per-step paragraphs
            for k in [k for k in pick if re.fullmatch(r"EVENT\d+", k)]:
                del pick[k]
        parts = []
        if step != "CHARACTERS":                                      # the first request already contains the source
            parts.append("SOURCE (authoritative):\n" + self.source)
        if pick:
            parts.append("CONVERSATION SO FAR (the earlier steps this request depends on; you are the ASSISTANT in it; do not repeat it):")
        for idx in sorted(pick.values()):
            u, a = pairs[idx]
            parts.append(f"[USER REQUEST - {u['step']}]\n{u['short']}\n\n[ASSISTANT REPLY - {a['step']}]\n{a['content']}")
        for m in working:
            parts.append(("[USER REQUEST - %s]\n" if m["role"] == "user" else "[ASSISTANT REPLY - %s]\n") % m["step"] + m["content"])
        parts.append("=== NEW USER REQUEST - answer ONLY this request, in the requested format ===\n" + request)
        return "\n\n".join(parts)

    async def _send(self, step: str, request: str, working: list[dict], tokens: int) -> str:
        if self.backend == "grok":
            hist = [{"role": m["role"], "content": m["content"]} for m in self.history + working]
            raw = await _S._call_grok_text(self.system, request, api_key=self.grok_api_key, model=self.grok_model, max_tokens=tokens, on_log=self.on_log, history=hist or None)
            return raw or "", len(hist) + 1
        if _SESSION_MODE:
            return await self._send_session(step, request, working, tokens)
        sent = request if (step == "CHARACTERS" and not working) else self._transcript(step, working, request)
        wf = self.wf_template or {}
        if self.n_ctx:                                           # a copy with the larger context; the shared workflow template is never modified
            import copy
            wf = copy.deepcopy(wf)
            for node in wf.values():
                if isinstance(node, dict) and node.get("class_type") == "LLMSessionChatNode":
                    _ov.apply(node.setdefault("inputs", {}), n_ctx=int(self.n_ctx))      # Workflow mode keeps the workflow's n_ctx
        raw = await _S._call_local_text(wf, self.system, sent, max_tokens=tokens, session_id=self.session_id, on_log=self.on_log, reset=True)
        return raw or "", len(sent)

    def _need_tokens(self, new_text: str, reserve: int) -> int:
        """tokens(system + the session's history + the new message) + overhead + the output reserve + the safety margin; ESTIMATED (over-counts, the safe side)."""
        return (_FC.BASE_OVERHEAD + _FC.estimate_tokens(self.system) + (self._sess["tokens"] if self._sess else 0) + _FC.estimate_tokens(new_text) + _FC.PER_MESSAGE_OVERHEAD
                + int(reserve) + _FC.NODE_MARGIN + _FC.SAFETY_MARGIN_ESTIMATED)

    async def _send_session(self, step: str, request: str, working: list[dict], tokens: int) -> tuple[str, int]:
        """Local, one real session. Same session: only the new message goes (a retry sends just the correction: the node's history holds the rejected reply). A new session:
        the system prompt and the rebuilt conversation go once, reset_session=True."""
        n_ctx = _ov.effective_n_ctx(self.wf_template, max(int(self.n_ctx or 0), _FC.FILL_N_CTX))      # what the budget is planned against
        budget = min(max(_FC.BUDGET_CTX, n_ctx // 2), n_ctx)
        reason, self._rotate_reason = self._rotate_reason, ""
        if self._sess is None:
            reason = reason or "first request"
        elif not reason and self._need_tokens(request, tokens) > budget:
            reason = f"context budget: {self._need_tokens(request, tokens)} tokens needed > {budget}"
        sent, reset = request, False
        if reason:
            reset = True
            fresh = not self.history and not working and step == "CHARACTERS"            # the very first request already contains the source
            sent = request if fresh else self._transcript(step, working, request, all_kinds=True)
            self._sess = {"id": uuid.uuid4().hex[:8], "tokens": 0, "turns": 0}
            if self._need_tokens(sent, tokens) > budget and not fresh:
                lighter = self._transcript(step, working, request)                      # the steps this request depends on only (the old table)
                if self._need_tokens(lighter, tokens) < self._need_tokens(sent, tokens):
                    sent = lighter
            if self.on_log:
                await self.on_log(f"ℹ Story Bible (static conversation): new session {self._sess['id']} for {step} ({reason}); the system prompt is sent once"
                                  + ("" if fresh else f", with {len([m for m in self.history if m['role'] == 'user'])} accepted step(s) rebuilt"))
                await self.on_log(f"📋 Story Bible SYSTEM:\n{self.system}")
        raw = await _FC.call_session(self.wf_template or {}, self.system, sent, session_id=self._sess["id"], reset=reset, max_tokens=tokens, n_ctx=n_ctx, on_log=self.on_log)
        raw = raw or ""
        if not raw.strip():
            self._rotate_reason = "the call returned no text (the session history is unknown)"
            return "", len(sent)
        self._sess["tokens"] += _FC.estimate_tokens(sent) + _FC.estimate_tokens(raw) + 2 * _FC.PER_MESSAGE_OVERHEAD
        self._sess["turns"] += 1
        h = _FC.read_history(self.wf_template or {}, self._sess["id"])
        if h is not None and len(h.get("turns") or []) != self._sess["turns"]:
            self._rotate_reason = f"history file has {len(h.get('turns') or [])} turns, Python expected {self._sess['turns']}"
            if self.on_log:
                await self.on_log(f"⚠ Story Bible (static conversation): {self._rotate_reason}; the next request opens a new session")
        return raw, len(sent)

    def _publish_turns(self, step: str, working: list[dict], cur_request: str, raw: str, attempt: int, ok: bool) -> None:
        """cap["turns"] = the accepted pairs, then this step's rejected attempts (labelled) and the attempt just made."""
        turns = [{"stage": m["step"], "system_prompt": self.system, "user_prompt": u["content"], "raw_response": m["content"]}
                 for u, m in zip(self.history[0::2], self.history[1::2])]
        for i, (u, m) in enumerate(zip(working[0::2], working[1::2]), 1):
            turns.append({"stage": f"{step} - attempt {i} rejected", "system_prompt": self.system, "user_prompt": u["content"], "raw_response": m["content"]})
        turns.append({"stage": step if ok else f"{step} - attempt {attempt} rejected", "system_prompt": self.system, "user_prompt": cur_request, "raw_response": raw})
        self.cap["turns"] = turns

    async def ask(self, step: str, request: str, validate, *, tokens: int):
        """One user request. Returns (parsed result or None, problems of the last attempt). Rejected answers are corrected in a working copy of the
        conversation; only the accepted request/answer pair is committed to the history."""
        working: list[dict] = []
        cur_request = request
        parsed, problems = None, ["no reply"]
        for attempt in range(1, MAX_ATTEMPTS + 1):
            t0 = time.time()
            raw, ctx = await self._send(step, cur_request, working, tokens)
            elapsed = round(time.time() - t0, 1)
            raw_original = raw
            raw, transport_notes = _strip_template_tail(raw)
            raw, dup_notes = _strip_duplicate_closer(raw)
            transport_notes = transport_notes + dup_notes
            parsed, problems = validate(raw) if raw.strip() else (None, ["the reply was empty"])
            rec = {"step": step, "attempt": attempt, "backend": self.backend, "model": self.grok_model if self.backend == "grok" else self.cap.get("llm_wf_file"),
                   "arc": (self.cap.get("arc") or {}).get("id"), "session": ("grok messages" if self.backend == "grok" else (f"{self._sess['id']} (one session; system prompt sent once)" if _SESSION_MODE and self._sess else f"{self.session_id} (fresh each call; history sent by Python)")),
                   "system_prompt": self.system, "user_prompt": cur_request, "raw_response": raw_original, "transport_notes": transport_notes, "parsed": parsed, "validation": problems, "ok": not problems,
                   "retry_count": attempt - 1, "elapsed_s": elapsed, "context_size": ctx, "history_messages": len(self.history) + len(working)}
            self.records.append(rec)
            self.cap["conversation"] = self.records
            self._publish_turns(step, working, cur_request, raw, attempt, not problems)       # the chat tab follows every attempt live
            self._debug_flush()
            if self.on_log:
                await self.on_log(f"{'ℹ' if not problems else '⚠'} Story Bible (static conversation) {step} attempt {attempt}: "
                                  + ("accepted" if not problems else "; ".join(problems[:4])) + f" ({elapsed}s)" + (" [" + "; ".join(transport_notes) + "]" if transport_notes else ""))
            if not problems:
                if attempt > 1 and _SESSION_MODE and self.backend != "grok":
                    self._rotate_reason = "a rejected reply is in the session history"     # the next request opens a clean session rebuilt from the accepted answers
                self.history += [{"role": "user", "content": request, "step": step, "short": _short(request)}, {"role": "assistant", "content": raw, "step": step}]
                self.cap["turns"] = [{"stage": m["step"], "system_prompt": self.system, "user_prompt": u["content"], "raw_response": m["content"]}
                                     for u, m in zip(self.history[0::2], self.history[1::2])]
                return parsed, []
            working += [{"role": "user", "content": cur_request, "step": step}, {"role": "assistant", "content": raw, "step": step}]
            cur_request = (f"Your previous reply could not be accepted: {'; '.join(problems[:6])}. "
                           f"Reply again with the complete {step} result only, in the required format. {_format_reminder(step)}")
        self.cap["turns"] = [{"stage": m["step"], "system_prompt": self.system, "user_prompt": u["content"], "raw_response": m["content"]}
                             for u, m in zip((self.history + working)[0::2], (self.history + working)[1::2])]
        return None, problems


def _format_reminder(step: str) -> str:
    if step.startswith("STORY EVENT ") and step[12:].isdigit():
        return f"One <event_{step[12:]}>...</event_{step[12:]}> tag only."
    return _FORMAT_REMINDER[step]


_FORMAT_REMINDER = {
    "STORY EVENTS": "One <story_events>...</story_events> block only.",
    "REVISED STORY EVENTS": "One <story_events>...</story_events> block only.",
    "PLAN CHECK": "The single word PASS, or one <story_plan>...</story_plan> block only.",
    "CHARACTERS": "Character tags only.",
    "STORY PLAN": "One <story_plan>...</story_plan> block only.",
    "SOURCE ANCHORS": "One <source_anchors>...</source_anchors> block only.",
    "REVISED STORY PLAN": "One <story_plan>...</story_plan> block only.",
    "WORLD SHEET": "<world_sheet> and <source_style> only.",
    "LOCATIONS": "One <locations>...</locations> block only.",
    "IMPORTANT OBJECTS": "One <important_objects>...</important_objects> block only.",
    "MACRO EVENTS": "Exactly the five tags <E1>...</E1> to <E5>...</E5>, nothing else.",
    "CONTINUITY NOTES": "One <continuity_notes>...</continuity_notes> block only.",
}


# ---------------------------------------------------------------------------------------------------------------------------------
# the user requests (Python sends them; wording only, no story content)
# ---------------------------------------------------------------------------------------------------------------------------------
def _lang(desc_lang: str) -> str:
    return f"Write all text inside the tags in {lang_prompt_name(desc_lang or 'en')}; tag names and keys stay English."


def _solo_line(n: int) -> str:
    return ("There is exactly one character: this is a SOLO story. The viewpoint is a way of telling the story, not another person: do not add a partner, observer, voice, "
            "hand or touch of anyone who does not exist; every action is done by the character alone, to their own body, clothing, objects or surroundings.\n") if n == 1 else ""


_CHAR_HINTS = {
    "name": "one real personal name", "alias": "", "trigger": "", "age": "a number", "gender": "gender", "nationality": "a nationality you choose",
    "body": "build", "face": "face", "skin": "skin tone", "marks": "tattoos / scars, or none", "init_hair": "hairstyle and colour", "init_cloth": "the full outfit worn at the start: top, bottom, footwear, accessories",
}


def _req_characters(story: str, *, desc_lang: str, genre: str, requirement: str) -> str:
    skeleton = "\n".join(f"<char1.{f}>{_CHAR_HINTS.get(f, '')}</char1.{f}>" for f in _CHAR_FIELDS)
    return ("\n".join(x for x in (
        _lang(desc_lang),
        f"Genre: {genre}" if genre else "",
        f"Special requirements: {requirement.strip()}" if (requirement or "").strip() else "",
        "SOURCE (one static moment; authoritative):\n" + story,
        "",
        "TASK - CHARACTER SHEET: from the source make a character sheet for every character who appears: skip no one and add no one. "
        "You must give each character a real personal name of your own choice if the source has none; never write unspecified or unknown. "
        "init_cloth is the complete outfit the person wears at the start: top, bottom, footwear and any accessory, naming the garments themselves; how a garment is worn or arranged "
        "(pulled up, open, slipping) is not part of it. If the source shows only part of the outfit or says nothing, choose concrete, plausible garments for the rest; never write that it is not specified or assumed. "
        "Every field needs a value, at most 20 words each. Reply with tags only, one set per character: if the source has one character make char1 only; "
        "if it has two make char1 and char2; if more, continue char3, char4. Each set has these 12 tags (leave trigger empty):",
        skeleton,
        "(the next set, char2, has the same 12 tags, only if a second character appears in the source)",
    ) if x != ""))


def _req_plan(arc: dict, names: list[str], n_chars: int, *, desc_lang: str) -> str:
    n = len(arc["steps"])
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(arc["steps"], 1))
    hints = {"action": "who does what, to whom or what", "end_pose": "the main pose at the end of this step", "state": "what is held / worn / where objects are", "leads_to": "what this changes next"}
    keys = "\n".join(f"step_{i}.{k}: {h}" for i in range(1, n + 1) for k, h in hints.items())
    solo = " There is only one character: show a solo action and do not create anyone else." if n_chars == 1 else ""
    return ("\n".join(x for x in (
        _lang(desc_lang),
        f"TASK - STORY PLAN: write the story from the source in {n} steps, following this Static Arc.",
        "SELECTED STATIC ARC\nGenre: %s\nGenre direction: %s\nArc ID: %s\nArc Name: %s\n\nSteps:\n%s" % (arc["genre"], arc.get("goal", ""), arc["id"], arc["name"], steps),
        ("Static Arc rules: " + arc["rules"]) if arc.get("rules") else "",
        f"Use only these characters: {'; '.join(names)}.{solo}",
        "Reply with the story_plan block only:\n<story_plan>\nselected_arc: %s\ncast: the names used, separated by semicolons\n%s\n</story_plan>" % (arc["id"], keys),
    ) if x != ""))


def _req_events(arc: dict, names: list[str], n_chars: int, *, desc_lang: str, vn: bool = False) -> str:
    n = len(arc["steps"])
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(arc["steps"], 1))
    solo = " There is only one character: show a solo story and do not create anyone else." if n_chars == 1 else ""
    keys = "\n".join(f"<event_{i}>...</event_{i}>" for i in range(1, n + 1))
    return ("\n".join(x for x in (
        _lang(desc_lang),
        f"TASK - STORY EVENTS: write {n} paragraphs - one paragraph for each step of this Static Arc, in order. The focus is the body: each paragraph shows the character in a new pose and the action that gets there.",
        "SELECTED STATIC ARC\nGenre: %s\nGenre direction: %s\nArc ID: %s\nArc Name: %s\n\nSteps:\n%s" % (arc["genre"], arc.get("goal", ""), arc["id"], arc["name"], steps),
        ("Static Arc rules: " + arc["rules"]) if arc.get("rules") else "",
        "Paragraph 1 is the source scene exactly (characters, place, pose). In each later paragraph the pose, the support and the body position are clearly different from every earlier paragraph, "
        "and the facial expression and the mood differ too, following the genre direction and its step. The place and the environment stay the same; only small things change, such as wind, dust, light or "
        "a small object moving. Do not tell a new story: no new events beyond what the pose change needs. "
        f"Use only these characters: {'; '.join(names)}.{solo}",
        _places_rule(vn),
        "Reply with this block only, each paragraph inside its own event tag:\n<story_events>\nselected_arc: %s\n%s\n</story_events>" % (arc["id"], keys),
    ) if x != ""))


def _parse_event_step(raw: str | None, k: int) -> tuple[str | None, list[str]]:
    """One paragraph, exactly one <event_k>...</event_k> tag and nothing else."""
    problems: list[str] = []
    blk = _single_blocks(_clean(raw), (f"event_{k}",), problems)
    text = " ".join(blk.get(f"event_{k}", "").split())
    if f"event_{k}" in blk and not text:
        problems.append(f"<event_{k}> is empty")
    return (None, problems) if problems else (text, [])


def _req_event_step(arc: dict, k: int, names: list[str], n_chars: int, *, desc_lang: str) -> str:
    n = len(arc["steps"])
    solo = " There is only one character: show a solo story and do not create anyone else." if n_chars == 1 else ""
    head = "Genre: %s\nGenre direction: %s\nArc: %s (%s)\nStep %d of %d: %s" % (arc["genre"], arc.get("goal", ""), arc["name"], arc["id"], k, n, arc["steps"][k - 1])
    rules = ("Static Arc rules: " + arc["rules"]) if arc.get("rules") else ""
    if k == 1:
        ask = ("TASK - STORY EVENT 1: write paragraph 1 of the story. It is the source scene: use the characters, the environment and the pose that the source gives, as this Static Arc step says.")
    else:
        ask = (f"TASK - STORY EVENT {k}: from the story you have written so far, add paragraph {k}, following this Static Arc step. It carries the story forward from where the previous paragraph ended "
               "(what happens, why, what it changes). The character takes a new pose that is clearly different from the poses in every earlier paragraph, and the facial expression and the mood are also "
               "different from the earlier ones. A change of place is allowed. The paragraph may hold as many events as the story needs." + (" It pays off what the earlier paragraphs set up." if k == n else ""))
    return "\n".join(x for x in (_lang(desc_lang), ask, head, rules, f"Use only these characters: {'; '.join(names)}.{solo}",
                                 "Reply with this tag only:\n<event_%d>...</event_%d>" % (k, k)) if x)


def _parse_events_block(raw: str | None, arc: dict) -> tuple[dict | None, list[str]]:
    text = _clean(raw)
    problems: list[str] = []
    blk = _single_blocks(text, ("story_events",), problems)
    if "story_events" not in blk:
        return None, problems
    n = len(arc["steps"])
    inner = blk["story_events"]
    if re.search(r"<event_1>", inner, re.IGNORECASE):          # one tag per paragraph: <event_N>paragraph</event_N> (the model closes a long paragraph itself instead of closing the whole block)
        ev = {f"event_{i}": " ".join(m.group(1).split()) for i in range(1, n + 1) for m in [re.search(rf"<event_{i}>(.*?)</event_{i}>", inner, re.IGNORECASE | re.DOTALL)] if m}
        sel = re.search(r"selected_arc\s*:\s*(\S+)", inner)
        ev["selected_arc"] = sel.group(1) if sel else ""
    else:
        ev = _parse_keyed(inner, "selected_arc|" + "|".join(f"event_{i}" for i in range(1, n + 1)))
    if ev.get("selected_arc", "").strip():                     # the arc is chosen by Python: a line that is MISSING is filled with it, a line that names ANOTHER arc is refused
        if ev["selected_arc"].strip() != arc["id"]:
            problems.append(f"selected_arc must be {arc['id']!r}")
    else:
        ev["selected_arc"] = arc["id"]
    for i in range(1, n + 1):
        if not ev.get(f"event_{i}", "").strip():
            problems.append(f"event_{i} is missing or empty")
    return (None, problems) if problems else (ev, [])


def _req_plan_from_events(arc: dict, names: list[str], *, desc_lang: str, vn: bool = False) -> str:
    n = len(arc["steps"])
    hints = {"action": "the event, copied from your events", "end_pose": "the main body pose at the end of this event, as a result of it",
             "state": "where the character is, what is held / worn / where objects are" if vn else "what is held / worn / where objects are", "leads_to": "what this changes next"}
    keys = "\n".join(f"step_{i}.{k}: {h}" for i in range(1, n + 1) for k, h in hints.items())
    return (f"{_lang(desc_lang)}\nTASK - STORY PLAN: now write the story plan from your events. Keep each event as step_N.action. For each one give the body pose it ends in (it follows from the event, "
            f"and the character must be able to get there from the previous pose), what is held, worn and where objects are, and what it changes next. Reply with the story_plan block only:\n"
            f"<story_plan>\nselected_arc: {arc['id']}\ncast: {'; '.join(names)}\n{keys}\n</story_plan>")


def _req_plan_check(arc: dict, names: list[str], *, desc_lang: str) -> str:
    return (f"{_lang(desc_lang)}\nTASK - PLAN CHECK: check the story plan. For every step, can the character physically get from where the previous step ended to this step's pose by the action described, "
            f"with held objects, clothing and body support staying continuous? If every step is possible, reply with the single word PASS and nothing else. Otherwise rewrite only the impossible steps so that they are possible "
            f"and keep their events as close as you can, and reply with the complete story_plan block (selected_arc {arc['id']}, the same {len(arc['steps'])} steps and keys).")


def _parse_plan_check(raw: str | None, arc: dict, names: list[str], plan: dict) -> tuple[dict | None, list[str]]:
    """PASS (the single word) keeps the plan as it is; anything else must be a complete story_plan block, parsed as before."""
    if re.fullmatch(r"\W*pass\W*", _clean(raw), re.I):
        return plan, []
    return _parse_plan(raw, arc, names)


def _req_plan_challenge(arc: dict, names: list[str], *, desc_lang: str) -> str:
    """Sent after the first plan is accepted. Tested: the model keeps the character in place when asked up front, but writes real events when told that this is not
    what the user wants. The wording states the user's wish; Python does not judge the plan."""
    return (f"{_lang(desc_lang)}\nTASK - STORY PLAN REVIEW: this is not what we want. In most steps the character stays where they were and only moves an arm, a leg or the body. "
            f"We want every step from step 2 to be an important event that really changes the situation: leaving the current spot, moving to another place, using or changing something in the scene, "
            f"or taking a new body position with a clear movement into it. Keep it physically possible, keep the source facts and use only {'; '.join(names)}. "
            f"Write the complete story_plan again in the same format (selected_arc {arc['id']}, the same {len(arc['steps'])} steps and keys).")


def _req_anchors(*, desc_lang: str, vn: bool = False) -> str:
    sp, p = ("story plan", "plan") if _use_plan() else ("story events", "story events")
    return (f"{_lang(desc_lang)}\nTASK - SOURCE ANCHORS: from the source and the {sp}, list the facts that must stay true. The source is the authority. "
            f"Check the {p} against the source; do not change the {p} in this reply. If the {p} contradicts the source anywhere, describe the contradiction in plan_conflicts, otherwise write none."
            + (" New places and scenes after the first event are allowed in this Visual Novel and are not a conflict; the facts of the source scene itself must not be contradicted." if vn else "") + "\n"
            "Output format - one block, nothing else:\n<source_anchors>\ninitial_pose: the starting pose, exactly as the source gives it\ninitial_clothing: ...\n"
            "held_objects: what is held and by whom, or none\nappearance_locks: appearance facts that never change\nenvironment_anchors: place, atmosphere, light that stay true\n"
            "source_facts: other canonical facts of the source\nplan_conflicts: none, or the contradictions between the plan (or story events) and the source\n</source_anchors>")


def _req_revise_events(conflict: str, arc: dict, names: list[str], *, desc_lang: str) -> str:
    keys = "\n".join(f"<event_{i}>...</event_{i}>" for i in range(1, len(arc["steps"]) + 1))
    return (f"{_lang(desc_lang)}\nThe conflict you reported between the story events and the source: {conflict}\n"
            f"TASK - REVISED STORY EVENTS: revise ONLY the story events so that they keep every source fact; write the complete corrected set of the {len(arc['steps'])} paragraphs "
            f"(only the characters {'; '.join(names)}).\n"
            "Reply with this block only, each paragraph inside its own event tag:\n<story_events>\nselected_arc: %s\n%s\n</story_events>" % (arc["id"], keys))


def _req_revise_plan(conflict: str, arc: dict, names: list[str], n_chars: int, *, desc_lang: str) -> str:
    return (f"{_lang(desc_lang)}\nThe conflict you reported between the plan and the source: {conflict}\n"
            f"TASK - REVISED STORY PLAN: revise ONLY the story plan so that it keeps every source fact, and answer with the complete corrected plan in the same format as before "
            f"(selected_arc {arc['id']}, the same {len(arc['steps'])} steps, only the characters {'; '.join(names)}, the same keys).")


def _req_world(*, desc_lang: str, vn: bool = False) -> str:
    return (f"{_lang(desc_lang)}\nTASK - WORLD SHEET: from the source and the accepted {'plan' if _use_plan() else 'story events'}, write the persistent environment facts: setting, atmosphere, lighting and spatial limits"
            + (f" of the source scene and of every new place the {'plan' if _use_plan() else 'story'} visits. " if vn else ". ") +
            "Do not add rules or facts that contradict the source.\nOutput format:\n<world_sheet>...</world_sheet>\n"
            "<source_style>(English) one paragraph of image-style descriptors ONLY if the source states or is itself an overall visual style, otherwise empty</source_style>")


def _req_locations(*, desc_lang: str, vn: bool = False) -> str:
    return (f"{_lang(desc_lang)}\nTASK - LOCATIONS: describe each place used in the {'plan' if _use_plan() else 'story events'}" + (" (the source place and every new place)" if vn else "") +
            " as fixed reference details only: what the place is, its layout, its fixed features and surfaces, and how the places lie relative to each other (near, far, beside, inside). "
            "Describe the places, not the story: do not say what any character does, where a character stands or moves, or the order of the steps. Do not change any event.\n"
            "Output format - plain text inside the tag, no sub-tags:\n<locations>...</locations>")


def _req_objects(*, desc_lang: str) -> str:
    return (f"{_lang(desc_lang)}\nTASK - IMPORTANT OBJECTS: list only the external objects that matter to the story actions: what it is, who holds or owns it, where it is and its state "
            f"(held, put down, moved, used, damaged) across the {'plan' if _use_plan() else 'story events'}. No body parts, no worn clothing, no objects added only to fill the list; an empty tag if there is none.\n"
            "Output format:\n<important_objects>...</important_objects>")


def _req_macros(n_chars: int, n_events: int, *, desc_lang: str, vn: bool = False) -> str:
    if _use_plan():
        what = ("the story plan you made", "Keep the plan's order of actions, its cast, its end poses and every state transition; do not change the plan on your own. ")
    else:
        what = ("the story events you wrote", "Keep the order of the events, their cast, their places and every state change (clothing, held objects, contact); do not change the story on your own. ")
    return (f"{_lang(desc_lang)}\nTASK - MACRO EVENTS: now write the macro events E1-E{n_events} from " + what[0] + ", using the character sheet, source anchors, world sheet, locations and "
            "important objects we agreed. " + what[1] +
            "E1 keeps the source's starting pose exactly. Write each event as connected prose with real actions (not only feelings or gaze), name who does what instead of using pronouns, "
            "and do not add characters. " + ("State where each event takes place when the place changes, and keep the places already given.\n" if vn else "Keep every event in the source scene.\n") + _solo_line(n_chars) +
            "Output format - exactly %d tags, nothing else:\n<E1>connected prose of step 1</E1>\n<E2>connected prose of step 2, starting from where E1 ended</E2>\n(... up to <E%d>) with each tag closed by its own </E#>." % (n_events, n_events))


_CONTINUITY_ASK = ("list only the states that must stay the same from one event to the next, and where they change: clothing (including damage), objects held or put down and who has them, "
                   "where each person stands relative to the others and to fixed features, and any lasting physical state. Do not retell the events or the pose progression, "
                   "do not describe poses event by event, and do not invent new events.\n")


def _req_continuity(*, desc_lang: str, events: list[dict] | None = None) -> str:
    if events:        # the macro events were not written by a separate call (plan actions used directly): they are given here, as data inside the request
        listing = "\n".join(f"{e['id']}: {e['content']}" for e in events)
        return (f"{_lang(desc_lang)}\nTASK - CONTINUITY NOTES: these are the macro events (the step actions of the accepted story plan):\n{listing}\n"
                "From these events, " + _CONTINUITY_ASK + "Output format:\n<continuity_notes>...</continuity_notes>")
    return (f"{_lang(desc_lang)}\nTASK - CONTINUITY NOTES: from the macro events you actually wrote, " + _CONTINUITY_ASK + "Output format:\n<continuity_notes>...</continuity_notes>")


def _events_from_plan(plan: dict, n: int) -> tuple[list[dict] | None, list[str]]:
    """Direct mapping E<i> = step_<i>.action of the FINAL accepted plan, copied as written (stripped only). An empty action is an error: nothing is written in its place."""
    events, bad = [], []
    for i in range(1, n + 1):
        text = str((plan or {}).get(f"step_{i}.action") or "").strip()
        if not text:
            bad.append(f"step_{i}.action is empty")
        events.append({"id": f"E{i}", "content": text})
    return (None, bad) if bad else (events, [])


# ---------------------------------------------------------------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------------------------------------------------------------
async def run_a1_static_conversation(story_text, llm_wf_file, on_err, on_log=None, *, genre="", genre2="", requirement="", desc_lang="en",
                                     grok_api_key="", grok_model="grok-4-latest", chat_capture=None, wf_template=None, mode="cinematic"):
    is_grok = llm_wf_file == GROK_MARKER
    vn = mode in _VN_MODES                  # Outline Style: Visual Novel may invent new scenes; Sequential and Manga keep the source scene
    arc = _S._select_arc(genre, genre2)                                      # ONCE per run
    cap = chat_capture if chat_capture is not None else {}
    session_id = uuid.uuid4().hex[:8]
    cap.update({"route": "static", "mode": "conversation", "outline_style": "visual_novel" if vn else "sequential_or_manga", "new_scenes_allowed": vn, "arc": arc, "session_id": session_id, "backend": "grok" if is_grok else "local",
                "llm_wf_file": llm_wf_file, "grok_model": grok_model, "conversation": [], "turns": []})
    conv = _Conversation(backend=cap["backend"], wf_template=wf_template, session_id=session_id, grok_api_key=grok_api_key, grok_model=grok_model, on_log=on_log, cap=cap,
                         n_ctx=None if is_grok else n_ctx_for(len(story_text or "")))
    cap["n_ctx"] = conv.n_ctx or "workflow default"
    conv.source = story_text
    if on_log:
        await on_log("✨ Story Bible (static, conversation) → " + ("Grok" if is_grok else f"Comfy ({llm_wf_file})"))
        await on_log(f"ℹ Story Bible (static): genre={arc['genre']} ({arc['genre_source']}), arc={arc['id']} \"{arc['name']}\" [{arc.get('arc_source')}]")

    async def fail(step: str, problems: list[str]):
        await on_err(f"Story Bible (static): step {step} was not accepted after {MAX_ATTEMPTS} attempts - " + "; ".join(problems[:4])
                     + " (the conversation so far is in the A1 chat tab)")
        return None

    # 1 characters
    chars, pr = await conv.ask("CHARACTERS", _req_characters(story_text, desc_lang=desc_lang, genre=arc["genre"], requirement=requirement), _parse_characters, tokens=3072)
    if chars is None:
        return await fail("CHARACTERS", pr)
    names, n_chars = chars["names"], chars["count"]
    cap["cast"] = names
    # 2 story: events first (paragraphs that carry the story forward); the plan (poses / state) and its check only when _use_plan()
    plan = None
    if _EVENTS_FIRST and _EVENTS_PER_STEP:
        events = {"selected_arc": arc["id"]}
        for k in range(1, len(arc["steps"]) + 1):
            para, pr = await conv.ask(f"STORY EVENT {k}", _req_event_step(arc, k, names, n_chars, desc_lang=desc_lang), lambda r, k=k: _parse_event_step(r, k), tokens=2048)
            if para is None:
                return await fail(f"STORY EVENT {k}", pr)
            events[f"event_{k}"] = para
        cap["story_events"] = events
        cap["events_per_step"] = True
        if _use_plan():
            plan, pr = await conv.ask("STORY PLAN", _req_plan_from_events(arc, names, desc_lang=desc_lang, vn=vn), lambda r: _parse_plan(r, arc, names), tokens=4096)
    elif _EVENTS_FIRST:
        events, pr = await conv.ask("STORY EVENTS", _req_events(arc, names, n_chars, desc_lang=desc_lang, vn=vn), lambda r: _parse_events_block(r, arc), tokens=4096)
        if events is None:
            return await fail("STORY EVENTS", pr)
        cap["story_events"] = events
        if _use_plan():
            plan, pr = await conv.ask("STORY PLAN", _req_plan_from_events(arc, names, desc_lang=desc_lang, vn=vn), lambda r: _parse_plan(r, arc, names), tokens=4096)
    else:
        plan, pr = await conv.ask("STORY PLAN", _req_plan(arc, names, n_chars, desc_lang=desc_lang), lambda r: _parse_plan(r, arc, names), tokens=4096)
    if _use_plan():
        if plan is None:
            return await fail("STORY PLAN", pr)
        if _PLAN_CHECK:
            checked, pr = await conv.ask("PLAN CHECK", _req_plan_check(arc, names, desc_lang=desc_lang), lambda r: _parse_plan_check(r, arc, names, plan), tokens=4096)
            if checked is not None:
                cap["plan_changed_by_check"] = checked != plan
                plan = checked
            elif on_log:
                await on_log("⚠ Story Bible (static): the plan check was not accepted (" + "; ".join(pr[:2]) + "); the plan before the check is kept")
        # 2b the plan is challenged once (a failed answer keeps the first plan)
        if _PLAN_CHALLENGE:
            better, pr = await conv.ask("REVISED STORY PLAN", _req_plan_challenge(arc, names, desc_lang=desc_lang), lambda r: _parse_plan(r, arc, names), tokens=4096)
            if better is not None:
                plan = better
            elif on_log:
                await on_log("⚠ Story Bible (static): the challenged plan was not accepted (" + "; ".join(pr[:2]) + "); the first plan is kept")
    else:
        if on_log:
            await on_log("ℹ Story Bible (static): STORY PLAN and PLAN CHECK are not used - the story events are the story")
    # 3 anchors (+ at most one revision of the plan, or of the story events, when the model reports a conflict with the source)
    anchors = None
    for rev in range(_ANCHOR_REVISIONS + 1):
        anchors, pr = await conv.ask("SOURCE ANCHORS", _req_anchors(desc_lang=desc_lang, vn=vn), _parse_anchors, tokens=2048)
        if anchors is None:
            return await fail("SOURCE ANCHORS", pr)
        conflict = anchors["plan_conflicts"].strip()
        if conflict.lower().strip(" .") in _NONE_VALUES:
            break
        if on_log:
            await on_log(f"⚠ Story Bible (static): the model reports a plan/source conflict: {conflict[:200]}")
        if rev >= _ANCHOR_REVISIONS:
            return await fail("SOURCE ANCHORS", [f"the story still conflicts with the source after {_ANCHOR_REVISIONS} revision(s): {conflict[:200]}"])
        if _use_plan():
            plan, pr = await conv.ask("REVISED STORY PLAN", _req_revise_plan(conflict, arc, names, n_chars, desc_lang=desc_lang), lambda r: _parse_plan(r, arc, names), tokens=4096)
            if plan is None:
                return await fail("REVISED STORY PLAN", pr)
        else:
            revised, pr = await conv.ask("REVISED STORY EVENTS", _req_revise_events(conflict, arc, names, desc_lang=desc_lang), lambda r: _parse_events_block(r, arc), tokens=4096)
            if revised is None:
                return await fail("REVISED STORY EVENTS", pr)
            cap["story_events"] = revised
    cap.update({"story_plan": plan, "source_anchors": anchors})
    # 4 world
    def _world(raw):
        pb: list[str] = []
        b = _single_blocks(_clean(raw), ("world_sheet", "source_style"), pb, optional=("source_style",))
        if "world_sheet" in b and not b["world_sheet"]:
            pb.append("<world_sheet> is empty")
        return (None, pb) if pb else (b, [])
    world, pr = await conv.ask("WORLD SHEET", _req_world(desc_lang=desc_lang, vn=vn), _world, tokens=1536)
    if world is None:
        return await fail("WORLD SHEET", pr)
    # 5 locations
    def _one(tag, allow_empty=False):
        def f(raw):
            pb: list[str] = []
            b = _single_blocks(_clean(raw), (tag,), pb)
            if tag in b and not b[tag] and not allow_empty:
                pb.append(f"<{tag}> is empty")
            return (None, pb) if pb else (b[tag], [])
        return f
    locations, pr = await conv.ask("LOCATIONS", _req_locations(desc_lang=desc_lang, vn=vn), _one("locations"), tokens=2560)
    if locations is None:
        return await fail("LOCATIONS", pr)
    # 6 objects
    objects, pr = await conv.ask("IMPORTANT OBJECTS", _req_objects(desc_lang=desc_lang), _one("important_objects", allow_empty=True), tokens=1024)
    if objects is None:
        return await fail("IMPORTANT OBJECTS", pr)
    # 7 macro events
    n_ev = len(arc["steps"])
    if _PLAN_ACTION_AS_MACRO:
        src_plan = plan if plan is not None else {f"step_{i}.action": cap["story_events"].get(f"event_{i}", "") for i in range(1, n_ev + 1)}
        events, pr = _events_from_plan(src_plan, n_ev)       # the final accepted plan (after the plan check and any anchors revision), or the story events when there is no plan
        if events is None:
            await on_err("Story Bible (static): macro events from the plan cannot be built - " + "; ".join(pr) + " (nothing was written in their place)")
            return None
        if on_log:
            await on_log("ℹ Story Bible (static): MACRO EVENTS call skipped - E1-E%d are the plan's step actions" % n_ev)
    else:
        events, pr = await conv.ask("MACRO EVENTS", _req_macros(n_chars, n_ev, desc_lang=desc_lang, vn=vn), lambda r: _parse_events(r, n_ev), tokens=8192)
        if events is None:
            return await fail("MACRO EVENTS", pr)
    # 8 continuity
    continuity, pr = await conv.ask("CONTINUITY NOTES", _req_continuity(desc_lang=desc_lang, events=events if _PLAN_ACTION_AS_MACRO else None), _one("continuity_notes"), tokens=2048)
    if continuity is None:
        return await fail("CONTINUITY NOTES", pr)

    result = {"macro_events": events, "character_sheet": chars["sheet"], "world_sheet": world.get("world_sheet", ""), "locations": locations,
              "important_objects": objects, "continuity_notes": continuity, "source_style": world.get("source_style", "")}
    _S._merge_event_parts(result)
    dropped = _S._drop_empty_characters(result)
    ev_text = " ".join(e["content"] for e in result["macro_events"])
    missing = [n for n in names if n not in ev_text]
    if missing and on_log:
        await on_log(f"⚠ Story Bible (static): cast name(s) {missing} never appear in the events (the events may use another spelling)")
    cap.update({"static_anchor_event": "E1", "generated_events": [e["id"] for e in result["macro_events"][1:]], "cast_names_missing_in_events": missing,
                "calls": len(conv.records), "dropped_empty_characters": dropped,
                "macro_source": "plan_action" if _PLAN_ACTION_AS_MACRO else "llm", "macro_llm_call_skipped": bool(_PLAN_ACTION_AS_MACRO),
                "action_length": [len(e["content"]) for e in result["macro_events"]], "llm_calls_saved": 1 if _PLAN_ACTION_AS_MACRO else 0,
                "final_events": [dict(e) for e in result["macro_events"]]})
    if on_log:
        await on_log(f"ℹ Story Bible (static conversation): done in {len(conv.records)} call(s); anchor E1 (Entry keeps the source pose)")
    return result
