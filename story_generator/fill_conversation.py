"""
fill_conversation.py - Fill as a multi-call conversation (experimental, behind llm_engine_fill._FILL_CONVERSATION).

Local only. One conversation session on the ComfyUI LLMSessionChatNode:
  SHOT k  system = Fill system prompt + conversation rules + the data sheets
          user   = short request + PARENT STORY EVENT + CURRENT B SHOT, assistant = the Krea2 prompt
The data sheets are not repeated in the user message. A new session starts on the shot itself; there is no ACK round.
The node keeps the history (reset_session=False after call 1). The workflow COPY of each call sets n_ctx=32768, a large max_turns and
summarize_old_history=False, so every earlier turn is sent back to the model; the shared template file is never modified.

Python is the context manager: before EVERY call (INITIALIZE included)
    tokens(system + history + next input) + per-message overhead + output reserve + safety margin <= n_ctx
and when the next call would not fit, a NEW session is opened (INITIALIZE again with the Production Sheet + the last accepted shot(s) as evidence),
the shot index never restarts. After every call the node's history file is read back: a missing turn, a shortened turns_limit_used or a
shrunken max_tokens_used means the history is not what Python believes, and the next call opens a new session. Python never interprets the
prompt; it moves text and counts tokens.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime

from pathlib import Path

from .comfy_api import comfy_queue, comfy_history
from .conversation_debug import ConversationDebugLog

FILL_N_CTX = 32768                 # the n_ctx this mode runs the local model with (workflow copy)
BUDGET_CTX = 16000                 # what Python plans against: a session is rotated before its history + next call would exceed this (a real run hung at ~18k history tokens with 32768)
MAX_TURNS = 200                    # node limit; Python rotates long before (budget) and at TURN_CAP
TURN_CAP = 120                     # shots per session at most (the node's history reload cost grows with turns)
REHYDRATE_PREVIOUS = 2             # accepted shots carried into a new session as evidence (reduced when the budget is tight)
SAFETY_MARGIN_EXACT = 768          # tokens: chat template, tokenizer edge cases, node safety margin (64)
SAFETY_MARGIN_ESTIMATED = 2048     # larger because ESTIMATED counts are only an upper-bound approximation
PER_MESSAGE_OVERHEAD = 12          # tokens added by the chat template around each message
BASE_OVERHEAD = 32                 # bos / generation prompt
NODE_MARGIN = 64                   # LLMSessionChatNode: max_tokens_used = max_tokens - 64
ACK_RESERVE = 64
CALL_TIMEOUT = 300

# Output contract: the section headers Fill must write, in this order. OBJECT LOCK is the only optional one (it is written only when the shot has objects).
_FILL_SECTIONS = ("MAIN IMAGE DESCRIPTION", "FRAME LAYOUT", "CAMERA", "LIGHT", "IDENTITY LOCK", "LOCATION LOCK", "OBJECT LOCK")
_FILL_OPTIONAL = {"OBJECT LOCK"}
_FILL_HEADER_RE = re.compile(r"(?m)^[ \t]*(" + "|".join(re.escape(h) for h in _FILL_SECTIONS) + r")[ \t]*:[ \t]*$")
# Any other line that is shaped like a section header: an upper-case label (letters, digits, space, / & _ -) at the very start of a line followed by a colon,
# e.g. "STYLE: Cinematic Realism". Ordinary prose does not start a line with an all-capital label; text in other scripts has no [A-Z] at all.
_ANY_HEADER_RE = re.compile(r"(?m)^[ \t]*([A-Z][A-Z0-9 /&_-]{1,40}[A-Z0-9])[ \t]*:")


def validate_fill_output(text: str, *, reply_tokens: int = 0, max_tokens: int = 0, objects_nonempty: bool | None = None) -> str:
    """Structure only: '' when the reply is a well-formed Fill prompt, otherwise the reason. Header names are the output contract; the text inside a
    section is never read. Checks: nothing before the first header, every required header exactly once and in order, no empty section, and (when the
    caller measured it) a reply that used up its whole output budget, which means it was cut off. Any other upper-case section header (e.g. "STYLE:") is refused.
    objects_nonempty (the current B shot has objects): True -> OBJECT LOCK is required, False/None -> it stays optional."""
    t = (text or "").strip()
    if not t:
        return "empty reply"
    found = list(_FILL_HEADER_RE.finditer(t))
    if not found:
        return "no section headers (MAIN IMAGE DESCRIPTION ... LOCATION LOCK) found"
    if t[:found[0].start()].strip():
        return "text before the first section header"
    names = [m.group(1) for m in found]
    known = {m.start() for m in found}
    for m in _ANY_HEADER_RE.finditer(t):
        if m.start() not in known and m.group(1).strip() not in _FILL_SECTIONS:
            return f"unknown section header {m.group(1).strip()}:"
    for h in _FILL_SECTIONS:
        if names.count(h) > 1:
            return f"section {h} appears {names.count(h)} times"
    missing = [h for h in _FILL_SECTIONS if (h not in _FILL_OPTIONAL or (objects_nonempty and h == "OBJECT LOCK")) and h not in names]
    if missing:
        return "missing section(s): " + ", ".join(missing)
    if names != [h for h in _FILL_SECTIONS if h in names]:
        return "sections are not in the contract order: " + ", ".join(names)
    for i, m in enumerate(found):
        body = t[m.end():(found[i + 1].start() if i + 1 < len(found) else len(t))]
        if not body.strip():
            return f"section {m.group(1)} is empty"
    if max_tokens and reply_tokens and reply_tokens >= max_tokens - NODE_MARGIN:
        return f"the reply used its whole output budget ({reply_tokens} of {max_tokens} tokens): it was cut off"
    return ""


POSE_RULES_ON = False              # EXPERIMENT (default off): POSE_RULES below are appended to the system prompt of the conversation; False = the system prompt of the previous conversation mode

# Pose fidelity and section roles (compact; the base prompt keeps every rule it had). Appended only when POSE_RULES_ON.
POSE_RULES = """

