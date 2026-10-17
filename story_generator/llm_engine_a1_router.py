"""
llm_engine_a1_router.py - SOURCE ROUTER for A1: decides what kind of input the Story field holds BEFORE any macro story is written.

    STATIC     one frozen moment (appearance, outfit, pose, place, light)          -> A1 Static (arc-based story)
    NARRATIVE  things that happen one after another                                 -> A1 Novel
    (anything else: invalid reply / backend error)  -> the caller falls back to the previous behaviour and records why

One short LLM call with its own session; it only classifies, it never writes story content. The model answers with the LABELS OF THE ACTIVE
CONTRACT (see CONTRACTS); the reply is accepted ONLY when, after trimming whitespace, it is exactly one of that contract's two labels (case
ignored). Nothing else is accepted: no punctuation, no extra words, no label found inside a longer text, no abbreviation of a label the contract
does not define. The contract's labels are mapped to the two internal routes STATIC / NARRATIVE. Python does no keyword/regex interpretation of
the story.
"""

__version__ = "20261030_a1_router_sn"

import json
import re
import uuid

from .constants import LLM_DIR
from .llm_engine_a1 import GROK_MARKER, _call_grok_text, _call_local_text

ROUTES = ("STATIC", "NARRATIVE")

# Label contracts: the prompt and the accepted labels travel together; changing one without the other is a bug (the tests check them as a pair).
CONTRACTS = {
    "STATIC_NARRATIVE": {
        "labels": {"STATIC": "STATIC", "NARRATIVE": "NARRATIVE"},
        "system": """Classify the input into one category.

STATIC = one scene or moment without sequential story progression.
NARRATIVE = multiple ordered events or meaningful state changes.

Return exactly STATIC or NARRATIVE.
No explanation.""",
        "reminder": "Return exactly STATIC or NARRATIVE.",
    },
    "S_N": {
        "labels": {"S": "STATIC", "N": "NARRATIVE"},
        "system": """Classify the input into one category.

S = STATIC: one scene or moment without sequential story progression.
N = NARRATIVE: multiple ordered events or meaningful state changes.

Return exactly one character: S or N.
No explanation.""",
        "reminder": "Return exactly one character: S or N.",
    },
    "STATIC_STORY": {
        "labels": {"STATIC": "STATIC", "STORY": "NARRATIVE"},
        "system": """Classify the input into one category.

STATIC = one scene or moment without sequential story progression.
STORY = multiple ordered events or meaningful state changes.

Return exactly STATIC or STORY.
No explanation.""",
        "reminder": "Return exactly STATIC or STORY.",
    },
}
DEFAULT_CONTRACT = "S_N"       # chosen from the experiment (audit_reports/a1_router_label_tuning): single-character labels survive the small token budget of a classification call
_ROUTER_MAX_TOKENS = 256      # a budget of 16/64 tokens cut multi-token labels to their first token ("N", "ST") on this local model; 400 gave the full word


def _contract(name: str | None) -> tuple[str, dict]:
    n = name or DEFAULT_CONTRACT
    return n, CONTRACTS[n]


def _build_router_user_prompt(story_text: str, contract: str | None = None) -> str:
    return "INPUT:\n" + (story_text or "") + "\n\n" + _contract(contract)[1]["reminder"]


_TEMPLATE_TAIL_RE = re.compile(r"(?:\s*</?(?:end_of_turn|start_of_turn|eos)>)+\s*$")        # chat-template end markers a local model may leak after its answer


def _strip_template_tail(raw: str | None) -> str:
    """Transport only: a trailing marker such as </start_of_turn> is not part of the answer (observed: the reply was 'S</start_of_turn>' and was rejected as invalid,
    so a static description fell back to A1 Novel). Only a marker at the very end is removed; the label itself must still be exactly one of the contract's labels."""
    return _TEMPLATE_TAIL_RE.sub("", raw or "")


def parse_router_label(raw: str | None, contract: str | None = None) -> str | None:
    """Exact label check for the active contract: trim whitespace, ignore case, the whole reply must be one of the contract's labels.
    Returns the internal route 'STATIC' / 'NARRATIVE', or None for ANYTHING else (empty, punctuation, an explanation, both labels, a label
    inside a longer text, a label of another contract)."""
    s = _strip_template_tail(raw).strip().upper()
    return _contract(contract)[1]["labels"].get(s)


async def classify_source(story_text: str, llm_wf_file: str, on_log=None, grok_api_key: str = "", grok_model: str = "grok-4-latest",
                          contract: str | None = None) -> dict:
    """One classification call. Returns {"label": 'STATIC'|'NARRATIVE'|None (internal route), "status": 'ok'|'invalid'|'error', "raw": str,
    "backend": ..., "error": str|None, "contract": name, "expected_labels": [...]}. Never raises for a backend problem; the caller decides the fallback."""
    cname, c = _contract(contract)
    backend = "grok" if llm_wf_file == GROK_MARKER else "local"
    out = {"label": None, "status": "error", "raw": "", "backend": backend, "error": None, "contract": cname, "expected_labels": list(c["labels"]),
           "system_prompt": c["system"], "user_prompt": _build_router_user_prompt(story_text, cname)}
    try:
        if backend == "grok":
            raw = await _call_grok_text(c["system"], out["user_prompt"], api_key=grok_api_key, model=grok_model, max_tokens=_ROUTER_MAX_TOKENS, on_log=on_log)
        else:
            if not (llm_wf_file or "").strip():
                out["error"] = "no local LLM workflow selected"; return out
            wf_path = LLM_DIR / f"{llm_wf_file}.json"
            if not wf_path.exists():
                out["error"] = f"workflow not found: {wf_path}"; return out
            wf = json.loads(wf_path.read_text(encoding="utf-8"))
            raw = await _call_local_text(wf, c["system"], out["user_prompt"], max_tokens=_ROUTER_MAX_TOKENS, session_id="rt" + uuid.uuid4().hex[:6], on_log=on_log, reset=True)
    except Exception as exc:                                   # transport problem: reported, never guessed
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    out["raw"] = raw or ""
    if not (raw or "").strip():
        out["error"] = "empty reply (timeout or backend error)"
        return out
    label = parse_router_label(raw, cname)
    out["label"], out["status"] = label, ("ok" if label else "invalid")
    return out
