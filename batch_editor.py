"""
Batch Editor - Flet 0.85.3
Started from working code + added Prompt editor + ComfyUI API
"""
import asyncio
import json
import random
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import flet as ft

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

# ── Config ───────────────────────────────────────────────────────────
COMFY_HOST = "127.0.0.1:8188"
# Folder with comfy_apis/ (image, llm, caption, enhance), icons/: the project folder, or (PyInstaller) the folder next to the EXE.
APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
API_DIR = APP_ROOT / "comfy_apis" / "image"         # Folder containing the .json workflow files (exported with Export (API))
APP_ID      = "batch_editor"
APP_NAME    = "Batch Editor"
APP_VERSION = "0.1.0"   # SemVer; see RELEASE.md
USER_CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "WorkflowPad"     # user data: never next to the program
PROMPT_SAVE_PATH = USER_CONFIG_DIR / "saved_prompt.json"
try:
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if (APP_ROOT / "saved_prompt.json").is_file() and not PROMPT_SAVE_PATH.exists():      # one-time copy from an earlier build (the old file is kept)
        shutil.copy2(APP_ROOT / "saved_prompt.json", PROMPT_SAVE_PATH)
except OSError:
    pass


def scan_api_files() -> list[str]:
    """Return the list of .json filenames in the comfy_apis/image folder (excluding saved_prompt.json)"""
    files = sorted(
        p.name for p in API_DIR.glob("*.json")
        if p.name != PROMPT_SAVE_PATH.name
    )
    return files or ["No .json files found"]

# ── Helpers ──────────────────────────────────────────────────────────
def ps(h=0, v=0):
    return ft.Padding(left=h, top=v, right=h, bottom=v)

def po(left=0, top=0, right=0, bottom=0):
    return ft.Padding(left=left, top=top, right=right, bottom=bottom)

def br(r):
    return ft.BorderRadius(r, r, r, r)

def card_shadow():
    return ft.BoxShadow(
        spread_radius=0, blur_radius=8,
        color="#00000022", offset=ft.Offset(0, 2),
    )

# ── ComfyUI API ──────────────────────────────────────────────────────
def comfy_upload_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        data = f.read()
    filename = Path(image_path).name
    boundary = "----Boundary" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"http://{COMFY_HOST}/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["name"]