POSE FIDELITY: copy every body-orientation and support fact of CURRENT B SHOT exactly: prone, supine, on the side, sitting, standing or kneeling; which hand, arm or leg (left or right) does what; what each person's weight rests on; and where two people touch. Never turn one into another (prone and supine, standing and sitting, left and right, front and back), never add a limb side, grip or contact that B does not state and never drop one it states. Every limb does one thing at a time: if B gives one arm two jobs, keep the people and the contact and write one possible arrangement. Earlier answers tell only what persists; an action of another shot never enters this one.
SECTION ROLES: state each fact once, in the block that owns it: MAIN IMAGE DESCRIPTION = action and body pose; FRAME LAYOUT = where people and objects sit in the frame; CAMERA = viewpoint, framing, scale; LIGHT = light; IDENTITY LOCK = stable visual identity, current clothing and the persistent physical conditions still visible in this shot (injury, bruise, stain, mark left by a restraint, clothing damage) - never a temporary pose, action, body orientation or momentary contact, which belong in MAIN IMAGE DESCRIPTION; LOCATION LOCK = the fixed place; OBJECT LOCK = relevant objects, restraints included, with their current placement and state. Never drop a persistent condition while B still shows it, never turn a temporary state into a permanent trait, and all blocks must agree with the current B shot. Do not retell the action in other blocks. Where B gives an exact position, write it (in front of, behind, on top of, under, left or right of whom) instead of vague words such as "close together", "intertwined" or "beside each other"."""


# Pose and contact already stay out of IDENTITY LOCK in the base prompt. With POSE_RULES the remaining sentence is EDITED IN PLACE
# (no second override layer), in the conversation text only. Every old text must be found, otherwise the edit is skipped.
BASE_EDITS = (
    ("each object's identity, relevant appearance, current state, and ownership — never pose, emotion, appearance of people",
     "each object's identity, relevant appearance, current state, and ownership (a rope or cuffs that bind someone: what it is, where on the body or the furniture it is, tied or loose, or lying free) — never pose, emotion, appearance of people"),
)


def base_prompt(F) -> str:
    text = F._FILL_SYSTEM_PROMPT
    if POSE_RULES_ON:
        for old, new in BASE_EDITS:
            text = text.replace(old, new)
    return text


def system_text(F) -> str:
    return base_prompt(F) + CONVERSATION_RULES + (POSE_RULES if POSE_RULES_ON else "")


def attach_sheets(system: str, sheet: str) -> str:
    """Data sheets live in the system prompt, so a shot's user message does not repeat them."""
    sheet = (sheet or "").strip()
    if not sheet:
        return system
    return system + "\n\nDATA SHEETS (fixed for this story; never repeated in the user message):\n" + sheet + trigger_map(sheet)


def trigger_map(sheet: str) -> str:
    """A lookup table built from the sheet's own tags (<charN.name>, <charN.alias>, <charN.trigger>): which written name is which trigger token. The user fills the triggers after
    B has written the shots, so B still says the names; this table only tells the model which token stands for which name. Nothing is replaced in B or in the answer."""
    rows = []
    for m in re.finditer(r"<char(\d+)\.name>(.*?)</char\1\.name>", sheet or "", re.S):
        n = m.group(1)
        trig = re.search(rf"<char{n}\.trigger>(.*?)</char{n}\.trigger>", sheet, re.S)
        alias = re.search(rf"<char{n}\.alias>(.*?)</char{n}\.alias>", sheet, re.S)
        token = trig.group(1).strip() if trig else ""
        if not token:
            continue
        names = [m.group(2).strip()]
        if alias:
            names += [x.strip() for x in re.split(r"[,;]", alias.group(1)) if x.strip() and x.strip().lower() not in ("empty", "none")]
        rows.append(f"- {' / '.join(names)}  =>  {token}")
    if not rows:
        return ""
    return ("\n\nTRIGGER TOKENS (CURRENT B SHOT writes these people by name; you write them by the token, in every block, MAIN IMAGE DESCRIPTION first, as if the token were their name: "
            "'<token> sits', '<token>'s left hand'. Never write the name or alias of a person listed here):\n" + "\n".join(rows))


LAST_RUN_DEBUG: list[dict] = []    # filled by every run (for tests / inspection): one record per LLM call + session events

