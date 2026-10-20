"""
Prompt Lab – used together with image_editor.py
- same folder, same COMFY_HOST, same scan_apis()
- stores the prompt list in prompts_tester.json
"""
import asyncio, base64, json, random, time, uuid, urllib.parse, urllib.request, urllib.error
import sys
from pathlib import Path
import flet as ft
import os as _os
import update_check as _update_check

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
APP_VERSION = "0.1.8"   # SemVer; see RELEASE.md

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
_DEFAULT_SYS = (
    "You are an AI image prompt engineer for Flux/Krea.\n\n"
    "Reply in the same language the user writes in. Prompt content must be in English.\n\n"
    "Output ONLY a raw JSON object — no markdown, no code block, no text before or after:\n"
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
    return {"system_prompt": _DEFAULT_SYS, "history_dir": "user/llm_session"}

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
    "text3"   : "#4B5563",
    "toolbar" : "#161616",
}

def po(left=0, top=0, right=0, bottom=0):
    return ft.Padding(left=left, top=top, right=right, bottom=bottom)

def br(r):
    return ft.BorderRadius(r, r, r, r)

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
def load_prompts() -> list[dict]:
    try:
        data = json.loads(PROMPTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for p in data:
        if isinstance(p, dict):
            p.setdefault("name", "")
            p.setdefault("text", "")
            out.append(p)
    return out

def thumb_to_bytes(b64str: str) -> bytes:
    return base64.b64decode(b64str)

def save_prompts(prompts: list[dict]):
    PROMPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROMPTS_FILE.write_text(json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")

def load_img_tabs() -> dict[int, list]:
    """Load persisted image tab entries. Filters out missing files."""
    if not IMAGES_FILE.exists():
        return {}
    try:
        raw = json.loads(IMAGES_FILE.read_text(encoding="utf-8"))
        result = {}
        for k, entries in raw.items():
            valid = [e for e in entries if Path(e.get("path", "")).exists()]
            if valid:
                result[int(k)] = valid
        return result
    except Exception:
        return {}

def save_img_tabs(img_dict: dict):
    """Save image tab entries (path-based only) to disk."""
    IMAGES_FILE.parent.mkdir(parents=True, exist_ok=True)
    serialisable = {
        str(k): [
            {"path": e["path"], "seed": e.get("seed", -1), "label": e["label"]}
            for e in entries if "path" in e
        ]
        for k, entries in img_dict.items()
    }
    IMAGES_FILE.write_text(json.dumps(serialisable, ensure_ascii=False, indent=2), encoding="utf-8")

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
def _parse_llm_output(raw: str) -> tuple[str, str]:
    """Parse JSON output from the LLM and return (reply, prompt)."""
    import re, json as _json
    raw = raw.strip()

    def _clean(s: str) -> str:
        return s.replace("\\n", "\n").strip()

    # 1. parse directly
    try:
        data = _json.loads(raw)
        return (_clean(str(data.get("reply",""))), _add_section_spacing(_clean(str(data.get("prompt","")))))
    except Exception:
        pass

    # 2. code block
    m = re.search(r'```(?:json)?\s*({.*?})\s*```', raw, re.DOTALL)
    if m:
        try:
            data = _json.loads(m.group(1))
            return (_clean(str(data.get("reply",""))), _add_section_spacing(_clean(str(data.get("prompt","")))))
        except Exception:
            pass

    # 3. regex field extraction — supports pretty-print and unquoted values
    reply_m  = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"|\"reply\"\s*:\s*([^,}]+)', raw, re.DOTALL)
    prompt_m = re.search(r'"prompt"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.DOTALL)

    # If there is no prompt in quotes, try to find the prompt section directly after "prompt":
    if not prompt_m:
        pm2 = re.search(r'"prompt"\s*:\s*([\s\S]*?)(?="reply"|$)', raw)
        if not pm2:
            pm2 = re.search(r'"prompt"\s*:[\s\n]*(.*)', raw, re.DOTALL)
        if pm2:
            prompt_raw = pm2.group(1).strip().strip('"}').strip()
        else:
            prompt_raw = ""
    else:
        prompt_raw = prompt_m.group(1)

    reply_raw = ""
    if reply_m:
        reply_raw = reply_m.group(1) or reply_m.group(2) or ""

    if reply_raw or prompt_raw:
        return (_clean(reply_raw), _add_section_spacing(_clean(prompt_raw)))

    # 4. fallback
    return (raw, "")

def _add_section_spacing(prompt: str) -> str:
    """Add a blank line before each section header."""
    import re
    lines = prompt.splitlines()
    clean = []
    section_pattern = re.compile(r'^[A-Za-z][A-Za-z ]+:\s?', re.IGNORECASE)
    for line in lines:
        stripped = line.strip()
        if section_pattern.match(stripped) and clean and clean[-1].strip() != '':
            clean.append('')
        clean.append(line)
    return '\n'.join(clean).strip()

async def run_llm(user_text: str, cfg: dict, session_id: str, on_result, on_err, on_debug=None, wf_file: str = "gemma4_llm.json"):
    """Run the LLM workflow; on_result(reply, prompt) parses the JSON output."""
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
            wf[nid]["inputs"]["max_tokens"]    = int(cfg.get("max_tokens", 1024))
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
                t, p = _parse_llm_output(raw_text.strip())
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
                        dst = TEMP_DIR / f"pt_{uuid.uuid4().hex[:8]}.png"
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
    page.window_min_width  = 1200
    page.window_min_height = 500
    page.padding   = 0
    page.bgcolor   = C["bg"]
    page.theme_mode = ft.ThemeMode.DARK

    prompts: list[dict] = load_prompts()
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
                    on_click=lambda e: _select(e.control.data),
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
        prompts.append({"name": name, "text": ""})
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
        left_w[0] = max(140, min(400, left_w[0] + e.local_delta.x))
        left_container.width = left_w[0]
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
        multiline=True, min_lines=22, expand=True,
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
        ], spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
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

    def _refresh_list(lv):
        """Update the list itself, not just its container: a list swapped in by a tab switch otherwise showed new bubbles only after switching tabs again."""
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
        _refresh_list(ch)
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
        current_prompt = editor_field.value.strip()
        # Send the current prompt only when it exists
        if current_prompt:
            combined = f"Current prompt:\n{current_prompt}\n\n---\nInstruction/Question:\n{question}"
        else:
            combined = f"[No prompt yet]\n\nUser: {question}"

        # snapshot the selection first — prevent the user from changing the prompt while waiting for the LLM
        target_idx = sel[0]
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
                i = target_idx
                if i < 0 or i >= len(prompts):
                    # There is no prompt selected yet — create a new one
                    new_p = {"name": "LLM prompt", "text": prompt.strip()}
                    prompts.append(new_p)
                    i = len(prompts) - 1
                    sel[0] = i
                    refresh_list(i)
                prompts[i]["text"] = prompt.strip()
                save_prompts(prompts)
                # Update the editor only when the selected prompt is still the same one
                if sel[0] == i:
                    editor_field.value = prompt.strip()
                    _autosave_text(prompt.strip())
            page.update()

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
            _grok_history[0].append({"role": "user", "content": combined})
            async def _run_grok():
                try:
                    sys_prompt = llm_cfg[0].get("system_prompt", _DEFAULT_SYS)
                    print(f"[GROK DEBUG] calling API model={_grok_model[0]}")
                    _pin_log("→ GROK", f"model={_grok_model[0]}", C["text2"])
                    reply = await _call_grok(
                        _grok_api_key[0], _grok_model[0], sys_prompt,
                        _grok_history[0],
                        max_tokens=int(llm_cfg[0].get("max_tokens", 1024)),
                        temperature=float(llm_cfg[0].get("temperature", 0.7)),
                    )
                    _pin_log("← GROK RAW", reply[:500], C["text2"])
                    _grok_history[0].append({"role": "assistant", "content": reply})
                    t, p = _parse_llm_output(reply)
                    await on_result(t, p)
                except Exception as exc:
                    import traceback
                    tb = traceback.format_exc()
                    print(f"[GROK ERROR] {exc}\n{tb}")
                    await on_err(f"{exc}\n{tb[-400:]}")
            asyncio.create_task(_run_grok())
        else:
            _wf_file = f"{_sent_model}.json"
            asyncio.create_task(run_llm(combined, llm_cfg[0], _model_states[_sent_model]["session_id"], on_result, on_err, on_debug=_on_debug, wf_file=_wf_file))

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
        label="System Prompt",
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
        value=str(llm_cfg[0].get("max_tokens", 1024)),
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

    def _open_llm_config(_):
        cfg_sys_field.value         = llm_cfg[0].get("system_prompt", _DEFAULT_SYS)
        cfg_dir_field.value         = llm_cfg[0].get("history_dir", "")
        cfg_max_tokens_field.value  = str(llm_cfg[0].get("max_tokens", 1024))
        cfg_temperature_field.value = str(llm_cfg[0].get("temperature", 0.7))
        cfg_nctx_field.value        = str(llm_cfg[0].get("n_ctx", 16384))
        cfg_grok_key_field.value    = _grok_api_key[0]
        cfg_grok_model_dd.value     = _grok_model[0]
        _switch_cfg_tab("local")   # always open on Local LLM tab
        config_dialog.open = True
        page.update()

    def _save_llm_config(_):
        llm_cfg[0]["system_prompt"] = cfg_sys_field.value.strip() or _DEFAULT_SYS
        llm_cfg[0]["history_dir"]   = cfg_dir_field.value.strip()
        try:
            llm_cfg[0]["max_tokens"] = int(cfg_max_tokens_field.value.strip())
        except ValueError:
            llm_cfg[0]["max_tokens"] = 1024
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

    def _label(txt: str):
        return ft.Text(txt, size=10, weight=ft.FontWeight.W_600, color=C["text2"])

    cfg_local_content = ft.Column([
        _label("System Prompt"),
        cfg_sys_field,
        ft.Divider(height=1, color=C["border"]),
        _label("Generation"),
        ft.Row([cfg_max_tokens_field, cfg_temperature_field, cfg_nctx_field],
               spacing=8, alignment=ft.MainAxisAlignment.START),
        ft.Divider(height=1, color=C["border"]),
        _label("Session"),
        cfg_dir_field,
        ft.Text(
            "💡 LLM workflow files (*.json) must be in the comfy_apis/llm/ folder next to this file",
            size=10, color=C["text3"], italic=True,
        ),
        ft.Divider(height=1, color=C["border"]),
        _label("Updates"),
        _update_check.settings_row(ft, page, APP_NAME, APP_VERSION),
    ], spacing=6, tight=True, scroll=ft.ScrollMode.AUTO, expand=True)

    cfg_grok_content = ft.Column([
        cfg_grok_key_field,
        cfg_grok_model_dd,
        ft.Text(
            "💡 Credentials shared with story_generator (%APPDATA%\\StoryGenerator\\credentials.json)",
            size=10, color=C["text3"], italic=True,
        ),
    ], spacing=10, tight=True, visible=False)

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
            height=520,
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
        lg = _model_states[model or _active_model[0]]["logs"]
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
        _refresh_list(lg)
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
        _llm_h[0] = max(120.0, min(600.0, _llm_h[0] - e.local_delta.y))
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
    api_dd = _dd([ft.dropdown.Option(f) for f in scan_apis()], "Select Workflow")

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
    _prompt_images: dict[int, list] = load_img_tabs()
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
        result = await fp.save_file(
            dialog_title="Save image",
            file_name=f"output_{time.strftime('%Y%m%d_%H%M%S')}.png",
            allowed_extensions=["png"],
        )
        if result:
            p = result if result.endswith(".png") else result + ".png"
            Path(p).write_bytes(_img_bytes[0])
            set_status(f"💾 Saved: {Path(p).name}", C["green"])

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
        if preview_img.page: preview_img.page.update()

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
        if seed and seed >= 0:
            seed_field.value = str(seed)
            seed_field.color = C["amber"]
            _current_seed[0] = seed
        meta_parts = []
        if entry.get("api"): meta_parts.append(entry["api"])
        if entry.get("size"): meta_parts.append(entry["size"])
        if entry.get("steps"): meta_parts.append(f"steps={entry['steps']}")
        img_tab_info.value = "  ·  ".join(meta_parts)
        if preview_img.page: preview_img.page.update()

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
        if img_tab_row.page:
            img_tab_row.update()

    async def _do_run(force_new_seed: bool):
        prompt_text = editor_field.value.strip()
        if not prompt_text:
            set_status("⚠ No prompt", C["amber"]); return
        api_fn = api_dd.value
        if not api_fn:
            set_status("⚠ Workflow not selected", C["amber"]); return
        api_path = API_DIR / api_fn
        if not api_path.exists():
            set_status(f"⚠ File not found: {api_fn}", C["red"]); return

        size_label = size_dd.value or _DEFAULT_SIZE
        w, h = _size_map.get(size_label, (2304, 1296))
        s = int(steps_dd.value or 20)

        run_prompt_idx = sel[0]   # จำ prompt ที่กด Run ไว้ก่อนเลย
        btn_new_run.disabled = True
        btn_seed_run.disabled = True
        btn_quick.disabled = True; _gen_state(True)
        progress.visible = True
        _start_time = time.time()
        set_status("⏳ 0s", C["accent"])
        page.update()

        async def _tick():
            while progress.visible:
                elapsed = time.time() - _start_time
                set_status(f"⏳ {elapsed:.0f}s", C["accent"])
                await asyncio.sleep(1)
        asyncio.create_task(_tick())

        try:
            wf = json.loads(api_path.read_text(encoding="utf-8"))
            wf = inject_prompt(wf, prompt_text)
            wf = inject_size(wf, w, h, s)
            # New Run = always random, Run = use the current seed
            fixed = -1 if force_new_seed else (_current_seed[0] if _current_seed[0] >= 0 else -1)
            wf, used_seed = inject_seed(wf, fixed)
            _current_seed[0] = used_seed
            seed_field.value = str(used_seed)
            seed_field.color = C["text2"] if force_new_seed else C["amber"]
        except Exception as e:
            set_status(f"❌ Failed to load workflow: {e}", C["red"])
            btn_new_run.disabled = False; btn_seed_run.disabled = False
            btn_quick.disabled = False; _gen_state(False)
            progress.visible = False; page.update(); return

        _run_api    = api_fn
        _run_size   = size_label
        _run_steps  = s
        async def on_img(path: str, _pi=run_prompt_idx):
            data = Path(path).read_bytes()
            imgs = _prompt_images.setdefault(_pi, [])
            label = f"#{len(imgs)+1} {time.strftime('%H:%M')}"
            imgs.append({"path": path, "seed": _current_seed[0], "label": label,
                         "api": _run_api, "size": _run_size, "steps": _run_steps})
            new_ti = len(imgs) - 1
            save_img_tabs(_prompt_images)
            # อัปเดต thumbnail ของ prompt นั้น
            if 0 <= _pi < len(prompts):
                tb = make_thumbnail_bytes(data)
                prompts[_pi]["thumbnail"]  = base64.b64encode(tb).decode()
                prompts[_pi]["thumb_time"] = time.strftime("%d/%m %H:%M")
                save_prompts(prompts)
                if sel[0] == _pi:
                    load_thumb_for_prompt(_pi)
            # แสดงรูปเฉพาะถ้ากำลัง view prompt นั้นอยู่
            if sel[0] == _pi:
                _active_img_tab[0] = new_ti
                _show_tab(_pi, new_ti)
                _rebuild_tabs()
            page.update()

        async def on_err(msg: str):
            set_status(f"❌ {msg}", C["red"])
            btn_new_run.disabled = False
            btn_seed_run.disabled = False
            btn_quick.disabled = False; _gen_state(False)
            progress.visible = False
            page.update()

        async def on_done():
            elapsed = time.time() - _start_time
            set_status(f"✅ Done! ({elapsed:.1f}s)", C["green"])
            btn_new_run.disabled = False
            btn_seed_run.disabled = False
            btn_quick.disabled = False; _gen_state(False)
            progress.visible = False
            page.update()

        asyncio.create_task(run_workflow(wf, on_img, on_err, on_done, stop_event=_stop_event, on_stop=_on_gen_stopped))

    btn_new_run.on_click  = lambda _: asyncio.ensure_future(_do_run(force_new_seed=True))
    btn_seed_run.on_click = lambda _: asyncio.ensure_future(_do_run(force_new_seed=False))

    async def _do_quick_run(_):
        """Quick Gen: 2304×1296, steps=8, same seed."""
        prompt_text = editor_field.value.strip()
        if not prompt_text:
            set_status("⚠ No prompt", C["amber"]); return
        api_fn = api_dd.value
        if not api_fn:
            set_status("⚠ Workflow not selected", C["amber"]); return
        api_path = API_DIR / api_fn
        if not api_path.exists():
            set_status(f"⚠ File not found: {api_fn}", C["red"]); return

        run_prompt_idx = sel[0]
        btn_new_run.disabled = True
        btn_seed_run.disabled = True
        btn_quick.disabled = True; _gen_state(True)
        progress.visible = True
        _start_time = time.time()
        set_status("⚡ 0s", C["amber"])

        async def _tick():
            while progress.visible:
                elapsed = time.time() - _start_time
                set_status(f"⚡ {elapsed:.0f}s", C["amber"])
                await asyncio.sleep(1)
        asyncio.create_task(_tick())

        try:
            wf = json.loads(api_path.read_text(encoding="utf-8"))
            wf = inject_prompt(wf, prompt_text)
            fixed = _current_seed[0] if _current_seed[0] >= 0 else -1
            wf, used_seed = inject_seed(wf, fixed)
            _current_seed[0] = used_seed
            seed_field.value = str(used_seed)
            # read actual size/steps from workflow nodes for meta display
            _q_size = ""; _q_steps = ""
            for node in wf.values():
                if not isinstance(node, dict): continue
                inp = node.get("inputs", {})
                if node.get("class_type", "") in _LATENT_CLS and "width" in inp:
                    _q_size = f"{inp['width']}×{inp['height']}"
                if node.get("class_type", "") in _SCLS and "steps" in inp:
                    _q_steps = inp["steps"]
        except Exception as e:
            set_status(f"❌ Failed to load workflow: {e}", C["red"])
            btn_new_run.disabled = False; btn_seed_run.disabled = False
            btn_quick.disabled = False; _gen_state(False); progress.visible = False; page.update(); return

        async def on_img(path: str, _pi=run_prompt_idx):
            data = Path(path).read_bytes()
            imgs = _prompt_images.setdefault(_pi, [])
            label = f"⚡#{len(imgs)+1} {time.strftime('%H:%M')}"
            imgs.append({"path": path, "seed": _current_seed[0], "label": label,
                         "api": api_fn, "size": _q_size, "steps": _q_steps})
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

        async def on_err(msg: str):
            set_status(f"❌ {msg}", C["red"])
            btn_new_run.disabled = False; btn_seed_run.disabled = False
            btn_quick.disabled = False; _gen_state(False); progress.visible = False; page.update()

        async def on_done():
            elapsed = time.time() - _start_time
            set_status(f"⚡ Quick Run done ({elapsed:.1f}s)", C["green"])
            btn_new_run.disabled = False; btn_seed_run.disabled = False
            btn_quick.disabled = False; _gen_state(False); progress.visible = False; page.update()

        asyncio.create_task(run_workflow(wf, on_img, on_err, on_done, stop_event=_stop_event, on_stop=_on_gen_stopped))

    btn_quick.on_click = _do_quick_run

    def on_refresh_api(_):
        opts = [ft.dropdown.Option(f) for f in scan_apis()]
        api_dd.options = opts
        api_dd.value   = None
        set_status(f"🔄 Found {len(opts)} workflow(s)", C["text2"])
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
        expand=True,
        content=ft.Column(spacing=8, expand=True, controls=[
            ft.Row([
                ft.Container(content=api_dd, expand=True),
                ft.IconButton(
                    ft.Icons.REFRESH, icon_color=C["text2"], tooltip="Refresh API list",
                    on_click=on_refresh_api,
                    style=ft.ButtonStyle(padding=po(4,4,4,4)),
                ),
            ], spacing=4),
            ft.Row([size_dd, steps_dd], spacing=6, tight=True),
            ft.Row([seed_field, btn_seed_run, btn_quick], spacing=6, tight=True,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([btn_new_run, btn_stop], spacing=6, tight=True),
        ]),
    )

    right_container = ft.Container(
        expand=True,
        bgcolor=C["surface"],
        padding=po(left=12, top=12, right=12, bottom=10),
        content=ft.Column(spacing=8, expand=True, controls=[
            ft.Text("Generation", size=13, weight=ft.FontWeight.BOLD, color=C["text1"]),
            ft.Row([thumb_col, ft.VerticalDivider(width=1, color=C["border"]), ctrl_col],
                   spacing=10, expand=False, vertical_alignment=ft.CrossAxisAlignment.START),
            ft.Divider(height=1, color=C["border"]),
            preview_stack,
        ]),
    )

    def on_right_drag(e: ft.DragUpdateEvent):
        # drag left = negative delta_x = expand the right side
        mid_panel.width = max(180, (mid_panel.width or 300) + e.local_delta.x)
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

    page.add(ft.Column(spacing=0, expand=True, controls=[
        title_bar,
        ft.Row(spacing=0, expand=True, controls=[
            left_container,
            _resize_divider(on_left_drag),
            mid_panel,
            _resize_divider(on_right_drag),
            right_container,
        ]),
    ]))
    _update_check.start_notice(ft, page, APP_NAME, APP_VERSION)      # a notice only (GitHub releases, once a day, silent when offline); see update_check.py

    refresh_list(sel[0])
    load_thumb_for_prompt(sel[0])
    # restore image tabs for the initially selected prompt
    _imgs0 = _prompt_images.get(sel[0], [])
    _active_img_tab[0] = max(0, len(_imgs0) - 1)
    if _imgs0:
        _show_tab(sel[0], _active_img_tab[0])
    _rebuild_tabs()

ft.app(target=main)