def comfy_queue(workflow: dict, client_id: str) -> str:
    payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode()
    req = urllib.request.Request(
        f"http://{COMFY_HOST}/prompt", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["prompt_id"]

def comfy_history(prompt_id: str) -> dict:
    with urllib.request.urlopen(
        f"http://{COMFY_HOST}/history/{prompt_id}", timeout=10
    ) as r:
        return json.loads(r.read())

def comfy_cancel(prompt_id: str) -> None:
    """Cancel a job: remove it from the queue and, if it is the one running, interrupt it."""
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"http://{COMFY_HOST}/queue", data=json.dumps({"delete": [prompt_id]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST"), timeout=5)
    except Exception:
        pass
    running = None
    try:
        with urllib.request.urlopen(f"http://{COMFY_HOST}/queue", timeout=5) as r:
            running = [it[1] for it in json.loads(r.read()).get("queue_running", []) if len(it) > 1]
    except Exception:
        pass
    if running is None or prompt_id in running:   # never interrupt somebody else's job
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"http://{COMFY_HOST}/interrupt", data=b"",
                headers={"Content-Type": "application/json"}, method="POST"), timeout=5)
        except Exception:
            pass

_ACTIVE_PID: list = [None]     # prompt_id of the job being waited on (so Stop can cancel it at once)

def comfy_get_image(filename: str, subfolder: str, folder_type: str) -> bytes:
    params = urllib.parse.urlencode(
        {"filename": filename, "subfolder": subfolder, "type": folder_type}
    )
    with urllib.request.urlopen(f"http://{COMFY_HOST}/view?{params}", timeout=30) as r:
        return r.read()

def load_workflow(filename: str | None = None) -> dict:
    path = API_DIR / filename if filename else list(API_DIR.glob("*.json"))[0]
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# ── Workflow auto-detector ────────────────────────────────────────────
# class_type priority lists — search from top to bottom and stop at the first match

_IMAGE_INPUT_CLASSES = [
    "LoadImage", "ETN_LoadImageBase64", "LoadImageMask",
    "ImageBatch", "LoadImageFromUrl", "LoadImagePath",
]

_PROMPT_CLASSES = [
    # Flux / SD3 text encoders first
    "CLIPTextEncodeFlux", "CLIPTextEncodeSD3", "CLIPTextEncodeHunyuan",
    # smZ / advanced encoders
    "smZ CLIPTextEncode", "BNK_CLIPTextEncodeAdvanced",
    # standard
    "CLIPTextEncode",
    # fallback: node with an input field named "text" or "prompt"
]

_SEED_CLASSES = [
    "RandomNoise", "KSamplerAdvanced", "KSampler",
    "SamplerCustomAdvanced", "SamplerCustom",
    "FluxGuidance",   # Some workflows embed the seed here
]
_SEED_FIELDS = ["noise_seed", "seed", "rand_seed"]

# Positive-prompt nodes often have a title/meta containing "positive" or "pos"
_POSITIVE_HINTS = {"positive", "pos", "prompt", "clip positive", "positive prompt"}


def _node_title(node_id: str, wf: dict) -> str:
    """Get _meta.title if present"""
    return (wf.get(node_id, {}).get("_meta", {}).get("title", "") or "").lower()


def detect_workflow_mapping(wf: dict) -> dict:
    """
    Analyze the workflow dict and return a mapping:
    {
        "image_node":  str | None,   # node id
        "image_field": str,          # input field name (usually "image")
        "prompt_nodes": [            # can include multiple nodes (positive, negative, ...)
            {"node": str, "field": str, "role": "positive"|"negative"|"unknown"}
        ],
        "seed_node":   str | None,
        "seed_field":  str,
    }
    """
    image_node = image_field = None
    prompt_hits: list[dict] = []
    seed_node = seed_field = None

    # ── 1. image input ──────────────────────────────────────────────
    for nid, node in wf.items():
        ct = node.get("class_type", "")
        if ct in _IMAGE_INPUT_CLASSES:
            inputs = node.get("inputs", {})
            # Use the "image" field first; if unavailable, use the first non-list field
            if "image" in inputs:
                image_node, image_field = nid, "image"
            elif not image_node:
                for k, v in inputs.items():
                    if not isinstance(v, list):   # list = linked node
                        image_node, image_field = nid, k
                        break
            if image_node:
                break

    # ── 2. prompt / text nodes ──────────────────────────────────────
    # Collect all candidates first, then sort by priority
    candidates: list[tuple[int, str, str, str]] = []  # (priority, nid, field, role)

    for nid, node in wf.items():
        ct = node.get("class_type", "")
        inputs = node.get("inputs", {})
        title = _node_title(nid, wf)

        # Find priority from class_type
        prio = next((i for i, c in enumerate(_PROMPT_CLASSES) if c == ct), None)

        # fallback: class containing "clip" and "encode" in its name
        if prio is None and "clip" in ct.lower() and "encode" in ct.lower():
            prio = len(_PROMPT_CLASSES)

        # fallback: node with a string input field named "text" or "prompt"
        if prio is None:
            for fld in ("text", "prompt", "positive", "negative"):
                if fld in inputs and isinstance(inputs[fld], str):
                    prio = len(_PROMPT_CLASSES) + 10
                    break

        if prio is None:
            continue

        # Find the string text field
        text_field = None
        for fld in ("text", "prompt", "positive", "negative"):
            if fld in inputs and isinstance(inputs[fld], str):
                text_field = fld
                break
        if text_field is None:
            # Use the first string field
            for k, v in inputs.items():
                if isinstance(v, str) and len(v) > 0:
                    text_field = k
                    break
        if text_field is None:
            continue

        # Infer the role from the title meta
        role = "unknown"
        if any(h in title for h in _POSITIVE_HINTS):
            role = "positive"
        elif "neg" in title:
            role = "negative"

        candidates.append((prio, nid, text_field, role))

    # Sort by priority and prefer positive roles first
    candidates.sort(key=lambda x: (x[0], 0 if x[3] == "positive" else 1))

    # If there is no role at all, treat the first node as positive
    seen_positive = False
    for prio, nid, fld, role in candidates:
        if role == "unknown" and not seen_positive:
            role = "positive"
        if role == "positive":
            seen_positive = True
        prompt_hits.append({"node": nid, "field": fld, "role": role})

    # ── 3. seed node ────────────────────────────────────────────────
    for cls in _SEED_CLASSES:
        for nid, node in wf.items():
            if node.get("class_type", "") == cls:
                inputs = node.get("inputs", {})
                for sf in _SEED_FIELDS:
                    if sf in inputs:
                        seed_node, seed_field = nid, sf
                        break
                if seed_node:
                    break
        if seed_node:
            break

    # fallback seed: any node with a noise_seed / seed field
    if not seed_node:
        for nid, node in wf.items():
            inputs = node.get("inputs", {})
            for sf in _SEED_FIELDS:
                if sf in inputs and isinstance(inputs[sf], (int, float)):
                    seed_node, seed_field = nid, sf
                    break
            if seed_node:
                break

    return {
        "image_node": image_node,
        "image_field": image_field or "image",
        "prompt_nodes": prompt_hits,
        "seed_node": seed_node,
        "seed_field": seed_field or "noise_seed",
    }


def build_workflow(uploaded_filename: str, prompt_text: str,
                   api_filename: str | None = None) -> tuple[dict, dict]:
    """
    Return (workflow_dict, mapping_used)
    mapping_used is used to show debug info in the UI
    """
    wf = load_workflow(api_filename)
    mapping = detect_workflow_mapping(wf)

    # inject image
    if mapping["image_node"]:
        wf[mapping["image_node"]]["inputs"][mapping["image_field"]] = uploaded_filename

    # Inject prompt into the positive node only
    for ph in mapping["prompt_nodes"]:
        if ph["role"] in ("positive", "unknown"):
            wf[ph["node"]]["inputs"][ph["field"]] = prompt_text
            break   # inject only the first matching node

    # inject seed
    if mapping["seed_node"]:
        wf[mapping["seed_node"]]["inputs"][mapping["seed_field"]] = random.randint(0, 2**53)

    return wf, mapping

def _load_settings() -> dict:
    if PROMPT_SAVE_PATH.exists():
        try:
            return json.loads(PROMPT_SAVE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def load_saved_prompt() -> str:
    s = _load_settings()
    if s.get("prompt"):
        return s["prompt"]
    try:
        api_files = scan_api_files()
        if not api_files:
            return ""
        wf = load_workflow(api_files[0])
        mapping = detect_workflow_mapping(wf)
        for ph in mapping["prompt_nodes"]:
            if ph["role"] in ("positive", "unknown"):
                val = wf[ph["node"]]["inputs"].get(ph["field"], "")
                if isinstance(val, str):
                    return val
    except Exception:
        pass
    return ""

def load_saved_folder() -> str:
    return _load_settings().get("output_folder", "")

def load_saved_api() -> str:
    return _load_settings().get("selected_api", "")

def load_prompt_history() -> list[str]:
    return _load_settings().get("prompt_history", [])

MAX_PROMPT_HISTORY = 50

def save_settings(prompt: str, folder: str, selected_api: str = "",
                  add_to_history: bool = False):
    s = _load_settings()
    history: list[str] = s.get("prompt_history", [])
    if add_to_history and prompt.strip():
        # Remove duplicates first, then add the new item to the top
        history = [h for h in history if h.strip() != prompt.strip()]
        history.insert(0, prompt.strip())
        history = history[:MAX_PROMPT_HISTORY]
    PROMPT_SAVE_PATH.write_text(
        json.dumps({
            "prompt": prompt,
            "output_folder": folder,
            "selected_api": selected_api,
            "prompt_history": history,
        }, ensure_ascii=False, indent=2), encoding="utf-8"
    )

def delete_prompt_from_history(index: int):
    s = _load_settings()
    history: list[str] = s.get("prompt_history", [])
    if 0 <= index < len(history):
        history.pop(index)
    PROMPT_SAVE_PATH.write_text(
        json.dumps({
            "prompt": s.get("prompt", ""),
            "output_folder": s.get("output_folder", ""),
            "selected_api": s.get("selected_api", ""),
            "prompt_history": history,
        }, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return history

async def run_comfy_job(image_path: str, prompt_text: str, out_dir: Path,
                        on_progress=None, api_filename: str | None = None,
                        stop_event: asyncio.Event | None = None) -> str | None:
    """Polling as before + update the progress text on each poll"""
    loop = asyncio.get_event_loop()
    client_id = uuid.uuid4().hex
    uploaded = await loop.run_in_executor(None, comfy_upload_image, image_path)
    wf, _mapping = build_workflow(uploaded, prompt_text, api_filename)
    prompt_id = await loop.run_in_executor(None, comfy_queue, wf, client_id)
    _ACTIVE_PID[0] = prompt_id

    tick = 0
    deadline = time.time() + 300
    try:
        while time.time() < deadline:
            if stop_event is not None and stop_event.is_set():
                await loop.run_in_executor(None, comfy_cancel, prompt_id)
                return None
            await asyncio.sleep(2)
            tick += 1
            if on_progress:
                on_progress(tick, 0, f"🔄 generating... ({tick*2}s)")

            history = await loop.run_in_executor(None, comfy_history, prompt_id)
            if prompt_id in history:
                for node_out in history[prompt_id].get("outputs", {}).values():
                    if "images" in node_out:
                        info = node_out["images"][0]
                        img_bytes = await loop.run_in_executor(
                            None, comfy_get_image,
                            info["filename"], info.get("subfolder", ""), info.get("type", "output")
                        )
                        out_dir.mkdir(parents=True, exist_ok=True)
                        out_path = out_dir / f"{Path(image_path).stem}_comfy.jpg"
                        out_path.write_bytes(img_bytes)
                        return str(out_path)
                break
    finally:
        _ACTIVE_PID[0] = None
    return None

# ── Main ─────────────────────────────────────────────────────────────
async def main(page: ft.Page):
    page.title = f"{APP_NAME} {APP_VERSION}"
    _icon = APP_ROOT / "icons" / "batch_editor.ico"
    if _icon.exists():
        page.window.icon = str(_icon)
    page.bgcolor = "#E2E2E2"
    page.padding = 0
    page.spacing = 0

    selected_files: list[str] = []
    output_dir: list[str] = []
    card_map: dict[str, ft.Container] = {}

    # Load the saved folder
    _saved_folder = load_saved_folder()
    if _saved_folder:
        output_dir.append(_saved_folder)

    # ── HEADER ───────────────────────────────────────────────────────
    header = ft.Container(
        content=ft.Row([
            ft.Text(APP_NAME, size=15, weight=ft.FontWeight.W_700, color="#1A1A1A"),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        bgcolor="#FFFFFF",
        padding=ps(h=20, v=11),
        border=ft.Border(bottom=ft.BorderSide(1, "#E0E0E0")),
        height=46,
    )

    # ── CARD builder ─────────────────────────────────────────────────
    def make_card(src: str, label: str, badge: str | None = None,
                  done: bool = False) -> ft.Container:
        badge_color = "#27AE60" if done else ("#2979FF" if badge == "JPEG" else "#555")
        badge_ctrl = ft.Container(
            content=ft.Text(badge, size=8, color="#FFF", weight=ft.FontWeight.W_700),
            bgcolor=badge_color,
            border_radius=br(3),
            padding=po(left=5, right=5, top=2, bottom=2),
            visible=badge is not None,
        )
        check_icon = ft.Container(
            content=ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, color="#27AE60", size=26),
            alignment=ft.Alignment(1, -1), padding=4, visible=done,
        )
        return ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Stack([
                        ft.Image(src=src, fit=ft.BoxFit.COVER, width=200, height=110),
                        check_icon,
                    ]),
                    border_radius=ft.BorderRadius(6, 6, 0, 0),
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    bgcolor="#CCCCCC",
                    height=110,
                ),
                ft.Container(
                    content=ft.Row([
                        ft.Text(label, size=10, color="#444",
                                overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                        badge_ctrl,
                    ], spacing=4),
                    padding=po(left=8, right=8, top=6, bottom=6),
                ),
            ], spacing=0),
            bgcolor="#FFFFFF",
            border_radius=br(6),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            shadow=card_shadow(),
            width=200,
            border=ft.Border(
                top=ft.BorderSide(2, "#27AE60"), right=ft.BorderSide(2, "#27AE60"),
                bottom=ft.BorderSide(2, "#27AE60"), left=ft.BorderSide(2, "#27AE60"),
            ) if done else None,
        )

    def set_card_loading(card: ft.Container, prog_text_ref: list):
        """Place a spinner overlay over the input card and return a Text ref for progress updates"""
        prog_lbl = ft.Text("⏳ Waiting in queue...", size=9, color="#FFF",
                           text_align=ft.TextAlign.CENTER,
                           weight=ft.FontWeight.W_600)
        prog_text_ref.append(prog_lbl)
        overlay = ft.Container(
            content=ft.Column([
                ft.ProgressRing(width=22, height=22, stroke_width=2, color="#FFF"),
                prog_lbl,
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),
            alignment=ft.Alignment(0, 0),
            bgcolor="#00000077",
            border_radius=ft.BorderRadius(6, 6, 0, 0),
            height=110,
        )
        # The thumbnail container is content.controls[0]
        thumb_stack = card.content.controls[0].content
        if isinstance(thumb_stack, ft.Stack):
            thumb_stack.controls.append(overlay)
        else:
            # fallback: wrap it in a Stack
            card.content.controls[0].content = ft.Stack([thumb_stack, overlay])

    def clear_card_loading(card: ft.Container):
        """Remove the overlay"""
        thumb_stack = card.content.controls[0].content
        if isinstance(thumb_stack, ft.Stack) and len(thumb_stack.controls) > 1:
            thumb_stack.controls.pop()


    # ── RIGHT PANEL ───────────────────────────────────────────────────
    output_col = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=8, expand=True)
    status_text = ft.Text("", size=11, color="#666", italic=True,
                           text_align=ft.TextAlign.CENTER)
    _init_folder = load_saved_folder()
    folder_text = ft.Text(
        Path(_init_folder).name if _init_folder else "Not selected yet",
        size=10,
        color="#2979FF" if _init_folder else "#999",
        italic=not bool(_init_folder),
        overflow=ft.TextOverflow.ELLIPSIS,
        expand=True,
    )

    # FilePicker remains unchanged
    async def pick_folder(e):
        fp = ft.FilePicker()
        result = await fp.get_directory_path(dialog_title="Select Output Folder")
        if result:
            output_dir.clear()
            output_dir.append(result)
            folder_text.value = Path(result).name
            folder_text.color = "#2979FF"
            folder_text.italic = False
            page.update()

    folder_btn = ft.FilledButton(
        content=ft.Text("📁 Select", size=11, color="#FFF"),
        on_click=pick_folder,
        bgcolor="#555",
        height=30,
    )

    # ── API selector ──────────────────────────────────────────────────
    _api_files = scan_api_files()
    _saved_api = load_saved_api()
    _api_init = _saved_api if _saved_api in _api_files else (_api_files[0] if _api_files else "")

    selected_api: list[str] = [_api_init]

    # mapping info labels (shown below the dropdown)
    def _make_tag(name: str, status: str, ok: bool | None = True, detail: str = "") -> ft.Container:
        """Status chip: "<name> · <status>". Raw node ids stay available in the tooltip (detail)."""
        if ok is True:
            bg, txt_color, dot_color = "#EBF8EF", "#1E7E3E", "#27AE60"
        elif ok is False:
            bg, txt_color, dot_color = "#FEECEB", "#C0392B", "#E53935"
        else:  # None = neutral/gray
            bg, txt_color, dot_color = "#F4F4F4", "#777", "#AAAAAA"
        return ft.Container(
            content=ft.Row([
                ft.Container(width=6, height=6, bgcolor=dot_color,
                             border_radius=br(3)),
                ft.Text(f"{name} · {status}", size=10, color=txt_color,
                        weight=ft.FontWeight.W_500,
                        overflow=ft.TextOverflow.ELLIPSIS),
            ], spacing=5, tight=True),
            bgcolor=bg,
            border_radius=br(10),
            padding=po(left=7, right=9, top=3, bottom=3),
            tooltip=detail or None,
        )

    map_image_tag  = _make_tag("Image", "—", None)
    map_prompt_tag = _make_tag("Prompt", "—", None)
    map_seed_tag   = _make_tag("Seed", "—", None)
    map_warn_lbl   = ft.Text("", size=9, color="#E53935", italic=True)

    mapping_info = ft.Container(
        content=ft.Column([
            ft.Row([map_image_tag, map_prompt_tag, map_seed_tag],
                   spacing=4, wrap=True),
            map_warn_lbl,
        ], spacing=4),
        padding=po(left=0, right=0, top=4, bottom=0),
        visible=False,
    )

    def _refresh_mapping_info(api_filename: str):
        try:
            wf = load_workflow(api_filename)
            m = detect_workflow_mapping(wf)
            warns = []

            if m["image_node"]:
                new_img = _make_tag("Image", "Ready", True, f"Image node {m['image_node']}")
            else:
                new_img = _make_tag("Image", "Missing", False)
                warns.append("Image")

            if m["prompt_nodes"]:
                pos = next((p for p in m["prompt_nodes"]
                            if p["role"] in ("positive", "unknown")), None)
                if pos:
                    new_txt = _make_tag("Prompt", "Ready", True, f"Prompt node {pos['node']}")
                else:
                    new_txt = _make_tag("Prompt", "Missing", False)
                    warns.append("Prompt")
            else:
                new_txt = _make_tag("Prompt", "Missing", False)
                warns.append("Prompt")

            if m["seed_node"]:
                new_seed = _make_tag("Seed", "Ready", True, f"Seed node {m['seed_node']}")
            else:
                new_seed = _make_tag("Seed", "Fixed", None, "No seed node detected — the workflow keeps its own seed")

            # swap controls in the Row
            tag_row = mapping_info.content.controls[0]
            tag_row.controls[0] = new_img
            tag_row.controls[1] = new_txt
            tag_row.controls[2] = new_seed

            map_warn_lbl.value = (
                f"Workflow is missing: {', '.join(warns)}" if warns else ""
            )
            mapping_info.visible = True
        except Exception as ex:
            tag_row = mapping_info.content.controls[0]
            tag_row.controls[0] = _make_tag("Image", "—", None)
            tag_row.controls[1] = _make_tag("Prompt", "—", None)
            tag_row.controls[2] = _make_tag("Seed", "—", None)
            map_warn_lbl.value = f"Couldn't load workflow: {ex}"
            mapping_info.visible = True

    if _api_init:
        _refresh_mapping_info(_api_init)

    api_dropdown = ft.Dropdown(
        options=[ft.dropdown.Option(f) for f in _api_files],
        value=_api_init or None,
        hint_text="Select workflow",
        text_size=11,
        height=40,
        border_radius=br(8),
        border_color="#DDDDDD",
        focused_border_color="#2979FF",
        bgcolor="#FFFFFF",
        content_padding=po(left=10, right=6, top=0, bottom=0),
    )

    refresh_lbl = ft.Text("", size=10, color="#27AE60")

    # ── API change SnackBar ───────────────────────────────────────────
    api_snack = ft.SnackBar(
        content=ft.Text("", color="#FFF", size=12, weight=ft.FontWeight.W_600),
        bgcolor="#27AE60",
        duration=2500,
    )
    page.overlay.append(api_snack)

    def api_changed(e):
        val = api_dropdown.value or ""
        selected_api.clear()
        selected_api.append(val)
        if val:
            _refresh_mapping_info(val)
            api_snack.content.value = f"✓ Changed Workflow to {val}"
            api_snack.open = True
        page.update()

    def refresh_apis(e):
        files = scan_api_files()
        api_dropdown.options = [ft.dropdown.Option(f) for f in files]
        if selected_api[0] not in files:
            api_dropdown.value = files[0] if files else None
            selected_api.clear()
            selected_api.append(api_dropdown.value or "")
        if api_dropdown.value:
            _refresh_mapping_info(api_dropdown.value)
        refresh_lbl.value = f"{len(files)} workflow{'' if len(files) == 1 else 's'} found"
        page.update()
        async def _clear():
            await asyncio.sleep(2)
            refresh_lbl.value = ""
            page.update()
        asyncio.create_task(_clear())

    api_dropdown.on_select = api_changed   # on_select instead of on_change (Flet 0.85.3+)

    # ── Prompt editor + History ───────────────────────────────────────
    prompt_field = ft.TextField(
        value=load_saved_prompt(),
        multiline=True, min_lines=4, max_lines=7,
        hint_text="Positive prompt...",
        text_size=11, border_color="#CCCCCC",
        focused_border_color="#2979FF", bgcolor="#FFFFFF",
        content_padding=po(left=8, right=8, top=6, bottom=6),
        expand=True,
    )
    prompt_save_lbl = ft.Text("", size=10, color="#27AE60")

    # ── History bottom sheet ──────────────────────────────────────────
    history_list_col = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=0, expand=True)
    history_empty_lbl = ft.Text(
        "No prompt history yet", size=11, color="#AAA", italic=True,
        text_align=ft.TextAlign.CENTER,
    )

    def _make_history_row(idx: int, text: str) -> ft.Container:
        preview = text[:80].replace("\n", " ") + ("…" if len(text) > 80 else "")

        def _use(e, t=text):
            prompt_field.value = t
            history_bs.open = False
            page.update()

        def _del(e, i=idx):
            new_hist = delete_prompt_from_history(i)
            _rebuild_history_list(new_hist)
            page.update()

        return ft.Container(
            content=ft.Row([
                ft.Container(
                    content=ft.Text(preview, size=10, color="#333",
                                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                    expand=True,
                    on_click=_use,
                    ink=True,
                    border_radius=br(4),
                    padding=po(left=8, right=4, top=6, bottom=6),
                ),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                    icon_color="#CCCCCC",
                    icon_size=16,
                    tooltip="Delete",
                    on_click=_del,
                    width=32, height=32,
                    padding=0,
                ),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
            border=ft.Border(bottom=ft.BorderSide(1, "#F0F0F0")),
        )

    def _rebuild_history_list(history: list[str]):
        history_list_col.controls.clear()
        if not history:
            history_list_col.controls.append(
                ft.Container(history_empty_lbl, padding=po(top=20), alignment=ft.Alignment(0, 0))
            )
        else:
            for i, txt in enumerate(history):
                history_list_col.controls.append(_make_history_row(i, txt))

    _rebuild_history_list(load_prompt_history())

    history_bs = ft.BottomSheet(
        content=ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Row([
                        ft.Text("📋  Prompt History", size=13,
                                weight=ft.FontWeight.W_700, color="#1A1A1A",
                                expand=True),
                        ft.IconButton(
                            icon=ft.Icons.CLOSE_ROUNDED,
                            icon_color="#888", icon_size=18,
                            on_click=lambda e: setattr(history_bs, "open", False) or page.update(),
                            width=32, height=32, padding=0,
                        ),
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=po(left=16, right=8, top=14, bottom=10),
                    border=ft.Border(bottom=ft.BorderSide(1, "#EBEBEB")),
                ),
                history_list_col,
            ], spacing=0, expand=True),
            height=420,
            bgcolor="#FFFFFF",
            border_radius=ft.BorderRadius(12, 12, 0, 0),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        ),
        open=False,
    )
    page.overlay.append(history_bs)

    def open_history(e):
        _rebuild_history_list(load_prompt_history())
        history_bs.open = True
        page.update()

    def save_prompt(e):
        txt = prompt_field.value or ""
        save_settings(txt,
                      output_dir[0] if output_dir else "",
                      selected_api[0] if selected_api else "",
                      add_to_history=True)
        _rebuild_history_list(load_prompt_history())
        prompt_save_lbl.value = "✓ Saved"
        page.update()
        async def clear():
            await asyncio.sleep(2)
            prompt_save_lbl.value = ""
            page.update()
        asyncio.create_task(clear())

    # ── Run (changed from PIL → ComfyUI) ──────────────────────────────
    run_label = ft.Text("▶  Start Run", color="#FFF", size=13,
                         weight=ft.FontWeight.W_600)

    _stop_event = asyncio.Event()

    async def run_batch(e):
        if not selected_files:
            status_text.value = "⚠  No image selected yet"
            page.update(); return
        if not output_dir:
            status_text.value = "⚠  Please select an Output Folder"
            page.update(); return

        _stop_event.clear()
        run_btn.disabled = True
        run_btn.visible = False
        stop_btn.visible = True
        stop_btn.disabled = False
        run_label.value = "Generating..."
        status_text.value = ""
        page.update()

        prompt_text = prompt_field.value or ""
        out_dir = Path(output_dir[0])
        save_settings(prompt_text, str(out_dir),
                      selected_api[0] if selected_api else "",
                      add_to_history=True)
        total      = len(selected_files)
        done_count = 0
        job_times: list[float] = []   # Store each job time (seconds)
        eta_task: list[asyncio.Task] = []

        def _fmt_time(sec: float) -> str:
            sec = int(sec)
            if sec < 60:   return f"{sec}s"
            if sec < 3600: return f"{sec//60}m {sec%60:02d}s"
            return f"{sec//3600}h {(sec%3600)//60:02d}m"

        async def _eta_countdown(eta_end: float, remaining: int):
            """Update the status every 1 second and show an ETA countdown"""
            while True:
                left = eta_end - time.time()
                if left <= 0: break
                avg = sum(job_times) / len(job_times) if job_times else 0
                status_text.value = (
                    f"⏳ {done_count+1}/{total}  "
                    f"│ Average {_fmt_time(avg)}/image  "
                    f"│ ETA {_fmt_time(left)}"
                )
                page.update()
                await asyncio.sleep(1)

        def replace_card_with_result(card: ft.Container, out_path: str):
            thumb_stack = card.content.controls[0].content
            if isinstance(thumb_stack, ft.Stack):
                thumb_stack.controls[0].src = out_path
            footer_row = card.content.controls[1].content
            footer_row.controls[0].value = Path(out_path).name
            badge = footer_row.controls[1]
            badge.content.value = "✓ DONE"
            badge.bgcolor = "#27AE60"
            badge.visible = True
            card.border = ft.Border(
                top=ft.BorderSide(2, "#27AE60"), right=ft.BorderSide(2, "#27AE60"),
                bottom=ft.BorderSide(2, "#27AE60"), left=ft.BorderSide(2, "#27AE60"),
            )

        def mark_card_error(card: ft.Container):
            badge = card.content.controls[1].content.controls[1]
            badge.content.value = "❌ ERR"
            badge.bgcolor = "#E53935"
            badge.visible = True
            card.border = ft.Border(
                top=ft.BorderSide(2, "#E53935"), right=ft.BorderSide(2, "#E53935"),
                bottom=ft.BorderSide(2, "#E53935"), left=ft.BorderSide(2, "#E53935"),
            )

        for idx, path in enumerate(selected_files):
            if _stop_event.is_set():
                break
            remaining = total - idx
            input_card = card_map.get(path)
            prog_ref: list = []
            if input_card:
                set_card_loading(input_card, prog_ref)

            # Calculate ETA from the available average
            avg = sum(job_times) / len(job_times) if job_times else 0
            if avg > 0:
                eta_end = time.time() + avg * remaining
                # Cancel the previous countdown first
                if eta_task and not eta_task[0].done():
                    eta_task[0].cancel()
                eta_task.clear()
                eta_task.append(asyncio.create_task(_eta_countdown(eta_end, remaining)))
            else:
                status_text.value = f"⏳  {idx+1}/{total}  {Path(path).name}"
            page.update()

            def make_progress_cb(pref):
                def cb(step, total_steps, txt):
                    if pref:
                        pref[0].value = txt
                        page.update()
                return cb

            t_start = time.time()
            try:
                current_api = api_dropdown.value or (selected_api[0] if selected_api else None)
                out_path = await run_comfy_job(
                    path, prompt_text, out_dir,
                    on_progress=make_progress_cb(prog_ref),
                    api_filename=current_api,
                    stop_event=_stop_event,
                )
                elapsed = time.time() - t_start
                if elapsed > 2:   # Avoid overcounting upload/network overhead
                    job_times.append(elapsed)

                if input_card:
                    clear_card_loading(input_card)
                if out_path:
                    done_count += 1
                    if input_card:
                        replace_card_with_result(input_card, out_path)
                else:
                    if input_card and not _stop_event.is_set():   # a stopped job is not an error
                        mark_card_error(input_card)
            except Exception as ex:
                if input_card:
                    clear_card_loading(input_card)
                    mark_card_error(input_card)
                print(f"Error {path}: {ex}")
            page.update()

        # Cancel any pending countdown
        if eta_task and not eta_task[0].done():
            eta_task[0].cancel()

        avg_final = sum(job_times) / len(job_times) if job_times else 0
        avg_str   = f"  │ Average {_fmt_time(avg_final)}/image" if avg_final else ""
        if _stop_event.is_set():
            status_text.value = f"⏹  Stopped — {done_count}/{total} file(s) done{avg_str}"
        else:
            status_text.value = f"✓  Completed {done_count}/{total} file(s){avg_str}"
        run_btn.disabled = False
        run_btn.visible = True
        stop_btn.visible = False
        run_label.value = "▶  Start Run"
        page.update()

    async def stop_batch(e):
        _stop_event.set()
        stop_btn.disabled = True
        status_text.value = "⏹  Stopping..."
        page.update()
        pid = _ACTIVE_PID[0]
        if pid:
            await asyncio.get_event_loop().run_in_executor(None, comfy_cancel, pid)

    run_btn = ft.FilledButton(
        content=run_label,
        on_click=run_batch,
        bgcolor="#2979FF",
        width=224,
        height=42,
    )
    stop_btn = ft.FilledButton(
        content=ft.Row([ft.Icon(ft.Icons.STOP_ROUNDED, size=16, color="#FFF"),
                        ft.Text("Stop", color="#FFF", size=13, weight=ft.FontWeight.W_600)],
                       alignment=ft.MainAxisAlignment.CENTER, spacing=6, tight=True),
        on_click=stop_batch,
        bgcolor="#E53935",
        width=224,
        height=42,
        visible=False,
        tooltip="Stop generating and cancel the ComfyUI job",
    )

    right_panel = ft.Container(
        content=ft.Column([
            ft.Container(
                content=ft.Column([
                    ft.Text("📂  Output Folder", size=11,
                            weight=ft.FontWeight.W_600, color="#555"),
                    ft.Row([folder_text, folder_btn], spacing=6,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ], spacing=5),
                padding=po(left=12, right=12, top=10, bottom=10),
                border=ft.Border(bottom=ft.BorderSide(1, "#EBEBEB")),
            ),
            ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.ACCOUNT_TREE_OUTLINED, size=14, color="#555"),
                        ft.Container(width=6),
                        ft.Text("Workflow", size=11,
                                weight=ft.FontWeight.W_600, color="#555",
                                expand=True),
                        ft.IconButton(
                            icon=ft.Icons.REFRESH_ROUNDED,
                            tooltip="Refresh files",
                            on_click=refresh_apis,
                            icon_color="#888",
                            icon_size=16,
                            width=28, height=28,
                            padding=0,
                        ),
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                    api_dropdown,
                    mapping_info,
                    ft.Row([
                        refresh_lbl,
                        ft.Text("Loaded from the comfy_apis/image folder",
                                size=9, color="#999"),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ], spacing=5),
                padding=po(left=12, right=12, top=10, bottom=10),
                border=ft.Border(bottom=ft.BorderSide(1, "#EBEBEB")),
            ),
            ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.EDIT_OUTLINED, size=14, color="#555"),
                        ft.Container(width=6),
                        ft.Text("Prompt", size=11,
                                weight=ft.FontWeight.W_600, color="#555",
                                expand=True),
                        ft.TextButton(
                            content=ft.Row([
                                ft.Icon(ft.Icons.HISTORY_ROUNDED, size=15, color="#2979FF"),
                                ft.Text("Load", size=11, color="#2979FF",
                                        weight=ft.FontWeight.W_600),
                            ], spacing=4, tight=True),
                            tooltip="Load Prompt",
                            on_click=open_history,
                            style=ft.ButtonStyle(
                                padding=po(left=8, right=8, top=2, bottom=2),
                                shape=ft.RoundedRectangleBorder(radius=6),
                            ),
                        ),
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                    prompt_field,
                    ft.Row([
                        prompt_save_lbl,
                        ft.FilledButton(
                            content=ft.Row([
                                ft.Icon(ft.Icons.SAVE_OUTLINED, size=14, color="#FFF"),
                                ft.Text("Save Prompt", size=10, color="#FFF"),
                            ], spacing=5, tight=True),
                            on_click=save_prompt, bgcolor="#555", height=28,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
                        ),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ], spacing=5),
                padding=po(left=12, right=12, top=10, bottom=10),
                border=ft.Border(bottom=ft.BorderSide(1, "#EBEBEB")),
            ),
            ft.Container(expand=True),
            ft.Divider(height=1, color="#E8E8E8"),
            ft.Container(
                content=ft.Column([
                    status_text,
                    ft.Container(content=run_btn, alignment=ft.Alignment(0, 0)),
                    ft.Container(content=stop_btn, alignment=ft.Alignment(0, 0)),
                ], spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=12,
            ),
        ], spacing=0, expand=True),
        width=260,
        bgcolor="#F7F7F7",
        border=ft.Border(left=ft.BorderSide(1, "#D8D8D8")),
    )

    # ── CENTER (same as before, just with card_map added) ───────────────────────
    preview_wrap = ft.Column(
        scroll=ft.ScrollMode.AUTO, spacing=0, expand=True, visible=False,
    )
    drop_hint = ft.Column([
        ft.Icon(ft.Icons.FOLDER_OPEN_OUTLINED, size=56, color="#BBBBBB"),
        ft.Text(
            "Drag and drop images or\nfolder to start editing",
            size=14, weight=ft.FontWeight.W_600, color="#999",
            text_align=ft.TextAlign.CENTER,
        ),
        ft.Text("OR", size=12, color="#BBBBBB"),
    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12, visible=True)

    async def browse_click(e):
        fp = ft.FilePicker()
        files = await fp.pick_files(
            dialog_title="Select images",
            allow_multiple=True,
            allowed_extensions=["jpg", "jpeg", "png", "webp", "bmp", "tiff", "tif"],
        )
        if not files:
            return
        paths = [f.path for f in files if f.path]
        if not paths:
            return

        selected_files.clear()
        selected_files.extend(paths)
        card_map.clear()
        preview_wrap.controls.clear()

        row: list[ft.Control] = []
        for i, path in enumerate(paths):
            c = make_card(path, Path(path).name, badge="INPUT")
            card_map[path] = c          # Store the ref to switch the border to green
            row.append(c)
            if len(row) == 3 or i == len(paths) - 1:
                preview_wrap.controls.append(
                    ft.Container(
                        content=ft.Row(row, spacing=10,
                                       wrap=False,
                                       scroll=ft.ScrollMode.AUTO),
                        padding=po(left=16, right=16, top=8, bottom=8),
                    )
                )
                row = []

        drop_hint.visible = False
        browse_btn.visible = False
        preview_wrap.visible = True
        top_bar_row.controls[0].value = f"{len(paths)} file(s) selected"
        clear_btn.visible = True
        page.update()

    browse_btn = ft.FilledButton(
        content=ft.Row([
            ft.Icon(ft.Icons.PHOTO_LIBRARY_OUTLINED, color="#FFF", size=16),
            ft.Text("Browse images", color="#FFF", size=13),
        ], tight=True, spacing=6),
        on_click=browse_click,
        bgcolor="#2979FF",
        height=42,
    )

    def clear_all(e):
        selected_files.clear()
        card_map.clear()
        preview_wrap.controls.clear()
        preview_wrap.visible = False
        drop_hint.visible = True
        browse_btn.visible = True
        clear_btn.visible = False
        top_bar_row.controls[0].value = ""
        output_col.controls.clear()
        status_text.value = ""
        page.update()

    clear_btn = ft.IconButton(
        icon=ft.Icons.CLEAR_ALL_ROUNDED,
        tooltip="Clear list",
        on_click=clear_all,
        visible=False,
        icon_color="#888",
        icon_size=20,
    )
    top_bar_row = ft.Row([
        ft.Text("", size=11, color="#666"),
        clear_btn,
    ], alignment=ft.MainAxisAlignment.END,
       vertical_alignment=ft.CrossAxisAlignment.CENTER)

    center_zone = ft.Container(
        content=ft.Column([
            ft.Container(
                content=top_bar_row,
                padding=po(right=8, top=4, left=14),
                height=40,
            ),
            ft.Container(
                content=ft.Column([
                    drop_hint,
                    browse_btn,
                    preview_wrap,
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                   spacing=16, expand=True),
                expand=True,
                alignment=ft.Alignment(0, 0),
            ),
        ], spacing=0, expand=True),
        expand=True,
        bgcolor="#E2E2E2",
    )

    # ── LAYOUT (same as before) ─────────────────────────────────────────────────
    page.add(ft.Column([
        header,
        ft.Row([center_zone, right_panel], spacing=0, expand=True),
    ], spacing=0, expand=True))


ft.app(target=main)