CONVERSATION_RULES = """

CONVERSATION MODE (this overrides only how the work is delivered, not any rule above):
1. CHARACTERS, WORLD, LOCATIONS, OBJECT REFERENCE, STYLE, and VISUAL DIRECTION are the DATA SHEETS in this system prompt. They are not sent again in the user message. Never answer ACK.
2. Every user message is one shot: a short request, optionally a PARENT STORY EVENT, and the CURRENT B SHOT. Answer it with the final prompt in exactly the required block structure above and nothing else - no ACK, no commentary, no tags or sections that are not listed, no markdown fences.
3. Use the DATA SHEETS and the earlier messages of this conversation automatically. "PREVIOUS FRAME LAYOUT", "PREVIOUS CAMERA" and "PREVIOUS SHOT POSE" mean the FRAME LAYOUT and CAMERA of your previous answer and the pose in the previous request's B shot; the screen-space continuity rule and the same-pose camera rule above apply to them.
4. Keep identity, clothing, object, spatial and state continuity across shots. When the CURRENT B SHOT states a change (clothing, body state, object, location, contact), the new state is the truth from that shot on. Never carry an earlier state into a shot whose B shot contradicts it. Identity, clothing (including damage), held objects, location and where people stand persist until a B shot changes them, so keep them when CURRENT B SHOT is silent. Hand and limb positions, gaze and facial expression do not persist: describe them only as CURRENT B SHOT states them, and never copy such wording from an earlier answer.
5. CURRENT B SHOT is the only truth for what happens now. Never invent events and never change the action, pose, people, objects or location of the B shot; earlier shots are continuity evidence only."""

_ESTIMATE_THAI = re.compile(r"[฀-๿]")


# ----------------------------------------------------------------------------------------------------------------------------------
# token counting
# ----------------------------------------------------------------------------------------------------------------------------------
_HELPER_CODE = (
    "import sys, json\n"
    "from llama_cpp import Llama\n"
    "llm = Llama(model_path=sys.argv[1], vocab_only=True, verbose=False)\n"
    "sys.stdout.write('READY\\n'); sys.stdout.flush()\n"
    "for line in sys.stdin:\n"
    "    s = json.loads(line)\n"
    "    sys.stdout.write(str(len(llm.tokenize(s.encode('utf-8'), add_bos=False, special=False))) + '\\n'); sys.stdout.flush()\n"
)


def estimate_tokens(text: str) -> int:
    """ESTIMATED: thai_chars/2.2 + other_chars/4.0 (measured on the real tokenizer: this over-counts 13-29% on Thai-English text, i.e. the safe side)."""
    t = text or ""
    thai = len(_ESTIMATE_THAI.findall(t))
    return int(thai / 2.2 + (len(t) - thai) / 4.0) + 1


class TokenCounter:
    """EXACT when the ComfyUI standalone python with llama_cpp can load the model vocab (vocab_only, no VRAM); otherwise ESTIMATED."""

    def __init__(self, wf_template: dict):
        self.mode = "ESTIMATED"
        self.reason = ""
        self._proc = None
        self._lock = asyncio.Lock()
        try:
            node = next(n for n in (wf_template or {}).values() if isinstance(n, dict) and n.get("class_type") == "LLMSessionChatNode")
            inputs = node.get("inputs") or {}
            hist = Path(str(inputs.get("history_dir") or ""))
            comfy_root = hist.parent.parent                       # ...\ComfyUI  (user\llm_session -> ComfyUI)
            py = comfy_root.parent / "standalone-env" / "python.exe"
            model = comfy_root / "models" / "LLM" / str(inputs.get("model") or "")
            if not py.exists():
                self.reason = f"no python at {py}"
            elif not model.exists():
                self.reason = f"no model file at {model}"
            else:
                self._proc = subprocess.Popen([str(py), "-c", _HELPER_CODE, str(model)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                              text=True, encoding="utf-8", bufsize=1)
                first = self._proc.stdout.readline().strip()
                if first == "READY":
                    self.mode = "EXACT"
                else:
                    self.reason = "tokenizer helper did not start"
                    self.close()
        except Exception as exc:
            self.reason = f"{type(exc).__name__}: {exc}"
            self.close()

    def close(self) -> None:
        p, self._proc = self._proc, None
        if p is not None:
            try:
                p.stdin.close()
            except Exception:
                pass
            try:
                p.terminate()
            except Exception:
                pass

    @property
    def margin(self) -> int:
        return SAFETY_MARGIN_EXACT if self.mode == "EXACT" else SAFETY_MARGIN_ESTIMATED

    def _count_sync(self, text: str) -> int:
        if self._proc is None:
            return estimate_tokens(text)
        try:
            self._proc.stdin.write(json.dumps(text or "", ensure_ascii=True) + "\n")
            self._proc.stdin.flush()
            return int(self._proc.stdout.readline().strip())
        except Exception as exc:
            self.mode, self.reason = "ESTIMATED", f"helper failed: {exc}"
            self.close()
            return estimate_tokens(text)

    async def count(self, text: str) -> int:
        async with self._lock:
            return await asyncio.get_event_loop().run_in_executor(None, self._count_sync, text)


# ----------------------------------------------------------------------------------------------------------------------------------
# transport
# ----------------------------------------------------------------------------------------------------------------------------------
def _history_dir(wf_template: dict) -> Path | None:
    for n in (wf_template or {}).values():
        if isinstance(n, dict) and n.get("class_type") == "LLMSessionChatNode":
            h = (n.get("inputs") or {}).get("history_dir")
            return Path(str(h)) if h else None
    return None


def read_history(wf_template: dict, session_id: str) -> dict | None:
    d = _history_dir(wf_template)
    if d is None:
        return None
    try:
        return json.loads((d / f"{session_id}.json").read_text(encoding="utf-8"))
    except Exception:
        return None


async def call_session(wf_template: dict, system_prompt: str, user_text: str, *, session_id: str, reset: bool, max_tokens: int,
                       n_ctx: int = FILL_N_CTX, on_log=None) -> str | None:
    """One LLMSessionChatNode call on a COPY of the workflow: n_ctx forced, history kept (max_turns large, summaries off)."""
    wf = copy.deepcopy(wf_template or {})
    hit = False
    for node in wf.values():
        if not isinstance(node, dict) or node.get("class_type") != "LLMSessionChatNode":
            continue
        inp = node.setdefault("inputs", {})
        inp.update({"user_text": user_text, "system_prompt": system_prompt, "session_id": session_id, "reset_session": bool(reset), "max_tokens": int(max_tokens),
                    "n_ctx": int(n_ctx), "max_turns": MAX_TURNS, "summarize_old_history": False})
        hit = True
    if not hit:
        if on_log:
            await on_log("❌ Image Prompts (conversation): workflow has no LLMSessionChatNode")
        return None
    loop = asyncio.get_event_loop()
    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, uuid.uuid4().hex)
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Image Prompts (conversation) queue: {exc}")
        return None
    deadline = time.time() + CALL_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(1.5)
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
                    return text
        if ((entry.get("status") or {}).get("status_str") or "").lower() in ("success", "error"):
            break
    return None


