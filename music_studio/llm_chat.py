"""AI chat backend for the lyrics assistant: the same two engines the other programs use (a local LLM workflow through ComfyUI, or Grok),
a strict answer format and the prompt. No UI. Blocking functions: the UI runs them in an executor.

Answer format (separator lines, each alone on its line, exactly once, in this order):
  =========== REPLY ===========     short chat answer
  =========== PROPOSAL ===========  the proposed text (empty when only chatting)
  =========== END ===========
Nothing is repaired or guessed: an answer that does not follow it is reported, not applied.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path

from . import ace_step_knowledge as AK
from . import comfy_audio as CA

APPDATA = Path(os.environ.get("APPDATA", str(Path.home())))
LLM_CFG_FILE = APPDATA / "WorkflowPad" / "llm_chat_config.json"          # shared with Prompt Lab (read only here)
GROK_CREDS_FILE = APPDATA / "StoryGenerator" / "credentials.json"        # shared with Story Generator / Prompt Lab (read only here)

SEP = {"REPLY": "=========== REPLY ===========", "PROPOSAL": "=========== PROPOSAL ===========", "END": "=========== END ==========="}

SYSTEM_PROMPT = (
    "You are a songwriting assistant for ACE-Step 1.5, an AI model that sings the lyrics it is given.\n"
    "Reply in the language the user writes in. Lyrics stay in the language of the song.\n\n"
    "LYRICS FORMAT (ACE-Step):\n"
    "- Section tags on their own line: [Intro] [Verse 1] [Pre-Chorus] [Chorus] [Bridge] [Outro] [Instrumental] [Guitar Solo]. A style may follow a dash: [Chorus - powerful belting].\n"
    "- Sung lines are plain text, one phrase per line. '...' is a pause. (words in parentheses) are backing vocals.\n"
    "- A line starting with [Hold] followed by a romanized / stretched spelling asks for a held note.\n"
    "- NEVER use HTML or XML tags such as <whisper> or <strong>: the model would sing them.\n\n"
    + AK.knowledge(AK.CHAT_TOPICS) + "\n\n"
    "QUESTIONS: you can answer questions about ACE-Step, captions, vocal tags, BPM / key / time signature, structure or why a part sounds weak. Answer in the REPLY and leave the PROPOSAL empty. "
    "Say clearly what the official guide (G) confirms and what is only your suggestion (S); never promise something this program's workflow cannot do.\n\n"
    "OUTPUT FORMAT - exactly these three marker lines, each alone on its own line, in this order, nothing before the first or after the last:\n"
    + SEP["REPLY"] + "\n<short conversational reply, 1-3 sentences>\n\n"
    + SEP["PROPOSAL"] + "\n<the proposed lyrics text; leave empty when you are only chatting or no change is needed>\n\n"
    + SEP["END"] + "\n\n"
    "RULES:\n"
    "- When SELECTED TEXT is given, the PROPOSAL replaces ONLY that selected text (same kind of lines, same tags if it has any).\n"
    "- When there is no selected text, the PROPOSAL is the COMPLETE new lyrics, keeping every part the user did not ask to change word for word.\n"
    "- Keep the existing section tags and structure unless asked to change them. No markdown, no code block, no explanations inside the PROPOSAL."
)


class LLMFormatError(ValueError):
    raw = ""


def _is_sep(line: str, word: str) -> bool:
    m = re.fullmatch(r"([\W_]*)\b" + word + r"\b([\W_]*)", (line or "").strip(), re.IGNORECASE)
    return bool(m and re.search(r"[^\w\s]|_", m.group(1) + m.group(2)))


def parse_answer(raw: str) -> tuple[str, str]:
    text = re.sub(r"(?:\s*</?(?:end_of_turn|end_turn|start_of_turn|eos)>)+\s*$", "", raw or "").strip()
    if not text:
        raise LLMFormatError("the answer is empty")
    lines = text.splitlines()
    if len(lines) >= 2 and re.fullmatch(r"\s*```[A-Za-z]*\s*", lines[0]) and re.fullmatch(r"\s*```\s*", lines[-1]):
        lines = lines[1:-1]
    pos = {}
    for w in ("REPLY", "PROPOSAL", "END"):
        found = [i for i, ln in enumerate(lines) if _is_sep(ln, w)]
        if not found:
            raise LLMFormatError(f"the {w} separator line is missing")
        if len(found) > 1:
            raise LLMFormatError(f"the {w} separator line appears {len(found)} times")
        pos[w] = found[0]
    if not (pos["REPLY"] < pos["PROPOSAL"] < pos["END"]):
        raise LLMFormatError("the separator lines are not in the order REPLY, PROPOSAL, END")
    reply = "\n".join(lines[pos["REPLY"] + 1:pos["PROPOSAL"]]).strip()
    proposal = "\n".join(lines[pos["PROPOSAL"] + 1:pos["END"]]).strip("\n")
    if not reply:
        raise LLMFormatError("the REPLY section is empty")
    return reply, proposal


def build_user_message(title: str, settings: dict, lyrics: str, selected: str, instruction: str, history: list[tuple[str, str]]) -> str:
    parts = [f"SONG: {title}",
             f"STYLE: {settings.get('genre', '')}; mood: {settings.get('mood', '') or '-'}; language code: {settings.get('language', '')}; BPM {settings.get('bpm', '')}"]
    if history:
        parts.append("RECENT CHAT:\n" + "\n".join(f"{'User' if r == 'user' else 'Assistant'}: {t[:600]}" for r, t in history[-4:]))
    parts.append("CURRENT LYRICS:\n" + (lyrics.strip() if lyrics.strip() else "(empty)"))
    if selected:
        parts.append("SELECTED TEXT (the proposal replaces only this):\n" + selected)
    parts.append("USER REQUEST:\n" + instruction.strip())
    return "\n\n".join(parts)


# ── engines ──────────────────────────────────────────────────────────────────────────────────────────────
def load_cfg() -> dict:
    try:
        return json.loads(LLM_CFG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def grok_creds() -> dict:
    try:
        d = json.loads(GROK_CREDS_FILE.read_text(encoding="utf-8"))
        return {"key": d.get("grok_api_key", ""), "model": d.get("grok_model", "grok-4-latest")}
    except Exception:
        return {"key": "", "model": "grok-4-latest"}


def list_local_workflows(llm_dir: Path) -> list[str]:
    out = []
    if llm_dir.is_dir():
        for f in sorted(llm_dir.glob("*.json")):
            try:
                wf = json.loads(f.read_text(encoding="utf-8"))
                if any(isinstance(n, dict) and n.get("class_type") == "LLMSessionChatNode" for n in wf.values()):
                    out.append(f.name)
            except Exception:
                continue
    return out


def ask_local(host: str, wf_path: Path, system: str, user_text: str, timeout_s: float = 300.0) -> str:
    """Run the local LLM workflow once with a fresh session (the app sends everything the model needs; no server-side history is mixed in)."""
    wf = json.loads(wf_path.read_text(encoding="utf-8"))
    cfg = load_cfg()
    found = False
    for nid, node in wf.items():
        if isinstance(node, dict) and node.get("class_type") == "LLMSessionChatNode":
            found = True
            i = node["inputs"]
            i["user_text"] = user_text
            i["system_prompt"] = system
            i["session_id"] = "music_" + uuid.uuid4().hex[:8]
            i["reset_session"] = True
            i["history_dir"] = cfg.get("history_dir", "user/llm_session")
            i["max_tokens"] = int(cfg.get("max_tokens", 4096))
            i["temperature"] = float(cfg.get("temperature", 0.7))
    if not found:
        raise RuntimeError("This workflow has no LLMSessionChatNode")
    pid = CA.submit(host, wf)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        time.sleep(1.5)
        entry = CA.poll(host, pid)
        if entry is None:
            continue
        err = CA.job_error(entry)
        if err:
            raise RuntimeError(err)
        for out in (entry.get("outputs") or {}).values():
            texts = out.get("text") or ([out["string"]] if "string" in out else [])
            if texts:
                t = texts[0] if isinstance(texts[0], str) else str(texts[0])
                if t.strip():
                    return t.strip()
        raise RuntimeError("The LLM finished but returned no text")
    raise RuntimeError("The LLM did not answer in time")


def ask_grok(key: str, model: str, system: str, user_text: str, max_tokens: int = 4096) -> str:
    if not key:
        raise RuntimeError("Grok API key is not set (set it in Prompt Lab or Story Generator)")
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_text}],
            "max_tokens": max_tokens, "temperature": 0.7, "reasoning_effort": "low"}

    def post(b):
        req = urllib.request.Request("https://api.x.ai/v1/chat/completions", data=json.dumps(b).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    try:
        try:
            resp = post(body)
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors="replace")
            if e.code == 400 and "reasoning" in err.lower():
                body.pop("reasoning_effort", None)
                resp = post(body)
            else:
                raise RuntimeError(f"Grok HTTP {e.code}: {err[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Grok: {e.reason}")
    content = (resp["choices"][0]["message"].get("content") or "").strip()
    if not content:
        raise RuntimeError("Grok returned an empty answer (raise max tokens)")
    return content
