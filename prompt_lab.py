"""
Prompt Lab – used together with image_editor.py
- same folder, same COMFY_HOST, same scan_apis()
- stores the prompt list in prompts_tester.json
"""
import asyncio, base64, json, random, shutil, time, uuid, urllib.parse, urllib.request, urllib.error
import sys
import re
from pathlib import Path
import flet as ft
import os as _os
import update_check as _update_check
import app_error as _app_error
import export_util as _export_util

# ── Force UTF-8 stdout/stderr (fix UnicodeEncodeError on Windows) ──────
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception: pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try: sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception: pass

# ── App metadata ──────────────────────
APP_ID      = "prompt_lab"
APP_NAME    = "Prompt Lab"
APP_VERSION = "0.1.9"   # SemVer; see RELEASE.md

# ── Config ────────────────────────────────────────────────────────────
COMFY_HOST   = "127.0.0.1:8188"
# Folder with comfy_apis/ (image_create, image_edit, llm, caption, enhance), icons/: the project folder, or (PyInstaller) the folder next to the EXE.
APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
API_DIR      = APP_ROOT / "comfy_apis" / "image_create"      # text-to-image workflows
ENHANCE_DIR  = APP_ROOT / "comfy_apis" / "enhance"
# Scratch files go to LOCALAPPDATA, never next to the source: an installed copy
# may sit in a read-only folder.
TEMP_DIR     = Path(_os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WorkflowPad" / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)
try: ENHANCE_DIR.mkdir(parents=True, exist_ok=True)
except OSError: pass
PROMPTS_FILE = Path(_os.environ.get("APPDATA", str(Path.home()))) / "WorkflowPad" / "prompts_tester.json"
IMAGES_FILE  = Path(_os.environ.get("APPDATA", str(Path.home()))) / "WorkflowPad" / "prompt_images.json"

# Shared Grok credentials — same file as story_generator
_GROK_CREDS_PATH = Path(_os.environ.get("APPDATA", str(Path.home()))) / "StoryGenerator" / "credentials.json"

def _load_grok_creds() -> dict:
    try:
        if _GROK_CREDS_PATH.exists():
            return json.loads(_GROK_CREDS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_grok_creds(api_key: str, model: str):
    try:
        _GROK_CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_grok_creds()
        existing["grok_api_key"] = api_key
        existing["grok_model"]   = model
        _GROK_CREDS_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

async def _call_grok(api_key: str, model: str, system_prompt: str,
                     messages: list[dict], max_tokens: int = 1024, temperature: float = 0.7) -> str:
    """Call xAI Grok API. Returns the assistant reply text."""
    if not api_key:
        raise ValueError("Grok API key not set")
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    body = {
        "model": model,
        "messages": full_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        # grok-4-latest is a reasoning model; default effort is "high" which burns
        # the max_tokens budget / the timeout before any answer is produced.
        "reasoning_effort": "low",
    }
    import urllib.error as _ue
    def _do():
        def _post(b: dict):
            req = urllib.request.Request(
                "https://api.x.ai/v1/chat/completions",
                data=json.dumps(b).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        try:
            try:
                return _post(body)
            except _ue.HTTPError as e:
                err = e.read().decode(errors="replace")
                # Models that don't support reasoning_effort reject it — retry without.
                if e.code == 400 and "reasoning" in err.lower():
                    body.pop("reasoning_effort", None)
                    return _post(body)
                raise RuntimeError(f"HTTP {e.code}: {err[:400]}")
        except _ue.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")
        except Exception as e:
            print(f"[GROK HTTP] error: {type(e).__name__}: {e}")
            raise
    resp = await asyncio.get_running_loop().run_in_executor(None, _do)
    content = resp["choices"][0]["message"].get("content") or ""
    if not content.strip():
        raise RuntimeError("Grok returned empty content (reasoning may have used up max_tokens — raise Max Tokens in Settings)")
    return content
LLM_DIR      = APP_ROOT / "comfy_apis" / "llm"
LLM_CFG_FILE = Path(_os.environ.get("APPDATA", str(Path.home()))) / "WorkflowPad" / "llm_chat_config.json"
_LEGACY_LLM_CFG = APP_ROOT / "llm_chat_config.json"

# ── LLM Chat config helpers ───────────────────────────────────────────
_SEP_REPLY = "=========== REPLY ==========="
_SEP_PROMPT = "=========== PROMPT ==========="
_SEP_END = "=========== END ==========="
_OUTPUT_CONTRACT = (
    "OUTPUT FORMAT - exactly these three marker lines, each alone on its own line, in this order, with nothing before the first or after the last:\n"
    + _SEP_REPLY + "\n"
    "<short conversational reply, 1-2 sentences, no labels>\n\n"
    + _SEP_PROMPT + "\n"
    "<the complete image prompt in English; several lines are fine; leave this section empty when you are only chatting or no prompt change is needed>\n\n"
    + _SEP_END
)
_DEFAULT_SYS = (
    "You are an AI image prompt engineer for Flux/Krea.\n\n"
    "Reply in the same language the user writes in. Prompt content must be in English.\n\n"
    + _OUTPUT_CONTRACT + "\n\n"
    "RULES:\n"
    "- The prompt: include only sections relevant to the image (skip irrelevant ones), each section on its own line\n"
    "- Keep ALL details. Never shorten, summarize, or use placeholders\n"
    "- Only modify what user asked, keep everything else word-for-word\n"
    "- If [No prompt yet] and user just chatting: leave the PROMPT section empty\n"
    "- Never write the prompt inside the REPLY section; no markdown, no code block"
)


_SEP_WORDS = ("REPLY", "PROMPT", "END")


def _is_sep_line(line: str, name: str) -> bool:
    """A separator line: the protocol word (REPLY / PROMPT / END, any case) ALONE on a line with only decoration around it, and at least one decoration character. So '=== END ===', '=END=', '= END =',
    '=========== END ==========', '--- END ---', '**END**', '[END]', '<END>', '### END ###', 'END ===', '═══ END ═══' are all the END separator. The kind and the NUMBER of decoration characters are not the
    protocol (models vary them all the time); the word on a line of its own is. A bare word with no decoration (an ordinary line of text) and a word with other words beside it ('THE END', 'END of prompt',
    'ENDING') are NOT separators."""
    m = re.fullmatch(r"([\W_]*)\b" + name + r"\b([\W_]*)", (line or "").strip(), re.IGNORECASE)
    return bool(m and re.search(r"[^\w\s]|_", m.group(1) + m.group(2)))


_INLINE_DECO = r"[=\-_*#~<>+—–═━─＝]{2,}"          # decoration only (no quotes, braces, colons or commas: a JSON key like {"reply": ...} must never look like a separator)
_INLINE_SEP_RE = re.compile(_INLINE_DECO + r"[ \t]*\b(?:REPLY|PROMPT|END)\b[ \t]*" + _INLINE_DECO, re.IGNORECASE)


def _split_inline_separators(lines: list[str]) -> list[str]:
    """A fully decorated separator ('=== END ===': two or more decoration characters on BOTH sides of the word) that sits in the same line as text ('=== REPLY === Hi' or 'the last words === END ===') is
    moved onto its own line; the text stays exactly as it was. A line that already is a separator line, and every ordinary line ('Prompt: a woman', 'a == b'), is left alone."""
    out: list[str] = []
    for ln in lines:
        if "\n" in ln or not _INLINE_SEP_RE.search(ln) or any(_is_sep_line(ln, n) for n in _SEP_WORDS):
            out.append(ln)
            continue
        out.extend(part for part in _INLINE_SEP_RE.sub(lambda m: "\n" + m.group(0) + "\n", ln).split("\n"))
    return out


def _prompt_has_contract(text: str) -> bool:
    """True when the system prompt carries the three separator lines (each alone on a line). A saved prompt without them (the old JSON one) cannot produce answers this program can read."""
    lines = (text or "").splitlines()
    return all(any(_is_sep_line(ln, n) for ln in lines) for n in _SEP_WORDS)


def load_llm_cfg() -> dict:
    # One-time migration: earlier builds kept this file next to the source.
    if not LLM_CFG_FILE.exists() and _LEGACY_LLM_CFG.exists():
        try:
            LLM_CFG_FILE.parent.mkdir(parents=True, exist_ok=True)
            LLM_CFG_FILE.write_bytes(_LEGACY_LLM_CFG.read_bytes())
        except Exception:
            pass
    if LLM_CFG_FILE.exists():
        try:
            return json.loads(LLM_CFG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # history_dir stays empty: ComfyUI resolves a relative path against its own
    # installation, so we must not guess where that is.
    return {"system_prompt": _DEFAULT_SYS, "history_dir": "user/llm_session", "max_tokens": _DEFAULT_MAX_TOKENS}

def save_llm_cfg(cfg: dict):
    LLM_CFG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LLM_CFG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

# ── Palette ───────────────────────────────────────────────────────────
C = {
    "bg"      : "#141414",
    "surface" : "#1E1E1E",
    "panel"   : "#252525",
    "panel2"  : "#2A2A2A",
    "border"  : "#383838",
    "accent"  : "#3B82F6",
    "green"   : "#22C55E",
    "red"     : "#EF4444",
    "amber"   : "#F59E0B",
    "text1"   : "#F1F5F9",
    "text2"   : "#94A3B8",
    "text3"   : "#8391A3",          # hints, placeholders, secondary text (was #4B5563: too dark on the dark panels)
    "toolbar" : "#161616",
}

def po(left=0, top=0, right=0, bottom=0):
    return ft.Padding(left=left, top=top, right=right, bottom=bottom)

def br(r):
    return ft.BorderRadius(r, r, r, r)

# ── the APIs ticked in the APIs dialog (kept between runs of the program) ──────────────────────────
APIS_SEL_FILE = Path(_os.environ.get("APPDATA", str(Path.home()))) / "WorkflowPad" / "prompt_lab_apis.json"

def load_api_selection() -> list[str]:
    data, _problem = _export_util.read_json_safe(APIS_SEL_FILE)
    items = data.get("selected") if isinstance(data, dict) else None
    out: list[str] = []
    for f in items if isinstance(items, list) else []:
        if isinstance(f, str) and f and f not in out:
            out.append(f)
    return out

def save_api_selection(files: list[str]) -> None:
    try:
        _export_util.write_json_atomic(APIS_SEL_FILE, {"selected": list(files)}, keep_backup=False, ensure_ascii=False, indent=2)
    except OSError:
        pass

def api_file_info(file: str) -> tuple[str, str]:
    """(status text, level 'ok'|'bad') of a workflow file for the APIs dialog."""
    p = API_DIR / file
    if not p.is_file():
        return "Missing workflow", "bad"
    try:
        wf = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(wf, dict):
            raise ValueError("not an object")
    except Exception:
        return "Can't read workflow", "bad"
    nodes = [v for v in wf.values() if isinstance(v, dict)]
    has_prompt = any(n.get("class_type", "") in _PCLS for n in nodes)
    has_seed = any(any("seed" in k.lower() for k in (n.get("inputs") or {})) for n in nodes)
    return ("Prompt ✓" if has_prompt else "No prompt input") + " · " + ("Seed ✓" if has_seed else "Seed fixed"), ("ok" if has_prompt else "bad")

# ── scan_apis ─────────────────────────────────────────────────────────
def scan_apis() -> list[str]:
    return sorted(p.name for p in API_DIR.glob("*.json")
                  if "project" not in p.name and "editor" not in p.name
                  and "prompts_tester" not in p.name
                  and "llm_chat_config" not in p.name)

# ── ComfyUI helpers ───────────────────────────────────────────────────
def comfy_queue(wf: dict, cid: str) -> str:
    clean_wf = {k: v for k, v in wf.items() if isinstance(v, dict) and "class_type" in v}
    req = urllib.request.Request(
        f"http://{COMFY_HOST}/prompt",
        data=json.dumps({"prompt": clean_wf, "client_id": cid}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["prompt_id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:300]}")

def comfy_hist(pid: str) -> dict:
    with urllib.request.urlopen(f"http://{COMFY_HOST}/history/{pid}", timeout=10) as r:
        return json.loads(r.read())

def comfy_cancel(pid: str) -> None:
    """Cancel a job: remove it from the queue and, if it is the one running, interrupt it."""
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"http://{COMFY_HOST}/queue", data=json.dumps({"delete": [pid]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST"), timeout=5)
    except Exception:
        pass
    running = None
    try:
        with urllib.request.urlopen(f"http://{COMFY_HOST}/queue", timeout=5) as r:
            running = [it[1] for it in json.loads(r.read()).get("queue_running", []) if len(it) > 1]
    except Exception:
        pass
    if running is None or pid in running:   # never interrupt somebody else's job
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"http://{COMFY_HOST}/interrupt", data=b"",
                headers={"Content-Type": "application/json"}, method="POST"), timeout=5)
        except Exception:
            pass

_ACTIVE_PID: list = [None]     # prompt_id of the job being waited on (so Stop can cancel it at once)

def comfy_get_img(fn, sub, ft_) -> bytes:
    p = urllib.parse.urlencode({"filename": fn, "subfolder": sub, "type": ft_})
    with urllib.request.urlopen(f"http://{COMFY_HOST}/view?{p}", timeout=30) as r:
        return r.read()

# ── Inject helpers ────────────────────────────────────────────────────
_PCLS = ["CLIPTextEncodeFlux","CLIPTextEncodeSD3","CLIPTextEncode","smZ CLIPTextEncode"]
_SCLS = ["RandomNoise","KSamplerAdvanced","KSampler","SamplerCustomAdvanced"]
_SF   = ["noise_seed","seed","rand_seed"]
_LATENT_CLS = ["EmptyLatentImage","EmptySD3LatentImage","EmptyHunyuanLatentVideo","EmptyLatentImagePresets"]

def inject_prompt(wf: dict, prompt_text: str) -> dict:
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        ct  = node.get("class_type", "")
        inp = node.get("inputs", {})
        if ct in _PCLS:
            title = (node.get("_meta", {}).get("title", "") or "").lower()
            if not any(h in title for h in ("neg", "negative")):
                for f in ("text", "prompt", "positive"):
                    if f in inp and isinstance(inp[f], str):
                        wf[nid]["inputs"][f] = prompt_text
                        return wf
    return wf

def inject_size(wf: dict, width: int, height: int, steps: int) -> dict:
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        ct  = node.get("class_type", "")
        inp = node.get("inputs", {})
        if ct in _LATENT_CLS:
            if "width"  in inp: wf[nid]["inputs"]["width"]  = width
            if "height" in inp: wf[nid]["inputs"]["height"] = height
        if ct in _SCLS:
            if "steps" in inp: wf[nid]["inputs"]["steps"] = steps
    return wf

def inject_seed(wf: dict, fixed_seed: int = -1) -> tuple[dict, int]:
    """Inject the seed; if fixed_seed=-1, a new random seed is used and the function returns (wf, used_seed)."""
    seed = fixed_seed if fixed_seed >= 0 else random.randint(0, 2**32 - 1)
    injected = []
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        ct  = node.get("class_type", "")
        inp = node.get("inputs", {})
        # Find seed fields in every node that has the seed keyword
        seed_fields = [k for k in inp if any(s in k.lower() for s in ("seed", "noise_seed", "rand_seed"))]
        if ct in _SCLS or seed_fields:
            for sf in (seed_fields or _SF):
                if sf in inp and isinstance(inp[sf], (int, float)):
                    wf[nid]["inputs"][sf] = seed
                    injected.append(f"node={nid} class={ct} field={sf}")
    return wf, seed

# ── JSON helpers ──────────────────────────────────────────────────────
def new_prompt_id() -> str:
    return uuid.uuid4().hex[:12]


def ensure_prompt_ids(prompts: list[dict]) -> bool:
    """Every prompt has its own stable id (older files have none; a duplicated id is replaced). True when something was added or changed. The id, not the position in the list, is what an operation that
    finishes later (an LLM answer, a finished image) uses to find its prompt: positions move when a prompt above it is deleted."""
    seen: set[str] = set()
    changed = False
    for p in prompts:
        pid = p.get("id")
        if not isinstance(pid, str) or not pid or pid in seen:
            p["id"] = new_prompt_id()
            changed = True
        seen.add(p["id"])
    return changed


def find_prompt_index(prompts: list[dict], pid) -> int:
    """The current position of the prompt with this id, or -1 when it no longer exists (it was deleted)."""
    if not pid:
        return -1
    for i, p in enumerate(prompts):
        if p.get("id") == pid:
            return i
    return -1


def apply_llm_prompt(prompts: list[dict], target_id, text: str) -> tuple[str, int]:
    """Put an LLM answer into ITS prompt. ('updated', index) for the prompt the question was about, ('created', index) when the question was asked with no prompt selected (a new prompt is added), and
    ('target_deleted', -1) when that prompt was deleted while the LLM was working: nothing is written, so no other prompt is ever changed."""
    text = (text or "").strip()
    if target_id is None:
        prompts.append({"id": new_prompt_id(), "name": "LLM prompt", "text": text})
        return "created", len(prompts) - 1
    i = find_prompt_index(prompts, target_id)
    if i < 0:
        return "target_deleted", -1
    prompts[i]["text"] = text
    return "updated", i


LOAD_NOTICES: list[str] = []          # what the person must be told at start (a damaged data file); shown once the window is up
_IDS_UNSAVED: list = [False]          # True while ids made at load time exist only in memory (the prompt file could not be written): the image list must not refer to them


def _preserve_damaged(path: Path) -> str:
    """Keep a damaged data file under its own name (<name>.corrupt-<time>) before anything can overwrite it. Returns the new file name, or '' when even that copy fails."""
    try:
        keep = _export_util.unique_dest(path.parent, f"{path.stem}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}")     # a second damaged copy in the same second gets _1, never overwrites the first
        shutil.copy2(str(path), str(keep))
        return keep.name
    except OSError:
        return ""


def load_prompts() -> list[dict]:
    data, problem = _export_util.read_json_safe(PROMPTS_FILE)
    if problem == "missing":
        return []                                           # first start: no file yet is normal
    if problem or not isinstance(data, list):
        # A damaged file is NOT "no prompts": keep it, try the last good backup, and say so. Starting empty here would let the next save replace the damaged file for good.
        kept = _preserve_damaged(PROMPTS_FILE)
        bak, bak_problem = _export_util.read_json_safe(str(PROMPTS_FILE) + ".bak")
        if not bak_problem and isinstance(bak, list):
            LOAD_NOTICES.append("The prompt list file was damaged. It was restored from the last good backup" + (f"; the damaged file is kept as {kept}." if kept else "."))
            data = bak
        else:
            LOAD_NOTICES.append("The prompt list file was damaged and no backup could be used, so the list starts empty" + (f". The damaged file is kept as {kept}." if kept else "."))
            return []
    out = []
    for p in data:
        if isinstance(p, dict):
            p.setdefault("name", "")
            p.setdefault("text", "")
            out.append(p)
    if ensure_prompt_ids(out):                              # an older file has no ids: write them NOW (the old file stays as .bak), so the image list can refer to them and still find the prompts after a restart
        try:
            save_prompts(out)
        except OSError as ex:
            _IDS_UNSAVED[0] = True
            LOAD_NOTICES.append(f"The prompt list could not be updated on disk ({ex}); images made now are not saved to the image list until it can.")
    return out

def thumb_to_bytes(b64str: str) -> bytes:
    return base64.b64decode(b64str)

def save_prompts(prompts: list[dict]):
    """Atomic: the file is the old list or the new one, never half of it; the last good copy is kept as .bak. A failed save raises and leaves the old file untouched."""
    _export_util.write_json_atomic(PROMPTS_FILE, prompts, keep_backup=True, ensure_ascii=False, indent=2)
    _IDS_UNSAVED[0] = False

_PROMPT_LIST: list = [[]]            # the live prompt list (set by main): the image tabs are translated between list positions (the screen) and prompt ids (the file) with it
_ORPHAN_IMAGES: dict = {}            # image entries found in the file whose prompt is not in the list right now: written back untouched, never dropped


def _clean_entry(e: dict, n: int):
    if not isinstance(e, dict) or not Path(str(e.get("path", ""))).exists():
        return None                                                          # a missing image file is skipped, as before
    e.setdefault("seed", -1)                                                 # an older file has no seed / label / settings: the fields the screen reads get a default
    e.setdefault("label", f"#{n}")
    return e


def load_img_tabs(prompts: list | None = None) -> dict[int, list]:
    """Load the persisted image tabs as {position in the prompt list: [entries]}. The file is keyed by prompt id (older files by position: they are migrated with the current list). Entries whose prompt
    is not in the list are kept aside (_ORPHAN_IMAGES) and written back by save_img_tabs, so nothing is lost when the list and this file disagree. Missing image files are skipped."""
    prompts = _PROMPT_LIST[0] if prompts is None else prompts
    _ORPHAN_IMAGES.clear()
    raw, problem = _export_util.read_json_safe(IMAGES_FILE)
    if problem == "missing":
        return {}
    if problem or not isinstance(raw, dict):
        kept = _preserve_damaged(IMAGES_FILE)
        bak, bak_problem = _export_util.read_json_safe(str(IMAGES_FILE) + ".bak")
        LOAD_NOTICES.append("The image list file was damaged. " + ("It was restored from the last good backup" if not bak_problem and isinstance(bak, dict) else "No backup could be used, so the image list starts empty")
                            + (f"; the damaged file is kept as {kept}." if kept else "."))
        if bak_problem or not isinstance(bak, dict):
            return {}
        raw = bak
    index_of = {p.get("id"): i for i, p in enumerate(prompts) if p.get("id")}
    by_id: dict = {}
    legacy_positions = "by_id" not in raw
    if legacy_positions:                                                      # older file: {"<position>": [entries]}
        for k, entries in raw.items():
            try:
                pos = int(k)
            except (TypeError, ValueError):
                continue
            if 0 <= pos < len(prompts) and prompts[pos].get("id"):
                by_id.setdefault(prompts[pos]["id"], []).extend(entries if isinstance(entries, list) else [])
            else:
                _ORPHAN_IMAGES.setdefault(f"pos{pos}", []).extend(entries if isinstance(entries, list) else [])
    else:
        for pid, entries in (raw.get("by_id") or {}).items():
            by_id[pid] = entries if isinstance(entries, list) else []
        for key, entries in (raw.get("unmapped") or {}).items():
            entries = entries if isinstance(entries, list) else []
            if key in index_of:                                               # the prompt is back in the list: its images come back with it
                by_id.setdefault(key, []).extend(entries)
            else:
                _ORPHAN_IMAGES.setdefault(key, []).extend(entries)
    result: dict = {}
    for pid, entries in by_id.items():
        if pid not in index_of:
            _ORPHAN_IMAGES.setdefault(pid, []).extend(e for e in entries if isinstance(e, dict))
            continue
        valid = []
        for e in entries:
            c = _clean_entry(e, len(valid) + 1)
            if c is not None:
                valid.append(c)
        if valid:
            result[index_of[pid]] = valid
    return result


def save_img_tabs(img_dict: dict, prompts: list | None = None):
    """Save the image tabs, keyed by prompt id. img_dict is {position: [entries]}; the id of each position comes from `prompts` (default: the live list). A position with no prompt, and the entries that
    were found for prompts not in the list, go under 'unmapped' untouched. Keeps everything the screen needs to show an image again AND to reuse its settings (workflow, size, steps)."""
    prompts = _PROMPT_LIST[0] if prompts is None else prompts
    if _IDS_UNSAVED[0]:
        save_prompts(prompts)        # the ids this file is keyed by must be on disk first; when that fails this raises and no mapping is written
    keep = ("path", "seed", "label", "api", "size", "steps")

    def ser(entries):
        return [{**{f: e[f] for f in keep if f in e}, "seed": e.get("seed", -1), "label": e.get("label", "")} for e in entries if isinstance(e, dict) and "path" in e]

    by_id: dict = {}
    unmapped: dict = {k: list(v) for k, v in _ORPHAN_IMAGES.items()}
    for pos, entries in img_dict.items():
        try:
            pos = int(pos)
        except (TypeError, ValueError):
            continue
        pid = prompts[pos].get("id") if 0 <= pos < len(prompts) else None
        if pid:
            by_id[pid] = ser(entries)
            unmapped.pop(pid, None)
        else:
            unmapped.setdefault(f"pos{pos}", []).extend(ser(entries))
    out = {"version": 2, "by_id": by_id, "unmapped": {k: ser(v) for k, v in unmapped.items() if v}}
    _export_util.write_json_atomic(IMAGES_FILE, out, keep_backup=True, ensure_ascii=False, indent=2)


_IMG_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def image_save_path(chosen: str, data: bytes) -> str:
    """The file name to save `data` under: its extension always matches the bytes (a JPEG is never written as .png). A typed image extension that does not match is replaced, a missing one is added."""
    ext = _export_util.image_ext(data, ".png")
    p = Path(chosen)
    suffix = p.suffix.lower()
    if suffix == ext or (ext == ".jpg" and suffix == ".jpeg"):
        return chosen
    return str(p.with_suffix(ext)) if suffix in _IMG_SUFFIXES else chosen + ext


_EDITOR_MIN_H = 130          # the prompt editor always keeps this much height
_CHAT_MIN_H = 150            # the chat panel needs room for its tabs, the history and the input row with the Send button


def llm_height_limits(window_h: float, mode: str) -> tuple[float, float]:
    """(smallest, largest) height of the chat panel for this window: the title bar, the tab bar of the narrow layouts and the editor's own minimum height (name, find/replace, the editor) are kept free.
    Dragging the divider and resizing the window both use it, so the chat can never push the editor or its own input row out of the window."""
    reserved = 40 + (48 if mode != "wide" else 0) + 150 + _EDITOR_MIN_H
    return float(_CHAT_MIN_H), float(max(_CHAT_MIN_H, window_h - reserved))


_GEN_MIN = 560               # the generation panel keeps at least this much width in the three-column layout
_MID_MIN = 320               # ... the editor column at least this much
_LEFT_MIN, _LEFT_MAX = 140, 400


def wide_widths(window_w: float, left: float, mid: float) -> tuple[float, float]:
    """The widths of the prompt list and of the editor column for the three-column layout, clamped so that the generation panel keeps _GEN_MIN and the list stays between _LEFT_MIN and _LEFT_MAX.
    Dragging either divider and resizing the window all go through this."""
    left = max(_LEFT_MIN, min(_LEFT_MAX, left))
    mid_max = max(_MID_MIN, window_w - left - 10 - _GEN_MIN)          # 10 = the two divider bars
    mid = max(_MID_MIN, min(mid_max, mid))
    left = max(_LEFT_MIN, min(left, window_w - mid - 10 - _GEN_MIN))
    return left, mid


def make_thumbnail_bytes(img_bytes: bytes, max_size: int = 500) -> bytes:
    """Resize the image so it stays within max_size and return it as bytes."""
    try:
        from PIL import Image as PILImage
        import io
        img = PILImage.open(io.BytesIO(img_bytes))
        img.thumbnail((max_size, max_size), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return img_bytes

# ── async run_llm ─────────────────────────────────────────────────────
_DEFAULT_MAX_TOKENS = 8192            # Grok's thinking counts inside Max Tokens too, and a full image prompt is long

# The System Prompt this app shipped before the separator format (JSON). Compared without spaces / punctuation, so only a prompt that is still the untouched old default is treated as "the default".
_OLD_DEFAULT_SYS = (
    "You are an AI image prompt engineer for Flux/Krea.\n\n"
    "Reply in the same language the user writes in. Prompt content must be in English.\n\n"
    "Output ONLY a raw JSON object - no markdown, no code block, no text before or after:\n"
    '{"reply": "casual reply 1-2 sentences", "prompt": "full prompt in English\\nSubject: ...\\nEnvironment: ...\\nLighting: ...\\nCamera: ...\\nNegative: ..."}\n\n'
    "RULES:\n"
    "- reply: casual, 1-2 sentences, no labels\n"
    "- prompt: include only sections relevant to the image (skip irrelevant ones)\n"
    "- Each section on its own line inside the prompt string\n"
    "- Keep ALL details. Never shorten, summarize, or use placeholders\n"
    "- Only modify what user asked, keep everything else word-for-word\n"
    "- If [No prompt yet] and user just chatting: set prompt to empty string\n"
    "- Output ONLY valid JSON. Nothing else."
)


def _norm_prompt(text: str) -> str:
    return re.sub(r"\W+", "", text or "")


def _auto_upgrade_default_prompt(cfg: dict) -> str:
    """If the saved System Prompt is exactly the OLD app default (not the user's own text), keep it as a backup and switch to the new default at once; returns the notice text ('' when nothing changed).
    A custom prompt is never touched here. Nothing else is reset."""
    saved = cfg.get("system_prompt")
    if not saved or _prompt_has_contract(saved) or _norm_prompt(saved) != _norm_prompt(_OLD_DEFAULT_SYS):
        return ""
    _remember_backup(cfg, saved)
    cfg["system_prompt"] = _DEFAULT_SYS
    try:
        save_llm_cfg(cfg)
    except Exception:
        pass
    return "The default System Prompt was updated to the new answer format (REPLY / PROMPT / END). The previous one is saved as a backup (system_prompt_backup in the settings file)."


class LLMFormatError(ValueError):
    """The LLM answer does not follow the REPLY / PROMPT / END separator format; str() is the reason, .raw is the answer as received (set by the caller that has it)."""
    raw = ""


_TEMPLATE_TAIL_RE = re.compile(r"(?:\s*</?(?:end_of_turn|end_turn|start_turn|start_of_turn|eos)>)+\s*$")     # chat-template end markers a local model may leak after its answer


def _parse_llm_output(raw: str) -> tuple[str, str]:
    """Strict, structure-only parser: (reply, prompt). The answer is the three separator lines in this order, each ALONE on its line and present exactly once, with the text before the first and after the
    last ignored. The text of each section is kept exactly as the LLM wrote it (outer whitespace trimmed). REPLY must not be empty; PROMPT may be. Anything else raises LLMFormatError with the reason; nothing is
    guessed, repaired or salvaged. A chat-template marker at the very end (</end_of_turn> ...) is transport and is ignored."""
    text = _TEMPLATE_TAIL_RE.sub("", raw or "").strip()
    if not text:
        raise LLMFormatError("the answer is empty")
    lines = text.splitlines()
    if len(lines) >= 2 and re.fullmatch(r"\s*```[A-Za-z]*\s*", lines[0]) and re.fullmatch(r"\s*```\s*", lines[-1]):
        lines = lines[1:-1]                    # the whole answer wrapped in a code fence: transport, like the chat-template marker
    lines = _split_inline_separators(lines)
    pos: dict[str, int] = {}
    for name in _SEP_WORDS:
        found = [k for k, ln in enumerate(lines) if _is_sep_line(ln, name)]
        if not found:
            hint = " (check that it is written as =========== END ===========; if the answer stops in the middle of a sentence it was cut off: raise Max Tokens)" if name == "END" and pos.get("PROMPT") is not None else ""
            raise LLMFormatError(f"the {name} separator line is missing{hint}")
        if len(found) > 1:
            raise LLMFormatError(f"the {name} separator line appears {len(found)} times")
        pos[name] = found[0]
    if not (pos["REPLY"] < pos["PROMPT"] < pos["END"]):
        raise LLMFormatError("the separator lines are not in the order REPLY, PROMPT, END")
    # Only the two blocks count: REPLY -> PROMPT and PROMPT -> END. Whatever is before the REPLY line or after the END line (a greeting, <|eos|>, a chat-template token, a stray sentence) is not part of the answer and is dropped.
    reply = "\n".join(lines[pos["REPLY"] + 1:pos["PROMPT"]]).strip()
    prompt = "\n".join(lines[pos["PROMPT"] + 1:pos["END"]]).strip()
    if not reply:
        raise LLMFormatError("the REPLY section is empty")
    return reply, prompt

async def run_llm(user_text: str, cfg: dict, session_id: str, on_result, on_err, on_debug=None, wf_file: str = "gemma4_llm.json", on_bad=None):
    """Run the LLM workflow; on_result(reply, prompt) gets the parsed REPLY / PROMPT answer; a malformed answer goes to on_bad(reason, raw) and nothing is saved."""
    llm_wf_path = LLM_DIR / wf_file
    if not llm_wf_path.exists():
        await on_err(f"Could not find {llm_wf_path}")
        return
    try:
        wf = json.loads(llm_wf_path.read_text(encoding="utf-8"))
    except Exception as e:
        await on_err(f"Could not load {wf_file}: {e}")
        return

    # Inject user_text and config into LLMSessionChatNode
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        if node.get("class_type") == "LLMSessionChatNode":
            wf[nid]["inputs"]["user_text"]    = user_text
            wf[nid]["inputs"]["system_prompt"] = cfg.get("system_prompt", _DEFAULT_SYS)
            wf[nid]["inputs"]["history_dir"]   = cfg.get("history_dir", "")
            wf[nid]["inputs"]["session_id"]    = session_id
            wf[nid]["inputs"]["reset_session"] = False
            wf[nid]["inputs"]["max_tokens"]    = int(cfg.get("max_tokens", _DEFAULT_MAX_TOKENS))
            wf[nid]["inputs"]["temperature"]   = float(cfg.get("temperature", 0.7))
            wf[nid]["inputs"]["n_ctx"]         = int(cfg.get("n_ctx", 16384))

    loop = asyncio.get_event_loop()
    cid  = uuid.uuid4().hex

    # Check whether the workflow has the required nodes
    clean_check = {k: v for k, v in wf.items() if isinstance(v, dict) and "class_type" in v}
    if not clean_check:
        await on_err(f"The workflow has no valid nodes — check {wf_file}")
        return

    async def _dbg(msg: str):
        if on_debug:
            await on_debug(msg)

    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, cid)
        await _dbg(f"queued pid={pid[:8]}...")
    except Exception as e:
        await on_err(str(e))
        return

    deadline = time.time() + 300
    while time.time() < deadline:
        await asyncio.sleep(2)

        # ── poll history API ──
        try:
            hist = await loop.run_in_executor(None, comfy_hist, pid)
        except Exception:
            continue
        if pid not in hist:
            continue

        status_str = hist[pid].get("status", {}).get("status_str", "")

        # ── Read text output from PreviewAny / LLMSessionChatNode ──
        for node_id, nout in hist[pid].get("outputs", {}).items():
            raw_text = None
            texts = nout.get("text", [])
            if texts:
                raw_text = texts[0] if isinstance(texts[0], str) else str(texts[0])
            elif "string" in nout:
                raw_text = str(nout["string"])
            # debug — show in chat
            await _dbg(f"[node {node_id}] keys={list(nout.keys())} raw={repr(raw_text)[:120]}")
            if raw_text and raw_text.strip():
                await _dbg(f"RAW:\n{raw_text.strip()}")
                try:
                    t, p = _parse_llm_output(raw_text.strip())
                except LLMFormatError as exc:
                    if on_bad:
                        await on_bad(str(exc), raw_text.strip())
                    else:
                        await on_err(f"Answer format error: {exc}")
                    return
                await _dbg(f"[parse] thought={repr(t)[:60]} prompt={repr(p)[:60]}")
                await on_result(t, p)
                return

        if status_str == "success":
            all_outputs = hist[pid].get("outputs", {})
            await _dbg(f"[success] outputs keys={list(all_outputs.keys())} full={repr(str(all_outputs))[:200]}")
            await on_err("The LLM finished but produced no text output — see debug in chat")
            return
        if status_str == "error":
            errs = hist[pid].get("status", {}).get("messages", [])
            err_msg = str(errs[-1]) if errs else "unknown error"
            await _dbg(f"[error] {err_msg}")
            await on_err(f"ComfyUI error: {err_msg}")
            return

    await on_err("⏱ Timeout: the LLM did not respond within 5 minutes")

async def grok_chat_turn(history: list, user_text: str, call, sys_prompt: str):
    """One Grok turn. call(sys_prompt, messages) -> the answer text. The user message and the answer join `history` ONLY after the parser accepts the answer: a failed or malformed turn leaves the history
    exactly as it was (no wrong answer, no orphan user turn). Returns (raw answer, reply, prompt); raises LLMFormatError (with .raw) for a malformed answer."""
    user = {"role": "user", "content": user_text}
    answer = await call(sys_prompt, list(history) + [user])
    try:
        reply, prompt = _parse_llm_output(answer)
    except LLMFormatError as exc:
        exc.raw = answer
        raise
    history.append(user)
    history.append({"role": "assistant", "content": answer})
    return answer, reply, prompt


def _new_session_id() -> str:
    return uuid.uuid4().hex[:8]


def _rotate_session(state: dict) -> str:
    """A new ComfyUI session id for this model: the next call starts a fresh conversation, so a wrong answer in the old session's history cannot influence it."""
    state["session_id"] = _new_session_id()
    return state["session_id"]


def _reset_conversations(model_states: dict, grok_key: str, grok_history: list) -> list[str]:
    """After a real System Prompt change: every Local model gets a new session, the Grok history is cleared. What is already shown in the chats is not touched. Returns the Local model keys reset."""
    done = []
    for key, st in model_states.items():
        if key != grok_key:
            _rotate_session(st)
            done.append(key)
    grok_history.clear()
    return done


async def local_chat_turn(state: dict, user_text: str, cfg: dict, wf_file: str, on_result, on_err, on_debug=None, on_bad=None):
    """One Local turn in this model's session (state['session_id']). A good answer keeps the session; a malformed one is refused (on_bad gets reason + raw) and the session is rotated first."""
    async def _bad(reason: str, raw_answer: str):
        _rotate_session(state)
        if on_bad:
            await on_bad(reason, raw_answer)
        else:
            await on_err(f"Answer format error: {reason}")
    await run_llm(user_text, cfg, state["session_id"], on_result, on_err, on_debug=on_debug, wf_file=wf_file, on_bad=_bad)


_OLD_CONTRACT_TOKENS = ("JSON", '"reply"', '\\"reply\\"')      # protocol tokens of the old answer format; nothing else is ever read from a custom prompt
_MAX_REMOVED_LINES = 8


def _old_contract_lines(text: str) -> list[int]:
    """Indexes of the lines that carry an old-format protocol token (JSON / "reply")."""
    return [n for n, ln in enumerate((text or "").splitlines()) if any(t in ln for t in _OLD_CONTRACT_TOKENS)]


def _convert_system_prompt(text: str) -> tuple[str | None, list[str], str]:
    """(new text or None, removed lines, note). Removes ONLY the lines that carry an old protocol token and makes sure the prompt ends with exactly one separator Output Contract; every other line of the
    custom instructions is kept as it is. Returns None (nothing to apply, the original is untouched) when the old format cannot be separated safely: more than _MAX_REMOVED_LINES such lines, a piece of
    pretty-printed JSON left over, or a prompt that already holds the separator lines more than once."""
    lines = (text or "").splitlines()
    idx = set(_old_contract_lines(text))
    removed = [lines[n] for n in sorted(idx)]
    if len(idx) > _MAX_REMOVED_LINES:
        return None, removed, f"{len(idx)} lines mention the old format (more than {_MAX_REMOVED_LINES}); that is too many to remove safely"
    kept = [ln for n, ln in enumerate(lines) if n not in idx]
    for ln in kept:
        t = ln.strip()
        if t in ("{", "}", "},", "{{", "}}") or '"prompt":' in t or '\\"prompt\\":' in t:
            return None, removed, f"a piece of the old JSON example is left over ({t[:40]!r}); it cannot be separated safely"
    counts = [sum(1 for ln in kept if _is_sep_line(ln, n)) for n in _SEP_WORDS]
    present = [n for n, c in zip(_SEP_WORDS, counts) if c]
    counts = [c for c in counts if c]
    if present and (len(present) != 3 or any(c != 1 for c in counts)):
        return None, removed, "the prompt already holds some of the separator lines, but not exactly one of each"
    body = "\n".join(kept).rstrip()
    new = body if len(present) == 3 else (body + "\n\n" if body else "") + _OUTPUT_CONTRACT
    return new, removed, ""


def _remember_backup(cfg: dict, text: str) -> None:
    """Keep the original System Prompt before it is replaced: system_prompt_backup (the latest) and system_prompt_backups (every different one). Never deletes a backup."""
    text = (text or "").strip()
    if not text:
        return
    lst = cfg.setdefault("system_prompt_backups", [])
    if text not in lst:
        lst.append(text)
    cfg["system_prompt_backup"] = text


# ── async run_workflow ────────────────────────────────────────────────
async def run_workflow(wf: dict, on_img, on_err, on_done, stop_event=None, on_stop=None):
    cid  = uuid.uuid4().hex
    loop = asyncio.get_event_loop()
    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, cid)
        _ACTIVE_PID[0] = pid
        deadline = time.time() + 300
        while time.time() < deadline:
            if stop_event is not None and stop_event.is_set():
                await loop.run_in_executor(None, comfy_cancel, pid)
                if on_stop:
                    await on_stop()
                return
            await asyncio.sleep(2)
            hist = await loop.run_in_executor(None, comfy_hist, pid)
            if pid in hist:
                for node_out in hist[pid].get("outputs", {}).values():
                    for img_info in node_out.get("images", []):
                        raw = await loop.run_in_executor(
                            None, comfy_get_img,
                            img_info["filename"],
                            img_info.get("subfolder", ""),
                            img_info.get("type", "output"),
                        )
                        dst = TEMP_DIR / f"pt_{uuid.uuid4().hex[:8]}{_export_util.image_ext(raw, '.png')}"
                        dst.write_bytes(raw)
                        await on_img(str(dst))
                        await on_done()
                        return
        await on_err("⏱ Timeout: ComfyUI did not respond within 5 minutes")
    except Exception as e:
        await on_err(str(e))
    finally:
        _ACTIVE_PID[0] = None

# ── UI helper ─────────────────────────────────────────────────────────
def _dd(options, hint):
    return ft.Dropdown(
        options=options, hint_text=hint,
        text_size=12, height=42,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel2"], color=C["text1"],
        hint_style=ft.TextStyle(color=C["text3"]),
    )

def _btn(label, icon, color, on_click):
    return ft.Button(
        label, icon=icon, on_click=on_click,
        style=ft.ButtonStyle(
            bgcolor=color, color=C["text1"],
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=po(left=10, right=10, top=6, bottom=6),
        ),
    )

def _resize_divider(on_drag):
    """Drag bar to resize the panel."""
    return ft.GestureDetector(
        content=ft.Container(width=5, bgcolor=C["border"]),
        drag_interval=10,
        on_pan_update=on_drag,
        mouse_cursor=ft.MouseCursor.RESIZE_LEFT_RIGHT,
    )

# ── Main ──────────────────────────────────────────────────────────────
def main(page: ft.Page):
    page.title         = f"{APP_NAME} {APP_VERSION}"
    _icon = APP_ROOT / "icons" / "prompt_lab.ico"
    if _icon.exists():
        page.window.icon = str(_icon)
    page.window_width  = 1100
    page.window_height = 700
    page.window_min_width  = 860
    page.window_min_height = 520
    try:
        page.window.min_width = 860
        page.window.min_height = 520
    except Exception:
        pass
    page.padding   = 0
    page.bgcolor   = C["bg"]
    page.theme_mode = ft.ThemeMode.DARK

    prompts: list[dict] = load_prompts()
    _PROMPT_LIST[0] = prompts                       # the image tabs are saved by prompt id: they need the live list
    sel: list[int] = [-1]

    # ── Status ───────────────────────────────────────────────────────
    status_txt = ft.Text("Ready", size=11, color=C["text2"])

    def set_status(msg, color=None):
        status_txt.value = msg
        status_txt.color = color or C["text2"]
        page.update()

    # ── Left panel ───────────────────────────────────────────────────
    list_view = ft.ListView(spacing=2, expand=True)

    def _set_inputs_enabled(on: bool):
        """Editing/run controls stay disabled until a prompt is selected."""
        for c in (name_field, editor_field, find_field, replace_field, btn_replace_all,
                  chat_input, btn_ask, btn_new_run, btn_seed_run, btn_quick):
            c.disabled = not on

    def refresh_list(idx=-1):
        _set_inputs_enabled(0 <= idx < len(prompts))
        list_view.controls.clear()
        # newest first; `data=i` keeps the real index into `prompts`
        for i in reversed(range(len(prompts))):
            p = prompts[i]
            is_sel = i == idx
            list_view.controls.append(
                ft.Container(
                    content=ft.Text(
                        p["name"], size=12,
                        weight=ft.FontWeight.W_500 if is_sel else ft.FontWeight.NORMAL,
                        color=C["text1"] if is_sel else C["text2"],
                        overflow=ft.TextOverflow.ELLIPSIS, max_lines=1,
                    ),
                    padding=po(left=10, top=7, right=10, bottom=7),
                    border_radius=br(5),
                    bgcolor=C["accent"] if is_sel else C["panel"],
                    ink=True, data=i,
                    on_click=lambda e: (_select(e.control.data), _after_pick()),
                )
            )
        page.update()

    def _select(idx_val):
        sel[0] = idx_val
        name_field.value   = prompts[idx_val]["name"]
        editor_field.value = prompts[idx_val]["text"]
        refresh_list(idx_val)
        load_thumb_for_prompt(idx_val)
        imgs = _prompt_images.get(idx_val, [])
        _active_img_tab[0] = max(0, len(imgs) - 1)
        if imgs:
            _show_tab(idx_val, _active_img_tab[0])
        else:
            _show_blank()
        _rebuild_tabs()

    def on_new(_):
        name = f"Prompt {len(prompts)+1}"
        prompts.append({"id": new_prompt_id(), "name": name, "text": ""})
        save_prompts(prompts)
        sel[0] = len(prompts) - 1
        name_field.value = name
        editor_field.value = ""
        refresh_list(sel[0])
        load_thumb_for_prompt(sel[0])
        _active_img_tab[0] = 0
        _show_blank()
        _rebuild_tabs()

    def on_delete(_):
        i = sel[0]
        if i < 0 or i >= len(prompts): return
        prompts.pop(i)
        save_prompts(prompts)
        # Image history is keyed by prompt index: drop i and shift later keys down.
        remapped = {(k - 1 if k > i else k): v for k, v in _prompt_images.items() if k != i}
        _prompt_images.clear()
        _prompt_images.update(remapped)
        save_img_tabs(_prompt_images)
        ni = max(0, i-1) if prompts else -1
        sel[0] = ni
        if prompts and ni >= 0:
            _select(ni)   # also reloads that prompt's image tabs after the remap
        else:
            name_field.value = editor_field.value = ""
            refresh_list(ni)
            load_thumb_for_prompt(ni)
            _active_img_tab[0] = 0
            _show_blank()
            _rebuild_tabs()

    left_w: list[float] = [195]
    left_container = ft.Container(
        width=left_w[0],
        bgcolor=C["surface"],
        padding=po(left=10, top=12, right=10, bottom=10),
        content=ft.Column(spacing=8, expand=True, controls=[
            ft.Text("Prompt List", size=13, weight=ft.FontWeight.BOLD, color=C["text1"]),
            ft.Row([
                _btn("New",    ft.Icons.ADD,            C["accent"], on_new),
                _btn("Delete", ft.Icons.DELETE_OUTLINE, C["red"],    on_delete),
            ], spacing=6),
            ft.Divider(height=1, color=C["border"]),
            list_view,
        ]),
    )

    def on_left_drag(e: ft.DragUpdateEvent):
        left_w[0], _m = wide_widths(_win_w(), left_w[0] + e.local_delta.x, (mid_panel.width or mid_w[0]))
        left_container.width = left_w[0]
        mid_panel.width = _m
        page.update()

    # ── Mid panel ────────────────────────────────────────────────────
    name_field = ft.TextField(
        hint_text="Prompt name...",
        border=ft.InputBorder.UNDERLINE,
        border_color=C["border"], focused_border_color=C["accent"],
        color=C["text1"], hint_style=ft.TextStyle(color=C["text3"]),
        text_size=13, dense=True,
        on_change=lambda e: _autosave_name(e.control.value),
    )
    editor_field = ft.TextField(
        hint_text="Write your prompt here...",
        multiline=True, min_lines=4, expand=True,
        border=ft.InputBorder.OUTLINE,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel"], color=C["text1"],
        hint_style=ft.TextStyle(color=C["text3"]),
        text_size=13,
        on_change=lambda e: _autosave_text(e.control.value),
    )

    def _autosave_name(v):
        i = sel[0]
        if 0 <= i < len(prompts):
            prompts[i]["name"] = v
            save_prompts(prompts)
            refresh_list(i)

    def _autosave_text(v):
        i = sel[0]
        if 0 <= i < len(prompts):
            prompts[i]["text"] = v
            # auto-name เป็น "Prompt N" ถ้ายังไม่มีชื่อ
            if not prompts[i].get("name", "").strip() and v.strip():
                auto_name = f"Prompt {i + 1}"
                prompts[i]["name"] = auto_name
                name_field.value = auto_name
                name_field.update()
                refresh_list(i)
            save_prompts(prompts)

    # ── Find & Replace toolbar ───────────────────────────────────────
    find_field = ft.TextField(
        hint_text="Find...", dense=True, text_size=12,
        width=130, height=34,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel2"], color=C["text1"],
        hint_style=ft.TextStyle(color=C["text3"]),
    )
    replace_field = ft.TextField(
        hint_text="Replace...", dense=True, text_size=12,
        width=130, height=34,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel2"], color=C["text1"],
        hint_style=ft.TextStyle(color=C["text3"]),
    )
    replace_count = ft.Text("", size=10, color=C["text3"])

    def on_replace_all(_):
        find_txt = find_field.value
        repl_txt = replace_field.value
        if not find_txt:
            replace_count.value = "⚠ Enter the text you want to find"
            replace_count.color = C["amber"]
            page.update(); return
        original = editor_field.value
        count = original.count(find_txt)
        if count == 0:
            replace_count.value = "Not found"
            replace_count.color = C["text3"]
        else:
            new_text = original.replace(find_txt, repl_txt)
            editor_field.value = new_text
            replace_count.value = f"✓ Replaced {count} occurrence(s)"
            replace_count.color = C["green"]
            # autosave
            i = sel[0]
            if 0 <= i < len(prompts):
                prompts[i]["text"] = new_text
                save_prompts(prompts)
        page.update()

    btn_replace_all = ft.IconButton(
        ft.Icons.FIND_REPLACE_ROUNDED,
        icon_color=C["accent"], icon_size=18,
        tooltip="Replace All",
        on_click=on_replace_all,
        style=ft.ButtonStyle(padding=po(4,4,4,4)),
    )

    find_replace_bar = ft.Container(
        bgcolor=C["panel"],
        border_radius=br(6),
        padding=po(left=8, top=4, right=8, bottom=4),
        content=ft.Row([
            ft.Icon(ft.Icons.SEARCH, size=14, color=C["text3"]),
            find_field,
            ft.Icon(ft.Icons.ARROW_FORWARD, size=12, color=C["text3"]),
            replace_field,
            btn_replace_all,
            replace_count,
        ], spacing=4, run_spacing=4, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
    )

    # ── LLM Chat panel ───────────────────────────────────────────────
    # scan comfy_apis/llm/*.json to create model tabs
    def _scan_llm_models() -> list[str]:
        if LLM_DIR.exists():
            return sorted(p.stem for p in LLM_DIR.glob("*.json"))
        return ["gemma4_llm"]

    _LLM_MODELS = _scan_llm_models()
    if not _LLM_MODELS:
        _LLM_MODELS = ["gemma4_llm"]

    _GROK_TAB = "__grok__"   # sentinel key for the Grok tab

    # nice label for each model
    def _model_label(stem: str) -> str:
        if stem == _GROK_TAB: return "Grok"
        return stem.replace("_llm", "").replace("_", " ").title()

    _ALL_TABS = _LLM_MODELS + [_GROK_TAB]

    # separate state per model
    _active_model: list[str] = [_LLM_MODELS[0]]
    _model_states: dict = {
        m: {
            "session_id": uuid.uuid4().hex[:8],
            "chat":       ft.ListView(spacing=6, expand=True, auto_scroll=True),
            "logs":       ft.ListView(spacing=4, expand=True, auto_scroll=True),
            "busy":       False,
            "status":     "",
        }
        for m in _ALL_TABS
    }

    # Grok credentials (shared with story_generator)
    _grok_creds = _load_grok_creds()
    _grok_api_key: list[str] = [_grok_creds.get("grok_api_key", "")]
    _grok_model:   list[str] = [_grok_creds.get("grok_model", "grok-4-latest")]
    _grok_history: list[list] = [[]]   # conversation history [{"role","content"}]

    llm_cfg: list[dict] = [load_llm_cfg()]
    _sys_notice: list[str] = [_auto_upgrade_default_prompt(llm_cfg[0])]       # shown in the Settings page
    _thumb_cb: list = [None]

    # shared UI
    chat_history  = _model_states[_active_model[0]]["chat"]
    chat_input = ft.TextField(
        hint_text="Ask the LLM or edit the prompt... (Enter to send, Shift+Enter for a new line)",
        multiline=True, min_lines=1, max_lines=4,
        shift_enter=True,
        expand=True,
        border=ft.InputBorder.OUTLINE,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel"], color=C["text1"],
        hint_style=ft.TextStyle(color=C["text3"]),
        text_size=12,
    )
    llm_status = ft.Text("", size=10, color=C["text3"])
    llm_progress = ft.ProgressRing(width=14, height=14, color=C["accent"], visible=False)

    def _refresh_list(lv, holder=None, model=None):
        """Show a new bubble / log line at once. The visible container is pointed at this model's list again when that model's tab is the open one (a list swapped in by a tab switch, e.g. Grok, otherwise
        showed new lines only after switching tabs again), then the container and the list itself are updated."""
        try:
            if holder is not None and model == _active_model[0] and holder.content is not lv:
                holder.content = lv
            if holder is not None:
                holder.update()
        except Exception:
            pass
        try: lv.update()
        except Exception: pass

    def _add_chat_bubble(role: str, text: str, color: str, model: str | None = None):
        is_user = role == "user"
        model = model or _active_model[0]
        ch = _model_states[model]["chat"]
        print(f"[CHAT BUBBLE] role={role} model={model} ch_controls={len(ch.controls)}")
        if is_user and len(ch.controls) > 0:
            ch.controls.append(
                ft.Container(height=1, bgcolor=C["border"],
                             margin=ft.Margin(left=0, right=0, top=8, bottom=8))
            )
        ch.controls.append(
            ft.Container(
                content=ft.Column([
                    ft.Text(
                        "You" if is_user else f"🤖 {_model_label(model)}",
                        size=10, color=C["text3"], weight=ft.FontWeight.W_500
                    ),
                    ft.Text(text, size=12, color=C["text1"], selectable=True),
                ], spacing=2, tight=True),
                bgcolor=color,
                border_radius=br(6),
                padding=po(left=10, top=6, right=10, bottom=6),
                margin=ft.Margin(
                    left=40 if is_user else 0,
                    right=0 if is_user else 40,
                    top=0, bottom=2
                ),
            )
        )
        _refresh_list(ch, chat_view, model)
        page.update()

    def _set_model_status(m: str, text: str, color: str, busy: bool | None = None):
        """Busy/status belong to each model tab; the shared widgets only mirror the active one."""
        st = _model_states[m]
        if busy is not None:
            st["busy"] = busy
        st["status"] = (text, color)
        if m == _active_model[0]:
            llm_status.value = text
            llm_status.color = color
            llm_progress.visible = st["busy"]
        page.update()

    async def _do_ask_llm(_=None):
        # Surface any exception from the handler — Flet swallows it otherwise,
        # which looks like "nothing happens, no logs".
        try:
            await _do_ask_llm_inner()
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            print(f"[ASK ERROR] {tb}", file=sys.stderr)
            _set_model_status(_active_model[0], f"❌ {type(exc).__name__}: {exc}", C["red"], busy=False)

    async def _do_ask_llm_inner():
        print(f"[ASK] model={_active_model[0]!r} busy={_model_states[_active_model[0]]['busy']} text={chat_input.value!r}")
        if _model_states[_active_model[0]]["busy"]:
            _set_model_status(_active_model[0], "⏳ Still waiting for the previous request", C["amber"])
            return
        question = chat_input.value.strip()
        if not question:
            return
        if not _prompt_has_contract(llm_cfg[0].get("system_prompt", _DEFAULT_SYS)):
            # An old saved System Prompt (JSON) cannot produce answers this program reads. It is NOT overwritten here: say so and stop; Settings offers the new default (old text kept as backup).
            _msg = "The saved System Prompt does not use the new answer format (REPLY / PROMPT / END separator lines). Nothing was sent. Open ⚙ Settings and press 'Use new default prompt' (your current text is kept as a backup), or add the format to your own prompt."
            _add_chat_bubble("llm", "⚠ " + _msg, C["panel"])
            _set_model_status(_active_model[0], "❌ System Prompt needs the new format (see ⚙ Settings)", C["red"], busy=False)
            return
        current_prompt = editor_field.value.strip()
        # Send the current prompt only when it exists
        if current_prompt:
            combined = f"Current prompt:\n{current_prompt}\n\n---\nInstruction/Question:\n{question}"
        else:
            combined = f"[No prompt yet]\n\nUser: {question}"

        # snapshot the selection first — prevent the user from changing the prompt while waiting for the LLM
        target_id = prompts[sel[0]].get("id") if 0 <= sel[0] < len(prompts) else None     # the prompt's id, not its position: the list can change while the LLM works
        # Pin chat/log output to the tab the question was sent from, so a reply that
        # arrives after switching tabs doesn't land in (or vanish from) the wrong one.
        _sent_model = _active_model[0]
        def _pin_bubble(role, text, color): _add_chat_bubble(role, text, color, model=_sent_model)
        def _pin_log(label, text, color): _add_log(label, text, color, model=_sent_model)

        chat_input.value = ""
        _pin_bubble("user", question, C["panel2"])
        _pin_log("→ SENT", combined, C["text2"])
        _set_model_status(_sent_model, "⏳ Asking the LLM...", C["amber"], busy=True)

        async def on_result(thought: str, prompt: str):
            _set_model_status(_sent_model, "✅ Done", C["green"], busy=False)
            chat_text = thought if thought else prompt if prompt else "(No response)"
            _pin_bubble("llm", chat_text, C["panel"])
            if prompt:
                _pin_log("← PROMPT", prompt, C["accent"])
            if prompt and prompt.strip():
                outcome, i = apply_llm_prompt(prompts, target_id, prompt)
                if outcome == "target_deleted":
                    # the prompt this answer was for is gone: nothing is written anywhere else; the answer stays in the chat
                    set_status("⚠ The prompt was deleted while the LLM was working: the answer was not applied (it is in the chat)", C["amber"])
                else:
                    if outcome == "created":
                        # There is no prompt selected yet — a new one was created
                        sel[0] = i
                        refresh_list(i)
                    save_prompts(prompts)
                    # Update the editor only when the selected prompt is still the same one
                    if sel[0] == i:
                        editor_field.value = prompt.strip()
                        _autosave_text(prompt.strip())
            page.update()

        async def on_bad(reason: str, raw_answer: str):
            """The answer did not follow the format: nothing is saved or changed; the reason and the RAW answer are shown."""
            _set_model_status(_sent_model, f"❌ Answer format error: {reason}", C["red"], busy=False)
            _pin_bubble("llm", f"⚠ The answer was not accepted ({reason}). The prompt box was not changed, and the wrong answer was not kept in the conversation memory"
                               + (" (a new session starts with your next message)." if _sent_model != _GROK_TAB else ".") + f"\n\nRAW answer:\n{raw_answer[:3000]}", C["panel"])
            _pin_log("❌ FORMAT", f"{reason}\n\nRAW:\n{raw_answer}", C["red"])

        async def on_err(msg: str):
            _set_model_status(_sent_model, f"❌ {msg}", C["red"], busy=False)
            _pin_log("❌ ERROR", msg, C["red"])

        async def _on_debug(msg: str):
            _pin_log("DEBUG", msg, C["text3"])

        print(f"[GROK DEBUG] active_model={_active_model[0]!r} GROK_TAB={_GROK_TAB!r} match={_active_model[0] == _GROK_TAB}")
        if _active_model[0] == _GROK_TAB:
            print(f"[GROK DEBUG] api_key set={bool(_grok_api_key[0])}")
            if not _grok_api_key[0]:
                await on_err("Grok API key not set — go to Settings (⚙)"); return
            async def _run_grok():
                try:
                    sys_prompt = llm_cfg[0].get("system_prompt", _DEFAULT_SYS)
                    print(f"[GROK DEBUG] calling API model={_grok_model[0]}")
                    _pin_log("→ GROK", f"model={_grok_model[0]}", C["text2"])

                    async def _call(sp, msgs):
                        return await _call_grok(
                            _grok_api_key[0], _grok_model[0], sp, msgs,
                            max_tokens=int(llm_cfg[0].get("max_tokens", _DEFAULT_MAX_TOKENS)),
                            temperature=float(llm_cfg[0].get("temperature", 0.7)),
                        )
                    try:
                        # the history gets this turn only when the answer is accepted: a wrong answer or a failed call leaves it exactly as it was
                        reply, t, p = await grok_chat_turn(_grok_history[0], combined, _call, sys_prompt)
                    except LLMFormatError as fmt:
                        await on_bad(str(fmt), fmt.raw)
                        return
                    _pin_log("← GROK RAW", reply[:500], C["text2"])
                    await on_result(t, p)
                except Exception as exc:
                    import traceback
                    tb = traceback.format_exc()
                    print(f"[GROK ERROR] {exc}\n{tb}")
                    await on_err(f"{exc}\n{tb[-400:]}")
            asyncio.create_task(_run_grok())
        else:
            _wf_file = f"{_sent_model}.json"
            asyncio.create_task(local_chat_turn(_model_states[_sent_model], combined, llm_cfg[0], _wf_file, on_result, on_err, on_debug=_on_debug, on_bad=on_bad))

    chat_input.on_submit = _do_ask_llm

    btn_ask = ft.IconButton(
        ft.Icons.SEND_ROUNDED,
        icon_color=C["accent"],
        tooltip="Send the question to the LLM (Enter)",
        on_click=_do_ask_llm,
        style=ft.ButtonStyle(padding=po(6,6,6,6)),
    )

    def _reset_session(_=None):
        new_sid = uuid.uuid4().hex[:8]
        _model_states[_active_model[0]]["session_id"] = new_sid
        _model_states[_active_model[0]]["chat"].controls.clear()
        _model_states[_active_model[0]]["logs"].controls.clear()
        if _active_model[0] == _GROK_TAB:
            _grok_history[0].clear()
        _set_model_status(_active_model[0], "🔄 New session", C["text3"])

    btn_clear_chat = ft.IconButton(
        ft.Icons.DELETE_SWEEP_ROUNDED,
        icon_color=C["text3"], icon_size=16,
        tooltip="Clear chat and start a new session (forget all history)",
        on_click=_reset_session,
        style=ft.ButtonStyle(padding=po(4,4,4,4)),
    )

    # ── Config Dialog ─────────────────────────────────────────────────
    cfg_sys_field = ft.TextField(
        value=llm_cfg[0].get("system_prompt", _DEFAULT_SYS),
        multiline=True, min_lines=5, max_lines=10,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel"], color=C["text1"],
        label_style=ft.TextStyle(color=C["text2"]),
        text_size=12, expand=True,
    )
    cfg_dir_field = ft.TextField(
        value=llm_cfg[0].get("history_dir", ""),
        label="History Dir",
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel"], color=C["text1"],
        label_style=ft.TextStyle(color=C["text2"]),
        text_size=12, expand=True,
    )
    cfg_max_tokens_field = ft.TextField(
        value=str(llm_cfg[0].get("max_tokens", _DEFAULT_MAX_TOKENS)),
        label="Max Tokens (response length)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel"], color=C["text1"],
        label_style=ft.TextStyle(color=C["text2"]),
        text_size=12,
    )
    cfg_temperature_field = ft.TextField(
        value=str(llm_cfg[0].get("temperature", 0.7)),
        label="Temperature (0.1-1.5)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel"], color=C["text1"],
        label_style=ft.TextStyle(color=C["text2"]),
        text_size=12,
    )
    cfg_nctx_field = ft.TextField(
        value=str(llm_cfg[0].get("n_ctx", 16384)),
        label="n_ctx (context window)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel"], color=C["text1"],
        label_style=ft.TextStyle(color=C["text2"]),
        text_size=12,
    )

    cfg_sys_note = ft.Text("", size=10, color=C["amber"], visible=False)
    cfg_sys_notice = ft.Text("", size=10, color=C["accent"], visible=False)
    cfg_sys_chip_text = ft.Text("", size=10, weight=ft.FontWeight.W_600, color=C["green"])
    cfg_sys_chip = ft.Container(content=cfg_sys_chip_text, padding=po(left=9, top=2, right=9, bottom=2), border_radius=20, bgcolor="#12301F")

    def _sys_note_refresh():
        txt = cfg_sys_field.value
        has = _prompt_has_contract(txt)
        mixed = has and bool(_old_contract_lines(txt))
        if not has:
            cfg_sys_chip_text.value, cfg_sys_chip_text.color, cfg_sys_chip.bgcolor = "Old format", C["amber"], "#33260A"
            cfg_sys_note.value = ("This System Prompt has no REPLY / PROMPT / END lines, so chat will not send. 'Convert my prompt' keeps your own instructions "
                                  "(it removes only the lines that mention JSON / \"reply\" and adds the new format); your current text is saved as a backup first.")
        elif mixed:
            cfg_sys_chip_text.value, cfg_sys_chip_text.color, cfg_sys_chip.bgcolor = "Mixed formats", C["amber"], "#33260A"
            cfg_sys_note.value = "The new format is there, but some lines still mention the old one (JSON / \"reply\"). Remove them, or press 'Convert my prompt'."
        else:
            cfg_sys_chip_text.value, cfg_sys_chip_text.color, cfg_sys_chip.bgcolor = "New format ✓", C["green"], "#12301F"
            cfg_sys_note.value = ""
        cfg_sys_note.visible = bool(cfg_sys_note.value)
        cfg_sys_notice.value = _sys_notice[0]
        cfg_sys_notice.visible = bool(_sys_notice[0])

    def _use_new_default(_):
        old = cfg_sys_field.value.strip()
        if old and old != _DEFAULT_SYS.strip():
            _remember_backup(llm_cfg[0], old)                 # kept, never lost
            save_llm_cfg(llm_cfg[0])
        cfg_sys_field.value = _DEFAULT_SYS
        _sys_note_refresh()
        cfg_sys_note.value = ("The default prompt is in the box. Your own instructions are NOT in it (they are saved as a backup in the settings file: system_prompt_backup). "
                              "Press Save to use it.")
        cfg_sys_note.visible = True
        page.update()

    def _convert_my_prompt(_):
        original = cfg_sys_field.value
        new, removed, why = _convert_system_prompt(original)
        if new is None:
            # nothing is changed: the text stays in the box, untouched; the user is told where the old format is and edits it by hand
            listing = "\n".join(f"  - {ln.strip()[:90]}" for ln in removed[:10])
            cfg_sys_note.value = (f"Could not convert automatically: {why}. Nothing was changed. Edit the System Prompt by hand: remove the old JSON instructions"
                                  + (" (lines that mention JSON / \"reply\":\n" + listing + ")" if listing else "") + " and add the REPLY / PROMPT / END format.")
            cfg_sys_note.visible = True
            page.update()
            return
        _remember_backup(llm_cfg[0], original)                 # the original is saved BEFORE the box changes
        save_llm_cfg(llm_cfg[0])
        cfg_sys_field.value = new
        listing = "\n".join(f"  - {ln.strip()[:90]}" for ln in removed)
        cfg_sys_note.value = (f"Converted: {len(removed)} old-format line(s) removed" + (":\n" + listing if listing else "") + "\nThe new format was added once at the end. "
                              "Your original text is saved as a backup in the settings file. Review the text, then press Save.")
        cfg_sys_note.visible = True
        page.update()

    def _open_llm_config(_):
        cfg_sys_field.value         = llm_cfg[0].get("system_prompt", _DEFAULT_SYS)
        _sys_note_refresh()
        cfg_dir_field.value         = llm_cfg[0].get("history_dir", "")
        cfg_max_tokens_field.value  = str(llm_cfg[0].get("max_tokens", _DEFAULT_MAX_TOKENS))
        cfg_temperature_field.value = str(llm_cfg[0].get("temperature", 0.7))
        cfg_nctx_field.value        = str(llm_cfg[0].get("n_ctx", 16384))
        cfg_grok_key_field.value    = _grok_api_key[0]
        cfg_grok_model_dd.value     = _grok_model[0]
        _switch_cfg_tab("local")   # always open on Local LLM tab
        config_dialog.open = True
        page.update()

    def _save_llm_config(_):
        _old_sys = llm_cfg[0].get("system_prompt", _DEFAULT_SYS)
        llm_cfg[0]["system_prompt"] = cfg_sys_field.value.strip() or _DEFAULT_SYS
        _sys_changed = llm_cfg[0]["system_prompt"].strip() != (_old_sys or "").strip()
        llm_cfg[0]["history_dir"]   = cfg_dir_field.value.strip()
        try:
            llm_cfg[0]["max_tokens"] = int(cfg_max_tokens_field.value.strip())
        except ValueError:
            llm_cfg[0]["max_tokens"] = _DEFAULT_MAX_TOKENS
        try:
            llm_cfg[0]["temperature"] = float(cfg_temperature_field.value.strip())
        except ValueError:
            llm_cfg[0]["temperature"] = 0.7
        try:
            llm_cfg[0]["n_ctx"] = int(cfg_nctx_field.value.strip())
        except ValueError:
            llm_cfg[0]["n_ctx"] = 16384
        save_llm_cfg(llm_cfg[0])
        # Save Grok credentials to shared StoryGenerator credentials.json
        new_key   = cfg_grok_key_field.value.strip()
        new_model = cfg_grok_model_dd.value or "grok-4-latest"
        _grok_api_key[0] = new_key
        _grok_model[0]   = new_model
        _save_grok_creds(new_key, new_model)
        if _sys_changed:
            # the models were talking under the OLD System Prompt: start every Local session fresh and clear the Grok history; the chats on screen stay as they are
            _reset_conversations(_model_states, _GROK_TAB, _grok_history[0])
            for _m in _ALL_TABS:
                _add_chat_bubble("llm", "ℹ The System Prompt was changed: the model's conversation memory was started fresh (new session). The messages above stay on screen.", C["panel"], model=_m)
        config_dialog.open = False
        llm_status.value = "💾 Settings saved"
        llm_status.color = C["green"]
        page.update()

    _CFG_W = 740   # dialog inner content width
    _COL3  = (_CFG_W - 16) // 3   # three equal columns with 8px gaps

    cfg_max_tokens_field.width  = _COL3
    cfg_temperature_field.width = _COL3
    cfg_nctx_field.width        = _COL3

    _GROK_MODELS = ["grok-4-latest", "grok-3-latest", "grok-3-mini-latest", "grok-2-latest"]
    cfg_grok_key_field = ft.TextField(
        value=_grok_api_key[0],
        label="Grok API Key",
        password=True, can_reveal_password=True,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel"], color=C["text1"],
        label_style=ft.TextStyle(color=C["text2"]),
        text_size=12,
        expand=True,
    )
    cfg_grok_model_dd = ft.Dropdown(
        value=_grok_model[0] if _grok_model[0] in _GROK_MODELS else "grok-4-latest",
        label="Grok Model",
        options=[ft.dropdown.Option(m) for m in _GROK_MODELS],
        text_size=12, height=44,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel2"], color=C["text1"],
        label_style=ft.TextStyle(color=C["text2"]),
        expand=True,
    )

    # ── Config dialog tabs ────────────────────────────────────────────
    _cfg_tab: list[str] = ["local"]   # "local" or "grok"

    def _cfg_tab_style(active: bool):
        return ft.ButtonStyle(
            color=C["text1"] if active else C["text3"],
            bgcolor=C["panel2"] if active else "transparent",
            padding=po(left=14, top=4, right=14, bottom=4),
            shape=ft.RoundedRectangleBorder(radius=6),
        )

    cfg_tab_local_btn = ft.TextButton("Local LLM", style=_cfg_tab_style(True))
    cfg_tab_grok_btn  = ft.TextButton("Grok (xAI)", style=_cfg_tab_style(False))

    def _bd(color):
        side = ft.BorderSide(1, color)
        return ft.Border(top=side, right=side, bottom=side, left=side)

    def _hint(txt: str):
        return ft.Text(txt, size=10, color=C["text3"])

    def _card(title: str, subtitle: str, body: list, right=None):
        head = ft.Row([
            ft.Column([ft.Text(title, size=13, weight=ft.FontWeight.W_600, color=C["text1"]),
                       ft.Text(subtitle, size=10, color=C["text3"])], spacing=1, tight=True, expand=True),
            *([right] if right is not None else []),
        ], vertical_alignment=ft.CrossAxisAlignment.START)
        return ft.Container(
            content=ft.Column([head] + body, spacing=10, tight=True),
            bgcolor=C["panel"], border=_bd(C["border"]), border_radius=10,
            padding=po(left=14, top=12, right=14, bottom=12),
        )

    cfg_local_content = ft.Column([
        _card("System Prompt", "The instructions the model follows. The answer format (REPLY / PROMPT / END) must be in it.", [
            cfg_sys_field,
            cfg_sys_notice,
            cfg_sys_note,
            ft.Row([
                ft.OutlinedButton("Convert my prompt", icon=ft.Icons.AUTO_FIX_HIGH, on_click=_convert_my_prompt,
                                  tooltip="Keeps your own instructions: removes only the lines that mention JSON / \"reply\" and adds the new format. Your original is saved as a backup first."),
                ft.TextButton("Reset to default prompt", icon=ft.Icons.RESTART_ALT, on_click=_use_new_default,
                              tooltip="Puts the app's default prompt in the box. Your own instructions are NOT kept in it (they are saved as a backup).",
                              style=ft.ButtonStyle(color=C["text2"])),
            ], wrap=True, spacing=6),
        ], right=cfg_sys_chip),
        _card("Generation", "Applies to the Local models and to Grok.", [
            ft.Row([
                ft.Column([cfg_max_tokens_field, _hint("Answer length. Grok's thinking counts too: 8192 is a good default.")], spacing=4, expand=True),
                ft.Column([cfg_temperature_field, _hint("Lower = more literal, higher = more varied.")], spacing=4, expand=True),
                ft.Column([cfg_nctx_field, _hint("Context window of the local model.")], spacing=4, expand=True),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
        ]),
        _card("Session", "Where the local models keep their conversations.", [
            cfg_dir_field,
            _hint("LLM workflow files (*.json) go in the comfy_apis/llm/ folder next to this program."),
        ]),
        _card("Updates", "A notice only; nothing is downloaded or installed.", [
            _update_check.settings_row(ft, page, APP_NAME, APP_VERSION),
        ]),
    ], spacing=12, tight=True, scroll=ft.ScrollMode.AUTO, expand=True)

    cfg_grok_content = ft.Column([
        _card("Grok (xAI)", "Used by the Grok tab of the chat.", [
            cfg_grok_key_field,
            cfg_grok_model_dd,
            _hint("The key is shared with Story Generator (%APPDATA%\\StoryGenerator\\credentials.json)."),
            _hint("Max Tokens and Temperature from the Local LLM tab apply to Grok as well."),
        ]),
    ], spacing=12, tight=True, visible=False)

    cfg_body = ft.Container(
        expand=True,
        content=ft.Stack([cfg_local_content, cfg_grok_content]),
    )

    def _switch_cfg_tab(tab: str):
        _cfg_tab[0] = tab
        cfg_local_content.visible = (tab == "local")
        cfg_grok_content.visible  = (tab == "grok")
        cfg_tab_local_btn.style = _cfg_tab_style(tab == "local")
        cfg_tab_grok_btn.style  = _cfg_tab_style(tab == "grok")
        page.update()

    cfg_tab_local_btn.on_click = lambda _: _switch_cfg_tab("local")
    cfg_tab_grok_btn.on_click  = lambda _: _switch_cfg_tab("grok")

    config_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Row([ft.Text("LLM Settings", size=14, weight=ft.FontWeight.BOLD, color=C["text1"]),
                      ft.Text(f"{APP_NAME}  ·  Version {APP_VERSION}", size=11, color=C["text3"])],
                     spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=C["surface"],
        content=ft.Container(
            width=_CFG_W,
            height=580,
            content=ft.Column([
                ft.Container(
                    content=ft.Row([cfg_tab_local_btn, cfg_tab_grok_btn], spacing=4),
                    border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
                    padding=po(bottom=8),
                ),
                cfg_body,
            ], spacing=12, expand=True),
        ),
        actions=[
            ft.TextButton("Cancel", on_click=lambda _: setattr(config_dialog, 'open', False) or page.update(),
                         style=ft.ButtonStyle(color=C["text2"])),
            ft.TextButton("Save", on_click=_save_llm_config,
                         style=ft.ButtonStyle(color=C["accent"])),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(config_dialog)

    btn_llm_config = ft.IconButton(
        ft.Icons.SETTINGS_ROUNDED,
        icon_color=C["text3"], icon_size=16,
        tooltip="LLM settings (System Prompt / History Dir)",
        on_click=_open_llm_config,
        style=ft.ButtonStyle(padding=po(4,4,4,4)),
    )

    # ── LLM panel height (resizable) ──────────────────────────────────
    _llm_h: list[float] = [240.0]
    _active_view: list[str] = ["chat"]  # "chat" or "logs"

    def _add_log(label: str, text: str, color: str, model: str | None = None):
        model = model or _active_model[0]
        lg = _model_states[model]["logs"]
        lg.controls.append(
            ft.Container(
                content=ft.Column([
                    ft.Text(label, size=9, color=C["text3"], weight=ft.FontWeight.W_600),
                    ft.Text(text, size=11, color=color, selectable=True),
                ], spacing=2, tight=True),
                bgcolor=C["panel2"],
                border_radius=br(4),
                padding=po(left=8, top=4, right=8, bottom=4),
            )
        )
        _refresh_list(lg, logs_view, model)
        page.update()

    def _tab_style(active: bool):
        return ft.ButtonStyle(
            color=C["text1"] if active else C["text3"],
            bgcolor=C["panel2"] if active else "transparent",
            padding=po(left=6, top=2, right=6, bottom=2),
        )

    def _model_tab_style(active: bool):
        return ft.ButtonStyle(
            color=C["accent"] if active else C["text3"],
            bgcolor=C["panel2"] if active else "transparent",
            padding=po(left=8, top=2, right=8, bottom=2),
        )

    # content area
    chat_view = ft.Container(expand=True, border_radius=br(6),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        content=_model_states[_active_model[0]]["chat"])
    logs_view = ft.Container(expand=True, border_radius=br(6),
        clip_behavior=ft.ClipBehavior.HARD_EDGE, visible=False,
        content=_model_states[_active_model[0]]["logs"])

    tab_chat_btn = ft.TextButton("Chat", style=_tab_style(True),
        on_click=lambda _: _switch_view("chat"))
    tab_logs_btn = ft.TextButton("Logs", style=_tab_style(False),
        on_click=lambda _: _switch_view("logs"))

    def _switch_view(view: str):
        _active_view[0] = view
        chat_view.visible = (view == "chat")
        logs_view.visible = (view == "logs")
        tab_chat_btn.style = _tab_style(view == "chat")
        tab_logs_btn.style = _tab_style(view == "logs")
        page.update()

    # model tabs row
    _model_tab_btns: dict = {}
    def _switch_model(m: str):
        _active_model[0] = m
        llm_progress.visible = _model_states[m]["busy"]
        chat_view.content = _model_states[m]["chat"]
        logs_view.content = _model_states[m]["logs"]
        for k, b in _model_tab_btns.items():
            b.style = _model_tab_style(k == m)
        if _model_states[m]["busy"] and _model_states[m]["status"]:
            llm_status.value, llm_status.color = _model_states[m]["status"]
        else:
            llm_status.value = f"● {_model_label(m)}"
            llm_status.color = C["accent"]
        llm_chat_col.update()
        page.update()

    _model_tab_row_controls = []
    for _m in _ALL_TABS:
        _mbtn = ft.TextButton(
            _model_label(_m),
            style=_model_tab_style(_m == _active_model[0]),
            on_click=lambda e, m=_m: _switch_model(m),
        )
        _model_tab_btns[_m] = _mbtn
        _model_tab_row_controls.append(_mbtn)

    model_tabs_row = ft.Row(
        _model_tab_row_controls,
        spacing=2,
        scroll=ft.ScrollMode.AUTO,
    )

    llm_chat_col = ft.Column(spacing=4, expand=True, controls=[
        # row 1: model tabs
        ft.Container(
            content=model_tabs_row,
            border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
            padding=po(left=0, top=0, right=0, bottom=4),
        ),
        # row 2: view tabs + status + buttons
        ft.Row([
            tab_chat_btn,
            tab_logs_btn,
            ft.Container(expand=True),
            llm_progress,
            llm_status,
            btn_clear_chat,
            btn_llm_config,
        ], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        chat_view,
        logs_view,
        ft.Row([
            chat_input,
            btn_ask,
        ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.END),
    ])

    llm_panel = ft.Container(
        height=_llm_h[0],
        bgcolor=C["panel"],
        border_radius=br(8),
        padding=po(left=10, top=8, right=10, bottom=8),
        content=llm_chat_col,
    )

    def _on_llm_divider_drag(e: ft.DragUpdateEvent):
        # drag up = delta_y negative = increase chat panel height
        lo, hi = llm_height_limits(_win_h(), _layout["mode"] or "wide")
        _llm_h[0] = max(lo, min(hi, _llm_h[0] - e.local_delta.y))
        llm_panel.height = _llm_h[0]
        page.update()

    llm_divider = ft.GestureDetector(
        content=ft.Container(
            height=6,
            bgcolor=C["border"],
            border_radius=br(3),
            margin=ft.Margin(left=0, right=0, top=2, bottom=2),
        ),
        drag_interval=10,
        on_pan_update=_on_llm_divider_drag,
        mouse_cursor=ft.MouseCursor.RESIZE_UP_DOWN,
    )

    mid_panel = ft.Container(
        width=420,
        bgcolor=C["surface"],
        padding=po(left=14, top=12, right=14, bottom=10),
        content=ft.Column(spacing=8, expand=True, controls=[
            ft.Text("Prompt Editor", size=13, weight=ft.FontWeight.BOLD, color=C["text1"]),
            name_field,
            ft.Divider(height=1, color=C["border"]),
            find_replace_bar,
            editor_field,
            llm_divider,
            llm_panel,
        ]),
    )

    # ── Right panel ───────────────────────────────────────────────────
    sel_apis: list[str] = [f for f in load_api_selection() if (API_DIR / f).is_file()]     # the APIs a Run goes through, in this order
    apis_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)       # the APIs a Run goes through, numbered, always visible
    apis_box = ft.Container(content=apis_col, expand=True)
    apis_count = ft.Text("", size=10, color=C["text2"])
    _lvl_color = {"ok": C["text3"], "warn": C["amber"], "bad": C["red"]}

    def _apis_label():
        """Redraw the visible list of selected APIs (name, file, status) under the Add API button's header."""
        apis_col.controls.clear()
        if not sel_apis:
            apis_col.controls.append(ft.Text("No APIs selected: press Add API", size=11, color=C["amber"], italic=True))
        for i, f in enumerate(sel_apis):
            status, level = api_file_info(f)
            apis_col.controls.append(ft.Container(
                content=ft.Row([
                    ft.Container(content=ft.Text(str(i + 1), size=9, color=C["accent"], weight=ft.FontWeight.W_700),
                                 width=18, height=18, bgcolor="#1E3A5F", border_radius=br(9), alignment=ft.Alignment(0, 0)),
                    ft.Column([
                        ft.Text(Path(f).stem, size=11, color=C["text1"], weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(f"{f} · {status}", size=9, color=_lvl_color[level], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ], spacing=0, expand=True),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor=C["panel"], border_radius=br(6), tooltip=f"{Path(f).stem}\n{f}\n{status}",
                padding=po(left=6, right=6, top=4, bottom=4),
                border=ft.Border.all(1, C["red"] if level == "bad" else C["border"]),
            ))
        apis_count.value = f"{len(sel_apis)} selected" if sel_apis else ""
        try: page.update()
        except Exception: pass

    add_api_btn = ft.OutlinedButton(
        content=ft.Row([ft.Icon(ft.Icons.ADD_ROUNDED, size=13, color=C["accent"]),
                        ft.Text("Add API", size=10, color=C["accent"])], tight=True, spacing=4),
        height=28, tooltip="Choose which APIs a Run uses, and their order",
        style=ft.ButtonStyle(side=ft.BorderSide(1, C["accent"]), padding=po(left=8, right=10)))
    apis_fold_btn = ft.IconButton(ft.Icons.EXPAND_LESS_ROUNDED, icon_size=16, icon_color=C["text2"], tooltip="Collapse the APIs list", width=28, height=28, padding=0)

    def _toggle_apis(_=None):
        opened = not apis_box.visible
        apis_box.visible = opened
        api_dd.height = 168 if opened else 46
        apis_fold_btn.icon = ft.Icons.EXPAND_LESS_ROUNDED if opened else ft.Icons.EXPAND_MORE_ROUNDED
        apis_fold_btn.tooltip = "Collapse the APIs list" if opened else "Show the APIs list"
        page.update()

    apis_fold_btn.on_click = _toggle_apis
    # the APIs panel sits between the thumbnail and the controls: a header (title, count, refresh, Add API) and a list that scrolls
    api_dd = ft.Container(          # (named api_dd for the layout below)
        width=340, height=168, bgcolor=C["panel"], border_radius=br(8), border=ft.Border.all(1, C["border"]),
        padding=po(left=10, right=8, top=8, bottom=8),
        content=ft.Column([
            ft.Row([
                ft.Icon(ft.Icons.ACCOUNT_TREE_OUTLINED, size=14, color=C["accent"]),
                ft.Text("APIs", size=12, weight=ft.FontWeight.W_700, color=C["text1"]),
                apis_count,
                ft.Container(expand=True),
                apis_fold_btn,
                ft.IconButton(ft.Icons.REFRESH, icon_size=16, icon_color=C["text2"], tooltip="Refresh API list", width=28, height=28, padding=0,
                              on_click=lambda e: on_refresh_api(e)),
                add_api_btn,
            ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Divider(height=1, color=C["border"]),
            apis_box,
        ], spacing=6, expand=True),
    )
    _apis_label()

    # fixed sizes per aspect ratio (multiple of 64, sizes S/M/L/XL)
    _ALL_SIZES = [
        ("[1:1] 512 × 512",    512,  512),
        ("[1:1] 768 × 768",    768,  768),
        ("[1:1] 1024 × 1024",  1024, 1024),
        ("[1:1] 2048 × 2048",  2048, 2048),
        ("[3:2] 576 × 384",    576,  384),
        ("[3:2] 768 × 512",    768,  512),
        ("[3:2] 1536 × 1024",  1536, 1024),
        ("[3:2] 2304 × 1536",  2304, 1536),
        ("[4:3] 512 × 384",    512,  384),
        ("[4:3] 768 × 576",    768,  576),
        ("[4:3] 1024 × 768",   1024, 768),
        ("[4:3] 2048 × 1536",  2048, 1536),
        ("[16:9] 768 × 432",   768,  432),
        ("[16:9] 1280 × 720",  1280, 720),
        ("[16:9] 1920 × 1080", 1920, 1080),
        ("[16:9] 2304 × 1296", 2304, 1296),
        ("[2:3] 384 × 576",    384,  576),
        ("[2:3] 512 × 768",    512,  768),
        ("[2:3] 1024 × 1536",  1024, 1536),
        ("[2:3] 1536 × 2304",  1536, 2304),
        ("[3:4] 384 × 512",    384,  512),
        ("[3:4] 576 × 768",    576,  768),
        ("[3:4] 768 × 1024",   768,  1024),
        ("[3:4] 1536 × 2048",  1536, 2048),
        ("[9:16] 432 × 768",   432,  768),
        ("[9:16] 720 × 1280",  720,  1280),
        ("[9:16] 1080 × 1920", 1080, 1920),
        ("[9:16] 1296 × 2304", 1296, 2304),
    ]
    _size_map     = {lbl: (w, h) for lbl, w, h in _ALL_SIZES}
    _DEFAULT_SIZE = "[16:9] 2304 × 1296"

    size_dd = ft.Dropdown(
        label="Size",
        value=_DEFAULT_SIZE,
        options=[ft.dropdown.Option(lbl) for lbl, *_ in _ALL_SIZES],
        text_size=12, dense=True, height=44, width=230,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel2"], color=C["text1"],
        label_style=ft.TextStyle(color=C["text2"]),
    )

    _STEP_OPTS = ["1", "3", "6", "8", "12", "20", "30", "50"]
    steps_dd = ft.Dropdown(
        label="Steps",
        value="8",
        options=[ft.dropdown.Option(s) for s in _STEP_OPTS],
        text_size=12, dense=True, height=44, width=100,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel2"], color=C["text1"],
        label_style=ft.TextStyle(color=C["text2"]),
    )

    _BLANK = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

    # ── Per-prompt image tabs (state only — UI functions defined after UI elements) ──
    _prompt_images: dict[int, list] = load_img_tabs(prompts)
    _active_img_tab: list[int] = [0]
    img_tab_row = ft.Row(spacing=4, scroll=ft.ScrollMode.AUTO,
                         vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # ── Zoom/Pan viewer state ─────────────────────────────────────────
    _img_bytes: list[bytes]  = [_BLANK]
    _zoom:      list[float]  = [1.0]
    _pan_x:     list[float]  = [0.0]
    _pan_y:     list[float]  = [0.0]

    preview_img = ft.Image(
        src=_BLANK,
        fit=ft.BoxFit.CONTAIN,
        expand=True,
        visible=False,
    )

    # wrap in a Container using scale+offset instead of scaling the Image directly
    img_wrapper = ft.Container(
        content=preview_img,
        expand=True,
        alignment=ft.Alignment(0, 0),
    )

    viewer_container = ft.Container(
        expand=True,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        border_radius=br(6),
        bgcolor=C["bg"],
        content=img_wrapper,
    )

    def _apply_transform():
        z = _zoom[0]
        img_wrapper.scale   = ft.Scale(z)
        img_wrapper.offset  = ft.Offset(
            _pan_x[0] / ((viewer_container.width  or page.window_width  or 800) * z),
            _pan_y[0] / ((viewer_container.height or page.window_height or 600) * z),
        )
        page.update()

    def on_viewer_scroll(e: ft.ScrollEvent):
        delta = e.scroll_delta.y if e.scroll_delta else 0
        _zoom[0] = max(0.5, min(8.0, _zoom[0] * (0.95 if delta > 0 else 1.05)))
        _apply_transform()

    def on_viewer_pan(e: ft.DragUpdateEvent):
        _pan_x[0] += e.local_delta.x * 0.4
        _pan_y[0] += e.local_delta.y * 0.4
        _apply_transform()

    def on_viewer_double_tap(_):
        _zoom[0] = 1.0; _pan_x[0] = 0.0; _pan_y[0] = 0.0
        _apply_transform()

    viewer_gesture = ft.GestureDetector(
        content=viewer_container,
        on_scroll=on_viewer_scroll,
        on_pan_update=on_viewer_pan,
        on_double_tap=on_viewer_double_tap,
        mouse_cursor=ft.MouseCursor.MOVE,
        expand=True,
        drag_interval=10,
    )

    preview_ph = ft.Container(
        expand=True, border_radius=br(6), bgcolor=C["panel"],
        content=ft.Column(
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.IMAGE_OUTLINED, size=52, color=C["text3"]),
                ft.Text("Output", size=11, color=C["text3"]),
            ],
        ),
    )

    btn_save = ft.IconButton(
        ft.Icons.SAVE_ALT_ROUNDED,
        icon_color=C["text2"],
        tooltip="Save image",
        disabled=True,
    )

    async def on_save_click(_):
        if not _img_bytes[0] or _img_bytes[0] == _BLANK: return
        fp = ft.FilePicker()
        _ext = _export_util.image_ext(_img_bytes[0], ".png")
        result = await fp.save_file(
            dialog_title="Save image",
            file_name=f"output_{time.strftime('%Y%m%d_%H%M%S')}{_ext}",
            allowed_extensions=[_ext.lstrip(".")],
        )
        if result:
            p = image_save_path(result, _img_bytes[0])
            if p != result and Path(p).exists():
                # the extension was corrected to match the bytes and the file picker never asked about THAT name: another file lives there, so it is not replaced, the next free name is used
                _t0 = time.perf_counter()
                p = str(await _export_util.write_bytes_unique(p, _img_bytes[0]))
                _secs = time.perf_counter() - _t0
            else:
                _secs = await _export_util.write_bytes(p, _img_bytes[0])      # in a worker thread: the window stays responsive; the name is the one the person confirmed
            set_status(f"💾 Saved: {Path(p).name} ({_secs:.1f}s)", C["green"])

    btn_save.on_click = on_save_click

    zoom_reset_btn = ft.IconButton(
        ft.Icons.FIT_SCREEN,
        icon_color=C["text2"],
        tooltip="Reset zoom (double-click)",
        on_click=on_viewer_double_tap,
    )

    # ── Seed controls ────────────────────────────────────────────────
    _current_seed: list[int] = [-1]   # -1 = random

    # ── Run with seed controls ──────────────────────────────────────
    seed_field = ft.TextField(
        value="",
        hint_text="seed",
        width=160, dense=True, text_size=11,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["panel2"], color=C["text1"],
        hint_style=ft.TextStyle(color=C["text3"]),
        keyboard_type=ft.KeyboardType.NUMBER,
        read_only=True,
    )

    btn_new_run = ft.Button(
        "New Run",
        icon=ft.Icons.SHUFFLE_ROUNDED,
        style=ft.ButtonStyle(
            bgcolor=C["panel2"], color=C["text1"],
            shape=ft.RoundedRectangleBorder(radius=6),
            side=ft.BorderSide(1, C["border"]),
            padding=po(left=8, right=8, top=6, bottom=6),
        ),
        tooltip="Use a new random seed and run",
    )

    btn_quick = ft.Button(
        "Quick Run",
        icon=ft.Icons.BOLT_ROUNDED,
        style=ft.ButtonStyle(
            bgcolor=C["amber"], color="#000000",
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=po(left=8, right=8, top=6, bottom=6),
        ),
        tooltip="Run workflow as-is (size & steps from workflow file, current seed)",
    )

    btn_seed_run = ft.Button(
        "Run",
        icon=ft.Icons.PLAY_ARROW_ROUNDED,
        style=ft.ButtonStyle(
            bgcolor=C["green"], color="#000000",
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=po(left=8, right=8, top=6, bottom=6),
        ),
        tooltip="Run with the current seed (lock seed)",
    )

    # Stop: cancels the ComfyUI job that is generating right now (visible only while generating)
    _stop_event = asyncio.Event()
    btn_stop = ft.Button(
        "Stop",
        icon=ft.Icons.STOP_ROUNDED,
        visible=False,
        style=ft.ButtonStyle(
            bgcolor=C["red"], color="#FFFFFF",
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=po(left=8, right=8, top=6, bottom=6),
        ),
        tooltip="Stop generating and cancel the ComfyUI job",
    )

    def _gen_state(running: bool):
        """Called wherever the Run buttons are disabled/enabled; owns the Stop button + stop flag."""
        if running:
            _stop_event.clear()
        btn_stop.visible = running
        btn_stop.disabled = False

    async def _on_gen_stopped():
        set_status("⏹ Stopped", C["text2"])
        btn_new_run.disabled = False
        btn_seed_run.disabled = False
        btn_quick.disabled = False; _gen_state(False)
        progress.visible = False
        _gen_state(False)
        page.update()

    async def _do_stop(_):
        _stop_event.set()
        btn_stop.disabled = True
        set_status("⏹ Stopping...", C["amber"])
        pid = _ACTIVE_PID[0]
        if pid:
            await asyncio.get_event_loop().run_in_executor(None, comfy_cancel, pid)

    btn_stop.on_click = _do_stop

    viewer_toolbar = ft.Row(
        controls=[
            ft.Container(expand=True),
            zoom_reset_btn,
            btn_save,
        ],
        alignment=ft.MainAxisAlignment.END,
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    img_tab_info = ft.Text("", size=10, color=C["text3"])

    preview_stack = ft.Container(
        expand=True,
        content=ft.Column([
            img_tab_row,
            img_tab_info,
            ft.Stack([
                preview_ph,
                ft.Column([viewer_toolbar, viewer_gesture], spacing=2, expand=True),
            ], expand=True),
        ], spacing=2, expand=True),
    )
    progress = ft.ProgressRing(width=20, height=20, color=C["accent"], visible=False)

    # ── Image tab functions (defined after all UI elements) ───────────
    def _show_blank():
        preview_img.src = _BLANK
        preview_img.visible = False
        preview_ph.visible = True
        btn_save.disabled = True
        img_tab_info.value = ""
        page.update()                  # (a control in a panel that is not on screen right now has no .page: asking for it raises)

    def _show_tab(prompt_idx: int, tab_idx: int):
        imgs = _prompt_images.get(prompt_idx, [])
        if not imgs or tab_idx >= len(imgs):
            _show_blank(); return
        entry = imgs[tab_idx]
        path = entry.get("path")
        data = entry.get("data")
        if path and Path(path).exists():
            data = Path(path).read_bytes()
        if not data:
            _show_blank(); return
        _img_bytes[0] = data
        _zoom[0] = 1.0; _pan_x[0] = 0.0; _pan_y[0] = 0.0
        preview_img.src = data
        img_wrapper.scale = ft.Scale(1.0)
        img_wrapper.offset = ft.Offset(0, 0)
        preview_img.visible = True
        preview_ph.visible = False
        btn_save.disabled = False
        seed = entry.get("seed", -1)
        if isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0:      # 0 is a real seed
            seed_field.value = str(seed)
            seed_field.color = C["amber"]
            _current_seed[0] = seed
        # The seed field is the GENERATION seed (what Run uses): an image with a seed loads it, an image without one leaves it alone. The image's own seed is shown in its info line below.
        meta_parts = []
        if entry.get("api"): meta_parts.append(entry["api"])
        if entry.get("size"): meta_parts.append(entry["size"])
        if entry.get("steps"): meta_parts.append(f"steps={entry['steps']}")
        if isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0: meta_parts.append(f"seed {seed}")
        img_tab_info.value = "  ·  ".join(meta_parts)
        page.update()                  # (a control in a panel that is not on screen right now has no .page: asking for it raises)

    def _rebuild_tabs():
        pi = sel[0]
        imgs = _prompt_images.get(pi, [])
        img_tab_row.controls.clear()
        for t_idx, entry in enumerate(imgs):
            is_active = (t_idx == _active_img_tab[0])
            def _make_tab(ti=t_idx, lbl=entry["label"], active=is_active):
                def _on_select(_e):
                    _active_img_tab[0] = ti
                    _show_tab(pi, ti)
                    _rebuild_tabs()
                def _on_close(_e):
                    _prompt_images.get(pi, []).pop(ti)
                    remaining = _prompt_images.get(pi, [])
                    new_ti = max(0, min(ti, len(remaining) - 1))
                    _active_img_tab[0] = new_ti
                    if remaining:
                        _show_tab(pi, new_ti)
                    else:
                        _show_blank()
                    save_img_tabs(_prompt_images)
                    _rebuild_tabs()
                return ft.Container(
                    content=ft.Row([
                        ft.Text(lbl, size=10, color="white" if active else C["text2"]),
                        ft.IconButton(ft.Icons.CLOSE_ROUNDED, icon_size=10,
                                      icon_color="white" if active else C["text3"],
                                      width=18, height=18, padding=0, on_click=_on_close),
                    ], spacing=2, tight=True),
                    bgcolor=C["accent"] if active else C["panel2"],
                    border_radius=br(5),
                    padding=po(left=6, right=2, top=2, bottom=2),
                    on_click=_on_select,
                )
            img_tab_row.controls.append(_make_tab())
        page.update()

    async def _run_apis(mode: str):
        """mode: 'new' (new random seed), 'seed' (current seed), 'quick' (the workflow's own size/steps, current seed).
        Goes through every selected API in order with the SAME prompt and the SAME seed (so the results can be compared); every image becomes a tab of the prompt."""
        quick = mode == "quick"; force_new_seed = mode == "new"
        prompt_text = editor_field.value.strip()
        if not prompt_text:
            set_status("⚠ No prompt", C["amber"]); return
        apis = list(sel_apis)
        if not apis:
            set_status("⚠ No API selected: press the APIs button", C["amber"]); return
        gone = [f for f in apis if not (API_DIR / f).exists()]
        if gone:
            set_status(f"⚠ File not found: {', '.join(gone)}", C["red"]); return

        size_label = size_dd.value or _DEFAULT_SIZE
        w, h = _size_map.get(size_label, (2304, 1296))
        s = int(steps_dd.value or 20)
        icon, col = ("⚡", C["amber"]) if quick else ("⏳", C["accent"])

        run_prompt_id = prompts[sel[0]].get("id") if 0 <= sel[0] < len(prompts) else None   # the prompt Run was pressed for, by id
        btn_new_run.disabled = True
        btn_seed_run.disabled = True
        btn_quick.disabled = True; _gen_state(True)
        progress.visible = True
        _start_time = time.time()
        where = [""]
        set_status(f"{icon} 0s", col)
        page.update()

        async def _tick():
            while progress.visible:
                set_status(f"{icon} {where[0]}{time.time() - _start_time:.0f}s", col)
                await asyncio.sleep(1)
        asyncio.create_task(_tick())

        done_ok = 0; problems: list[str] = []; stopped = False; target_deleted = False
        fixed = -1 if force_new_seed else (_current_seed[0] if _current_seed[0] >= 0 else -1)
        for n, api_fn in enumerate(apis, 1):
            if _stop_event.is_set():
                stopped = True; break
            where[0] = f"[{n}/{len(apis)}] " if len(apis) > 1 else ""
            try:
                wf = json.loads((API_DIR / api_fn).read_text(encoding="utf-8"))
                wf = inject_prompt(wf, prompt_text)
                _q_size = ""; _q_steps = ""
                if quick:
                    for node in wf.values():          # the size / steps the workflow itself has, for the tab's meta line
                        if not isinstance(node, dict): continue
                        inp = node.get("inputs", {})
                        if node.get("class_type", "") in _LATENT_CLS and "width" in inp:
                            _q_size = f"{inp['width']}×{inp['height']}"
                        if node.get("class_type", "") in _SCLS and "steps" in inp:
                            _q_steps = inp["steps"]
                else:
                    wf = inject_size(wf, w, h, s)
                    _q_size, _q_steps = size_label, s
                wf, used_seed = inject_seed(wf, fixed)
                fixed = used_seed                        # the next API gets the same seed
                _current_seed[0] = used_seed
                seed_field.value = str(used_seed)
                if not quick:
                    seed_field.color = C["text2"] if force_new_seed else C["amber"]
            except Exception as e:
                problems.append(f"{Path(api_fn).stem}: failed to load workflow: {e}")
                continue

            outcome = {"state": "", "msg": "", "deleted": False}

            async def on_img(path: str, _rid=run_prompt_id, _api=api_fn, _sz=_q_size, _st=_q_steps, _oc=outcome, _sd=used_seed):
                _pi = find_prompt_index(prompts, _rid)          # where that prompt is NOW (-1: it was deleted while the image was being made)
                if _pi < 0:
                    _oc["deleted"] = True
                    set_status("⚠ The prompt was deleted while the image was being made: the image was not attached to any prompt", C["amber"])
                    page.update()
                    return
                data = Path(path).read_bytes()
                imgs = _prompt_images.setdefault(_pi, [])
                label = f"{'⚡' if quick else ''}#{len(imgs)+1} {time.strftime('%H:%M')}"
                imgs.append({"path": path, "seed": _sd, "label": label,          # the seed this job was started with, not whatever the screen shows when the image arrives
                             "api": _api, "size": _sz, "steps": _st})
                new_ti = len(imgs) - 1
                save_img_tabs(_prompt_images)
                if 0 <= _pi < len(prompts):
                    tb = make_thumbnail_bytes(data)
                    prompts[_pi]["thumbnail"]  = base64.b64encode(tb).decode()
                    prompts[_pi]["thumb_time"] = time.strftime("%d/%m %H:%M")
                    save_prompts(prompts)
                    if sel[0] == _pi:
                        load_thumb_for_prompt(_pi)
                if sel[0] == _pi:
                    _active_img_tab[0] = new_ti
                    _show_tab(_pi, new_ti)
                    _rebuild_tabs()
                page.update()

            async def on_err(msg: str, _o=outcome):
                _o["state"] = "err"; _o["msg"] = msg

            async def on_done(_o=outcome):
                _o["state"] = "ok"

            async def on_stop(_o=outcome):
                _o["state"] = "stopped"

            await run_workflow(wf, on_img, on_err, on_done, stop_event=_stop_event, on_stop=on_stop)
            if outcome["deleted"]:                          # the image came back but its prompt is gone: it is attached to nothing and the rest of the list is not run (the job that finished is left alone)
                target_deleted = True; break
            if outcome["state"] == "ok":
                done_ok += 1
            elif outcome["state"] == "stopped":
                stopped = True; break
            else:
                problems.append(f"{Path(api_fn).stem}: {outcome['msg'] or 'no image came back'}")

        elapsed = time.time() - _start_time
        btn_new_run.disabled = False
        btn_seed_run.disabled = False
        btn_quick.disabled = False; _gen_state(False)
        progress.visible = False
        if stopped:
            set_status("⏹ Stopped" + (f" ({done_ok} of {len(apis)} done)" if len(apis) > 1 else ""), C["text2"])
        elif target_deleted:
            set_status("⚠ The prompt was deleted while the image was being made: the image was not attached to any prompt" + (f" ({done_ok} of {len(apis)} done before)" if done_ok else ""), C["amber"])
        elif problems:
            head = f"{done_ok} of {len(apis)} done · " if len(apis) > 1 else ""
            set_status(f"❌ {head}{problems[0]}" + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""), C["red"])
        elif quick:
            set_status(f"⚡ Quick Run done ({elapsed:.1f}s)" if len(apis) == 1 else f"⚡ Quick Run done: {done_ok} APIs ({elapsed:.1f}s)", C["green"])
        else:
            set_status(f"✅ Done! ({elapsed:.1f}s)" if len(apis) == 1 else f"✅ Done: {done_ok} APIs ({elapsed:.1f}s)", C["green"])
        page.update()

    async def _do_run(force_new_seed: bool):
        await _run_apis("new" if force_new_seed else "seed")

    btn_new_run.on_click  = lambda _: asyncio.ensure_future(_do_run(force_new_seed=True))
    btn_seed_run.on_click = lambda _: asyncio.ensure_future(_do_run(force_new_seed=False))

    async def _do_quick_run(_):
        """Quick Gen: the workflow's own size/steps, same seed."""
        await _run_apis("quick")

    btn_quick.on_click = _do_quick_run

    def _open_apis_dialog(e=None):
        """APIs dialog (same as Gallery Generator): AVAILABLE | SELECTED in generation order, edited on a copy; double click moves a row; » / « move all."""
        work: list[str] = list(sel_apis)
        files = [scan_apis()]
        hl_left: set = set(); hl_right: set = set()
        left_list = ft.ListView(spacing=2, expand=True)
        right_list = ft.ListView(spacing=2, expand=True)
        note = ft.Text("", size=10, color=C["text3"])
        lvl = {"ok": C["text3"], "warn": C["amber"], "bad": C["red"]}
        last_tap = [None, 0.0]

        def _row(title, sub, level, highlighted, on_tap):
            return ft.GestureDetector(
                content=ft.Container(
                    content=ft.Column([
                        ft.Text(title, size=12, color=C["text1"], weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(sub, size=10, color=lvl[level], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ], spacing=1),
                    padding=po(left=10, right=10, top=6, bottom=6), border_radius=br(6),
                    bgcolor="#1E3A5F" if highlighted else C["panel2"],
                    border=ft.Border.all(1, C["accent"] if highlighted else C["border"])),
                on_tap=on_tap, mouse_cursor=ft.MouseCursor.CLICK)

        def render():
            left_list.controls.clear(); right_list.controls.clear()
            hl_left.intersection_update(set(files[0])); hl_right.intersection_update(set(work))
            avail = [f for f in files[0] if f not in work]
            for f in avail:
                status, level = api_file_info(f)
                left_list.controls.append(_row(Path(f).stem, f"{f} · {status}", level, f in hl_left,
                                               lambda e, _f=f: toggle(hl_left, _f, add_files)))
            if not avail:
                left_list.controls.append(ft.Text("No more APIs available", size=10, color=C["text3"], italic=True))
            for i, f in enumerate(work):
                status, level = api_file_info(f)
                right_list.controls.append(_row(f"{i + 1}.  {Path(f).stem}", f"{f} · {status}", level, f in hl_right,
                                                lambda e, _f=f: toggle(hl_right, _f, remove_files)))
            if not work:
                right_list.controls.append(ft.Text("No APIs selected", size=10, color=C["text3"], italic=True))
            page.update()

        def toggle(group: set, f: str, move_one):
            now = time.monotonic()
            if last_tap[0] == f and now - last_tap[1] < 0.5:      # two taps on the same row = double click: move it to the other side
                last_tap[0] = None
                move_one([f]); return
            last_tap[0], last_tap[1] = f, now
            group.symmetric_difference_update({f}); render()

        def clear_selection(e=None):
            hl_left.clear(); hl_right.clear(); render()

        def add_files(names):
            for f in names:
                if f not in work:
                    work.append(f)
            hl_left.clear(); render()

        def remove_files(names):
            work[:] = [f for f in work if f not in set(names)]
            hl_right.clear(); render()

        def move(delta: int):
            idx = [i for i, f in enumerate(work) if f in hl_right]
            for i in (idx if delta < 0 else reversed(idx)):
                j = i + delta
                if 0 <= j < len(work) and work[j] not in hl_right:
                    work[i], work[j] = work[j], work[i]
            render()

        def do_refresh(e):
            files[0] = scan_apis()
            note.value = f"{len(files[0])} API(s) found"
            render()

        def do_apply(e):
            sel_apis[:] = list(work)
            save_api_selection(sel_apis)
            _apis_label()
            page.pop_dialog(); page.update()

        def _mid_btn(icon, tip, on_click):
            return ft.IconButton(icon=icon, tooltip=tip, on_click=on_click, icon_size=18, width=36, height=36, icon_color=C["text1"],
                                 style=ft.ButtonStyle(side=ft.BorderSide(1, C["border"]), shape=ft.RoundedRectangleBorder(radius=6)))

        def _panel(title, lst):
            return ft.Column([
                ft.Text(title, size=10, weight=ft.FontWeight.W_700, color=C["text3"]),
                ft.GestureDetector(
                    content=ft.Container(content=lst, expand=True, padding=6, bgcolor=C["panel"], border_radius=br(8), border=ft.Border.all(1, C["border"])),
                    on_tap=clear_selection, expand=True),
            ], spacing=4, expand=True)

        try:
            pw = float(page.width or page.window.width or 1200); ph = float(page.height or page.window.height or 700)
        except Exception:
            pw, ph = 1200.0, 700.0
        dw, dh = int(max(560, min(900, pw * 0.8))), int(max(360, min(560, ph * 0.7)))
        body = ft.Container(width=dw, height=dh, content=ft.Column([
            ft.Row([
                _panel("AVAILABLE APIs", left_list),
                ft.Column([
                    _mid_btn(ft.Icons.KEYBOARD_DOUBLE_ARROW_RIGHT_ROUNDED, "Add all", lambda ev: add_files([f for f in files[0] if f not in work])),
                    _mid_btn(ft.Icons.CHEVRON_RIGHT_ROUNDED, "Add selected", lambda ev: add_files([f for f in files[0] if f in hl_left])),
                    _mid_btn(ft.Icons.CHEVRON_LEFT_ROUNDED, "Remove selected", lambda ev: remove_files(list(hl_right))),
                    _mid_btn(ft.Icons.KEYBOARD_DOUBLE_ARROW_LEFT_ROUNDED, "Remove all", lambda ev: remove_files(list(work))),
                    ft.Container(height=14),
                    _mid_btn(ft.Icons.ARROW_UPWARD_ROUNDED, "Move up", lambda ev: move(-1)),
                    _mid_btn(ft.Icons.ARROW_DOWNWARD_ROUNDED, "Move down", lambda ev: move(1)),
                ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=6),
                _panel("SELECTED APIs — generation order", right_list),
            ], expand=True, spacing=12, vertical_alignment=ft.CrossAxisAlignment.STRETCH),
            ft.Row([
                ft.TextButton(content=ft.Row([ft.Icon(ft.Icons.REFRESH_ROUNDED, size=14, color=C["text2"]),
                                              ft.Text("Refresh", size=11, color=C["text2"])], tight=True, spacing=4), on_click=do_refresh),
                note,
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=8))
        render()
        page.show_dialog(ft.AlertDialog(
            modal=True, bgcolor=C["surface"],
            title=ft.Text("APIs", size=15, weight=ft.FontWeight.W_700, color=C["text1"]),
            content=body,
            actions=[ft.TextButton("Cancel", on_click=lambda ev: page.pop_dialog(), style=ft.ButtonStyle(color=C["text2"])),
                     ft.FilledButton("Apply", on_click=do_apply, bgcolor=C["accent"])],
            actions_alignment=ft.MainAxisAlignment.END))

    add_api_btn.on_click = _open_apis_dialog

    def on_refresh_api(_):
        found = scan_apis()
        sel_apis[:] = [f for f in sel_apis if f in found]       # a selected file that is gone drops out
        save_api_selection(sel_apis)
        _apis_label()
        set_status(f"🔄 Found {len(found)} workflow(s)", C["text2"])
        page.update()

    right_w: list[float] = [235]
    # ── Thumbnail strip (show the thumbnail for the selected prompt) ──────────
    thumb_img = ft.Image(
        src=_BLANK,
        fit=ft.BoxFit.CONTAIN,
        width=200, height=120,
        visible=False,
        border_radius=br(6),
    )
    thumb_label = ft.Text("No image yet", size=10, color=C["text3"], italic=True)
    thumb_ph = ft.Container(
        width=200, height=120,
        border_radius=br(6),
        bgcolor=C["panel"],
        content=ft.Column(
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.PHOTO_OUTLINED, size=32, color=C["text3"]),
                ft.Text("No image yet", size=10, color=C["text3"]),
            ],
        ),
    )
    thumb_stack = ft.Stack([thumb_ph, thumb_img], width=200, height=120)

    def load_thumb_for_prompt(idx: int):
        """Load the thumbnail from the prompt data and display it in the thumb panel."""
        if 0 <= idx < len(prompts) and prompts[idx].get("thumbnail"):
            thumb_img.src     = thumb_to_bytes(prompts[idx]["thumbnail"])
            thumb_img.visible = True
            thumb_ph.visible  = False
            thumb_label.value = f"🕐 {prompts[idx].get('thumb_time','')}"
            thumb_label.color = C["text3"]
        else:
            thumb_img.visible = False
            thumb_ph.visible  = True
            thumb_label.value = ""
        page.update()
    _thumb_cb[0] = load_thumb_for_prompt

    # Column on the left: thumbnail
    thumb_col = ft.Container(
        width=220,
        content=ft.Column([
            ft.Text("Latest Output", size=11, weight=ft.FontWeight.W_500, color=C["text2"]),
            thumb_stack,
            thumb_label,
        ], spacing=4, tight=True),
    )

    # Column on the right: controls
    ctrl_col = ft.Container(
        width=340,
        content=ft.Column(spacing=8, controls=[
            ft.Row([size_dd, steps_dd], spacing=6, run_spacing=6, wrap=True),
            ft.Row([seed_field, btn_seed_run, btn_quick], spacing=6, run_spacing=6, wrap=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([btn_new_run, btn_stop], spacing=6, run_spacing=6, wrap=True),
        ]),
    )

    right_container = ft.Container(
        expand=True,
        bgcolor=C["surface"],
        padding=po(left=12, top=12, right=12, bottom=10),
        content=ft.Column(spacing=8, expand=True, controls=[
            ft.Text("Generation", size=13, weight=ft.FontWeight.BOLD, color=C["text1"]),
            ft.Row([thumb_col, api_dd, ctrl_col], spacing=12, run_spacing=10, wrap=True, expand=False,
                   vertical_alignment=ft.CrossAxisAlignment.START),
            ft.Divider(height=1, color=C["border"]),
            preview_stack,
        ]),
    )

    def on_right_drag(e: ft.DragUpdateEvent):
        # drag left = negative delta_x = expand the right side
        left_w[0], mid_panel.width = wide_widths(_win_w(), left_w[0], (mid_panel.width or mid_w[0]) + e.local_delta.x)
        left_container.width = left_w[0]
        page.update()

    # ── Title bar ─────────────────────────────────────────────────────
    title_bar = ft.Container(
        bgcolor=C["toolbar"],
        border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
        padding=po(left=16, top=8, right=16, bottom=8),
        content=ft.Row(controls=[
            ft.Icon(ft.Icons.TUNE_ROUNDED, color=C["accent"], size=16),
            ft.Text("Prompt Lab", size=14, weight=ft.FontWeight.BOLD, color=C["text1"]),
            ft.Container(expand=True),
            progress,
            status_txt,
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
    )

    # ── Responsive workspace ─────────────────────────────────────────────────────────────────────────────────
    # The real window width decides: >= 1600 three columns (list / editor+chat / generation); 1100-1599 the list plus ONE workspace that switches Editor <-> Generate;
    # < 1100 a single column with tabs Prompts / Editor / Generate. The same controls are re-parented, so text, selection, chat and logs are untouched by a resize.
    BP_WIDE, BP_MEDIUM = 1600, 1100
    _layout = {"mode": "", "tab": "editor"}
    mid_w: list[float] = [420.0]
    workspace = ft.Container(expand=True)

    def _win_w() -> float:
        try:
            return float(page.width or page.window.width or 1400)
        except Exception:
            return 1400.0

    def _win_h() -> float:
        try:
            return float(page.height or page.window.height or 800)
        except Exception:
            return 800.0

    def _mode_for(w: float) -> str:
        return "wide" if w >= BP_WIDE else ("medium" if w >= BP_MEDIUM else "compact")

    def _tab_button(label, icon, key, active):
        col = "#FFFFFF" if active else C["text2"]
        return ft.Container(
            content=ft.Row([ft.Icon(icon, size=15, color=col), ft.Text(label, size=12, color=col, weight=ft.FontWeight.W_600 if active else ft.FontWeight.NORMAL)],
                           spacing=6, tight=True, alignment=ft.MainAxisAlignment.CENTER),
            expand=True, height=36, alignment=ft.Alignment(0, 0), border_radius=br(6), ink=True,
            bgcolor=C["accent"] if active else C["panel"], on_click=lambda e, k=key: _set_tab(k))

    def _tabbar(items):
        return ft.Container(
            content=ft.Row([_tab_button(lbl, ic, k, k == _layout["tab"]) for lbl, ic, k in items], spacing=6),
            padding=po(left=10, right=10, top=6, bottom=6), bgcolor=C["toolbar"], border=ft.Border(bottom=ft.BorderSide(1, C["border"])))

    _T_PROMPTS = ("Prompts", ft.Icons.LIST_ALT_ROUNDED, "prompts")
    _T_EDITOR = ("Editor", ft.Icons.EDIT_NOTE_ROUNDED, "editor")
    _T_GEN = ("Generate", ft.Icons.AUTO_AWESOME_ROUNDED, "generate")

    def _apply_layout(force: bool = False):
        mode = _mode_for(_win_w())
        if mode == _layout["mode"] and not force:
            return
        if _layout["mode"] == "wide" and mid_panel.width:
            mid_w[0] = mid_panel.width                       # remember the dragged width of the editor column
        _layout["mode"] = mode
        thumb_col.visible = mode == "wide"                   # the viewer shows the same image: the small thumbnail is only kept where there is room
        if mode == "wide":
            left_w[0], mid_w[0] = wide_widths(_win_w(), left_w[0], mid_w[0])
            left_container.width = left_w[0]; left_container.expand = False
            mid_panel.width = mid_w[0]; mid_panel.expand = False
            workspace.content = ft.Row(spacing=0, expand=True, controls=[
                left_container, _resize_divider(on_left_drag), mid_panel, _resize_divider(on_right_drag), right_container])
        elif mode == "medium":
            if _layout["tab"] not in ("editor", "generate"):
                _layout["tab"] = "editor"
            left_container.width = min(left_w[0], 220); left_container.expand = False
            mid_panel.width = None; mid_panel.expand = True
            body = mid_panel if _layout["tab"] == "editor" else right_container
            workspace.content = ft.Row(spacing=0, expand=True, controls=[
                left_container, ft.VerticalDivider(width=1, color=C["border"]),
                ft.Column(spacing=0, expand=True, controls=[_tabbar([_T_EDITOR, _T_GEN]), body])])
        else:
            left_container.width = None; left_container.expand = True
            mid_panel.width = None; mid_panel.expand = True
            body = {"prompts": left_container, "editor": mid_panel, "generate": right_container}.get(_layout["tab"], mid_panel)
            workspace.content = ft.Column(spacing=0, expand=True, controls=[_tabbar([_T_PROMPTS, _T_EDITOR, _T_GEN]), body])

    def _fit_sizes():
        """Parts with a remembered height / width follow the window: the chat panel keeps room for the editor, dialogs never outgrow the window."""
        w, h = _win_w(), _win_h()
        lo, hi = llm_height_limits(h, _layout["mode"] or "wide")
        llm_panel.height = max(lo, min(hi, _llm_h[0]))
        try:
            config_dialog.content.width = min(_CFG_W, max(320, w - 80))
            config_dialog.content.height = min(580, max(280, h - 200))
        except Exception:
            pass

    def _set_tab(key: str):
        _layout["tab"] = key
        _apply_layout(force=True)
        _fit_sizes()
        page.update()

    def _after_pick():
        """A prompt was picked from the list: in the single-column layout go on to its editor."""
        if _layout["mode"] == "compact":
            _set_tab("editor")

    def _on_page_resize(e=None):
        _apply_layout()
        if _layout["mode"] == "wide":                         # a window made smaller must not leave the editor wider than the room it has
            left_w[0], _m = wide_widths(_win_w(), left_w[0], mid_panel.width or mid_w[0])
            left_container.width = left_w[0]; mid_panel.width = _m
        _fit_sizes()
        page.update()

    page.on_resize = _on_page_resize
    _apply_layout()
    _fit_sizes()
    page.add(ft.Column(spacing=0, expand=True, controls=[title_bar, workspace]))
    _update_check.start_notice(ft, page, APP_NAME, APP_VERSION)      # a notice only (GitHub releases, once a day, silent when offline); see update_check.py

    if prompts:
        _select(len(prompts) - 1)                      # the newest prompt (top of the list) is open on start: editor, chat and image tabs are not empty. Nothing is created
    else:
        refresh_list(-1)                               # no prompts: the empty state; nothing is created for the person
        load_thumb_for_prompt(-1)
        _rebuild_tabs()
    if LOAD_NOTICES:                                   # a damaged data file was found at start: say so (the damaged file itself is kept)
        set_status("⚠ " + " ".join(LOAD_NOTICES), C["amber"])

ft.app(target=_app_error.guard(ft, main, APP_NAME, APP_VERSION))   # friendly error dialog instead of Flet's red 'application crashed' page