# ----------------------------------------------------------------------------------------------------------------------------------
# conversation
# ----------------------------------------------------------------------------------------------------------------------------------
class FillConversationError(Exception):
    pass


class _Session:
    """What Python believes the node's history holds for one session."""

    def __init__(self, session_id: str):
        self.id = session_id
        self.pairs: list[tuple[str, str]] = []        # (user, assistant) in order, INITIALIZE first
        self.tokens = 0                              # system excluded
        self.shots = 0


class FillConversation:
    def __init__(self, wf_template: dict, system_prompt: str, sheet: str, *, on_log=None, n_ctx: int = FILL_N_CTX, budget_ctx: int | None = None,
                 counter: TokenCounter | None = None, call=None, previous_n: int = REHYDRATE_PREVIOUS, validator=None):
        self.wf = wf_template
        self.system = system_prompt
        self.sheet = sheet
        self.on_log = on_log
        self.n_ctx = n_ctx                              # what the model really runs with
        self.budget_ctx = budget_ctx or BUDGET_CTX           # what Python plans against (tests make it smaller; never larger than n_ctx)
        self.budget_ctx = min(self.budget_ctx, self.n_ctx)
        self.counter = counter or TokenCounter(wf_template)
        self._call = call or call_session
        self.previous_n = previous_n
        self.validator = validator                      # reply text -> '' (ok) or a reason; None = every non-empty reply is accepted (old behaviour)
        self.last_rejected: dict | None = None          # set by shot() when the validator refused the reply: {'shot', 'reason', 'raw', 'session'}
        self.s: _Session | None = None
        self.system_tokens = 0
        self.accepted: list[tuple[str, str]] = []       # (user message text, accepted prompt) for every shot whose Fill prompt was accepted, global order
        self.canon: list[tuple[str, str | None]] = []   # EVERY processed shot, global order: (user message text with its B shot, accepted prompt or None when Fill's reply was refused / empty)
        self.sessions_opened = 0
        self.debug = LAST_RUN_DEBUG
        self.dbg = ConversationDebugLog("fill")           # debug mode: ONE file for the whole run, every session in it
        self.dbg_sessions: list[dict] = []
        self.dbg_meta: dict = {}
        self.force_rotate_reason = ""

    async def _log(self, msg: str):
        if self.on_log:
            await self.on_log(msg)

    async def _tok(self, text: str) -> int:
        return await self.counter.count(text) + PER_MESSAGE_OVERHEAD

    async def _need(self, new_input: str, reserve: int) -> tuple[int, int, int]:
        """(required, existing, new) tokens for the next call in the current session."""
        existing = BASE_OVERHEAD + self.system_tokens + (self.s.tokens if self.s else 0)
        new = await self._tok(new_input)
        return existing + new + reserve + NODE_MARGIN + self.counter.margin, existing, new

    def _dbg_flush(self) -> None:
        if self.dbg.enabled:
            self.dbg.write({"meta": {"backend": "local", "n_ctx": self.n_ctx, "budget_ctx": self.budget_ctx, "counter": self.counter.mode, **self.dbg_meta},
                            "system_prompt": self.system, "sessions": self.dbg_sessions, "events": list(self.debug)})

    def _dbg_message(self, sid: str, **kw) -> None:
        for s in self.dbg_sessions:
            if s["session_id"] == sid:
                s["messages"].append({"time": datetime.now().strftime("%H:%M:%S"), **kw})
                break

    def _record(self, **kw):
        self.debug.append({"t": round(time.time(), 1), **kw})

    # -- INITIALIZE ---------------------------------------------------------------------------------------------------------------
    def _continuity(self, entries: list[dict]) -> str:
        """Finished shots carried into a rotated session. The data sheets are already in the system prompt. Every entry is
        {"n": real shot number, "request": the shot request with its B shot, "prompt": accepted Fill prompt or "", "why": why there is no prompt}."""
        if not entries:
            return ""
        parts = ["SEQUENCE SO FAR - this session continues an earlier one. The shots below are already finished (do not write them again). "
                 "They are continuity evidence only: the state at the end of the last one is whatever its B shot states and its accepted prompt shows; "
                 "every new B shot decides what is true from then on."]
        for e in entries:
            if e["prompt"]:
                parts.append(f"FINISHED SHOT {e['n']}\nREQUEST:\n{e['request']}\nACCEPTED PROMPT:\n{e['prompt']}")
            else:                                          # the B shot happened; there is no prompt to show
                parts.append(f"FINISHED SHOT {e['n']}\nREQUEST:\n{e['request']}\nACCEPTED PROMPT: none ({e['why']}; the B shot above is what happened)")
        parts.append("=== END OF SEQUENCE SO FAR === The shot to write now follows.")
        return "\n\n".join(parts)

    def _evidence_plan(self) -> list[dict]:
        """Canonical B evidence first, accepted Fill prompts second (both from self.canon, global shot numbers = position + 1):
        - the last previous_n ACCEPTED shots keep their accepted prompt (visual continuity),
        - EVERY refused shot and the immediately preceding shot are carried as B only (the story history does not depend on Fill),
        in story order. Nothing is selected by reading the text."""
        canon = self.canon
        acc = [i for i, (_u, p) in enumerate(canon) if p]
        full = set(acc[-self.previous_n:]) if self.previous_n else set()
        chosen = sorted(full | {i for i, (_u, p) in enumerate(canon) if not p} | ({len(canon) - 1} if canon else set()))
        out = []
        for i in chosen:
            u, p = canon[i]
            out.append({"n": i + 1, "request": u, "prompt": p if i in full else "", "why": "the reply was refused" if not p else "not carried", "refused": not p})
        return out

    async def _open_session(self, shot_no: int, shot_request: str, reserve: int, reason: str) -> str:
        """Open a session without an ACK call. Returns the first user text (continuity, if any, plus this shot)."""
        if not self.system_tokens:
            self.system_tokens = await self._tok(self.system)
        sid = uuid.uuid4().hex[:8]
        entries = self._evidence_plan()
        report = {"kept": [], "shortened": [], "dropped": []}
        last_n = len(self.canon)                                           # the immediately preceding shot (its B is the last thing dropped)
        while True:
            continuity = self._continuity(entries)
            user_text = f"{continuity}\n\n{shot_request}" if continuity else shot_request
            user_tok = await self._tok(user_text)
            required = BASE_OVERHEAD + self.system_tokens + user_tok + reserve + NODE_MARGIN + self.counter.margin
            self._record(event="init_budget", session=sid, reason=reason, previous_shots=len(entries), system_tokens=self.system_tokens, first_user_tokens=user_tok,
                         output_reserve=reserve, safety_margin=self.counter.margin, node_margin=NODE_MARGIN, required=required, n_ctx=self.n_ctx, budget_ctx=self.budget_ctx, counter=self.counter.mode)
            if required <= self.budget_ctx:
                break
            if not entries:
                raise FillConversationError(
                    f"shot {shot_no}: even a fresh session cannot hold the system prompt (rules and data sheets) and this shot ({required} tokens needed, budget {self.budget_ctx}, "
                    f"n_ctx {self.n_ctx}, counter {self.counter.mode}); nothing was sent")
            # over budget: first shorten the OLDEST accepted prompt to its B shot (the story fact stays), then drop the oldest B-only entry, the preceding shot's B last
            longer = [e for e in entries if e["prompt"]]
            if longer:
                longer[0]["prompt"] = ""
                longer[0]["why"] = "its accepted prompt was left out to fit the context budget"
                report["shortened"].append(longer[0]["n"])
                continue
            victims = [e for e in entries if e["n"] != last_n] or entries
            entries.remove(victims[0])
            report["dropped"].append(victims[0]["n"])
        report["kept"] = [(e["n"], "B + accepted prompt" if e["prompt"] else "B only") for e in entries]
        prev_n = len(entries)
        if report["shortened"] or report["dropped"]:
            await self._log(f"⚠ Image Prompts (conversation): session {sid} for shot {shot_no} did not fit the budget with all continuity evidence: kept {report['kept']}, "
                            f"accepted prompt left out for shots {report['shortened']}, B shot left out for shots {report['dropped']}; continuity of what the left-out shots established is NOT guaranteed")
        self._record(event="evidence", session=sid, shot=shot_no, **report)
        self.sessions_opened += 1
        await self._log(f"ℹ Image Prompts (conversation): new session {sid} for shot {shot_no} ({reason}); data sheets are in the system prompt, no ACK round, {prev_n} finished shot(s) carried, about {required} tokens planned of {self.budget_ctx}")
        self.dbg_sessions.append({"session_id": sid, "opened_for_shot": shot_no, "reason": reason, "finished_shots_carried": prev_n, "evidence": report, "planned_tokens": required, "messages": []})
        self._dbg_flush()
        self.s = _Session(sid)
        return user_text

    async def _verify_history(self, expected_turns: int, max_tokens_req: int) -> str:
        """Read the node's history file back; return '' when it is what Python believes, otherwise the reason."""
        h = read_history(self.wf, self.s.id)
        if h is None:
            return ""                                      # history dir unreadable: cannot verify (recorded, not treated as a fault)
        turns = h.get("turns") or []
        if len(turns) != expected_turns:
            return f"history file has {len(turns)} turns, Python expected {expected_turns}"
        p = (turns[-1].get("params") or {}) if turns else {}
        if p.get("turns_limit_used") is not None and p["turns_limit_used"] < MAX_TURNS:
            return f"node reduced turns_limit to {p['turns_limit_used']} (history was cut)"
        if p.get("max_tokens_used") is not None and p.get("max_tokens_req") and p["max_tokens_req"] - p["max_tokens_used"] > NODE_MARGIN:
            return f"node shrank max_tokens {p['max_tokens_req']} -> {p['max_tokens_used']}"
        if (h.get("summary") or {}).get("enabled") or ((h.get("summary") or {}).get("text") or "").strip():
            return "node summarised old turns"
        return ""

    # -- one shot -----------------------------------------------------------------------------------------------------------------
    async def shot(self, shot_no: int, shot_request: str, reserve: int) -> str | None:
        """Return the model's prompt for this shot (None when the call returned no text). Raises FillConversationError when it can never fit."""
        reason = self.force_rotate_reason
        self.force_rotate_reason = ""
        if self.s is None:
            reason = reason or "first session"
        elif not reason:
            required, existing, new = await self._need(shot_request, reserve)
            self._record(event="budget", shot=shot_no, session=self.s.id, existing=existing, new_input=new, output_reserve=reserve, node_margin=NODE_MARGIN,
                         safety_margin=self.counter.margin, required=required, n_ctx=self.n_ctx, budget_ctx=self.budget_ctx, counter=self.counter.mode)
            if required > self.budget_ctx:
                reason = f"budget: {required} tokens needed > {self.budget_ctx}"
            elif self.s.shots >= TURN_CAP:
                reason = f"turn cap {TURN_CAP}"
        user_text = shot_request
        reset = False
        if reason:
            user_text = await self._open_session(shot_no, shot_request, reserve, reason)
            reset = True
            required, existing, new = await self._need(user_text, reserve)
            if required > self.budget_ctx:
                raise FillConversationError(f"shot {shot_no}: after opening a new session the shot still needs {required} tokens > budget {self.budget_ctx}; stopped (no further sessions)")
        t0 = time.time()
        raw = await self._call(self.wf, self.system, user_text, session_id=self.s.id, reset=reset, max_tokens=reserve, n_ctx=self.n_ctx, on_log=self.on_log)
        text = raw.strip() if isinstance(raw, str) else ""
        self._dbg_message(self.s.id, role="user", kind="shot", shot=shot_no, content=user_text)
        self._dbg_message(self.s.id, role="assistant", kind="shot", shot=shot_no, content=raw, seconds=round(time.time() - t0, 1), no_text=not text, max_tokens=reserve)
        self._record(event="shot", shot=shot_no, session=self.s.id, requested_max_tokens=reserve, n_ctx=self.n_ctx, ok=bool(text), seconds=round(time.time() - t0, 1),
                     reply_chars=len(text), session_pairs=len(self.s.pairs) + (1 if text else 0))
        if not text:
            self.force_rotate_reason = "the call returned no text (history state unknown)"
            self._dbg_flush()
            return None
        self.last_rejected = None
        why = self.validator(text, reply_tokens=await self.counter.count(text), max_tokens=reserve) if self.validator else ""
        if why:
            # The node already stored this exchange in its history file. The reply is not committed anywhere on our side (not in the session record,
            # not in self.accepted), and the next shot opens a NEW session, which is rebuilt from self.canon: the B shot of this shot is carried (it happened), the refused reply is not.
            self.last_rejected = {"shot": shot_no, "reason": why, "raw": raw, "session": self.s.id}
            self.force_rotate_reason = f"shot {shot_no} reply refused ({why}); its session history holds the refused reply"
            self._record(event="rejected", shot=shot_no, session=self.s.id, reason=why, reply_chars=len(text))
            self._dbg_message(self.s.id, role="note", kind="rejected", content=why)
            await self._log(f"⚠ Image Prompts (conversation): shot {shot_no} reply refused: {why}; the shot keeps its old prompt and the next shot opens a new session")
            self._dbg_flush()
            return None
        self.s.pairs.append((user_text, text))
        self.s.tokens += await self._tok(user_text) + await self._tok(text)
        self.s.shots += 1
        fault = await self._verify_history(len(self.s.pairs), reserve)
        if fault:
            self.force_rotate_reason = fault
            self._record(event="history_fault", shot=shot_no, session=self.s.id, fault=fault)
            self._dbg_message(self.s.id, role="note", kind="history_fault", content=fault)
            await self._log(f"⚠ Image Prompts (conversation): {fault}; the next shot opens a new session")
        self._dbg_flush()
        return text

    def finish(self):
        self._dbg_flush()
        self.counter.close()


# ----------------------------------------------------------------------------------------------------------------------------------
# Fill entry point
# ----------------------------------------------------------------------------------------------------------------------------------
def shot_request(F, shot: dict, idx: int, total: int, a1: dict, micro_beats: list[dict]) -> str:
    """The short per-shot request (Local and Grok): the rules live in the system prompt, the sheet and earlier shots in the conversation history."""
    parent = F._parent_macro_text(a1, micro_beats, shot)
    return "\n\n".join(x for x in (
        f"Shot {idx + 1} of {total}: write its final Krea2 prompt, continuing the sequence from the data sheets in the system prompt and your earlier answers.",
        ("PARENT STORY EVENT:\n" + parent) if parent else "",
        "CURRENT B SHOT:\n" + F._shot_payload(shot),
    ) if x)


async def run_fill_conversation(F, shots: list[dict], a1: dict, wf_template: dict, on_err, on_log=None, *, image_style: str, props_desc: str, visual_direction: str,
                                micro_beats: list[dict], on_shot=None, stop_event: asyncio.Event | None = None, budget_ctx: int | None = None, counter=None, call=None) -> list[dict]:
    """F = the llm_engine_fill module (system prompt, sheet / payload builders, max_tokens policy). Shots are processed in order; the index never restarts."""
    del LAST_RUN_DEBUG[:]
    total = len(shots)
    sheet = F._production_reference(a1, props_desc=props_desc, image_style=image_style) + "\n\n" + visual_direction
    system = attach_sheets(system_text(F), sheet)
    conv = FillConversation(wf_template, system, "", on_log=on_log, budget_ctx=budget_ctx, counter=counter, call=call, validator=validate_fill_output)
    conv.dbg_meta = {"shots": total}
    await conv._log(f"ℹ Image Prompts (conversation): {total} shots, n_ctx {conv.n_ctx}, budget {conv.budget_ctx}, token counter {conv.counter.mode}" + (f" ({conv.counter.reason})" if conv.counter.reason else ""))
    try:
        for idx, shot in enumerate(shots):
            if stop_event and stop_event.is_set():
                if on_log:
                    await on_log(f"⏹ Stop Image Prompts after {idx}/{total} shots")
                break
            previous = shot.get("prompt", "")
            request = shot_request(F, shot, idx, total, a1, micro_beats)
            has_objects = bool(str(shot.get("objects") or "").strip())
            conv.validator = lambda t, _o=has_objects, **kw: validate_fill_output(t, objects_nonempty=_o, **kw)       # OBJECT LOCK is required exactly when the current B shot has objects
            try:
                text = await conv.shot(idx + 1, request, F._local_max_tokens(shot))
            except FillConversationError as exc:
                await on_err(f"Image Prompts (conversation): {exc}")
                for rest in shots[idx:]:
                    rest["_fill_failed"] = True
                if on_shot:
                    await on_shot(total, total)
                break
            if text:
                shot["prompt"] = text
                shot["_fill_engine"] = F.FILL_ENGINE_VNEXT2
                shot["prompt_style"] = F.FILL_ENGINE_VNEXT2
                shot.pop("_fill_session_id", None)          # one session holds many shots: the shot card's Regen chat must start its own session, not continue this one
                shot.pop("_fill_failed", None)
                shot.pop("_fill_rejected", None)
                shot.pop("_fill_renderer_ready", None)
                conv.accepted.append((request, text))
                conv.canon.append((request, text))
                commit = "NEW"
            else:
                shot["_fill_failed"] = True
                conv.canon.append((request, None))              # the B shot is still part of the story history for later sessions
                if conv.last_rejected and conv.last_rejected.get("shot") == idx + 1:
                    shot["_fill_rejected"] = {"reason": conv.last_rejected["reason"], "raw": conv.last_rejected["raw"]}     # kept for review; never used as a prompt
                commit = "KEEP_OLD" if str(previous or "").strip() else "EMPTY"
            if on_log:
                await on_log(f"NORMAL RENDER PROMPT shot={shot.get('shot')} commit={commit} prompt_len={len(text or '')} (conversation, session {conv.s.id if conv.s else '-'})")
            if on_shot:
                await on_shot(idx + 1, total)
    finally:
        conv.finish()
    return shots


# ----------------------------------------------------------------------------------------------------------------------------------
# Grok conversation (no token budget: Grok takes the real message history and has a much larger window)
# ----------------------------------------------------------------------------------------------------------------------------------
GROK_TIMEOUT = 180
GROK_ACK_TOKENS = 32


async def call_grok_chat(F, system_prompt: str, messages: list[dict], *, api_key: str, model: str, max_tokens: int, on_log=None) -> str | None:
    """One Grok chat-completions call with the real message history: [system] + messages. Same payload fields as Fill's Grok batch call."""
    import urllib.error
    import urllib.request
    if not api_key:
        if on_log:
            await on_log("❌ Image Prompts (conversation): Grok API key is empty")
        return None
    payload = {"model": model, "messages": [{"role": "system", "content": system_prompt}] + list(messages), "max_tokens": int(max_tokens),
               "temperature": F._GROK_TEMPERATURE, "reasoning_effort": F._GROK_REASONING_EFFORT}

    def _post(body: dict) -> dict:
        req = urllib.request.Request("https://api.x.ai/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=GROK_TIMEOUT) as response:
            return json.loads(response.read())

    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(None, _post, payload)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 400 and "reasoning" in body.lower():
            retry = dict(payload)
            retry.pop("reasoning_effort", None)
            try:
                response = await loop.run_in_executor(None, _post, retry)
            except Exception as retry_exc:
                if on_log:
                    await on_log(f"❌ Image Prompts (conversation) Grok retry: {retry_exc}")
                return None
        else:
            if on_log:
                await on_log(f"❌ Image Prompts (conversation) Grok HTTP {exc.code}: {body[:300]}")
            return None
    except Exception as exc:
        if on_log:
            await on_log(f"❌ Image Prompts (conversation) Grok: {exc}")
        return None
    choices = response.get("choices") if isinstance(response, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else {}
    message = first.get("message") if isinstance(first, dict) else {}
    text = message.get("content") if isinstance(message, dict) else None
    return text if isinstance(text, str) else None


async def run_fill_conversation_grok(F, shots: list[dict], a1: dict, on_err, on_log=None, *, api_key: str, model: str, image_style: str, props_desc: str, visual_direction: str,
                                     micro_beats: list[dict], on_shot=None, stop_event: asyncio.Event | None = None, call=None) -> list[dict]:
    """Fill as a Grok conversation: data sheets are in the system prompt, then one short request per shot with the real history. No ACK round.
    A shot that returns no text is marked failed and NOT added to the history; the conversation goes on."""
    del LAST_RUN_DEBUG[:]
    call = call or call_grok_chat
    total = len(shots)
    sheet = F._production_reference(a1, props_desc=props_desc, image_style=image_style) + "\n\n" + visual_direction
    system = attach_sheets(system_text(F), sheet)
    history: list[dict] = []
    dbg = ConversationDebugLog("fill")                    # debug mode: one file for the run
    dsession = {"session_id": "grok messages", "messages": []}
    def dflush():
        if dbg.enabled:
            dbg.write({"meta": {"backend": "grok", "model": model, "shots": total}, "system_prompt": system, "sessions": [dsession], "events": list(LAST_RUN_DEBUG)})
    def dmsg(**kw):
        dsession["messages"].append({"time": datetime.now().strftime("%H:%M:%S"), **kw})
    if on_log:
        await on_log(f"ℹ Image Prompts (conversation, Grok {model}): {total} shots, data sheets in the system prompt, no ACK round")
    for idx, shot in enumerate(shots):
        if stop_event and stop_event.is_set():
            if on_log:
                await on_log(f"⏹ Stop Image Prompts after {idx}/{total} shots")
            break
        previous = shot.get("prompt", "")
        request = shot_request(F, shot, idx, total, a1, micro_beats)
        t1 = time.time()
        raw = await call(F, system, history + [{"role": "user", "content": request}], api_key=api_key, model=model, max_tokens=F._local_max_tokens(shot), on_log=on_log)
        text = raw.strip() if isinstance(raw, str) else ""
        dmsg(role="user", kind="shot", shot=idx + 1, content=request)
        dmsg(role="assistant", kind="shot", shot=idx + 1, content=raw, seconds=round(time.time() - t1, 1), no_text=not text)
        dflush()
        LAST_RUN_DEBUG.append({"event": "shot", "backend": "grok", "shot": idx + 1, "history_messages": len(history), "ok": bool(text), "reply_chars": len(text), "seconds": round(time.time() - t1, 1)})
        why = validate_fill_output(text, objects_nonempty=bool(str(shot.get("objects") or "").strip())) if text else ""
        if why:
            LAST_RUN_DEBUG.append({"event": "rejected", "backend": "grok", "shot": idx + 1, "reason": why})
            if on_log:
                await on_log(f"⚠ Image Prompts (conversation, Grok): shot {idx + 1} reply refused: {why}; it is not added to the history")
            shot["_fill_rejected"] = {"reason": why, "raw": raw}
            text = ""
        if text:
            history += [{"role": "user", "content": request}, {"role": "assistant", "content": text}]
            shot.pop("_fill_rejected", None)
            shot["prompt"] = text
            shot["_fill_engine"] = F.FILL_ENGINE_VNEXT2
            shot["prompt_style"] = F.FILL_ENGINE_VNEXT2
            shot.pop("_fill_session_id", None)
            shot.pop("_fill_failed", None)
            shot.pop("_fill_renderer_ready", None)
            commit = "NEW"
        else:
            shot["_fill_failed"] = True
            commit = "KEEP_OLD" if str(previous or "").strip() else "EMPTY"
        if on_log:
            await on_log(f"NORMAL RENDER PROMPT shot={shot.get('shot')} commit={commit} prompt_len={len(text)} (conversation, Grok)")
        if on_shot:
            await on_shot(idx + 1, total)
    return shots
